"""Claude Code as a process: started, talked to over its stdio control protocol, and stopped with
every process it started.

The transport and control channel the Claude Agent SDK speaks in 0.2.165
(`_internal/transport/subprocess_cli.py`, `_internal/query.py`), as much as an executor instance
needs:

- **One JSON object a line**, both ways. Atlas writes prompts and steers as user messages and its
  answers as `control_response`; Claude Code writes its messages, and `control_request` when it
  asks (`can_use_tool`). A `control_cancel_request` withdraws a request still open.
- **One process, many turns**: it lives from `start` to `close`, and every message it writes
  reaches the listener, whether a turn waits for it or not.
- **A request Claude Code asks waits as long as the user takes**: the SDK sets no deadline on it.
  Atlas's own requests to Claude Code (`initialize`, `interrupt`, `set_permission_mode`) wait a
  minute, as the SDK's.
- **Stopping kills the whole process tree**: the SDK stops `claude.exe` only, and the tools and MCP
  servers it started are left behind (its issues #889 and #291). No console window opens on Windows
  (its issue #480).
"""

import asyncio
import contextlib
import json
import os
import secrets
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Sequence
from logging import Logger
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import Account, ExecutorError

from .protocol import (
    Attached,
    account_of,
    command,
    control_error,
    control_request,
    control_response,
    environment,
    status_of,
    user_message,
)

#: How long Atlas waits for Claude Code to answer its own requests (the SDK's 60 seconds).
CONTROL_SECONDS = 60.0
#: How long a closing Claude Code has to write its session down before it is killed (the SDK's 5).
CLOSE_SECONDS = 5.0
#: The longest line read: a tool's result travels whole in one (the SDK keeps 1 MB by default).
LINE_LIMIT = 16 * 1024 * 1024

#: Answers a `can_use_tool` request: the request, and the answer to write back.
type Answerer = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
#: Hears every other message Claude Code writes.
type Listener = Callable[[dict[str, Any]], None]


