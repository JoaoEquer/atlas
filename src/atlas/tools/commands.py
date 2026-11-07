"""run_command: a command in the system's shell, in the foreground or in the background.

Decided on 09/10/2026, with what Atlas V3 learned:

- **The system's own shell**: PowerShell on Windows (`pwsh` when installed), bash elsewhere (`sh`
  without it). The description names it, PowerShell's edition included, with 5.1's pitfalls, so
  the model writes that shell's syntax (it writes PowerShell 7's when not told). On Windows a
  preamble makes PowerShell read and write UTF-8 (V3: accents came back garbled, and `Get-Content`
  read UTF-8 files as ANSI).
- **Foreground** returns the output, standard error and standard output together, and the exit
  code; it stops at `timeout` seconds (2 minutes by default, 10 at most).
- **Background** (`run_in_background`) returns at once: the output goes to a file in
  `ATLAS_HOME/commands/`, read with read_file, and a last line says when and how the command ended.
  Reading the file is enough, so no tool for reading that output is added. A background command
  lives as long as Atlas runs; surviving a restart is a job's (step 12 of the roadmap).
- **Stopping stops the whole tree**, children and grandchildren: on 2026-08 V3's `git fetch` with a
  2-minute timeout ran for 2 hours, because a credential helper it started held the pipes after git
  itself was killed. Stopping a turn stops its command too.
- **Nothing waits for a keyboard**: standard input is closed (V3: a command that read it took the
  MCP protocol's bytes and hung the turn), no window opens on Windows, and pagers and colors are
  off (`PAGER=cat`, `NO_COLOR=1`, `TERM=dumb`).
- **Approvals run before the command starts**, background included: the tool gate runs before any
  tool (V3 learned that order the hard way with detached jobs).
- **The screen sees background commands** (decided with the user on 14/10/2026): each one's start
  and end reach `on_change`, for the agent map's list of background tasks, named by the
  `description` the model gives, or by its command when it gives none. The screen reads the last
  4,000 characters of the output, and stops a command by its id.
"""

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path, PureWindowsPath
from typing import Any

from atlas.agent import SpillFolder, Tool
from atlas.home import private_folder, utc_now
from atlas.tools.files import Workspace
from atlas.trust import outside_program, script_texts, untrusted

#: Seconds a foreground command may run when the model does not say, and the most it may ask.
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
#: A foreground command's output kept in memory: its start and its end, never more.
HEAD_BYTES = 1_000_000
TAIL_BYTES = 1_000_000
#: Seconds a stopped tree has to end before it is killed.
GRACE_SECONDS = 3.0
#: The end of a background command's output its notice carries; the file has the rest.
NOTICE_OUTPUT_CHARS = 2_000
#: The end of a background command's output the screen shows (a 200-character preview is too
#: thin).
SCREEN_OUTPUT_CHARS = 4_000
#: Ended background commands the screen can still read the output of, the newest kept.
ENDED_KEPT = 20

_WINDOWS = sys.platform == "win32"
#: The environment for the commands Atlas runs, so nothing pages or colors its output, minus the
#: locale on Windows, where PowerShell's preamble sets the encoding.
_ENVIRONMENT = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "COLORTERM": "",
    "PAGER": "cat",
    "GIT_PAGER": "cat",
    "GH_PAGER": "cat",
    # Python children write as they go, so a background log fills while they run (V3).
    "PYTHONUNBUFFERED": "1",
    # On Windows a Python child writes to a pipe in the ANSI code page, and accents came back
    # garbled; this touches only its standard streams, not the files it opens.
    "PYTHONIOENCODING": "utf-8",
}
_POSIX_LOCALE = {"LANG": "C.UTF-8", "LC_CTYPE": "C.UTF-8", "LC_ALL": "C.UTF-8"}
#: From Atlas V3's sandbox: UTF-8 in and out, and errors that do not end a pipeline midway.
_POWERSHELL_PREAMBLE = (
    "$ErrorActionPreference='Continue'; "
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    "$PSDefaultParameterValues['Get-Content:Encoding']='UTF8'; "
    "$PSDefaultParameterValues['Select-String:Encoding']='UTF8'; "
    "$PSDefaultParameterValues['Import-Csv:Encoding']='UTF8'; "
)
#: `-Command` ends with 1 for any failure, so a program's own exit code was lost. On a line of its
#: own, so a comment at the end of the command does not swallow it: the program's code, or 1 when
#: a cmdlet failed.
_POWERSHELL_EPILOGUE = (
    "\n$atlasOk = $?; if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) "
    "{ exit $LASTEXITCODE } elseif (-not $atlasOk) { exit 1 }"
)
#: What Windows PowerShell 5.1 does differently from PowerShell 7, which models write by default:
#: an `ssh-keygen -N ''` arrived as `-N` with nothing after it, and the key was never made.
_WINDOWS_POWERSHELL = (
    "Windows PowerShell 5.1, not 7: no `&&` or `||` (write `a; if ($?) { b }`), and it drops an "
    "empty argument to a program (write `'\"\"'`, not `''`)."
)


