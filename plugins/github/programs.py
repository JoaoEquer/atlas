"""Running `gh`: its login's status read, and its login in the browser kept running until the
browser said yes. `gh` keeps its own login on the machine; the plugin only reads what it prints."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

#: What reads the login, without asking for anything.
STATUS = ("auth", "status", "--hostname", "github.com", "--json", "hosts")
#: The login in the browser: away from a terminal it prints a one-time code and the page to type
#: it on, then waits until the browser said yes. Git over HTTPS, as the github skill says.
LOGIN = ("auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web")
#: How long a status may take, how long a login waits for the browser, and for its code.
STATUS_SECONDS = 20
LOGIN_SECONDS = 15 * 60
CODE_SECONDS = 30

#: Windows opens no console window for these programs.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
#: What of a program's output an error keeps.
_OUTPUT_CHARS = 400
_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_URL = re.compile(r"https://\S+")


class LoginError(Exception):
    """A login could not start or be read; the message says what to do."""


class GhError(Exception):
    """`gh` could not do what was asked; the message is what it said."""


#: How long a call of `gh` about a pull request or the issues may take.
CALL_SECONDS = 60


@dataclass(frozen=True, slots=True)
class DeviceCode:
    """What the user types in the browser, and where."""

    code: str
    url: str


def program(name: str, environment: Mapping[str, str]) -> list[str] | None:
    """How to run `name` from `environment`'s PATH, or `None` when it is not on it. An npm
    install is a .cmd on Windows, which runs only through cmd."""
    found = shutil.which(name, path=environment.get("PATH") or environment.get("Path"))
    if found is None:
        return None
    if sys.platform == "win32" and found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", found]
    return [found]


def parse_status(text: str) -> str | None:
    """The account `gh auth status --json hosts` says is active on github.com, or `None`."""
    try:
        found: object = json.loads(text)
    except ValueError:
        return None
    hosts = cast("dict[str, object]", found).get("hosts") if isinstance(found, dict) else None
    if not isinstance(hosts, dict):
        return None
    accounts = cast("dict[str, object]", hosts).get("github.com")
    if not isinstance(accounts, list):
        return None
    for each in cast("list[object]", accounts):
        if not isinstance(each, dict):
            continue
        account = cast("dict[str, object]", each)
        if account.get("state") == "success" and account.get("active") is True:
            login = account.get("login")
            return login if isinstance(login, str) else ""
    return None


def parse_device_code(text: str) -> DeviceCode | None:
    """The one-time code and its page in what `gh auth login --web` printed so far."""
    code = _CODE.search(text)
    url = _URL.search(text)
    if code is None or url is None:
        return None
    return DeviceCode(code.group(1), url.group(0).rstrip(".,"))


async def _spawn(
    argv: Sequence[str],
    environment: Mapping[str, str],
    *,
    stderr: int,
    folder: str | None = None,
) -> asyncio.subprocess.Process:
    try:
        return await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            env=dict(environment),
            cwd=folder,
            creationflags=_NO_WINDOW,
        )
    except OSError as error:
        raise LoginError(f"{' '.join(argv)} could not start: {error}") from error


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


async def call(
    argv: Sequence[str], environment: Mapping[str, str], folder: str, seconds: float
) -> tuple[bool, str, str]:
    """`argv` run in `folder`: whether it succeeded, its output and its errors."""
    process = await _spawn(argv, environment, stderr=asyncio.subprocess.PIPE, folder=folder)
    try:
        output, errors = await asyncio.wait_for(process.communicate(), seconds)
    except TimeoutError:
        await stop(process)
        raise GhError(f"gh did not answer in {seconds:.0f} seconds.") from None
    return (
        process.returncode == 0,
        output.decode("utf-8", errors="replace"),
        errors.decode("utf-8", errors="replace"),
    )


def parse_pull_request(text: str) -> dict[str, Any]:
    """What `gh pr view --json number,url,state,title,isDraft,statusCheckRollup` said, as the
    screen shows it: each check by its name and how it stands (its conclusion once it ended, its
    status until then), lower case."""
    found = json.loads(text)
    checks: list[dict[str, str]] = []
    for each in cast("list[dict[str, Any]]", found.get("statusCheckRollup") or []):
        name = each.get("name") or each.get("context") or ""
        state = each.get("conclusion") or each.get("state") or each.get("status") or ""
        checks.append({"name": str(name), "state": str(state).lower()})
    return {
        "exists": True,
        "number": int(found.get("number") or 0),
        "url": str(found.get("url") or ""),
        "state": str(found.get("state") or "").lower(),
        "title": str(found.get("title") or ""),
        "draft": bool(found.get("isDraft")),
        "checks": checks,
    }


def last_words(errors: str) -> str:
    """What `gh` said last on its errors, for the screen."""
    lines = [line.strip() for line in errors.splitlines() if line.strip()]
    return lines[-1][:_OUTPUT_CHARS] if lines else "gh failed."


async def start_login(
    argv: Sequence[str], environment: Mapping[str, str]
) -> tuple[asyncio.subprocess.Process, DeviceCode]:
    """Start a login in the browser and wait for the code it prints; the process goes on."""
    process = await _spawn(argv, environment, stderr=asyncio.subprocess.STDOUT)
    output = process.stdout
    if output is None:  # never: it is a pipe
        raise LoginError("The login's output could not be read.")
    said = ""
    try:
        async with asyncio.timeout(CODE_SECONDS):
            while (code := parse_device_code(said)) is None:
                chunk = await output.read(1024)
                if not chunk:
                    await process.wait()
                    raise LoginError(
                        "The login ended before it gave a code: "
                        + (said.strip()[-_OUTPUT_CHARS:] or f"exit {process.returncode}")
                    )
                said += chunk.decode("utf-8", errors="replace")
    except TimeoutError as error:
        await stop(process)
        raise LoginError("The login gave no code in time; try again.") from error
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
