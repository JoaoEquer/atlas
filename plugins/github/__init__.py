"""GitHub, through `gh`: the plugin's `register` (step 26; the core's since step 14b).

- **The skill** `github:github` teaches the model to work with GitHub through `gh`, which keeps
  its own login on the machine: the plugin never sees a token.
- **The connection**: its screen says whether `gh` is on the machine and who it is logged in as
  (`status`), and starts `gh`'s own login in the browser, giving the one-time code to type there
  (`log_in`); one login at a time, stopped when the plugin is turned off.
- **A workspace's pull request** (step 21, A4): beside a workspace's changes, its screen says the
  pull request of its branch and how its checks stand (`pull_request`), or opens one at the user's
  click once Atlas sent the branch (`open_pull_request`).
- **The issues** (A5): under a project among the workspaces, its open issues (`issues`), each of
  which starts a workspace at the user's click, its session beginning with the issue.

What `gh` may do without asking (`gh pr merge`, `gh repo delete` and the others) is not this
plugin's: Atlas's gate keeps its list of actions that reach outside, whatever brought the program.

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

import asyncio
import json
import os
from typing import Any, cast

from atlas.plugins.sdk import PluginContext

from .programs import (
    CALL_SECONDS,
    LOGIN,
    LOGIN_SECONDS,
    STATUS,
    STATUS_SECONDS,
    DeviceCode,
    GhError,
    LoginError,
    call,
    finish,
    last_words,
    parse_pull_request,
    parse_status,
    program,
    read,
    start_login,
    stop,
)

#: What a pull request is read with.
PR_FIELDS = "number,url,state,title,isDraft,statusCheckRollup"
#: How many open issues are listed.
ISSUES = 30


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

    async def pull_request(self, params: Any) -> dict[str, Any]:
        """The pull request of a workspace's branch, with its checks; `exists` false when the
        branch has none."""
        folder, branch = _text(params, "folder"), _text(params, "branch")
        done, output, errors = await call(
            [*_gh(), "pr", "view", branch, "--json", PR_FIELDS], os.environ, folder, CALL_SECONDS
        )
        if not done:
            if "no pull requests found" in errors.lower():
                return {"exists": False}
            raise GhError(last_words(errors))
        return parse_pull_request(output)

    async def open_pull_request(self, params: Any) -> dict[str, Any]:
        """A pull request of a workspace's branch into the branch it came from, which Atlas sent
        first; its title the user's."""
        folder, branch = _text(params, "folder"), _text(params, "branch")
        argv = [
            *_gh(), "pr", "create", "--head", branch, "--base", _text(params, "base"),
            "--title", _text(params, "title"), "--body", "",
        ]  # fmt: skip
        if params.get("draft") is True:
            argv.append("--draft")
        done, _, errors = await call(argv, os.environ, folder, CALL_SECONDS)
        if not done:
            raise GhError(last_words(errors))
        return await self.pull_request({"folder": folder, "branch": branch})

    async def issues(self, params: Any) -> list[dict[str, Any]]:
        """The project's open issues, the newest first."""
        done, output, errors = await call(
            [
                *_gh(), "issue", "list", "--state", "open", "--limit", str(ISSUES),
                "--json", "number,title,body,url",
            ],
            os.environ,
            _text(params, "folder"),
            CALL_SECONDS,
        )  # fmt: skip
        if not done:
            raise GhError(last_words(errors))
        listed: object = json.loads(output or "[]")
        if not isinstance(listed, list):
            return []
        issues = [
            cast("dict[str, Any]", issue)
            for issue in cast("list[object]", listed)
            if isinstance(issue, dict)
        ]
        return [
            {
                "number": int(issue.get("number") or 0),
                "title": str(issue.get("title") or ""),
                "body": str(issue.get("body") or ""),
                "url": str(issue.get("url") or ""),
            }
            for issue in issues
        ]

    async def close(self) -> None:
        """Stop a login still waiting for the browser."""
        if self._login is not None:
            # Ended rather than cancelled: the wait reads its output to the end, and the pipes
            # close (a read cancelled on Windows leaves its pipe behind).
            _, process, task = self._login
            self._login = None
            await stop(process)
            await asyncio.gather(task, return_exceptions=True)


def _gh() -> list[str]:
    argv = program("gh", os.environ)
    if argv is None:
        raise GhError(
            "GitHub's program, gh, is not on this machine: install it from cli.github.com."
        )
    return argv


def _text(params: Any, name: str) -> str:
    """A screen's parameter that must be text: a screen's JSON is not trusted to be well formed."""
    fields = cast("dict[str, object]", params) if isinstance(params, dict) else {}
    value = fields.get(name)
    if not isinstance(value, str) or not value.strip():
        raise GhError(f"The call needs {name}.")
    return value.strip()


def register(ctx: PluginContext) -> None:
    github = Github()
    ctx.register_call("status", github.status)
    ctx.register_call("log_in", github.log_in)
    ctx.register_call("pull_request", github.pull_request)
    ctx.register_call("open_pull_request", github.open_pull_request)
    ctx.register_call("issues", github.issues)
    ctx.on_unload(github.close)