@dataclass(frozen=True, slots=True)
class Shell:
    """The program that runs a command, and how it is named to the model."""

    name: str
    program: str

    @property
    def described(self) -> str:
        """The shell as the model reads it: PowerShell's edition, and 5.1's pitfalls."""
        if self.name != "PowerShell":
            return f"{self.name}."
        if PureWindowsPath(self.program).stem.lower() == "pwsh":
            return "PowerShell 7 (pwsh)."
        return _WINDOWS_POWERSHELL

    def arguments(self, command: str) -> list[str]:
        if self.name == "PowerShell":
            return [
                self.program,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _POWERSHELL_PREAMBLE + command + _POWERSHELL_EPILOGUE,
            ]
        return [self.program, "-c", command]


def system_shell() -> Shell:
    """PowerShell on Windows (pwsh when installed), bash elsewhere, or sh without bash."""
    if _WINDOWS:
        return Shell("PowerShell", shutil.which("pwsh") or "powershell.exe")
    bash = shutil.which("bash")
    return Shell("bash", bash) if bash else Shell("sh", shutil.which("sh") or "/bin/sh")


@dataclass(frozen=True, slots=True)
class BackgroundCommand:
    """A background command as the screen shows it: running, or ended and how."""

    id: str
    command: str
    #: What it does in a few words, as the model named it; empty when it gave none.
    description: str
    output: Path
    started_at: datetime
    #: `running`, `completed` (exit code 0), `failed` (another code) or `stopped`.
    status: str = "running"
    exit_code: int | None = None
    ended_at: datetime | None = None


@dataclass
class _Background:
    id: str
    command: str
    description: str
    process: asyncio.subprocess.Process
    output: Path
    started: float
    started_at: datetime
    watcher: asyncio.Task[None] | None = None
    stopped: bool = False
    notify: bool = False

    def view(self) -> BackgroundCommand:
        return BackgroundCommand(
            self.id, self.command, self.description, self.output, self.started_at
        )


