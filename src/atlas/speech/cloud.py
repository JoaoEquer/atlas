"""Writing down a recording at a provider, when the user chose one over this computer: OpenAI's
transcription endpoint, or a Gemini interaction, each with the key the vault holds for Atlas's
provider of that name.

One request a recording, sent as it is: both take the formats a person sends (the app's WebM,
Telegram's OGG, MP3, M4A, WAV). A recording too large for Gemini's request goes up through its
Files API first.
"""

import base64
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

import httpx2

from atlas.speech.errors import SpeechError

#: A request's deadline: a long recording takes a while to write down.
REQUEST_SECONDS = 110.0
#: The largest recording sent inside Gemini's request: it takes 20 MB a request, and base64 grows
#: a file by a third.
INLINE_BYTES = 14 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CloudProvider:
    """A provider that writes down recordings, and the model it does it with."""

    name: str
    #: The vault name of its key, the one Atlas's provider of that name reads.
    key: str
    api_url: str
    model: str

    def no_key(self) -> str:
        return (
            f"There is no {self.key} in the vault: store it in Settings, Model, or with "
            f"`atlas secrets set {self.key}`, or write down recordings on this computer."
        )


OPENAI = CloudProvider("OpenAI", "OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-transcribe")
GEMINI = CloudProvider(
    "Gemini",
    "GEMINI_API_KEY",
    "https://generativelanguage.googleapis.com/v1beta",
    "gemini-3.5-transcribe",
)


async def transcribe_openai(
    provider: CloudProvider,
    key: str | None,
    name: str,
    data: bytes,
    media_type: str,
) -> str:
    """What a recording says, by OpenAI's endpoint; raises `SpeechError`."""
    if not key:
        raise SpeechError(provider.no_key())
    async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
        try:
            response = await client.post(
                f"{provider.api_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                data={"model": provider.model, "response_format": "json"},
                files={"file": (name, data, media_type or "application/octet-stream")},
            )
        except httpx2.HTTPError as error:
            raise SpeechError(f"{provider.name} could not be reached: {error}") from error
    if response.status_code != 200:
        raise SpeechError(_refusal(provider, "the transcription", response))
    text = _object(_json(response)).get("text")
    if not isinstance(text, str):
        raise SpeechError(f"{provider.name}'s answer held no text.")
    return text.strip()


async def transcribe_gemini(
    provider: CloudProvider,
    key: str | None,
    name: str,
    data: bytes,
    media_type: str,
) -> str:
    """What a recording says, by a Gemini interaction with its transcription model; raises
    `SpeechError`."""
    if not key:
        raise SpeechError(provider.no_key())
    media_type = (media_type or "audio/ogg").split(";", 1)[0]
    async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
        audio: dict[str, str] = {"type": "audio", "mime_type": media_type}
        if len(data) > INLINE_BYTES:
            audio["uri"] = await _upload(client, provider, key, name, data, media_type)
        else:
            audio["data"] = base64.b64encode(data).decode("ascii")
        try:
            response = await client.post(
                f"{provider.api_url}/interactions",
                headers={"x-goog-api-key": key},
                json={"model": provider.model, "input": [audio]},
            )
        except httpx2.HTTPError as error:
            raise SpeechError(f"{provider.name} could not be reached: {error}") from error
    if response.status_code != 200:
        raise SpeechError(_refusal(provider, "the transcription", response))
    texts = [
        str(content.get("text") or "")
        for content in _outputs(_object(_json(response)))
        if content.get("type") == "text"
    ]
    if not texts:
        raise SpeechError(f"{provider.name}'s answer held no text.")
    return "".join(texts).strip()


async def _upload(
    client: httpx2.AsyncClient,
    provider: CloudProvider,
    key: str,
    name: str,
    data: bytes,
    media_type: str,
) -> str:
    """The Files API's resumable upload, in its two requests: the file's URI."""
    address = urlsplit(provider.api_url)
    start = f"{address.scheme}://{address.netloc}/upload{address.path}/files"
    try:
        begun = await client.post(
            start,
            headers={
                "x-goog-api-key": key,
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(len(data)),
                "X-Goog-Upload-Header-Content-Type": media_type,
            },
            json={"file": {"display_name": name}},
        )
        if begun.status_code != 200 or not begun.headers.get("x-goog-upload-url"):
            raise SpeechError(_refusal(provider, "the upload", begun))
        sent = await client.post(
            begun.headers["x-goog-upload-url"],
            headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
            content=data,
        )
    except httpx2.HTTPError as error:
        raise SpeechError(f"{provider.name} could not be reached: {error}") from error
    if sent.status_code != 200:
        raise SpeechError(_refusal(provider, "the upload", sent))
    uri = _object(_object(_json(sent)).get("file")).get("uri")
    if not isinstance(uri, str) or not uri:
        raise SpeechError(f"{provider.name}'s upload answered no file.")
    return uri


def _outputs(found: dict[str, Any]) -> list[dict[str, Any]]:
    """The contents of an interaction's answer, its thoughts left out."""
    contents: list[dict[str, Any]] = []
    steps = found.get("steps")
    for step in cast("list[object]", steps) if isinstance(steps, list) else []:
        entry = _object(step)
        content = entry.get("content")
        if entry.get("type") == "model_output" and isinstance(content, list):
            contents += [_object(item) for item in cast("list[object]", content)]
    return contents


def _refusal(provider: CloudProvider, what: str, response: httpx2.Response) -> str:
    """`OpenAI refused <what> (HTTP 401): <its message>`. Both answer an error as
    `{"error": {"message": ...}}`."""
    message = _object(_object(_json(response)).get("error")).get("message")
    reason = message if isinstance(message, str) and message else "no reason given"
    return f"{provider.name} refused {what} (HTTP {response.status_code}): {reason}"


def _json(response: httpx2.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _object(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}
