"""The DeepSeek Harness (`dsh`) as the SDK's executor: found, started as an instance, its sessions
listed and read back (step 16q, ROADMAP: Codex, Gemini CLI e DeepSeek).

**An instance is one `dsh --profile acp`** (the SDK's `AcpAgent`) in one folder, holding one
session: a prompt is a `session/prompt`, which answers when the turn ends. What it asks goes to the
host as a permission, answered `allow-once` or `reject-once`, the only two it offers; it asks no
questions over ACP.

**A steer is a cancel and a new prompt**: `dsh` takes one prompt at a time and refuses a second,
so the turn that runs is cancelled (its work so far kept in the session) and the steer goes in as
the next prompt, the same turn for Atlas. A refusal that says why is followed by such a steer with
the reason, since ACP's answer carries none (Atlas's own choice, as for Codex and the Gemini CLI).

**Its automatic mode is its preset `danger-full-access`**, which approves everything with no
review, chosen by the user on 21/10/2026 as for the Gemini CLI. The preset is the process's
(`DSH_PERMISSION_MODE`, read once): switching it between turns starts `dsh` again on the same
session (`session/resume`); during a turn, from the next one.

**The session logs it sends DeepSeek with each request** (`session-log-deepseek`: the messages,
tools, paths and feedback, on by default) are off in the processes Atlas starts, by a `--patch`
of its own, unless the plugin's `session_logs` setting turns them on (decided with the user on
21/10/2026); its terminal's are left as they are.

**Everything else is read from its home, without running it** (`DshHome`): whether it has a key,
its sessions (from the projections it keeps beside them) and their transcripts (its logs, in
Zstandard frames). Its models and efforts come from a session it opens. Its balance comes from
DeepSeek's `/user/balance` with the plugin's key.

**Where it is found**: the `dsh` on the PATH, else npm's global folder. npm's `dsh.cmd` starts a
Node script, which `CreateProcess` cannot run, so on Windows the script is run with the Node
beside it, or the one on the PATH.

**Its other accounts** are homes of their own (`DSH_HOME`, `~/.dsh-<nickname>`), each with its
key. Its ACP has no fork and no copy, so a session goes on only in the account that has it.

**Its newest version** (SDK version 27) is npm's, for an install npm keeps in a folder the user may
write to: the user's npm updates it when they ask. Any other install is left to its own updater.
"""

import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from logging import Logger
from pathlib import Path
from typing import Any, cast

import httpx2

from atlas.plugins.acp import (
    CLOSE_SECONDS,
    AcpAgent,
    AcpError,
    Turn,
    UnhandledError,
    initialize_params,
    permission_of,
    permission_outcome,
    prompt_blocks,
    stop_tree,
    update_events,
)
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
    LimitReached,
    ModelChoice,
    PlanUsage,
    SessionFolder,
    SessionInfo,
    SlashCommand,
    Started,
    Transcript,
    UpdateSource,
)

from .home import DshHome
from .protocol import (
    BALANCE_URL,
    CLIENT_NAME,
    CLIENT_TITLE,
    CLIENT_VERSION,
    EFFORT_OPTION,
    HOME_FOLDER,
    KEY_SECRET,
    LOGS_OFF,
    MODEL_OPTION,
    TITLE,
    Models,
    at_least,
    attached_note,
    balance_of,
    environment,
    finished_of,
    is_limit,
    models_of,
    refused_note,
    version_of,
)

#: The oldest `dsh` this plugin drives: the one it was studied and proved on (21/10/2026).
MINIMUM_VERSION = "0.2.1"
#: The sessions Atlas runs now.
_OWN: set[str] = set()
#: Where npm's package keeps the program, under a global folder.
SCRIPT = Path("node_modules") / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
#: How its own documentation installs it.
INSTALL = "npm install -g @deepseek-ai/dsh"
#: How long `dsh --version`, and a read of the balance, may take.
STATUS_SECONDS = 30.0
#: How long Atlas waits for a session to open: it starts its MCP servers first.
OPEN_SECONDS = 120.0
#: The patch file Atlas hands it, in the plugin's own folder.
PATCH_FILE = "session-logs-off.yml"


def installed_cli() -> list[str] | None:
    """The `dsh` this computer has, as Atlas runs it, or `None` when it has none."""
    hit = shutil.which("dsh")
    if sys.platform == "win32":
        prefixes = [*([Path(hit).parent] if hit else []), *known_prefixes()]
        return next(
            (found for prefix in prefixes if (found := _npm_program(prefix)) is not None), None
        )
    if hit is not None:
        return [hit]
    return next(([str(place)] for place in known_places() if place.is_file()), None)