@dataclass
class Commands:
    """Runs commands for one Atlas, and keeps its background commands until they end or it
    closes. `folder` receives the background output (`ATLAS_HOME/commands/`)."""

    workspace: Workspace
    folder: Path
    shell: Shell = field(default_factory=system_shell)
    #: Hears a background command's notice when it ends, if the model asked for one: the
    #: conversation starts a turn with it.
    on_notice: Callable[[str], None] | None = None
    #: Hears each background command start and end, for the screen.
    on_change: Callable[[BackgroundCommand], None] | None = None
    #: The folders put first on the commands' `PATH`, read at each start: the `bin/` of the
    #: plugins that are on (step 16k, as Claude Code's plugin format has it).
    path: Callable[[], Sequence[Path]] = lambda: ()
    _running: dict[str, _Background] = field(default_factory=dict[str, _Background])
    _ended: dict[str, BackgroundCommand] = field(default_factory=dict[str, BackgroundCommand])
    _closing: bool = False
    _count: int = 0

    def tool(self) -> Tool:
        return Tool(
            name="run_command",
            description=(
                "Runs a command and returns its output (errors included) and exit code; it "
                "stops at `timeout` seconds. For a server or a long build, set `background`: it "
                "returns at once, the output goes to a file you read with read_file, and its last "
                "line says when the command ended. For files, prefer find_files, read_file and "
                f"edit_file. The shell is {self.shell.described}"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {
                        "type": "string",
                        "description": "Folder to run in. Defaults to the working folder.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": f"Seconds, up to {MAX_TIMEOUT}. Defaults to "
                        f"{DEFAULT_TIMEOUT}.",
                    },
                    "background": {"type": "boolean"},
                    "notify_on_complete": {
                        "type": "boolean",
                        "description": "With background: be told when it ends.",
                    },
                    "description": {
                        "type": "string",
                        "description": "With background: what it does, in a few words the user "
                        "sees.",
                    },
                    "stop": {
                        "type": "string",
                        "description": "The id of a background command to stop, instead.",
                    },
                },
                "additionalProperties": False,
            },
            run=self._run,
            failure_tolerant=True,
        )

    async def close(self) -> None:
        """Stop every background command still running; none of them notifies."""
        self._closing = True
        for job in list(self._running.values()):
            await self._stop(job)

    def background(self) -> list[BackgroundCommand]:
        """The background commands still running, the oldest first."""
        return [job.view() for job in self._running.values()]

    def screen_output(self, job_id: str) -> str | None:
        """The end of a background command's output, for the screen: one running or among the
        last that ended; `None` for one it does not know."""
        job = self._running.get(job_id)
        ended = self._ended.get(job_id)
        output = job.output if job is not None else None if ended is None else ended.output
        return None if output is None else _tail(output, SCREEN_OUTPUT_CHARS)

    async def stop(self, job_id: str) -> str:
        """Stop a background command by its id, tree and all: what the model reads."""
        job = self._running.get(job_id)
        if job is None:
            running = ", ".join(sorted(self._running)) or "none"
            return f"[Atlas: no background command {job_id} is running. Running: {running}.]"
        await self._stop(job)
        return f"Stopped {job_id}. Its output is in {job.output}."

    # -- running -----------------------------------------------------------------------------

    async def _run(self, arguments: dict[str, Any]) -> str:
        stop = arguments.get("stop")
        if isinstance(stop, str) and stop:
            return await self.stop(stop)
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return "[Atlas: run_command needs a command, or `stop` with a background command's id.]"
        try:
            cwd = self.workspace.resolve(arguments.get("cwd") or str(self.workspace.root))
        except ValueError as error:
            return f"[Atlas: {error}]"
        if not cwd.is_dir():
            return f"[Atlas: {cwd} is not a folder to run in.]"
        if arguments.get("background") is True:
            described = arguments.get("description")
            return await self._start(
                command,
                cwd,
                notify=arguments.get("notify_on_complete") is True,
                description=described.strip() if isinstance(described, str) else "",
            )
        timeout = _seconds(arguments.get("timeout"))
        return await self._foreground(command, cwd, timeout)

    async def _spawn(
        self, command: str, cwd: Path, stdout: Any, extra: dict[str, str] | None = None
    ) -> asyncio.subprocess.Process:
        environment = {
            **os.environ,
            **_ENVIRONMENT,
            **({} if _WINDOWS else _POSIX_LOCALE),
            **(extra or {}),
        }
        if first := [str(folder) for folder in self.path()]:
            environment["PATH"] = os.pathsep.join([*first, environment.get("PATH", "")])
        return await asyncio.create_subprocess_exec(
            *self.shell.arguments(command),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=subprocess.STDOUT,
            env=environment,
            # Its own process group, so the whole tree can be stopped together.
            start_new_session=not _WINDOWS,
            creationflags=_creation_flags(),
        )

    async def execute(
        self, command: str, timeout: int, environment: dict[str, str] | None = None
    ) -> tuple[str, int | None]:
        """Run a command in the working folder, nobody watching (a routine's command or probe): its
        output, and its exit code, `None` when it ran past `timeout` and was stopped with its tree.
        `environment` adds variables to the command's."""
        return await self._output(command, self.workspace.root, timeout, environment)

    async def _foreground(self, command: str, cwd: Path, timeout: int) -> str:
        text, code = await self._output(command, cwd, timeout)
        program = outside_program(command)
        if program is None:
            scripts = script_texts(command, self.workspace.resolve, str(cwd))
            program = next(filter(None, map(outside_program, scripts)), None)
        if program is not None:
            text = untrusted(program, text)
        if code is None:
            return (
                f"{text}\n[Atlas: stopped after {timeout} seconds, with every process it started. "
                "For a long command, set background.]"
            )
        return f"{text}\n[exit code {code}]"

    async def _output(
        self, command: str, cwd: Path, timeout: int, extra: dict[str, str] | None = None
    ) -> tuple[str, int | None]:
        process = await self._spawn(command, cwd, subprocess.PIPE, extra)
        output = _Output()
        timed_out = False
        try:
            try:
                await asyncio.wait_for(_drain(process, output), timeout)
            except TimeoutError:
                timed_out = True
                await _stop_tree(process)
                await _drain_rest(process, output)
            code = await process.wait()
        finally:
            if process.returncode is None:  # the turn was stopped while the command ran
                await asyncio.shield(_stop_tree(process))
        return output.text() or "(no output)", None if timed_out else code

    async def _start(
        self, command: str, cwd: Path, *, notify: bool = False, description: str = ""
    ) -> str:
        private_folder(self.folder)
        SpillFolder(self.folder).sweep()
        self._count += 1
        job_id = f"cmd-{self._count}"
        now = utc_now()
        output = self.folder / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{job_id}.txt"
        with output.open("wb") as file:
            process = await self._spawn(command, cwd, file)
        job = _Background(job_id, command, description, process, output, time.monotonic(), now)
        job.notify = notify and self.on_notice is not None
        job.watcher = asyncio.create_task(self._watch(job))
        self._running[job_id] = job
        if self.on_change is not None:
            self.on_change(job.view())
        told = (
            " You will be told when it ends: do other work instead of waiting."
            if job.notify
            else ""
        )
        return (
            f"Started in the background as {job_id} (process {process.pid}). Its output goes to "
            f"{output}; read it with read_file, and its last line will say when it ended. To stop "
            f'it, call run_command with stop: "{job_id}".{told}'
        )

    async def _watch(self, job: _Background) -> None:
        code = await job.process.wait()
        seconds = time.monotonic() - job.started
        how = "it was stopped" if job.stopped else f"exit code {code}"
        with job.output.open("a", encoding="utf-8") as file:
            file.write(f"\n[Atlas: the command ended after {seconds:.1f} seconds, {how}.]\n")
        self._running.pop(job.id, None)
        status = "stopped" if job.stopped else "completed" if code == 0 else "failed"
        ended = replace(job.view(), status=status, exit_code=code, ended_at=utc_now())
        self._ended[job.id] = ended
        for old in list(self._ended)[:-ENDED_KEPT]:
            del self._ended[old]
        if self.on_change is not None:
            self.on_change(ended)
        if job.notify and not self._closing and self.on_notice is not None:
            self.on_notice(_notice(job, code))

    async def _stop(self, job: _Background) -> None:
        job.stopped = True
        await _stop_tree(job.process)
        if job.watcher is not None:
            await job.watcher


