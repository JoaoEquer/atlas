"""Google Workspace, through `gws`: the plugin's `register` (step 26; the core's since step 14b).

- **The skills** (`google:gws-gmail`, `google:gws-calendar` and the rest, `google:gws-shared`
  first) teach the model Gmail and Calendar through `gws`, which keeps its own login on the
  machine: the plugin never sees a token.
- **The connection**: its screen says whether `gws` is on the machine and who it is logged in as
  (`status`). Setting `gws` up, with its Google Cloud project, is done with Atlas in a
  conversation, which the screen starts.

What `gws` may do without asking (`gws gmail send` and the others) is not this plugin's: Atlas's
gate keeps its list of actions that reach outside, whatever brought the program.

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from typing import Any, cast

from atlas.plugins.sdk import PluginContext

#: What reads the login, without asking for anything.
STATUS = ("auth", "status")
STATUS_SECONDS = 20
#: Windows opens no console window for it.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def program(environment: Mapping[str, str]) -> list[str] | None:
    """How to run `gws` from `environment`'s PATH, or `None` when it is not on it. An npm
    install is a .cmd on Windows, which runs only through cmd."""
    found = shutil.which("gws", path=environment.get("PATH") or environment.get("Path"))
    if found is None:
        return None
    if sys.platform == "win32" and found.lower().endswith((".cmd", ".bat")):
        return ["cmd", "/c", found]
    return [found]


def parse_status(text: str) -> tuple[bool, str]:
    """Whether `gws auth status` holds a token Google takes, and the e-mail when it says one.

    It prints a line or two before its JSON. A token Google refused (`token_valid` false, with
    "expired or revoked") is no login: the user signs in again.
    """
    start = text.find("{")
    try:
        found: object = json.loads(text[start:]) if start >= 0 else None
    except ValueError:
        found = None
    status = cast("dict[str, object]", found) if isinstance(found, dict) else None
    if status is None or status.get("token_valid") is not True:
        return False, ""
    for key in ("user", "email", "account"):
        value = status.get(key)
        if isinstance(value, str) and value:
            return True, value
    return True, ""


async def read_status(argv: list[str], environment: Mapping[str, str]) -> str:
    """What `gws auth status` prints, whatever its exit; empty when it does not answer in time."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            *STATUS,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(environment),
            creationflags=_NO_WINDOW,
        )
    except OSError:
        return ""
    try:
        output, _ = await asyncio.wait_for(process.communicate(), STATUS_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        return ""
    return output.decode("utf-8", errors="replace")


async def status(_params: Any) -> dict[str, Any]:
    """Whether `gws` is on this machine, and who it is logged in as."""
    argv = program(os.environ)
    if argv is None:
        return {"installed": False, "logged_in": False, "account": ""}
    logged_in, account = parse_status(await read_status(argv, os.environ))
    return {"installed": True, "logged_in": logged_in, "account": account}


def register(ctx: PluginContext) -> None:
    ctx.register_call("status", status)
