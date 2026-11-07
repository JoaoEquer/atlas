"""Which of Claude Code's sessions are open right now in another program (step 16f).

Decided with the user on 15/10/2026: a session continued in two places at once forks into two
branches, and Claude Code later follows one and forgets the other (its issue #83719, reproduced
with two VS Code windows). Claude Code keeps a registry of its running processes,
`~/.claude/sessions/<pid>.json` (`sessionId`, `pid`, `procStart`, `entrypoint`), which its VS Code
extension reads to warn ("This conversation is still open somewhere else"). Atlas reads it so: a
record counts only while its process lives and is the same process (its
start time, `procStart`, which on Windows is the process's creation time; a PID given to another
program after a crash is not it), and Atlas's own Claude Code processes are not "elsewhere".

The registry is not documented and may change between versions; read wrong, it only warns less.
"""

import json
import os
import sys
from collections.abc import Collection
from pathlib import Path
from typing import Any, cast


def open_elsewhere(root: Path, own: Collection[int]) -> dict[str, str]:
    """Each session open now in a Claude Code process that is not one of `own`, with where it
    runs (its `entrypoint`: `claude-vscode`, `cli`, or what Claude Code wrote)."""
    found: dict[str, str] = {}
    try:
        files = list((root / "sessions").glob("*.json"))
    except OSError:
        return found
    for file in files:
        try:
            raw: object = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        record = cast("dict[str, Any]", raw)
        session, pid = record.get("sessionId"), record.get("pid")
        if not isinstance(session, str) or not isinstance(pid, int) or pid in own:
            continue
        if _alive(pid, str(record.get("procStart") or "")):
            found[session] = str(record.get("entrypoint") or record.get("kind") or "")
    return found


def _alive(pid: int, started: str) -> bool:
    """Whether `pid` runs, and is the process that wrote the record when its start is known."""
    if sys.platform == "win32":
        created = _win32_created(pid)
        return created is not None and (not started or str(created) == started)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Pinned result types: the default c_int mangles handles.
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    _kernel32.GetProcessTimes.restype = ctypes.c_int
    _kernel32.GetProcessTimes.argtypes = (ctypes.c_void_p, *(ctypes.c_void_p,) * 4)
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    _QUERY_LIMITED_INFORMATION = 0x1000

    def _win32_created(pid: int) -> int | None:
        """A running process's creation time (a FILETIME, as `procStart` writes it); `None`
        when there is no such process or it cannot be read."""
        handle: int | None = _kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if not _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                return None
            # A process that ended but whose handle someone still holds has an exit time.
            created, ended = times[0], times[1]
            if ended.dwHighDateTime or ended.dwLowDateTime:
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            _kernel32.CloseHandle(handle)
