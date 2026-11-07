"""Codex as the SDK's executor: found, started as an instance, its sessions listed and read back
(step 16o, ROADMAP: Codex, Gemini CLI e DeepSeek).

**An instance is one `codex app-server`** (`CodexServer`) in one folder, holding one thread: a
prompt is a `turn/start`, a steer a `turn/steer` into the turn that runs. What it asks goes to the
host as a permission (or, where Codex asks one, a question), and the host's answer goes back as
Codex reads it; the plugin never knows who answered. Codex's `decline` carries no reason, so a
refusal that says why is followed by a steer with the reason, for Codex to read (Atlas's own
choice, marked in the roadmap).

**Its automatic mode, its model and its effort** are what each `turn/start` carries: Codex changes
them only there, so a switch while a turn runs counts from the next one.

**Everything else is read from a Codex started in the home folder and closed at once**, with no
prompt and no model call: its account (`account/read`), models (`model/list`), connectors
(`mcpServerStatus/list`), plan limits (`account/rateLimits/read`), sessions (`thread/list`, always
`useStateDbOnly`, which leaves its database as it is) and their turns (`thread/turns/list`).

**Where it is found**: the `codex` on the PATH (on Windows only a native `codex.exe`: npm's
`codex.cmd` starts a Node script, which `CreateProcess` cannot run, so the native program npm's
package carries is run instead), the Codex app of the Microsoft Store, found through the
registry since its folder changes with each version (its `codex.exe` runs from outside the app;
proved on 20/10/2026), and Homebrew's on a Mac.

**Its other accounts** are Codex's own folders, one each (`CODEX_HOME`): `~/.codex-<nickname>`
beside the user's own, logged in by `codex login` on the user's browser. A session goes on from
one account to another in a copy: `thread/fork` takes the path of the other account's session
file, which that account's Codex names.

**Its newest version** (SDK version 27) is npm's, for an install npm keeps in a folder the user may
write to: the user's npm updates it when they ask. Any other install is left to its own updater.
"""

import asyncio
import contextlib
import os
import shutil
import sys
from collections.abc import AsyncGenerator, Sequence
from logging import Logger
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    import winreg

from atlas.plugins.sdk import (
    Account,
    Capabilities,
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
    Started,
    Transcript,
    UpdateSource,
)

from .protocol import (
    PERMISSION_METHODS,
    QUESTION_METHOD,
    TITLE,
    TurnState,
    account_of,
    answers_result,
    at_least,
    connectors_of,
    effort_problem,
    entries_of,
    events_of,
    models_of,
    permission_of,
    questions_of,
    records,
    refused_note,
    session_of,
    thread_params,
    turn_params,
    usage_of,
    user_input,
    verdict_result,
    version_of,
)
from .server import CLOSE_SECONDS, LOGIN_SECONDS, STATUS_SECONDS, CodexServer, named, run_cli

#: The oldest Codex this plugin drives: the one it was proved on (20/10/2026).
MINIMUM_VERSION = "0.163.0"
#: The threads Atlas runs now: never deleted under it.
_OWN: set[str] = set()
#: How many sessions one `thread/list` page holds, and the most pages read.
PAGE = 100
PAGES = 50
#: Where the sessions listed began: its terminal, an editor or its app, a script (`codex exec`),
#: and a client of its `app-server` (Atlas's own); its subagents' threads are left out.
SOURCES = ("cli", "vscode", "exec", "appServer")
#: The Microsoft Store's package of the Codex app, and where its program sits inside.
STORE_PACKAGE = "OpenAI.Codex_"
STORE_PROGRAM = Path("app") / "resources" / "codex.exe"
_STORE_KEY = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppModel"
    r"\Repository\Packages"
)
#: How Codex's own documentation installs it.
INSTALL = "npm install -g @openai/codex"


def installed_cli() -> list[str] | None:
    """The `codex` this computer has, as its own program, or `None` when Atlas can run none."""
    found = codex_program()
    return None if found is None else [str(found)]


def codex_program() -> Path | None:
    """The `codex` on the PATH, or where Codex's installers put it."""
    if sys.platform == "win32":
        hits = (shutil.which("codex"), shutil.which("codex.exe"))
        found = next((hit for hit in hits if hit is not None and _native(hit)), None)
        if found is None and hits[0] is not None:
            carried = _npm_program(Path(hits[0]).parent)
            found = None if carried is None else str(carried)
    else:
        found = shutil.which("codex")
    if found is not None:
        return Path(found)
    return next((place for place in known_places() if place.is_file()), None)


