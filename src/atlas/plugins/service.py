"""Plugins: which there are, turning them on with the user's consent, and loading their Python.

**Loading has a deadline**, so a `register()` that hangs does not hang the whole start: the import
and `register(ctx)` run in a thread and get 10 seconds; past them the plugin is marked failed and
what its context registers later is ignored. A plugin that raises, on load or later, is marked
failed and the rest of Atlas goes on.

**A context grants only what the manifest declares:** a secret or a setting it does not declare
raises, and so does a channel, an executor or a tool; the consent covered the declaration, so the
code may not do more.

**What a plugin started stops with it** (`on_unload`): the stops its Python registered run
when it is turned off, when what registered them is loaded again, and when Atlas closes.

**Files a plugin brings** (step 16j) are read where they are, while it is on: its skills folder,
and its MCP servers, named for it, with its folders in place of Claude Code's variables.

**Dependencies are Claude Code's** (step 16n): a plugin runs only while the plugins it needs run,
in the versions it asks for; one turned off or failed stops those that need it, which say why.
Turning a plugin on whose dependencies are not on is refused, and so is turning off one that
another on needs. Each needs its own consent, so the user is told which to turn on first (Atlas's
own, marked). They load first.
"""

import asyncio
import hashlib
import inspect
import logging
import os
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from atlas.home import Database, Home, utc_now
from atlas.hooks import EVENTS, PluginCallback, PluginCommand, PluginMonitor
from atlas.plugins.model import (
    AGENTS_FOLDER,
    BIN_FOLDER,
    COMMANDS_FOLDER,
    ON_SKILL,
    SCREEN_FILE,
    SKILLS_FOLDER,
    THEMES_FOLDER,
    Admission,
    Manifest,
    ManifestError,
    Pairing,
    Theme,
    VersionError,
    admission,
    new_code,
    normal_code,
    parse_theme,
    plugin_servers,
    satisfies,
)
from atlas.plugins.sdk import (
    API_VERSION,
    CallRun,
    Channel,
    CliRun,
    CommandRun,
    Executor,
    HookRun,
    LlmError,
    LlmMessage,
    LlmResult,
    PluginLlm,
    ReadRun,
    ToolRun,
)
from atlas.plugins.store import (
    BUNDLED_FOLDER,
    Found,
    PluginStore,
    find_plugins,
    import_plugin,
    plugin_hooks,
)
from atlas.secrets import SecretsError, Vault
from atlas.skills import command_files

#: How long a plugin's import and `register(ctx)` may take.
LOAD_SECONDS = 10.0
#: What the watch of a plugin's files skips (step 16n): what programs write, not its author.
UNWATCHED = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".ruff_cache"}
)
#: The most files of a plugin the watch looks at; past them, the rest go unseen.
WATCHED_FILES = 5000

_logger = logging.getLogger("atlas.plugins")


class PluginError(Exception):
    """What the user asked of a plugin cannot be done; the message says why and what to do."""


@dataclass(frozen=True, slots=True)
class PluginView:
    """A plugin as the user's screen and the terminal list it."""

    name: str
    folder: Path
    bundled: bool
    manifest: Manifest | None
    enabled: bool
    #: The user consented to what it declares now; an update that declares more clears it.
    consented: bool
    #: Why it cannot be turned on or run: an unreadable manifest, what Atlas does not load yet,
    #: or what failed while it ran.
    problem: str | None
    #: Each declared secret, with whether the vault holds it.
    secrets: dict[str, bool] = field(default_factory=dict[str, bool])
    #: Each declared setting's value, the user's or its default.
    settings: dict[str, str] = field(default_factory=dict[str, str])

    @property
    def running(self) -> bool:
        return self.enabled and self.consented and self.problem is None


@dataclass(frozen=True, slots=True)
class LoadedChannel:
    plugin: str
    name: str
    make: Callable[[], Channel]


@dataclass(frozen=True, slots=True)
class LoadedExecutor:
    plugin: str
    name: str
    make: Callable[[], Executor]


@dataclass(frozen=True, slots=True)
class LoadedTool:
    """A tool a plugin registered (SDK version 14), for the assistant to offer."""

    plugin: str
    name: str
    description: str
    parameters: dict[str, Any]
    run: ToolRun
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class LoadedCommand:
    """A slash command a plugin registered (SDK version 16), typed `/<plugin>:<name>`."""

    plugin: str
    name: str
    run: CommandRun
    description: str
    hint: str = ""

    @property
    def typed(self) -> str:
        return f"{self.plugin}:{self.name}"


@dataclass(frozen=True, slots=True)
class LoadedCli:
    """A terminal command a plugin registered (SDK version 16), `atlas <name>`."""

    plugin: str
    name: str
    run: CliRun
    description: str