def _notice(job: _Background, code: int) -> str:
    """The completion notice: the status, the command, and the end of its output, the whole of it
    in the file."""
    status = "was stopped" if job.stopped else ("completed normally" if code == 0 else "exited")
    output = job.output.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    if len(output) > NOTICE_OUTPUT_CHARS:
        output = (
            f"...(the first {len(output) - NOTICE_OUTPUT_CHARS:,} characters cut; the whole output "
            f"is in {job.output})\n" + output[-NOTICE_OUTPUT_CHARS:]
        )
    return (
        f"[IMPORTANT: Background command {job.id} {status} (exit code {code}).\n"
        f"Command: {job.command}\nOutput:\n{output.strip()}]"
    )


def _tail(path: Path, chars: int) -> str:
    """A file's last `chars` characters, read from its end: a build's output can be large."""
    try:
        with path.open("rb") as file:
            size = file.seek(0, os.SEEK_END)
            file.seek(max(0, size - chars * 4))  # 4 bytes hold any UTF-8 character
            data = file.read()
    except OSError:
        return ""
    return data.decode("utf-8", "replace").replace("\r\n", "\n")[-chars:]


@dataclass
class _Output:
    """A command's output: its start whole, and its end, past what memory should hold."""

    head: bytearray = field(default_factory=bytearray)
    tail: bytearray = field(default_factory=bytearray)
    dropped: int = 0

    def add(self, chunk: bytes) -> None:
        room = HEAD_BYTES - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        self.tail += chunk
        if len(self.tail) > TAIL_BYTES:
            extra = len(self.tail) - TAIL_BYTES
            del self.tail[:extra]
            self.dropped += extra

    def text(self) -> str:
        middle = (
            f"\n[Atlas: {self.dropped:,} bytes of output in the middle were dropped.]\n"
            if self.dropped
            else ""
        )
        whole = bytes(self.head).decode("utf-8", "replace") + middle
        whole += bytes(self.tail).decode("utf-8", "replace")
        # Windows programs end lines in CRLF; one kind of line end reads the same and costs less.
        return whole.replace("\r\n", "\n").rstrip()


async def _drain(process: asyncio.subprocess.Process, output: _Output) -> None:
    stream = process.stdout
    if stream is None:  # pragma: no cover - asked for with PIPE
        return
    while chunk := await stream.read(65_536):
        output.add(chunk)


async def _drain_rest(process: asyncio.subprocess.Process, output: _Output) -> None:
    """What a stopped command still had in its pipe; given up after a few seconds, since a
    grandchild that escaped could hold the pipe open."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(_drain(process, output), GRACE_SECONDS)


async def _stop_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a process and everything it started, children before the parent."""
    if process.returncode is not None:
        return
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await killer.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), GRACE_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), GRACE_SECONDS)


def _creation_flags() -> int:
    """No window on Windows, and a process group of its own."""
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


def _seconds(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_TIMEOUT
    return max(1, min(MAX_TIMEOUT, int(value)))
