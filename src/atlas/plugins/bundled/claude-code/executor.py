"""Claude Code as the SDK's executor: found, started as an instance, its sessions listed (step
16f), by folder too, and read back.

**An instance is one process** (`ClaudeCodeProcess`) in one folder: a prompt is a user message,
and so is a steer, written while the turn runs, which Claude Code takes into that turn. Its own
`auto` mode is switched mid-session with the control request `set_permission_mode`. What it asks
goes to the host as a question or a permission, and the host's answer goes back as Claude Code
reads it; the plugin never knows who answered.

**Its models and efforts** are those its own `/model` lists for the account, which `initialize`
answers (`models`, each with its `supportedEffortLevels`). The model switches mid-session with
`set_model`, as the SDK's `set_model()`; the effort with `apply_flag_settings` and its
`effortLevel`, the request the CLI's own host sends for `/effort` (both proved without a prompt on
2.1.296, 16/10/2026). Before any instance runs, a Claude Code started in the home folder and
closed at once answers the list: no prompt, no model call.

**Its connectors** are what its own `/mcp` lists, which the control request `mcp_status` answers
(the SDK's `mcpServerStatus()`), asked of a Claude Code started the same way. The account's
claude.ai connectors come a moment later than the rest: on 2.1.296 (16/10/2026) the first answer,
at once, had only the plugins' servers, and the claude.ai ones came within a second, some still
`pending` until 2.5 seconds. So it asks again every half second until none is pending and, for a
login on a claude.ai plan, the account's have come, for up to 10 seconds.

**Its slash commands** are what its own `/` menu offers in the folder, which `initialize`
answers described (`commands`: its own, marked `builtin`, the user's and the project's skills and
commands, its plugins'), and `commands_changed` says again whole when they change: both are
`CommandsChanged`. Before a session runs in a folder, a Claude Code started there and closed at
once answers them, with no prompt. A command it runs itself (`/context`, `/usage`) answers with
words of its own, no model call: a synthetic message, or `local_command_output`.

**Its plan's limits** are what its own `/usage` shows, which the control request `get_usage`
answers (with `skip_behaviors`, which leaves out its scan of the week's transcripts), asked of a
Claude Code started the same way: no prompt, no model call, and Claude Code reads its plan's usage
with its own login, which Atlas never touches. Its SDK calls the request
`usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET`, so a Claude Code that refuses it or
answers another shape says why, and the `rate_limit_event` it writes after each request of a turn
still says the windows (`UsageChanged`): the plan's usage is read here two ways, the second the
stream's own.

**The version it needs**: 2.1.140, the one step 13 was proved on (12/10/2026), which already has
the `auto` mode and `set_permission_mode`. The `claude` WinGet installs never updates itself, so
detection says which version there is, and how to update it.

**Where it is found**, as the Agent SDK finds it (`_find_cli`, 0.2.165): the `claude` on the PATH,
on Windows only a native `claude.exe`, then where Claude Code's own installer puts it, since the
PATH Atlas started with can be older than the install (the app was open, or opened from a Mac's
Dock). npm's `claude.cmd` on Windows, which `CreateProcess` cannot run, is never run, as the SDK
refuses it. Atlas's own additions to the SDK's places: WinGet's (its `Links` folder and its package
folder, which the user's Claude Code comes from), Homebrew's on a Mac, and on Windows the native
`claude.exe` npm's package carries (its `bin/claude.exe`, which the `claude.cmd` only starts). The
SDK refuses an npm install whole, and a tester's Claude Code, installed through npm, was no longer
found once Atlas followed it (19/10/2026). An npm install without that program is refused with how
to install the native one.

**Its other accounts** (step 16h, decided with the user on 19/10/2026) are Claude Code's own way
to stay logged in to several at once (code.claude.com/docs, Authentication, "Log in with multiple
accounts"): a folder each, `~/.claude-<nickname>` beside the user's own, handed to every process
of that account as `CLAUDE_CONFIG_DIR`, so each has its own login, settings, plugins and sessions.
Claude Code logs it in itself (`claude auth login`, on the user's browser). A session goes on from
one account to another in a copy: `--resume` takes the path of the other account's transcript, and
`--fork-session` writes the copy in this account's folder, the original left as it was.

**Detection asks two things at once**, `claude --version` and `claude auth status --json`, so a
slow first start (an antivirus scanning a new `claude.exe`) costs one wait, not two. The status is
read whatever the exit code: nobody logged in exits 1, saying so (2.1.296, 18/10/2026).

**Its browser**, Claude in Chrome, is turned on only when the process starts (`--chrome`): its
`/chrome`, which turns it on in a terminal, is a screen of its own that a session over stdio does
not have. The plugin's `chrome` setting passes the flag to each session, on every account; Claude
Code then connects to the extension in the user's own Chrome, its server `claude-in-chrome`
(proved on 2.1.302, 31/10/2026). A Claude Code started only to say what it has never gets it.

**Its newest version** (SDK version 27) is where its install updates from: Claude Code's own
releases for the native program and for npm's (`claude update` updates both), the channel the
user chose (`autoUpdatesChannel`, `latest` unless `stable`); WinGet's catalog for a WinGet install,
which lags behind them and updates through `winget upgrade`; the stable channel for Homebrew's
cask. A program installed some other way (a system package) is left to its own updater.
"""

