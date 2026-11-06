"""One account's `dsh` home, read as `dsh` leaves it: whether it has a key, its sessions and their
transcripts. Read without running it.

A session's log is Zstandard frames, which `frames` decodes with the plugin's extra, loaded only
when a transcript is asked for.
"""

import importlib
import importlib.util
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml

from atlas.plugins.sdk import Account, ExecutorError, SessionInfo, Transcript

from .protocol import CREDENTIALS, KEY_SECRET, entries_of, session_of

#: Where its projections are, one a session, and its logs.
PROJECTIONS = Path("storages") / "session_projcache" / "sessions"
SESSIONS = "sessions"
LOG = "session.v4.jsonl.zstd"


class DshHome:
    """The `dsh` home `home`."""

    def __init__(self, home: Path) -> None:
        self.home = home

    def account(self, environ: Mapping[str, str]) -> Account:
        """Logged in when a key is there for it: in the environment it runs with, or in its own
        credentials file. DeepSeek's key names no account."""
        if environ.get(KEY_SECRET) or self._stored_key():
            return Account(logged_in=True, login="API key")
        return Account(logged_in=False)

    def _stored_key(self) -> bool:
        try:
            stored: object = yaml.safe_load((self.home / CREDENTIALS).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return False
        found = cast("dict[str, Any]", stored) if isinstance(stored, dict) else {}
        return bool(found.get(KEY_SECRET))

    def sessions(self, folder: Path | None = None) -> list[SessionInfo]:
        """Its sessions with a prompt, of every folder or of `folder`, the most recent first."""
        wanted = None if folder is None else _same(folder)
        found: list[SessionInfo] = []
        try:
            projections = list((self.home / PROJECTIONS).glob("*.json"))
        except OSError:
            return []
        for file in projections:
            log = self.log(file.stem)
            changed = (log or file).stat().st_mtime
            info = session_of(_read(file), file.stem, changed)
            if info is None or (wanted is not None and _same(info.folder) != wanted):
                continue
            found.append(info)
        found.sort(key=lambda info: info.updated_at, reverse=True)
        return found

    def log(self, session: str) -> Path | None:
        """A session's log file."""
        return next((self.home / SESSIONS).glob(f"*/{session}/{LOG}"), None)

    def transcript(self, session: str) -> Transcript:
        log = self.log(session)
        if log is None:
            return Transcript(expired=True)
        return Transcript(entries_of(decoded(log.read_bytes()).splitlines()))


def decoded(raw: bytes) -> str:
    """A log's frames, decoded. Raises `ExecutorError` without the extra."""
    if importlib.util.find_spec("zstandard") is None:
        raise ExecutorError(
            "Reading DeepSeek's sessions needs an extra: run the installer again, or "
            "`uv sync --extra deepseek` in a checkout."
        )
    from .frames import decoded as from_frames  # noqa: PLC0415 - the extra, loaded only here

    return from_frames(raw)


def _same(folder: Path) -> str:
    return os.path.normcase(os.path.normpath(str(folder)))


def _read(file: Path) -> dict[str, Any]:
    try:
        found: object = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return cast("dict[str, Any]", found) if isinstance(found, dict) else {}