@dataclass(frozen=True, slots=True)
class LoadedCall:
    """A call a plugin's screen may make to its Python (SDK version 17)."""

    plugin: str
    name: str
    run: CallRun


@dataclass(frozen=True, slots=True)
class LoadedReader:
    """What a plugin registered to read one kind of attachment into text (SDK version 21)."""

    plugin: str
    kind: str
    run: ReadRun


@dataclass(frozen=True, slots=True)
class PluginScreen:
    """A plugin's screen for the app (step 16m): its `desktop/plugin.js`, as the app loads it."""

    plugin: str
    source: str
    #: Its desktop powers (`atlas.desktop`, version 23), which the app gives it.
    desktop: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PluginTheme:
    """A theme of a plugin that is on (version 24), as the app paints it."""

    plugin: str
    theme: Theme


#: The largest screen Atlas hands the app: a plugin's whole screen in one file.
SCREEN_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PluginParts:
    """What the Python of the plugins that are on registered for the loop: tools (version 14),
    hooks (version 15), slash commands (version 16) and attachment readers (version 21)."""

    tools: list[LoadedTool] = field(default_factory=list[LoadedTool])
    hooks: list[PluginCallback] = field(default_factory=list[PluginCallback])
    commands: list[LoadedCommand] = field(default_factory=list[LoadedCommand])
    calls: list[LoadedCall] = field(default_factory=list[LoadedCall])
    readers: list[LoadedReader] = field(default_factory=list[LoadedReader])


class PluginHost(Protocol):
    """What Atlas lends a plugin from outside the plugins (SDK version 16): the user's model, and
    its conversations. The server gives it; with none, a plugin's `llm` and `notify` say Atlas is
    not serving (a terminal command)."""

    async def complete(
        self,
        plugin: str,
        prompt: str | Sequence[LlmMessage],
        *,
        system: str,
        max_tokens: int | None,
        json_schema: Mapping[str, Any] | None,
    ) -> LlmResult: ...

    async def notify(self, plugin: str, conversation: str, text: str) -> bool: ...


class _NoHost:
    async def complete(
        self,
        plugin: str,
        prompt: str | Sequence[LlmMessage],
        *,
        system: str,
        max_tokens: int | None,
        json_schema: Mapping[str, Any] | None,
    ) -> LlmResult:
        raise LlmError("Atlas is not serving here, so there is no model to call.")

    async def notify(self, plugin: str, conversation: str, text: str) -> bool:
        return False


#: What a plugin registered to stop with it.
type Stop = Callable[[], Awaitable[None] | None]