import asyncio
import contextlib
import json
import os
import shutil
import sys
from collections.abc import Callable, Sequence
from logging import Logger
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import (
    Account,
    Capabilities,
    CommandsChanged,
    Connector,
    Detection,
    ExecutorError,
    ExecutorFile,
    ExecutorHost,
    ExecutorSpec,
    Finished,
    ModelChoice,
    PlanUsage,
    SessionFolder,
    SessionInfo,
    SlashCommand,
    Started,
    Transcript,
    UpdateSource,
)

from .live import open_elsewhere
from .process import CLOSE_SECONDS, STATUS_SECONDS, ClaudeCodeProcess, account, log_in, run_cli
from .protocol import (
    AUTO_MODE,
    DEFAULT_MODE,
    IMAGE_TYPES,
    TITLE,
    Attached,
    answered,
    asked_of,
    at_least,
    commands_of,
    connectors_of,
    effort_problem,
    events_of,
    hosts_connectors,
    judged,
    models_of,
    slash_commands_of,
    usage_of,
    version_of,
)
from .sessions import list_folders, list_sessions
from .transcript import claude_folder, delete_session_files, read_transcript, session_file

#: The oldest Claude Code this plugin drives.
MINIMUM_VERSION = "2.1.140"
#: The Claude Code processes Atlas runs now: their sessions are open here, not elsewhere.
_OWN: set[int] = set()
#: How long a Claude Code started to list its connectors waits for them to settle, and how often
#: it asks again meanwhile.
CONNECTORS_SECONDS = 10.0
_CONNECTORS_EVERY = 0.5


#: How Claude Code's own documentation installs the native program, per system.
INSTALL_WINDOWS = "irm https://claude.ai/install.ps1 | iex"
INSTALL_ELSEWHERE = "curl -fsSL https://claude.ai/install.sh | bash"


def installed_cli() -> list[str] | None:
    """The `claude` this computer has, as its own program, or `None` when Atlas can run none."""
    found = claude_program()
    return None if found is None else [str(found)]


def claude_program() -> Path | None:
    """The `claude` on the PATH, or where Claude Code's installers put it."""
    if sys.platform == "win32":
        # A `.cmd` early on the PATH would shadow a native `claude.exe` later on it (the SDK's).
        hits = (shutil.which("claude"), shutil.which("claude.exe"))
        found = next((hit for hit in hits if hit is not None and _native(hit)), None)
        if found is None and hits[0] is not None:
            carried = _npm_program(Path(hits[0]).parent)
            found = str(carried) if carried.is_file() else None
    else:
        found = shutil.which("claude")
    if found is not None:
        return Path(found)
    return next((place for place in known_places() if place.is_file()), None)