def known_prefixes() -> list[Path]:
    """npm's global folders on Windows, besides the PATH's."""
    roaming = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return [roaming / "npm"]


def known_places() -> list[Path]:
    """Where npm puts `dsh` on Linux and macOS, besides the PATH."""
    home = Path.home()
    return [
        home / ".npm-global" / "bin" / "dsh",
        Path("/usr/local/bin/dsh"),
        Path("/opt/homebrew/bin/dsh"),
    ]


def _npm_program(prefix: Path) -> list[str] | None:
    """The Node script npm's package keeps in a global folder, with the Node to run it: the one
    beside it (a Node of its own, as Atlas's), else the PATH's."""
    script = prefix / SCRIPT
    if not script.is_file():
        return None
    beside = prefix / "node.exe"
    node = str(beside) if beside.is_file() else shutil.which("node")
    return None if node is None else [node, str(script)]


def not_found() -> str:
    return f"DeepSeek's harness (dsh) is not installed. Install it, in a terminal: {INSTALL}"


#: The npm package it comes in.
PACKAGE = "@deepseek-ai/dsh"


def update_source(script: str) -> UpdateSource | None:
    """npm's newest version, when the script found is in npm's package, in a folder the user may
    write to; else `None`: another install updates some other way."""
    path = str(Path(script).resolve()).replace("\\", "/")
    at = path.find(f"/node_modules/{PACKAGE}/")
    if at < 0:
        return None
    package = Path(path[: at + len(f"/node_modules/{PACKAGE}")])
    return UpdateSource(npm=PACKAGE) if os.access(package, os.W_OK) else None


def too_old(version: str) -> str:
    return (
        f"dsh {version} is older than {MINIMUM_VERSION}, the oldest this plugin drives. Update it, "
        f"in a terminal: {INSTALL}"
    )


def own_home() -> Path:
    """The user's own `dsh` home: `DSH_HOME`, else `~/.dsh`."""
    named = os.environ.get("DSH_HOME")
    return Path(named) if named else Path.home() / HOME_FOLDER


def no_login() -> str:
    return (
        "DeepSeek's harness logs in with an API key: create one on DeepSeek's platform "
        f"(https://platform.deepseek.com/api_keys) and keep it in the plugin's {KEY_SECRET} secret."
    )


