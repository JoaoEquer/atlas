"""A harness's newest version, read where its makers publish it, and the update the user asks for.

The plugin says where the version is published and how the install it found updates
(`UpdateSource`); Atlas reads the version itself, so no plugin reaches a host for it, at most once
every `FRESH` per source, never on the way to a start. The update is a program run on the user's
computer, so only the owner's click in the app runs it (`executor.update`), never the agent.
"""

import asyncio
import json
import re
import shutil
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta

import httpx2

from atlas.plugins import UpdateSource

#: How long a version read stays good: the harnesses' own checks wait about as long (Codex, 20
#: hours).
FRESH = timedelta(hours=20)
#: The longest a read of the newest version may take.
LOOK_SECONDS = 10.0
#: The longest an update may run: an npm install over a slow connection takes minutes.
UPDATE_SECONDS = 15 * 60
#: How much of an update's output the screen gets, its end.
OUTPUT_CHARS = 4000

_NPM_TAGS = "https://registry.npmjs.org/-/package/{package}/dist-tags"
#: WinGet's catalog, one folder per version of each package: the version WinGet can install,
#: which lags behind the makers' own (Claude Code's).
_WINGET = "https://api.github.com/repos/microsoft/winget-pkgs/contents/manifests/{letter}/{path}"
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


class UpdateError(Exception):
    """The newest version could not be read, or an update could not run."""


def source_key(source: UpdateSource) -> str:
    """One source, however many executors read it (each account of a harness reads its own)."""
    return f"npm:{source.npm}@{source.tag}" if source.npm else source.winget or source.url


async def newest(source: UpdateSource, client: httpx2.AsyncClient) -> str:
    """The newest version where `source` says it is published."""
    try:
        if source.npm:
            answer = await client.get(_NPM_TAGS.format(package=source.npm))
            answer.raise_for_status()
            found = json.loads(answer.text).get(source.tag, "")
        elif source.winget:
            publisher, _, name = source.winget.partition(".")
            path = f"{publisher}/{name}"
            answer = await client.get(_WINGET.format(letter=publisher[:1].lower(), path=path))
            answer.raise_for_status()
            folders = [entry.get("name", "") for entry in json.loads(answer.text)]
            found = max((f for f in folders if _parse(f)), key=_order, default="")
        else:
            answer = await client.get(source.url)
            answer.raise_for_status()
            found = answer.text.strip()
    except (httpx2.HTTPError, ValueError, AttributeError, TypeError) as error:
        raise UpdateError(f"The newest version could not be read: {error}") from error
    if not isinstance(found, str) or _parse(found) is None:
        raise UpdateError(f"The newest version read is not one: {str(found)[:40]!r}")
    return found


def newer(candidate: str, installed: str) -> bool:
    """Whether `candidate` comes after `installed`, a prerelease before its release
    (`0.2.1-rc.4` < `0.2.1-rc.5` < `0.2.1`); a version that does not read as one is never newer."""
    if _parse(candidate) is None or _parse(installed) is None:
        return False
    return _order(candidate) > _order(installed)


def command(source: UpdateSource, version: str) -> tuple[str, ...] | None:
    """What updates the install to `version`: the plugin's command, or the user's npm for an
    npm package; `None` when there is no npm here to run."""
    if source.command:
        return source.command
    npm = shutil.which("npm")
    if npm is None:
        return None
    # npm is a .cmd on Windows, which runs only through cmd.
    program = ["cmd", "/c", "npm"] if sys.platform == "win32" else [npm]
    return (*program, "install", "-g", f"{source.npm}@{version}")


def shown(argv: Sequence[str]) -> str:
    """The command as the user would type it, for the screen."""
    words = list(argv[2:]) if tuple(argv[:2]) == ("cmd", "/c") else list(argv)
    return " ".join(f'"{w}"' if " " in w else w for w in words)


async def run(argv: Sequence[str]) -> tuple[bool, str]:
    """Run an update, its output and errors in one, the end of it kept."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as error:
        return False, f"{shown(argv)} could not start: {error}"
    try:
        output, _ = await asyncio.wait_for(process.communicate(), UPDATE_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        minutes = UPDATE_SECONDS // 60
        return False, f"{shown(argv)} took more than {minutes} minutes; it was stopped."
    text = output.decode("utf-8", errors="replace").strip()
    return process.returncode == 0, text[-OUTPUT_CHARS:]


class Newest:
    """The newest version of each source, read at most once every `FRESH`."""

    def __init__(self) -> None:
        self._read: dict[str, tuple[datetime, str]] = {}

    def get(self, source: UpdateSource) -> str:
        found = self._read.get(source_key(source))
        return "" if found is None else found[1]

    def due(self, source: UpdateSource, now: datetime) -> bool:
        found = self._read.get(source_key(source))
        return found is None or now - found[0] >= FRESH

    def keep(self, source: UpdateSource, version: str, now: datetime) -> None:
        self._read[source_key(source)] = (now, version)


def _parse(version: str) -> tuple[int, int, int, str | None] | None:
    found = _VERSION.match(version.strip())
    if found is None:
        return None
    major, minor, patch, pre = found.groups()
    return int(major), int(minor), int(patch), pre


def _order(version: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    parsed = _parse(version)
    if parsed is None:
        return (0, 0, 0, 0, ())
    major, minor, patch, pre = parsed
    if pre is None:
        # A release comes after each of its prereleases.
        return (major, minor, patch, 1, ())
    parts = tuple((0, int(p)) if p.isdigit() else (1, p) for p in pre.split("."))
    return (major, minor, patch, 0, parts)
