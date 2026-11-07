"""One account's `.gemini` folder, read as the Gemini CLI leaves it: its login, its sessions and
their transcripts, its connectors; and the two things Atlas writes there, a session's copy and a
session deleted, as the Gemini CLI itself does them.

Read without running it: started, the Gemini CLI renews its login and may write its files, and
its own `--list-sessions` names one folder's sessions and may ask the model for their titles.
"""

import contextlib
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import (
    Account,
    Connector,
    ConnectorOrigin,
    SessionInfo,
    Transcript,
)

from .protocol import (
    Conversation,
    account_of,
    chosen_login,
    conversation,
    entries_of,
    renamed,
    session_file_name,
    usable_token,
)

#: The marker in a project's folder that names the folder it is for.
PROJECT_ROOT = ".project_root"
#: Folders of a project's that are not sessions.
_RESERVED = frozenset({"chats", "logs", "tool-outputs"})


class GeminiHome:
    """The `.gemini` folder `state`."""

    def __init__(self, state: Path) -> None:
        self.state = state

    def account(self, environ: Mapping[str, str]) -> Account:
        return account_of(
            _read(self.state / "settings.json"),
            _read(self.state / "google_accounts.json"),
            (self.state / "oauth_creds.json").is_file(),
            dict(environ),
        )

    def login(self) -> str:
        """The login it chose: Google's, an API key, Vertex AI."""
        return chosen_login(_read(self.state / "settings.json"))

    def token(self) -> str:
        """Its Google token while it is good; empty when it has none or it expired."""
        return usable_token(_read(self.state / "oauth_creds.json"), time.time())

    # -- sessions ---------------------------------------------------------------------------------

    def sessions(self, folder: Path | None = None) -> list[SessionInfo]:
        """Its sessions, of every folder or of `folder`, the most recent first; a session with
        nothing to go on with, or a subagent's, is left out, as its own `--resume` leaves them."""
        found: list[SessionInfo] = []
        wanted = None if folder is None else _same(folder)
        for project in self._projects():
            root = project_folder(project)
            if root is None or (wanted is not None and _same(root) != wanted):
                continue
            for file in _session_files(project):
                read = conversation(_lines(file))
                if not read.id or read.subagent or not read.resumable():
                    continue
                found.append(_info(read, root))
        found.sort(key=lambda info: info.updated_at, reverse=True)
        return _unique(found)

    def transcript(self, session: str) -> Transcript:
        file = self.find(session)
        if file is None:
            return Transcript(expired=True)
        return Transcript(entries_of(conversation(_lines(file))))

    def find(self, session: str) -> Path | None:
        """The file of a session, the most recently changed when there are several."""
        best: tuple[float, Path] | None = None
        for project in self._projects():
            for file in _session_files(project):
                if not file.stem.endswith(session[:8]):
                    continue
                if conversation(_lines(file)[:1]).id != session:
                    continue
                changed = file.stat().st_mtime
                if best is None or changed > best[0]:
                    best = (changed, file)
        return None if best is None else best[1]

    def copy(self, source: Path, folder: Path) -> str:
        """A copy of the session file `source` among `folder`'s sessions here, with an id of its
        own, and that id. The folder must be one the Gemini CLI registered here (a session it
        opened there). Raises `LookupError`."""
        project = self.project_of(folder)
        if project is None:
            raise LookupError(f"The Gemini CLI has no project for {folder} yet.")
        session = str(uuid.uuid4())
        now = datetime.now(UTC)
        chats = project / "chats"
        chats.mkdir(parents=True, exist_ok=True)
        target = chats / session_file_name(session, now)
        target.write_text("".join(renamed(_lines(source), session, now)), encoding="utf-8")
        return session

    def project_of(self, folder: Path) -> Path | None:
        """The project folder the Gemini CLI keeps for `folder` (its `projects.json`)."""
        registry = _dict(_read(self.state / "projects.json").get("projects"))
        slug = registry.get(registry_key(folder))
        return None if not isinstance(slug, str) or not slug else self.state / "tmp" / slug

    def delete(self, session: str) -> bool:
        """A session's file and what the Gemini CLI kept beside it (its activity log, its tools'
        output, its subagents' sessions), as its `--delete-session` deletes them. Whether there
        was one."""
        file = self.find(session)
        if file is None:
            return False
        chats = file.parent
        project = chats.parent
        safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in session)
        with contextlib.suppress(FileNotFoundError):
            (project / "logs" / f"session-{safe}.jsonl").unlink()
        shutil.rmtree(project / "tool-outputs" / f"session-{safe}", ignore_errors=True)
        if safe.lower() not in _RESERVED:
            shutil.rmtree(project / safe, ignore_errors=True)
            shutil.rmtree(chats / safe, ignore_errors=True)
        file.unlink(missing_ok=True)
        return True

    def _projects(self) -> list[Path]:
        root = self.state / "tmp"
        try:
            return [entry for entry in root.iterdir() if entry.is_dir()]
        except OSError:
            return []

    # -- connectors -------------------------------------------------------------------------------

    def connectors(self) -> list[Connector]:
        """The MCP servers of its settings and of its extensions, as `gemini mcp list` names them,
        without starting any: its settings' win over an extension's of the same name. The state
        shown is whether it is on (its `mcp-server-enablement.json`)."""
        off = _off(_read(self.state / "mcp-server-enablement.json"))
        named: dict[str, tuple[ConnectorOrigin, str]] = {}
        for extension in sorted(self._extensions()):
            manifest = _read(extension / "gemini-extension.json")
            for name in _dict(manifest.get("mcpServers")):
                named.setdefault(name, ("plugin", str(manifest.get("name") or extension.name)))
        for name in _dict(_read(self.state / "settings.json").get("mcpServers")):
            named[name] = ("user", "")
        return [
            Connector(
                name, "disabled" if name.lower().strip() in off else "pending", origin, plugin
            )
            for name, (origin, plugin) in named.items()
        ]

    def _extensions(self) -> list[Path]:
        root = self.state / "extensions"
        try:
            return [entry for entry in root.iterdir() if entry.is_dir()]
        except OSError:
            return []