class DeepSeek:
    """The executor. `cli` is the program and the arguments before `dsh`'s own; `None` when there
    is none on this computer. With `home`, on the account whose home it is; else on the user's own.
    `data` is the plugin's folder, where its patch is written; `session_logs` keeps `dsh`'s uploads
    on; `transport` reaches DeepSeek (a test's stand-in)."""

    title = TITLE

    def __init__(
        self,
        cli: Sequence[str] | None,
        *,
        logger: Logger,
        data: Path,
        home: Path | None = None,
        api_key: str | None = None,
        session_logs: bool = False,
        transport: httpx2.AsyncBaseTransport | None = None,
        found: bool = False,
    ) -> None:
        self._cli = None if cli is None else list(cli)
        #: Found where its installers put it, not named by the `program` setting: only then
        #: Atlas knows how it updates.
        self._found = found
        self._logger = logger
        self._data = data
        self._home = home
        self._api_key = api_key or None
        self._session_logs = session_logs
        self._transport = transport
        self._files = DshHome(home or own_home())
        #: The models the last session it opened offered; `None` until one did.
        self._models: Models | None = None

    async def detect(self) -> Detection:
        cli = self._cli
        if cli is None:
            return Detection(False, minimum=MINIMUM_VERSION, problem=not_found())
        version, problem = await _version(cli, self._environment(auto=False))
        if not version:
            return Detection(False, minimum=MINIMUM_VERSION, problem=problem)
        supported = at_least(version, MINIMUM_VERSION)
        return Detection(
            True,
            version,
            MINIMUM_VERSION,
            supported,
            self._files.account(self._environment(auto=False)),
            "" if supported else too_old(version),
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            questions=False,
            permissions=True,
            steer=True,
            auto=True,
            accounts=True,
            sessions=True,
            fork=False,
            models=True,
            connectors=False,
            folders=True,
            usage=True,
            commands=False,
            account_folders=True,
            delete_sessions=False,
            files=True,
            updates=True,
        )

    def update_source(self) -> UpdateSource | None:
        return None if self._cli is None or not self._found else update_source(self._cli[-1])

    def account_folder(self) -> Path:
        return self._home or own_home()

    def new_account_folder(self, nickname: str) -> Path:
        return Path(os.path.normpath(Path.home() / f"{HOME_FOLDER}-{nickname}"))

    def for_account(self, folder: Path) -> "DeepSeek":
        return DeepSeek(
            self._cli,
            logger=self._logger,
            data=self._data,
            home=folder,
            api_key=self._api_key,
            session_logs=self._session_logs,
            transport=self._transport,
        )

    async def start(self, spec: ExecutorSpec, host: ExecutorHost) -> "DeepSeekInstance":
        if spec.fork or spec.resume_from is not None:
            raise ExecutorError(
                "DeepSeek's harness copies no session: it goes on only in the account that has it."
            )
        instance = DeepSeekInstance(
            spec, host, self._logger, self._command, self._environment, self._heard
        )
        await instance.open()
        return instance

    async def transcript(self, session: str, folder: Path) -> Transcript:
        return await asyncio.to_thread(self._files.transcript, session)

    async def sessions(self, *, folder: Path | None = None) -> list[SessionInfo]:
        return await asyncio.to_thread(self._files.sessions, folder)

    async def folders(self) -> list[SessionFolder]:
        newest: dict[str, SessionFolder] = {}
        for info in await self.sessions():
            key = os.path.normcase(str(info.folder))
            if key not in newest:
                newest[key] = SessionFolder(info.folder, info.updated_at)
        return list(newest.values())

    async def models(self) -> list[ModelChoice]:
        if self._models is None:
            async with self._probe() as opened:
                self._models = models_of(opened)
        return list(self._models.choices)

    async def connectors(self) -> list[Connector]:
        return []

    async def commands(self, *, folder: Path) -> list[SlashCommand]:
        return []

    async def usage(self) -> PlanUsage:
        key = self._environment(auto=False).get(KEY_SECRET)
        if not key:
            return PlanUsage(
                unavailable="Its balance shows with the plugin's key, or one in its environment."
            )
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, timeout=STATUS_SECONDS
            ) as client:
                response = await client.get(BALANCE_URL, headers={"Authorization": f"Bearer {key}"})
        except httpx2.HTTPError as error:
            return PlanUsage(unavailable=f"DeepSeek's balance could not be read: {error}")
        if response.status_code != 200:
            return PlanUsage(
                unavailable=f"DeepSeek's balance could not be read ({response.status_code})."
            )
        found: object = response.json()
        return balance_of(cast("dict[str, Any]", found) if isinstance(found, dict) else {})

    async def log_in(self, login: str) -> Account:
        raise ExecutorError(no_login())

    async def delete_session(self, session: str, folder: Path) -> None:
        raise ExecutorError("DeepSeek's harness deletes no session over its channel.")

    # -- helpers ----------------------------------------------------------------------------------

    def _heard(self, models: Models) -> None:
        if models.choices:
            self._models = models

    def _environment(self, *, auto: bool) -> dict[str, str]:
        home = None if self._home is None else os.path.normpath(self._home.absolute())
        return environment(os.environ, home, auto=auto, api_key=self._api_key)

    def _command(self) -> list[str]:
        """`dsh --profile acp`, with its session logs off unless the setting keeps them on."""
        if self._cli is None:
            raise ExecutorError("DeepSeek's harness (dsh) is not installed on this computer.")
        command = [*self._cli, "--profile", "acp"]
        if not self._session_logs:
            self._data.mkdir(parents=True, exist_ok=True)
            patch = self._data / PATCH_FILE
            if not patch.is_file() or patch.read_text(encoding="utf-8") != LOGS_OFF:
                patch.write_text(LOGS_OFF, encoding="utf-8")
            command += ["--patch", str(patch)]
        return command

    @contextlib.asynccontextmanager
    async def _probe(self) -> AsyncGenerator[dict[str, Any]]:
        """A `dsh` that runs no prompt: a session opened in the home folder and closed. Its
        projection counts no prompt, so its lists and Atlas's leave it out."""
        agent = AcpAgent(
            self._command(),
            Path.home(),
            title=TITLE,
            environment=self._environment(auto=False),
            logger=self._logger,
        )
        try:
            await agent.start(_client())
            opened = await agent.request(
                "session/new", {"cwd": str(Path.home()), "mcpServers": []}, seconds=OPEN_SECONDS
            )
            yield opened
            with contextlib.suppress(ExecutorError):
                await agent.request("session/close", {"sessionId": opened.get("sessionId")})
        finally:
            await agent.close()


def _client() -> dict[str, Any]:
    return initialize_params(CLIENT_NAME, CLIENT_TITLE, CLIENT_VERSION)


