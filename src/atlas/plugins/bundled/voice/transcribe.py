"""Writing down what an audio file says, through OpenAI's transcription endpoint.

One request a file, with a deadline, the file sent as it is: the endpoint takes the formats a
person sends (Telegram's OGG voice notes, MP3, M4A, WAV, the app's WebM recordings) up to 25 MB,
past Atlas's own 20 MB limit for audio. What fails raises with what to tell, and Atlas leaves the
file's note.
"""

from typing import Any, cast

import httpx2

from .endpoint import OPENAI, VoiceError, refusal, unreachable

#: A request's deadline: a long recording takes a while to write down.
REQUEST_SECONDS = 110.0


class Transcriber:
    """OpenAI's transcription, with one key and one model."""

    def __init__(self, key: str | None, api_url: str, model: str) -> None:
        self._key = key
        self._url = f"{api_url.rstrip('/')}/audio/transcriptions"
        self._model = model.strip()

    async def transcribe_data(self, name: str, data: bytes, media_type: str) -> str:
        """What a recording says, as text; raises `VoiceError`."""
        if not self._key:
            raise VoiceError(OPENAI.no_key())
        async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
            try:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._key}"},
                    data={"model": self._model, "response_format": "json"},
                    files={"file": (name, data, media_type or "application/octet-stream")},
                )
            except httpx2.HTTPError as error:
                raise unreachable(OPENAI, error) from error
        if response.status_code != 200:
            raise VoiceError(refusal(OPENAI, "the transcription", response))
        try:
            body = cast("dict[str, Any]", response.json())
        except ValueError:
            body = {}
        text = body.get("text")
        if not isinstance(text, str):
            raise VoiceError("OpenAI's answer held no text.")
        return text.strip()
