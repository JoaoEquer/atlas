"""What the voice plugin's calls to a provider share: the providers it speaks to, the error they
raise, and how a refusal is told."""

from dataclasses import dataclass
from typing import Any, cast

import httpx2


class VoiceError(Exception):
    """The provider could not do what was asked; the message says why."""


@dataclass(frozen=True, slots=True)
class Provider:
    """A provider the voice reaches, with its own choices for what the settings leave empty."""

    id: str
    name: str
    #: The vault name of its key, the same one Atlas's provider of that name reads.
    key: str
    api_url: str
    transcription_model: str
    speech_model: str
    live_model: str
    voice: str
    #: The model that answers spoken turns, as `provider/model`.
    turns_model: str

    def no_key(self) -> str:
        return (
            f"There is no {self.key} in the vault: store it on Voice's card in Settings, "
            f"Extensions, or with `atlas secrets set {self.key}`."
        )


# Dated choices, as the model catalog's defaults are: the settings name others, and the list the
# app offers them in comes from the provider itself.
OPENAI = Provider(
    id="openai",
    name="OpenAI",
    key="OPENAI_API_KEY",
    api_url="https://api.openai.com/v1",
    transcription_model="gpt-transcribe",
    speech_model="gpt-4o-mini-tts",
    live_model="gpt-live-1",
    voice="marin",
    turns_model="openai/gpt-6-luna",
)
GEMINI = Provider(
    id="gemini",
    name="Gemini",
    key="GEMINI_API_KEY",
    api_url="https://generativelanguage.googleapis.com/v1beta",
    transcription_model="gemini-3.5-transcribe",
    speech_model="gemini-3.8-flash-tts",
    live_model="gemini-3.8-live",
    voice="Kore",
    turns_model="gemini/gemini-3.8-flash",
)
PROVIDERS = {provider.id: provider for provider in (OPENAI, GEMINI)}


def refusal(provider: Provider, what: str, response: httpx2.Response) -> str:
    """`OpenAI refused <what> (HTTP 401): <its message>`. Both providers answer an error as
    `{"error": {"message": ...}}`."""
    reason = "no reason given"
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = cast("dict[str, Any]", body).get("error")
        if isinstance(error, dict):
            message = cast("dict[str, Any]", error).get("message")
            if isinstance(message, str) and message:
                reason = message
    return f"{provider.name} refused {what} (HTTP {response.status_code}): {reason}"


def unreachable(provider: Provider, error: Exception) -> VoiceError:
    return VoiceError(f"{provider.name} could not be reached: {error}")