def project_folder(project: Path) -> Path | None:
    """The folder a project of the Gemini CLI is for (its `.project_root`), written as the folder
    is: it keeps it in lower case on Windows."""
    try:
        written = (project / PROJECT_ROOT).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not written:
        return None
    path = Path(written)
    if sys.platform == "win32" and path.exists():
        # Its real case, which Windows keeps and the marker loses.
        return Path(os.path.realpath(path))
    return path


def registry_key(folder: Path) -> str:
    """How its `projects.json` names a folder: absolute, in lower case on Windows."""
    resolved = str(Path(folder).absolute())
    return resolved.lower() if sys.platform == "win32" else resolved


def _info(read: Conversation, folder: Path) -> SessionInfo:
    return SessionInfo(
        read.id,
        folder,
        read.title(),
        str(read.fields.get("lastUpdated") or ""),
        str(read.fields.get("startTime") or ""),
    )


def _unique(found: list[SessionInfo]) -> list[SessionInfo]:
    """One entry a session: the most recent file wins, as for its own reader."""
    seen: set[str] = set()
    kept: list[SessionInfo] = []
    for info in found:
        if info.id not in seen:
            seen.add(info.id)
            kept.append(info)
    return kept


def _session_files(project: Path) -> list[Path]:
    chats = project / "chats"
    try:
        return [
            entry
            for entry in chats.iterdir()
            if entry.is_file()
            and entry.name.startswith("session-")
            and entry.suffix in (".jsonl", ".json")
        ]
    except OSError:
        return []


def _lines(file: Path) -> list[str]:
    """A session file's lines; an old one, a single JSON document, as its records."""
    try:
        text = file.read_text(encoding="utf-8")
    except OSError:
        return []
    if file.suffix == ".json":
        with contextlib.suppress(ValueError):
            whole = _dict(json.loads(text))
            messages = whole.pop("messages", [])
            return [
                json.dumps(whole),
                *(json.dumps(item) for item in cast("list[object]", messages)),
            ]
    return text.splitlines(keepends=True)


def _off(enablement: dict[str, Any]) -> set[str]:
    """The servers its enablement file turns off, named as it names them (in lower case)."""
    return {
        name.lower().strip()
        for name, state in enablement.items()
        if isinstance(state, dict) and cast("dict[str, Any]", state).get("enabled") is False
    }


def _same(folder: Path) -> str:
    """A folder written one way, to compare two of them: resolved, since `project_folder` resolves
    the folder the Gemini CLI wrote, and a folder asked for by a short name (`C:\\Users\\RUNNER~1`),
    a junction or a link must still match it (found on GitHub's Windows runners, 24/10/2026)."""
    return os.path.normcase(os.path.realpath(folder))


def _read(file: Path) -> dict[str, Any]:
    try:
        return _dict(json.loads(file.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}
