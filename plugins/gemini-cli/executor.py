"""The Gemini CLI as the SDK's executor: found, started as an instance, its sessions listed and
read back (step 16p, ROADMAP: Codex, Gemini CLI e DeepSeek).

**An instance is one `gemini --acp`** (the SDK's `AcpAgent`) in one folder, holding one session: a
prompt is a `session/prompt`, which answers when the turn ends. What it asks goes to the host as a
permission and the host's answer goes back as an option of the kind the verdict is; the plugin
never knows who answered. It asks no questions over ACP (its `ask_user` is left out there).

**A steer is a second prompt**: the Gemini CLI ends the turn that runs at its next step and goes on
with the new prompt, its history kept, so Atlas waits for the second as the same turn. A refusal
that says why is followed by such a prompt with the reason, since ACP's answer carries none
(Atlas's own choice, as for Codex).

**Its automatic mode is its own `yolo`**, which approves every tool, chosen by the user on
21/10/2026 over Atlas's guardian. A folder the Gemini CLI does not trust refuses it: the session
then asks before each tool, and says so (the user chose to leave the Gemini CLI's trust as it is).

**Everything else is read from its files, without running it** (`GeminiHome`): its account, its
sessions and transcripts, its connectors. Its models come from a session it opens (`session/new`,
which writes a file with no message, which its own lists leave out). Its plan's limits are read
from Google's Code Assist with the token its login keeps, only while the token is good: Atlas
never renews it (decided with the user on 21/10/2026).

**Where it is found**: the `gemini` on the PATH, else where npm and Homebrew put it. On Windows
npm's `gemini.cmd` starts a Node script, which `CreateProcess` cannot run: the script is run with
the Node beside it, or the one on the PATH.

**Its other accounts** are homes of their own (`GEMINI_CLI_HOME`, whose `.gemini` it runs on):
`~/.gemini-<nickname>` beside the user's own `~/.gemini`, logged in through ACP's `authenticate`
on the user's browser. A session goes on in another account, or in a fork, as a copy of its file
with an id of its own, which the Gemini CLI then loads.

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
    AUTH_REQUIRED,
    CLOSE_SECONDS,
    AcpAgent,
    AcpError,
    Turn,
    UnhandledError,
    block_text,
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
    CommandsChanged,
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
    Said,
    SessionFolder,
    SessionInfo,
    SlashCommand,
    Started,
    Transcript,
    UpdateSource,
    UsageChanged,
)

from .home import GeminiHome
from .protocol import (
    ASKING_MODE,
    AUTO_MODE,
    CLIENT_NAME,
    CLIENT_TITLE,
    CLIENT_VERSION,
    CODE_ASSIST,
    GOOGLE_LOGIN,
    KEY_SECRET,
    MODE_NOTE,
    NO_PERSONAL_LOGIN,
    STATE,
    TITLE,
    UNTRUSTED,
    at_least,
    attached_note,
    current_mode,
    current_model,
    environment,
    finished_of,
    is_limit,
    load_request,
    models_of,
    plan_of,
    quota_project,
    refused_note,
    usage_of,
    version_of,
)

#: The oldest Gemini CLI this plugin drives: the one it was studied and proved on (21/10/2026).
MINIMUM_VERSION = "0.65.0"
#: The sessions Atlas runs now: never deleted under it.
_OWN: set[str] = set()
#: Where npm's package keeps the program, under a global folder.
SCRIPT = Path("node_modules") / "@google" / "gemini-cli" / "bundle" / "gemini.js"
#: How its own documentation installs it.
INSTALL = "npm install -g @google/gemini-cli"
#: How long a login may take: the user signs in on the browser (its own flow waits 5 minutes).
LOGIN_SECONDS = 6 * 60
#: How long `gemini --version`, and a read of Google's quota, may take.
STATUS_SECONDS = 30.0
#: How long Atlas waits for a session to open: it starts its MCP servers first.
OPEN_SECONDS = 120.0


def installed_cli() -> list[str] | None:
    """The `gemini` this computer has, as Atlas runs it, or `None` when it has none."""
    if sys.platform == "win32":
        hit = shutil.which("gemini")
        prefixes = [*([Path(hit).parent] if hit else []), *known_prefixes()]
        return next(
            (found for prefix in prefixes if (found := _npm_program(prefix)) is not None), None
        )
    hit = shutil.which("gemini")
    if hit is not None:
        return [hit]
    return next(([str(place)] for place in known_places() if place.is_file()), None)


def known_prefixes() -> list[Path]:
    """npm's global folders on Windows, besides the PATH's."""
    home = Path.home()
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    return [roaming / "npm"]


def known_places() -> list[Path]:
    """Where npm and Homebrew put `gemini` on Linux and macOS, besides the PATH."""
    home = Path.home()
    return [
        home / ".npm-global" / "bin" / "gemini",
        Path("/usr/local/bin/gemini"),
        Path("/opt/homebrew/bin/gemini"),
        Path("/home/linuxbrew/.linuxbrew/bin/gemini"),
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
    """Why there is no `gemini` Atlas can run, and how to have one."""
    return f"The Gemini CLI is not installed. Install it, in a terminal: {INSTALL}"


#: The npm package it comes in.
PACKAGE = "@google/gemini-cli"


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
        f"Gemini CLI {version} is older than {MINIMUM_VERSION}, the oldest this plugin drives. "
        f"Update it, in a terminal: {INSTALL}"
    )


def own_state() -> Path:
    """The user's own `.gemini`: in `GEMINI_CLI_HOME`, else in the home folder."""
    named = os.environ.get("GEMINI_CLI_HOME")
    return (Path(named) if named else Path.home()) / STATE


