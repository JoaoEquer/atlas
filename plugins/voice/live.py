"""The live voice conversation's server side: the session with OpenAI's full-duplex voice model,
opened with the key that never leaves this computer, and the note each spoken turn carries.

The voice model (`gpt-live-1`) talks with the user and has no tools of its own: what needs real
work it hands to Atlas, through its client delegation, and speaks what Atlas answers. The app holds
the WebRTC connection, the microphone and the speaker; this only exchanges the app's SDP offer for
OpenAI's answer (`POST /live/sessions`), so the key stays here. OpenAI bills the session's time,
silence included.
"""

from typing import Any, cast

import httpx2

from .endpoint import OPENAI, VoiceError, refusal, unreachable

REQUEST_SECONDS = 30.0

#: Who the voice is, and when it hands the conversation to Atlas.
PERSONA = (
    "You are the voice of Atlas, the user's personal assistant, in a spoken conversation. Speak "
    "naturally and briefly, as a person on a call does, in the language the user speaks.\n\n"
    "Atlas does the work: it has the user's tools, files, memory and accounts, and you have none "
    "of them. Hand the request to Atlas whenever the user asks for something to be done, checked, "
    "looked up, found, written, fixed, run or remembered; whenever the answer depends on facts you "
    "do not have, or on what happened before this call; and whenever they correct a request "
    "already handed over.\n\n"
    "Answer yourself only a greeting, small talk, a short clarifying question, or a repeat of an "
    "answer Atlas already gave.\n\n"
    "While Atlas works, say in a few words that you are on it, then wait for its answer. Never "
    "make up a result, and never say something is done before Atlas says so. When Atlas answers, "
    "say it in your own words, shortly."
)

#: What a turn handed over by the voice tells the model, after the user's words.
TURN_NOTE = (
    "This message comes from a live spoken conversation: it is a transcript of speech, which may "
    "hold mishearings and corrections, so follow what the user meant last. A voice will say your "
    "answer aloud: answer in a few plain spoken sentences, unless the user asked for detail; no "
    "Markdown, no lists, no tables, no code, and no addresses spelled out. Do the work with your "
    "tools as always, and never say something is done before it is."
)


def turn_note(spoken: str) -> str:
    """The note for one turn, with the recent spoken exchange it refers to: a "no, Thursday"
    means nothing alone."""
    spoken = spoken.strip()
    if not spoken:
        return TURN_NOTE
    return f"{TURN_NOTE}\n\nThe conversation so far, the newest last:\n{spoken}"


class LiveSessions:
    """Opening live sessions with one key, one model and one voice."""

    def __init__(self, key: str | None, api_url: str, model: str, voice: str) -> None:
        self._key = key
        self._url = f"{api_url.rstrip('/')}/live/sessions"
        self._model = model.strip()
        self._voice = voice.strip()

    @property
    def ready(self) -> bool:
        return bool(self._key)

    async def open(self, offer: str, history: list[dict[str, Any]]) -> dict[str, str]:
        """OpenAI's answer to the app's WebRTC offer: `{"session": id, "sdp": answer}`. `history`
        is the conversation's last messages, in the voice model's input shape, so the voice knows
        what was said before. Raises `VoiceError`."""
        if not self._key:
            raise VoiceError(OPENAI.no_key())
        session: dict[str, Any] = {
            "model": self._model,
            "instructions": PERSONA,
            "audio": {"output": {"voice": self._voice}},
            "delegation": {"type": "client"},
        }
        if history:
            session["input"] = history
        body = {"session": session, "transport": {"type": "webrtc", "sdp": offer}}
        async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
            try:
                response = await client.post(
                    self._url, headers={"Authorization": f"Bearer {self._key}"}, json=body
                )
            except httpx2.HTTPError as error:
                raise unreachable(OPENAI, error) from error
        if response.status_code not in (200, 201):
            raise VoiceError(refusal(OPENAI, "the voice session", response))
        try:
            found = cast("dict[str, Any]", response.json())
        except ValueError as error:
            raise VoiceError("OpenAI's answer was not JSON.") from error
        transport = found.get("transport")
        sdp = cast("dict[str, Any]", transport).get("sdp") if isinstance(transport, dict) else None
        if not isinstance(sdp, str) or not sdp:
            raise VoiceError("OpenAI's answer held no WebRTC answer.")
        opened = found.get("session")
        session_id = cast("dict[str, Any]", opened).get("id") if isinstance(opened, dict) else ""
        return {"session": str(session_id or ""), "sdp": sdp}


def history_of(
    messages: object, *, most: int = 24, characters: int = 6_000
) -> list[dict[str, Any]]:
    """The conversation's last text messages, newest kept, in the voice model's input shape: what
    the screen sends, checked, since a screen's JSON is not trusted to be well formed."""
    if not isinstance(messages, list):
        return []
    picked: list[dict[str, Any]] = []
    left = characters
    for item in reversed(cast("list[object]", messages)):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, Any]", item)
        role, text = entry.get("role"), entry.get("text")
        if role not in ("user", "assistant") or not isinstance(text, str):
            continue
        text = " ".join(text.split())[:1_200]
        if not text:
            continue
        if len(picked) >= most or left < len(text):
            break
        left -= len(text)
        kind = "input_text" if role == "user" else "output_text"
        picked.append({"type": "message", "role": role, "content": [{"type": kind, "text": text}]})
    picked.reverse()
    return picked
