"""GitHub, through `gh`: the plugin's `register` (step 26; the core's since step 14b).

- **The skill** `github:github` teaches the model to work with GitHub through `gh`, which keeps
  its own login on the machine: the plugin never sees a token.
- **The connection**: its screen says whether `gh` is on the machine and who it is logged in as
  (`status`), and starts `gh`'s own login in the browser, giving the one-time code to type there
  (`log_in`); one login at a time, stopped when the plugin is turned off.

What `gh` may do without asking (`gh pr merge`, `gh repo delete` and the others) is not this
plugin's: Atlas's gate keeps its list of actions that reach outside, whatever brought the program.

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

import asyncio
import os
from typing import Any

from atlas.plugins.sdk import PluginContext

from .programs import (
    LOGIN,
    LOGIN_SECONDS,
    STATUS,
    STATUS_SECONDS,
    DeviceCode,
    LoginError,
    finish,
    parse_status,
    program,
    read,
    start_login,
    stop,
)


class Github:
    """`gh`'s login, read, and one login in the browser at a time."""

    def __init__(self) -> None:
        self._login: tuple[DeviceCode, asyncio.subprocess.Process, asyncio.Task[bool]] | None = None

    async def status(self, _params: Any) -> dict[str, Any]:
        """Whether `gh` is on this machine, and who it is logged in as."""
        argv = program("gh", os.environ)
        if argv is None:
            return {"installed": False, "logged_in": False, "account": "", "waiting": False}
        account = parse_status(await read([*argv, *STATUS], os.environ, STATUS_SECONDS))
        waiting = self._login is not None and not self._login[2].done()
        return {
            "installed": True,
            "logged_in": account is not None,
            "account": account or "",
            "waiting": waiting,
        }

    async def log_in(self, _params: Any) -> dict[str, str]:
        """Start `gh`'s login in the browser and give its code, or the code of the one still
        waiting."""
        if self._login is not None and not self._login[2].done():
            code = self._login[0]
            return {"code": code.code, "url": code.url}
        argv = program("gh", os.environ)
        if argv is None:
            raise LoginError(
                "GitHub's program, gh, is not on this machine: install it from cli.github.com."
            )
        process, code = await start_login([*argv, *LOGIN], os.environ)
        self._login = (code, process, asyncio.create_task(finish(process, LOGIN_SECONDS)))
        return {"code": code.code, "url": code.url}

    async def close(self) -> None:
        """Stop a login still waiting for the browser."""
        if self._login is not None:
            # Ended rather than cancelled: the wait reads its output to the end, and the pipes
            # close (a read cancelled on Windows leaves its pipe behind).
            _, process, task = self._login
            self._login = None
            await stop(process)
            await asyncio.gather(task, return_exceptions=True)


def register(ctx: PluginContext) -> None:
    github = Github()
    ctx.register_call("status", github.status)
    ctx.register_call("log_in", github.log_in)
    ctx.on_unload(github.close)
