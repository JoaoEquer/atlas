"""Claude Code's sessions, listed wherever they began: its terminal, an editor, Atlas (step 16f).

Read as the Claude Agent SDK's `list_sessions` reads them in 0.2.165 (`_internal/sessions.py`),
which is what Claude Code's `/resume` and its VS Code extension list:

- **Every `<id>.jsonl` under `projects/`**, one folder per project, read lite: its size and time,
  and the first and last 64 KB, never the whole file. A session that is only a side chain (a
  subagent's) or holds no prompt is left out.
- **The folder is the `cwd` the session recorded**, since a project folder's name (the path with
  every character but letters and digits made a hyphen) does not turn back into the path.
- **The title is what Claude Code kept** (`customTitle`, the user's `/rename`, then `aiTitle`, its
  own, then an old `summary`) **or the start of the first prompt**: no model is asked. The SDK puts
  the last prompt before the summary; Atlas lists by title, decided with the user on 15/10/2026.
- **One folder's sessions** are the SDK's `list_sessions(directory=...)`: only the project folder
  its path names (`_find_project_dir`, a long path's hash matched by its first 200 characters), a
  session that recorded no `cwd` taken as that folder's. Its git worktrees are left out: each is a
  folder of its own in the list, as before.
- **The folders apart from their sessions** are Atlas's own (the SDK lists sessions only), for the
  executors tab, which reads a folder's sessions only once it is opened: each project's session
  files by their time alone, read lite from the newest until one is a session the list would
  show, whose `cwd` names the folder.
"""

import dataclasses
import json
import os
import re
from collections.abc import Collection
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import SessionFolder, SessionInfo

from .live import open_elsewhere

#: How much of each end of a session's file is read (the SDK's `LITE_READ_BUF_SIZE`).
LITE_BYTES = 65536
#: The longest first prompt kept as a title (the SDK's).
TITLE_CHARS = 200

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
#: What Claude Code writes as the user's turn and the user never typed (the SDK's).
_SKIP_PROMPT = re.compile(
    r"^(?:<local-command-stdout>|<session-start-hook>|<tick>|<goal>|"
    r"\[Request interrupted by user[^\]]*\]|"
    r"\s*<ide_opened_file>[\s\S]*</ide_opened_file>\s*$|"
    r"\s*<ide_selection>[\s\S]*</ide_selection>\s*$)"
)
_COMMAND_NAME = re.compile(r"<command-name>(.*?)</command-name>")
_NOT_ASCII_ALNUM = re.compile(r"[^a-zA-Z0-9]")
#: The longest project folder name before Claude Code cuts it and adds a hash (the SDK's).
LONGEST_FOLDER = 200


def project_folder(path: str) -> str:
    """The name of the folder Claude Code keeps a project's sessions in: every character but ASCII
    letters and digits made a hyphen, a longer one cut with a hash of the path after it (the SDK's
    `_sanitize_path`). Before step 16f Atlas kept accented letters, and missed those folders."""
    named = _NOT_ASCII_ALNUM.sub("-", path)
    if len(named) <= LONGEST_FOLDER:
        return named
    return f"{named[:LONGEST_FOLDER]}-{_hash(path)}"


def _hash(text: str) -> str:
    """JavaScript's 32-bit string hash in base 36, as Claude Code names a long folder (the SDK's
    `_simple_hash`)."""
    value = 0
    for character in text:
        value = ((value << 5) - value + ord(character)) & 0xFFFFFFFF
    if value >= 0x80000000:
        value -= 0x100000000
    value = abs(value)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while value:
        value, digit = divmod(value, 36)
        out = digits[digit] + out
    return out or "0"


def list_sessions(
    root: Path, own: Collection[int] = (), folder: Path | None = None
) -> list[SessionInfo]:
    """Every session under `root/projects`, the most recent first; one kept in two folders once,
    its newest. With `folder`, only the sessions of its project. Each says where it is open now
    outside Atlas, `own` being Atlas's processes."""
    projects = root / "projects"
    if folder is None:
        try:
            kept = [entry for entry in projects.iterdir() if entry.is_dir()]
        except OSError:
            return []
    else:
        found = _project_of(projects, str(folder))
        kept = [] if found is None else [found]
    newest: dict[str, tuple[float, SessionInfo]] = {}
    for project in kept:
        for file in _session_files(project):
            read = _read_lite(file)
            if read is None:
                continue
            mtime, head, tail = read
            info = _info(file.stem, mtime, head, tail, folder)
            if info is not None and (file.stem not in newest or newest[file.stem][0] < mtime):
                newest[file.stem] = (mtime, info)
    ordered = sorted(newest.values(), key=lambda found: found[0], reverse=True)
    elsewhere = open_elsewhere(root, own)
    return [
        dataclasses.replace(info, elsewhere=elsewhere[info.id]) if info.id in elsewhere else info
        for _, info in ordered
    ]


def list_folders(root: Path) -> list[SessionFolder]:
    """The folders the sessions under `root/projects` work in, the one changed last first: of
    each project, its newest session the list would show names the folder and its time. One
    folder whose sessions sit in two projects is listed once, at its newest."""
    projects = root / "projects"
    try:
        kept = [entry for entry in projects.iterdir() if entry.is_dir()]
    except OSError:
        return []
    newest: dict[str, tuple[float, SessionFolder]] = {}
    for project in kept:
        found = _newest_listed(project)
        if found is None:
            continue
        mtime, info = found
        key = str(info.folder)
        if key not in newest or newest[key][0] < mtime:
            newest[key] = (mtime, SessionFolder(info.folder, info.updated_at))
    ordered = sorted(newest.values(), key=lambda found: found[0], reverse=True)
    return [folder for _, folder in ordered]


