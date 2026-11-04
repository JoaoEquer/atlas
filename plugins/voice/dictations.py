"""The dictations kept in the home (step 17b): each recording with what it said.

A dictation is two files in the plugin's data folder, under `dictations/`: the recording as the app
made it, and a JSON beside it with the words, when, where it was said (the box's microphone or the
shortcut anywhere) and, when OpenAI could not write it down, why. A recording is kept before it is
written down, so speech is never lost to a failed transcription: the page writes it down again.
Kept until the user deletes them (decided by the user on 25/10/2026).
"""

import json
import re
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

#: A dictation's id: when it was made and a few random letters, so it names files safely.
_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
#: The recording's file ending, by its media type; anything else is kept as `.audio`.
_ENDINGS = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav"}
#: The most dictations a listing gives, newest first.
LISTED = 500


@dataclass(frozen=True, slots=True)
class Dictation:
    id: str
    #: When it was recorded, as ISO 8601 in the computer's time zone.
    at: str
    text: str
    media_type: str
    #: `box`, the conversation's microphone, or `anywhere`, the shortcut in any program.
    where: str
    #: Why OpenAI did not write it down, when it did not.
    error: str = ""


class Dictations:
    """The dictations of one home, in `folder`."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def keep(self, data: bytes, media_type: str, where: str) -> Dictation:
        """Keep a recording, not written down yet."""
        self._folder.mkdir(parents=True, exist_ok=True)
        made = Dictation(
            id=f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}",
            at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            text="",
            media_type=media_type,
            where=where if where in ("box", "anywhere") else "box",
        )
        self._audio_path(made).write_bytes(data)
        self._write(made)
        return made

    def written(self, dictation: Dictation, text: str, error: str = "") -> Dictation:
        """The dictation with what it said, or why it was not written down."""
        done = Dictation(**{**asdict(dictation), "text": text, "error": error})
        self._write(done)
        return done

    def listed(self) -> list[Dictation]:
        """The dictations, newest first; one whose JSON cannot be read is left out."""
        if not self._folder.is_dir():
            return []
        found: list[Dictation] = []
        for path in sorted(self._folder.glob("*.json"), reverse=True)[:LISTED]:
            dictation = self._read(path)
            if dictation is not None:
                found.append(dictation)
        return found

    def get(self, dictation_id: str) -> Dictation:
        """One dictation; raises `ValueError` for one the home does not keep."""
        found = self._read(self._json_path(dictation_id)) if _ID.match(dictation_id) else None
        if found is None:
            raise ValueError("There is no such dictation.")
        return found

    def audio(self, dictation: Dictation) -> bytes:
        return self._audio_path(dictation).read_bytes()

    def delete(self, dictation_id: str) -> None:
        dictation = self.get(dictation_id)
        self._audio_path(dictation).unlink(missing_ok=True)
        self._json_path(dictation_id).unlink(missing_ok=True)

    def _json_path(self, dictation_id: str) -> Path:
        return self._folder / f"{dictation_id}.json"

    def _audio_path(self, dictation: Dictation) -> Path:
        ending = _ENDINGS.get(dictation.media_type.split(";")[0].strip(), ".audio")
        return self._folder / f"{dictation.id}{ending}"

    def _write(self, dictation: Dictation) -> None:
        text = json.dumps(asdict(dictation), ensure_ascii=False, indent=2)
        self._json_path(dictation.id).write_text(text, encoding="utf-8")

    def _read(self, path: Path) -> Dictation | None:
        try:
            raw: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        given = cast("dict[str, Any]", raw)
        fields = ("id", "at", "text", "media_type", "where", "error")
        if not all(isinstance(given.get(name, ""), str) for name in fields):
            return None
        if not _ID.match(str(given.get("id", ""))):
            return None
        return Dictation(**{name: str(given.get(name, "")) for name in fields})