class Plugins:
    """The home's plugins."""

    def __init__(
        self,
        home: Home,
        database: Database,
        vault: Vault,
        *,
        clock: Callable[[], datetime] = utc_now,
        bundled_folder: Path = BUNDLED_FOLDER,
        load_seconds: float = LOAD_SECONDS,
    ) -> None:
        self._home = home
        self._vault = vault
        self._clock = clock
        self._bundled = bundled_folder
        self._load_seconds = load_seconds
        self.store = PluginStore(database)
        #: What failed while a plugin loaded or ran, until it is turned on again.
        self._failures: dict[str, str] = {}
        #: The stops each plugin registered, by the plugin and what was loaded (`tools`,
        #: `channels`, `executors`).
        self._stops: dict[tuple[str, str], list[Stop]] = {}
        #: The user's model and conversations, as the server lends them (version 16).
        self.host: PluginHost = _NoHost()

    # -- listing ----------------------------------------------------------------------------------

    @property
    def folder(self) -> Path:
        """Where the user's plugins are: the home's `plugins/`."""
        return self._home.plugins_folder

    def data_folder(self, name: str) -> Path:
        """A plugin's data folder (`${CLAUDE_PLUGIN_DATA}`), which outlives its updates."""
        return self._home.plugin_data_folder / name

    def stamps(self) -> dict[str, str]:
        """What changes, by plugin, when its files are saved, or it is turned on, off or set
        (step 16n): the server reloads a plugin whose stamp changed. The files are those of the
        user's plugins, but for `UNWATCHED`; a plugin Atlas ships changes only with Atlas."""
        states = self.store.states()
        found: dict[str, str] = {}
        for view in self.all():
            state = states.get(view.name)
            digest = hashlib.sha256(repr((state, self.store.settings(view.name))).encode())
            if not view.bundled:
                _stamp_files(view.folder, digest)
            found[view.name] = digest.hexdigest()
        return found

    def all(self) -> list[PluginView]:
        states = self.store.states()
        try:
            stored = set(self._vault.names())
        except SecretsError:
            stored = set[str]()
        views: list[PluginView] = []
        for found in find_plugins(self._home.plugins_folder, self._bundled):
            state = states.get(found.name)
            manifest = found.manifest
            enabled = state is not None and state.enabled
            consented = (
                manifest is not None
                and state is not None
                and state.consent == manifest.fingerprint()
            )
            views.append(
                PluginView(
                    found.name,
                    found.folder,
                    found.bundled,
                    manifest,
                    enabled,
                    consented,
                    _problem(found) or self._failures.get(found.name),
                    {}
                    if manifest is None
                    else {s.name: s.name in stored for s in manifest.secrets},
                    {} if manifest is None else self._settings(manifest),
                )
            )
        return _with_dependencies(views)

    def view(self, name: str) -> PluginView:
        found = next((view for view in self.all() if view.name == name), None)
        if found is None:
            raise PluginError(f"There is no plugin {name!r}. `atlas plugins list` lists them.")
        return found

    def _settings(self, manifest: Manifest) -> dict[str, str]:
        chosen = self.store.settings(manifest.name)
        return {s.name: chosen.get(s.name, s.default) for s in manifest.settings}

    # -- deciding ---------------------------------------------------------------------------------

    def enable(self, name: str, consent: str) -> PluginView:
        """Turn a plugin on, `consent` being the fingerprint of the declaration the user saw: a
        plugin that changed since is refused, so the user is asked again."""
        view = self.view(name)
        manifest = view.manifest
        if manifest is None:
            raise PluginError(f"{name} cannot be turned on: {view.problem}")
        if manifest.unsupported:
            raise PluginError(
                f"{name} brings {', '.join(manifest.unsupported)}, which Atlas does not load yet."
            )
        if manifest.api > API_VERSION:
            raise PluginError(
                f"{name} was written for a newer Atlas (SDK {manifest.api}; this one is "
                f"{API_VERSION}). Update Atlas with `atlas update`."
            )
        if consent != manifest.fingerprint():
            raise PluginError(
                f"{name} declares something other than what was shown. Look at it again and "
                "consent to what it declares now."
            )
        unmet = _unmet(manifest, {other.name: other for other in self.all()})
        if unmet is not None:
            raise PluginError(f"{name} cannot be turned on yet. {unmet}")
        self.store.decide(name, enabled=True, consent=consent, now=self._clock())
        self._failures.pop(name, None)
        _logger.info("A plugin was turned on.", extra={"plugin": name})
        return self.view(name)

    def disable(self, name: str) -> PluginView:
        """Turn a plugin off; refused while a plugin that is on needs it, as in Claude Code."""
        view = self.view(name)
        needing = needs(self.all(), name)
        if needing:
            raise PluginError(
                f"{', '.join(needing)} {'needs' if len(needing) == 1 else 'need'} {name}: turn "
                f"{'it' if len(needing) == 1 else 'them'} off first."
            )
        self.store.decide(name, enabled=False, consent=None, now=self._clock())
        _logger.info("A plugin was turned off.", extra={"plugin": view.name})
        return self.view(name)

    def set_setting(self, name: str, setting: str, value: str | None) -> PluginView:
        """Change a setting the plugin declares; `None` goes back to its default."""
        view = self.view(name)
        if view.manifest is None or view.manifest.setting(setting) is None:
            raise PluginError(f"{name} has no setting {setting!r}.")
        self.store.set_setting(name, setting, None if value is None else value.strip())
        return self.view(name)

    def set_secret(self, name: str, secret: str, value: str) -> PluginView:
        """Store a secret the plugin declares, in the vault."""
        view = self.view(name)
        if secret not in view.secrets:
            raise PluginError(f"{name} declares no secret {secret}.")
        value = value.strip()
        if not value:
            raise PluginError(f"The value for {secret} is empty.")
        try:
            self._vault.set(secret, value)
        except SecretsError as error:
            raise PluginError(str(error)) from error
        return self.view(name)

    def clear_failure(self, name: str) -> None:
        self._failures.pop(name, None)

    def failed(self, name: str, reason: str) -> None:
        """A plugin's code failed while it ran: it shows, and it stays off until turned on
        again."""
        self._failures[name] = reason
        _logger.warning("A plugin failed: %s", reason, extra={"plugin": name})

    # -- loading ----------------------------------------------------------------------------------

    async def channels(self, only: set[str] | None = None) -> list[LoadedChannel]:
        """The channels of the plugins that are on (of those named in `only`), each plugin loaded
        with its deadline."""
        loaded: list[LoadedChannel] = []
        for context in await self._load(lambda m: bool(m.channels), only, kind="channels"):
            loaded += [LoadedChannel(context.name, n, make) for n, make in context.channels.items()]
        return loaded

    async def executors(self) -> list[LoadedExecutor]:
        """The executors of the plugins that are on, each plugin loaded with its deadline."""
        loaded: list[LoadedExecutor] = []
        for context in await self._load(lambda m: bool(m.executors), None, kind="executors"):
            loaded += [
                LoadedExecutor(context.name, n, make) for n, make in context.executors.items()
            ]
        return loaded

    async def parts(self) -> PluginParts:
        """The tools and the Python hooks of the plugins that are on, each plugin loaded once,
        with its deadline; what the ones loaded before started is stopped first."""
        await self.stop(kind="parts")
        parts = PluginParts()
        wanted: Callable[[Manifest], bool] = lambda m: bool(  # noqa: E731
            m.tools or m.python_hooks or m.slash_commands or m.calls or m.readers
        )
        for context in await self._load(wanted, None, kind="parts"):
            parts.tools.extend(context.tools)
            parts.hooks.extend(context.hooks)
            parts.commands.extend(context.commands)
            parts.calls.extend(context.calls)
            for reader in context.readers:
                taken = next((r for r in parts.readers if r.kind == reader.kind), None)
                if taken is None:
                    parts.readers.append(reader)
                else:
                    # One reader a kind, the first loaded: a file is read once, by one plugin.
                    _logger.warning(
                        "%s reads %s attachments, which %s already reads; it is left out.",
                        reader.plugin,
                        reader.kind,
                        taken.plugin,
                    )
        return parts

    def screens(self) -> list[PluginScreen]:
        """The screens of the plugins that are on (step 16m), for the app to load. One whose
        file cannot be read, or is past `SCREEN_BYTES`, marks its plugin."""
        found: list[PluginScreen] = []
        for view in self.all():
            if not view.running or view.manifest is None or not view.manifest.screen:
                continue
            file = view.folder / SCREEN_FILE
            try:
                data = file.read_bytes()
            except OSError as error:
                self.failed(view.name, f"Its screen cannot be read: {error}")
                continue
            if len(data) > SCREEN_BYTES:
                self.failed(view.name, f"Its screen is over {SCREEN_BYTES // 1024 // 1024} MB.")
                continue
            found.append(
                PluginScreen(
                    view.name,
                    data.decode("utf-8", errors="replace"),
                    view.manifest.desktop,
                )
            )
        return found

    def themes(self) -> list[PluginTheme]:
        """The themes of the plugins that are on (version 24), for the app to offer. A file that
        cannot be read, or is no theme, marks its plugin."""
        found: list[PluginTheme] = []
        for view in self.all():
            if not view.running or view.manifest is None or not view.manifest.themes:
                continue
            for name in view.manifest.themes:
                file = view.folder / THEMES_FOLDER / f"{name}.json"
                try:
                    theme = parse_theme(name, file.read_text(encoding="utf-8", errors="replace"))
                except (OSError, ManifestError) as error:
                    self.failed(view.name, f"Its theme {name} cannot be read: {error}")
                    continue
                found.append(PluginTheme(view.name, theme))
        return found

    async def cli_commands(self) -> list[LoadedCli]:
        """The terminal commands of the plugins that are on (version 16), each plugin loaded with
        its deadline, for `atlas <name>`."""
        loaded: list[LoadedCli] = []
        for context in await self._load(lambda m: bool(m.cli_commands), None, kind="cli"):
            loaded += context.cli
        return loaded

    async def tools(self) -> list[LoadedTool]:
        """The tools of the plugins that are on (SDK version 14), loaded as `parts` loads them."""
        return (await self.parts()).tools

    def command_hooks(self) -> list[PluginCommand]:
        """The command hooks (`hooks/hooks.json`) of the plugins that are on, with where each
        plugin lives (step 16k)."""
        found: list[PluginCommand] = []
        for view in self.all():
            manifest = view.manifest
            if not view.running or manifest is None or not manifest.hooks:
                continue
            data = self._home.plugin_data_folder / manifest.name
            options = self._options(view)
            found += [
                PluginCommand(manifest.name, hook, view.folder, data, options)
                for hook in plugin_hooks(view.folder)
            ]
        return found

    def _options(self, view: PluginView) -> dict[str, str]:
        """A plugin's `userConfig` options by key: a setting's value, or a sensitive one's from
        the vault (empty while not stored)."""
        manifest = view.manifest
        if manifest is None:
            return {}
        values: dict[str, str] = {}
        for key, secret in manifest.options.items():
            if not secret:
                values[key] = view.settings.get(key, "")
                continue
            try:
                values[key] = self._vault.get(secret) or ""
            except SecretsError as error:
                _logger.warning("A plugin's option cannot be read: %s", error)
                values[key] = ""
        return values

    def skill_folders(self) -> dict[str, Path]:
        """The `skills/` folder of each plugin that is on and brings skills, by the plugin's
        name: its skills are named after it (`video:render`), as in Claude Code."""
        return {
            view.name: view.folder / SKILLS_FOLDER
            for view in self.all()
            if view.running and view.manifest is not None and view.manifest.skills
        }

    def command_folders(self) -> dict[str, Path]:
        """The `commands/` folder of each plugin that is on and brings commands (step 16k), by
        the plugin's name: each command is a skill named after it (`/video:render`), as Claude
        Code reads a plugin's commands."""
        return {
            view.name: view.folder / COMMANDS_FOLDER
            for view in self.all()
            if view.running and view.manifest is not None and view.manifest.commands
        }

    def agent_files(self) -> dict[str, list[Path]]:
        """The `agents/` files of each plugin that is on and brings agents (step 16k), by the
        plugin's name: predefined subagents `delegate` starts as `<plugin>:<agent>`."""
        return {
            view.name: command_files(view.folder / AGENTS_FOLDER)
            for view in self.all()
            if view.running and view.manifest is not None and view.manifest.agents
        }

    def monitors(self) -> list[PluginMonitor]:
        """The monitors of the plugins that are on (step 16k), with where each plugin lives."""
        found: list[PluginMonitor] = []
        for view in self.all():
            manifest = view.manifest
            if not view.running or manifest is None:
                continue
            data = self._home.plugin_data_folder / manifest.name
            found += [
                PluginMonitor(
                    manifest.name,
                    monitor.name,
                    monitor.command,
                    monitor.description,
                    monitor.when.removeprefix(ON_SKILL),
                    view.folder,
                    data,
                )
                for monitor in manifest.monitors
            ]
        return found

    def providers(self) -> dict[str, dict[str, Mapping[str, Any]]]:
        """The model providers of the plugins that are on (step 16l), by plugin, each as
        `models.toml` writes one."""
        return {
            view.name: dict(view.manifest.providers)
            for view in self.all()
            if view.running and view.manifest is not None and view.manifest.providers
        }

    def program_folders(self) -> list[Path]:
        """The `bin/` folder of each plugin that is on and brings programs (step 16k), for the
        `PATH` of the commands the model runs, as Claude Code puts them on its Bash tool's."""
        return [
            view.folder / BIN_FOLDER
            for view in self.all()
            if view.running and view.manifest is not None and view.manifest.programs
        ]

    def mcp_config(self) -> dict[str, Any]:
        """The MCP servers of the plugins that are on, as one MCP file declares them, each named
        for its plugin (`plugin_<plugin>_<server>`)."""
        servers: dict[str, Any] = {}
        for view in self.all():
            manifest = view.manifest
            if not view.running or manifest is None or not manifest.mcp_servers:
                continue
            data = self._home.plugin_data_folder / manifest.name
            data.mkdir(parents=True, exist_ok=True)
            declared = plugin_servers(
                manifest, view.folder.as_posix(), data.as_posix(), view.settings
            )
            servers.update(declared["mcpServers"])
        return {"mcpServers": servers}

    async def stop(self, name: str | None = None, *, kind: str | None = None) -> None:
        """Run the stops plugins registered: `name`'s, of what `kind` loaded, or all. A stop that
        raises is logged, and the others still run."""
        for key in [k for k in self._stops if name in (None, k[0]) and kind in (None, k[1])]:
            for stop in self._stops.pop(key):
                try:
                    done = stop()
                    if inspect.isawaitable(done):
                        await done
                except Exception:  # a plugin's own code: its failure is logged
                    _logger.warning(
                        "A plugin's stop failed.", extra={"plugin": key[0]}, exc_info=True
                    )

    async def offered_executors(self) -> list[LoadedExecutor]:
        """The executors of the plugins Atlas ships that are off, to look at before the user turns
        them on (step 16c): the onboarding offers the harnesses installed here. Their code is
        Atlas's own, shipped with it; a plugin of the user's runs only once consented to."""
        loaded: list[LoadedExecutor] = []
        offered = _offered
        for context in await self._load(
            lambda m: bool(m.executors), None, pick=offered, kind="offered"
        ):
            loaded += [
                LoadedExecutor(context.name, n, make) for n, make in context.executors.items()
            ]
        return loaded

    async def _load(
        self,
        wanted: Callable[[Manifest], bool],
        only: set[str] | None,
        *,
        kind: str,
        pick: Callable[[PluginView], bool] = lambda view: view.running,
    ) -> list["_Context"]:
        """The registrations of the plugins `pick` takes (those that are on) that bring what is
        `wanted`."""
        contexts: list[_Context] = []
        for view in load_order(self.all()):
            manifest = view.manifest
            if not pick(view) or manifest is None or not manifest.python:
                continue
            if not wanted(manifest) or (only is not None and view.name not in only):
                continue
            context = _Context(self, view, manifest)
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(context.load, view.folder), self._load_seconds
                )
            except TimeoutError:
                context.abandoned.set()
                self.failed(view.name, f"It took more than {self._load_seconds:.0f} s to load.")
                continue
            except Exception as error:  # noqa: BLE001 - a plugin's own code: any failure is its own
                context.abandoned.set()
                self.failed(view.name, f"It failed to load: {type(error).__name__}: {error}")
                continue
            if context.stops:
                self._stops.setdefault((view.name, kind), []).extend(context.stops)
            contexts.append(context)
        return contexts

    def secret(self, name: str) -> str | None:
        try:
            return self._vault.get(name)
        except SecretsError as error:
            raise PluginError(str(error)) from error

    # -- pairing ----------------------------------------------------------------------------------

    def admit(self, channel: str, user: str, name: str, chat: str = "") -> Admission:
        """Whether `user` may talk through `channel`, writing from `chat`; someone new gets a
        code. The chat they write from is kept, for what reaches them outside any chat."""
        now = self._clock()
        pairings = self.store.pairings(channel)
        known = next((p for p in pairings if p.user == user), None)
        waiting = sum(
            1 for p in pairings if p.user != user and not p.approved and not p.expired(now)
        )
        decided = admission(known, waiting, now)
        if not decided.new:
            if known is not None and chat and known.chat != chat:
                self.store.wrote_from(channel, user, chat)
            return decided
        code = new_code()
        self.store.ask_pairing(Pairing(channel, user, name, code, now, chat=chat))
        _logger.info("Someone asked to talk through a channel.", extra={"channel": channel})
        return Admission(False, code, new=True)

    def allowed(self, channel: str, user: str) -> bool:
        """Whether `user` may talk through `channel`; nobody is given a code."""
        return any(p.user == user and p.approved for p in self.store.pairings(channel))

    def reached(self, channel: str) -> list[str]:
        """The chats of everyone let in on `channel`, each once: where what belongs to no chat is
        shown, such as a permission, which reaches every paired sender."""
        return list(
            dict.fromkeys(p.chat for p in self.store.pairings(channel) if p.approved and p.chat)
        )

    def waiting(self) -> list[Pairing]:
        """The codes still valid, and the people allowed."""
        now = self._clock()
        return [p for p in self.store.pairings() if not p.expired(now)]

    def approve(self, code: str, channel: str | None = None) -> Pairing:
        """Let in whoever holds `code`."""
        typed = normal_code(code)
        now = self._clock()
        for pairing in self.store.pairings(channel):
            if pairing.code == typed and not pairing.approved and not pairing.expired(now):
                self.store.approve(pairing.channel, pairing.user)
                _logger.info(
                    "Someone was allowed on a channel.", extra={"channel": pairing.channel}
                )
                return pairing
        raise PluginError(f"No code {typed} waits. Codes last an hour; ask them to write again.")

    def forget(self, channel: str, user: str) -> None:
        if not self.store.forget(channel, user):
            raise PluginError(f"{channel} does not know {user}.")


