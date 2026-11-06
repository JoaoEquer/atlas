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
import re
import secrets
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast

import httpx2

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
    GitState,
    InstanceActivity,
    InstanceChanged,
    InstanceState,
    PreviewAsked,
    Reviewed,
    RoutineRunner,
    SubagentChanged,
    Titled,
    TurnStarted,
    TurnStopped,
    WorkbenchEvent,
    account_name,
    other_accounts,
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
    BACKUP_PREFIX,
    TRANSCRIPTIONS,
    Database,
    Home,
    HomeError,
    MemorySettings,
    PictureError,
    Schema,
    Settings,
    SettingsError,
    TrustSettings,
    backup_name,
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
    prune_backups,
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
from atlas.jobs import (
    AccountStore,
    DelegationError,
    JobsError,
    Routine,
    RoutineExecutor,
    Run,
    RunStatus,
    Scheduler,
)
from atlas.marketplace import Kind as MarketKind
from atlas.marketplace import Marketplace, MarketplaceError, MarketplaceStore, outside_sources
from atlas.marketplace import Page as MarketPage
from atlas.marketplace import Row as MarketRow
from atlas.marketplace import fetch as market_fetch
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
    LoadedReader,
    Pairing,
    PluginError,
    Plugins,
    PluginView,
    UpdateSource,
    official_plugins,
    refresh_official,
)
from atlas.plugins import Permission as ExecutorPermission
from atlas.plugins import Question as ExecutorQuestion
from atlas.plugins import Verdict as ExecutorVerdict
from atlas.secrets import MCP_SIGN_IN_FILE, SecretsError, Vault
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
    Activity,
    ApprovalParams,
    ApprovalResult,
    Archived,
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
    CheckpointList,
    CheckpointRow,
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
    DictationAudio,
    DictationList,
    DictationRef,
    DictationRow,
    DictationWrite,
    DiffFileRow,
    DiffLineRow,
    Empty,
    Events,
    EventsParams,
    ExecutorAccountParams,
    ExecutorAccounts,
    ExecutorApprovalParams,
    ExecutorApprovalResult,
    ExecutorChoiceRow,
    ExecutorCommandsParams,
    ExecutorConnector,
    ExecutorConnectors,
    ExecutorList,
    ExecutorModel,
    ExecutorModels,
    ExecutorQuestionParams,
    ExecutorQuestionResult,
    ExecutorQuestionRow,
    ExecutorRef,
    ExecutorRow,
    ExecutorUpdated,
    ExecutorUsageRow,
    FolderGit,
    FolderRef,
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
    McpList,
    McpServerRow,
    McpSignInPage,
    McpSignInParams,
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
    ProjectRow,
    ProjectSettings,
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
    Renamed,
    RenameParams,
    RequestCancel,
    ReviewSummary,
    RoutineExecutorRow,
    RoutineFinished,
    RoutineList,
    RoutineRef,
    RoutineRemoved,
    RoutineRow,
    RoutineSave,
    RoutinesChanged,
    RunList,
    RunRow,
    Said,
    ScoreboardRow,
    SearchHit,
    SearchParams,
    SearchResults,
    SecretParams,
    SecretResult,
    SendParams,
    SendResult,
    SessionContext,
    SessionDeleteParams,
    SessionFolderList,
    SessionFolderRow,
    SessionList,
    SessionListParams,
    SessionRead,
    SessionReadParams,
    SessionRow,
    SettingRow,
    SettingsList,
    SettingsWrite,
    SkillList,
    SkillRow,
    SlashCommandList,
    SlashCommandRow,
    Soul,
    SoulText,
    SpeechState,
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
    WorkbenchModeParams,
    WorkbenchPrompted,
    WorkbenchPromptParams,
    WorkbenchStartParams,
    WorkbenchSwitchParams,
    WorkbenchUseAccountParams,
    WorkspaceCommit,
    WorkspaceCreate,
    WorkspaceDelete,
    WorkspaceDiff,
    WorkspaceList,
    WorkspaceListParams,
    WorkspacePushed,
    WorkspaceRef,
    WorkspaceRestore,
    WorkspaceRow,
    WorktreeParams,
    dump,
    read,
)
from atlas.server.replay import Replay
from atlas.server.updates import LOOK_SECONDS, Newest, UpdateError, newer, newest
from atlas.server.updates import command as update_command
from atlas.server.updates import run as run_update
from atlas.server.updates import shown as shown_command
from atlas.skills import SkillLibrary, SkillUsage, changed, read_manifest, retire_bundled
from atlas.speech import Dictation, Dictations, ModelStore, Speech, SpeechError
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
from atlas.tools import Connection as OfferedConnection
from atlas.trust import (
    ApprovalAnswer,
    ApprovalRequest,
    Approver,
    Choice,
    detect_dangerous_command,
    detect_hardline_command,
)
from atlas.trust import Outcome as ApprovalOutcome
from atlas.workspaces import Project, Workspace, WorkspaceError, Workspaces, WorkspaceState

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
#: The most conversations the list's search returns (step 9e).
SEARCH_MOST = 100
#: How much of a bot's last message its row shows.
PREVIEW_CHARS = 120
#: The conversations' table, as a backup's manifest counts it for the import dialog.
_THREADS_TABLE = "conversations_threads"
#: How long after the host's update ended a server that starts still tells its clients.
UPDATE_NEWS_SECONDS = 600.0
#: The plugin that keeps the harnesses' other accounts (step 22), its own file in its data folder,
#: and the one an update leaves there with what Atlas kept before.
ACCOUNTS_PLUGIN = "accounts"
ACCOUNTS_BOOK = "accounts.json"
ACCOUNTS_FROM_ATLAS = "from-atlas.json"
#: The voice plugin, and the file in its data folder that says its provider was kept for writing
#: down recordings (step 26).
VOICE_PLUGIN = "voice"
VOICE_MOVED = "transcription-moved"
#: Where the voice plugin kept the dictations before step 26, in its data folder.
VOICE_DICTATIONS = "dictations"
#: The longest dictation a client may send, in bytes: a few minutes of speech.
DICTATION_BYTES = 10 * 1024 * 1024

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
        update_transport: httpx2.AsyncBaseTransport | None = None,
        speech: Speech | None = None,
    ) -> None:
        self.replay = Replay()
        #: Where each executor's newest version is published and how its install updates, as
        #: its plugin says; the newest version of each, read at most once a day; the transport
        #: those reads take (a test's own).
        self._update_sources: dict[str, UpdateSource] = {}
        self._newest = Newest()
        self._update_transport = update_transport
        self._looking: asyncio.Task[None] | None = None
        #: The executors updating now, at the user's click.
        self._updating: set[str] = set()
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
        #: The last moment the home was seen working or with a client (step 20).
        self._idle_since: datetime | None = None
        #: The MCP servers as the clients were last told, so `mcp.changed` says only a change.
        self._mcp_shown = McpList([], None)
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
        #: Writing down recordings (step 26), the model at the root for every home: Atlas's own
        #: reader of audio, on every channel.
        self.speech = speech or Speech(
            ModelStore(Home(home_root(home.root)).speech_folder),
            lambda: self.assistant.settings.voice.transcription,
            vault.get,
            address=self._provider_address,
        )
        self.speech.on_changed = self._speech_changed
        #: The home's workspaces (step 21), each a worktree on a branch of its own.
        self.workspaces = Workspaces(
            home.worktrees_folder, Database(home.database_file), clock=clock
        )
        #: The setup scripts running, kept so none is collected before it ends.
        self._preparing: set[asyncio.Task[None]] = set()
        #: What the user dictated in this home (step 26), each recording with its words.
        self.dictations = Dictations(home.dictations_folder)
        self.assistant.use_reader(LoadedReader("atlas", "audio", self.speech.transcribe))
        self.assistant.use_workspaces(_TaskWorkspaces(self))
        #: Housekeeping's rounds, so the connections are read every few.
        self._houses = 0
        #: Whether each offered connection was logged in when its plugin's status was last read.
        self._logged_in: dict[str, bool] = {}
        self.assistant.use_connections(self.connections)
        self.plugins = Plugins(home, Database(home.database_file), vault, clock=clock)
        self.plugins.host = AssistantPluginHost(self.assistant, self._told_accounts_changed)
        #: The marketplace (step 16s), installing into this home.
        self.marketplace = Marketplace(
            home,
            MarketplaceStore(home.marketplace_folder, settings.marketplace),
            self.plugins,
            SkillLibrary(home.skills_folder, SkillUsage(Database(home.database_file))),
            stored=self._stored_names,
            clock=clock,
            sources=outside_sources(settings.marketplace, market_fetch, home.marketplace_folder),
        )
        #: `mcp.json` as last seen (step 16s): read again once it changes, by the marketplace or
        #: by hand.
        self._mcp_stamp: tuple[int, int] | None = None
        self._sign_in_stamp: tuple[int, int] | None = None
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
        #: The other accounts went to their plugin in this run, or there were none (step 22).
        self._accounts_moved = False
        #: What the accounts plugin's changes start, told to the screens in the background.
        self._accounts_telling: set[asyncio.Task[None]] = set()
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
            "mcp.list": self._mcp_list,
            "mcp.sign_in": self._mcp_sign_in,
            "skill.list": self._skill_list,
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
            "conversation.search": self._search,
            "conversation.rename": self._rename,
            "conversation.archive": self._archive,
            "conversation.unarchive": self._unarchive,
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
            "speech.status": self._speech_status,
            "speech.download": self._speech_download,
            "speech.decline": self._speech_decline,
            "workspace.list": self._workspace_list,
            "workspace.create": self._workspace_create,
            "workspace.archive": self._workspace_archive,
            "workspace.unarchive": self._workspace_unarchive,
            "workspace.delete": self._workspace_delete,
            "workspace.diff": self._workspace_diff,
            "workspace.checkpoints": self._workspace_checkpoints,
            "workspace.restore": self._workspace_restore,
            "workspace.commit": self._workspace_commit,
            "workspace.merge": self._workspace_merge,
            "workspace.push": self._workspace_push,
            "workspace.project": self._workspace_project,
            "workspace.set_project": self._workspace_set_project,
            "dictation.write": self._dictation_write,
            "dictation.list": self._dictation_list,
            "dictation.audio": self._dictation_audio,
            "dictation.again": self._dictation_again,
            "dictation.delete": self._dictation_delete,
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
            "server.activity": self._activity,
            "routine.save": self._routine_save,
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
            "workbench.mode": self._workbench_mode,
            "workbench.git": self._workbench_git,
            "workbench.worktree": self._workbench_worktree,
            "workbench.context": self._workbench_context,
            "workbench.model": self._workbench_model,
            "workbench.atlas_answers": self._workbench_atlas_answers,
            "workbench.duplicate": self._workbench_duplicate,
            "workbench.folder": self._workbench_folder,
            "workbench.account": self._workbench_account,
            "workbench.use_account": self._workbench_use_account,
            "workbench.account_switch": self._workbench_account_switch,
            "workbench.stop": self._workbench_stop,
            "executor.detect": self._executor_detect,
            "executor.update": self._executor_update,
            "executor.folders": self._executor_folders,
            "executor.sessions": self._executor_sessions,
            "executor.models": self._executor_models,
            "executor.connectors": self._executor_connectors,
            "executor.usage": self._executor_usage,
            "executor.commands": self._executor_commands,
            "executor.account": self._executor_account,
            "executor.accounts": self._executor_accounts,
            "executor.delete_session": self._executor_delete_session,
            "executor.session": self._executor_session,
            "subagent.session": self._subagent_session,
        }
        runner = RoutineRunner(self.assistant)
        self.scheduler = Scheduler(
            self.assistant.routines,
            runner,
            clock=clock,
            on_finished=self._routine_finished,
            prober=runner,
        )

    @property
    def handled(self) -> frozenset[str]:
        """The methods this gateway serves; the protocol must declare exactly these."""
        return frozenset(self._handlers)

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.assistant.on_bots_changed = self._bots_changed
        self.assistant.on_mcp_changed = self._mcp_changed
        self.assistant.start()
        abandoned = self.scheduler.start()
        if abandoned:
            _logger.warning("Runs a previous Atlas left running are now unknown: %d.", abandoned)
        self._housekeeping = asyncio.create_task(self._keep_house())
        # Atlas's own plugins come to this version first, and those used before step 26, when
        # Atlas shipped them inside itself, are installed (atlas.plugins.official).
        refreshed = await asyncio.to_thread(refresh_official, self.plugins)
        if refreshed:
            _logger.info("Atlas's own plugins were put in place: %s.", ", ".join(refreshed))
        await asyncio.to_thread(self._skills_to_plugins)
        await asyncio.to_thread(self._voice_to_speech)
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
                programs=[*parts.programs, *programs],
                agents=agents,
                monitors=monitors,
                slash=parts.commands,
                readers=parts.readers,
                probes=parts.probes,
            )
            self._calls = {(call.plugin, call.name): call for call in parts.calls}
            self._plugin_providers(await asyncio.to_thread(self.plugins.providers))
        await self.read_connections()

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
            # Given here (a test, a proof): they stay as given, and are looked at all the same,
            # with the other accounts of the plugin that brings them.
            async with self._executors_lock:
                await asyncio.to_thread(self._move_accounts)
                accounts = await self.plugins.accounts()
                self.assistant.set_accounts(None if accounts is None else accounts.accounts)
                for name in self.assistant.executors():
                    given = self.assistant.executor(name)
                    if given is not None:
                        self.detections[name] = await self._look(given)
                        self._executor_names[name] = ("", given.title)
                        self.detections.update(await self._account_detections(name, given))
                        source = _update_source(given)
                        if source is not None:
                            self._update_sources[name] = source
            self._look_for_updates()
            return
        async with self._executors_lock:
            found: dict[str, Executor] = {}
            detections: dict[str, Detection] = {}
            plugins: set[str] = set()
            offered: set[str] = set()
            sources: dict[str, UpdateSource] = {}
            self._executors_seen = await asyncio.to_thread(_executor_states, self.plugins)
            # The other accounts first: the executors on them are rows of their own (step 22).
            await asyncio.to_thread(self._move_accounts)
            accounts = await self.plugins.accounts()
            self.assistant.set_accounts(None if accounts is None else accounts.accounts)
            for loaded in await self.plugins.executors():
                executor, detection = await self._detect(loaded)
                plugins.add(loaded.plugin)
                detections[loaded.name] = detection
                source = None if executor is None else _update_source(executor)
                if source is not None and detection.installed:
                    sources[loaded.name] = source
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
            self._update_sources = sources
        self._look_for_updates()
        # Only a change is told: each telling reads the plugins and the executors again on every
        # screen, and the executors tab now looks whenever it shows.
        if before != (detections, plugins, offered):
            self.plugins_changed()

    def _skills_to_plugins(self) -> None:
        """Skills Atlas shipped that a plugin of Atlas's brings now (step 26: `github` and the
        `gws-*` ones): a home that had them gets that plugin installed and turned on, as the
        update's own doing, since the skills were on for it, and their copies leave the library,
        archived when untouched, the user's own when changed. A plugin the user decided on is left
        as they decided."""
        library = SkillLibrary(self._home.skills_folder)
        shipped = read_manifest(library.folder)
        decided = self.plugins.store.states()
        for name, found in official_plugins().items():
            manifest = found.manifest
            had: set[str] = set(manifest.skills) & shipped.keys() if manifest is not None else set()
            if not had or manifest is None:
                continue
            if name not in decided:
                try:
                    self.plugins.enable(name, manifest.fingerprint())
                except PluginError as error:
                    _logger.warning(
                        "A plugin that brings Atlas's skills was not turned on: %s", error
                    )
                    continue
            archived, kept = retire_bundled(library, had)
            _logger.info(
                "Skills Atlas shipped come from a plugin now.",
                extra={"plugin": name, "archived": archived, "kept": kept},
            )

    def _voice_to_speech(self) -> None:
        """Once, a home whose voice plugin wrote down recordings before step 26 keeps its
        dictations, now the home's, and has recordings written down by the same provider: its
        `provider` becomes `[voice] transcription`, unless the user set that already. Without it,
        the update would move them to this computer's model, which asks for a download first."""
        kept = self.plugins.data_folder(VOICE_PLUGIN) / VOICE_DICTATIONS
        if kept.is_dir() and not self._home.dictations_folder.exists():
            # The dictations the voice plugin kept are the home's now, where the page reads them.
            shutil.move(kept, self._home.dictations_folder)
            _logger.info("The voice plugin's dictations moved to the home's.")
        marker = self.plugins.data_folder(VOICE_PLUGIN) / VOICE_MOVED
        if marker.exists():
            return
        try:
            view = self.plugins.view(VOICE_PLUGIN)
        except PluginError:
            return
        if not view.enabled:
            return
        provider = view.settings.get("provider", "").strip().lower() or "openai"
        written = {
            (value.setting.section, value.setting.key): value
            for value in editable_values(self._home.root, self._environ)
        }
        chosen = written[("voice", "transcription")]
        if chosen.written is None and chosen.variable is None and provider in TRANSCRIPTIONS:
            try:
                settings = edit_settings(
                    self._home.root, self._environ, {("voice", "transcription"): provider}
                )
            except SettingsError as error:
                _logger.warning("The voice's provider could not be kept: %s", error)
                return
            self.assistant.use_settings(settings)
            _logger.info("Recordings stay written down by the voice's provider.")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")

    def _move_accounts(self) -> None:
        """Once, the other accounts Atlas kept itself until step 22 go to the accounts plugin,
        which is turned on for whoever had any, as the update's own doing (decided with the user
        on 03/11/2026: it is what they used): its data folder gets them, and no account's folder
        is touched. A home that never added an account, or whose plugin already keeps its own,
        is left as it is."""
        if self._accounts_moved:
            return
        self._accounts_moved = True
        folder = self.plugins.data_folder(ACCOUNTS_PLUGIN)
        if (folder / ACCOUNTS_BOOK).exists() or (folder / ACCOUNTS_FROM_ATLAS).exists():
            return
        exported = AccountStore(Database(self._home.database_file)).export()
        if exported is None:
            return
        try:
            view = self.plugins.view(ACCOUNTS_PLUGIN)
        except PluginError:
            _logger.warning("The accounts plugin is not here: the other accounts stay unread.")
            return
        folder.mkdir(parents=True, exist_ok=True)
        text = json.dumps(exported, indent=2, ensure_ascii=False) + "\n"
        (folder / ACCOUNTS_FROM_ATLAS).write_text(text, encoding="utf-8")
        if view.manifest is not None and not view.enabled:
            try:
                self.plugins.enable(ACCOUNTS_PLUGIN, view.manifest.fingerprint())
            except PluginError as error:
                _logger.warning("The accounts plugin could not be turned on: %s", error)
                return
        _logger.info("The other accounts went to the accounts plugin.")

    async def _account_detections(self, name: str, executor: Executor) -> dict[str, Detection]:
        """The detection of `name`'s executor on each of its other accounts (step 16h), each one
        row more, named as the screens name it: its login is that account's folder's."""
        if not executor.capabilities().account_folders:
            return {}
        plugin = self._executor_names.get(name, ("", executor.title))[0]
        kept = await asyncio.to_thread(other_accounts, self.assistant.accounts, name)
        found: dict[str, Detection] = {}
        for account in kept:
            named = account_name(name, account.id)
            on = self.assistant.executor(named)
            if on is not None:
                found[named] = await self._look(on)
                self._executor_names[named] = (plugin, f"{executor.title} · {account.nickname}")
                self._account_rows[named] = (name, account.id, account.nickname)
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

    def _look_for_updates(self) -> None:
        """Read, in the background, the newest version of each executor whose last read is a day
        old: never on the way to a start, and one read at a time."""
        now = self._clock()
        due = [s for s in self._update_sources.values() if self._newest.due(s, now)]
        if due and (self._looking is None or self._looking.done()):
            self._looking = asyncio.create_task(self._read_newest(due))

    async def _read_newest(self, sources: Sequence[UpdateSource]) -> None:
        changed = False
        async with httpx2.AsyncClient(
            timeout=LOOK_SECONDS, transport=self._update_transport, follow_redirects=True
        ) as client:
            for source in sources:
                try:
                    found = await newest(source, client)
                except UpdateError as error:
                    # Read again a day later, not at every minute's housekeeping.
                    _logger.info("A harness's newest version was not read: %s", error)
                    found = self._newest.get(source)
                changed = changed or found != self._newest.get(source)
                self._newest.keep(source, found, self._clock())
        if changed:
            self.plugins_changed()

    def _update_of(self, name: str) -> tuple[str, tuple[str, ...]] | None:
        """The newer version of `name` and the command that installs it, when there is one."""
        source = self._update_sources.get(name)
        found = self.detections.get(name)
        if source is None or found is None:
            return None
        version = self._newest.get(source)
        argv = update_command(source, version) if newer(version, found.version) else None
        return None if argv is None else (version, argv)

    async def close(self) -> None:
        for task in (self._housekeeping, self._plugin_watch, self._update_news, self._looking):
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
        conversations = self.assistant.conversations
        found, more = conversations.page(limit, after, archived=params.archived)
        running = self.assistant.running
        rows = [_row(info, running(info.id)) for info in found]
        return ConversationList(
            rows, "" if more is None else more.text, conversations.archived_count()
        )

    async def _search(self, connection: Connection, params: SearchParams) -> SearchResults:
        limit = max(1, min(params.limit, SEARCH_MOST))
        found, more = self.assistant.conversations.find(params.query, limit)
        running = self.assistant.running
        hits = [
            SearchHit(
                _row(hit.conversation, running(hit.conversation.id)),
                list(hit.title),
                list(hit.snippet),
                hit.said_by,
            )
            for hit in found
        ]
        return SearchResults(hits, more)

    async def _rename(self, connection: Connection, params: RenameParams) -> Renamed:
        title = self.assistant.conversations.rename(params.conversation_id, params.title)
        if not title:
            raise ProtocolError(INVALID_PARAMS, "params.title holds no words.")
        self.conversations_changed(params.conversation_id, title=title)
        return Renamed(title)

    async def _archive(self, connection: Connection, params: ConversationRef) -> Archived:
        # A turn that runs may wait for the user, and a card waiting there never hides in the
        # Archived section (decided with the user): it is archived once the turn ends.
        if self.assistant.running(params.conversation_id):
            raise ProtocolError(
                INVALID_PARAMS, "A turn of this conversation runs: archive it once the turn ends."
            )
        archived_at = self.assistant.conversations.archive(params.conversation_id)
        self.conversations_changed(params.conversation_id)
        return Archived(archived_at)

    async def _unarchive(self, connection: Connection, params: ConversationRef) -> Empty:
        if self.assistant.conversations.unarchive(params.conversation_id):
            self.conversations_changed(params.conversation_id)
        return Empty()

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
        blocked always and what is dangerous both ask, the user deciding. Off the event loop:
        reading a long block as shell takes its time."""
        return await asyncio.to_thread(_command_check, params.command)

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

    async def _speech_status(self, connection: Connection, params: Empty) -> SpeechState:
        return await asyncio.to_thread(self._speech_now)

    async def _speech_download(self, connection: Connection, params: Empty) -> SpeechState:
        self._owner(connection)
        self.speech.download()
        return await asyncio.to_thread(self._speech_now)

    async def _speech_decline(self, connection: Connection, params: Empty) -> SpeechState:
        self._owner(connection)
        await asyncio.to_thread(self.speech.decline)
        return await asyncio.to_thread(self._speech_now)

    def _provider_address(self, provider: str) -> str | None:
        """Where Atlas's provider of this name is reached, as its model catalog says."""
        try:
            return self.assistant.models.catalog.provider(provider).base_url
        except ModelsError:
            return None

    def _speech_now(self) -> SpeechState:
        return SpeechState(**asdict(self.speech.status()))

    def _speech_changed(self) -> None:
        """Tell every client where the speech model is: its download moved, or a recording
        waited for it."""
        frame = self._event_frame("speech.changed", self._speech_now())
        for connection in self._connections:
            connection.put(frame)

    async def _workspace_list(
        self, connection: Connection, params: WorkspaceListParams
    ) -> WorkspaceList:
        folder = Path(params.folder) if params.folder else None
        rows = await self._workspaces(self.workspaces.listed, folder)
        return WorkspaceList([await asyncio.to_thread(self._workspace_row, row) for row in rows])

    async def _workspace_create(
        self, connection: Connection, params: WorkspaceCreate
    ) -> WorkspaceRow:
        self._owner(connection)
        made = await self._new_workspace(Path(params.folder), params.name, params.base)
        return await asyncio.to_thread(self._workspace_row, made)

    async def _workspace_archive(
        self, connection: Connection, params: WorkspaceRef
    ) -> WorkspaceRow:
        self._owner(connection)
        done = await self._workspaces(self.workspaces.archive, params.id)
        self._workspaces_changed()
        return await asyncio.to_thread(self._workspace_row, done)

    async def _workspace_unarchive(
        self, connection: Connection, params: WorkspaceRef
    ) -> WorkspaceRow:
        self._owner(connection)
        done = await self._workspaces(self.workspaces.unarchive, params.id)
        self._workspaces_changed()
        return await asyncio.to_thread(self._workspace_row, done)

    async def _workspace_delete(self, connection: Connection, params: WorkspaceDelete) -> Empty:
        self._owner(connection)
        await self._workspaces(lambda: self.workspaces.delete(params.id, force=params.force))
        self._workspaces_changed()
        return Empty()

    async def _workspace_diff(self, connection: Connection, params: WorkspaceRef) -> WorkspaceDiff:
        self._owner(connection)
        files, more = await self._workspaces(self.workspaces.diff, params.id)
        uncommitted, ahead = await self._workspaces(self.workspaces.status, params.id)
        return WorkspaceDiff(
            [
                DiffFileRow(
                    found.path,
                    found.status,
                    found.added,
                    found.removed,
                    found.binary,
                    found.truncated,
                    [DiffLineRow(line.kind, line.text, line.old, line.new) for line in found.lines],
                )
                for found in files
            ],
            more,
            uncommitted,
            ahead,
        )

    async def _workspace_commit(
        self, connection: Connection, params: WorkspaceCommit
    ) -> WorkspaceRow:
        self._owner(connection)
        done = await self._workspaces(self.workspaces.commit, params.id, params.message)
        self._workspaces_changed()
        return await asyncio.to_thread(self._workspace_row, done)

    async def _workspace_merge(self, connection: Connection, params: WorkspaceRef) -> WorkspaceRow:
        """Into the project's own branch: the app asked the user first."""
        self._owner(connection)
        done = await self._workspaces(self.workspaces.merge, params.id)
        self._workspaces_changed()
        return await asyncio.to_thread(self._workspace_row, done)

    async def _workspace_push(
        self, connection: Connection, params: WorkspaceRef
    ) -> WorkspacePushed:
        """Outward, to the project's remote: the app asked the user first."""
        self._owner(connection)
        said = await self._workspaces(self.workspaces.push, params.id)
        self._workspaces_changed()
        return WorkspacePushed(said)

    async def _workspace_checkpoints(
        self, connection: Connection, params: WorkspaceRef
    ) -> CheckpointList:
        self._owner(connection)
        found = await self._workspaces(self.workspaces.checkpoints, params.id)
        return CheckpointList([CheckpointRow(k.commit, k.label, k.created_at) for k in found])

    async def _workspace_restore(
        self, connection: Connection, params: WorkspaceRestore
    ) -> WorkspaceRow:
        """Refused while a session works in the workspace: its harness would go on over files
        that changed under it."""
        self._owner(connection)
        workspace = await self._workspaces(self.workspaces.get, params.id)
        inside = Path(workspace.folder).resolve()
        for run in self.assistant.workbench.runs():
            busy = run.state in (InstanceState.STARTING, InstanceState.WORKING)
            if busy and run.spec.folder.resolve().is_relative_to(inside):
                raise ProtocolError(
                    INVALID_PARAMS,
                    f"A session works in {workspace.name} now: stop it, then go back.",
                )
        done = await self._workspaces(self.workspaces.restore, params.id, params.commit)
        self._workspaces_changed()
        return await asyncio.to_thread(self._workspace_row, done)

    async def _workspace_project(self, connection: Connection, params: FolderRef) -> ProjectRow:
        found = await self._workspaces(self.workspaces.project_of, Path(params.folder))
        if found is None:
            raise ProtocolError(INVALID_PARAMS, f"{params.folder} is not in a git repository.")
        return _project_row(found)

    async def _workspace_set_project(
        self, connection: Connection, params: ProjectSettings
    ) -> ProjectRow:
        """The setup script is the owner's own command, typed in the app, as the box's `!` is."""
        self._owner(connection)
        done = await self._workspaces(
            lambda: self.workspaces.set_project(
                Path(params.folder),
                setup_script=params.setup_script,
                ports_start=params.ports_start,
                ports_size=params.ports_size,
            )
        )
        return _project_row(done)

    async def task_workspace(self, folder: Path, name: str) -> tuple[Path, str]:
        """A workspace for a delegated task (step 21), made as the app's are and prepared before
        the task starts in it: its folder and its branch."""
        try:
            made = await asyncio.to_thread(self.workspaces.create, folder, name)
        except WorkspaceError as error:
            raise DelegationError(f"The workspace could not be made: {error}") from error
        self._workspaces_changed()
        if made.state is WorkspaceState.PREPARING:
            await self._prepare(made.id)
            made = await asyncio.to_thread(self.workspaces.get, made.id)
        if made.state is WorkspaceState.SETUP_FAILED:
            raise DelegationError(
                f"The workspace {made.branch} was made at {made.folder}, but its setup script "
                f"failed: {made.setup_output.strip()[-500:]} It is kept for the user."
            )
        return Path(made.folder), made.branch

    async def task_workspace_changes(self, folder: Path) -> str:
        """What the workspace at `folder` changed since it was made; empty when none is there."""
        found = await asyncio.to_thread(self.workspaces.found_at, folder)
        if found is None:
            return ""
        return await asyncio.to_thread(self.workspaces.changes, found.id)

    async def task_workspace_finish(self, folder: Path, title: str, *, draft: bool) -> str:
        """A routine's run's workspace once the run ended (step 25): archived when it changed
        nothing; otherwise kept and, with `draft`, committed, sent and opened as a draft pull
        request by the GitHub plugin. What became of it, in a line."""
        found = await asyncio.to_thread(self.workspaces.found_at, folder)
        if found is None:
            return ""
        changes = await asyncio.to_thread(self.workspaces.changes, found.id)
        uncommitted, ahead = await asyncio.to_thread(self.workspaces.status, found.id)
        try:
            if not uncommitted and not ahead:
                await asyncio.to_thread(self.workspaces.archive, found.id)
                return f"Its workspace {found.branch} changed nothing, and was archived."
            if not draft:
                return f"Its changes are in the workspace {found.branch}: {changes}"
            call = self._calls.get(("github", "open_pull_request"))
            if call is None:
                return (
                    f"Its changes are in the workspace {found.branch}: {changes} No draft pull "
                    "request was opened: the GitHub plugin is not on."
                )
            if uncommitted:
                await asyncio.to_thread(self.workspaces.commit, found.id, title)
            await asyncio.to_thread(self.workspaces.push, found.id)
            opened = await call.run(
                {
                    "folder": found.folder,
                    "branch": found.branch,
                    "base": found.base,
                    "title": title,
                    "draft": True,
                }
            )
        except Exception as error:  # noqa: BLE001 - git's or the plugin's failure, said in the run
            return f"Its changes are in the workspace {found.branch}, but {error}"
        finally:
            self._workspaces_changed()
        said = cast("dict[str, object]", opened) if isinstance(opened, dict) else {}
        url = str(said.get("url") or "")
        return f"Its changes went to a draft pull request: {url}".strip()

    async def _new_workspace(self, folder: Path, name: str, base: str) -> Workspace:
        """A workspace made, and its setup script run in the background, every client told."""
        made = await self._workspaces(self.workspaces.create, folder, name, base or None)
        self._workspaces_changed()
        if made.state is WorkspaceState.PREPARING:
            task = asyncio.create_task(self._prepare(made.id))
            self._preparing.add(task)
            task.add_done_callback(self._preparing.discard)
        return made

    async def _prepare(self, workspace_id: str) -> None:
        try:
            await asyncio.to_thread(self.workspaces.prepare, workspace_id)
        except WorkspaceError:
            _logger.warning("A workspace could not be prepared.", exc_info=True)
        self._workspaces_changed()

    async def _workspaces[**P, T](
        self, call: Callable[P, T], *args: P.args, **kwargs: P.kwargs
    ) -> T:
        try:
            return await asyncio.to_thread(call, *args, **kwargs)
        except WorkspaceError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error

    def _workspace_row(self, workspace: Workspace) -> WorkspaceRow:
        environment = self.workspaces.environment(workspace)
        common = Path(workspace.project)
        return WorkspaceRow(
            id=workspace.id,
            project_root=str(common.parent if common.name == ".git" else common),
            name=workspace.name,
            branch=workspace.branch,
            base=workspace.base,
            folder=workspace.folder,
            state=workspace.state.value,
            created_at=workspace.created_at,
            ports=environment.get("ATLAS_WORKSPACE_PORTS", ""),
            setup_output=workspace.setup_output,
        )

    def _workspaces_changed(self) -> None:
        frame = self._event_frame("workspaces.changed", Empty())
        for connection in self._connections:
            connection.put(frame)

    async def _dictation_write(
        self, connection: Connection, params: DictationWrite
    ) -> DictationRow:
        """Kept before it is written down: a failed transcription loses no speech."""
        self._owner(connection)
        try:
            data = base64.b64decode(params.audio, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ProtocolError(INVALID_PARAMS, "The recording is not base64.") from error
        if not data:
            raise ProtocolError(INVALID_PARAMS, "The recording is empty.")
        if len(data) > DICTATION_BYTES:
            raise ProtocolError(
                INVALID_PARAMS, "The recording is too long to dictate; record a shorter one."
            )
        kept = await asyncio.to_thread(
            self.dictations.keep, data, params.media_type or "audio/webm", params.where
        )
        return _dictation_row(await self._write_down(kept))

    async def _dictation_list(self, connection: Connection, params: Empty) -> DictationList:
        listed = await asyncio.to_thread(self.dictations.listed)
        return DictationList([_dictation_row(dictation) for dictation in listed])

    async def _dictation_audio(
        self, connection: Connection, params: DictationRef
    ) -> DictationAudio:
        dictation = self._dictation(params.id)
        data = await asyncio.to_thread(self.dictations.audio, dictation)
        return DictationAudio(base64.b64encode(data).decode("ascii"), dictation.media_type)

    async def _dictation_again(self, connection: Connection, params: DictationRef) -> DictationRow:
        self._owner(connection)
        return _dictation_row(await self._write_down(self._dictation(params.id)))

    async def _dictation_delete(self, connection: Connection, params: DictationRef) -> Empty:
        self._owner(connection)
        await asyncio.to_thread(self.dictations.delete, self._dictation(params.id).id)
        self._dictations_changed()
        return Empty()

    def _dictation(self, dictation_id: str) -> Dictation:
        try:
            return self.dictations.get(dictation_id)
        except SpeechError as error:
            raise ProtocolError(NOT_FOUND, str(error)) from error

    async def _write_down(self, dictation: Dictation) -> Dictation:
        """The dictation with what it said, or why it was not written down; every client told."""
        try:
            text = await self.speech.transcribe(
                self.dictations.path(dictation), dictation.media_type
            )
        except SpeechError as error:
            done = await asyncio.to_thread(self.dictations.written, dictation, "", str(error))
        else:
            done = await asyncio.to_thread(self.dictations.written, dictation, text)
        self._dictations_changed()
        return done

    def _dictations_changed(self) -> None:
        frame = self._event_frame("dictations.changed", Empty())
        for connection in self._connections:
            connection.put(frame)

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
            ("memory", "user_limit"): str(MemorySettings().user_limit),
            ("memory", "memory_limit"): str(MemorySettings().memory_limit),
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
        plugin that is on; what it raises reaches the screen as the error's message. The owner's
        app only, never a channel's bridge: a plugin's call runs with the user's permissions."""
        self._owner(connection)
        found = self._calls.get((params.plugin, params.name))
        if found is None:
            # A screen may call while the plugins still load, at start or after a save: the call
            # is looked for again once they are loaded.
            if self._plugins_start is not None and not self._plugins_start.done():
                await asyncio.wait({self._plugins_start})
            async with self._plugins_lock:
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

    # -- the Extensions screen's MCP servers and skills -------------------------------------------

    async def _mcp_list(self, connection: Connection, params: Empty) -> McpList:
        return self._mcp_servers()

    async def _mcp_sign_in(self, connection: Connection, params: McpSignInParams) -> McpSignInPage:
        try:
            return McpSignInPage(await self.assistant.mcp_sign_in(params.name))
        except ValueError as error:
            raise ProtocolError(INVALID_PARAMS, str(error)) from error

    def _mcp_servers(self) -> McpList:
        client = self.assistant.mcp
        if client is None:
            return McpList([], "MCP servers are left out here: the mcp extra is not installed.")
        rows = [
            McpServerRow(
                name=status.name,
                transport=status.transport.value,
                plugin=status.plugin,
                state=status.state.value,
                tools=status.tools,
                problem=status.problem,
                login=_login_command(status.name) if status.signs_in else None,
            )
            for status in client.status()
        ]
        return McpList(rows, client.file_problem)

    def _mcp_changed(self) -> None:
        """Tell every client when what `mcp.list` gives changed, and only then: the toolset is
        rebuilt more often than a server changes how it stands."""
        shown = self._mcp_servers()
        if shown == self._mcp_shown:
            return
        self._mcp_shown = shown
        frame = self._event_frame("mcp.changed", Empty())
        for connection in self._connections:
            connection.put(frame)

    async def _skill_list(self, connection: Connection, params: Empty) -> SkillList:
        def read() -> list[SkillRow]:
            shipped = read_manifest(self._home.skills_folder)
            library = SkillLibrary(self._home.skills_folder)
            rows: list[SkillRow] = []
            for skill in self.assistant.skills():
                plugin, _, _ = skill.name.rpartition(":")
                source: Literal["atlas", "home", "plugin"]
                if plugin:
                    source = "plugin"
                elif changed(library, skill.name, shipped) is False:
                    source = "atlas"
                else:
                    source = "home"
                rows.append(
                    SkillRow(
                        name=skill.name,
                        description=skill.description,
                        source=source,
                        plugin=plugin,
                        user_invocable=skill.user_invocable,
                    )
                )
            return sorted(rows, key=lambda row: row.name)

        return SkillList(await asyncio.to_thread(read))

    # -- the marketplace (step 16s) ---------------------------------------------------------------

    async def _marketplace_list(
        self, connection: Connection, params: MarketplaceListParams
    ) -> MarketplaceList:
        listing = await self._market_call(
            self.marketplace.listing, refresh=params.refresh, query=params.query
        )
        return MarketplaceList(
            name=listing.name,
            description=listing.description,
            items=[_market_row(row) for row in listing.rows],
            read_at=format_utc(listing.read_at) if listing.read_at is not None else None,
            problem=listing.problem,
            source_problems=listing.source_problems,
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
        did, and its servers follow without a restart. The MCP sign-ins too: a server left out for
        want of one connects once the user signed in from a terminal (`atlas mcp login`)."""
        stamp = _stamp(self._home.mcp_file)
        if self._mcp_stamp is None:
            self._mcp_stamp = stamp
        elif stamp != self._mcp_stamp:
            self._mcp_stamp = stamp
            await self.assistant.reload_mcp()
        signed = _stamp(self._home.secrets_folder / MCP_SIGN_IN_FILE)
        if self._sign_in_stamp is None:
            self._sign_in_stamp = signed
        elif signed != self._sign_in_stamp:
            self._sign_in_stamp = signed
            await self.assistant.mcp_signed_in()

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
                    s.name,
                    view.settings.get(s.name, s.default),
                    s.default,
                    s.description,
                    list(s.choices),
                    s.choices_from,
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

    def conversations_changed(
        self, conversation_id: str, status: str = "", *, title: str = ""
    ) -> None:
        """Tell every client a channel's conversation was made or moved, or a turn of any began or
        ended (`status`), so the lists show it and which works; or the name the user gave it
        (`title`, step 9e), which their tabs take."""
        frame = self._event_frame(
            "conversations.changed", ConversationsChanged(conversation_id, status, title)
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
                    modes=list(capabilities.modes),
                )
            if on is not None:
                row = dataclasses.replace(row, base=on[0], account_id=on[1], nickname=on[2])
            update = self._update_of(name)
            if update is not None:
                row = dataclasses.replace(row, latest=update[0], update=shown_command(update[1]))
            row = dataclasses.replace(row, updating=name in self._updating)
            view = views.get(plugin)
            manifest = view.manifest if view is not None else None
            if name in self._offered and view is not None and manifest is not None:
                row = dataclasses.replace(
                    row,
                    enabled=False,
                    declaration=list(manifest.declaration()),
                    consent=manifest.fingerprint(),
                    # On, and offered all the same: its consent no longer matches.
                    consent_again=view.enabled,
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
        await self._checkpoint(params.instance_id, params.text)
        steered = await self._on_workbench(
            self.assistant.workbench.prompt(params.instance_id, params.text, attached)
        )
        return WorkbenchPrompted(steered)

    async def _checkpoint(self, instance_id: str, prompt: str) -> None:
        """A checkpoint of the workspace an instance works in, before its prompt (step 21, A3);
        none outside a workspace, and a git that fails costs the prompt nothing, logged."""
        try:
            folder = self.assistant.workbench.get(instance_id).spec.folder
        except ExecutorError:
            return
        try:
            found = await asyncio.to_thread(self.workspaces.found_at, folder)
            if found is None:
                return
            kept = await asyncio.to_thread(self.workspaces.checkpoint, found.id, prompt)
        except WorkspaceError:
            _logger.warning("A workspace's checkpoint could not be kept.", exc_info=True)
            return
        if kept is not None:
            self._workspaces_changed()

    async def _workbench_auto(
        self, connection: Connection, params: WorkbenchSwitchParams
    ) -> InstanceRow:
        workbench = self.assistant.workbench
        await self._on_workbench(workbench.set_auto(params.instance_id, params.enabled))
        return _instance_row(workbench.get(params.instance_id).view())

    async def _workbench_git(self, connection: Connection, params: FolderRef) -> FolderGit:
        state = await self._on_workbench(self.assistant.workbench.git(Path(params.folder)))
        return _folder_git(params.folder, state)

    async def _workbench_worktree(
        self, connection: Connection, params: WorktreeParams
    ) -> FolderGit:
        """Step 18g's new worktree, a workspace since step 21: in the home, not beside the
        project."""
        self._owner(connection)
        made = await self._new_workspace(Path(params.folder), params.name, "")
        folder = Path(made.folder)
        return _folder_git(
            made.folder, await self._on_workbench(self.assistant.workbench.git(folder))
        )

    async def _workbench_context(
        self, connection: Connection, params: InstanceRef
    ) -> SessionContext:
        tokens, window = await self._on_workbench(
            self.assistant.workbench.context(params.instance_id)
        )
        return SessionContext(tokens, window)

    async def _workbench_mode(
        self, connection: Connection, params: WorkbenchModeParams
    ) -> InstanceRow:
        workbench = self.assistant.workbench
        await self._on_workbench(workbench.set_mode(params.instance_id, params.mode))
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

    async def _executor_delete_session(
        self, connection: Connection, params: SessionDeleteParams
    ) -> Empty:
        await self._on_workbench(
            self.assistant.workbench.delete_session(
                params.executor, params.session_id, params.folder
            )
        )
        return Empty()

    def _told_accounts_changed(self, executor: str) -> None:
        """The accounts plugin changed `executor`'s accounts or a folder's (step 22)."""
        task = asyncio.create_task(self._accounts_changed(executor))
        self._accounts_telling.add(task)
        task.add_done_callback(self._accounts_telling.discard)

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

    async def _executor_update(
        self, connection: Connection, params: ExecutorRef
    ) -> ExecutorUpdated:
        """Run the update its row shows, then look for the harnesses again: the version found
        then is the one the screen shows, whatever the command said."""
        self._owner(connection)
        name = params.executor
        update = self._update_of(name)
        if update is None:
            raise ProtocolError(INVALID_PARAMS, f"{name} has no update to install.")
        if name in self._updating:
            raise ProtocolError(INVALID_PARAMS, f"{name} is updating already.")
        running = [
            run
            for run in self.assistant.workbench.runs()
            if run.name.split("@")[0] == name
            and run.state not in (InstanceState.ENDED, InstanceState.FAILED)
        ]
        if running:
            raise ProtocolError(
                INVALID_PARAMS, "Stop its sessions that run first: a harness in use cannot update."
            )
        _logger.warning(
            "The owner's app asked to update an executor.",
            extra={"executor": name, "version": update[0]},
        )
        self._updating.add(name)
        self.plugins_changed()
        try:
            updated, output = await run_update(update[1])
        finally:
            self._updating.discard(name)
        await self.load_executors()
        self.plugins_changed()
        return ExecutorUpdated(updated, "" if updated else output)

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
        """Every minute: the routines due fire, a backup due is written, then the curator is
        asked."""
        while True:
            await asyncio.sleep(self._housekeeping_seconds)
            self._fire_routines()
            self._houses += 1
            if self._houses % CONNECTION_EVERY == 0:
                await self.read_connections()
            try:
                await asyncio.to_thread(self.back_up_when_due)
            except Exception:  # the server's edge: housekeeping must not stop, logged
                _logger.exception("The scheduled backup failed.")
            try:
                await self.channels.check()
                if self._executors_seen != await asyncio.to_thread(_executor_states, self.plugins):
                    await self.load_executors()
                else:
                    self._look_for_updates()
            except Exception:  # the server's edge: housekeeping must not stop, logged
                _logger.exception("The plugins could not be checked.")
            try:
                await self.assistant.curate()
            except Exception:  # the server's edge: housekeeping must not stop, logged
                _logger.exception("The curator failed.")

    def back_up_when_due(self) -> Path | None:
        """The home's backup in `[backup] folder` once the newest there is `every_hours` old
        (step 20), the oldest past `keep` removed; what was written, or `None`."""
        chosen = self.assistant.settings.backup
        if chosen.folder is None:
            return None
        folder = chosen.folder.expanduser()
        name = home_name(self._home.root)
        prefix = f"{BACKUP_PREFIX}{name}-"
        now = self._clock()
        newest = max(
            (found.stat().st_mtime for found in folder.glob(f"{prefix}*.zip")), default=None
        )
        if newest is not None and now.timestamp() - newest < chosen.every_hours * 3600:
            return None
        written = write_backup(
            self._home, folder / backup_name(name, now), version=__version__, now=now
        )
        _logger.info("The scheduled backup was written.", extra={"files": written.manifest.files})
        prune_backups(folder, prefix, chosen.keep)
        return written.path

    def connections(self) -> list[OfferedConnection]:
        """The connections Atlas offers in the user's conversations, and whether each is ready:
        its plugin there, on, consented to, and logged in when its status was last read."""
        found: list[OfferedConnection] = []
        views = {view.name: view for view in self.plugins.all()}
        for name, label, use in OFFERED_CONNECTIONS:
            view = views.get(name)
            if view is None:
                continue
            on = view.running
            ready = on and self._logged_in.get(name, False)
            found.append(OfferedConnection(name, label, use, ready))
        return found

    async def read_connections(self) -> None:
        """Ask each offered connection's plugin whether it is logged in (its `status` call), at
        start, when the plugins load and every few minutes; one that does not answer is not."""
        for name, _, _ in OFFERED_CONNECTIONS:
            call = self._calls.get((name, "status"))
            said: object = None
            if call is not None:
                try:
                    said = await asyncio.wait_for(call.run({}), CONNECTION_SECONDS)
                except Exception:  # noqa: BLE001 - a plugin's own failure: it is not logged in
                    said = None
            fields = cast("dict[str, object]", said) if isinstance(said, dict) else {}
            self._logged_in[name] = fields.get("logged_in") is True

    def _fire_routines(self) -> None:
        try:
            if self.scheduler.tick():
                self._routines_changed()
        except Exception:  # the server's edge: housekeeping must not stop, logged
            _logger.exception("The routines could not fire.")

    # -- routines ---------------------------------------------------------------------------------

    async def _routines(self, connection: Connection, params: Empty) -> RoutineList:
        routines = self.assistant.routines
        found = await asyncio.to_thread(routines.store.all)
        score = await asyncio.to_thread(routines.scoreboard)
        running = self.scheduler.running
        return RoutineList(
            [_routine_row(routine, routine.id in running) for routine in found],
            ScoreboardRow(format_utc(score.since), score.alone, score.needed, score.failed),
        )

    async def _routine_save(self, connection: Connection, params: RoutineSave) -> RoutineRow:
        """The user's own routine, made or changed from the app's form (step 25)."""
        self._owner(connection)
        routines = self.assistant.routines
        provider, _, model = params.model.partition("/")
        pinned = (provider, model, params.effort or None) if provider and model else None
        executor = RoutineExecutor(**dump(params.executor)) if params.executor is not None else None

        def save(key: str) -> Routine:
            if not key:
                return routines.create(
                    params.name,
                    params.schedule,
                    prompt=params.prompt,
                    model=pinned,
                    bot=params.bot or None,
                    probe=params.probe or None,
                    executor=executor,
                )
            found = routines.store.get(key)
            if found.prompt is None:
                raise JobsError(f"{found.name} runs a command: change it from the terminal.")
            return routines.update(
                key,
                name=params.name,
                schedule=params.schedule if params.schedule != found.schedule.display else None,
                prompt=params.prompt,
                probe=params.probe,
                model=pinned or ("", "", None),
                bot=params.bot,
                executor=executor or RoutineExecutor("", ""),
            )

        routine = await self._act(save, params.routine_id)
        self._routines_changed()
        return _routine_row(routine, routine.id in self.scheduler.running)

    async def _activity(self, connection: Connection, params: Empty) -> Activity:
        return await self.activity()

    async def activity(self) -> Activity:
        """What the home does now (step 20), for the client and for `GET /activity`: idle since the
        last moment something worked or a client was connected, and when the next routine is
        due."""
        now = self._clock()
        working = self.assistant.working() or bool(self.scheduler.running)
        clients = len(self._connections)
        if working or clients or self._idle_since is None:
            self._idle_since = now
        idle = None if working or clients else format_utc(self._idle_since)
        due = await asyncio.to_thread(self.assistant.routines.next_wake)
        return Activity(working, clients, idle, None if due is None else format_utc(due))

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


def _accounts_of(listed: AccountList) -> ExecutorAccounts:
    rows = [AccountRow(**dump(view)) for view in listed.accounts]
    return ExecutorAccounts(listed.executor, rows, listed.can_add, listed.failover, listed.compact)


def _command_check(command: str) -> CommandCheck:
    hard, reason = detect_hardline_command(command)
    if hard:
        return CommandCheck(True, reason or "")
    dangerous, _, description = detect_dangerous_command(command)
    return CommandCheck(dangerous, description or "")


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
        info.id,
        info.title,
        info.source,
        info.started_at,
        info.updated_at,
        info.messages,
        running,
        info.archived_at or "",
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


#: The connections Atlas offers in the user's conversations (05/11/2026): the plugin, what the
#: user calls it, and what it lets Atlas do.
OFFERED_CONNECTIONS = (
    ("google", "Gmail and Calendar", "their email and their calendar"),
    ("github", "GitHub", "their repositories, issues and pull requests"),
)
#: How long a plugin's status may take, and every how many housekeeping rounds it is read again.
CONNECTION_SECONDS = 30.0
CONNECTION_EVERY = 5


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
        routine.probe,
        None if routine.executor is None else RoutineExecutorRow(**dump(routine.executor)),
        f"{routine.model_provider}/{routine.model_name}"
        if routine.model_provider and routine.model_name
        else "",
        routine.model_effort or "",
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
        run.event[:2_000],
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


def _login_command(server: str) -> str:
    """The command that signs in to an MCP server, its name quoted when a shell would split it."""
    name = server if re.fullmatch(r"[A-Za-z0-9_.:-]+", server) else f'"{server}"'
    return f"atlas mcp login {name}"


def _stamp(path: Path) -> tuple[int, int]:
    """A file's time and size, to see it changed; (0, 0) while it does not exist."""
    try:
        status = path.stat()
    except FileNotFoundError:
        return (0, 0)
    return (status.st_mtime_ns, status.st_size)


async def _maybe_await(value: object) -> None:
    """Await `value` when it is a coroutine; otherwise it was a plain call that already ran. For
    callbacks that may be sync or async, as the restart hooks of a stored secret are."""
    if asyncio.iscoroutine(value):
        await value


class _TaskWorkspaces:
    """The gateway as the maker of the workspaces delegated tasks ask for (step 21)."""

    def __init__(self, gateway: Gateway) -> None:
        self._gateway = gateway

    async def make(self, folder: Path, name: str) -> tuple[Path, str]:
        return await self._gateway.task_workspace(folder, name)

    async def changes(self, folder: Path) -> str:
        return await self._gateway.task_workspace_changes(folder)

    async def finish(self, folder: Path, title: str, *, draft: bool) -> str:
        return await self._gateway.task_workspace_finish(folder, title, draft=draft)


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


def _folder_git(folder: str, state: GitState) -> FolderGit:
    return FolderGit(folder, state.repository, state.branch, state.root, state.worktree)


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


def _update_source(executor: Executor) -> UpdateSource | None:
    """Where the executor's newest version is published, when its plugin says (version 27)."""
    try:
        return executor.update_source() if executor.capabilities().updates else None
    except Exception:  # a plugin's own code: its failure is logged, and nothing is offered
        _logger.exception("An executor could not say how it updates.")
        return None


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
        source=row.source.value,
        updated=row.updated,
        installs=row.installs,
        category=row.category,
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


def _dictation_row(dictation: Dictation) -> DictationRow:
    return DictationRow(
        dictation.id,
        dictation.at,
        dictation.text,
        dictation.media_type,
        dictation.where,
        dictation.error,
    )


def _project_row(project: Project) -> ProjectRow:
    return ProjectRow(project.root, project.setup_script, project.ports_start, project.ports_size)