class ClaudeCodeProcess:
    """One Claude Code session, in `folder`."""

    def __init__(
        self,
        cli: Sequence[str],
        folder: Path,
        *,
        answer: Answerer,
        listen: Listener,
        logger: Logger,
        resume: str | None = None,
        auto: bool = False,
        fork: bool = False,
        model: str | None = None,
        effort: str | None = None,
        account_folder: Path | None = None,
        directories: Sequence[Path] = (),
        chrome: bool = False,
    ) -> None:
        self._cli = cli
        self._folder = folder
        self._answer = answer
        self._listen = listen
        self._logger = logger
        self._resume = resume
        self._auto = auto
        self._fork = fork
        self._model = model
        self._effort = effort
        #: Claude Code's folder for the account it runs on; `None`, the user's own.
        self._account_folder = account_folder
        #: Folders it reads as its own (`--add-dir`): the home's attachments.
        self._directories = tuple(str(directory) for directory in directories)
        #: Whether it starts with Claude in Chrome (`--chrome`).
        self._chrome = chrome
        self._process: asyncio.subprocess.Process | None = None
        self._writing = asyncio.Lock()
        self._waiting: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._asked: dict[str, asyncio.Task[None]] = {}
        self._readers: list[asyncio.Task[None]] = []
        self._stderr: list[str] = []
        #: Set once its output closed: the process ended or is ending.
        self.ended = asyncio.Event()
        #: What its `initialize` answered: the slash commands and the models among it.
        self.initialized: dict[str, Any] = {}

    async def start(self) -> None:
        """Start Claude Code and its control channel. Raises `ExecutorError`."""
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            options["start_new_session"] = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command(
                    self._cli,
                    resume=self._resume,
                    auto=self._auto,
                    fork=self._fork,
                    model=self._model,
                    effort=self._effort,
                    directories=self._directories,
                    chrome=self._chrome,
                ),
                cwd=self._folder,
                env=environment(os.environ, _named(self._account_folder)),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=LINE_LIMIT,
                **options,
            )
        except OSError as error:
            raise ExecutorError(f"Claude Code could not start: {error}") from error
        self._readers = [
            asyncio.create_task(self._pump()),
            asyncio.create_task(self._read_errors()),
        ]
        self.initialized = await self.control({"subtype": "initialize", "hooks": None})

    async def send(self, text: str, files: Sequence[Attached] = ()) -> None:
        """A user message: a turn's prompt, or a steer into the turn that runs, with the files
        attached to it."""
        await self._write(user_message(text, files))

    async def interrupt(self) -> None:
        """Ask Claude Code to stop its turn, as the SDK's `interrupt()`; its turn then ends with
        the reason `aborted_*`."""
        with contextlib.suppress(ExecutorError):
            await self.control({"subtype": "interrupt"})

    async def close(self) -> None:
        """Close its input, give it a moment to write its session down, then stop every process
        it started."""
        process = self._process
        if process is None:
            return
        for task in self._asked.values():
            task.cancel()
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), CLOSE_SECONDS)
        if process.returncode is None:
            await stop_tree(process)
        for reader in self._readers:
            reader.cancel()
        await asyncio.gather(*self._readers, return_exceptions=True)

    @property
    def pid(self) -> int | None:
        """Its process's id, once started."""
        return None if self._process is None else self._process.pid

    @property
    def errors(self) -> str:
        """The end of what Claude Code wrote on its error output."""
        return "\n".join(self._stderr[-20:])

    async def control(self, request: dict[str, Any]) -> dict[str, Any]:
        """One of Atlas's own requests to Claude Code, and its answer. Raises `ExecutorError`."""
        request_id = f"req_{secrets.token_hex(4)}"
        waiting = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = waiting
        try:
            await self._write(control_request(request_id, request))
            async with asyncio.timeout(CONTROL_SECONDS):
                return await waiting
        except TimeoutError as error:
            raise ExecutorError(
                f"Claude Code did not answer {request.get('subtype')} in {CONTROL_SECONDS:.0f} "
                "seconds."
            ) from error
        finally:
            self._waiting.pop(request_id, None)

    # -- the channel ------------------------------------------------------------------------------

    async def _pump(self) -> None:
        """Read Claude Code's lines: control messages are handled here, the rest heard."""
        process = self._process
        if process is None or process.stdout is None:  # pragma: no cover - start() made them
            return
        try:
            while line := await process.stdout.readline():
                try:
                    message = cast("dict[str, Any]", json.loads(line))
                except ValueError:
                    continue
                kind = message.get("type")
                if kind == "control_response":
                    self._replied(cast("dict[str, Any]", message.get("response") or {}))
                elif kind == "control_request":
                    request_id = str(message.get("request_id", ""))
                    request = cast("dict[str, Any]", message.get("request") or {})
                    self._asked[request_id] = asyncio.create_task(self._handle(request_id, request))
                elif kind == "control_cancel_request":
                    asked = self._asked.pop(str(message.get("request_id", "")), None)
                    if asked is not None:
                        asked.cancel()
                else:
                    self._listen(message)
        finally:
            for waiting in self._waiting.values():
                if not waiting.done():
                    waiting.set_exception(ExecutorError("Claude Code ended."))
            self.ended.set()

    async def _handle(self, request_id: str, request: dict[str, Any]) -> None:
        try:
            if request.get("subtype") != "can_use_tool":
                await self._write(control_error(request_id, "Atlas does not handle this request."))
                return
            answer = await self._answer(request)
            await self._write(control_response(request_id, answer))
        except asyncio.CancelledError:
            # Withdrawn by Claude Code, or Atlas is stopping it: nothing is written (the SDK).
            raise
        except Exception as error:  # an answerer is the edge: its failure goes back, logged
            self._logger.exception("A Claude Code request could not be answered.")
            with contextlib.suppress(ExecutorError):
                await self._write(control_error(request_id, str(error)))
        finally:
            self._asked.pop(request_id, None)

    def _replied(self, response: dict[str, Any]) -> None:
        waiting = self._waiting.get(str(response.get("request_id", "")))
        if waiting is None or waiting.done():
            return
        if response.get("subtype") == "error":
            waiting.set_exception(ExecutorError(str(response.get("error", "an unknown error"))))
        else:
            waiting.set_result(cast("dict[str, Any]", response.get("response") or {}))

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.stdin.is_closing():
            raise ExecutorError("Claude Code is no longer listening.")
        async with self._writing:
            try:
                # Escaped, as the SDK writes it (`json.dumps`): no reader's encoding mangles an
                # accent (found by the rehearsal on 12/10/2026, a question's text garbled).
                process.stdin.write((json.dumps(message) + "\n").encode())
                await process.stdin.drain()
            except (ConnectionError, OSError) as error:
                raise ExecutorError(f"Claude Code is no longer listening: {error}") from error

    async def _read_errors(self) -> None:
        process = self._process
        if process is None or process.stderr is None:  # pragma: no cover - start() made them
            return
        while line := await process.stderr.readline():
            self._stderr.append(line.decode("utf-8", "replace").rstrip())
            del self._stderr[:-200]