class _Context:
    """`PluginContext` for one plugin, granting what its manifest declares."""

    def __init__(self, plugins: Plugins, view: PluginView, manifest: Manifest) -> None:
        self.name = manifest.name
        self.data_folder = plugins._home.plugin_data_folder / manifest.name  # pyright: ignore[reportPrivateUsage]
        self.logger = logging.getLogger(f"atlas.plugins.{manifest.name}")
        self.channels: dict[str, Callable[[], Channel]] = {}
        self.executors: dict[str, Callable[[], Executor]] = {}
        self.tools: list[LoadedTool] = []
        self.hooks: list[PluginCallback] = []
        self.commands: list[LoadedCommand] = []
        self.cli: list[LoadedCli] = []
        self.calls: list[LoadedCall] = []
        self.readers: list[LoadedReader] = []
        self.stops: list[Stop] = []
        #: Set once the load gave up: what the plugin registers after is ignored.
        self.abandoned = threading.Event()
        self._plugins = plugins
        self._manifest = manifest
        self._settings = view.settings

    def load(self, folder: Path) -> None:
        self.data_folder.mkdir(parents=True, exist_ok=True)
        module = import_plugin(folder, self.name)
        register = getattr(module, "register", None)
        if not callable(register):
            raise PluginError("Its __init__.py defines no register(ctx).")
        register(self)

    def secret(self, name: str) -> str | None:
        if all(s.name != name for s in self._manifest.secrets):
            raise PluginError(
                f"{self.name} asked for the secret {name}, which it does not declare."
            )
        return self._plugins.secret(name)

    def setting(self, name: str) -> str:
        if name not in self._settings:
            raise PluginError(
                f"{self.name} asked for the setting {name}, which it does not declare."
            )
        return self._settings[name]

    def register_channel(self, name: str, make: Callable[[], Channel]) -> None:
        if name not in self._manifest.channels:
            raise PluginError(
                f"{self.name} registered the channel {name}, which it does not declare."
            )
        if self.abandoned.is_set():
            return
        self.channels[name] = make

    def register_executor(self, name: str, make: Callable[[], Executor]) -> None:
        if name not in self._manifest.executors:
            raise PluginError(
                f"{self.name} registered the executor {name}, which it does not declare."
            )
        if self.abandoned.is_set():
            return
        self.executors[name] = make

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        run: ToolRun,
        *,
        read_only: bool = False,
    ) -> None:
        if name not in self._manifest.tools:
            raise PluginError(f"{self.name} registered the tool {name}, which it does not declare.")
        if not description.strip():
            raise PluginError(f"{self.name} registered the tool {name} with no description.")
        if parameters.get("type") != "object":
            raise PluginError(
                f"{self.name} registered the tool {name}, whose parameters are not a JSON Schema "
                'object ({"type": "object", ...}).'
            )
        if not callable(run):
            raise PluginError(f"{self.name} registered the tool {name} with nothing to run.")
        if self.abandoned.is_set():
            return
        self.tools = [tool for tool in self.tools if tool.name != name]
        self.tools.append(LoadedTool(self.name, name, description, parameters, run, read_only))

    def register_hook(self, event: str, run: HookRun, *, matcher: str = "") -> None:
        if event not in self._manifest.python_hooks:
            raise PluginError(
                f"{self.name} registered a hook on {event}, which it does not declare under "
                "atlas.hooks."
            )
        if event not in EVENTS:
            raise PluginError(
                f"{self.name} registered a hook on {event}, which Atlas does not fire. The events "
                f"are {', '.join(EVENTS)}."
            )
        if not callable(run):
            raise PluginError(f"{self.name} registered a hook on {event} with nothing to run.")
        if self.abandoned.is_set():
            return
        self.hooks.append(PluginCallback(self.name, event, matcher.strip(), run))

    def on_unload(self, stop: Stop) -> None:
        if not callable(stop):
            raise PluginError(f"{self.name} registered a stop that cannot be called.")
        if self.abandoned.is_set():
            return
        self.stops.append(stop)

    def register_command(
        self, name: str, run: CommandRun, description: str, *, hint: str = ""
    ) -> None:
        if name not in self._manifest.slash_commands:
            raise PluginError(
                f"{self.name} registered the command {name}, which it does not declare under "
                "atlas.commands."
            )
        if not callable(run):
            raise PluginError(f"{self.name} registered the command {name} with nothing to run.")
        if self.abandoned.is_set():
            return
        self.commands = [command for command in self.commands if command.name != name]
        self.commands.append(LoadedCommand(self.name, name, run, description.strip(), hint.strip()))

    def register_cli_command(self, name: str, run: CliRun, description: str) -> None:
        if name not in self._manifest.cli_commands:
            raise PluginError(
                f"{self.name} registered the terminal command {name}, which it does not declare "
                "under atlas.cli."
            )
        if not callable(run):
            raise PluginError(
                f"{self.name} registered the terminal command {name} with nothing to run."
            )
        if self.abandoned.is_set():
            return
        self.cli = [command for command in self.cli if command.name != name]
        self.cli.append(LoadedCli(self.name, name, run, description.strip()))

    def register_call(self, name: str, run: CallRun) -> None:
        if name not in self._manifest.calls:
            raise PluginError(
                f"{self.name} registered the call {name}, which it does not declare under "
                "atlas.calls."
            )
        if not callable(run):
            raise PluginError(f"{self.name} registered the call {name} with nothing to run.")
        if self.abandoned.is_set():
            return
        self.calls = [call for call in self.calls if call.name != name]
        self.calls.append(LoadedCall(self.name, name, run))

    def register_reader(self, kind: str, run: ReadRun) -> None:
        if kind not in self._manifest.readers:
            raise PluginError(
                f"{self.name} registered a reader for {kind}, which it does not declare under "
                "atlas.readers."
            )
        if not callable(run):
            raise PluginError(f"{self.name} registered a reader for {kind} with nothing to run.")
        if self.abandoned.is_set():
            return
        self.readers = [reader for reader in self.readers if reader.kind != kind]
        self.readers.append(LoadedReader(self.name, kind, run))

    @property
    def llm(self) -> PluginLlm:
        return _Llm(self._plugins, self.name, self._manifest.llm)

    async def notify(self, conversation: str, text: str) -> bool:
        if not self._manifest.notify:
            raise PluginError(
                f"{self.name} wrote to a conversation, which it does not declare (atlas.notify)."
            )
        if not text.strip():
            return False
        return await self._plugins.host.notify(self.name, conversation, text)


