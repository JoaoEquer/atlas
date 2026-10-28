"""Chrome and the browser engine as processes: Chrome found, opened on its own profile and closed,
and browser-harness run on a call's code, stopped with every process it started.

Chrome is opened as the user would open a second profile: `--user-data-dir` and a DevTools port of
the system's choosing, read back from the profile's `DevToolsActivePort` (a file a Chrome that was
killed leaves behind, so it is removed before a start; found by the rehearsal on 12/10/2026). A
Chrome already running on the profile, from an earlier Atlas, is used as it is: a second one on the
same profile would only hand its window to the first and leave.
"""

import asyncio
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import websockets

from atlas.browser.errors import BrowserError
from atlas.browser.model import DEVTOOLS_FILE, chrome_arguments, devtools_endpoint

#: How long Chrome may take to write its DevTools endpoint.
START_SECONDS = 30.0
#: How long a browser whose first process exited cleanly may still take to write its endpoint:
#: Edge's launcher hands the window to another process and ends (found on 25/10/2026).
HANDOFF_SECONDS = 5.0
#: How long a closing Chrome, or a stopped engine, has before it is killed.
CLOSE_SECONDS = 5.0
#: How long a check that Chrome still listens may take.
PROBE_SECONDS = 1.0
#: The engine's own command: browser-harness on Atlas's interpreter.
ENGINE: tuple[str, ...] = (sys.executable, "-m", "browser_harness.run")


def find_chrome() -> Path | None:
    """The first Chrome, Edge or Chromium found where they install, Chrome first."""
    candidates: list[Path] = []
    if sys.platform == "win32":
        roots = [
            os.environ.get(name) for name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
        ]
        for relative in (
            "Google/Chrome/Application/chrome.exe",
            "Microsoft/Edge/Application/msedge.exe",
            "Chromium/Application/chrome.exe",
        ):
            candidates += [Path(root) / relative for root in roots if root]
    elif sys.platform == "darwin":
        candidates = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
    else:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                     "microsoft-edge"):  # fmt: skip
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return next((candidate for candidate in candidates if candidate.is_file()), None)


@dataclass
class Chrome:
    """One Chrome on one profile folder, and its DevTools endpoint."""

    profile: Path
    endpoint: str
    #: `None` for a Chrome Atlas found running and did not start.
    process: asyncio.subprocess.Process | None


async def open_chrome(program: Sequence[str], profile: Path, *, headless: bool) -> Chrome:
    """The Chrome on `profile`: the one running there, or a new one. Raises `BrowserError`."""
    profile.mkdir(parents=True, exist_ok=True)
    port_file = profile / DEVTOOLS_FILE
    running = _endpoint(port_file)
    if running is not None and await listening(running):
        return Chrome(profile, running, None)
    port_file.unlink(missing_ok=True)
    first, *rest = program
    arguments = chrome_arguments(first, str(profile), headless=headless)
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = await asyncio.create_subprocess_exec(
            arguments[0],
            *rest,
            *arguments[1:],
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            **options,
        )
    except OSError as error:
        raise BrowserError(f"The browser could not start: {error}") from error
    loop = asyncio.get_running_loop()
    deadline = loop.time() + START_SECONDS
    handed_off: float | None = None
    while loop.time() < deadline:
        endpoint = _endpoint(port_file)
        if endpoint is not None:
            return Chrome(profile, endpoint, process)
        if process.returncode is not None:
            handed_off = loop.time() if handed_off is None else handed_off
            if process.returncode != 0 or loop.time() - handed_off > HANDOFF_SECONDS:
                raise BrowserError(
                    f"The browser ended as it started (exit code {process.returncode}). Is "
                    f"another program using its profile, {profile}?"
                )
        await asyncio.sleep(0.1)
    await stop_tree(process)
    raise BrowserError(
        f"The browser did not open its DevTools port in {START_SECONDS:.0f} seconds."
    )


async def listening(endpoint: str) -> bool:
    """Whether something still listens on the endpoint's port."""
    try:
        port = int(endpoint.split(":")[2].split("/")[0])
        async with asyncio.timeout(PROBE_SECONDS):
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    except (OSError, ValueError, IndexError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def close_chrome(chrome: Chrome) -> None:
    """Close Chrome as its own menu would (`Browser.close`), so the profile keeps what it wrote,
    then stop whatever is left of it."""
    with contextlib.suppress(OSError, websockets.WebSocketException, TimeoutError):
        async with asyncio.timeout(CLOSE_SECONDS):
            async with websockets.connect(chrome.endpoint, max_size=None) as socket:
                await socket.send(json.dumps({"id": 1, "method": "Browser.close"}))
                with contextlib.suppress(websockets.ConnectionClosed):
                    await socket.recv()
    process = chrome.process
    if process is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), CLOSE_SECONDS)
        await stop_tree(process)
    # A browser whose launcher handed off is not the process waited on: it is gone when it stops
    # listening, and only then does its profile stop being held. One that still listens keeps its
    # endpoint, so the next start uses it: a second launcher on the profile would only hand its
    # window to it and leave.
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(CLOSE_SECONDS):
            while await listening(chrome.endpoint):
                await asyncio.sleep(0.1)
            (chrome.profile / DEVTOOLS_FILE).unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class EngineRun:
    """What the engine did with a call's code."""

    exit_code: int
    output: str
    errors: str


async def run_engine(
    engine: Sequence[str], code: str, environment: dict[str, str], seconds: float
) -> EngineRun:
    """Run the code through the engine and wait for it. Raises `TimeoutError` after `seconds`,
    with its whole process tree stopped (a daemon that inherited the pipes would keep the call
    waiting forever), and `BrowserError` when it cannot start."""
    process = await _start(engine, environment, stdin=True)
    try:
        output, errors = await asyncio.wait_for(process.communicate(code.encode()), seconds)
    except TimeoutError:
        await stop_tree(process)
        raise
    except asyncio.CancelledError:
        await asyncio.shield(stop_tree(process))
        raise
    return EngineRun(
        process.returncode if process.returncode is not None else -1,
        output.decode("utf-8", "replace"),
        errors.decode("utf-8", "replace"),
    )


async def stop_engine(engine: Sequence[str], environment: dict[str, str]) -> None:
    """Stop the engine's daemon of a session (`--reload`, its own identity-checked stop); the next
    call starts another."""
    with contextlib.suppress(BrowserError, TimeoutError):
        process = await _start([*engine, "--reload"], environment, stdin=False)
        try:
            await asyncio.wait_for(process.communicate(), CLOSE_SECONDS * 3)
        except TimeoutError:
            await stop_tree(process)


async def _start(
    command: Sequence[str], environment: dict[str, str], *, stdin: bool
) -> asyncio.subprocess.Process:
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        return await asyncio.create_subprocess_exec(
            *command,
            env=environment,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **options,
        )
    except OSError as error:
        raise BrowserError(f"The browser engine could not start: {error}") from error


async def stop_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a process and every process it started."""
    if process.returncode is None:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(process.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )  # fmt: skip
            await killer.wait()
        else:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), CLOSE_SECONDS)


def remove_profile(profile: Path) -> None:
    """Delete a temporary profile; a file Chrome still holds is left for the next try."""
    shutil.rmtree(profile, ignore_errors=True)


def read_image(path: Path, since: float, limit: int) -> bytes | None:
    """A screenshot's bytes, when the file was written by this call and is not too large."""
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_mtime < since - 1 or stat.st_size > limit:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _endpoint(port_file: Path) -> str | None:
    try:
        return devtools_endpoint(port_file.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