def _newest_listed(project: Path) -> tuple[float, SessionInfo] | None:
    """A project's newest session the list would show, its files read from the newest on: a side
    chain or a file of metadata only is passed over."""
    timed: list[tuple[float, Path]] = []
    for file in _session_files(project):
        try:
            timed.append((file.stat().st_mtime, file))
        except OSError:
            continue
    for _, file in sorted(timed, reverse=True):
        read = _read_lite(file)
        if read is None:
            continue
        mtime, head, tail = read
        info = _info(file.stem, mtime, head, tail)
        if info is not None:
            return mtime, info
    return None


def _session_files(project: Path) -> list[Path]:
    """A project's session files: `<id>.jsonl`, the id a UUID."""
    try:
        files = list(project.iterdir())
    except OSError:
        return []
    return [file for file in files if file.suffix == ".jsonl" and _UUID.match(file.stem)]


def _project_of(projects: Path, path: str) -> Path | None:
    """The project folder that keeps `path`'s sessions (the SDK's `_find_project_dir`): its name,
    or for a path cut with a hash, the first one named by the same 200 characters, since Claude
    Code under Bun hashes it another way than the SDK under Node."""
    named = project_folder(path)
    exact = projects / named
    if exact.is_dir():
        return exact
    if len(named) <= LONGEST_FOLDER:
        return None
    prefix = named[:LONGEST_FOLDER] + "-"
    try:
        return next(
            (
                entry
                for entry in sorted(projects.iterdir())
                if entry.is_dir() and entry.name.startswith(prefix)
            ),
            None,
        )
    except OSError:
        return None


def _read_lite(file: Path) -> tuple[float, str, str] | None:
    """A file's time and the text of its two ends; `None` when it is empty or unreadable."""
    try:
        with file.open("rb") as opened:
            stat = os.fstat(opened.fileno())
            head_bytes = opened.read(LITE_BYTES)
            if not head_bytes:
                return None
            head = head_bytes.decode("utf-8", errors="replace")
            start = max(0, stat.st_size - LITE_BYTES)
            if start == 0:
                return stat.st_mtime, head, head
            opened.seek(start)
            tail = opened.read(LITE_BYTES).decode("utf-8", errors="replace")
            return stat.st_mtime, head, tail
    except OSError:
        return None


def _info(
    session: str, mtime: float, head: str, tail: str, folder: Path | None = None
) -> SessionInfo | None:
    """A session's row; one that recorded no `cwd` is `folder`'s, when its project was named by
    it (the SDK's)."""
    first_line = head.split("\n", 1)[0]
    if '"isSidechain":true' in first_line or '"isSidechain": true' in first_line:
        return None
    cwd = _first_field(head, "cwd") or (str(folder) if folder is not None else None)
    title = (
        _last_field(tail, "customTitle")
        or _last_field(head, "customTitle")
        or _last_field(tail, "aiTitle")
        or _last_field(head, "aiTitle")
        or _last_field(tail, "summary")
        or _first_prompt(head)
    )
    if not cwd or not title:
        # Nothing to continue it in, or nothing it said: a file of metadata only.
        return None
    return SessionInfo(
        id=session,
        folder=Path(cwd),
        title=title,
        updated_at=_utc(mtime),
        started_at=_first_field(head, "timestamp") or "",
    )


def _utc(moment: float) -> str:
    """A file's time as Atlas writes every time: `2026-10-07T12:34:56.789Z`."""
    stamp = datetime.fromtimestamp(moment, UTC)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"


# -- a JSON string field, read without parsing a line that may be cut (the SDK's) -----------------


def _first_field(text: str, key: str) -> str | None:
    values = _fields(text, key)
    return values[0] if values else None


def _last_field(text: str, key: str) -> str | None:
    values = _fields(text, key)
    return values[-1] if values else None


def _fields(text: str, key: str) -> list[str]:
    """Every value of `"key":"value"` in `text`, in order, unescaped."""
    found: list[tuple[int, str]] = []
    for pattern in (f'"{key}":"', f'"{key}": "'):
        start = 0
        while (at := text.find(pattern, start)) >= 0:
            value_start = at + len(pattern)
            end = value_start
            while end < len(text) and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            if end >= len(text):
                break
            found.append((at, _unescape(text[value_start:end])))
            start = end + 1
    return [value for _, value in sorted(found)]


def _unescape(raw: str) -> str:
    if "\\" not in raw:
        return raw
    try:
        value: object = json.loads(f'"{raw}"')
    except ValueError:
        return raw
    return value if isinstance(value, str) else raw


def _first_prompt(head: str) -> str:
    """The first thing the user typed, on one line, cut at 200 characters; a slash command's name
    when the session holds nothing else (the SDK's)."""
    command = ""
    for line in head.split("\n"):
        if '"type":"user"' not in line and '"type": "user"' not in line:
            continue
        if '"tool_result"' in line or '"isMeta":true' in line or '"isCompactSummary":true' in line:
            continue
        try:
            record: object = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        for raw in _texts(cast("dict[str, Any]", record)):
            text = raw.replace("\n", " ").strip()
            if not text:
                continue
            named = _COMMAND_NAME.search(text)
            if named:
                command = command or named.group(1)
                continue
            if _SKIP_PROMPT.match(text):
                continue
            return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS].rstrip() + "…"
    return command


def _texts(record: dict[str, Any]) -> list[str]:
    if record.get("type") != "user" or record.get("isMeta"):
        return []
    message: object = record.get("message")
    content = cast("dict[str, Any]", message).get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    found: list[str] = []
    for block in cast("list[object]", content):
        if isinstance(block, dict):
            part = cast("dict[str, Any]", block)
            text: object = part.get("text")
            if part.get("type") == "text" and isinstance(text, str):
                found.append(text)
    return found
