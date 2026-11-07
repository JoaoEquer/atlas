"""Speaking a text, through OpenAI's speech endpoint, for the answers the user asks to hear.

The audio comes as Opus in an Ogg file, the format Telegram plays as a voice message; the app
plays it too. A text past the endpoint's limit is cut at a sentence: a spoken answer is short.
"""

import httpx2

from .endpoint import OPENAI, VoiceError, refusal, unreachable

#: The speech endpoint takes 4,096 characters at most.
MOST_CHARACTERS = 4_096
REQUEST_SECONDS = 110.0


class Speaker:
    """OpenAI's speech, with one key, one model and one voice."""

    def __init__(self, key: str | None, api_url: str, model: str, voice: str) -> None:
        self._key = key
        self._url = f"{api_url.rstrip('/')}/audio/speech"
        self._model = model.strip()
        self._voice = voice.strip()

    async def speak(self, text: str, *, tone: str = "") -> bytes:
        """`text` spoken, as an Ogg Opus file's bytes; `tone` says how (calm, cheerful).
        Raises `VoiceError`."""
        if not self._key:
            raise VoiceError(OPENAI.no_key())
        body: dict[str, str] = {
            "model": self._model,
            "voice": self._voice,
            "input": shortened(text),
            "response_format": "opus",
        }
        if tone.strip():
            body["instructions"] = tone.strip()
        async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
            try:
                response = await client.post(
                    self._url, headers={"Authorization": f"Bearer {self._key}"}, json=body
                )
            except httpx2.HTTPError as error:
                raise unreachable(OPENAI, error) from error
        if response.status_code != 200:
            raise VoiceError(refusal(OPENAI, "the speech", response))
        if not response.content:
            raise VoiceError("OpenAI's answer held no audio.")
        return response.content


def shortened(text: str) -> str:
    """The text within the endpoint's limit, cut after the last sentence that fits."""
    clean = text.strip()
    if len(clean) <= MOST_CHARACTERS:
        return clean
    cut = clean[:MOST_CHARACTERS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 0 else cut
