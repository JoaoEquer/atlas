"""The gateway: the clients' connections, their methods, the conversations' events, and the
questions the agent asks them.

Decided on 10/10/2026 (docs/ARCHITECTURE.md, Server):

- **A client attaches to a conversation** by opening, creating or sending to it, and from then on
  gets its events and its server requests. Several clients may attach to one conversation (the
  desktop and the phone).
- **Events are numbered per conversation** and the recent ones kept (`atlas.server.replay`), so a
  client that comes back asks for what it missed (`conversation.events`).
- **A server request** goes to every client attached to its conversation; the first answer wins,
  and every client then gets `request.cancel` to take the card down. With no client attached, it
  is undelivered at once, instead of waiting out its deadline. When the turn stops waiting (it was
  stopped, or the approval's deadline passed) the request is withdrawn. An answer to a request no
  longer open is dropped: the wait already ended. A client that comes back gets the requests still
  open again (`open_requests`).
- **Each connection writes through its own queue**, so a turn never waits on a slow client; a
  client that cannot take a frame in 30 seconds is closed, and its reconnect starts.
- **Every minute** the curator is asked whether it is due.
- **The memory proposals** (step 9b) are listed, approved and discarded here, and every client
  hears `proposals.changed` when the list changes, so each keeps its count right. They belong to no
  conversation, so a client that comes back reads them again.
"""

import asyncio
import base64
import binascii
import dataclasses
import json
import logging
import secrets
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

from atlas import __version__
from atlas.agent import (
    Compacted,
    InterimText,
    Steered,
    ToolFinished,
    ToolProgress,
    ToolStarted,
    TurnEnded,
    current_call_id,
    is_notice_text,
    is_skill_text,
    is_terminal_text,
)
from atlas.assistant import (
    IDENTITY,
    AccountList,
    Assistant,
    AssistantPluginHost,
    ConversationEvent,
    Decides,
    ExecutorUsage,
    FileSent,
    InstanceActivity,
    InstanceChanged,
    PreviewAsked,
    Reviewed,
    RouteView,
    RoutineRunner,
    SubagentChanged,
    Titled,
    TurnStarted,
    TurnStopped,
    WorkbenchEvent,
    account_name,
)
from atlas.bots import Bot, BotError
from atlas.browser import ENGINE
from atlas.conversations import LIMITS as ATTACHMENT_LIMITS
from atlas.conversations import (
    AttachmentError,
    Attachments,
    ConversationInfo,
    ConversationNotFoundError,
    Cursor,
    StoredAttachment,
    StoredMessage,
)
from atlas.home import (
    Database,
    Home,
    HomeError,
    PictureError,
    Schema,
    Settings,
    SettingsError,
    TrustSettings,
    check_backup,
    create_home,
    edit_settings,
    editable_values,
    format_utc,
    home_name,
    home_path,
    home_root,
    import_backup,
    list_homes,
    read_picture,
    suggest_home_name,
    utc_now,
    write_backup,
    write_picture,
)
from atlas.hooks import Hooks
from atlas.installation import (
    HandedOff,
    HostChannel,
    InstallationError,
    UpdateStatus,
    compose_apply,
    compose_check,
    compose_log,
    compose_progress,
)
from atlas.jobs import JobsError, RouteTarget, Routine, Run, RunStatus, Scheduler
from atlas.marketplace import Kind as MarketKind
from atlas.marketplace import Marketplace, MarketplaceError, MarketplaceStore
from atlas.marketplace import Page as MarketPage
from atlas.marketplace import Row as MarketRow
from atlas.memory import FILES as MEMORY_FILES
from atlas.memory import Action, OpenProposal, Operation, Target, apply, size
from atlas.models import (
    Attachment,
    AttachmentKind,
    Catalog,
    CatalogError,
    ModelChoice,
    Models,
    ModelsError,
    ReasoningDelta,
    TextDelta,
    UserMessage,
    default_effort,
    with_providers,
)
from atlas.plugins import Answers as ExecutorAnswers
from atlas.plugins import (
    Detection,
    Executor,
    ExecutorError,
    LoadedCall,
    LoadedExecutor,
    Pairing,
    PluginError,
    Plugins,
    PluginView,
)
from atlas.plugins import Permission as ExecutorPermission
from atlas.plugins import Question as ExecutorQuestion
from atlas.plugins import Verdict as ExecutorVerdict
from atlas.secrets import SecretsError, Vault
from atlas.server.channels import ChannelRunner
from atlas.server.connection import Connection, Peer
from atlas.server.previews import read_preview
from atlas.server.protocol import (
    ATTACHMENT_PART_BYTES,
    EVENTS,
    FORBIDDEN,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    METHODS,
    NOT_FOUND,
    PARSE_ERROR,
    SERVER_REQUESTS,
    AccountRow,
    AccountsChanged,
    AccountSwitchRow,
    ApprovalParams,
    ApprovalResult,
    AskParams,
    AskResult,
    AttachmentAdded,
    AttachmentAddParams,
    AttachmentLimits,
    AttachmentRow,
    AutoMode,
    AutoModeParams,
    BackupDownload,
    BackupInfo,
    BackupPart,
    BackupPartParams,
    BackupRef,
    BackupWritten,
    BotDefinition,
    BotList,
    BotOptions,
    BotRef,
    BotRow,
    BotsChanged,
    CallRow,
    CommandCheck,
    CommandList,
    CommandOutput,
    CommandParams,
    CommandRef,
    CommandRow,
    CommandStopped,
    ConnectStatus,
    ConversationCommandsParams,
    ConversationList,
    ConversationModel,
    ConversationModelParams,
    ConversationRef,
    ConversationRow,
    ConversationsChanged,
    Created,
    Empty,
    Events,
    EventsParams,
    ExecutorAccountParams,
    ExecutorAccountRef,
    ExecutorAccounts,
    ExecutorAddAccountParams,
    ExecutorApprovalParams,
    ExecutorApprovalResult,
    ExecutorChoiceRow,
    ExecutorCommandsParams,
    ExecutorConnector,
    ExecutorConnectors,
    ExecutorFailoverParams,
    ExecutorFolderAccountParams,
    ExecutorList,
    ExecutorModel,
    ExecutorModels,
    ExecutorQuestionParams,
    ExecutorQuestionResult,
    ExecutorQuestionRow,
    ExecutorRef,
    ExecutorRow,
    ExecutorUsageRow,
    HomeBackupParams,
    HomeCreateParams,
    HomeImportParams,
    HomeList,
    HomePicture,
    HomeRow,
    InstanceActivityPayload,
    InstanceRef,
    InstanceRow,
    Interim,
    Interrupted,
    KeyProvider,
    KeyProviders,
    ListParams,
    MarketplaceChanged,
    MarketplaceDone,
    MarketplaceList,
    MarketplaceListParams,
    MarketplaceNeed,
    MarketplacePage,
    MarketplaceRef,
    MarketplaceRow,
    MarketplaceSecretParams,
    MarketplaceSecretRow,
    MarketplaceUpdateParams,
    MemoryEdit,
    MemoryEntries,
    MemoryEntriesParams,
    MessageComplete,
    MessageRow,
    ModelOption,
    ModelOptions,
    Opened,
    OpenRequest,
    PairingApproveParams,
    PairingForgetParams,
    PairingList,
    PairingRow,
    PluginCallParams,
    PluginCallResult,
    PluginEnableParams,
    PluginList,
    PluginRef,
    PluginRow,
    PluginsChanged,
    PluginScreenRow,
    PluginScreens,
    PluginSecretParams,
    PluginSecretRow,
    PluginSettingParams,
    PluginSettingRow,
    PluginThemeRow,
    PluginThemes,
    PreviewPayload,
    PreviewRead,
    PreviewReadParams,
    ProposalApproved,
    ProposalChange,
    ProposalCount,
    ProposalDiscarded,
    ProposalList,
    ProposalRef,
    ProposalRow,
    ProtocolError,
    ProviderConnected,
    ProviderConnectParams,
    ProviderOption,
    QuestionRow,
    Ready,
    RequestCancel,
    ReviewSummary,
    RouteStopRow,
    RoutineFinished,
    RoutineList,
    RoutineRef,
    RoutineRemoved,
    RoutineRow,
    RoutinesChanged,
    RunList,
    RunRow,
    Said,
    SecretParams,
    SecretResult,
    SendParams,
    SendResult,
    SessionDeleteParams,
    SessionFolderList,
    SessionFolderRow,
    SessionList,
    SessionListParams,
    SessionRead,
    SessionReadParams,
    SessionRouteRow,
    SessionRow,
    SettingRow,
    SettingsList,
    SettingsWrite,
    SlashCommandList,
    SlashCommandRow,
    Soul,
    SoulText,
    StatusUpdate,
    SubagentControl,
    SubagentDecidesParams,
    SubagentList,
    SubagentRef,
    SubagentRow,
    SubagentSession,
    SubagentSteerParams,
    TerminalAvailable,
    TerminalExit,
    TerminalInputParams,
    TerminalOpened,
    TerminalOpenParams,
    TerminalOutput,
    TerminalParams,
    TerminalRef,
    TerminalResizeParams,
    TitleChanged,
    ToolComplete,
    ToolProgressPayload,
    ToolStart,
    TranscriptRow,
    UpdateApplied,
    UpdateApplyParams,
    UpdateEnded,
    UpdateInfo,
    UpdateLog,
    UpdateProgress,
    UsageWindowRow,
    UserSaid,
    WorkbenchAccountParams,
    WorkbenchFolderParams,
    WorkbenchList,
    WorkbenchModelParams,
    WorkbenchPrompted,
    WorkbenchPromptParams,
    WorkbenchRouteParams,
    WorkbenchStartParams,
    WorkbenchSwitchParams,
    WorkbenchUseAccountParams,
    dump,
    read,
)
from atlas.server.replay import Replay
from atlas.skills import SkillLibrary, SkillUsage
from atlas.terminals import TerminalError, Terminals
from atlas.tools import (
    SECRET_REQUEST_SECONDS,
    Answers,
    Asker,
    BackgroundCommand,
    Outcome,
    Question,
    SecretAnswer,
    SecretAsker,
    SecretOutcome,
    SecretRequest,
)
from atlas.trust import (
    ApprovalAnswer,
    ApprovalRequest,
    Approver,
    Choice,
    detect_dangerous_command,
    detect_hardline_command,
)
from atlas.trust import Outcome as ApprovalOutcome

#: How long ask_user's questions wait for an answer.
ASK_SECONDS = 300
#: How often the curator is asked whether it is due.
HOUSEKEEPING_SECONDS = 60
#: How often the plugins' files are looked at, to reload one saved (step 16n).
PLUGIN_WATCH_SECONDS = 1.0
#: How long an executor plugin may take to say whether its harness is installed.
DETECT_SECONDS = 60.0
#: The key of the workbench's requests: they belong to no conversation, and reach every screen.
_WORKBENCH = ""
#: How long the start waits for the executors before going on without them.
EXECUTORS_WAIT_SECONDS = 5.0
#: The most conversations a list returns: a client reads again every row its list holds in one
#: call, pages it read as it scrolled included (step 9e).
LIST_MOST = 1000
#: How much of a bot's last message its row shows.
PREVIEW_CHARS = 120
#: The conversations' table, as a backup's manifest counts it for the import dialog.
_THREADS_TABLE = "conversations_threads"
#: How long after the host's update ended a server that starts still tells its clients.
UPDATE_NEWS_SECONDS = 600.0

_logger = logging.getLogger("atlas.server")


class _Wait(StrEnum):
    ANSWERED = "answered"
    UNDELIVERED = "undelivered"
    TIMED_OUT = "timed_out"


@dataclass
class _Pending:
    """A server request waiting for its answer."""

    id: str
    conversation_id: str
    method: str
    params: dict[str, Any]
    answer: "asyncio.Future[object | None]"


type _Handler = Callable[[Connection, Any], Awaitable[object]]


