"""The voice through Gemini: writing down, speaking and the live conversation, with the Gemini key
Atlas's Gemini provider uses.

- **Writing down**: an interaction (`POST /interactions`) with the audio. A transcription model
  (`gemini-3.5-transcribe`) takes the audio alone; any other model is asked, beside it, for the
  words only. A recording too large for one request goes up through the Files API first.
- **Speaking**: an interaction with a speech model, which answers a WAV file (24 kHz, 16-bit mono).
- **The live conversation**: the app talks to Gemini's Live API over a WebSocket; this makes a
  token for one session (`POST /auth_tokens`), so the key never leaves this computer, and the
  messages that open it: the voice, the persona, the conversation so far, and the one function the
  voice has, `ask_atlas`, which hands a request to Atlas.
"""

import base64
import binascii
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

import httpx2

from .endpoint import GEMINI, VoiceError, refusal, unreachable
from .live import PERSONA

REQUEST_SECONDS = 110.0
#: The largest recording sent inside the request: Gemini takes 20 MB a request, and base64 grows
#: a file by a third.
INLINE_BYTES = 14 * 1024 * 1024
#: What a model that is not a transcription model is asked, beside the audio.
TRANSCRIBE_PROMPT = (
    "Write down exactly what is said in this audio, in the language it is spoken. Answer with the "
    "words only: no title, no notes, no timestamps, no quotes."
)
#: The one function the live voice has.
ASK_ATLAS = "ask_atlas"
LIVE_PERSONA = (
    f"{PERSONA}\n\nTo hand a request to Atlas, call {ASK_ATLAS} with what the user asked, in their "
    "words; what it returns is Atlas's answer, to be said."
)
#: The Live API's WebSocket, for a session opened with a token.
LIVE_PATH = (
    "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained"
)


class GeminiTranscriber:
    """Gemini's writing down, with one key and one model."""

    def __init__(self, key: str | None, api_url: str, model: str) -> None:
        self._key = key
        self._api_url = api_url.rstrip("/")
        self._model = model.strip()

    async def transcribe_data(self, name: str, data: bytes, media_type: str) -> str:
        """What a recording says, as text; raises `VoiceError`."""
        if not self._key:
            raise VoiceError(GEMINI.no_key())
        media_type = (media_type or "audio/ogg").split(";", 1)[0]
        async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
            audio: dict[str, str] = {"type": "audio", "mime_type": media_type}
            if len(data) > INLINE_BYTES:
                audio["uri"] = await self._upload(client, name, data, media_type)
            else:
                audio["data"] = _base64(data)
            items: list[dict[str, str]] = [audio]
            if "transcribe" not in self._model:
                items.insert(0, {"type": "text", "text": TRANSCRIBE_PROMPT})
            found = await _interaction(
                client,
                self._api_url,
                self._key,
                {"model": self._model, "input": items},
                "the transcription",
            )
        texts = [
            str(content.get("text") or "")
            for content in _outputs(found)
            if content.get("type") == "text"
        ]
        if not texts:
            raise VoiceError("Gemini's answer held no text.")
        return "".join(texts).strip()

    async def _upload(
        self, client: httpx2.AsyncClient, name: str, data: bytes, media_type: str
    ) -> str:
        """The Files API's resumable upload, in its two requests: the file's URI."""
        address = urlsplit(self._api_url)
        start = f"{address.scheme}://{address.netloc}/upload{address.path}/files"
        try:
            begun = await client.post(
                start,
                headers={
                    "x-goog-api-key": self._key or "",
                    "X-Goog-Upload-Protocol": "resumable",
                    "X-Goog-Upload-Command": "start",
                    "X-Goog-Upload-Header-Content-Length": str(len(data)),
                    "X-Goog-Upload-Header-Content-Type": media_type,
                },
                json={"file": {"display_name": name}},
            )
            if begun.status_code != 200 or not begun.headers.get("x-goog-upload-url"):
                raise VoiceError(refusal(GEMINI, "the upload", begun))
            sent = await client.post(
                begun.headers["x-goog-upload-url"],
                headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"},
                content=data,
            )
        except httpx2.HTTPError as error:
            raise unreachable(GEMINI, error) from error
        if sent.status_code != 200:
            raise VoiceError(refusal(GEMINI, "the upload", sent))
        uri = _object(_object(_json(sent)).get("file")).get("uri")
        if not isinstance(uri, str) or not uri:
            raise VoiceError("Gemini's upload answered no file.")
        return uri