def known_places() -> list[Path]:
    """Where Claude Code's installers put it: the Agent SDK's list, and Atlas's own additions."""
    home = Path.home()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        winget = local / "Microsoft" / "WinGet"
        return [
            home / ".local" / "bin" / "claude.exe",
            winget / "Links" / "claude.exe",
            *sorted(winget.glob("Packages/Anthropic.ClaudeCode_*/claude.exe")),
            # npm's own global folder on Windows.
            _npm_program(roaming / "npm"),
        ]
    places = [
        home / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        home / ".local" / "bin" / "claude",
        home / "node_modules" / ".bin" / "claude",
        home / ".yarn" / "bin" / "claude",
        home / ".claude" / "local" / "claude",
    ]
    return [*places, Path("/opt/homebrew/bin/claude")] if sys.platform == "darwin" else places


def _npm_program(prefix: Path) -> Path:
    """The native program npm's package carries in a global folder, which its `claude.cmd`
    starts."""
    return prefix / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"


def not_found() -> str:
    """Why there is no `claude` Atlas can run, and how to have one."""
    if sys.platform == "win32":
        shim = shutil.which("claude")
        if shim is not None and not _native(shim):
            # `which` names it with PATHEXT's case (`claude.CMD`); Windows' names have none.
            named = Path(shim).name.lower()
            return (
                f"Claude Code is installed through npm ({named}) without the native program "
                f"Atlas runs on Windows, as Anthropic's Agent SDK does. Install the native "
                f"one, in PowerShell: {INSTALL_WINDOWS}"
            )
        return f"Claude Code is not installed. Install it, in PowerShell: {INSTALL_WINDOWS}"
    return f"Claude Code is not installed. Install it, in a terminal: {INSTALL_ELSEWHERE}"


#: Where Claude Code publishes its newest version, a channel each: the version alone, as text.
RELEASES = "https://downloads.claude.ai/claude-code-releases/{channel}"
WINGET_ID = "Anthropic.ClaudeCode"


def update_source(program: str, folder: Path) -> UpdateSource | None:
    """Where the newest version of the Claude Code at `program` is published, and how it
    updates, by where it was installed."""
    path = str(Path(program).resolve()).replace("\\", "/").lower()
    if "/winget/" in path:
        quiet = ("--silent", "--accept-source-agreements", "--accept-package-agreements")
        upgrade = ("winget", "upgrade", "--id", WINGET_ID, "--exact", *quiet)
        return UpdateSource(upgrade, winget=WINGET_ID)
    if "/caskroom/" in path or "/homebrew/" in path:
        stable = RELEASES.format(channel="stable")
        return UpdateSource(("brew", "upgrade", "--cask", "claude-code"), url=stable)
    if path.startswith(("/usr/bin/", "/bin/")):
        return None
    return UpdateSource((program, "update"), url=RELEASES.format(channel=_channel(folder)))