class Gateway:
    """The clients of one Atlas and the assistant they talk to."""

    def __init__(
        self,
        home: Home,
        settings: Settings,
        models: Models,
        vault: Vault,
        *,
        clock: Callable[[], datetime] = utc_now,
        housekeeping_seconds: float = HOUSEKEEPING_SECONDS,
        plugin_watch_seconds: float = PLUGIN_WATCH_SECONDS,
        ask_seconds: float = ASK_SECONDS,
        executors: Mapping[str, Executor] | None = None,
        chrome: Sequence[str] | None = None,
        browser_engine: Sequence[str] = ENGINE,
        schemas: tuple[Schema, ...],
        terminals: Terminals | None = None,
        environ: Mapping[str, str] | None = None,
        host_updater: HostChannel | None = None,
    ) -> None:
        self.replay = Replay()
        #: The way to a Compose host's updater, for the update button of a remote app; `None` on
        #: a native installation, whose app updates it on its own machine.
        self._host_updater = host_updater
        #: How the host's last update ended, when it ended moments before this server started:
        #: each client that connects is told (`update.ended`).
        self._update_ended: UpdateEnded | None = None
        self._update_news: asyncio.Task[None] | None = None
        #: The environment the settings were read from: its `ATLAS_*` variables win over
        #: config.toml, so the settings screen shows them locked (step 15b).
        self._environ = dict(environ or {})
        #: The server terminal's shells (step 15), each its connection's.
        self.terminals = terminals or Terminals()
        #: Backups made for a client to take down (step 15): by id, the connection's, and where.
        self._downloads: dict[str, tuple[Connection, Path]] = {}
        self._home = home
        #: The database's migrations: a backup imported here is checked against them and brought
        #: forward by them.
        self._schemas = schemas
        self._clock = clock
        self._models = models
        self._housekeeping_seconds = housekeeping_seconds
        self._plugin_watch_seconds = plugin_watch_seconds
        #: The plugins' stamps when they were last loaded (step 16n), by name.
        self._plugin_stamps: dict[str, str] | None = None
        self._plugin_watch: asyncio.Task[None] | None = None
        self._ask_seconds = ask_seconds
        self._connections: set[Connection] = set()
        self._requests: dict[str, _Pending] = {}
        self._housekeeping: asyncio.Task[None] | None = None
        #: The server's event loop, once it runs: what happens in a worker thread is told to
        #: the clients from here.
        self._loop: asyncio.AbstractEventLoop | None = None
        #: Kept for the secrets tool handler (step 16d): the gateway writes a pasted key to
        #: the vault itself; the Assistant does not need its own write path.
        self._vault = vault
        self.assistant = Assistant(
            home,
            settings,
            models,
            vault,
            listener=self._publish,
            on_workbench=self._workbench_event,
            asker=self._asker,
            approver=self._approver,
            secret_asker=self._secret_asker,
            desk=_Desk(self),
            executors=executors,
            chrome=chrome,
            browser_engine=browser_engine,
            clock=clock,
        )
        self.plugins = Plugins(home, Database(home.database_file), vault, clock=clock)
        self.plugins.host = AssistantPluginHost(self.assistant)
        #: The marketplace (step 16s), installing into this home.
        self.marketplace = Marketplace(
            home,
            MarketplaceStore(home.marketplace_folder, settings.marketplace),
            self.plugins,
            SkillLibrary(home.skills_folder, SkillUsage(Database(home.database_file))),
            stored=self._stored_names,
            clock=clock,
        )
        #: `mcp.json` as last seen (step 16s): read again once it changes, by the marketplace or
        #: by hand.
        self._mcp_stamp: tuple[int, int] | None = None
        #: The model catalog before any plugin added a provider (step 16l).
        self._base_catalog: Catalog | None = None
        #: The calls the plugins' screens may make to their Python (step 16m).
        self._calls: dict[tuple[str, str], LoadedCall] = {}
        self.channels = ChannelRunner(self, self.plugins)
        self._channels_start: asyncio.Task[None] | None = None
        self._plugins_start: asyncio.Task[None] | None = None
        self._executors_start: asyncio.Task[None] | None = None
        #: Executors given here stay; otherwise they come from the plugins that are on.
        self._fixed_executors = executors is not None
        #: What each executor plugin's detection found (step 16c), and which plugins run one.
        self.detections: dict[str, Detection] = {}
        #: Which plugin drives each executor found, and how the screen calls it.
        self._executor_names: dict[str, tuple[str, str]] = {}
        #: Each row of an executor on another account (step 16h): its executor, the account's
        #: id and nickname.
        self._account_rows: dict[str, tuple[str, str, str]] = {}
        #: The executors found of the plugins Atlas ships that are off: the onboarding offers them.
        self._offered: set[str] = set()
        self._executor_plugins: set[str] = set()
        self._executors_seen: dict[str, _ExecutorState] | None = None
        self._executors_lock = asyncio.Lock()
        self._plugins_lock = asyncio.Lock()
        self._handlers: dict[str, _Handler] = {
            "ping": self._ping,
            "plugin.list": self._plugin_list,
            "plugin.enable": self._plugin_enable,
            "plugin.disable": self._plugin_disable,
            "plugin.secret": self._plugin_secret,
            "plugin.setting": self._plugin_setting,
            "plugin.reload": self._plugin_reload,
            "plugin.screens": self._plugin_screens,
            "plugin.themes": self._plugin_themes,
            "plugin.call": self._plugin_call_python,
            "marketplace.list": self._marketplace_list,
            "marketplace.page": self._marketplace_page,
            "marketplace.install": self._marketplace_install,
            "marketplace.update": self._marketplace_update,
            "marketplace.remove": self._marketplace_remove,
            "marketplace.secret": self._marketplace_secret,
            "pairing.list": self._pairing_list,
            "pairing.approve": self._pairing_approve,
            "pairing.forget": self._pairing_forget,
            "conversation.list": self._list,
            "conversation.create": self._create,
            "conversation.open": self._open,
            "conversation.events": self._events,
            "conversation.leave": self._leave,
            "conversation.send": self._send,
            "attachment.add": self._attachment_add,
            "attachment.limits": self._attachment_limits,
            "conversation.interrupt": self._interrupt,
            "conversation.terminal": self._terminal,
            "conversation.commands": self._conversation_commands,
            "trust.check": self._check_command,
            "preview.read": self._preview_read,
            "terminal.available": self._terminal_available,
            "terminal.open": self._terminal_open,
            "terminal.input": self._terminal_input,
            "terminal.resize": self._terminal_resize,
            "terminal.close": self._terminal_close,
            "conversation.auto_mode": self._auto_mode,
            "conversation.model": self._conversation_model,
            "model.options": self._model_options,
            "provider.list": self._providers,
            "provider.connect": self._provider_connect,
            "memory.proposals": self._proposals,
            "memory.approve": self._approve,
            "memory.discard": self._discard,
            "soul.read": self._soul,
            "soul.write": self._soul_write,
            "settings.read": self._settings_read,
            "settings.write": self._settings_write,
            "memory.entries": self._memory_entries,
            "memory.edit": self._memory_edit,
            "home.picture": self._home_picture,
            "home.set_picture": self._home_set_picture,
            "update.check": self._update_check,
            "update.apply": self._update_apply,
            "update.status": self._update_status,
            "update.log": self._update_log,
            "home.list": self._homes,
            "home.create": self._home_create,
            "home.backup": self._home_backup,
            "home.backup_download": self._home_backup_download,
            "home.backup_part": self._home_backup_part,
            "home.inspect": self._home_inspect,
            "home.import": self._home_import,
            "bot.list": self._bots,
            "bot.open": self._bot_open,
            "bot.create": self._bot_create,
            "bot.update": self._bot_update,
            "bot.describe": self._bot_describe,
            "bot.delete": self._bot_delete,
            "bot.options": self._bot_options,
            "routine.list": self._routines,
            "routine.runs": self._routine_runs,
            "routine.run": self._routine_run,
            "routine.pause": self._routine_pause,
            "routine.resume": self._routine_resume,
            "routine.remove": self._routine_remove,
            "subagent.list": self._subagents,
            "subagent.steer": self._subagent_steer,
            "subagent.interrupt": self._subagent_interrupt,
            "subagent.decides": self._subagent_decides,
            "command.list": self._commands,
            "command.output": self._command_output,
            "command.stop": self._command_stop,
            "workbench.list": self._workbench_list,
            "workbench.start": self._workbench_start,
            "workbench.prompt": self._workbench_prompt,
            "workbench.auto": self._workbench_auto,
            "workbench.model": self._workbench_model,
            "workbench.atlas_answers": self._workbench_atlas_answers,
            "workbench.duplicate": self._workbench_duplicate,
            "workbench.folder": self._workbench_folder,
            "workbench.account": self._workbench_account,
            "workbench.use_account": self._workbench_use_account,
            "workbench.route": self._workbench_route,
            "workbench.set_route": self._workbench_set_route,
            "workbench.account_switch": self._workbench_account_switch,
            "workbench.stop": self._workbench_stop,
            "executor.detect": self._executor_detect,
            "executor.folders": self._executor_folders,
            "executor.sessions": self._executor_sessions,
            "executor.models": self._executor_models,
            "executor.connectors": self._executor_connectors,
            "executor.usage": self._executor_usage,
            "executor.commands": self._executor_commands,
            "executor.account": self._executor_account,
            "executor.accounts": self._executor_accounts,
            "executor.add_account": self._executor_add_account,
            "executor.remove_account": self._executor_remove_account,
            "executor.folder_account": self._executor_folder_account,
            "executor.failover": self._executor_failover,
            "executor.delete_session": self._executor_delete_session,
            "executor.session": self._executor_session,
            "subagent.session": self._subagent_session,
        }
        self.scheduler = Scheduler(
            self.assistant.routines,
            RoutineRunner(self.assistant),
            clock=clock,
            on_finished=self._routine_finished,
        )

    @property
    def handled(self) -> frozenset[str]:
        """The methods this gateway serves; the protocol must declare exactly these."""
        return frozenset(self._handlers)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.assistant.on_bots_changed = self._bots_changed
        self.assistant.start()
        abandoned = self.scheduler.start()
        if abandoned:
            _logger.warning("Runs a previous Atlas left running are now unknown: %d.", abandoned)
        self._housekeeping = asyncio.create_task(self._keep_house())
        # In the background: a plugin may take its deadline to load, and the clients need not wait.
        self._channels_start = asyncio.create_task(self.channels.reconcile())
        self._plugins_start = asyncio.create_task(self.load_plugins())
        self._plugin_watch = asyncio.create_task(self._watch_plugins())
        # The executors are waited for a moment, so the first conversation already sees them;
        # one that takes longer is found in the background.
        self._executors_start = asyncio.create_task(self.load_executors())
        if self._host_updater is not None:
            self._update_news = asyncio.create_task(self._read_update_news())
        await asyncio.wait({self._executors_start}, timeout=EXECUTORS_WAIT_SECONDS)

    async def load_plugins(self) -> None:
        """Hand the assistant what the plugins that are on bring (step 16j): their tools, their
        skills folders and their MCP servers. At start, and whenever a plugin is turned on, off or
        set; a plugin whose Python fails to load is marked, and the others still come. Their hooks
        too (step 16k): the commands of their `hooks/hooks.json` and what their Python
        registered."""
        async with self._plugins_lock:
            # Taken before they load: a file saved while they do is seen after, and loads again.
            self._plugin_stamps = await asyncio.to_thread(self.plugins.stamps)
            parts = await self.plugins.parts()
            skills = await asyncio.to_thread(self.plugins.skill_folders)
            commands_folders = await asyncio.to_thread(self.plugins.command_folders)
            programs = await asyncio.to_thread(self.plugins.program_folders)
            agents = await asyncio.to_thread(self.plugins.agent_files)
            monitors = await asyncio.to_thread(self.plugins.monitors)
            servers = await asyncio.to_thread(self.plugins.mcp_config)
            commands = await asyncio.to_thread(self.plugins.command_hooks)
            hooks = Hooks(commands, parts.hooks)
            await self.assistant.set_plugins(
                parts.tools,
                skills,
                servers,
                hooks,
                commands=commands_folders,
                programs=programs,
                agents=agents,
                monitors=monitors,
                slash=parts.commands,
                readers=parts.readers,
            )
            self._calls = {(call.plugin, call.name): call for call in parts.calls}
            self._plugin_providers(await asyncio.to_thread(self.plugins.providers))

    async def reload_plugins(self, names: Iterable[str] | None = None) -> None:
        """Load the plugins again from their files (step 16n): those named, whose files changed,
        or all. A plugin that failed is tried again, its author may have fixed it; its channels
        start again, and the screens are told, so the app loads a changed screen again."""
        changed = (
            {view.name for view in await asyncio.to_thread(self.plugins.all)}
            if names is None
            else set(names)
        )
        for name in changed:
            self.plugins.clear_failure(name)
        await self.channels.reconcile(restart=changed)
        await self.load_executors()
        await self.load_plugins()
        self.plugins_changed()
        _logger.info("Plugins were loaded again: %s.", ", ".join(sorted(changed)))

    async def _watch_plugins(self) -> None:
        """Reload a plugin whose files were saved, or which was turned on, off or set from the
        terminal (step 16n). The whole plugin is watched, its Python too: its author sees a
        change without asking.
        A save writes in bursts, so a change is loaded once a look finds nothing newer."""
        while True:
            await asyncio.sleep(self._plugin_watch_seconds)
            try:
                await self._watch_mcp_file()
                if self._plugin_stamps is None:
                    continue
                now = await asyncio.to_thread(self.plugins.stamps)
                if now == self._plugin_stamps:
                    continue
                while True:
                    await asyncio.sleep(self._plugin_watch_seconds)
                    settled = await asyncio.to_thread(self.plugins.stamps)
                    if settled == now:
                        break
                    now = settled
                before = self._plugin_stamps
                changed = {n for n in now.keys() | before.keys() if now.get(n) != before.get(n)}
                await self.reload_plugins(changed)
            except Exception:  # the server's edge: the watch must not stop, logged
                _logger.exception("The plugins could not be loaded again.")

    def _plugin_providers(self, tables: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> None:
        """The model providers of the plugins that are on (step 16l), over the catalog Atlas
        started with: a plugin turned off takes its own away. One the catalog refuses marks its
        plugin, and the others still come."""
        models = self.assistant.models
        if self._base_catalog is None:
            self._base_catalog = models.catalog
        catalog = self._base_catalog
        for plugin, providers in tables.items():
            try:
                catalog = with_providers(catalog, providers, f"the plugin {plugin}")
            except CatalogError as error:
                self.plugins.failed(plugin, str(error))
        models.catalog = catalog

    async def load_executors(self) -> None:
        """Find the executors of the plugins that are on, each by its plugin's detection, and
        hand the assistant those installed in a version their plugin drives (step 16c): Atlas
        looks every time it starts, when a plugin is turned on, off or set, and when the screen
        asks (`executor.detect`). The executors of the plugins Atlas ships that are off are
        looked at too, for the onboarding to offer."""
        if self._fixed_executors:
            # Given here (a test, a proof): they stay as given, and are looked at all the same.
            async with self._executors_lock:
                for name in self.assistant.executors():
                    given = self.assistant.executor(name)
                    if given is not None:
                        self.detections[name] = await self._look(given)
                        self._executor_names[name] = ("", given.title)
                        self.detections.update(await self._account_detections(name, given))
            return
        async with self._executors_lock:
            found: dict[str, Executor] = {}
            detections: dict[str, Detection] = {}
            plugins: set[str] = set()
            offered: set[str] = set()
            self._executors_seen = await asyncio.to_thread(_executor_states, self.plugins)
            for loaded in await self.plugins.executors():
                executor, detection = await self._detect(loaded)
                plugins.add(loaded.plugin)
                detections[loaded.name] = detection
                if executor is not None and detection.installed and detection.supported:
                    # Found as before: the executor already made stays, with what it read since
                    # (its models), so a look from the screen starts no harness for nothing.
                    kept = self.assistant.executor(loaded.name)
                    same = kept is not None and self.detections.get(loaded.name) == detection
                    found[loaded.name] = kept if same and kept is not None else executor
            for loaded in await self.plugins.offered_executors():
                if loaded.name not in detections:
                    detections[loaded.name] = (await self._detect(loaded))[1]
                    offered.add(loaded.name)
            before = (self.detections, self._executor_plugins, self._offered)
            self.assistant.set_executors(found)
            for name, executor in found.items():
                detections.update(await self._account_detections(name, executor))
            self.detections, self._executor_plugins = detections, plugins
            self._offered = offered
        # Only a change is told: each telling reads the plugins and the executors again on every
        # screen, and the executors tab now looks whenever it shows.
        if before != (detections, plugins, offered):
            self.plugins_changed()

    async def _account_detections(self, name: str, executor: Executor) -> dict[str, Detection]:
        """The detection of `name`'s executor on each of its other accounts (step 16h), each one
        row more, named as the screens name it: its login is that account's folder's."""
        if not executor.capabilities().account_folders:
            return {}
        plugin = self._executor_names.get(name, ("", executor.title))[0]
        kept = await asyncio.to_thread(self.assistant.account_store.accounts, name)
        found: dict[str, Detection] = {}
        for account in kept:
            named = account_name(name, account.id)
            on = self.assistant.executor(named)
            if on is not None:
                found[named] = await self._look(on)
                self._executor_names[named] = (plugin, f"{executor.title} · {account.name}")
                self._account_rows[named] = (name, account.id, account.name)
        return found

    async def _detect(self, loaded: LoadedExecutor) -> tuple[Executor | None, Detection]:
        """An executor made by its plugin and looked at; `None` and why when the plugin makes
        none (a setting it refuses). A failed look is a finding, never a plugin turned off:
        before, a look that took too long or raised marked its plugin failed, and its harness
        left the workbench and the onboarding until the plugin was turned on again."""
        try:
            executor = loaded.make()
        except Exception as error:  # noqa: BLE001 - a plugin's own code: its refusal is told
            _logger.warning("An executor could not be made: %s", error)
            title = self._executor_names.get(loaded.name, ("", loaded.name))[1]
            self._executor_names[loaded.name] = (loaded.plugin, title)
            said = str(error) if isinstance(error, ExecutorError) else repr(error)
            return None, Detection(False, problem=said)
        self._executor_names[loaded.name] = (loaded.plugin, executor.title)
        return executor, await self._look(executor)

    async def _look(self, executor: Executor) -> Detection:
        """The plugin's detection, with its deadline; one that fails says why."""
        try:
            async with asyncio.timeout(DETECT_SECONDS):
                return await executor.detect()
        except TimeoutError:
            return Detection(
                False,
                problem=f"{executor.title} took more than {DETECT_SECONDS:.0f} s to say whether "
                "it is installed.",
            )
        except Exception as error:  # a plugin's own code: its failure is told, and logged
            _logger.exception("An executor could not be looked at.")
            return Detection(False, problem=f"Its detection failed: {error!r}")

    async def close(self) -> None:
        for task in (self._housekeeping, self._plugin_watch, self._update_news):
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        for starting in (self._channels_start, self._executors_start, self._plugins_start):
            if starting is not None:
                await asyncio.gather(starting, return_exceptions=True)
        await self.plugins.stop()
        await asyncio.to_thread(self.terminals.close_all)
        await self.channels.close()
        await self.scheduler.close()
        await self.assistant.close()
        await self._models.close()

    async def serve(self, peer: Peer) -> None:
        """Talk with one client until its socket closes."""
        connection = Connection(peer)
        self._connections.add(connection)
        writer = asyncio.create_task(connection.write())
        connection.put(self._event_frame("server.ready", Ready(__version__, self.replay.epoch)))
        if self._update_ended is not None:
            connection.put(self._event_frame("update.ended", self._update_ended))
        try:
            while not connection.gone and (text := await peer.receive()) is not None:
                await self._receive(connection, text)
        finally:
            self._connections.discard(connection)
            # Its terminals and the backups it did not take go with it; stopping a shell may
            # wait a moment.
            await asyncio.to_thread(self.terminals.close_owner, connection)
            self._drop_downloads(connection)
            connection.stop()
            await writer

    # -- frames from a client ---------------------------------------------------------------------

    async def _receive(self, connection: Connection, text: str) -> None:
        try:
            frame: object = json.loads(text)
        except ValueError:
            connection.put(_error(None, PARSE_ERROR, "The frame is not JSON."))
            return
        if not isinstance(frame, dict):
            connection.put(_error(None, INVALID_REQUEST, "A frame is a JSON object."))
            return
        fields: dict[str, Any] = frame  # pyright: ignore[reportUnknownVariableType]
        if "method" not in fields:
            if "id" in fields and ("result" in fields or "error" in fields):
                self._answer(fields)
            else:
                connection.put(_error(fields.get("id"), INVALID_REQUEST, "No method in the frame."))
            return
        request_id = fields.get("id")
        try:
            result = await self._call(connection, fields)
        except ProtocolError as error:
            reply = _error(request_id, error.code, error.message)
        except ConversationNotFoundError as error:
            reply = _error(request_id, NOT_FOUND, str(error))
        except Exception as error:  # the server's edge: a client's call must not end the server
            _logger.exception("A method failed.", extra={"method": fields.get("method")})
            reply = _error(request_id, INTERNAL_ERROR, f"{type(error).__name__}: {error}")
        else:
            reply = {"jsonrpc": "2.0", "id": request_id, "result": dump(result)}
        if request_id is not None:
            connection.put(reply)

    async def _call(self, connection: Connection, frame: dict[str, Any]) -> object:
        name = frame["method"]
        declared = METHODS.get(name) if isinstance(name, str) else None
        if declared is None:
            raise ProtocolError(METHOD_NOT_FOUND, f"There is no method {name!r}.")
        params = read(declared.params, frame.get("params", {}))
        result = await self._handlers[declared.name](connection, params)
        if type(result) is not declared.result:  # pragma: no cover - a handler's own mistake
            raise TypeError(f"{declared.name} returned {type(result).__name__}.")
        return result

    def _answer(self, frame: dict[str, Any]) -> None:
        pending = self._requests.get(str(frame.get("id")))
        if pending is None or pending.answer.done():
            return  # the wait already ended
        if "error" in frame:
            result = None
        else:
            try:
                result = read(SERVER_REQUESTS[pending.method].result, frame["result"], "result")
            except ProtocolError as error:
                _logger.warning(
                    "A client answered a %s request wrongly: %s", pending.method, error.message
                )
                return
        pending.answer.set_result(result)
        self._cancelled(pending, "resolved")

    # -- methods ----------------------------------------------------------------------------------

    async def _ping(self, connection: Connection, params: Empty) -> Empty:
        return Empty()

    async def _list(self, connection: Connection, params: ListParams) -> ConversationList:
        after = Cursor.read(params.cursor) if params.cursor else None
        if params.cursor and after is None:
            raise ProtocolError(INVALID_PARAMS, "params.cursor is not a cursor this server gave.")
        limit = max(1, min(params.limit, LIST_MOST))
        found, more = self.assistant.conversations.page(limit, after)
        running = self.assistant.running
        rows = [_row(info, running(info.id)) for info in found]
        return ConversationList(rows, "" if more is None else more.text)

    async def _create(self, connection: Connection, params: Empty) -> Created:
        created = self.assistant.conversations.create()
        # A new conversation starts in "Auto" (asked for by the user on 20/10/2026).
        self.assistant.set_auto_mode(created.id, True)
        connection.conversations.add(created.id)
        return Created(created.id, str(self.assistant.workspace))

    async def _open(self, connection: Connection, params: ConversationRef) -> Opened:
        conversation_id = params.conversation_id
        info = self.assistant.conversations.info(conversation_id)
        messages = self.assistant.conversations.messages(conversation_id)
        connection.conversations.add(conversation_id)
        return Opened(
            conversation=_row(info, self.assistant.running(conversation_id)),
            messages=[_message(message, self.assistant.attachments.folder) for message in messages],
            running=self.assistant.running(conversation_id),
            auto_mode=self.assistant.auto_mode(conversation_id),
            epoch=self.replay.epoch,
            seq=self.replay.latest(conversation_id),
            open_requests=self._open_requests(conversation_id),
            folder=str(self.assistant.workspace),
        )

    async def _events(self, connection: Connection, params: EventsParams) -> Events:
        self.assistant.conversations.info(params.conversation_id)
        events, truncated = self.replay.since(params.conversation_id, params.after)
        connection.conversations.add(params.conversation_id)
        return Events(
            self.replay.epoch, events, truncated, self._open_requests(params.conversation_id)
        )

    async def _leave(self, connection: Connection, params: ConversationRef) -> Empty:
        connection.conversations.discard(params.conversation_id)
        return Empty()

    async def _send(self, connection: Connection, params: SendParams) -> SendResult:
        if not params.text.strip() and not params.attachments:
            raise ProtocolError(INVALID_PARAMS, "params.text is empty.")
        attachments = self.assistant.attachments
        try:
            attached = tuple(attachments.get(item) for item in params.attachments)
        except AttachmentError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        conversation = self.assistant.conversation(params.conversation_id)
        connection.conversations.add(params.conversation_id)
        return SendResult(conversation.send(UserMessage(params.text, attached)).value)

    async def _attachment_add(
        self, connection: Connection, params: AttachmentAddParams
    ) -> AttachmentAdded:
        """One part of a file the user attached (step 16g)."""
        try:
            data = base64.b64decode(params.data, validate=True)
        except binascii.Error as error:
            raise ProtocolError(INVALID_PARAMS, f"params.data is not base64: {error}") from error
        if len(data) > ATTACHMENT_PART_BYTES:
            raise ProtocolError(
                INVALID_PARAMS, f"A part carries at most {ATTACHMENT_PART_BYTES:,} bytes."
            )
        attachments = self.assistant.attachments
        try:
            added = await asyncio.to_thread(
                attachments.add, params.upload, params.name, params.size, params.offset, data
            )
        except AttachmentError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        row = (
            None if added.attachment is None else _attachment(added.attachment, attachments.folder)
        )
        return AttachmentAdded(added.upload, added.received, row)

    async def _attachment_limits(self, connection: Connection, params: Empty) -> AttachmentLimits:
        return AttachmentLimits(
            image=ATTACHMENT_LIMITS[AttachmentKind.IMAGE],
            document=ATTACHMENT_LIMITS[AttachmentKind.DOCUMENT],
            text=ATTACHMENT_LIMITS[AttachmentKind.TEXT],
            video=ATTACHMENT_LIMITS[AttachmentKind.VIDEO],
            audio=ATTACHMENT_LIMITS[AttachmentKind.AUDIO],
            other=ATTACHMENT_LIMITS[AttachmentKind.OTHER],
            part=ATTACHMENT_PART_BYTES,
        )

    async def _conversation_commands(
        self, connection: Connection, params: ConversationCommandsParams
    ) -> SlashCommandList:
        found = await asyncio.to_thread(
            self.assistant.slash_commands, params.conversation_id or None
        )
        return SlashCommandList([SlashCommandRow(**dump(command)) for command in found])

    async def _terminal(self, connection: Connection, params: TerminalParams) -> SendResult:
        """A terminal row (step 16f): the agent answers the command's output."""
        if not params.command.strip():
            raise ProtocolError(INVALID_PARAMS, "params.command is empty.")
        conversation = self.assistant.conversation(params.conversation_id)
        connection.conversations.add(params.conversation_id)
        sent = conversation.terminal(params.command, params.output, params.exit_code, params.status)
        return SendResult(sent.value)

    async def _preview_read(self, connection: Connection, params: PreviewReadParams) -> PreviewRead:
        kind, content = await asyncio.to_thread(read_preview, params.path)
        return PreviewRead(kind, content)

    # -- the server terminal (step 15) -------------------------------------------------------

    async def _terminal_available(self, connection: Connection, params: Empty) -> TerminalAvailable:
        reason = self.terminals.unavailable()
        return TerminalAvailable(reason is None, reason or "")

    async def _terminal_open(
        self, connection: Connection, params: TerminalOpenParams
    ) -> TerminalOpened:
        loop = asyncio.get_running_loop()
        holder: list[str] = []

        def output(data: str) -> None:
            frame = self._event_frame("terminal.output", TerminalOutput(holder[0], data))
            loop.call_soon_threadsafe(connection.put, frame)

        def ended(code: int | None) -> None:
            frame = self._event_frame("terminal.exit", TerminalExit(holder[0], code))
            loop.call_soon_threadsafe(connection.put, frame)

        try:
            opened = await asyncio.to_thread(
                self.terminals.open,
                connection,
                cols=params.cols,
                rows=params.rows,
                cwd=params.cwd,
                nonce=params.nonce,
                on_output=output,
                on_exit=ended,
            )
        except TerminalError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        holder.append(opened.id)
        # Read once the answer that names it is queued: this task puts it before it yields.
        loop.call_soon(self.terminals.start, opened.id)
        return TerminalOpened(opened.id, opened.shell, opened.marks)

    async def _terminal_input(self, connection: Connection, params: TerminalInputParams) -> Empty:
        try:
            self.terminals.write(connection, params.id, params.data)
        except TerminalError as error:
            raise ProtocolError(NOT_FOUND, str(error)) from error
        return Empty()

    async def _terminal_resize(self, connection: Connection, params: TerminalResizeParams) -> Empty:
        try:
            self.terminals.resize(connection, params.id, params.cols, params.rows)
        except TerminalError as error:
            raise ProtocolError(NOT_FOUND, str(error)) from error
        return Empty()

    async def _terminal_close(self, connection: Connection, params: TerminalRef) -> Empty:
        await asyncio.to_thread(self.terminals.close, connection, params.id)
        return Empty()

    async def _check_command(self, connection: Connection, params: CommandParams) -> CommandCheck:
        """The trust gate's own detection, before a ▶ runs a command (step 16f): what is
        blocked always and what is dangerous both ask, the user deciding."""
        hard, reason = detect_hardline_command(params.command)
        if hard:
            return CommandCheck(True, reason or "")
        dangerous, _, description = detect_dangerous_command(params.command)
        return CommandCheck(dangerous, description or "")

    async def _interrupt(self, connection: Connection, params: ConversationRef) -> Interrupted:
        if not self.assistant.running(params.conversation_id):
            self.assistant.conversations.info(params.conversation_id)
            return Interrupted(False)
        stopped = await self.assistant.conversation(params.conversation_id).interrupt()
        return Interrupted(stopped)

    async def _subagents(self, connection: Connection, params: ConversationRef) -> SubagentList:
        self.assistant.conversations.info(params.conversation_id)
        live = self.assistant.subagents.live(params.conversation_id)
        return SubagentList([_subagent_row(view) for view in live])

    async def _subagent_steer(
        self, connection: Connection, params: SubagentSteerParams
    ) -> SubagentControl:
        said = await self.assistant.subagents.steer(
            params.conversation_id, params.subagent_id, params.message
        )
        return SubagentControl(not said.startswith("[Atlas:"), said)

    async def _subagent_decides(
        self, connection: Connection, params: SubagentDecidesParams
    ) -> SubagentControl:
        decides = Decides(params.decides)
        done = self.assistant.subagents.set_decides(
            params.conversation_id, params.subagent_id, decides
        )
        said = f"From now on {decides.value} answers it." if done else "No such executor runs."
        return SubagentControl(done, said)

    async def _subagent_interrupt(
        self, connection: Connection, params: SubagentRef
    ) -> SubagentControl:
        said = await self.assistant.subagents.stop(params.conversation_id, params.subagent_id)
        return SubagentControl(not said.startswith("[Atlas:"), said)

    async def _commands(self, connection: Connection, params: ConversationRef) -> CommandList:
        self.assistant.conversations.info(params.conversation_id)
        opened = self.assistant.opened(params.conversation_id)
        running = [] if opened is None else opened.background_commands()
        return CommandList([_command_row(view) for view in running])

    async def _command_output(self, connection: Connection, params: CommandRef) -> CommandOutput:
        self.assistant.conversations.info(params.conversation_id)
        opened = self.assistant.opened(params.conversation_id)
        text = None if opened is None else opened.command_output(params.command_id)
        return CommandOutput(False) if text is None else CommandOutput(True, text)

    async def _command_stop(self, connection: Connection, params: CommandRef) -> CommandStopped:
        self.assistant.conversations.info(params.conversation_id)
        opened = self.assistant.opened(params.conversation_id)
        if opened is None or not await opened.stop_command(params.command_id):
            return CommandStopped(False)
        return CommandStopped(True, opened.command_output(params.command_id) or "")

    async def _auto_mode(self, connection: Connection, params: AutoModeParams) -> AutoMode:
        if params.enabled is not None:
            self.assistant.set_auto_mode(params.conversation_id, params.enabled)
        else:
            self.assistant.conversations.info(params.conversation_id)
        return AutoMode(self.assistant.auto_mode(params.conversation_id))

    async def _model_options(self, connection: Connection, params: Empty) -> ModelOptions:
        models = self.assistant.models
        try:
            offered = await asyncio.to_thread(models.offered)
        except ModelsError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        try:
            chosen = await asyncio.to_thread(models.default)
            default = ConversationModel(chosen.provider, chosen.model, chosen.effort, False)
        except ModelsError:
            default = ConversationModel(None, None, None, False)
        return ModelOptions(
            [
                ProviderOption(
                    provider.id,
                    provider.name,
                    [
                        ModelOption(
                            model.id,
                            model.name,
                            model.reasoning,
                            None if model.efforts is None else list(model.efforts),
                            default_effort(model.reasoning, model.efforts),
                        )
                        for model in provider.models
                    ],
                )
                for provider in offered
            ],
            default,
        )

    async def _providers(self, connection: Connection, params: Empty) -> KeyProviders:
        try:
            keyed = await asyncio.to_thread(self.assistant.models.keyed)
        except ModelsError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        return KeyProviders(
            [
                KeyProvider(provider.id, provider.name, provider.key_page, stored)
                for provider, stored in keyed
            ]
        )

    async def _provider_connect(
        self, connection: Connection, params: ProviderConnectParams
    ) -> ProviderConnected:
        try:
            found = await self.assistant.models.connect(params.provider, params.key)
        except ModelsError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        # `missing` is never found here: the check ran with the key given.
        status = cast("ConnectStatus", found.status.value)
        return ProviderConnected(found.provider, status, found.detail, status != "refused")

    async def _conversation_model(
        self, connection: Connection, params: ConversationModelParams
    ) -> ConversationModel:
        conversation_id = params.conversation_id
        if params.provider is not None or params.model is not None:
            if params.provider is None or params.model is None:
                raise ProtocolError(INVALID_PARAMS, "Pick a provider and a model together.")
            choice = ModelChoice(params.provider, params.model, params.effort)
            try:
                await asyncio.to_thread(
                    self.assistant.set_conversation_model, conversation_id, choice
                )
            except ModelsError as error:
                raise ProtocolError(INVALID_PARAMS, str(error)) from error
        choice, picked = await asyncio.to_thread(self.assistant.conversation_model, conversation_id)
        if choice is None:
            return ConversationModel(None, None, None, picked)
        return ConversationModel(choice.provider, choice.model, choice.effort, picked)

    # -- the update of the server's host (docs/ARCHITECTURE.md, The update of a Compose host) --

    def _owner(self, connection: Connection) -> None:
        """The update is the owner's: a client that presented the server token, never a
        channel's bridge (a bot's chat). A plugin's screen is refused in the app
        (`apps/desktop/src/plugins/sdk.ts`), and no tool reaches these methods."""
        if type(connection) is not Connection:
            raise ProtocolError(FORBIDDEN, "Only the owner's app may update the server.")

    async def _update_check(self, connection: Connection, params: Empty) -> UpdateInfo:
        self._owner(connection)
        if self._host_updater is None:
            return UpdateInfo(supported=False, available=False, kind="native", reason="native")
        return _update_info(await asyncio.to_thread(compose_check, self._host_updater))

    async def _update_apply(
        self, connection: Connection, params: UpdateApplyParams
    ) -> UpdateApplied:
        self._owner(connection)
        if self._host_updater is None:
            return UpdateApplied(
                False, "This server's installation updates from the app on its own machine."
            )
        try:
            await asyncio.to_thread(
                compose_apply,
                self._host_updater,
                params.target,
                allow_unverified=params.allow_unverified,
            )
        except InstallationError as error:
            return UpdateApplied(False, str(error))
        _logger.warning(
            "The owner's app asked the host to update; the server restarts when it is built.",
            extra={"target": params.target[:12], "unverified": params.allow_unverified},
        )
        return UpdateApplied(True)

    async def _update_status(self, connection: Connection, params: Empty) -> UpdateProgress:
        self._owner(connection)
        if self._host_updater is None:
            return UpdateProgress(False)
        try:
            found = await asyncio.to_thread(compose_progress, self._host_updater)
        except InstallationError as error:
            raise ProtocolError(INTERNAL_ERROR, str(error)) from error
        ended = None if found.ended is None else _update_ended(found.ended)
        return UpdateProgress(found.running, found.step, found.started_at, found.target, ended)

    async def _update_log(self, connection: Connection, params: Empty) -> UpdateLog:
        self._owner(connection)
        if self._host_updater is None:
            return UpdateLog("")
        try:
            return UpdateLog(await asyncio.to_thread(compose_log, self._host_updater))
        except InstallationError as error:
            raise ProtocolError(INTERNAL_ERROR, str(error)) from error

    async def _read_update_news(self) -> None:
        """At start, whether the host's last update ended moments ago: this server is then the
        one it started, and every client that connects hears how it went, once per connection.
        Those already connected hear it now."""
        assert self._host_updater is not None  # noqa: S101 - started only with one
        try:
            found = await asyncio.to_thread(compose_progress, self._host_updater)
        except InstallationError as error:
            _logger.warning("The host updater did not answer: %s", error)
            return
        if found.running or found.ended is None:
            return
        try:
            ended_at = datetime.fromisoformat(found.ended.ended_at)
        except ValueError:
            return
        if (self._clock() - ended_at).total_seconds() > UPDATE_NEWS_SECONDS:
            return
        self._update_ended = _update_ended(found.ended)
        for connection in self._connections:
            connection.put(self._event_frame("update.ended", self._update_ended))

    async def _homes(self, connection: Connection, params: Empty) -> HomeList:
        found = await asyncio.to_thread(list_homes, home_root(self._home.root))
        return HomeList(
            home_name(self._home.root), [HomeRow(home.name, home.active) for home in found]
        )

    async def _home_create(self, connection: Connection, params: HomeCreateParams) -> HomeList:
        root = home_root(self._home.root)
        try:
            clone = None if params.clone_from is None else home_path(root, params.clone_from)
            await asyncio.to_thread(create_home, root, params.name, clone=clone)
        except HomeError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        return await self._homes(connection, Empty())

    async def _home_backup(self, connection: Connection, params: HomeBackupParams) -> BackupWritten:
        target = _absolute(params.path)
        try:
            written = await asyncio.to_thread(
                write_backup,
                self._home,
                target,
                version=__version__,
                now=self._clock(),
                replace=params.replace,
            )
        except (HomeError, OSError) as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        return BackupWritten(str(written.path), written.manifest.files, written.size)

    async def _home_backup_download(self, connection: Connection, params: Empty) -> BackupDownload:
        """The backup `home.backup` writes, into a folder of the server's own, for the client to
        take down part by part; one at a time per client."""
        self._drop_downloads(connection)
        folder = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="atlas-backup-"))
        try:
            written = await asyncio.to_thread(
                write_backup,
                self._home,
                folder / "backup.zip",
                version=__version__,
                now=self._clock(),
            )
        except (HomeError, OSError) as error:
            shutil.rmtree(folder, ignore_errors=True)
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        download = secrets.token_hex(16)
        self._downloads[download] = (connection, written.path)
        return BackupDownload(download, written.manifest.files, written.size)

    async def _home_backup_part(
        self, connection: Connection, params: BackupPartParams
    ) -> BackupPart:
        found = self._downloads.get(params.id)
        if found is None or found[0] is not connection:
            raise ProtocolError(NOT_FOUND, "That backup is gone; back up again.")
        path = found[1]

        def read() -> tuple[bytes, int]:
            with path.open("rb") as file:
                file.seek(max(0, params.offset))
                return file.read(ATTACHMENT_PART_BYTES), path.stat().st_size

        data, size = await asyncio.to_thread(read)
        done = params.offset + len(data) >= size
        if done:
            del self._downloads[params.id]
            shutil.rmtree(path.parent, ignore_errors=True)
        return BackupPart(base64.b64encode(data).decode("ascii"), done)

    def _drop_downloads(self, connection: Connection) -> None:
        for download, (owner, path) in list(self._downloads.items()):
            if owner is connection:
                del self._downloads[download]
                shutil.rmtree(path.parent, ignore_errors=True)

    async def _home_inspect(self, connection: Connection, params: BackupRef) -> BackupInfo:
        path = _absolute(params.path)
        try:
            manifest = await asyncio.to_thread(check_backup, path, self._schemas)
            suggested = await asyncio.to_thread(
                suggest_home_name, home_root(self._home.root), manifest
            )
        except (HomeError, OSError) as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        return BackupInfo(
            manifest.home,
            manifest.created_at,
            manifest.atlas,
            manifest.tables.get(_THREADS_TABLE, 0),
            suggested,
        )

    async def _home_import(self, connection: Connection, params: HomeImportParams) -> HomeList:
        path = _absolute(params.path)
        try:
            await asyncio.to_thread(
                import_backup, home_root(self._home.root), params.name, path, self._schemas
            )
        except (HomeError, OSError) as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        return await self._homes(connection, Empty())

    async def _bots(self, connection: Connection, params: Empty) -> BotList:
        found = await asyncio.to_thread(self.assistant.bots.list)
        return BotList([self._bot_row(bot) for bot in found])

    def _bot_row(self, bot: Bot) -> BotRow:
        conversations = self.assistant.conversations
        fixed = conversations.fixed(bot.name)
        said = "" if fixed is None else " ".join(conversations.last_said(fixed.id).split())
        return BotRow(
            bot.name,
            bot.title,
            bot.description,
            bot.model,
            None if fixed is None else fixed.id,
            None if fixed is None else fixed.updated_at,
            said[:PREVIEW_CHARS],
            fixed is not None and self.assistant.running(fixed.id),
        )

    async def _bot_create(self, connection: Connection, params: BotDefinition) -> BotRow:
        return self._bot_row(await self._bot(self.assistant.create_bot, params))

    async def _bot_update(self, connection: Connection, params: BotDefinition) -> BotRow:
        return self._bot_row(await self._bot(self.assistant.update_bot, params))

    async def _bot(self, write: Callable[..., Bot], params: BotDefinition) -> Bot:
        try:
            return await asyncio.to_thread(
                write,
                params.name,
                title=params.title,
                description=params.description,
                body=params.mission,
                model=params.model,
                tools=params.tools,
                mcp_servers=params.mcp_servers,
                skills=params.skills,
            )
        except BotError as error:
            raise _bot_refused(error) from error

    async def _bot_describe(self, connection: Connection, params: BotRef) -> BotDefinition:
        try:
            bot = await asyncio.to_thread(self.assistant.bots.get, params.name)
        except BotError as error:
            raise _bot_refused(error) from error
        return BotDefinition(
            bot.name,
            bot.title,
            bot.description,
            bot.body,
            bot.model,
            None if bot.tools is None else list(bot.tools),
            None if bot.mcp_servers is None else list(bot.mcp_servers),
            None if bot.skills is None else list(bot.skills),
        )

    async def _bot_delete(self, connection: Connection, params: BotRef) -> Empty:
        try:
            await self.assistant.delete_bot(params.name)
        except BotError as error:
            raise _bot_refused(error) from error
        self._routines_changed()
        return Empty()

    async def _bot_options(self, connection: Connection, params: Empty) -> BotOptions:
        return BotOptions(*await self.assistant.bot_options())

    def _bots_changed(self) -> None:
        """Tell every client the roster changed. A bot is made from a worker thread too (the
        screen's dialog), so the frames are put on the server's own loop."""
        count = len(self.assistant.bots.list())
        frame = self._event_frame("bots.changed", BotsChanged(count))
        connections = list(self._connections)

        def tell() -> None:
            for connection in connections:
                connection.put(frame)

        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(tell)

    async def _bot_open(self, connection: Connection, params: BotRef) -> Created:
        try:
            conversation_id = await asyncio.to_thread(self.assistant.bot_conversation, params.name)
        except BotError as error:
            raise _bot_refused(error) from error
        connection.conversations.add(conversation_id)
        return Created(conversation_id)

    async def _home_picture(self, connection: Connection, params: Empty) -> HomePicture:
        data = await asyncio.to_thread(read_picture, self._home)
        return HomePicture(None if data is None else base64.b64encode(data).decode("ascii"))

    async def _home_set_picture(self, connection: Connection, params: HomePicture) -> HomePicture:
        try:
            data = None if params.png is None else base64.b64decode(params.png, validate=True)
            await asyncio.to_thread(write_picture, self._home, data)
        except (binascii.Error, PictureError) as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        return await self._home_picture(connection, Empty())

    async def _soul(self, connection: Connection, params: Empty) -> Soul:
        return await asyncio.to_thread(self._soul_now)

    async def _soul_write(self, connection: Connection, params: SoulText) -> Soul:
        try:
            await asyncio.to_thread(self.assistant.soul.write, params.text)
        except OSError as error:
            raise ProtocolError(INTERNAL_ERROR, f"SOUL.md could not be written: {error}") from error
        return await asyncio.to_thread(self._soul_now)

    def _soul_now(self) -> Soul:
        soul = self.assistant.soul
        try:
            text = soul.text()
        except (OSError, UnicodeDecodeError) as error:
            raise ProtocolError(INTERNAL_ERROR, f"SOUL.md cannot be read: {error}") from error
        return Soul(text or "", str(soul.path), IDENTITY, text is not None)

    async def _settings_read(self, connection: Connection, params: Empty) -> SettingsList:
        return await asyncio.to_thread(self._settings_now)

    async def _settings_write(self, connection: Connection, params: SettingsWrite) -> SettingsList:
        changes = {(change.section, change.key): change.value for change in params.changes}

        def write() -> Settings:
            self._check_model(changes)
            return edit_settings(self._home.root, self._environ, changes)

        try:
            settings = await asyncio.to_thread(write)
        except (SettingsError, ModelsError) as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        self.assistant.use_settings(settings)
        _logger.info(
            "Settings changed on the settings screen.",
            extra={"settings": sorted(f"{section}.{key}" for section, key in changes)},
        )
        return await asyncio.to_thread(self._settings_now)

    def _check_model(self, changes: Mapping[tuple[str, str], str | None]) -> None:
        """A provider the catalog knows, and a model only with its provider: what the default
        model would refuse at the next turn is refused before the file is written."""
        written = {
            (value.setting.section, value.setting.key): value.written
            for value in editable_values(self._home.root, self._environ)
        }
        merged = {**written, **changes}
        provider = (merged.get(("model", "provider")) or "").strip()
        if provider:
            self._models.catalog.provider(provider)
        elif (merged.get(("model", "name")) or "").strip():
            raise SettingsError("[model] name needs [model] provider: choose the provider too.")

    def _settings_now(self) -> SettingsList:
        defaults = {
            ("trust", "approval_timeout"): str(TrustSettings().approval_timeout),
            ("workspace", "folder"): str(Path.home()),
        }
        try:
            values = editable_values(self._home.root, self._environ)
        except SettingsError as error:
            raise ProtocolError(INTERNAL_ERROR, str(error)) from error
        return SettingsList(
            [
                SettingRow(
                    value.setting.section,
                    value.setting.key,
                    value.setting.kind,
                    value.written,
                    defaults.get((value.setting.section, value.setting.key), ""),
                    value.variable,
                )
                for value in values
            ]
        )

    async def _memory_entries(
        self, connection: Connection, params: MemoryEntriesParams
    ) -> MemoryEntries:
        return await asyncio.to_thread(self._memory_now, Target(params.target))

    async def _memory_edit(self, connection: Connection, params: MemoryEdit) -> MemoryEntries:
        target = Target(params.target)
        action = Action(params.action)
        entry = params.entry
        if action is not Action.ADD and not entry:
            raise ProtocolError(INVALID_PARAMS, f"Name the entry to {action}.")
        operation = Operation(action, params.content, entry or "", entry)
        files = self.assistant.memory

        def change() -> str | None:
            limit = files.limits[target]
            # The tool's own message speaks to a model; the screen says it in its words.
            planned = apply(files.entries(target), [operation], limit)
            if planned.entries is None and planned.consolidation:
                wanted = apply(files.entries(target), [operation], 10**9).entries
                if wanted is not None and size(wanted) > limit:
                    return (
                        f"That would put {MEMORY_FILES[target]} at {size(wanted):,} of its "
                        f"{limit:,} characters: shorten or remove another entry first."
                    )
            return files.change(target, [operation]).error

        refused = await asyncio.to_thread(change)
        if refused is not None:
            raise ProtocolError(INVALID_PARAMS, refused)
        return await asyncio.to_thread(self._memory_now, target)

    def _memory_now(self, target: Target) -> MemoryEntries:
        files = self.assistant.memory
        entries = files.entries(target)
        return MemoryEntries(
            target.value, entries, size(entries), files.limits[target], str(files.path(target))
        )

    async def _proposals(self, connection: Connection, params: Empty) -> ProposalList:
        listed = await asyncio.to_thread(self.assistant.proposals.checked)
        return ProposalList([_proposal_row(item) for item in listed])

    async def _approve(self, connection: Connection, params: ProposalRef) -> ProposalApproved:
        approval = await asyncio.to_thread(self.assistant.proposals.approve, params.proposal_id)
        if approval.applied:
            self._proposals_changed()
        return ProposalApproved(
            approval.applied, approval.error, list(approval.replaced), list(approval.removed)
        )

    async def _discard(self, connection: Connection, params: ProposalRef) -> ProposalDiscarded:
        discarded = await asyncio.to_thread(self.assistant.proposals.discard, params.proposal_id)
        if discarded:
            self._proposals_changed()
        return ProposalDiscarded(discarded)

    # -- plugins ----------------------------------------------------------------------------------

    async def _plugin_screens(self, connection: Connection, params: Empty) -> PluginScreens:
        found = await asyncio.to_thread(self.plugins.screens)
        return PluginScreens(
            [
                PluginScreenRow(screen.plugin, screen.source, list(screen.desktop))
                for screen in found
            ]
        )

    async def _plugin_themes(self, connection: Connection, params: Empty) -> PluginThemes:
        found = await asyncio.to_thread(self.plugins.themes)
        return PluginThemes(
            [
                PluginThemeRow(
                    item.plugin,
                    item.theme.name,
                    item.theme.label,
                    item.theme.base,
                    dict(item.theme.colors),
                    dict(item.theme.labels),
                )
                for item in found
            ]
        )

    async def _plugin_call_python(
        self, connection: Connection, params: PluginCallParams
    ) -> PluginCallResult:
        """A plugin's screen calls its Python (step 16m): only a call its manifest declares, of a
        plugin that is on; what it raises reaches the screen as the error's message."""
        found = self._calls.get((params.plugin, params.name))
        if found is None:
            raise ProtocolError(
                NOT_FOUND, f"The plugin {params.plugin} has no call {params.name} running."
            )
        try:
            result = await found.run(params.params)
        except Exception as error:  # a plugin's own code: its failure is the screen's to show
            _logger.warning(
                "A plugin's call failed.", extra={"plugin": params.plugin}, exc_info=True
            )
            raise ProtocolError(INVALID_PARAMS, f"{type(error).__name__}: {error}") from error
        try:
            json.dumps(result)
        except (TypeError, ValueError) as error:
            raise ProtocolError(
                INVALID_PARAMS, f"The call {params.name} returned something that is not JSON."
            ) from error
        return PluginCallResult(result)

    async def _plugin_reload(self, connection: Connection, params: Empty) -> PluginList:
        await self.reload_plugins()
        return await self._plugin_list(connection, params)

    async def _plugin_list(self, connection: Connection, params: Empty) -> PluginList:
        views = await asyncio.to_thread(self.plugins.all)
        return PluginList([self._plugin_row(view) for view in views])

    async def _plugin_enable(self, connection: Connection, params: PluginEnableParams) -> PluginRow:
        view = await self._plugin_call(self.plugins.enable, params.name, params.consent)
        await self.plugins.stop(view.name)
        await self.channels.reconcile(restart={view.name})
        await self.load_executors()
        await self.load_plugins()
        return await self._plugin_changed(view.name)

    async def _plugin_disable(self, connection: Connection, params: PluginRef) -> PluginRow:
        view = await self._plugin_call(self.plugins.disable, params.name)
        await self.plugins.stop(view.name)
        await self.channels.reconcile()
        await self.load_executors()
        await self.load_plugins()
        return await self._plugin_changed(view.name)

    async def _plugin_secret(self, connection: Connection, params: PluginSecretParams) -> PluginRow:
        view = await self._plugin_call(
            self.plugins.set_secret, params.name, params.secret, params.value
        )
        return await self._plugin_restarted(view)

    async def _plugin_setting(
        self, connection: Connection, params: PluginSettingParams
    ) -> PluginRow:
        view = await self._plugin_call(
            self.plugins.set_setting, params.name, params.setting, params.value
        )
        return await self._plugin_restarted(view)

    async def _plugin_restarted(self, view: PluginView) -> PluginRow:
        """A plugin whose secret or setting changed starts again, when it is on: a channel that
        failed for a missing token runs once the token is stored. Its executors are looked at
        again either way: the onboarding offers a shipped one that is off as it is found now."""
        if view.enabled and view.consented:
            self.plugins.clear_failure(view.name)
            await self.channels.reconcile(restart={view.name})
        if view.manifest is not None and view.manifest.executors:
            await self.load_executors()
        if view.enabled and view.consented:
            await self.load_plugins()
        return await self._plugin_changed(view.name)

    async def _plugin_changed(self, name: str) -> PluginRow:
        self.plugins_changed()
        return self._plugin_row(await asyncio.to_thread(self.plugins.view, name))

    # -- the marketplace (step 16s) ---------------------------------------------------------------

    async def _marketplace_list(
        self, connection: Connection, params: MarketplaceListParams
    ) -> MarketplaceList:
        listing = await self._market_call(self.marketplace.listing, refresh=params.refresh)
        return MarketplaceList(
            name=listing.name,
            description=listing.description,
            items=[_market_row(row) for row in listing.rows],
            read_at=format_utc(listing.read_at) if listing.read_at is not None else None,
            problem=listing.problem,
        )

    async def _marketplace_page(
        self, connection: Connection, params: MarketplaceRef
    ) -> MarketplacePage:
        return _market_page(await self._market_call(self.marketplace.page, params.name))

    async def _marketplace_install(
        self, connection: Connection, params: MarketplaceRef
    ) -> MarketplaceDone:
        done = await self._market_call(self.marketplace.install, params.name)
        await self._market_changed(done.row.kind, params.name)
        return MarketplaceDone(_market_row(done.row), done.changed, done.local_changes)

    async def _marketplace_update(
        self, connection: Connection, params: MarketplaceUpdateParams
    ) -> MarketplaceDone:
        done = await self._market_call(self.marketplace.update, params.name, force=params.force)
        if done.changed:
            await self._market_changed(done.row.kind, params.name)
        return MarketplaceDone(_market_row(done.row), done.changed, done.local_changes)

    async def _marketplace_remove(
        self, connection: Connection, params: MarketplaceRef
    ) -> MarketplaceRow:
        row = await self._market_call(self.marketplace.remove, params.name)
        await self._market_changed(row.kind, params.name)
        return _market_row(row)

    async def _marketplace_secret(
        self, connection: Connection, params: MarketplaceSecretParams
    ) -> MarketplacePage:
        """A secret an MCP server of the marketplace reads, pasted on its page: stored in the
        vault, and the server tried again."""
        page = await self._market_call(self.marketplace.page, params.name)
        if params.secret not in {secret.name for secret in page.secrets}:
            raise ProtocolError(INVALID_PARAMS, f"{params.name} reads no secret {params.secret}.")
        try:
            await asyncio.to_thread(self._vault.set, params.secret, params.value)
        except SecretsError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        await self._secret_stored(params.secret)
        self._marketplace_changed(params.name)
        return _market_page(await self._market_call(self.marketplace.page, params.name))

    async def _market_call[**P, T](
        self, call: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        try:
            return await asyncio.to_thread(call, *args, **kwargs)
        except MarketplaceError as error:
            code = NOT_FOUND if "has no item" in str(error) else INVALID_PARAMS
            raise ProtocolError(code, str(error)) from error

    async def _market_changed(self, kind: MarketKind, name: str) -> None:
        """What an install, an update or a removal changed reaches the running Atlas at once: a
        plugin is loaded again (it arrives off; one updated may need consent again), and
        `mcp.json` is read again (a skill may have brought a server along)."""
        if kind in (MarketKind.PLUGIN, MarketKind.EXECUTOR):
            await self.reload_plugins({name})
        else:
            await self.assistant.reload_mcp()
        self._marketplace_changed(name)

    def _marketplace_changed(self, name: str) -> None:
        frame = self._event_frame("marketplace.changed", MarketplaceChanged(name))
        for connection in self._connections:
            connection.put(frame)

    def _stored_names(self) -> list[str]:
        try:
            return self._vault.names()
        except SecretsError:
            return []

    async def _watch_mcp_file(self) -> None:
        """Read `mcp.json` again once it changed (step 16s): the marketplace wrote it, or the user
        did, and its servers follow without a restart."""
        try:
            status = self._home.mcp_file.stat()
            stamp = (status.st_mtime_ns, status.st_size)
        except FileNotFoundError:
            stamp = (0, 0)
        if self._mcp_stamp is None:
            self._mcp_stamp = stamp
        elif stamp != self._mcp_stamp:
            self._mcp_stamp = stamp
            await self.assistant.reload_mcp()

    async def _plugin_call[**P, T](
        self, call: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        try:
            return await asyncio.to_thread(call, *args, **kwargs)
        except PluginError as error:
            code = NOT_FOUND if str(error).startswith("There is no plugin") else INVALID_PARAMS
            raise ProtocolError(code, str(error)) from error

    def _plugin_row(self, view: PluginView) -> PluginRow:
        manifest = view.manifest
        if manifest is None:
            return PluginRow(
                view.name, "", "", "", view.bundled, view.enabled, False, False, view.problem,
                [], "", [], [], [],
            )  # fmt: skip
        declared = {s.name: s for s in manifest.secrets}
        return PluginRow(
            name=view.name,
            version=manifest.version,
            description=manifest.description,
            author=manifest.author,
            bundled=view.bundled,
            enabled=view.enabled,
            consented=view.consented,
            running=self.channels.running(view.name)
            or view.name in self._executor_plugins
            # Its tools, skills and MCP servers are in use while it is on and nothing failed.
            or (view.running and bool(manifest.tools or manifest.skills or manifest.mcp_servers)),
            problem=view.problem,
            declaration=list(manifest.declaration()),
            consent=manifest.fingerprint(),
            channels=list(manifest.channels),
            secrets=[
                PluginSecretRow(name, declared[name].description, declared[name].url, stored)
                for name, stored in view.secrets.items()
            ],
            settings=[
                PluginSettingRow(
                    s.name, view.settings.get(s.name, s.default), s.default, s.description
                )
                for s in manifest.settings
            ],
        )

    async def _pairing_list(self, connection: Connection, params: Empty) -> PairingList:
        found = await asyncio.to_thread(self.plugins.waiting)
        return PairingList([_pairing_row(pairing) for pairing in found])

    async def _pairing_approve(
        self, connection: Connection, params: PairingApproveParams
    ) -> PairingRow:
        pairing = await self._plugin_call(self.plugins.approve, params.code)
        self.plugins_changed()
        return _pairing_row(replace(pairing, approved=True))

    async def _pairing_forget(self, connection: Connection, params: PairingForgetParams) -> Empty:
        await self._plugin_call(self.plugins.forget, params.channel, params.user)
        self.plugins_changed()
        return Empty()

    # -- what a channel's bridge uses (atlas.server.channels) -------------------------------------

    def attach(self, connection: Connection) -> None:
        self._connections.add(connection)

    def detach(self, connection: Connection) -> None:
        self._connections.discard(connection)

    def answer(self, request_id: str, result: dict[str, Any]) -> bool:
        """Answer a server request as a client would; `False` when it no longer waits."""
        pending = self._requests.get(request_id)
        if pending is None or pending.answer.done():
            return False
        self._answer({"id": request_id, "result": result})
        return pending.answer.done()

    def held(self, conversations: Iterable[str]) -> list[dict[str, Any]]:
        """The server requests still open of the workbench and of `conversations`, as frames: a
        channel that starts while they wait gets them, as an app reads them as it opens one."""
        wanted = set(conversations) | {_WORKBENCH}
        return [
            {"jsonrpc": "2.0", "id": p.id, "method": p.method, "params": p.params}
            for p in self._requests.values()
            if p.conversation_id in wanted and not p.answer.done()
        ]

    def conversations_changed(self, conversation_id: str, status: str = "") -> None:
        """Tell every client a channel's conversation was made or moved, or a turn of any began or
        ended (`status`), so the lists show it and which works."""
        frame = self._event_frame(
            "conversations.changed", ConversationsChanged(conversation_id, status)
        )
        for connection in self._connections:
            connection.put(frame)

    def plugins_changed(self) -> None:
        """Tell every client the plugins or the people waiting to pair changed."""
        waiting = sum(1 for pairing in self.plugins.waiting() if not pairing.approved)
        frame = self._event_frame("plugins.changed", PluginsChanged(waiting))
        for connection in self._connections:
            connection.put(frame)

    # -- events -----------------------------------------------------------------------------------

    def _publish(self, conversation_id: str, event: ConversationEvent) -> None:
        """A conversation's event, numbered, kept and sent to the clients attached to it."""
        self._send_event(conversation_id, *_wire(event, self.assistant.attachments.folder))
        if isinstance(event, Reviewed) and event.proposals:
            self._proposals_changed()
        match event:
            case TurnStarted():
                self.conversations_changed(conversation_id, "started")
            case TurnEnded():
                self.conversations_changed(conversation_id, event.outcome.value)
            case TurnStopped():
                self.conversations_changed(conversation_id, "interrupted")
            case _:
                pass

    def _proposals_changed(self) -> None:
        """Tell every client, attached to any conversation or none, how many proposals wait."""
        count = len(self.assistant.proposals.all())
        frame = self._event_frame("proposals.changed", ProposalCount(count))
        for connection in self._connections:
            connection.put(frame)

    def _send_event(self, conversation_id: str, name: str, payload: object) -> None:
        frame = self._event_frame(name, payload, conversation_id)
        for connection in self._attached(conversation_id):
            connection.put(frame)

    def _event_frame(
        self, name: str, payload: object, conversation_id: str | None = None
    ) -> dict[str, Any]:
        declared = EVENTS[name]
        if type(payload) is not declared.payload:  # pragma: no cover - a caller's own mistake
            raise TypeError(f"{name} carries {declared.payload.__name__}.")
        params: dict[str, Any] = {"type": name, "payload": dump(payload)}
        if declared.conversation and conversation_id is not None:
            params["conversation_id"] = conversation_id
            self.replay.record(conversation_id, params)
        return {"jsonrpc": "2.0", "method": "event", "params": params}

    def _attached(self, conversation_id: str) -> list[Connection]:
        return [c for c in self._connections if conversation_id in c.conversations and not c.gone]

    # -- server requests --------------------------------------------------------------------------

    def _asker(self, conversation_id: str) -> Asker:
        async def ask(questions: tuple[Question, ...]) -> Answers:
            rows = [QuestionRow(q.id, q.text, list(q.choices), q.multi_select) for q in questions]
            wait, result = await self._request(
                conversation_id, "ask_user", AskParams(conversation_id, rows), self._ask_seconds
            )
            if wait is _Wait.UNDELIVERED:
                notice = "No app is connected to this conversation to answer."
                return Answers(Outcome.UNDELIVERED, notice=notice)
            if wait is _Wait.TIMED_OUT:
                notice = f"Nobody answered in {self._ask_seconds:.0f} seconds."
                return Answers(Outcome.TIMED_OUT, notice=notice)
            if not isinstance(result, AskResult) or result.answers is None:
                return Answers(Outcome.CANCELLED, notice="The user dismissed the questions.")
            return Answers(Outcome.SUBMITTED, result.answers)

        return ask

    def _approver(self, conversation_id: str) -> Approver:
        async def approve(request: ApprovalRequest) -> ApprovalAnswer:
            params = ApprovalParams(
                conversation_id,
                request.tool,
                request.target,
                request.reason,
                request.allow_conversation,
                request.allow_always,
                request.guardian_denied,
                # The gate runs inside the call it judges: the screen marks that call waiting.
                current_call_id() or "",
            )
            # No deadline here: the approval gate keeps the user's (`[trust] approval_timeout`).
            wait, result = await self._request(conversation_id, "approval", params, None)
            if wait is _Wait.UNDELIVERED:
                return ApprovalAnswer(ApprovalOutcome.UNDELIVERED)
            if not isinstance(result, ApprovalResult):
                return ApprovalAnswer(ApprovalOutcome.CANCELLED)
            return ApprovalAnswer(ApprovalOutcome.ANSWERED, Choice(result.choice), result.reason)

        return approve

    def _secret_asker(self, conversation_id: str, bot: str = "") -> SecretAsker:
        """The server side of the secrets tool (step 16d): the request shows a masked card in the
        app, the value comes back as `SecretResult.value`, the server registers it with the
        redactor and writes it to the vault; the model gets only `stored`."""
        vault = self._vault

        async def ask(request: SecretRequest) -> SecretAnswer:
            try:
                stored = set(vault.names())
            except SecretsError:
                stored = set[str]()
            exists = request.name in stored
            updated_at = vault.updated_at(request.name) or "" if exists else ""
            params = SecretParams(
                conversation_id, request.name, request.reason, exists, updated_at, bot
            )
            wait, result = await self._request(
                conversation_id, "secret", params, SECRET_REQUEST_SECONDS
            )
            if wait is _Wait.UNDELIVERED:
                return SecretAnswer(
                    SecretOutcome.NO_ANSWER,
                    detail="No app is attached to this conversation to show the card.",
                )
            if wait is _Wait.TIMED_OUT:
                return SecretAnswer(
                    SecretOutcome.NO_ANSWER,
                    detail=(
                        f"Nobody answered in {SECRET_REQUEST_SECONDS // 60} minutes. "
                        f"Say the user can paste the key later with `atlas secrets set "
                        f"{request.name}`, or go on without it."
                    ),
                )
            if not isinstance(result, SecretResult):
                return SecretAnswer(SecretOutcome.SKIPPED)
            if result.skipped or not result.value:
                return SecretAnswer(SecretOutcome.SKIPPED)
            # Register with the redactor first: a failed write still leaves nothing in later
            # log lines or in replayed frames. Then write it as pasted, byte for byte; the vault
            # registers again on success.
            vault.register(result.value)
            try:
                vault.set(request.name, result.value)
            except SecretsError as error:
                return SecretAnswer(SecretOutcome.FAILED, detail=str(error))
            await self._secret_stored(request.name)
            return SecretAnswer(SecretOutcome.STORED)

        return ask

    async def _secret_stored(self, name: str) -> None:
        """A secret was just written: a plugin that declares it is started again, and an MCP
        server that could not start for want of it is tried again (step 16d).

        Both callbacks are best-effort: a plugin or an MCP server that still cannot start keeps
        its own problem, which the user reads on its screen.
        """
        restarter = getattr(self.channels, "secret_stored", None)
        if restarter is not None:
            try:
                await _maybe_await(restarter(name))
            except Exception:  # pragma: no cover - the plugins' edge, logged
                _logger.exception("A plugin could not restart after %s was stored.", name)
        shared = getattr(self.assistant, "_shared", None)
        client = getattr(shared, "mcp", None) if shared is not None else None
        if client is not None:
            retry = getattr(client, "secret_stored", None)
            if retry is not None:
                try:
                    await _maybe_await(retry(name))
                except Exception:  # pragma: no cover - the MCP edge, logged
                    _logger.exception("MCP could not retry %s after it was stored.", name)

    async def _request(
        self,
        conversation_id: str,
        method: str,
        params: object,
        seconds: float | None,
        *,
        held: bool = False,
    ) -> tuple[_Wait, object | None]:
        """Ask the clients of a conversation. With nobody attached the answer is "undelivered"
        at once, unless the request is `held`: an outside executor's waits for a client to come,
        which gets it with the conversation's open requests."""
        attached = (
            [c for c in self._connections if not c.gone]
            if conversation_id == _WORKBENCH
            else self._attached(conversation_id)
        )
        if not attached and not held:
            return _Wait.UNDELIVERED, None
        pending = _Pending(
            f"srq-{secrets.token_hex(6)}",
            conversation_id,
            method,
            dump(params),
            asyncio.get_running_loop().create_future(),
        )
        self._requests[pending.id] = pending
        frame = {"jsonrpc": "2.0", "id": pending.id, "method": method, "params": pending.params}
        for connection in attached:
            connection.put(frame)
        try:
            async with asyncio.timeout(seconds):
                result = await pending.answer
        except TimeoutError:
            self._cancelled(pending, "timeout")
            return _Wait.TIMED_OUT, None
        except asyncio.CancelledError:
            self._cancelled(pending, "withdrawn")
            raise
        finally:
            self._requests.pop(pending.id, None)
        return _Wait.ANSWERED, result

    async def executor_ask(
        self, asker: InstanceChanged, questions: Sequence[ExecutorQuestion]
    ) -> ExecutorAnswers:
        """What an executor instance asks the user, as a card on the workbench, on every screen,
        with no deadline (step 13): the user's answers, or `None` when they dismissed them."""
        rows = [
            ExecutorQuestionRow(
                question.text,
                question.header,
                [ExecutorChoiceRow(o.label, o.description) for o in question.options],
                question.multiple,
            )
            for question in questions
        ]
        params = ExecutorQuestionParams(
            asker.instance_id, asker.executor, asker.title, asker.parent_id, rows, asker.folder
        )
        _, result = await self._request(_WORKBENCH, "executor.question", params, None, held=True)
        if not isinstance(result, ExecutorQuestionResult):
            return None
        return result.answers

    async def executor_approve(
        self, asker: InstanceChanged, permission: ExecutorPermission
    ) -> ExecutorVerdict:
        """What an executor instance asks to do, as a card on the workbench, with no deadline."""
        approval = ExecutorApprovalParams(
            asker.instance_id,
            asker.executor,
            asker.title,
            asker.parent_id,
            permission.tool,
            permission.summary,
            permission.title,
            permission.description,
            asker.folder,
        )
        _, result = await self._request(_WORKBENCH, "executor.approval", approval, None, held=True)
        if isinstance(result, ExecutorApprovalResult) and result.allow:
            return ExecutorVerdict(True)
        reason = result.reason if isinstance(result, ExecutorApprovalResult) else None
        return ExecutorVerdict(
            False, f"The user denied this: {reason}" if reason else "The user denied this."
        )

    # -- the workbench ----------------------------------------------------------------------------

    def _workbench_event(self, event: WorkbenchEvent) -> None:
        """A change on the workbench, to every screen: it is the same in every conversation."""
        if isinstance(event, InstanceChanged):
            frame = self._event_frame("workbench.update", _instance_row(event))
        elif isinstance(event, ExecutorUsage):
            frame = self._event_frame("executor.usage", _usage_row(event))
        else:
            frame = self._event_frame(
                "workbench.activity", _activity(event, self.assistant.attachments.folder)
            )
        for connection in self._connections:
            connection.put(frame)

    async def _workbench_list(self, connection: Connection, params: Empty) -> WorkbenchList:
        instances = [_instance_row(run.view()) for run in self.assistant.workbench.runs()]
        executors = await self._executor_rows()
        return WorkbenchList(instances, executors, self._open_requests(_WORKBENCH))

    async def _executor_rows(self) -> list[ExecutorRow]:
        views = {view.name: view for view in await asyncio.to_thread(self.plugins.all)}
        executors: list[ExecutorRow] = []
        for name, found in sorted(self.detections.items()):
            plugin, title = self._executor_names.get(name, ("", name))
            row = _executor_row(name, found, plugin, title)
            running = self.assistant.executor(name)
            on = self._account_rows.get(name)
            if running is None and on is not None:
                continue
            if running is not None:
                capabilities = running.capabilities()
                row = dataclasses.replace(
                    row,
                    accounts=capabilities.accounts,
                    account_folders=capabilities.account_folders and on is None,
                    files=capabilities.files,
                )
            if on is not None:
                row = dataclasses.replace(row, base=on[0], account_id=on[1], nickname=on[2])
            manifest = views[plugin].manifest if plugin in views else None
            if name in self._offered and manifest is not None:
                row = dataclasses.replace(
                    row,
                    enabled=False,
                    declaration=list(manifest.declaration()),
                    consent=manifest.fingerprint(),
                )
            executors.append(row)
        return executors

    async def _workbench_start(
        self, connection: Connection, params: WorkbenchStartParams
    ) -> InstanceRow:
        run = await self._on_workbench(
            self.assistant.workbench.start(
                params.executor,
                Path(params.folder),
                resume=params.session,
                auto=params.auto,
                fork=params.fork,
                model=params.model,
                effort=params.effort,
                account=params.account_id,
            )
        )
        return _instance_row(run.view())

    async def _workbench_prompt(
        self, connection: Connection, params: WorkbenchPromptParams
    ) -> WorkbenchPrompted:
        try:
            attached = tuple(self.assistant.attachments.get(item) for item in params.attachments)
        except AttachmentError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error
        steered = await self._on_workbench(
            self.assistant.workbench.prompt(params.instance_id, params.text, attached)
        )
        return WorkbenchPrompted(steered)

    async def _workbench_auto(
        self, connection: Connection, params: WorkbenchSwitchParams
    ) -> InstanceRow:
        workbench = self.assistant.workbench
        await self._on_workbench(workbench.set_auto(params.instance_id, params.enabled))
        return _instance_row(workbench.get(params.instance_id).view())

    async def _workbench_model(
        self, connection: Connection, params: WorkbenchModelParams
    ) -> InstanceRow:
        workbench = self.assistant.workbench
        await self._on_workbench(
            workbench.set_model(params.instance_id, params.model, params.effort)
        )
        return _instance_row(workbench.get(params.instance_id).view())

    async def _workbench_atlas_answers(
        self, connection: Connection, params: WorkbenchSwitchParams
    ) -> InstanceRow:
        workbench = self.assistant.workbench
        try:
            workbench.set_atlas_answers(params.instance_id, params.enabled)
            return _instance_row(workbench.get(params.instance_id).view())
        except ExecutorError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error

    async def _workbench_duplicate(
        self, connection: Connection, params: WorkbenchFolderParams
    ) -> InstanceRow:
        run = await self._on_workbench(
            self.assistant.workbench.duplicate(params.instance_id, Path(params.folder))
        )
        return _instance_row(run.view())

    async def _workbench_folder(
        self, connection: Connection, params: WorkbenchFolderParams
    ) -> InstanceRow:
        run = await self._on_workbench(
            self.assistant.workbench.move(params.instance_id, Path(params.folder))
        )
        return _instance_row(run.view())

    async def _workbench_account(
        self, connection: Connection, params: WorkbenchAccountParams
    ) -> InstanceRow:
        run = await self._on_workbench(
            self.assistant.workbench.switch_account(params.instance_id, params.login)
        )
        return _instance_row(run.view())

    async def _workbench_use_account(
        self, connection: Connection, params: WorkbenchUseAccountParams
    ) -> InstanceRow:
        run = await self._on_workbench(
            self.assistant.workbench.use_account(params.instance_id, params.account_id)
        )
        return _instance_row(run.view())

    async def _workbench_route(
        self, connection: Connection, params: InstanceRef
    ) -> SessionRouteRow:
        found = await self._on_workbench(self.assistant.workbench.route(params.instance_id))
        return _route_of(found)

    async def _workbench_set_route(
        self, connection: Connection, params: WorkbenchRouteParams
    ) -> SessionRouteRow:
        targets = [
            RouteTarget(
                given.executor,
                given.account_id,
                model=given.model or None,
                effort=given.effort or None,
            )
            for given in params.targets
        ]
        found = await self._on_workbench(
            self.assistant.workbench.set_route(
                params.instance_id, targets, enabled=params.enabled, compact=params.compact
            )
        )
        return _route_of(found)

    async def _workbench_account_switch(
        self, connection: Connection, params: WorkbenchUseAccountParams
    ) -> AccountSwitchRow:
        found = await self._on_workbench(
            self.assistant.workbench.account_switch(params.instance_id, params.account_id)
        )
        return AccountSwitchRow(
            found.executor,
            found.account_id,
            found.login,
            list(found.missing),
            found.compared,
            found.tokens,
        )

    async def _executor_accounts(
        self, connection: Connection, params: ExecutorRef
    ) -> ExecutorAccounts:
        listed = await self._on_workbench(self.assistant.workbench.accounts(params.executor))
        return _accounts_of(listed)

    async def _executor_failover(
        self, connection: Connection, params: ExecutorFailoverParams
    ) -> ExecutorAccounts:
        listed = await self._on_workbench(
            self.assistant.workbench.set_failover(
                params.executor, params.order, enabled=params.enabled, compact=params.compact
            )
        )
        await self._accounts_changed(params.executor, rows=False)
        return _accounts_of(listed)

    async def _executor_add_account(
        self, connection: Connection, params: ExecutorAddAccountParams
    ) -> AccountRow:
        view = await self._on_workbench(
            self.assistant.workbench.add_account(params.executor, params.nickname)
        )
        await self._accounts_changed(params.executor)
        return AccountRow(**dump(view))

    async def _executor_remove_account(
        self, connection: Connection, params: ExecutorAccountRef
    ) -> Empty:
        await self._on_workbench(
            self.assistant.workbench.remove_account(params.executor, params.account_id)
        )
        await self._accounts_changed(params.executor)
        return Empty()

    async def _executor_folder_account(
        self, connection: Connection, params: ExecutorFolderAccountParams
    ) -> Empty:
        await self._on_workbench(
            self.assistant.workbench.set_folder_account(
                params.executor, params.folder, params.account_id
            )
        )
        await self._accounts_changed(params.executor, rows=False)
        return Empty()

    async def _executor_delete_session(
        self, connection: Connection, params: SessionDeleteParams
    ) -> Empty:
        await self._on_workbench(
            self.assistant.workbench.delete_session(
                params.executor, params.session_id, params.folder
            )
        )
        return Empty()

    async def _accounts_changed(self, executor: str, *, rows: bool = True) -> None:
        """Every screen reads the executor's accounts and folders again; with `rows`, its
        accounts' rows are looked at again first, one added or gone."""
        if rows:
            found = self.assistant.executor(executor)
            async with self._executors_lock:
                for name in [n for n, on in self._account_rows.items() if on[0] == executor]:
                    self._account_rows.pop(name, None)
                    self.detections.pop(name, None)
                if found is not None:
                    self.detections.update(await self._account_detections(executor, found))
            self.plugins_changed()
        frame = self._event_frame("executor.accounts", AccountsChanged(executor))
        for connection in self._connections:
            connection.put(frame)

    async def _executor_account(
        self, connection: Connection, params: ExecutorAccountParams
    ) -> ExecutorRow:
        name = params.executor
        await self._on_workbench(
            self.assistant.workbench.switch_executor_account(name, params.login)
        )
        executor = self.assistant.executor(name)
        if executor is not None:
            looked = await self._look(executor)
            # A look that failed leaves the row as it was: the login itself went through.
            if looked.installed:
                async with self._executors_lock:
                    self.detections[name] = looked
        return next(row for row in await self._executor_rows() if row.name == name)

    async def _workbench_stop(self, connection: Connection, params: InstanceRef) -> Empty:
        await self._on_workbench(self.assistant.workbench.stop(params.instance_id))
        return Empty()

    # -- the executors tab (step 16f) -----------------------------------------------------------

    async def _executor_detect(self, connection: Connection, params: Empty) -> ExecutorList:
        """Look for the harnesses again, as at a start: the screen asks when the executors tab
        shows and on its "Look again", so one installed, updated or logged in while Atlas runs is
        found without a restart."""
        await self.load_executors()
        return ExecutorList(await self._executor_rows())

    async def _executor_folders(
        self, connection: Connection, params: ExecutorRef
    ) -> SessionFolderList:
        views = await self._on_workbench(self.assistant.workbench.folders(params.executor))
        return SessionFolderList([SessionFolderRow(**dump(view)) for view in views])

    async def _executor_sessions(
        self, connection: Connection, params: SessionListParams
    ) -> SessionList:
        work = self.assistant.workbench.sessions(params.executor, params.folder)
        views = await self._on_workbench(work)
        return SessionList([SessionRow(**dump(view)) for view in views])

    async def _executor_models(self, connection: Connection, params: ExecutorRef) -> ExecutorModels:
        found = await self._on_workbench(self.assistant.workbench.models(params.executor))
        return ExecutorModels(
            [
                ExecutorModel(
                    m.id,
                    m.name,
                    m.description,
                    list(m.efforts),
                    m.resolved,
                    default_effort(None, m.efforts) or "",
                )
                for m in found
            ]
        )

    async def _executor_connectors(
        self, connection: Connection, params: ExecutorRef
    ) -> ExecutorConnectors:
        found = await self._on_workbench(self.assistant.workbench.connectors(params.executor))
        return ExecutorConnectors(
            found is not None,
            [
                ExecutorConnector(c.name, c.status, c.origin, c.plugin, c.tools, c.error)
                for c in found or ()
            ],
        )

    async def _executor_commands(
        self, connection: Connection, params: ExecutorCommandsParams
    ) -> SlashCommandList:
        work = self.assistant.workbench.commands(
            params.executor, Path(params.folder), params.instance_id
        )
        found = await self._on_workbench(work)
        return SlashCommandList([SlashCommandRow(**dump(command)) for command in found])

    async def _executor_usage(
        self, connection: Connection, params: ExecutorRef
    ) -> ExecutorUsageRow:
        found = await self._on_workbench(self.assistant.workbench.usage(params.executor))
        if found is None:
            return ExecutorUsageRow(params.executor, listed=False)
        return _usage_row(found)

    async def _executor_session(
        self, connection: Connection, params: SessionReadParams
    ) -> SessionRead:
        read = await self._on_workbench(
            self.assistant.workbench.transcript(
                params.executor, params.session_id, Path(params.folder)
            )
        )
        attached = self.assistant.attachments
        rows = [
            TranscriptRow(
                e.kind,
                e.text,
                e.tool,
                e.by,
                e.at,
                e.call,
                e.failed,
                [row for path in e.files if (row := _attached_at(attached, path)) is not None],
            )
            for e in read.entries
        ]
        return SessionRead(rows, read.expired)

    async def _subagent_session(
        self, connection: Connection, params: SubagentRef
    ) -> SubagentSession:
        found = self.assistant.subagents.session(params.conversation_id, params.subagent_id)
        if found is None:
            return SubagentSession(False)
        executor, folder, session = found
        return SubagentSession(True, executor, folder, session)

    async def _on_workbench[T](self, work: Awaitable[T]) -> T:
        """What the workbench refuses becomes the method's error, its words for the screen."""
        try:
            return await work
        except ExecutorError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error

    def _cancelled(self, pending: _Pending, reason: str) -> None:

        payload = RequestCancel(pending.id, pending.method, reason)  # pyright: ignore[reportArgumentType]
        if pending.conversation_id == _WORKBENCH:
            frame = self._event_frame("request.cancel", payload)
            for connection in self._connections:
                connection.put(frame)
            return
        self._send_event(pending.conversation_id, "request.cancel", payload)

    def _open_requests(self, conversation_id: str) -> list[OpenRequest]:
        return [
            OpenRequest(p.id, p.method, p.params)
            for p in self._requests.values()
            if p.conversation_id == conversation_id and not p.answer.done()
        ]

    # -- housekeeping -----------------------------------------------------------------------------

    async def _keep_house(self) -> None:
        """Every minute: the routines due fire, then the curator is asked."""
        while True:
            await asyncio.sleep(self._housekeeping_seconds)
            self._fire_routines()
            try:
                await self.channels.check()
                if self._executors_seen != await asyncio.to_thread(_executor_states, self.plugins):
                    await self.load_executors()
            except Exception:  # the server's edge: housekeeping must not stop, logged
                _logger.exception("The plugins could not be checked.")
            try:
                await self.assistant.curate()
            except Exception:  # the server's edge: housekeeping must not stop, logged
                _logger.exception("The curator failed.")

    def _fire_routines(self) -> None:
        try:
            if self.scheduler.tick():
                self._routines_changed()
        except Exception:  # the server's edge: housekeeping must not stop, logged
            _logger.exception("The routines could not fire.")

    # -- routines ---------------------------------------------------------------------------------

    async def _routines(self, connection: Connection, params: Empty) -> RoutineList:
        found = await asyncio.to_thread(self.assistant.routines.store.all)
        running = self.scheduler.running
        return RoutineList([_routine_row(routine, routine.id in running) for routine in found])

    async def _routine_runs(self, connection: Connection, params: RoutineRef) -> RunList:
        routine = await self._routine(params.routine_id)
        runs = await asyncio.to_thread(self.assistant.routines.store.runs, routine.id)
        return RunList([_run_row(run) for run in runs])

    async def _routine_run(self, connection: Connection, params: RoutineRef) -> RoutineRow:
        routine = await self._act(self.assistant.routines.trigger, params.routine_id)
        self._fire_routines()
        return _routine_row(routine, routine.id in self.scheduler.running)

    async def _routine_pause(self, connection: Connection, params: RoutineRef) -> RoutineRow:
        routine = await self._act(self.assistant.routines.pause, params.routine_id)
        self._routines_changed()
        return _routine_row(routine, routine.id in self.scheduler.running)

    async def _routine_resume(self, connection: Connection, params: RoutineRef) -> RoutineRow:
        routine = await self._act(self.assistant.routines.resume, params.routine_id)
        self._routines_changed()
        return _routine_row(routine, routine.id in self.scheduler.running)

    async def _routine_remove(self, connection: Connection, params: RoutineRef) -> RoutineRemoved:
        await self._act(self.assistant.routines.remove, params.routine_id)
        self._routines_changed()
        return RoutineRemoved(True)

    async def _routine(self, key: str) -> Routine:
        return await self._act(self.assistant.routines.store.get, key)

    async def _act(self, action: Callable[[str], Routine], key: str) -> Routine:
        try:
            return await asyncio.to_thread(action, key)
        except JobsError as error:
            raise ProtocolError(
                NOT_FOUND if "no routine" in str(error) else INVALID_PARAMS, str(error)
            ) from error

    def _routines_changed(self) -> None:
        count = len(self.assistant.routines.store.all())
        frame = self._event_frame("routines.changed", RoutinesChanged(count))
        for connection in self._connections:
            connection.put(frame)

    def _routine_finished(self, routine: Routine, run: Run) -> None:
        """Tell every client a run ended, so the screen notifies (a silent one says nothing)."""
        said = run.error if run.status is RunStatus.ERROR and run.error else run.output
        frame = self._event_frame(
            "routine.finished",
            RoutineFinished(
                routine.id, routine.name, run.status.value, run.conversation_id, said[:300]
            ),
        )
        for connection in self._connections:
            connection.put(frame)
        self._routines_changed()


def _wire(event: ConversationEvent, attached: Path) -> tuple[str, object]:
    """A conversation's event as the protocol's name and payload; `attached` is the home's
    attachments folder, where an attached file's path starts."""
    match event:
        case TurnStarted(text, attachments):
            rows = [_attachment(item, attached) for item in attachments]
            return "message.start", UserSaid(text, _said_kind(text), rows)
        case TextDelta(text):
            return "message.delta", Said(text)
        case ReasoningDelta(text):
            return "reasoning.delta", Said(text)
        case Steered(text, attachments):
            rows = [_attachment(item, attached) for item in attachments]
            return "message.steered", UserSaid(text, _said_kind(text), rows)
        case ToolStarted(call_id, name, arguments):
            return "tool.start", ToolStart(call_id, name, arguments)
        case ToolProgress(call_id, name, progress):
            return "tool.progress", ToolProgressPayload(call_id, name, _plain(progress))
        case InterimText(text):
            return "message.interim", Interim(text)
        case ToolFinished():
            return "tool.complete", ToolComplete(
                event.call_id,
                event.name,
                event.result,
                event.status.value,  # pyright: ignore[reportArgumentType]
                event.duration_s,
                event.spill_path,
                list(event.loaded),
            )
        case Compacted(before, after):
            text = f"The conversation was compacted from about {before:,} to {after:,} tokens."
            return "status.update", StatusUpdate("compacted", text)
        case TurnEnded():
            return "message.complete", MessageComplete(
                event.text,
                event.outcome.value,  # pyright: ignore[reportArgumentType]
                event.model_calls,
                _usd(event.cost_usd),
                event.error,
            )
        case TurnStopped(unsent):
            return "message.complete", MessageComplete("", "interrupted", unsent=list(unsent))
        case Titled(title):
            return "title.changed", TitleChanged(title)
        case Reviewed(actions, proposals, diffs):
            return "review.summary", ReviewSummary(
                event.text, list(actions), list(proposals), dict(diffs)
            )
        case SubagentChanged():
            return "subagent.update", _subagent_row(event)
        case BackgroundCommand():
            return "command.update", _command_row(event)
        case PreviewAsked(action, target, label):
            return f"preview.{action}", PreviewPayload(target, label)
        case FileSent(attachment):
            return "message.file", _attachment(attachment, attached)


def _said_kind(text: str) -> Literal["message", "notice", "terminal", "skill"]:
    """Who a user-side message is: a notice Atlas wrote, a terminal row, a skill the user
    invoked, or the user's words."""
    if is_notice_text(text):
        return "notice"
    if is_skill_text(text):
        return "skill"
    return "terminal" if is_terminal_text(text) else "message"


def _route_of(found: RouteView) -> SessionRouteRow:
    stops = [RouteStopRow(**dump(stop)) for stop in found.stops]
    return SessionRouteRow(found.instance_id, stops, found.enabled, found.compact)


def _accounts_of(listed: AccountList) -> ExecutorAccounts:
    rows = [AccountRow(**dump(view)) for view in listed.accounts]
    return ExecutorAccounts(listed.executor, rows, listed.can_add, listed.failover, listed.compact)


def _command_row(view: BackgroundCommand) -> CommandRow:
    return CommandRow(
        view.id,
        view.description,
        view.command,
        view.status,
        format_utc(view.started_at),
        view.exit_code,
        None if view.ended_at is None else format_utc(view.ended_at),
    )


def _subagent_row(view: SubagentChanged) -> SubagentRow:
    return SubagentRow(
        view.subagent_id,
        view.delegation_id,
        view.goal,
        view.model,
        view.status,
        view.started_at,
        view.last_tool,
        view.tools,
        view.ended_at,
        view.executor,
        view.decides,
        view.waiting,
        view.conversation_id,
        view.account,
        view.folder,
        view.session_id,
    )


def _plain(value: object) -> object:
    """A tool's progress as JSON values: a dataclass becomes its fields and its kind."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"kind": type(value).__name__, **dump(value)}
    if value is None or isinstance(value, str | int | float | bool | list | dict):
        return dump(cast("object", value))
    return str(value)


def _usd(cost: Decimal | None) -> str | None:
    return None if cost is None else str(cost)


def _row(info: ConversationInfo, running: bool) -> ConversationRow:
    return ConversationRow(
        info.id, info.title, info.source, info.started_at, info.updated_at, info.messages, running
    )


def _pairing_row(pairing: Pairing) -> PairingRow:
    return PairingRow(
        pairing.channel,
        pairing.user,
        pairing.name,
        pairing.code,
        format_utc(pairing.asked_at),
        pairing.approved,
    )


def _bot_refused(error: BotError) -> ProtocolError:
    return ProtocolError(NOT_FOUND if "no bot" in str(error) else INVALID_PARAMS, str(error))


def _proposal_row(item: OpenProposal) -> ProposalRow:
    proposal = item.proposal
    changes = [
        ProposalChange(operation.action.value, operation.content, operation.entry)  # pyright: ignore[reportArgumentType]
        for operation in proposal.operations
    ]
    return ProposalRow(
        proposal.id,
        proposal.target.value,  # pyright: ignore[reportArgumentType]
        changes,
        proposal.conversation_id,
        proposal.created_at,
        item.problem,
        proposal.bot,
    )


def _routine_row(routine: Routine, running: bool) -> RoutineRow:
    def text(moment: datetime | None) -> str | None:
        return None if moment is None else format_utc(moment)

    return RoutineRow(
        routine.id,
        routine.name,
        routine.schedule.display,
        routine.enabled,
        routine.prompt,
        routine.command,
        text(routine.next_run_at),
        text(routine.last_run_at),
        None if routine.last_status is None else routine.last_status.value,
        routine.created_by.value,
        running,
        routine.bot,
    )


def _run_row(run: Run) -> RunRow:
    return RunRow(
        run.number,
        format_utc(run.started_at),
        None if run.ended_at is None else format_utc(run.ended_at),
        run.status.value,
        run.lateness.value,
        run.conversation_id,
        run.output[:2_000],
        run.error,
    )


def _attachment(item: Attachment | StoredAttachment, attached: Path) -> AttachmentRow:
    kind = item.kind if isinstance(item, StoredAttachment) else item.kind.value
    path = attached / item.id / item.name
    return AttachmentRow(
        item.id,
        item.name,
        item.media_type,
        item.size,
        kind,  # pyright: ignore[reportArgumentType]
        str(path),
    )


def _message(message: StoredMessage, attached: Path) -> MessageRow:
    return MessageRow(
        message.number,
        message.role.value,
        message.kind.value,
        message.text,
        message.tool_name,
        message.created_at,
        message.active,
        message.reasoning,
        [CallRow(call.id, call.name, call.arguments) for call in message.calls],
        message.call_id,
        message.status,  # pyright: ignore[reportArgumentType]
        [_attachment(item, attached) for item in message.attachments],
    )


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _absolute(path: str) -> Path:
    """A path the app's file dialog gave: on the server's machine, and absolute, since the
    server's working folder is nothing the client knows."""
    if not path or not Path(path).is_absolute():
        raise ProtocolError(INVALID_PARAMS, f"{path!r} is not an absolute path.")
    return Path(path)


async def _maybe_await(value: object) -> None:
    """Await `value` when it is a coroutine; otherwise it was a plain call that already ran. For
    callbacks that may be sync or async, as the restart hooks of a stored secret are."""
    if asyncio.iscoroutine(value):
        await value


class _Desk:
    """The gateway as the desk of the executor instances: their requests become cards."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    async def ask(
        self, asker: InstanceChanged, questions: Sequence[ExecutorQuestion]
    ) -> ExecutorAnswers:
        return await self._gateway.executor_ask(asker, questions)

    async def approve(
        self, asker: InstanceChanged, permission: ExecutorPermission
    ) -> ExecutorVerdict:
        return await self._gateway.executor_approve(asker, permission)


def _instance_row(view: InstanceChanged) -> InstanceRow:
    return InstanceRow(**dump(view))


def _activity(event: InstanceActivity, attached: Path) -> InstanceActivityPayload:
    return InstanceActivityPayload(
        event.instance_id,
        event.kind,
        event.text,
        event.failed,
        event.call_id,
        event.tool,
        [_attachment(item, attached) for item in event.attachments],
    )


def _attached_at(attachments: Attachments, path: str) -> AttachmentRow | None:
    """The attachment a session's prompt names by its path (step 16i); `None` when it is not one
    of the home's, or is gone."""
    found = Path(path)
    kept = attachments.path(found.parent.name)
    if kept is None or kept.name != found.name:
        return None
    return _attachment(attachments.get(found.parent.name), attachments.folder)


def _usage_row(usage: ExecutorUsage) -> ExecutorUsageRow:
    return ExecutorUsageRow(
        usage.executor,
        True,
        [UsageWindowRow(**dump(window)) for window in usage.windows],
        usage.plan,
        usage.unavailable,
        usage.read_at,
        usage.extra_usage,
    )


def _executor_row(name: str, found: Detection, plugin: str, title: str) -> ExecutorRow:
    account = found.account
    return ExecutorRow(
        name=name,
        title=title,
        plugin=plugin,
        installed=found.installed,
        supported=found.supported,
        version=found.version,
        minimum=found.minimum,
        account=account.login if account is not None else "",
        plan=account.plan if account is not None else "",
        problem=found.problem,
        logged_in=account is not None and account.logged_in,
    )


type _ExecutorState = tuple[bool, str | None, tuple[tuple[str, str], ...]]


def _executor_states(plugins: Plugins) -> dict[str, _ExecutorState]:
    """Whether each plugin that brings an executor is on, what it was consented to, and its
    settings: one set from the terminal (`atlas plugins set`) is looked at again too."""
    states = plugins.store.states()
    return {
        view.name: (
            view.running,
            None if (state := states.get(view.name)) is None else state.consent,
            tuple(sorted(view.settings.items())),
        )
        for view in plugins.all()
        if view.manifest is not None and view.manifest.executors
    }


def _update_info(status: UpdateStatus) -> UpdateInfo:
    return UpdateInfo(
        supported=status.supported,
        available=status.available,
        kind=status.kind,
        reason=status.reason,
        current=status.current,
        target=status.target,
        behind=status.behind,
        commits=list(status.commits),
        commits_pt_br=list(status.commits_pt_br),
        dirty=status.dirty,
        error=status.error,
        unverified=status.unverified,
    )


def _update_ended(ended: HandedOff) -> UpdateEnded:
    return UpdateEnded(
        ok=ended.ok,
        before=ended.before,
        after=ended.after,
        commits=list(ended.commits),
        error=ended.error,
        log=ended.log,
        ended_at=ended.ended_at,
        reopen=ended.reopen,
    )


def _market_row(row: MarketRow) -> MarketplaceRow:
    return MarketplaceRow(
        name=row.name,
        kind=row.kind.value,
        title=row.title,
        summary=row.summary,
        author=row.author,
        license=row.license,
        tier=row.tier.value,
        version=row.version,
        state=row.state.value,
        shipped=row.shipped,
        update=row.update,
        image=row.image,
        source_url=row.source_url,
        problem=row.problem,
    )


def _market_page(page: MarketPage) -> MarketplacePage:
    return MarketplacePage(
        item=_market_row(page.row),
        readme=page.readme,
        picture_base=page.picture_base,
        link_base=page.link_base,
        needs=[MarketplaceNeed(need.kind, need.name, need.state) for need in page.needs],
        secrets=[
            MarketplaceSecretRow(secret.name, secret.description, secret.url, secret.stored)
            for secret in page.secrets
        ],
        screenshots=page.screenshots,
        homepage=page.homepage,
        entry=page.entry,
    )