class GeminiSpeaker:
    """Gemini's speech, with one key, one model and one voice."""

    def __init__(self, key: str | None, api_url: str, model: str, voice: str) -> None:
        self._key = key
        self._api_url = api_url.rstrip("/")
        self._model = model.strip()
        self._voice = voice.strip()

    async def speak(self, text: str, *, tone: str = "") -> bytes:
        """`text` spoken, as a WAV file's bytes; `tone` says how. Raises `VoiceError`."""
        if not self._key:
            raise VoiceError(GEMINI.no_key())
        said: dict[str, Any] = {"type": "text", "text": text.strip()}
        if tone.strip():
            said["annotations"] = [{"type": "speech_metadata", "style": tone.strip()}]
        body = {
            "model": self._model,
            "input": [{"type": "user_input", "content": [said]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": self._voice}]},
        }
        async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
            found = await _interaction(client, self._api_url, self._key, body, "the speech")
        for content in _outputs(found):
            data = content.get("data")
            if content.get("type") == "audio" and isinstance(data, str) and data:
                try:
                    return _bytes(data)
                except ValueError:
                    break
        raise VoiceError("Gemini's answer held no audio.")


class GeminiLive:
    """Opening live sessions with one key, one model and one voice."""

    def __init__(self, key: str | None, api_url: str, model: str, voice: str) -> None:
        self._key = key
        self._api_url = api_url.rstrip("/")
        self._model = model.strip()
        self._voice = voice.strip()

    @property
    def ready(self) -> bool:
        return bool(self._key)

    async def open(self, history: list[dict[str, Any]], resume: str = "") -> dict[str, Any]:
        """What the app needs to open a session: `url`, the WebSocket with a token for it, and
        `messages`, the setup and the conversation so far, to send in order. `resume` is the
        handle of a session Gemini closed, to go on with it. Raises `VoiceError`."""
        if not self._key:
            raise VoiceError(GEMINI.no_key())
        now = datetime.now(UTC)
        body = {
            "uses": 1,
            "expireTime": _stamp(now + timedelta(minutes=30)),
            "newSessionExpireTime": _stamp(now + timedelta(minutes=1)),
        }
        async with httpx2.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{self._api_url}/auth_tokens",
                    headers={"x-goog-api-key": self._key},
                    json=body,
                )
            except httpx2.HTTPError as error:
                raise unreachable(GEMINI, error) from error
        if response.status_code != 200:
            raise VoiceError(refusal(GEMINI, "the voice session", response))
        token = _object(_json(response)).get("name")
        if not isinstance(token, str) or not token:
            raise VoiceError("Gemini's answer held no token.")
        address = urlsplit(self._api_url)
        scheme = "ws" if address.scheme == "http" else "wss"
        setup: dict[str, Any] = {
            "model": f"models/{self._model}",
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}}
                },
            },
            "systemInstruction": {"parts": [{"text": LIVE_PERSONA}]},
            "tools": [
                {
                    "functionDeclarations": [
                        {
                            "name": ASK_ATLAS,
                            "description": "Hand the user's request to Atlas, which does the "
                            "work, and get its answer to say.",
                            "parameters": {
                                "type": "OBJECT",
                                "properties": {
                                    "request": {
                                        "type": "STRING",
                                        "description": "What the user asked, in their words.",
                                    }
                                },
                                "required": ["request"],
                            },
                        }
                    ]
                }
            ],
            "inputAudioTranscription": {},
            "outputAudioTranscription": {},
            # Past its window the oldest turns are dropped, so a long call does not end; the
            # handle Gemini sends lets the app go on when it closes the connection.
            "contextWindowCompression": {"slidingWindow": {}},
            "sessionResumption": {"handle": resume} if resume else {},
        }
        messages: list[dict[str, Any]] = [{"setup": setup}]
        if history and not resume:
            messages.append({"clientContent": {"turns": history, "turnComplete": False}})
        return {
            "url": f"{scheme}://{address.netloc}{LIVE_PATH}?access_token={token}",
            "messages": messages,
        }


def live_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The conversation so far, from the voice model's input shape (`live.history_of`) to
    Gemini's turns."""
    turns: list[dict[str, Any]] = []
    for message in messages:
        text = "".join(
            str(part.get("text") or "") for part in cast("list[dict[str, Any]]", message["content"])
        )
        role = "user" if message.get("role") == "user" else "model"
        turns.append({"role": role, "parts": [{"text": text}]})
    return turns


async def models(key: str, api_url: str) -> list[dict[str, Any]]:
    """Every model the key may use, as Gemini lists them (`name`, `displayName`,
    `supportedGenerationMethods`)."""
    return await _listed(key, f"{api_url.rstrip('/')}/models", "models")


async def voices(key: str, api_url: str, language: str) -> list[dict[str, Any]]:
    """The voices Gemini speaks with in a language, such as `pt-BR` (`id`, `display_name`,
    `gender`, `pitch`...)."""
    return await _listed(
        key, f"{api_url.rstrip('/')}/voices", "voices", {"language_code": language}
    )


async def _listed(
    key: str, url: str, field: str, asked: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    page = ""
    async with httpx2.AsyncClient(timeout=30.0) as client:
        for _ in range(10):
            params = {**(asked or {}), "pageSize": "1000", **({"pageToken": page} if page else {})}
            try:
                response = await client.get(url, headers={"x-goog-api-key": key}, params=params)
            except httpx2.HTTPError as error:
                raise unreachable(GEMINI, error) from error
            if response.status_code != 200:
                raise VoiceError(refusal(GEMINI, f"the list of {field}", response))
            body = _object(_json(response))
            items = body.get(field)
            if isinstance(items, list):
                found += [
                    cast("dict[str, Any]", item)
                    for item in cast("list[object]", items)
                    if isinstance(item, dict)
                ]
            # The models' list names it in camelCase, the voices' in snake_case.
            page = str(body.get("nextPageToken") or body.get("next_page_token") or "")
            if not page:
                break
    return found


async def _interaction(
    client: httpx2.AsyncClient, api_url: str, key: str, body: dict[str, Any], what: str
) -> dict[str, Any]:
    try:
        response = await client.post(
            f"{api_url}/interactions", headers={"x-goog-api-key": key}, json=body
        )
    except httpx2.HTTPError as error:
        raise unreachable(GEMINI, error) from error
    if response.status_code != 200:
        raise VoiceError(refusal(GEMINI, what, response))
    return _object(_json(response))


def _outputs(found: dict[str, Any]) -> list[dict[str, Any]]:
    """The contents of the interaction's answer, its thoughts left out."""
    contents: list[dict[str, Any]] = []
    steps = found.get("steps")
    for step in cast("list[object]", steps) if isinstance(steps, list) else []:
        entry = _object(step)
        if entry.get("type") != "model_output":
            continue
        content = entry.get("content")
        if isinstance(content, list):
            contents += [_object(item) for item in cast("list[object]", content)]
    return contents


def _json(response: httpx2.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _object(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _bytes(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except binascii.Error as error:
        raise ValueError(str(error)) from error