#: How long a login may take: the user signs in on the browser (Claude Code's own flow).
LOGIN_SECONDS = 15 * 60
#: How long `claude auth status` and `claude --version` may take.
STATUS_SECONDS = 30.0


async def account(cli: Sequence[str], account_folder: Path | None = None) -> Account:
    """Who Claude Code is logged in as now (`claude auth status --json`), in the user's own folder
    or `account_folder`, read whatever its exit code: nobody logged in exits 1, saying so. Not
    logged in when it cannot say."""
    try:
        output = await run_cli(
            cli, ["auth", "status", "--json"], STATUS_SECONDS, check=False, folder=account_folder
        )
    except ExecutorError:
        return Account(logged_in=False)
    status = status_of(output)
    return Account(logged_in=False) if status is None else account_of(status)


async def log_in(cli: Sequence[str], email: str, account_folder: Path | None = None) -> Account:
    """Claude Code's own login for `email` (`claude auth login --email`; with none, whichever
    account the user signs in to), which opens the user's browser on Anthropic's page and keeps
    the login in the user's own folder or `account_folder`; Atlas never sees the credentials.
    Then who it is logged in as. Raises `ExecutorError` when the login fails or takes longer than
    15 minutes."""
    arguments = ["auth", "login", "--claudeai", *(["--email", email] if email else [])]
    await run_cli(cli, arguments, LOGIN_SECONDS, folder=account_folder)
    return await account(cli, account_folder)


async def run_cli(
    cli: Sequence[str],
    arguments: list[str],
    seconds: float,
    *,
    check: bool = True,
    folder: Path | None = None,
) -> str:
    """One of Claude Code's own commands, its output, on the user's own folder or the account
    folder `folder`. Raises `ExecutorError`; with `check` off, not for an exit code that says
    something other than success, whose output is read too."""
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    named = " ".join(arguments[:2])
    try:
        process = await asyncio.create_subprocess_exec(
            *cli,
            *arguments,
            env=environment(os.environ, _named(folder)),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **options,
        )
    except OSError as error:
        raise ExecutorError(f"Claude Code could not start: {error}") from error
    try:
        output, errors = await asyncio.wait_for(process.communicate(), seconds)
    except TimeoutError as error:
        await stop_tree(process)
        raise ExecutorError(f"claude {named} did not finish in {seconds:.0f} seconds.") from error
    if check and process.returncode != 0:
        said = errors.decode("utf-8", "replace").strip() or output.decode("utf-8", "replace")
        raise ExecutorError(f"claude {named} failed: {said.strip()[:500]}")
    return output.decode("utf-8", "replace")


async def stop_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a process and every process it started."""
    if process.returncode is None:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/T",
                "/F",
                "/PID",
                str(process.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await killer.wait()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), CLOSE_SECONDS)


def _named(folder: Path | None) -> str | None:
    """An account folder as Claude Code is handed it: absolute and written one way, since it keys
    the login by the exact text (Claude Code's #92252: a trailing separator or an unexpanded `~`
    names another, empty, folder)."""
    return None if folder is None else os.path.normpath(folder.expanduser().absolute())