class _Llm:
    """`PluginContext.llm` for one plugin: the host's completion, when the manifest says so."""

    def __init__(self, plugins: Plugins, name: str, declared: bool) -> None:
        self._plugins = plugins
        self._name = name
        self._declared = declared

    async def complete(
        self,
        prompt: str | Sequence[LlmMessage],
        *,
        system: str = "",
        max_tokens: int | None = None,
        json_schema: Mapping[str, Any] | None = None,
    ) -> LlmResult:
        if not self._declared:
            raise LlmError(
                f"{self._name} called the user's model, which it does not declare (atlas.llm)."
            )
        return await self._plugins.host.complete(
            self._name, prompt, system=system, max_tokens=max_tokens, json_schema=json_schema
        )


def _offered(view: PluginView) -> bool:
    """A plugin Atlas ships, off, that nothing keeps from being turned on."""
    return view.bundled and not view.running and view.problem is None


def _problem(found: Found) -> str | None:
    if found.problem is not None:
        return found.problem
    manifest = found.manifest
    if manifest is not None and manifest.unsupported:
        return f"It brings {', '.join(manifest.unsupported)}, which Atlas does not load yet."
    if manifest is not None and manifest.api > API_VERSION:
        return f"It was written for a newer Atlas (SDK {manifest.api}); update Atlas."
    return None


