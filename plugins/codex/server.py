"""Codex's `app-server` as a process: started, talked to over JSON-RPC on its stdio, and stopped
with every process it started.

Asynchronous, and cut to what an executor needs:

- **One JSON object a line**, both ways: Atlas's requests and Codex's replies, Codex's
  notifications, and the requests it makes of Atlas (an approval, a question), which Atlas answers.
- **A request Codex makes waits as long as the user takes**; one it withdraws
  (`serverRequest/resolved`, or its turn ending) is cancelled. Atlas's own requests wait a minute.
- **A line may be long**: the connectors' list of one account passed 64 KB on 20/10/2026, so the
  reader takes 16 MB, as the Claude Code plugin's.
- **Stopping kills the whole process tree** (the MCP servers and commands it started), with no
  console window on Windows.
"""

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Sequence
from logging import Logger
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import ExecutorError

from .protocol import (
    environment,
    error_response,
    initialize_params,
    notification,
    request,
    response,
)

#: How long Atlas waits for Codex to answer its own requests.
REQUEST_SECONDS = 60.0
#: How long a closing Codex has to write its session down before it is killed.
CLOSE_SECONDS = 5.0
#: The longest line read.
LINE_LIMIT = 16 * 1024 * 1024
#: The version Atlas names itself with in the handshake.
CLIENT_VERSION = "1"

#: Answers a request Codex makes: its method and params, and the result to write back.
type Answerer = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
#: Hears every notification Codex writes.
type Listener = Callable[[dict[str, Any]], None]


async def _refuse(method: str, params: dict[str, Any]) -> dict[str, Any]:
    raise ExecutorError("Atlas only asked what Codex has.")


