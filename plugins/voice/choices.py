"""The lists the Plugins screen offers for the voice's settings (SDK version 25), asked of the
provider the voice uses, so a new model shows up without a new Atlas.

Each provider lists every model its key may use; the voice keeps those that do the setting's job.
Gemini says what each model does (`supportedGenerationMethods`: a live model takes
`bidiGenerateContent`), and names its speech models `-tts` and its transcription ones
`-transcribe`. OpenAI says only the names, which carry the job the same way: `transcribe` and
`whisper`, `tts`, `gpt-live`. Gemini lists its voices too, by language: those of the app's
language first, then the ones of its first voices, which speak every language. OpenAI lists no
voices, so its voice is typed.
"""

import hashlib
import re
import time
from typing import Any, cast

import httpx2

from . import gemini
from .endpoint import OPENAI, Provider, VoiceError, refusal, unreachable

#: How long a provider's list is kept before it is asked again.
KEPT_SECONDS = 600.0
#: The setting whose choices are the models that answer spoken turns.
TURNS_SETTING = "voice_turns_model"
CONVERSATION_MODEL = "conversation"
#: Words in the name of a Gemini model that does something else than talk: images, music,
#: embeddings, robots, a computer, research.
NOT_TALK = ("tts", "image", "banana", "embedding", "lyria", "robotics", "computer-use", "research")
#: An OpenAI snapshot's date, listed after the name it pins.
DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")

#: The language Gemini lists its first voices under.
EVERY_LANGUAGE = "en-US"

Choice = dict[str, str]

_kept: dict[tuple[str, str, str, str], tuple[float, list[Choice]]] = {}


async def setting_choices(
    setting: str,
    provider: Provider,
    key: str | None,
    api_url: str,
    models: object,
    language: str = "",
) -> dict[str, Any]:
    """`{"choices": [{value, label}], "why": ...}`: the values the setting may take, the
    provider's own choice first as the empty value; `why` says what kept the list short.
    `models` are the models Atlas's picker offers, and `language` the app's, as the app sends
    them."""
    default = {
        "transcription_model": provider.transcription_model,
        "speech_model": provider.speech_model,
        "live_model": provider.live_model,
        "voice": provider.voice,
        TURNS_SETTING: provider.turns_model,
    }.get(setting)
    if default is None:
        return {"choices": [], "why": ""}
    choices: list[Choice] = [{"value": "", "label": f"{provider.name}'s choice: {default}"}]
    if setting == TURNS_SETTING:
        choices.append({"value": CONVERSATION_MODEL, "label": "The conversation's own model"})
        return {"choices": choices + _app_models(models), "why": ""}
    if not key:
        return {"choices": choices, "why": provider.no_key()}
    try:
        choices += await _asked(setting, provider, key, api_url, language)
    except VoiceError as error:
        return {"choices": choices, "why": str(error)}
    return {"choices": choices, "why": ""}


async def _asked(
    setting: str, provider: Provider, key: str, api_url: str, language: str
) -> list[Choice]:
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    at = (provider.id, api_url, fingerprint, f"{setting}:{language}")
    kept = _kept.get(at)
    if kept is not None and time.monotonic() - kept[0] < KEPT_SECONDS:
        return kept[1]
    if provider is OPENAI:
        found = _openai(setting, await _openai_models(key, api_url))
    elif setting == "voice":
        local = await gemini.voices(key, api_url, language) if _language(language) else []
        found = _gemini_voices(local, await gemini.voices(key, api_url, EVERY_LANGUAGE))
    else:
        found = _gemini(setting, await gemini.models(key, api_url))
    _kept[at] = (time.monotonic(), found)
    return found


def _openai(setting: str, names: list[str]) -> list[Choice]:
    def fits(name: str) -> bool:
        if setting == "transcription_model":
            streamed = any(word in name for word in ("live", "realtime", "diarize"))
            return ("transcribe" in name or "whisper" in name) and not streamed
        if setting == "speech_model":
            return "tts" in name
        if setting == "live_model":
            return name.startswith("gpt-live") and "transcribe" not in name
        return False

    picked = sorted(
        (name for name in names if fits(name)), key=lambda n: (bool(DATED.search(n)), n)
    )
    return [{"value": name, "label": name} for name in picked]


def _gemini(setting: str, listed: list[dict[str, Any]]) -> list[Choice]:
    first: list[Choice] = []
    rest: list[Choice] = []
    for model in listed:
        name = str(model.get("name") or "").removeprefix("models/")
        methods = model.get("supportedGenerationMethods")
        does = set(cast("list[str]", methods)) if isinstance(methods, list) else set[str]()
        label = str(model.get("displayName") or name)
        choice = {"value": name, "label": label}
        if not name:
            continue
        if setting == "transcription_model":
            if "generateContent" not in does or not name.startswith("gemini"):
                continue
            if "transcribe" in name:
                first.append(choice)
            elif not any(word in name for word in NOT_TALK):
                rest.append(choice)
        elif setting == "speech_model":
            if "generateContent" in does and "tts" in name:
                first.append(choice)
        elif setting == "live_model" and "bidiGenerateContent" in does:
            if not any(word in name for word in ("transcribe", "translate", "robotics")):
                first.append(choice)
    return first + rest


def _gemini_voices(local: list[dict[str, Any]], first: list[dict[str, Any]]) -> list[Choice]:
    """The app's language's voices, then Gemini's first voices: those whose id is their name
    (`kore`, Kore), listed under English and speaking every language."""
    choices: list[Choice] = []
    seen: set[str] = set()
    every = [
        voice
        for voice in first
        if str(voice.get("id") or "") == str(voice.get("display_name") or "").lower()
    ]
    for voice in [*local, *every]:
        value = str(voice.get("id") or "")
        name = str(voice.get("display_name") or value)
        if not value or value in seen:
            continue
        seen.add(value)
        about = ", ".join(
            str(voice[field]) for field in ("gender", "pitch", "language_code") if voice.get(field)
        )
        if voice in every:
            about = ", ".join(
                part for part in (str(voice.get("gender") or ""), "every language") if part
            )
        choices.append({"value": value, "label": f"{name} ({about})" if about else name})
    return choices


def _language(language: str) -> bool:
    """Whether the app's language has voices of its own beyond the first ones."""
    return bool(language) and language.lower() != EVERY_LANGUAGE.lower()


def _app_models(models: object) -> list[Choice]:
    """The models Atlas's picker offers, as the app sends them: checked, since a screen's JSON is
    not trusted to be well formed."""
    choices: list[Choice] = []
    for item in cast("list[object]", models) if isinstance(models, list) else []:
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, Any]", item)
        value, label = entry.get("value"), entry.get("label")
        if isinstance(value, str) and "/" in value:
            choices.append({"value": value, "label": label if isinstance(label, str) else value})
    return choices


async def _openai_models(key: str, api_url: str) -> list[str]:
    async with httpx2.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(
                f"{api_url.rstrip('/')}/models", headers={"Authorization": f"Bearer {key}"}
            )
        except httpx2.HTTPError as error:
            raise unreachable(OPENAI, error) from error
    if response.status_code != 200:
        raise VoiceError(refusal(OPENAI, "the list of models", response))
    try:
        body = response.json()
    except ValueError:
        body = None
    data = cast("dict[str, Any]", body).get("data") if isinstance(body, dict) else None
    names: list[str] = []
    for item in cast("list[object]", data) if isinstance(data, list) else []:
        name = cast("dict[str, Any]", item).get("id") if isinstance(item, dict) else None
        if isinstance(name, str) and name:
            names.append(name)
    return names