def _channel(folder: Path) -> str:
    """The release channel the user chose in Claude Code's settings: `latest` unless `stable`."""
    try:
        settings: object = json.loads((folder / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "latest"
    stable = (
        isinstance(settings, dict)
        and cast(dict[str, object], settings).get("autoUpdatesChannel") == "stable"
    )
    return "stable" if stable else "latest"


def too_old(version: str, program: str) -> str:
    """Why this version is not driven, and how its install updates."""
    how = "winget upgrade Anthropic.ClaudeCode" if "winget" in program.lower() else "claude update"
    return f"Claude Code {version} is older than {MINIMUM_VERSION}. Update it: {how}"


def _native(path: str) -> bool:
    """A program `CreateProcess` runs as it is, `.exe` or `.com` (the SDK's
    `_is_windows_native_exe`): not npm's `.cmd`, nor a script."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rstrip(". ").lower().endswith((".exe", ".com"))


class ClaudeCode:
    """The executor. `cli` is the program and the arguments before Claude Code's own; `None`
    when there is none on this computer. With `account_folder`, on the account Claude Code keeps
    there (step 16h); else on the user's own folder (`sessions`, a test's). With `chrome`, each
    session starts with Claude in Chrome."""

    title = TITLE

    def __init__(
        self,
        cli: Sequence[str] | None,
        *,
        logger: Logger,
        sessions: Path | None = None,
        account_folder: Path | None = None,
        chrome: bool = False,
        found: bool = False,
    ) -> None:
        self._cli = None if cli is None else list(cli)
        #: Found where its installers put it, not named by the `program` setting: only then
        #: Atlas knows how it updates.
        self._found = found
        self._logger = logger
        self._chrome = chrome
        self._sessions = sessions
        self._account_folder = account_folder
        #: The models the last Claude Code started answered; `None` until one did.
        self._models: tuple[ModelChoice, ...] | None = None

    async def detect(self) -> Detection:
        cli = self._cli
        if cli is None:
            return Detection(False, minimum=MINIMUM_VERSION, problem=not_found())
        (version, problem), logged = await asyncio.gather(
            _version(cli), account(cli, self._account_folder)
        )
        if not version:
            return Detection(False, minimum=MINIMUM_VERSION, problem=problem)
        supported = at_least(version, MINIMUM_VERSION)
        return Detection(
            True,
            version,
            MINIMUM_VERSION,
            supported,
            logged,
            "" if supported else too_old(version, cli[0]),
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            questions=True,
            permissions=True,
            steer=True,
            auto=True,
            accounts=True,
            sessions=True,
            fork=True,
            models=True,
            connectors=True,
            folders=True,
            usage=True,
            commands=True,
            account_folders=True,
            delete_sessions=True,
            files=True,
            updates=True,
        )

    def update_source(self) -> UpdateSource | None:
        if self._cli is None or not self._found:
            return None
        return update_source(self._cli[0], self.account_folder())

    def account_folder(self) -> Path:
        return self._account_folder or self._sessions or claude_folder()

    def new_account_folder(self, nickname: str) -> Path:
        return Path(os.path.normpath(Path.home() / f".claude-{nickname}"))

    def for_account(self, folder: Path) -> "ClaudeCode":
        return ClaudeCode(
            self._cli, logger=self._logger, account_folder=folder, chrome=self._chrome
        )

    async def delete_session(self, session: str, folder: Path) -> None:
        root = self.account_folder()
        found = await asyncio.to_thread(session_file, root, session, folder)
        if found is None:
            raise ExecutorError("Claude Code no longer keeps this session.")
        if session in await asyncio.to_thread(open_elsewhere, root, frozenset(_OWN)):
            raise ExecutorError(
                "This session is open in another program. Close it there, then delete it."
            )
        try:
            await asyncio.to_thread(delete_session_files, root, found)
        except OSError as error:
            raise ExecutorError(f"The session could not be deleted: {error}") from error

    async def start(self, spec: ExecutorSpec, host: ExecutorHost) -> "ClaudeCodeInstance":
        if self._cli is None:
            raise ExecutorError("Claude Code is not installed on this computer.")
        problem = effort_problem(self._models or (), spec.model, spec.effort)
        if problem is not None:
            raise ExecutorError(problem)
        resume = spec.resume
        if spec.resume_from is not None and resume is not None:
            # Another account's session: Claude Code reads its file there, and its copy is kept
            # here (`--fork-session`), the original left as it was.
            found = await asyncio.to_thread(session_file, spec.resume_from, resume, spec.folder)
            if found is None:
                raise ExecutorError("The other account no longer keeps this session.")
            resume = str(found)
        instance = ClaudeCodeInstance(
            self._cli,
            spec,
            host,
            self._logger,
            self._known,
            resume,
            self._account_folder,
            chrome=self._chrome,
        )
        await instance.open()
        return instance

    async def models(self) -> list[ModelChoice]:
        if self._models is None:
            probe = self._probe()
            try:
                await probe.start()
                self._known(models_of(probe.initialized.get("models")))
            finally:
                await probe.close()
        return list(self._models or ())

    async def connectors(self) -> list[Connector]:
        probe = self._probe()
        try:
            await probe.start()
            hosted = hosts_connectors(probe.initialized.get("account"))
            clock = asyncio.get_running_loop()
            deadline = clock.time() + CONNECTORS_SECONDS
            while True:
                answer = await probe.control({"subtype": "mcp_status"})
                found = connectors_of(answer.get("mcpServers"))
                settled = all(c.status != "pending" for c in found) and (
                    not hosted or any(c.origin == "account" for c in found)
                )
                # The last answer before the deadline is what there is, a pending one too.
                if settled or clock.time() + _CONNECTORS_EVERY > deadline:
                    return list(found)
                await asyncio.sleep(_CONNECTORS_EVERY)
        finally:
            await probe.close()

    async def commands(self, *, folder: Path) -> list[SlashCommand]:
        probe = self._probe(folder)
        try:
            await probe.start()
            return list(slash_commands_of(probe.initialized.get("commands")))
        finally:
            await probe.close()

    async def usage(self) -> PlanUsage:
        probe = self._probe()
        try:
            await probe.start()
            try:
                answer = await probe.control({"subtype": "get_usage", "skip_behaviors": True})
            except ExecutorError as error:
                raise ExecutorError(
                    f"Claude Code did not say its plan's usage ({error}). It shows once a "
                    "session's turn runs."
                ) from error
        finally:
            await probe.close()
        return usage_of(answer)

    def _probe(self, folder: Path | None = None) -> ClaudeCodeProcess:
        """A Claude Code that runs no prompt, only asked what it has: in the home folder, or in
        `folder` for what a project adds."""
        if self._cli is None:
            raise ExecutorError("Claude Code is not installed on this computer.")
        return ClaudeCodeProcess(
            self._cli,
            folder or Path.home(),
            answer=_nothing_to_answer,
            listen=lambda _message: None,
            logger=self._logger,
            account_folder=self._account_folder,
        )

    def _known(self, models: tuple[ModelChoice, ...]) -> None:
        """What a Claude Code just started answered: the list, until another account logs in."""
        self._models = models

    async def transcript(self, session: str, folder: Path) -> Transcript:
        root = self.account_folder()
        return await asyncio.to_thread(read_transcript, root, session, folder)

    async def sessions(self, *, folder: Path | None = None) -> list[SessionInfo]:
        root = self.account_folder()
        return await asyncio.to_thread(list_sessions, root, frozenset(_OWN), folder)

    async def folders(self) -> list[SessionFolder]:
        return await asyncio.to_thread(list_folders, self.account_folder())

    async def log_in(self, login: str) -> Account:
        if self._cli is None:
            raise ExecutorError("Claude Code is not installed on this computer.")
        # Another account may be offered other models.
        self._models = None
        return await log_in(self._cli, login, self._account_folder)


async def _version(cli: Sequence[str]) -> tuple[str, str]:
    """`claude --version`'s version, or none and why."""
    try:
        output = await run_cli(cli, ["--version"], STATUS_SECONDS)
    except ExecutorError as error:
        return "", str(error)
    version = version_of(output)
    if not version:
        return "", f"claude --version said no version Atlas can read: {output.strip()[:200]!r}"
    return version, ""


async def _nothing_to_answer(request: dict[str, Any]) -> dict[str, Any]:
    """A Claude Code started only to say what it has runs no prompt, so asks nothing."""
    return {"behavior": "deny", "message": "Atlas only asked what Claude Code has."}


class ClaudeCodeInstance:
    """One Claude Code session, in its folder."""

    def __init__(
        self,
        cli: Sequence[str],
        spec: ExecutorSpec,
        host: ExecutorHost,
        logger: Logger,
        known: Callable[[tuple[ModelChoice, ...]], None] = lambda _models: None,
        resume: str | None = None,
        account_folder: Path | None = None,
        *,
        chrome: bool = False,
    ) -> None:
        self._host = host
        self._known = known
        # A copy's id is its own, which its `init` says.
        self._resume = None if spec.fork or spec.resume_from else spec.resume
        self._turn: asyncio.Future[None] | None = None
        self._stopping = False
        self._watch: asyncio.Task[None] | None = None
        self.process = ClaudeCodeProcess(
            cli,
            spec.folder,
            answer=self._answer,
            listen=self._heard,
            logger=logger,
            # Another account's session is named by its file's path (`ClaudeCode.start`).
            resume=resume if resume is not None else spec.resume,
            auto=spec.auto,
            fork=spec.fork or spec.resume_from is not None,
            model=spec.model,
            effort=spec.effort,
            account_folder=account_folder,
            directories=spec.directories,
            chrome=chrome,
        )

    async def open(self) -> None:
        await self.process.start()
        if self.process.pid is not None:
            _OWN.add(self.process.pid)
        self._watch = asyncio.create_task(self._watch_end())
        models = models_of(self.process.initialized.get("models"))
        if models:
            self._known(models)
        # Its slash commands are known from its `initialize`, before its first turn says `init`.
        listed = self.process.initialized.get("commands")
        commands = commands_of(listed)
        if commands:
            self._host.event(Started(self._resume or "", commands=commands))
            self._host.event(CommandsChanged(slash_commands_of(listed)))

    async def prompt(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        if self._turn is not None and not self._turn.done():
            raise ExecutorError("Claude Code is in a turn already; steer it instead.")
        attached = await asyncio.to_thread(_attached, files)
        turn = self._turn = asyncio.get_running_loop().create_future()
        await self.process.send(text, attached)
        ended = asyncio.create_task(self.process.ended.wait())
        try:
            await asyncio.wait({turn, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ended.cancel()
        if not turn.done():
            raise ExecutorError(f"It ended without a result. {self.process.errors}".strip())

    async def steer(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        await self.process.send(text, await asyncio.to_thread(_attached, files))

    async def set_auto(self, on: bool) -> None:
        mode = AUTO_MODE if on else DEFAULT_MODE
        await self.process.control({"subtype": "set_permission_mode", "mode": mode})

    async def set_model(self, model: str | None, effort: str | None) -> None:
        problem = effort_problem(models_of(self.process.initialized.get("models")), model, effort)
        if problem is not None:
            raise ExecutorError(problem)
        await self.process.control({"subtype": "set_model", "model": model})
        # `None` hands the effort back to the user's settings and the model's own default.
        settings = {"effortLevel": effort}
        await self.process.control({"subtype": "apply_flag_settings", "settings": settings})

    async def stop(self) -> None:
        self._stopping = True
        # A Claude Code that hangs does not hold the stop: it is killed after a moment.
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.process.interrupt(), CLOSE_SECONDS)
        await self.process.close()
        _OWN.discard(self.process.pid or 0)
        if self._watch is not None:
            self._watch.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watch

    def _heard(self, message: dict[str, Any]) -> None:
        for event in events_of(message):
            self._host.event(event)
            if isinstance(event, Finished) and self._turn is not None and not self._turn.done():
                self._turn.set_result(None)

    async def _answer(self, request: dict[str, Any]) -> dict[str, Any]:
        asked = asked_of(request)
        if asked.is_question:
            return answered(asked, await self._host.ask(asked.questions))
        return judged(asked, await self._host.approve(asked.permission))

    async def _watch_end(self) -> None:
        """Between turns, a process that ends by itself is told: nobody waits on a prompt."""
        await self.process.ended.wait()
        if not self._stopping and (self._turn is None or self._turn.done()):
            self._host.failed(f"Claude Code ended. {self.process.errors}".strip())


def _attached(files: Sequence[ExecutorFile]) -> list[Attached]:
    """The files as Claude Code gets them: an image it reads as itself with its bytes, any other
    by its path alone. Raises `ExecutorError` for an image that is gone."""
    found: list[Attached] = []
    for item in files:
        image = None
        if item.media_type in IMAGE_TYPES:
            try:
                image = item.path.read_bytes()
            except OSError as error:
                raise ExecutorError(f"{item.name} could not be read: {error}") from error
        found.append(Attached(str(item.path), item.media_type, image))
    return found