def known_places() -> list[Path]:
    """Where Codex's installers put it, besides the PATH."""
    home = Path.home()
    if sys.platform == "win32":
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        npm = _npm_program(roaming / "npm")
        return [*store_programs(), *([npm] if npm is not None else [])]
    places = [home / ".npm-global" / "bin" / "codex", Path("/usr/local/bin/codex")]
    return [*places, Path("/opt/homebrew/bin/codex")] if sys.platform == "darwin" else places


def store_programs() -> list[Path]:
    """The `codex.exe` of the Codex app the Microsoft Store installed, the newest first, found in
    the registry: its folder under `WindowsApps` cannot be listed, and changes with each version."""
    if sys.platform != "win32":
        return []
    found: list[Path] = []
    with (
        contextlib.suppress(OSError),
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, _STORE_KEY) as packages,
    ):
        index = 0
        while True:
            try:
                name = winreg.EnumKey(packages, index)
            except OSError:
                break
            index += 1
            if not name.startswith(STORE_PACKAGE):
                continue
            with contextlib.suppress(OSError), winreg.OpenKey(packages, name) as package:
                root, _kind = winreg.QueryValueEx(package, "PackageRootFolder")
                found.append(Path(str(root)) / STORE_PROGRAM)
    return sorted(found, reverse=True)


def _npm_program(prefix: Path) -> Path | None:
    """The native program npm's package carries in a global folder, which its `codex.cmd`
    starts through Node."""
    package = prefix / "node_modules" / "@openai" / "codex"
    places = [package / "node_modules" / "@openai", prefix / "node_modules" / "@openai"]
    for place in places:
        for program in sorted(place.glob("codex-win32-*/vendor/*/bin/codex.exe")):
            return program
    return None


def _native(path: str) -> bool:
    """A program `CreateProcess` runs as it is, `.exe` or `.com`: not npm's `.cmd`."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return name.rstrip(". ").lower().endswith((".exe", ".com"))


def not_found() -> str:
    """Why there is no `codex` Atlas can run, and how to have one."""
    return (
        f"Codex is not installed. Install the Codex app, or in a terminal: {INSTALL}"
        if sys.platform == "win32"
        else f"Codex is not installed. Install it, in a terminal: {INSTALL}"
    )


#: The npm package it comes in.
PACKAGE = "@openai/codex"


def update_source(program: str) -> UpdateSource | None:
    """npm's newest version, when the program found is in npm's package, in a folder the user may
    write to; else `None`: another install updates some other way."""
    path = str(Path(program).resolve()).replace("\\", "/")
    at = path.find(f"/node_modules/{PACKAGE}/")
    if at < 0:
        return None
    package = Path(path[: at + len(f"/node_modules/{PACKAGE}")])
    return UpdateSource(npm=PACKAGE) if os.access(package, os.W_OK) else None


def too_old(version: str, program: str) -> str:
    """Why this version is not driven, and how its install updates."""
    how = "update the Codex app in the Microsoft Store" if "WindowsApps" in program else INSTALL
    return f"Codex {version} is older than {MINIMUM_VERSION}. Update it: {how}"


def codex_home() -> Path:
    """The user's own Codex folder: `CODEX_HOME`, or `~/.codex`."""
    named_home = os.environ.get("CODEX_HOME")
    return Path(named_home) if named_home else Path.home() / ".codex"