async def _version(cli: Sequence[str], env: dict[str, str]) -> tuple[str, str]:
    """`dsh --version`'s version, or none and why."""
    options: dict[str, Any] = {}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        process = await asyncio.create_subprocess_exec(
            *cli,
            "--version",
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **options,
        )
    except OSError as error:
        return "", f"dsh could not start: {error}"
    try:
        output, errors = await asyncio.wait_for(process.communicate(), STATUS_SECONDS)
    except TimeoutError:
        await stop_tree(process)
        return "", f"dsh --version did not finish in {STATUS_SECONDS:.0f} seconds."
    said = output.decode("utf-8", "replace")
    version = version_of(said)
    if process.returncode != 0 or not version:
        shown = (errors.decode("utf-8", "replace") or said).strip()[:200]
        return "", f"dsh --version said no version Atlas can read: {shown!r}"
    return version, ""


class DeepSeekInstance:
    """One session of `dsh`, in its folder."""

    def __init__(
        self,
        spec: ExecutorSpec,
        host: ExecutorHost,
        logger: Logger,
        command: Callable[[], list[str]],
        environment: Callable[..., dict[str, str]],
        heard: Callable[[Models], None],
    ) -> None:
        self._spec = spec
        self._host = host
        self._logger = logger
        self._command = command
        self._environment = environment
        self._heard_models = heard
        self._auto = spec.auto
        #: The preset the running process has: it changes only with a new process.
        self._running_auto = spec.auto
        self._session = ""
        self._models = Models()
        self._model = spec.model
        self._effort = spec.effort
        self._turn: Turn | None = None
        self._prompts: list[asyncio.Task[dict[str, Any]]] = []
        #: What a steer says, prompted once the turn it cancelled answers.
        self._steers: list[str] = []
        self._stopping = False
        self._watch: asyncio.Task[None] | None = None
        self._notes: set[asyncio.Task[None]] = set()
        #: What a refusal said, steered in once the refusal is written.
        self._unsaid: list[str] = []
        self.agent = self._new_agent()

    def _new_agent(self) -> AcpAgent:
        return AcpAgent(
            self._command(),
            self._spec.folder,
            title=TITLE,
            environment=self._environment(auto=self._auto),
            logger=self._logger,
            answer=self._answer,
            listen=self._hear,
            answered=self._said_why,
        )

    async def open(self) -> None:
        """Start `dsh` and its session; one that cannot open it is stopped."""
        try:
            await self.agent.start(_client())
            opened = await self._open_session(self._spec.resume)
        except BaseException:
            await self.agent.close()
            raise
        _OWN.add(self._session)
        self._models = models_of(opened)
        self._heard_models(self._models)
        await self._choose(self._model, self._effort)
        self._host.event(Started(self._session, model=self._model or self._models.current))
        self._watch = asyncio.create_task(self._watch_end())

    async def _open_session(self, resume: str | None) -> dict[str, Any]:
        folder: dict[str, Any] = {"cwd": str(self._spec.folder), "mcpServers": []}
        if resume is None:
            opened = await self.agent.request("session/new", folder, seconds=OPEN_SECONDS)
            self._session = str(opened.get("sessionId") or "")
            if not self._session:
                raise ExecutorError("DeepSeek's harness started no session.")
            return opened
        resumed = await self.agent.request(
            "session/resume", {"sessionId": resume, **folder}, seconds=OPEN_SECONDS
        )
        self._session = resume
        return resumed

    async def _choose(self, model: str | None, effort: str | None) -> None:
        """Its model and effort as asked, through its configuration options."""
        value = None if model is None else self._models.value(model)
        if model is not None and value is None:
            raise ExecutorError(f"DeepSeek's harness offers no model {model}.")
        if value is not None:
            await self._set_option(MODEL_OPTION, value)
        if effort is not None:
            await self._set_option(EFFORT_OPTION, effort)

    async def _set_option(self, option: str, value: str) -> None:
        await self.agent.request(
            "session/set_config_option",
            {"sessionId": self._session, "configId": option, "value": value},
        )

    async def prompt(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        if self._turn is not None:
            raise ExecutorError("DeepSeek's harness is in a turn already; steer it instead.")
        if self._auto != self._running_auto:
            await self._restart()
        turn = self._turn = Turn()
        self._prompts = [self._send(text, files)]
        try:
            result = await self._last_prompt()
        except AcpError as error:
            self._end(turn)
            if is_limit(error):
                self._host.event(LimitReached())
            self._host.event(
                Finished(str(error), failed=True, session=self._session, reason="failed")
            )
            return
        except ExecutorError as error:
            self._end(turn)
            raise ExecutorError(
                f"It ended without a result. {error} {self.agent.errors}".strip()
            ) from error
        self._end(turn)
        self._host.event(finished_of(result, self._session, turn.last))

    async def _last_prompt(self) -> dict[str, Any]:
        """What the turn's last prompt answered: a prompt a steer cancelled answers `cancelled`,
        and the steer goes in next."""
        while True:
            result = await self._prompts[-1]
            if not self._steers:
                return result
            self._prompts.append(self._send(self._steers.pop(0), ()))

    def _send(self, text: str, files: Sequence[ExecutorFile]) -> asyncio.Task[dict[str, Any]]:
        params = {
            "sessionId": self._session,
            "prompt": prompt_blocks(text, (), attached_note(files)),
        }
        return asyncio.create_task(self.agent.request("session/prompt", params, seconds=None))

    def _end(self, turn: Turn) -> None:
        for event in turn.flush():
            self._host.event(event)
        self._turn = None

    async def steer(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        if self._turn is None:
            raise ExecutorError("DeepSeek's harness has no turn running to steer.")
        note = attached_note(files)
        self._steers.append(f"{note}\n\n{text}" if note else text)
        await self.agent.notify("session/cancel", {"sessionId": self._session})

    async def set_auto(self, on: bool) -> None:
        self._auto = on
        if self._turn is None and on != self._running_auto:
            await self._restart()

    async def _restart(self) -> None:
        """`dsh` again on the same session, with the preset Atlas's switch says now."""
        old = self.agent
        with contextlib.suppress(ExecutorError):
            await old.request("session/close", {"sessionId": self._session}, seconds=CLOSE_SECONDS)
        self._stopping = True
        await old.close()
        if self._watch is not None:
            self._watch.cancel()
            await asyncio.gather(self._watch, return_exceptions=True)
        self._stopping = False
        self.agent = self._new_agent()
        await self.agent.start(_client())
        await self._open_session(self._session)
        self._running_auto = self._auto
        await self._choose(self._model, self._effort)
        self._watch = asyncio.create_task(self._watch_end())

    async def set_model(self, model: str | None, effort: str | None) -> None:
        self._model, self._effort = model or self._model, effort or self._effort
        await self._choose(model, effort)

    async def stop(self) -> None:
        self._stopping = True
        self._steers.clear()
        if self._turn is not None:
            with contextlib.suppress(ExecutorError):
                await self.agent.notify("session/cancel", {"sessionId": self._session})
            with contextlib.suppress(TimeoutError, ExecutorError):
                await asyncio.wait_for(asyncio.gather(*self._prompts), CLOSE_SECONDS)
        with contextlib.suppress(ExecutorError, TimeoutError):
            await self.agent.request(
                "session/close", {"sessionId": self._session}, seconds=CLOSE_SECONDS
            )
        await self.agent.close()
        _OWN.discard(self._session)
        tasks: list[asyncio.Task[Any]] = [
            *self._prompts,
            *self._notes,
            *([self._watch] if self._watch else []),
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _hear(self, message: dict[str, Any]) -> None:
        if message.get("method") != "session/update":
            return
        params = dict(message.get("params") or {})
        if params.get("sessionId") != self._session or self._turn is None:
            return
        for event in update_events(dict(params.get("update") or {}), self._turn):
            self._host.event(event)

    async def _answer(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if method != "session/request_permission":
            raise UnhandledError(f"Atlas does not offer {method}.")
        verdict = await self._host.approve(permission_of(params))
        if not verdict.allow and verdict.reason.strip() and self._turn is not None:
            self._unsaid.append(refused_note(verdict.reason.strip()))
        offered: object = params.get("options")
        options = [
            cast("dict[str, Any]", option)
            for option in (cast("list[object]", offered) if isinstance(offered, list) else [])
            if isinstance(option, dict)
        ]
        return permission_outcome(options, verdict.allow)

    def _said_why(self) -> None:
        """Tell `dsh`, once its request is answered, what its answer could not carry."""
        while self._unsaid:
            self._note(self._steer_quietly(self._unsaid.pop(0)))

    async def _steer_quietly(self, text: str) -> None:
        with contextlib.suppress(ExecutorError):
            await self.steer(text)

    def _note(self, work: Awaitable[None]) -> None:
        async def run() -> None:
            await work

        task = asyncio.create_task(run())
        self._notes.add(task)
        task.add_done_callback(self._notes.discard)

    async def _watch_end(self) -> None:
        """Between turns, a process that ends by itself is told: nobody waits on a prompt."""
        agent = self.agent
        await agent.ended.wait()
        if not self._stopping and self._turn is None and agent is self.agent:
            _OWN.discard(self._session)
            self._host.failed(f"DeepSeek's harness ended. {agent.errors}".strip())