# -- reloading and dependencies (step 16n) --------------------------------------------------------


def _stamp_files(folder: Path, digest: Any) -> None:
    """Add each file of `folder` to `digest`: its path, its size and when it last changed."""
    seen = 0
    for root, folders, files in os.walk(folder):
        folders[:] = sorted(f for f in folders if f not in UNWATCHED)
        for name in sorted(files):
            path = Path(root, name)
            try:
                info = path.stat()
            except OSError:
                continue
            line = f"{path.relative_to(folder)}|{info.st_size}|{info.st_mtime_ns}\n"
            digest.update(line.encode())
            seen += 1
            if seen >= WATCHED_FILES:
                return


def needs(views: Sequence[PluginView], name: str) -> list[str]:
    """The plugins that are on and need `name`."""
    return [
        view.name
        for view in views
        if view.enabled
        and view.manifest is not None
        and any(dependency.name == name for dependency in view.manifest.dependencies)
    ]


def _unmet(manifest: Manifest, views: Mapping[str, PluginView]) -> str | None:
    """Why `manifest`'s dependencies do not hold, the first that does not; `None` when they do."""
    for dependency in manifest.dependencies:
        found = views.get(dependency.name)
        if found is None or (found.manifest is None and found.problem is not None):
            return f"It needs the plugin {dependency.name}, which is not installed."
        installed = found.manifest.version if found.manifest is not None else ""
        if dependency.version:
            try:
                held = satisfies(installed, dependency.version)
            except VersionError:
                held = False
            if not held:
                return (
                    f"It needs {dependency.name} {dependency.version}; the one installed is "
                    f"{installed or 'without a version'}."
                )
        if not found.running:
            return f"It needs the plugin {dependency.name}, which is off or failed: turn it on."
    return None


def _with_dependencies(views: list[PluginView]) -> list[PluginView]:
    """`views`, a plugin that runs but whose dependencies do not marked with why: until none
    changes, so one stopped stops those that need it, and those that need them."""
    by_name = {view.name: view for view in views}
    changed = True
    while changed:
        changed = False
        for index, view in enumerate(views):
            if not view.running or view.manifest is None or not view.manifest.dependencies:
                continue
            unmet = _unmet(view.manifest, by_name)
            if unmet is not None:
                views[index] = by_name[view.name] = replace(view, problem=unmet)
                changed = True
    return views


def load_order(views: Sequence[PluginView]) -> list[PluginView]:
    """`views` with each plugin after those it needs, otherwise in their order; plugins that need
    each other in a circle keep their order."""
    names = {view.name for view in views}
    left = list(views)
    ordered: list[PluginView] = []
    placed: set[str] = set()
    while left:
        ready = next(
            (
                view
                for view in left
                if view.manifest is None
                or all(d.name in placed or d.name not in names for d in view.manifest.dependencies)
            ),
            left[0],
        )
        left.remove(ready)
        ordered.append(ready)
        placed.add(ready.name)
    return ordered