class Codex:
    """The executor. `cli` is the program and the arguments before Codex's own; `None` when there
    is none on this computer. With `home`, on the account Codex keeps there; else on the user's
    own folder."""

    title = TITLE

    def __init__(
        self,
        cli: Sequence[str] | None,
        *,
        logger: Logger,
        home: Path | None = None,
        found: bool = False,
    ) -> None:
        self._cli = None if cli is None else list(cli)
        #: Found where its installers put it, not named by the `program` setting: only then
        #: Atlas knows how it updates.
        self._found = found
        self._logger = logger
        self._home = home
        #: The models the last Codex started answered; `None` until one did.
        self._models: tuple[ModelChoice, ...] | None = None

    async def detect(self) -> Detection:
        cli = self._cli
        if cli is None:
            return Detection(False, minimum=MINIMUM_VERSION, problem=not_found())
        (version, problem), logged = await asyncio.gather(_version(cli), self._account())
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
            questions=False,
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
            commands=False,
            account_folders=True,
            delete_sessions=True,
            files=True,
            updates=True,
        )

    def update_source(self) -> UpdateSource | None:
        return None if self._cli is None or not self._found else update_source(self._cli[-1])

    def account_folder(self) -> Path:
        return self._home or codex_home()

    def new_account_folder(self, nickname: str) -> Path:
        return Path(os.path.normpath(Path.home() / f".codex-{nickname}"))

    def for_account(self, folder: Path) -> "Codex":
        return Codex(self._cli, logger=self._logger, home=folder)

    async def start(self, spec: ExecutorSpec, host: ExecutorHost) -> "CodexInstance":
        cli = self._need()
        problem = effort_problem(self._models or (), spec.model, spec.effort)
        if problem is not None:
            raise ExecutorError(problem)
        path = None
        if spec.resume_from is not None and spec.resume is not None:
            # Another account's session: its Codex names the file, this one forks it here.
            other = Codex(cli, logger=self._logger, home=spec.resume_from)
            path = await other.session_path(spec.resume)
        instance = CodexInstance(cli, spec, host, self._logger, self._home, path)
        await instance.open()
        return instance

    async def session_path(self, session: str) -> str:
        """Where this account's Codex keeps a session's file. Raises `ExecutorError`."""
        answer = await self._ask("thread/read", {"threadId": session, "includeTurns": False})
        path = str(dict(answer.get("thread") or {}).get("path") or "")
        if not path:
            raise ExecutorError("The other account no longer keeps this session.")
        return path

    async def transcript(self, session: str, folder: Path) -> Transcript:
        turns: list[dict[str, Any]] = []
        async with self._probe() as server:
            cursor: str | None = None
            for _ in range(PAGES):
                params: dict[str, Any] = {
                    "threadId": session,
                    "sortDirection": "asc",
                    "itemsView": "full",
                    "limit": PAGE,
                }
                if cursor is not None:
                    params["cursor"] = cursor
                try:
                    page = await server.request("thread/turns/list", params)
                except ExecutorError:
                    return Transcript(expired=not turns)
                turns.extend(records(page))
                cursor = page.get("nextCursor")
                if not cursor:
                    break
        return Transcript(entries_of(turns))

    async def sessions(self, *, folder: Path | None = None) -> list[SessionInfo]:
        threads = await self._threads(folder)
        return [session_of(thread) for thread in threads]

    async def folders(self) -> list[SessionFolder]:
        newest: dict[str, SessionFolder] = {}
        for info in await self.sessions():
            key = _same(info.folder)
            if key not in newest:
                newest[key] = SessionFolder(info.folder, info.updated_at)
        return list(newest.values())

    async def models(self) -> list[ModelChoice]:
        if self._models is None:
            self._models = models_of(await self._ask("model/list", {}))
        return list(self._models)

    async def connectors(self) -> list[Connector]:
        return list(connectors_of(await self._ask("mcpServerStatus/list", {})))

    async def commands(self, *, folder: Path) -> list[Any]:
        return []

    async def usage(self) -> PlanUsage:
        return usage_of(await self._ask("account/rateLimits/read", {}))

    async def log_in(self, login: str) -> Account:
        cli = self._need()
        # Another account may be offered other models.
        self._models = None
        _make(self._home)
        await run_cli(cli, ["login"], LOGIN_SECONDS, home=self._home)
        return await self._account()

    async def delete_session(self, session: str, folder: Path) -> None:
        if session in _OWN:
            raise ExecutorError("This session runs in Atlas now. Stop it, then delete it.")
        await self._ask("thread/delete", {"threadId": session})

    # -- reading ----------------------------------------------------------------------------------

    async def _threads(self, folder: Path | None) -> list[dict[str, Any]]:
        """Its sessions, the most recent first, read from its database as it is."""
        found: list[dict[str, Any]] = []
        async with self._probe() as server:
            cursor: str | None = None
            for _ in range(PAGES):
                params: dict[str, Any] = {
                    "limit": PAGE,
                    "sortKey": "updated_at",
                    "useStateDbOnly": True,
                    "sourceKinds": list(SOURCES),
                }
                if cursor is not None:
                    params["cursor"] = cursor
                page = await server.request("thread/list", params)
                found.extend(records(page))
                cursor = page.get("nextCursor")
                if not cursor:
                    break
        if folder is None:
            return found
        # Its `cwd` filter finds nothing in the database alone (0.163.0, 20/10/2026): a folder's
        # sessions are picked here.
        wanted = _same(folder)
        return [thread for thread in found if _same(Path(str(thread.get("cwd") or ""))) == wanted]

    async def _account(self) -> Account:
        """Who Codex is logged in as now; not logged in when it cannot say."""
        try:
            return account_of(await self._ask("account/read", {"refreshToken": False}))
        except ExecutorError:
            return Account(logged_in=False)

    async def _ask(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        async with self._probe() as server:
            return await server.request(method, params)

    @contextlib.asynccontextmanager
    async def _probe(self) -> AsyncGenerator[CodexServer]:
        """A Codex that runs no prompt, only asked what it has, in the home folder."""
        server = CodexServer(self._need(), Path.home(), logger=self._logger, home=self._home)
        _make(self._home)
        try:
            await server.start()
            yield server
        finally:
            await server.close()

    def _need(self) -> list[str]:
        if self._cli is None:
            raise ExecutorError("Codex is not installed on this computer.")
        return self._cli


def _same(folder: Path) -> str:
    """A folder written one way, to compare two of them."""
    return os.path.normcase(os.path.normpath(str(folder)))


def _make(home: Path | None) -> None:
    """An account's folder, which Codex refuses to run on when it does not exist."""
    if home is not None:
        Path(named(home) or home).mkdir(parents=True, exist_ok=True)


async def _version(cli: Sequence[str]) -> tuple[str, str]:
    """`codex --version`'s version, or none and why."""
    try:
        output = await run_cli(cli, ["--version"], STATUS_SECONDS)
    except ExecutorError as error:
        return "", str(error)
    version = version_of(output)
    if not version:
        return "", f"codex --version said no version Atlas can read: {output.strip()[:200]!r}"
    return version, ""


class CodexInstance:
    """One Codex thread, in its folder."""

    def __init__(
        self,
        cli: Sequence[str],
        spec: ExecutorSpec,
        host: ExecutorHost,
        logger: Logger,
        home: Path | None = None,
        resume_path: str | None = None,
    ) -> None:
        self._spec = spec
        self._host = host
        self._resume_path = resume_path
        self._auto = spec.auto
        self._model = spec.model
        self._effort = spec.effort
        self._thread = ""
        self._turn_id: str | None = None
        self._turn: asyncio.Future[None] | None = None
        self._state = TurnState()
        self._stopping = False
        self._watch: asyncio.Task[None] | None = None
        self._notes: set[asyncio.Task[None]] = set()
        #: What a refusal said, steered into the turn once the refusal is written.
        self._unsaid: list[str] = []
        _make(home)
        self.server = CodexServer(
            cli,
            spec.folder,
            logger=logger,
            answer=self._answer,
            listen=self._heard,
            answered=self._said_why,
            home=home,
        )

    async def open(self) -> None:
        """Start Codex and its thread; a Codex that cannot open the session is stopped."""
        try:
            await self.server.start()
            answer = await self._open_thread()
        except BaseException:
            await self.server.close()
            raise
        thread = dict(answer.get("thread") or {})
        self._thread = str(thread.get("id") or "")
        if not self._thread:
            await self.server.close()
            raise ExecutorError("Codex started no session.")
        _OWN.add(self._thread)
        model = str(answer.get("model") or thread.get("model") or "")
        self._host.event(Started(self._thread, model=model))
        self._watch = asyncio.create_task(self._watch_end())

    async def _open_thread(self) -> dict[str, Any]:
        """A new thread, or the one to go on with: itself, or a copy of it (`fork`, or another
        account's, by its file)."""
        spec = self._spec
        params = thread_params(str(spec.folder), auto=self._auto, model=self._model)
        if spec.resume is None:
            return await self.server.request("thread/start", params)
        if self._resume_path is not None:
            copy = {"threadId": spec.resume, "path": self._resume_path, **params}
            return await self.server.request("thread/fork", copy)
        if spec.fork:
            return await self.server.request("thread/fork", {"threadId": spec.resume, **params})
        resumed = {"threadId": spec.resume, "excludeTurns": True, **params}
        return await self.server.request("thread/resume", resumed)

    async def prompt(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        if self._turn is not None and not self._turn.done():
            raise ExecutorError("Codex is in a turn already; steer it instead.")
        turn = self._turn = asyncio.get_running_loop().create_future()
        params = turn_params(
            self._thread, text, files, auto=self._auto, model=self._model, effort=self._effort
        )
        try:
            answer = await self.server.request("turn/start", params)
        except ExecutorError:
            self._turn = None
            raise
        if not turn.done():
            self._turn_id = str(dict(answer.get("turn") or {}).get("id") or "") or self._turn_id
        ended = asyncio.create_task(self.server.ended.wait())
        try:
            await asyncio.wait({turn, ended}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            ended.cancel()
        if not turn.done():
            raise ExecutorError(f"It ended without a result. {self.server.errors}".strip())

    async def steer(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        if self._turn_id is None or self._turn is None or self._turn.done():
            raise ExecutorError("Codex has no turn running to steer.")
        await self.server.request(
            "turn/steer",
            {
                "threadId": self._thread,
                "input": user_input(text, files),
                "expectedTurnId": self._turn_id,
            },
        )

    async def set_auto(self, on: bool) -> None:
        self._auto = on

    async def set_mode(self, mode: str) -> None:
        raise ExecutorError("It has no permission modes Atlas switches, only its automatic one.")

    async def set_model(self, model: str | None, effort: str | None) -> None:
        self._model, self._effort = model, effort

    async def stop(self) -> None:
        self._stopping = True
        if self._turn_id is not None and self._turn is not None and not self._turn.done():
            with contextlib.suppress(ExecutorError, TimeoutError):
                await asyncio.wait_for(
                    self.server.request(
                        "turn/interrupt", {"threadId": self._thread, "turnId": self._turn_id}
                    ),
                    CLOSE_SECONDS,
                )
        await self.server.close()
        _OWN.discard(self._thread)
        for task in (*self._notes, *([self._watch] if self._watch else [])):
            task.cancel()
        await asyncio.gather(
            *self._notes, *([self._watch] if self._watch else []), return_exceptions=True
        )

    def _heard(self, message: dict[str, Any]) -> None:
        params = dict(message.get("params") or {})
        thread = params.get("threadId")
        # A subagent of Codex's own works in a thread of its own: only this one's is shown.
        if thread is not None and thread != self._thread:
            return
        # Codex can start the turn, and ask in it, before the answer to `turn/start` is read: the
        # turn's id comes from whichever is first, so a steer then has a turn to name.
        if message.get("method") == "turn/started":
            self._turn_id = str(dict(params.get("turn") or {}).get("id") or "") or self._turn_id
        for event in events_of(message, self._state):
            self._host.event(event)
            if isinstance(event, Finished) and self._turn is not None and not self._turn.done():
                self._turn_id = None
                self._turn.set_result(None)

    async def _answer(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._turn is not None and not self._turn.done():
            self._turn_id = self._turn_id or params.get("turnId")
        if method in PERMISSION_METHODS:
            verdict = await self._host.approve(permission_of(method, params))
            if not verdict.allow and verdict.reason.strip():
                self._unsaid.append(refused_note(verdict.reason.strip()))
            return verdict_result(method, params, verdict.allow)
        if method == QUESTION_METHOD:
            asked = questions_of(params)
            answers = await self._host.ask([question for _, question in asked])
            return answers_result(asked, answers)
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        raise ExecutorError(f"Atlas does not handle {method}.")

    def _said_why(self) -> None:
        """Tell Codex, once its request is answered, what its `decline` could not carry."""
        while self._unsaid:
            task = asyncio.create_task(self._steer_quietly(self._unsaid.pop(0)))
            self._notes.add(task)
            task.add_done_callback(self._notes.discard)

    async def _steer_quietly(self, text: str) -> None:
        with contextlib.suppress(ExecutorError):
            await self.steer(text)

    async def _watch_end(self) -> None:
        """Between turns, a process that ends by itself is told: nobody waits on a prompt."""
        await self.server.ended.wait()
        if not self._stopping and (self._turn is None or self._turn.done()):
            _OWN.discard(self._thread)
            self._host.failed(f"Codex ended. {self.server.errors}".strip())
