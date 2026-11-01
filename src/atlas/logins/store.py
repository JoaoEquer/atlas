"""Running `gh` and `gws`: their status read, and `gh`'s login in the browser kept running until
the browser said yes."""

import asyncio
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence

from atlas.logins.errors import LoginsError
from atlas.logins.model import CODE_SECONDS, DeviceCode, parse_device_code

#: Windows opens no console window for these programs.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
#: What of a program's output an error keeps.
_OUTPUT_CHARS = 400


def program(name: str, environment: Mapping[str, str]) -> list[str] | None:
    """How to run `name` from `environment`'s PATH, or `None` when it is not on it. An npm
    install (`gws`) is a .cmd on Windows, which runs only through cmd."""
    found = shutil.which(name, path=environment.get("PATH") or environment.get("Path"))
    if found is None:
        return None
    if sys.platform == "win32" and found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", found]
    return [found]


async def _spawn(
    argv: Sequence[str], environment: Mapping[str, str], *, stderr: int
) -> asyncio.subprocess.Process:
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            env=dict(environment),
            creationflags=_NO_WINDOW,
        )
    except OSError as error:
        raise LoginsError(f"{' '.join(argv)} could not start: {error}") from error


async def read(argv: Sequence[str], environment: Mapping[str, str], seconds: float) -> str:
    """What a status command prints on its output, whatever its exit; empty when it does not
    answer in time."""
    process = await _spawn(argv, environment, stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), seconds)
    except TimeoutError:
        await stop(process)
        return ""
    return output.decode("utf-8", errors="replace")


async def start_login(
    argv: Sequence[str], environment: Mapping[str, str]
) -> tuple[asyncio.subprocess.Process, DeviceCode]:
    """Start a login in the browser and wait for the code it prints; the process goes on."""
    process = await _spawn(argv, environment, stderr=asyncio.subprocess.STDOUT)
    output = process.stdout
    if output is None:  # never: it is a pipe
        raise LoginsError("The login's output could not be read.")
    said = ""
    try:
        async with asyncio.timeout(CODE_SECONDS):
            while (code := parse_device_code(said)) is None:
                chunk = await output.read(1024)
                if not chunk:
                    await process.wait()
                    raise LoginsError(
                        "The login ended before it gave a code: "
                        + (said.strip()[-_OUTPUT_CHARS:] or f"exit {process.returncode}")
                    )
                said += chunk.decode("utf-8", errors="replace")
    except TimeoutError as error:
        await stop(process)
        raise LoginsError("The login gave no code in time; try again.") from error
    return process, code


async def finish(process: asyncio.subprocess.Process, seconds: float) -> bool:
    """Wait for a login started by `start_login` to end, reading what it still prints; whether
    it ended well. Stopped when it waits longer than `seconds`, or is cancelled."""
    try:
        await asyncio.wait_for(process.communicate(), seconds)
    except TimeoutError:
        await stop(process)
        return False
    except asyncio.CancelledError:
        await stop(process)
        raise
    return process.returncode == 0


async def stop(process: asyncio.subprocess.Process) -> None:
    """End the process and what it started (cmd's child on Windows)."""
    if process.returncode is not None:
        return
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/T",
            "/F",
            "/PID",
            str(process.pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        await killer.wait()
    else:
        process.kill()
    await process.wait()
