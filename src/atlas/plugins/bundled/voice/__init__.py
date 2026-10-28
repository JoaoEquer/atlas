"""Voice, talking to Atlas by voice: the plugin's `register` (step 17a).

Everything goes through one provider, the `provider` setting's: OpenAI or Gemini, each with the
key Atlas's provider of that name reads, and each with its own models where a setting is left
empty.

- **Audio the user sends**, dropped in the app or sent on a channel, is written down by the
  provider's transcription model before the turn (a reader, SDK version 21), and the model reads
  what it says.
- **Dictation**: the box's microphone (its screen, `desktop/plugin.js`) records, and the words come
  back into the box to be corrected before they are sent (the `dictate` call). **Anywhere** (step
  17b): a shortcut heard in every program records, and the words are pasted where the user is.
  Every dictation is kept in the home with its recording (`dictations`, the screen's page).
- **Atlas speaks when asked**: `voice_speak` says a text with the provider's speech model and sends
  it to the user (SDK version 22), a voice message on Telegram, a file in the app. Receiving audio
  never makes Atlas answer in audio.
- **The live conversation**: the screen talks with the provider's full-duplex voice model, whose
  session this opens (`live_session`) so the key stays here; each request the voice hands over is a
  turn of the conversation, which a `UserPromptSubmit` hook marks as spoken (`live_turn`, then the
  note).
- **The settings' lists** (SDK version 25): the Plugins screen asks `setting_choices` for the
  models and voices the provider has, so they are picked rather than typed.

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

import base64
import binascii
import secrets
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, cast

from atlas.plugins.sdk import PluginContext, ToolHost

from .audio import voice_note
from .choices import CONVERSATION_MODEL, setting_choices
from .dictations import Dictation, Dictations
from .endpoint import GEMINI, OPENAI, PROVIDERS, Provider, VoiceError
from .gemini import GeminiLive, GeminiSpeaker, GeminiTranscriber, live_history
from .live import LiveSessions, history_of, turn_note
from .speech import Speaker
from .transcribe import Transcriber

#: The longest dictation the screen may send, in bytes: a few minutes of speech.
DICTATION_BYTES = 10 * 1024 * 1024
#: How long a spoken turn's context waits for its turn.
CONTEXT_SECONDS = 60.0

SPEAK_DESCRIPTION = (
    "Say a text aloud and send it to the user as an audio message: a voice message on Telegram, "
    "an audio file in the app. Use it only when the user asks to hear the answer, or asks for an "
    "audio or a voice message; receiving audio from them is no reason to answer in audio. The "
    "text is what will be spoken, so write it to be heard: plain sentences, no Markdown, no "
    "lists, no code."
)
SPEAK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "What to say, in the user's language."},
        "tone": {
            "type": "string",
            "description": "Optional: how to say it (calm, cheerful, slow).",
        },
    },
    "required": ["text"],
}


class Transcribes(Protocol):
    async def transcribe_data(self, name: str, data: bytes, media_type: str) -> str: ...


def register(ctx: PluginContext) -> None:
    #: The spoken exchange each conversation's next turn refers to, and when it was handed over.
    spoken: dict[str, tuple[str, float]] = {}

    # Read on each use, so a key or a setting changed after the plugin was turned on is used.
    def provider() -> Provider:
        return PROVIDERS.get(ctx.setting("provider").strip().lower(), OPENAI)

    def key() -> str | None:
        return ctx.secret(provider().key)

    def api_url() -> str:
        return ctx.setting("api_url").strip() or provider().api_url

    def chosen(setting: str, default: str) -> str:
        return ctx.setting(setting).strip() or default

    def transcriber() -> Transcribes:
        model = chosen("transcription_model", provider().transcription_model)
        if provider() is GEMINI:
            return GeminiTranscriber(key(), api_url(), model)
        return Transcriber(key(), api_url(), model)

    async def read(path: Path, media_type: str) -> str:
        return await transcriber().transcribe_data(path.name, path.read_bytes(), media_type)

    async def speak(arguments: dict[str, Any], host: ToolHost) -> str:
        text = str(arguments.get("text") or "").strip()
        if not text:
            raise ValueError("Give the text to say.")
        host.progress("Speaking")
        using = provider()
        model = chosen("speech_model", using.speech_model)
        voice = chosen("voice", using.voice)
        tone = str(arguments.get("tone") or "")
        try:
            if using is GEMINI:
                wav = await GeminiSpeaker(key(), api_url(), model, voice).speak(text, tone=tone)
                audio, suffix = await voice_note(wav)
            else:
                audio = await Speaker(key(), api_url(), model, voice).speak(text, tone=tone)
                suffix = ".ogg"
        except VoiceError as error:
            raise ValueError(str(error)) from error
        folder = ctx.data_folder / "speech"
        folder.mkdir(parents=True, exist_ok=True)
        made = folder / f"atlas-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}{suffix}"
        made.write_bytes(audio)
        try:
            kept = await host.send_file(made)
        finally:
            made.unlink(missing_ok=True)
        return f"The audio message was sent to the user; the home keeps it at {kept}."

    async def note_spoken_turn(event: dict[str, Any]) -> dict[str, Any] | None:
        conversation = str(event.get("session_id") or "")
        # A turn handed over before its conversation was made (the first words) waits under "".
        handed = spoken.pop(conversation, None) or spoken.pop("", None)
        if handed is None or time.monotonic() - handed[1] > CONTEXT_SECONDS:
            return None
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": turn_note(handed[0]),
            }
        }

    kept = Dictations(ctx.data_folder / "dictations")

    async def write_down(dictation: Dictation, data: bytes) -> Dictation:
        name = "dictation.ogg" if "ogg" in dictation.media_type else "dictation.webm"
        try:
            text = await transcriber().transcribe_data(name, data, dictation.media_type)
        except VoiceError as error:
            kept.written(dictation, "", str(error))
            raise ValueError(str(error)) from error
        return kept.written(dictation, text)

    async def dictate(params: Any) -> dict[str, str]:
        given = _object(params)
        try:
            data = base64.b64decode(str(given.get("audio") or ""), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("The recording is not base64.") from error
        if not data:
            raise ValueError("The recording is empty.")
        if len(data) > DICTATION_BYTES:
            raise ValueError("The recording is too long to dictate; record a shorter one.")
        media_type = str(given.get("media_type") or "audio/webm")
        # Kept before it is written down: a failed transcription loses no speech.
        dictation = kept.keep(data, media_type, str(given.get("where") or "box"))
        done = await write_down(dictation, data)
        return {"id": done.id, "text": done.text}

    async def dictation_status(_params: Any) -> dict[str, Any]:
        ready = bool(key())
        return {
            "ready": ready,
            "why": "" if ready else provider().no_key(),
            "shortcut": ctx.setting("dictation_shortcut").strip(),
        }

    async def dictations(_params: Any) -> dict[str, Any]:
        return {"dictations": [asdict(dictation) for dictation in kept.listed()]}

    async def dictation_audio(params: Any) -> dict[str, str]:
        dictation = kept.get(str(_object(params).get("id") or ""))
        data = kept.audio(dictation)
        return {"audio": base64.b64encode(data).decode("ascii"), "media_type": dictation.media_type}

    async def dictation_again(params: Any) -> dict[str, str]:
        dictation = kept.get(str(_object(params).get("id") or ""))
        done = await write_down(dictation, kept.audio(dictation))
        return {"id": done.id, "text": done.text}

    async def dictation_delete(params: Any) -> bool:
        kept.delete(str(_object(params).get("id") or ""))
        return True

    async def live_status(_params: Any) -> dict[str, Any]:
        using = provider()
        turns = chosen("voice_turns_model", using.turns_model)
        provider_id, _, model = turns.partition("/")
        fixed = turns != CONVERSATION_MODEL and bool(provider_id and model)
        ready = bool(key())
        return {
            "ready": ready,
            "why": "" if ready else using.no_key(),
            # The screen speaks WebRTC to OpenAI and a WebSocket to Gemini.
            "provider": using.id,
            # Spoken turns run on a model of their own, with no reasoning, or the conversation's.
            "turns": {"provider": provider_id, "model": model, "effort": "none"} if fixed else None,
            "quiet_seconds": _seconds(ctx.setting("quiet_close_seconds"), 120),
        }

    async def live_session(params: Any) -> dict[str, Any]:
        given = _object(params)
        using = provider()
        model = chosen("live_model", using.live_model)
        voice = chosen("voice", using.voice)
        history = history_of(given.get("history"))
        try:
            if using is GEMINI:
                live = GeminiLive(key(), api_url(), model, voice)
                return await live.open(live_history(history), str(given.get("resume") or ""))
            offer = str(given.get("sdp") or "")
            if not offer.startswith("v="):
                raise ValueError("The screen sent no WebRTC offer.")
            return dict(await LiveSessions(key(), api_url(), model, voice).open(offer, history))
        except VoiceError as error:
            raise ValueError(str(error)) from error

    async def live_turn(params: Any) -> bool:
        given = _object(params)
        conversation = str(given.get("conversation") or "")
        spoken[conversation] = (str(given.get("spoken") or "")[-4_000:], time.monotonic())
        return True

    async def choices(params: Any) -> dict[str, Any]:
        given = _object(params)
        return await setting_choices(
            str(given.get("setting") or ""),
            provider(),
            key(),
            api_url(),
            given.get("models"),
            str(given.get("language") or "")[:20],
        )

    ctx.register_reader("audio", read)
    ctx.register_tool("voice_speak", SPEAK_DESCRIPTION, SPEAK_PARAMETERS, speak)
    ctx.register_hook("UserPromptSubmit", note_spoken_turn)
    ctx.register_call("dictate", dictate)
    ctx.register_call("dictation_status", dictation_status)
    ctx.register_call("dictations", dictations)
    ctx.register_call("dictation_audio", dictation_audio)
    ctx.register_call("dictation_again", dictation_again)
    ctx.register_call("dictation_delete", dictation_delete)
    ctx.register_call("live_status", live_status)
    ctx.register_call("live_session", live_session)
    ctx.register_call("live_turn", live_turn)
    ctx.register_call("setting_choices", choices)


def _object(params: Any) -> dict[str, Any]:
    """A call's parameters as an object: a screen's JSON is not trusted to be one."""
    return cast("dict[str, Any]", params) if isinstance(params, dict) else {}


def _seconds(value: str, default: int) -> int:
    try:
        return max(0, int(value))
    except ValueError:
        return default