def state_of(folder: Path) -> Path:
    """An account folder's `.gemini`: itself, the user's own, or inside another account's home."""
    return folder if folder.name == STATE else folder / STATE


def home_of(folder: Path) -> str:
    """What `GEMINI_CLI_HOME` is for an account folder."""
    return os.path.normpath((folder.parent if folder.name == STATE else folder).absolute())


class GeminiCli:
    """The executor. `cli` is the program and the arguments before the Gemini CLI's own; `None`
    when there is none on this computer. With `home`, on the account whose folder it is; else on
    the user's own. `transport` reaches Google (a test's stand-in); `api_key` is the plugin's
    secret, handed to the sessions it opens."""

    title = TITLE

    def __init__(
        self,
        cli: Sequence[str] | None,
        *,
        logger: Logger,
        home: Path | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        api_key: str | None = None,
        found: bool = False,
    ) -> None:
        self._cli = None if cli is None else list(cli)
        #: Found where its installers put it, not named by the `program` setting: only then
        #: Atlas knows how it updates.
        self._found = found
        #: The plugin's secret: the API key a personal account logs in with.
        self._api_key = api_key or None
        self._logger = logger
        self._home = home
        self._transport = transport
        self._files = GeminiHome(state_of(home) if home is not None else own_state())
        #: The models the last session it opened offered; `None` until one did.
        self._models: tuple[ModelChoice, ...] | None = None
        #: The slash commands the last session it opened offered.
        self._commands: tuple[SlashCommand, ...] = ()

    async def detect(self) -> Detection:
        cli = self._cli
        if cli is None:
            return Detection(False, minimum=MINIMUM_VERSION, problem=not_found())
        version, problem = await _version(cli, self._environment())
        if not version:
            return Detection(False, minimum=MINIMUM_VERSION, problem=problem)
        supported = at_least(version, MINIMUM_VERSION)
        return Detection(
            True,
            version,
            MINIMUM_VERSION,
            supported,
            self._files.account(self._environment()),
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
        return None if self._cli is None or not self._found else update_source(self._cli[-1])

    def account_folder(self) -> Path:
        return self._home or own_state()

    def new_account_folder(self, nickname: str) -> Path:
        return Path(os.path.normpath(Path.home() / f".gemini-{nickname}"))

    def for_account(self, folder: Path) -> "GeminiCli":
        return GeminiCli(
            self._cli,
            logger=self._logger,
            home=folder,
            transport=self._transport,
            api_key=self._api_key,
        )

    async def start(self, spec: ExecutorSpec, host: ExecutorHost) -> "GeminiInstance":
        cli = self._need()
        source: Path | None = None
        if spec.resume is not None and (spec.fork or spec.resume_from is not None):
            # A copy: of its own session, or of another account's, whose file that account keeps.
            files = (
                self._files if spec.resume_from is None else GeminiHome(state_of(spec.resume_from))
            )
            source = await asyncio.to_thread(files.find, spec.resume)
            if source is None:
                raise ExecutorError("The Gemini CLI no longer keeps this session.")
        instance = GeminiInstance(
            cli,
            spec,
            host,
            self._logger,
            self._environment(),
            self._files,
            source,
            heard=self._heard,
            usage=self._usage_after_turn,
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
            async with self._probe() as (_agent, opened):
                self._models = models_of(opened)
        return list(self._models)

    async def connectors(self) -> list[Connector]:
        return await asyncio.to_thread(self._files.connectors)

    async def commands(self, *, folder: Path) -> list[SlashCommand]:
        return list(self._commands)

    async def usage(self) -> PlanUsage:
        account = self._files.account(self._environment())
        if not account.logged_in:
            return PlanUsage(unavailable="The Gemini CLI is not logged in.")
        if not self._google():
            return PlanUsage(
                unavailable="The Gemini CLI is logged in with an API key, which has no plan "
                "limits to read."
            )
        token = self._files.token()
        if not token:
            return PlanUsage(
                unavailable="The Gemini CLI renews its login when it next runs; the limits show "
                "after its next turn."
            )
        try:
            return await read_quota(token, self._transport)
        except ExecutorError as error:
            return PlanUsage(unavailable=str(error))

    async def log_in(self, login: str) -> Account:
        """Its own login with Google, on the user's browser, through ACP's `authenticate`: a
        company's account only, since Google refuses a personal one, which keeps an API key in
        the plugin's secret instead."""
        self._models = None
        self._files.state.mkdir(parents=True, exist_ok=True)
        agent = self._agent(Path.home())
        try:
            await agent.start(_client())
            await agent.request("authenticate", {"methodId": GOOGLE_LOGIN}, seconds=LOGIN_SECONDS)
        except AcpError as error:
            if NO_PERSONAL_LOGIN not in str(error).lower():
                raise
            raise ExecutorError(no_personal_login()) from error
        finally:
            await agent.close()
        return self._files.account(self._environment())

    async def delete_session(self, session: str, folder: Path) -> None:
        if session in _OWN:
            raise ExecutorError("This session runs in Atlas now. Stop it, then delete it.")
        if not await asyncio.to_thread(self._files.delete, session):
            raise ExecutorError("The Gemini CLI no longer keeps this session.")

    # -- helpers ----------------------------------------------------------------------------------

    def _heard(self, models: tuple[ModelChoice, ...], commands: tuple[SlashCommand, ...]) -> None:
        """What a session it opened offered: kept for the box until the next one says."""
        if models:
            self._models = models
        if commands:
            self._commands = commands

    async def _usage_after_turn(self) -> PlanUsage | None:
        """The limits after a turn, when its login is Google's: the token is fresh then."""
        if not self._google():
            return None
        usage = await self.usage()
        return usage if usage.windows else None

    def _google(self) -> bool:
        account = self._files.account(self._environment())
        return account.logged_in and self._files.login() == GOOGLE_LOGIN

    def _environment(self) -> dict[str, str]:
        home = None if self._home is None else home_of(self._home)
        return environment(os.environ, home, self._api_key)

    def _agent(self, folder: Path) -> AcpAgent:
        return AcpAgent(
            [*self._need(), "--acp"],
            folder,
            title=TITLE,
            environment=self._environment(),
            logger=self._logger,
        )

    @contextlib.asynccontextmanager
    async def _probe(self) -> AsyncGenerator[tuple[AcpAgent, dict[str, Any]]]:
        """A Gemini CLI that runs no prompt, a session opened in the home folder and closed."""
        agent = self._agent(Path.home())
        try:
            await agent.start(_client())
            opened = await agent.request(
                "session/new", {"cwd": str(Path.home()), "mcpServers": []}, seconds=OPEN_SECONDS
            )
            yield agent, opened
        finally:
            await agent.close()

    def _need(self) -> list[str]:
        if self._cli is None:
            raise ExecutorError("The Gemini CLI is not installed on this computer.")
        return self._cli


def _client() -> dict[str, Any]:
    return initialize_params(CLIENT_NAME, CLIENT_TITLE, CLIENT_VERSION)


async def read_quota(token: str, transport: httpx2.AsyncBaseTransport | None = None) -> PlanUsage:
    """The account's tier and quota from Google's Code Assist, as the Gemini CLI's `/stats` reads
    them: no model request is spent. Raises `ExecutorError`."""
    base = os.environ.get("CODE_ASSIST_ENDPOINT")
    endpoint = f"{base}/v1internal" if base else CODE_ASSIST
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx2.AsyncClient(
            transport=transport, timeout=STATUS_SECONDS, headers=headers
        ) as client:
            loaded = await _post(client, f"{endpoint}:loadCodeAssist", load_request())
            project = quota_project(loaded)
            if not project:
                return PlanUsage(
                    plan=plan_of(loaded), unavailable="Google names no project for this account."
                )
            quota = await _post(client, f"{endpoint}:retrieveUserQuota", {"project": project})
    except httpx2.HTTPError as error:
        raise ExecutorError(f"Google's quota could not be read: {error}") from error
    return usage_of(plan_of(loaded), quota)


async def _post(client: httpx2.AsyncClient, url: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(url, json=body)
    if response.status_code != 200:
        raise ExecutorError(f"Google's quota could not be read ({response.status_code}).")
    found: object = response.json()
    return found if isinstance(found, dict) else {}  # pyright: ignore[reportUnknownVariableType]


async def _version(cli: Sequence[str], env: dict[str, str]) -> tuple[str, str]:
    """`gemini --version`'s version, or none and why."""
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
        return "", f"The Gemini CLI could not start: {error}"
    try:
        output, errors = await asyncio.wait_for(process.communicate(), STATUS_SECONDS)
    except TimeoutError:
        await stop_tree(process)
        return "", f"gemini --version did not finish in {STATUS_SECONDS:.0f} seconds."
    said = output.decode("utf-8", "replace")
    version = version_of(said)
    if process.returncode != 0 or not version:
        shown = (errors.decode("utf-8", "replace") or said).strip()[:200]
        return "", f"gemini --version said no version Atlas can read: {shown!r}"
    return version, ""


class GeminiInstance:
    """One session of the Gemini CLI, in its folder."""

    def __init__(
        self,
        cli: Sequence[str],
        spec: ExecutorSpec,
        host: ExecutorHost,
        logger: Logger,
        env: dict[str, str],
        files: GeminiHome,
        source: Path | None = None,
        *,
        heard: Callable[[tuple[ModelChoice, ...], tuple[SlashCommand, ...]], None],
        usage: Callable[[], Awaitable[PlanUsage | None]],
    ) -> None:
        self._spec = spec
        self._host = host
        self._files = files
        self._source = source
        self._heard_offer = heard
        self._usage = usage
        self._auto = spec.auto
        self._session = ""
        self._models: tuple[ModelChoice, ...] = ()
        #: The turn that runs: what its updates said so far, and its prompts (a steer adds one).
        self._turn: Turn | None = None
        self._prompts: list[asyncio.Task[dict[str, Any]]] = []
        self._stopping = False
        self._watch: asyncio.Task[None] | None = None
        self._notes: set[asyncio.Task[None]] = set()
        #: What a refusal said, prompted once the refusal is written.
        self._unsaid: list[str] = []
        command = [*cli, "--acp"]
        if spec.directories:
            command += ["--include-directories", ",".join(str(path) for path in spec.directories)]
        files.state.mkdir(parents=True, exist_ok=True)
        self.agent = AcpAgent(
            command,
            spec.folder,
            title=TITLE,
            environment=env,
            logger=logger,
            answer=self._answer,
            listen=self._hear,
            answered=self._said_why,
        )

    async def open(self) -> None:
        """Start the Gemini CLI and its session; one that cannot open it is stopped."""
        try:
            await self.agent.start(_client())
            opened = await self._open_session()
        except AcpError as error:
            await self.agent.close()
            if error.code == AUTH_REQUIRED:
                raise ExecutorError(
                    f"The Gemini CLI is not logged in: {error}. Log in to it, then try again."
                ) from error
            raise
        except BaseException:
            await self.agent.close()
            raise
        _OWN.add(self._session)
        self._models = models_of(opened)
        self._heard_offer(self._models, ())
        # The model asked for, before the session is said to start on it.
        if self._spec.model is not None:
            await self.set_model(self._spec.model, None)
        self._host.event(Started(self._session, model=self._spec.model or current_model(opened)))
        await self._set_mode(current_mode(opened))
        self._watch = asyncio.create_task(self._watch_end())

    async def _open_session(self) -> dict[str, Any]:
        """A new session, or the one to go on with: itself, or a copy of it (a fork, or another
        account's), made from its file once a session here registered the folder."""
        spec = self._spec
        folder: dict[str, Any] = {"cwd": str(spec.folder), "mcpServers": []}
        opened = await self.agent.request("session/new", folder, seconds=OPEN_SECONDS)
        if spec.resume is None:
            self._session = str(opened.get("sessionId") or "")
            if not self._session:
                raise ExecutorError("The Gemini CLI started no session.")
            return opened
        resumed = spec.resume
        if self._source is not None:
            try:
                resumed = await asyncio.to_thread(self._files.copy, self._source, spec.folder)
            except (LookupError, OSError) as error:
                raise ExecutorError(f"The session could not be copied: {error}") from error
        loaded = await self.agent.request(
            "session/load", {"sessionId": resumed, **folder}, seconds=OPEN_SECONDS
        )
        self._session = resumed
        return {**opened, **loaded}

    async def _set_mode(self, mode: str) -> None:
        """Its mode as Atlas's switch says: `yolo` on, the one that asks off. A folder it does not
        trust refuses `yolo`, and the session says so."""
        wanted = AUTO_MODE if self._auto else ASKING_MODE
        if mode == wanted:
            return
        try:
            await self.agent.request(
                "session/set_mode", {"sessionId": self._session, "modeId": wanted}
            )
        except AcpError as error:
            if UNTRUSTED not in str(error).lower():
                raise
            self._host.event(Said(untrusted_note()))

    async def prompt(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        if self._turn is not None:
            raise ExecutorError("The Gemini CLI is in a turn already; steer it instead.")
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
        self._note(self._tell_usage())

    async def _last_prompt(self) -> dict[str, Any]:
        """What the turn's last prompt answered: a prompt a steer replaced answers `cancelled`,
        and the steer's goes on."""
        while True:
            current = self._prompts[-1]
            result = await current
            if current is self._prompts[-1]:
                return result

    def _send(self, text: str, files: Sequence[ExecutorFile]) -> asyncio.Task[dict[str, Any]]:
        params = {
            "sessionId": self._session,
            "prompt": prompt_blocks(text, files, attached_note(files)),
        }
        return asyncio.create_task(self.agent.request("session/prompt", params, seconds=None))

    def _end(self, turn: Turn) -> None:
        for event in turn.flush():
            self._host.event(event)
        self._turn = None

    async def steer(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        if self._turn is None:
            raise ExecutorError("The Gemini CLI has no turn running to steer.")
        self._prompts.append(self._send(text, files))

    async def set_auto(self, on: bool) -> None:
        self._auto = on
        wanted = AUTO_MODE if on else ASKING_MODE
        try:
            await self.agent.request(
                "session/set_mode", {"sessionId": self._session, "modeId": wanted}
            )
        except AcpError as error:
            if UNTRUSTED in str(error).lower():
                raise ExecutorError(untrusted_note()) from error
            raise

    async def set_mode(self, mode: str) -> None:
        raise ExecutorError("It has no permission modes Atlas switches, only its automatic one.")

    async def set_model(self, model: str | None, effort: str | None) -> None:
        if model is None:
            return
        await self.agent.request(
            "session/set_model", {"sessionId": self._session, "modelId": model}
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._turn is not None:
            with contextlib.suppress(ExecutorError):
                await self.agent.notify("session/cancel", {"sessionId": self._session})
            with contextlib.suppress(TimeoutError, ExecutorError):
                await asyncio.wait_for(asyncio.gather(*self._prompts), CLOSE_SECONDS)
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
        if params.get("sessionId") != self._session:
            return
        update = dict(params.get("update") or {})
        kind = update.get("sessionUpdate")
        if kind == "available_commands_update":
            events = update_events(update, Turn())
            for event in events:
                if isinstance(event, CommandsChanged):
                    self._heard_offer((), event.commands)
                self._host.event(event)
            return
        # Between turns only a session's replay comes (`session/load` says its history again).
        turn = self._turn
        if turn is None:
            return
        # A tool's `content` is a list; only a message's is one block, which its mode notes are.
        if kind == "agent_message_chunk" and block_text(update.get("content")).startswith(
            MODE_NOTE
        ):
            return
        for event in update_events(update, turn):
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
        """Tell the Gemini CLI, once its request is answered, what its answer could not carry."""
        while self._unsaid:
            self._note(self._steer_quietly(self._unsaid.pop(0)))

    async def _steer_quietly(self, text: str) -> None:
        with contextlib.suppress(ExecutorError):
            await self.steer(text)

    async def _tell_usage(self) -> None:
        with contextlib.suppress(ExecutorError):
            usage = await self._usage()
            if usage is not None:
                self._host.event(UsageChanged(usage.windows))

    def _note(self, work: Awaitable[None]) -> None:
        async def run() -> None:
            await work

        task = asyncio.create_task(run())
        self._notes.add(task)
        task.add_done_callback(self._notes.discard)

    async def _watch_end(self) -> None:
        """Between turns, a process that ends by itself is told: nobody waits on a prompt."""
        await self.agent.ended.wait()
        if not self._stopping and self._turn is None:
            _OWN.discard(self._session)
            self._host.failed(f"The Gemini CLI ended. {self.agent.errors}".strip())


def no_personal_login() -> str:
    """What a personal account is told when Google refuses its login (found on 21/10/2026)."""
    return (
        "Google no longer lets a personal account log in to the Gemini CLI. Create an API key in "
        "Google AI Studio (https://aistudio.google.com/apikey) and keep it in the plugin's "
        f"{KEY_SECRET} secret."
    )


def untrusted_note() -> str:
    return (
        "[Atlas: the Gemini CLI does not trust this folder, so it asks before each tool even in "
        "the automatic mode. Trust the folder in the Gemini CLI to let it decide by itself. "
        "Atlas's workspaces are all in the worktrees folder of its home: trusting that folder "
        "once covers every one.]"
    )