class CodexServer:
    """One `codex app-server`, in `folder`."""

    def __init__(
        self,
        cli: Sequence[str],
        folder: Path,
        *,
        logger: Logger,
        answer: Answerer = _refuse,
        listen: Listener = lambda _message: None,
        answered: Callable[[], None] = lambda: None,
        home: Path | None = None,
    ) -> None:
        self._cli = list(cli)
        self._folder = folder
        self._logger = logger
        self._answer = answer
        self._listen = listen
        #: Told once an answer to one of Codex's requests is written.
        self._answered = answered
        #: Codex's folder for the account it runs on; `None`, the user's own.
        self._home = home
        self._process: asyncio.subprocess.Process | None = None
        self._writing = asyncio.Lock()
        self._next = 1
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._asked: dict[str, asyncio.Task[None]] = {}
        self._readers: list[asyncio.Task[None]] = []
        self._stderr: list[str] = []
        #: Set once its output closed: the process ended or is ending.
        self.ended = asyncio.Event()
        #: What its `initialize` answered.
        self.initialized: dict[str, Any] = {}

    async def start(self) -> None:
        """Start Codex and its handshake. Raises `ExecutorError`."""
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            options["start_new_session"] = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._cli,
                "app-server",
                cwd=self._folder,
                env=environment(os.environ, named(self._home)),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=LINE_LIMIT,
                **options,
            )
        except OSError as error:
            raise ExecutorError(f"Codex could not start: {error}") from error
        self._readers = [
            asyncio.create_task(self._pump()),
            asyncio.create_task(self._read_errors()),
        ]
        self.initialized = await self.request("initialize", initialize_params(CLIENT_VERSION))
        await self._write(notification("initialized"))

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """One of Atlas's own requests to Codex, and its result. Raises `ExecutorError`."""
        request_id, self._next = self._next, self._next + 1
        waiting = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = waiting
        try:
            await self._write(request(request_id, method, params))
            async with asyncio.timeout(REQUEST_SECONDS):
                return await waiting
        except TimeoutError as error:
            raise ExecutorError(
                f"Codex did not answer {method} in {REQUEST_SECONDS:.0f} seconds."
            ) from error
        finally:
            self._waiting.pop(request_id, None)

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

    def withdraw(self, request_id: str) -> None:
        """Codex no longer waits on one of its requests: what answers it is cancelled."""
        asked = self._asked.pop(request_id, None)
        if asked is not None:
            asked.cancel()

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def errors(self) -> str:
        """The end of what Codex wrote on its error output."""
        return "\n".join(self._stderr[-20:])

    # -- the channel ------------------------------------------------------------------------------

    async def _pump(self) -> None:
        process = self._process
        if process is None or process.stdout is None:  # pragma: no cover - start() made them
            return
        try:
            while line := await process.stdout.readline():
                try:
                    message = cast("dict[str, Any]", json.loads(line))
                except ValueError:
                    continue
                if not isinstance(message, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
                    continue
                if "method" not in message:
                    self._replied(message)
                elif "id" in message:
                    key = str(message["id"])
                    self._asked[key] = asyncio.create_task(self._handle(message))
                else:
                    if message.get("method") == "serverRequest/resolved":
                        params = cast("dict[str, Any]", message.get("params") or {})
                        self.withdraw(str(params.get("requestId", "")))
                    self._listen(message)
        finally:
            for waiting in self._waiting.values():
                if not waiting.done():
                    waiting.set_exception(ExecutorError("Codex ended."))
            self.ended.set()

    async def _handle(self, message: dict[str, Any]) -> None:
        request_id: object = message.get("id")
        method = str(message.get("method") or "")
        params = cast("dict[str, Any]", message.get("params") or {})
        try:
            result = await self._answer(method, params)
            await self._write(response(request_id, result))
            self._answered()
        except asyncio.CancelledError:
            raise
        except ExecutorError as error:
            with contextlib.suppress(ExecutorError):
                await self._write(error_response(request_id, str(error)))
        except Exception as error:  # an answerer is the edge: its failure goes back, logged
            self._logger.exception("A Codex request could not be answered.")
            with contextlib.suppress(ExecutorError):
                await self._write(error_response(request_id, str(error)))
        finally:
            self._asked.pop(str(request_id), None)

    def _replied(self, message: dict[str, Any]) -> None:
        raw: object = message.get("id")
        if not isinstance(raw, int):
            return
        waiting = self._waiting.get(raw)
        if waiting is None or waiting.done():
            return
        error: object = message.get("error")
        if isinstance(error, dict):
            said = cast("dict[str, Any]", error).get("message") or "an unknown error"
            waiting.set_exception(ExecutorError(str(said)))
        else:
            waiting.set_result(cast("dict[str, Any]", message.get("result") or {}))

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.stdin.is_closing():
            raise ExecutorError("Codex is no longer listening.")
        async with self._writing:
            try:
                process.stdin.write((json.dumps(message) + "\n").encode())
                await process.stdin.drain()
            except (ConnectionError, OSError) as error:
                raise ExecutorError(f"Codex is no longer listening: {error}") from error

    async def _read_errors(self) -> None:
        process = self._process
        if process is None or process.stderr is None:  # pragma: no cover - start() made them
            return
        while line := await process.stderr.readline():
            self._stderr.append(line.decode("utf-8", "replace").rstrip())
            del self._stderr[:-200]


#: How long a login may take: the user signs in on the browser (Codex's own flow).
LOGIN_SECONDS = 15 * 60
#: How long `codex --version` may take.
STATUS_SECONDS = 30.0


async def run_cli(
    cli: Sequence[str], arguments: list[str], seconds: float, *, home: Path | None = None
) -> str:
    """One of Codex's own commands, its output, on the user's own folder or the account folder
    `home`. Raises `ExecutorError`."""
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    named_command = " ".join(arguments[:2])
    try:
        process = await asyncio.create_subprocess_exec(
            *cli,
            *arguments,
            env=environment(os.environ, named(home)),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **options,
        )
    except OSError as error:
        raise ExecutorError(f"Codex could not start: {error}") from error
    try:
        output, errors = await asyncio.wait_for(process.communicate(), seconds)
    except TimeoutError as error:
        await stop_tree(process)
        raise ExecutorError(
            f"codex {named_command} did not finish in {seconds:.0f} seconds."
        ) from error
    if process.returncode != 0:
        said = errors.decode("utf-8", "replace").strip() or output.decode("utf-8", "replace")
        raise ExecutorError(f"codex {named_command} failed: {said.strip()[:500]}")
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


def named(folder: Path | None) -> str | None:
    """An account folder as Codex is handed it: absolute and written one way."""
    return None if folder is None else os.path.normpath(folder.expanduser().absolute())
