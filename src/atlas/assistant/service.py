"""The assistant: the agent put together for each conversation, its turns, and the work that
follows them.

Decided on 10/10/2026 (docs/ARCHITECTURE.md, Server):

- **One assistant per process**, holding what every conversation shares: the database's stores,
  the memory files, the skills, the user's MCP servers. A conversation is opened when someone
  sends to it, and keeps its agent loop, its tools and its approval gate while Atlas runs.
- **A turn runs in a task of its own**, so it goes on when the client that started it leaves; its
  events go to the listener as they come, and the conversation is saved after each of them but the
  streamed ones.
- **A message sent while a turn runs steers it**: it enters after the round of tools in course,
  or starts the next turn when the turn answered first. Stopping the turn hands back what never
  entered.
- **After a turn, the background review** (`atlas.memory.BackgroundReview`) runs when its memory
  or skill half is due, in a task of its own, one per conversation at a time, on the same model and
  the same tools, so the provider's cache holds. The tools are a fresh copy of the conversation's:
  what the review reads does not count as read in the conversation. What it changed is reported
  (`Reviewed`), with the memory proposals it made: what its own tools did, never what another
  conversation or the user changed while it ran.
- **The memory proposals** (`atlas.memory.Proposals`) are the home's, shared by every
  conversation: the review makes them, and whoever keeps the process lists, approves and
  discards them.
- **The curator** (`atlas.skills.Curator`) is asked by whoever keeps the process (the server asks
  every minute); it runs once nothing ran for 2 hours.
- **MCP is an extra**: without it installed, the conversations simply have no MCP tools.
- **Every conversation has a role** (`ConversationRole`): the user's, a routine's run, or a
  subagent's. The role decides its tools, who answers its approvals, the loop guards' posture,
  and whether a review follows. Subagents run in `atlas.assistant.subagents`, as routines run in
  `atlas.assistant.routines`.

Decided on 13/10/2026, with the bots (docs/ARCHITECTURE.md, Bots):

- **A bot's conversation runs as the bot**: its mission in the identity's place, the home's
  profile and its own memory in the instructions and behind the memory tool, only the tools
  and MCP servers its definition allows, and its model when the conversation picked none. Its
  definition is read from disk each time the instructions are asked for, so an edit to its
  mission reaches its next turn; its tools and its model are taken when the conversation is
  opened.
- **A bot's fixed conversation** (`Assistant.bot_conversation`) is made the first time it is
  asked for, and reads its instructions again at each turn that finds them changed
  (`atlas.agent`).

Decided on 15/10/2026, with the soul (docs/ARCHITECTURE.md, The soul):

- **The main agent's conversations run as its soul**: `SOUL.md` is seeded when the assistant
  starts, if missing, and read from disk each time the instructions are asked for, so an edit
  reaches the next conversation. A subagent has none.
"""

import asyncio
import contextvars
import importlib
import importlib.util
import logging
import platform
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import atlas.tools
from atlas.agent import (
    SPILL_CHARS,
    AgentLoop,
    SpillFolder,
    Tool,
    ToolFinished,
    ToolProgress,
    Toolset,
    ToolSetError,
    ToolStarted,
    TurnEnded,
    TurnEvent,
    TurnLimit,
    TurnOutcome,
    cut,
    from_atlas,
    input_window,
    is_model_switch,
    model_switch_note,
    report_progress,
    skill_text,
    terminal_text,
    typed_text,
)
from atlas.assistant.describe import describe_image
from atlas.assistant.executors import ExecutorDesk, split_account
from atlas.assistant.messages import AgentMessages
from atlas.assistant.model import (
    ATLAS_COMMANDS,
    IDENTITY,
    ConversationRole,
    FileSent,
    PreviewAsked,
    Reviewed,
    Sent,
    SlashCommandView,
    SubagentChanged,
    Titled,
    TurnStarted,
    TurnStopped,
    WorkbenchEvent,
    bot_identity,
    identity,
    review_actions,
    skill_action,
)
from atlas.assistant.subagents import Subagents
from atlas.assistant.titles import TITLE_TURNS, TitleQuarantine, write_title
from atlas.assistant.workbench import Workbench, answer_for_user
from atlas.bots import MAIN_AGENT, Bot, BotError, Bots, roster_section
from atlas.browser import ENGINE, Browser, find_chrome
from atlas.conversations import (
    BOT,
    AttachmentError,
    Attachments,
    Conversation,
    ConversationStore,
    TitleSource,
)
from atlas.home import Database, Home, Settings, TrustSettings, time_zone, utc_now
from atlas.hooks import ConversationHooks, ConversationMonitors, Hooks, PluginMonitor
from atlas.jobs import (
    MAX_CHILD_CALLS,
    AccountStore,
    AgentDefinition,
    DelegationError,
    InstanceStore,
    Routines,
    RoutineStore,
    SubagentStore,
    parse_agent,
)
from atlas.memory import (
    BackgroundReview,
    Instructions,
    MemoryFiles,
    Proposals,
    ProposalStore,
    SoulFile,
    review_due,
    skill_review_due,
)
from atlas.models import (
    TOOL_SEARCH,
    AssistantMessage,
    Attachment,
    AttachmentKind,
    Message,
    ModelChoice,
    ModelClient,
    Models,
    ModelsError,
    ReasoningDelta,
    TextDelta,
    ToolResult,
    ToolStatus,
    UserMessage,
    sees_images,
)
from atlas.plugins import Executor, LoadedCommand, LoadedReader, LoadedTool, Transcript
from atlas.secrets import MCP_SIGN_IN_FILE, SecretsError, Vault
from atlas.skills import (
    Curator,
    CuratorRun,
    Skill,
    SkillLibrary,
    SkillShelves,
    SkillUsage,
    support_files,
)
from atlas.tools import (
    SECRETS_TOOL,
    Answers,
    Asker,
    BackgroundCommand,
    BotMaker,
    Commands,
    Delegator,
    DocumentTexts,
    Messenger,
    Outcome,
    Plan,
    Question,
    SecretAsker,
    SecretView,
    SessionReader,
    Show,
    SkillUse,
    WebFetch,
    Workspace,
    ask_user,
    bot_create,
    conversation_search,
    cronjob_manage,
    delegation_search,
    desktop_preview,
    executor_accounts,
    file_tools,
    find_files,
    install_tool_search,
    make_secrets_tool,
    message_agent,
    search_backend,
    web_search,
)
from atlas.trust import ApprovalGate, Approver, TrustStore, Verdict

type ConversationEvent = (
    TurnEvent
    | TurnStarted
    | TurnStopped
    | Reviewed
    | Titled
    | SubagentChanged
    | BackgroundCommand
    | PreviewAsked
    | FileSent
)
#: Where the conversations' events go: the conversation's id, and the event.
type Listener = Callable[[str, ConversationEvent], None]

#: Saving after these would only store the same history again.
_STREAMED = (TextDelta, ReasoningDelta, ToolProgress)
#: How long a stopped turn has to end before it is told again.
STOP_AGAIN_SECONDS = 0.1
#: The MCP servers' tools come from sources named so (`mcp:github`), and the plugins' so
#: (`plugin:video`).
_MCP_SOURCE = "mcp:"
_PLUGIN_SOURCE = "plugin:"
#: Files of a skill named when the user invokes it, as the use_skill tool names them.
SKILL_FILES_NAMED = 20
#: A subagent's input budget: its 250 calls are the cap; the tokens are left to compaction, as in
#: the user's turns.
_CHILD_LIMIT = TurnLimit(MAX_CHILD_CALLS, 1 << 62)
#: How long a plugin's reader may take over one file (step 17a), as the SDK promises.
READ_SECONDS = 120.0

_logger = logging.getLogger("atlas.assistant")
#: The vault's names of the account tokens step 16h kept on 18/10/2026
#: (`EXECUTOR_CLAUDE_CODE_1A2B3C4D`).
_ACCOUNT_TOKEN = re.compile(r"EXECUTOR_[A-Z0-9_]+_[0-9A-F]{8}")


@dataclass
class _Shared:
    """What every conversation of one Atlas shares."""

    home: Home
    settings: Settings
    models: Models
    vault: Vault
    listener: Listener
    asker: Callable[[str], Asker | None]
    approver: Callable[[str], Approver | None]
    #: The secrets tool's server side (step 16d): for a conversation and its bot name (empty
    #: for the main agent), the asker that shows a masked card in the app and writes the value
    #: to the vault. `None` turns the tool off for the turn.
    secret_asker: Callable[[str, str], SecretAsker | None]
    clock: Callable[[], datetime]
    conversations: ConversationStore
    trust: TrustStore
    usage: SkillUsage
    library: SkillLibrary
    memory: MemoryFiles
    soul: SoulFile
    proposals: Proposals
    bots: Bots
    texts: DocumentTexts
    spill: SpillFolder
    workspace: Path
    routines: Routines
    subagent_store: SubagentStore
    #: The title providers skipped for now (no credit, a refused key).
    titles: TitleQuarantine
    instance_store: InstanceStore
    #: The executors' extra accounts and each folder's (step 16h).
    account_store: AccountStore
    #: The files the user attached to messages (step 16g).
    attachments: Attachments
    #: The MCP servers' tools and the plugins' (step 16j), which each conversation copies.
    mcp_tools: Toolset = field(default_factory=Toolset)
    mcp: Any = None
    #: The plugins' tools, one line per plugin for tool search's list of sources.
    plugin_sources: dict[str, str] = field(default_factory=dict[str, str])
    #: The plugins' MCP servers, as an MCP file's document, for the MCP client.
    plugin_servers: dict[str, Any] = field(default_factory=dict[str, Any])
    #: The plugins' hooks (step 16k), which every conversation's loop runs as they are now.
    hooks: Hooks = field(default_factory=Hooks)
    #: The plugins' `bin/` folders (step 16k), first on the `PATH` of the commands the model runs.
    programs: tuple[Path, ...] = ()
    #: The plugins' agents (step 16k): predefined subagents `delegate` starts, by name.
    agents: dict[str, AgentDefinition] = field(default_factory=dict[str, AgentDefinition])
    #: The plugins' monitors (step 16k), which the user's conversations run.
    monitors: tuple[PluginMonitor, ...] = ()
    #: The plugins' slash commands (step 16l), by what is typed after the slash (`video:status`).
    slash: dict[str, LoadedCommand] = field(default_factory=dict[str, LoadedCommand])
    #: The plugins' attachment readers (step 17a), by the kind each reads (`audio`).
    readers: dict[str, LoadedReader] = field(default_factory=dict[str, LoadedReader])
    last_active: datetime | None = None
    #: The process's subagents, made once the assistant is.
    subagents: "Subagents | None" = None
    #: The browser, when a Chrome, Edge or Chromium is installed (step 14).
    browser: Browser | None = None
    #: The messages between the home's agents, made once the assistant is.
    messages: "AgentMessages | None" = None
    #: Makes a bot for the main agent's `bot_create`, and tells the screens.
    make_bot: Callable[..., Bot] | None = None
    #: Reads an executor instance's session through its plugin, for delegation_search.
    read_session: SessionReader | None = None

    def secret(self, name: str) -> str | None:
        try:
            return self.vault.get(name)
        except SecretsError as error:
            _logger.warning("The secret %s cannot be read: %s", name, error)
            return None

    def secret_views(self) -> list[SecretView]:
        """The vault's secrets as the `secrets` tool's `list` reports them: name and date,
        never the value. The vault lists without decrypting; a vault that cannot be read gives
        an empty list, which the tool shows to the model."""
        try:
            names = self.vault.names()
        except SecretsError as error:
            _logger.warning("The vault cannot be listed for secrets.list: %s", error)
            return []
        views: list[SecretView] = []
        for name in names:
            views.append(SecretView(name, self.vault.updated_at(name) or ""))
        return views

    def mcp_sources(self) -> dict[str, str]:
        found = dict(self.mcp.sources) if self.mcp is not None else {}
        return found | self.plugin_sources

    def active(self) -> None:
        self.last_active = self.clock()

    def tools(
        self,
        conversation_id: str,
        history: Sequence[Message],
        asker: Asker | None,
        window: int | None,
        on_notice: Callable[[str], None] | None = None,
        *,
        role: ConversationRole = ConversationRole.CONVERSATION,
        delegator: Delegator | None = None,
        browser: bool | None = None,
        bot: Bot | None = None,
        messenger: Messenger | None = None,
        maker: BotMaker | None = None,
        on_command: Callable[[BackgroundCommand], None] | None = None,
        on_preview: Show | None = None,
        only: frozenset[str] | None = None,
        on_skill: Callable[[str], None] | None = None,
    ) -> tuple[Toolset, Commands, Workspace]:
        """A conversation's tools, always in the same order, so the provider's cache holds. A
        subagent is not given memory, delegate, ask_user or cronjob_manage; a routine's run is not
        given ask_user or cronjob_manage, so a run makes no routines. `browser` gives browser_exec,
        for a model that sees images (`True`) or not (`False`), wherever run_command is, since its
        code is host Python. The background review uses the turn's tools, not tools of its own. A
        `bot` writes its own memory, and is given only the tools and
        the MCP servers its definition allows. `on_command` hears its background commands start
        and end, for the screen. `only` keeps Atlas's own tools to those it names, as a bot's
        definition does: a plugin's agent that lists its tools (step 16k)."""
        child = role is ConversationRole.SUBAGENT
        workspace = Workspace(self.workspace, protected=(self.home.secrets_folder,))
        commands = Commands(
            workspace,
            self.home.commands_folder,
            on_notice=on_notice,
            on_change=on_command,
            path=lambda: self.programs,
        )
        plan = Plan()
        plan.hydrate(history)
        skills = self.skills_of(bot)
        tools = [
            *file_tools(workspace),
            find_files(workspace, texts=self.texts),
            commands.tool(),
            web_search(search_backend(self.secret)),
            WebFetch(self.spill).tool(),
            *(
                []
                if browser is None or self.browser is None
                else [self.browser.tool(conversation_id, sees_images=browser)]
            ),
            # Through the package: its module of the same name would read as an import of it.
            *([] if child else [atlas.tools.memory(*self.memory_of(bot))]),
            *([] if asker is None else [ask_user(asker)]),
            # The secrets tool (step 16d): deferred, given only when the conversation has someone
            # to answer (as ask_user is). Subagents, routines and the review are not given it.
            *(
                []
                if asker is None
                or (secret_a := self.secret_asker(conversation_id, "" if bot is None else bot.name))
                is None
                else [make_secrets_tool(secret_a, self.secret_views)]
            ),
            *(
                []
                if (
                    made := atlas.tools.delegate(
                        None if child else delegator,
                        agents=[(a.name, a.description) for a in self.agents.values()],
                    )
                )
                is None
                else [made]
            ),
            atlas.tools.skill_manage(skills),
            conversation_search(
                self.conversations, conversation_id, bot=None if bot is None else bot.name
            ),
            *(
                []
                if child or self.read_session is None
                else [delegation_search(self.instance_store, self.read_session)]
            ),
            *([] if child or (accounts := executor_accounts(delegator)) is None else [accounts]),
            plan.tool(),
            *(
                [cronjob_manage(self.routines, bot=None if bot is None else bot.name)]
                if role is ConversationRole.CONVERSATION
                else []
            ),
            *([] if messenger is None else [message_agent(messenger)]),
            *([] if maker is None else [bot_create(maker)]),
            # The app's side panel, for the user's own conversations (step 16f).
            *([] if on_preview is None else [desktop_preview(on_preview, self.workspace)]),
        ]
        counted = skills if isinstance(skills, SkillShelves) else self.usage
        use = SkillUse(skills.catalog, window, usage=counted, on_use=on_skill).tool()
        if use is not None:
            tools.append(use)
        if bot is not None and bot.tools is not None:
            tools = [tool for tool in tools if tool.name in bot.tools]
        if only is not None:
            tools = [tool for tool in tools if tool.name in only]
        toolset = Toolset(tools)
        _sync(toolset, self.mcp_tools, _mcp_allowed(bot))
        install_tool_search(toolset, self.mcp_sources(), window)
        return toolset, commands, workspace

    def skills_of(self, bot: Bot | None) -> SkillLibrary | SkillShelves:
        """The skills an agent loads and writes: the home's library for the main agent; for a
        bot, its own folder first and the library behind it, of which it sees what its
        definition lists."""
        if bot is None:
            return self.library
        return SkillShelves(bot_library(self, bot), self.library, bot.skills)

    def invocable(self, bot_name: str | None) -> list[Skill]:
        """The skills the user invokes with `/name` in a conversation of the main agent or of
        `bot_name`: those it loads, but a skill that says `user-invocable: false` (Claude Code's),
        and one named as Atlas's own command, which the command keeps."""
        bot = None if bot_name is None else self.bots.get(bot_name)
        own = {command.name for command in ATLAS_COMMANDS}
        skills = self.skills_of(bot).catalog.skills()
        return [s for s in skills if s.user_invocable and s.name not in own]

    def memory_of(self, bot: Bot | None) -> tuple[MemoryFiles, Proposals]:
        """The stores an agent reads and writes, and its proposals: the home's for the main
        agent; for a bot, the home's profile and the bot's own memory."""
        if bot is None:
            return self.memory, self.proposals
        files = bot_memory(self.home, bot)
        return files, self.proposals.for_bot(bot.name, files)


def bot_library(shared: _Shared, bot: Bot) -> SkillLibrary:
    """A bot's own skills folder; what is created there is the bot's making."""
    return SkillLibrary(
        bot.skills_folder, shared.usage.owned_by(bot.name), shared.clock, creator=bot.name
    )


def bot_memory(home: Home, bot: Bot) -> MemoryFiles:
    """A bot's stores: the home's profile, one for all, and the memory in the bot's folder."""
    return MemoryFiles(home.memory_folder, memory_folder=bot.folder)


class Assistant:
    """Every conversation of one Atlas, and what they share."""

    def __init__(
        self,
        home: Home,
        settings: Settings,
        models: Models,
        vault: Vault,
        *,
        listener: Listener,
        asker: Callable[[str], Asker | None] = lambda _conversation: None,
        approver: Callable[[str], Approver | None] = lambda _conversation: None,
        secret_asker: Callable[[str, str], SecretAsker | None] = lambda _c, _bot: None,
        clock: Callable[[], datetime] = utc_now,
        desk: ExecutorDesk | None = None,
        executors: Mapping[str, Executor] | None = None,
        on_workbench: Callable[[WorkbenchEvent], None] = lambda _event: None,
        chrome: Sequence[str] | None = None,
        browser_engine: Sequence[str] = ENGINE,
    ) -> None:
        """`listener` gets every conversation's events; `asker` and `approver` give, for a
        conversation, who answers its questions and its approvals (none for a turn nobody waits
        on); `desk` brings what an outside executor asks to the user. `executors` are the outside
        executors by name, from the plugins that drive them (`set_executors`); by default none;
        `on_workbench` hears every change on the workbench, which belongs to no conversation.
        `chrome` is the browser's program; by default `[browser] chrome`, or the Chrome, Edge or
        Chromium installed, and with none the conversations have no browser."""
        database = Database(home.database_file)
        usage = SkillUsage(database)
        library = SkillLibrary(home.skills_folder, usage, creator=MAIN_AGENT)
        memory = MemoryFiles(home.memory_folder)
        soul = SoulFile(home.soul_file)
        try:
            soul.seed(IDENTITY)
        except OSError as error:
            _logger.warning(
                "SOUL.md could not be seeded; the default identity stands in: %s", error
            )
        bots = Bots(home.bots_folder)

        def bot_files(name: str) -> MemoryFiles | None:
            try:
                return bot_memory(home, bots.get(name))
            except BotError:
                return None

        self._shared = _Shared(
            home=home,
            settings=settings,
            models=models,
            vault=vault,
            listener=listener,
            asker=asker,
            approver=approver,
            secret_asker=secret_asker,
            clock=clock,
            conversations=ConversationStore(database),
            trust=TrustStore(database),
            usage=usage,
            library=library,
            memory=memory,
            soul=soul,
            proposals=Proposals(memory, ProposalStore(database), clock, bot_files=bot_files),
            bots=bots,
            texts=DocumentTexts(home.documents_folder),
            spill=SpillFolder(home.spill_folder),
            workspace=settings.workspace.folder or Path.home(),
            routines=Routines(
                RoutineStore(database), zone=time_zone(settings.time.zone), clock=clock
            ),
            subagent_store=SubagentStore(database),
            titles=TitleQuarantine(clock),
            instance_store=InstanceStore(database),
            account_store=AccountStore(database),
            attachments=Attachments(home.attachments_folder),
            last_active=clock(),
        )
        self.desk = desk
        self._executors: dict[str, Executor] = dict(executors or {})
        #: Each account's executor made so far (step 16h), by its name, with the base executor and
        #: the folder it was made from.
        self._on_accounts: dict[str, tuple[Executor, str, Executor]] = {}
        self.workbench = Workbench(self, on_workbench)
        self.subagents = self._shared.subagents = Subagents(self)
        self.messages = self._shared.messages = AgentMessages(self)
        self._shared.make_bot = self.create_bot
        self._shared.read_session = self._read_session
        if chrome is not None:
            program: list[str] | None = list(chrome)
        elif settings.browser.chrome:
            program = list(settings.browser.chrome)
        else:
            program = None if (found := _installed_browser()) is None else [str(found)]
        self.browser = self._shared.browser = (
            None
            if program is None
            else Browser(
                home.browser_folder,
                program,
                redact=vault.redact,
                engine=browser_engine,
            )
        )
        self.conversations = self._shared.conversations
        self.proposals = self._shared.proposals
        #: The main agent's soul, which the app's screen reads and writes.
        self.soul = soul
        #: The main agent's profile and memory, which the settings screen edits entry by entry.
        self.memory = memory
        self.bots = bots
        #: Told when a bot is made, changed or deleted: whoever keeps the process tells the
        #: screens.
        self.on_bots_changed: Callable[[], None] = lambda: None
        #: Told when an MCP server connected, was left out or stopped, or its tools changed.
        self.on_mcp_changed: Callable[[], None] = lambda: None
        self.routines = self._shared.routines
        self._curator = Curator(
            library,
            usage,
            clock,
            more=lambda: [bot_library(self._shared, bot) for bot in bots.list()],
        )
        self._mcp_start: asyncio.Task[None] | None = None
        self._open: dict[str, OpenConversation] = {}

    @property
    def home(self) -> Home:
        return self._shared.home

    def clock(self) -> datetime:
        return self._shared.clock()

    @property
    def workspace(self) -> Path:
        return self._shared.workspace

    def agent(self, name: str) -> AgentDefinition | None:
        """A plugin's agent by the name `delegate` gives it (`video:editor`), step 16k."""
        return self._shared.agents.get(name)

    @property
    def mcp(self) -> Any:
        """The MCP client (`atlas.mcp.McpClient`) once `start` made it; `None` without the mcp
        extra."""
        return self._shared.mcp

    def skills(self) -> list[Skill]:
        """The skills the main agent can load: the library's, then the plugins' that are on."""
        return self._shared.library.catalog.skills()

    @property
    def programs(self) -> tuple[Path, ...]:
        """The plugins' `bin/` folders, first on the `PATH` of the commands Atlas runs."""
        return self._shared.programs

    @property
    def attachments(self) -> Attachments:
        """The files the user attached to messages (step 16g)."""
        return self._shared.attachments

    @property
    def settings(self) -> Settings:
        return self._shared.settings

    def use_settings(self, settings: Settings) -> None:
        """The editable settings changed on the settings screen (step 15b), handed to whoever
        holds them: the default model from the next turn of a conversation that picked none,
        the trust rules from the next request of every open conversation, the folder in new
        conversations, the zone the next time a routine is scheduled. A subagent already running
        ends on the rules it started with."""
        shared = self._shared
        shared.settings = replace(
            shared.settings,
            model=settings.model,
            trust=settings.trust,
            workspace=settings.workspace,
            time=settings.time,
        )
        shared.models.use(settings.model)
        shared.workspace = settings.workspace.folder or Path.home()
        shared.routines.use_zone(time_zone(settings.time.zone))
        for conversation in self._open.values():
            conversation.use_trust(settings.trust)

    @property
    def spill(self) -> SpillFolder:
        return self._shared.spill

    @property
    def subagent_store(self) -> SubagentStore:
        return self._shared.subagent_store

    @property
    def instance_store(self) -> InstanceStore:
        """The delegation record: every executor instance and its turns (step 16c)."""
        return self._shared.instance_store

    @property
    def account_store(self) -> AccountStore:
        """The executors' other accounts, and the account of each folder (step 16h)."""
        return self._shared.account_store

    @property
    def vault(self) -> Vault:
        return self._shared.vault

    def notify(self, conversation_id: str, event: ConversationEvent) -> None:
        """Tell the listener something about a conversation outside its turns."""
        self._shared.listener(conversation_id, event)

    async def _send_file(self, conversation_id: str, path: Path) -> str:
        """A plugin's tool sends the user a file (SDK version 22): kept in the home's attachments,
        told to the conversation's listeners (the app, a channel), and where it is kept returned.
        Raises `ValueError` for a file that cannot be kept."""
        attachments = self._shared.attachments

        def keep() -> tuple[Attachment, str]:
            try:
                data = path.read_bytes()
            except OSError as error:
                raise ValueError(f"{path} cannot be read: {error}") from error
            try:
                added = attachments.add("", path.name, len(data), 0, data)
            except AttachmentError as error:
                raise ValueError(str(error)) from error
            kept = added.attachment
            if kept is None:  # pragma: no cover - one part of the whole size completes it
                raise ValueError(f"{path.name} could not be kept.")
            return kept, str(attachments.path(kept.id))

        attachment, where = await asyncio.to_thread(keep)
        if conversation_id:
            self._shared.listener(conversation_id, FileSent(attachment))
        return where

    def executors(self) -> list[str]:
        """The outside executors installed on this computer, by name."""
        return sorted(self._executors)

    def executor(self, name: str) -> Executor | None:
        """An outside executor by name; `None` when it is not installed. An account's name
        (`claude-code@1a2b3c4d`, step 16h) gives its executor on that account's folder, made by
        its plugin; `None` when the account is gone."""
        executor, account = split_account(name)
        found = self._executors.get(executor)
        if found is None or not account:
            return found
        if not found.capabilities().account_folders:
            return None
        kept = self._shared.account_store.get(executor, account)
        if kept is None:
            self._on_accounts.pop(name, None)
            return None
        made = self._on_accounts.get(name)
        if made is None or made[0] is not found or made[1] != kept.folder:
            made = (found, kept.folder, found.for_account(Path(kept.folder)))
            self._on_accounts[name] = made
        return made[2]

    async def _read_session(self, name: str, session: str, folder: str) -> Transcript | None:
        """An executor instance's session, normalized by its plugin; `None` when its executor is
        not installed now."""
        found = self.executor(name)
        return None if found is None else await found.transcript(session, Path(folder))

    def set_executors(self, executors: Mapping[str, Executor]) -> None:
        """The outside executors installed now, found by the plugins that are on (step 16c)."""
        self._executors = dict(executors)

    async def set_plugins(
        self,
        tools: Sequence[LoadedTool],
        skills: Mapping[str, Path],
        servers: Mapping[str, Any],
        hooks: Hooks | None = None,
        *,
        commands: Mapping[str, Path] | None = None,
        programs: Sequence[Path] = (),
        agents: Mapping[str, Sequence[Path]] | None = None,
        monitors: Sequence[PluginMonitor] = (),
        slash: Sequence[LoadedCommand] = (),
        readers: Sequence[LoadedReader] = (),
    ) -> None:
        """What the plugins that are on bring now (step 16j): their tools, which join the
        conversations behind the tool search as the MCP servers' do; their skills folders, listed
        after the library's; their MCP servers, connected or stopped to match; and their hooks
        (step 16k), which open conversations run from their next event."""
        shared = self._shared
        shared.hooks = hooks if hooks is not None else Hooks()
        shared.library.plugin_skills = dict(skills)
        shared.library.plugin_commands = dict(commands or {})
        shared.programs = tuple(programs)
        shared.agents = _agents(agents or {})
        shared.slash = {command.typed: command for command in slash}
        shared.readers = {reader.kind: reader for reader in readers}
        if tuple(monitors) != shared.monitors:
            shared.monitors = tuple(monitors)
            # Each conversation starts them again as they are now, when their time comes.
            for conversation in list(self._open.values()):
                await conversation.stop_monitors()
        by_source: dict[str, list[Tool]] = {}
        for loaded in tools:
            by_source.setdefault(f"{_PLUGIN_SOURCE}{loaded.plugin}", []).append(
                _plugin_tool(loaded, self._send_file)
            )
        held = {
            tool.source
            for tool in (*shared.mcp_tools.always, *shared.mcp_tools.deferred)
            if tool.source.startswith(_PLUGIN_SOURCE)
        }
        for source in sorted(held | set(by_source)):
            try:
                shared.mcp_tools.replace(source, by_source.get(source, []))
            except ToolSetError as error:
                _logger.warning("The tools of %s were left out: %s", source, error)
        shared.plugin_sources = {
            source: f"Tools the plugin {source.removeprefix(_PLUGIN_SOURCE)} adds."
            for source in by_source
        }
        shared.plugin_servers = dict(servers)
        self._mcp_changed()
        if shared.mcp is not None:
            await shared.mcp.reload_plugins()

    async def reload_mcp(self) -> None:
        """The home's `mcp.json` changed (the marketplace wrote it, or the user did): its new
        servers connect and the ones gone or changed stop, the others left connected."""
        if self._shared.mcp is not None:
            await self._shared.mcp.reload_file()

    async def mcp_sign_in(self, name: str) -> str:
        """The app's Sign in button for the MCP server `name`: the page to open in the browser
        (`atlas.mcp.McpClient.sign_in`). Raises `ValueError` saying why there is none."""
        if self._shared.mcp is None:
            raise ValueError("MCP servers are left out here: the mcp extra is not installed.")
        module = importlib.import_module("atlas.mcp")
        try:
            return await self._shared.mcp.sign_in(name)
        except module.McpError as error:
            raise ValueError(str(error)) from error

    async def mcp_signed_in(self) -> None:
        """The MCP sign-ins changed (`atlas mcp login`): the servers left out for want of one are
        tried again."""
        if self._shared.mcp is not None:
            await self._shared.mcp.signed_in()

    # -- the process ------------------------------------------------------------------------------

    def start(self) -> None:
        """Deliver what subagents of an earlier process left, and connect the user's MCP servers
        in the background; their tools join the conversations as they arrive. Needs a running
        event loop."""
        self.subagents.recover()
        self._forget_account_tokens()
        ended = self._shared.instance_store.abandon_running(self.clock())
        if ended:
            _logger.info("Executor instances an earlier Atlas left running are ended: %d.", ended)
        if importlib.util.find_spec("mcp") is None:
            _logger.info("MCP servers are left out: the mcp extra is not installed.")
            return
        shared = self._shared
        module = importlib.import_module("atlas.mcp")
        shared.mcp = module.McpClient(
            shared.home.mcp_file,
            shared.secret,
            shared.mcp_tools,
            self._mcp_changed,
            plugins=lambda: shared.plugin_servers,
            sign_ins=shared.vault.beside(MCP_SIGN_IN_FILE),
        )
        self._mcp_start = asyncio.create_task(shared.mcp.start())

    def _forget_account_tokens(self) -> None:
        """The credentials step 16h kept in the vault on 18/10/2026, before each account became
        the harness's own folder (decided with the user on 19/10/2026), leave it."""
        vault = self._shared.vault
        try:
            stale = [name for name in vault.names() if _ACCOUNT_TOKEN.fullmatch(name)]
            for name in stale:
                vault.delete(name)
        except SecretsError as error:
            _logger.warning("The vault could not be cleared of old account tokens: %s", error)
            return
        if stale:
            _logger.info("Old account tokens left the vault: %d.", len(stale))

    async def close(self) -> None:
        """Stop every subagent, turn and review, every background command, every MCP server, and
        the browser, and the user's executor instances."""
        await self.subagents.close()
        await self.workbench.close()
        await self.messages.close()
        for conversation in list(self._open.values()):
            await conversation.close()
        if self.browser is not None:
            await self.browser.close()
        if self._mcp_start is not None:
            self._mcp_start.cancel()
            await asyncio.gather(self._mcp_start, return_exceptions=True)
        if self._shared.mcp is not None:
            await self._shared.mcp.close()

    async def curate(self) -> CuratorRun | None:
        """A curator pass when one is due: nothing running, nothing ran for 2 hours, and the last
        pass 7 days old."""
        if any(conversation.busy for conversation in self._open.values()):
            return None
        shared = self._shared
        idle = shared.clock() - (shared.last_active or shared.clock())
        return await asyncio.to_thread(self._curator.run, idle)

    # -- conversations ----------------------------------------------------------------------------

    def slash_commands(self, conversation_id: str | None) -> list[SlashCommandView]:
        """What the box offers as `/` is typed in a conversation (a new one, with no id, is the
        main agent's): Atlas's own commands, then its skills, by name. A bot's conversation is
        its one, so it has no `/new`. Raises `ConversationNotFoundError`."""
        bot = None
        if conversation_id:
            info = self._shared.conversations.info(conversation_id)
            bot = info.bot
        own = [c for c in ATLAS_COMMANDS if bot is None or c.name != "new"]
        plugins = [
            SlashCommandView(typed, command.description, command.hint, group="command")
            for typed, command in sorted(self._shared.slash.items())
        ]
        skills = sorted(self._shared.invocable(bot), key=lambda skill: skill.name)
        return [*own, *plugins, *(SlashCommandView(s.name, s.description) for s in skills)]

    def conversation(
        self,
        conversation_id: str,
        *,
        role: ConversationRole = ConversationRole.CONVERSATION,
        prompt: str | None = None,
        listener: Listener | None = None,
        tools: frozenset[str] | None = None,
    ) -> "OpenConversation":
        """The open conversation, opened from the database the first time, in its `role`. A
        subagent's `prompt` follows its identity, its events go to `listener` instead of the
        assistant's, and `tools` keeps Atlas's own tools to those (a plugin's agent). Raises
        `ConversationNotFoundError`."""
        found = self._open.get(conversation_id)
        if found is None:
            stored = self.conversations.open(conversation_id)
            found = OpenConversation(
                self._shared, stored, role=role, prompt=prompt, listener=listener, tools=tools
            )
            self._open[conversation_id] = found
        return found

    def bot_conversation(self, name: str) -> str:
        """The id of a bot's fixed conversation, made the first time it is asked for. Raises
        `BotError` when there is no such bot."""
        bot = self.bots.get(name)
        fixed = self.conversations.fixed(bot.name)
        if fixed is not None:
            return fixed.id
        created = self.conversations.create(source=BOT, bot=bot.name, title=bot.title).id
        self.set_auto_mode(created, True)
        return created

    def create_bot(self, name: str, **definition: Any) -> Bot:
        """Make a bot (`Bots.create`), and tell the screens. Raises `BotError`."""
        bot = self.bots.create(name, **definition)
        self.on_bots_changed()
        return bot

    def update_bot(self, name: str, **definition: Any) -> Bot:
        """Write a bot's definition anew (`Bots.update`): its mission reaches its next turn,
        its tools and model the next time its conversation opens. Raises `BotError`."""
        bot = self.bots.update(name, **definition)
        self.on_bots_changed()
        return bot

    async def delete_bot(self, name: str) -> None:
        """Delete a bot: its turns stop, its folder goes with its memory and skills, its
        routines go too, and its conversations are kept and let go of the name. Raises
        `BotError` when there is no such bot."""
        self.bots.get(name)
        for conversation in list(self._open.values()):
            if conversation.bot_name == name:
                await conversation.close()
                self._open.pop(conversation.id, None)
        self.bots.delete(name)
        self.conversations.release(name)
        self._shared.usage.owned_by(name).forget()
        for routine in self.routines.store.all():
            if routine.bot == name:
                self.routines.store.remove(routine.id)
        self.on_bots_changed()

    async def bot_options(self) -> tuple[list[str], list[str], list[str]]:
        """What a bot's definition may name: the home's tools, its MCP servers and the
        library's skills."""
        shared = self._shared
        toolset, commands, _ = shared.tools(
            "", [], _nobody, None, browser=None if self.browser is None else True
        )
        await commands.close()
        tools = {
            tool.name
            for tool in (*toolset.always, *toolset.deferred)
            if not tool.source.startswith(_MCP_SOURCE) and tool.name != TOOL_SEARCH
        } | {"delegate", "message_agent"}
        servers = sorted(source.removeprefix(_MCP_SOURCE) for source in shared.mcp_sources())
        skills = sorted(skill.name for skill in shared.library.catalog.skills())
        return sorted(tools), servers, skills

    def opened(self, conversation_id: str) -> "OpenConversation | None":
        """The conversation when it is open, without opening it."""
        return self._open.get(conversation_id)

    def forget(self, conversation_id: str) -> None:
        """Let go of an open conversation that is done, such as a routine's run."""
        self._open.pop(conversation_id, None)

    def running(self, conversation_id: str) -> bool:
        found = self._open.get(conversation_id)
        return found is not None and found.running

    def auto_mode(self, conversation_id: str) -> bool:
        return self._shared.trust.auto_mode(conversation_id)

    def set_auto_mode(self, conversation_id: str, enabled: bool) -> None:
        """The conversation's "Auto", kept in the database. Raises `ConversationNotFoundError`."""
        self.conversations.info(conversation_id)
        self._shared.trust.set_auto_mode(conversation_id, enabled, self._shared.clock())

    @property
    def models(self) -> Models:
        return self._shared.models

    def conversation_model(self, conversation_id: str) -> tuple[ModelChoice | None, bool]:
        """The model the conversation uses, and whether it picked it: its pick, or the default.
        The default is `None` when no model can be chosen (no key in the vault). Raises
        `ConversationNotFoundError`. The effort is the one it runs on: Atlas's default when the
        pick names none."""
        picked = self.conversations.model(conversation_id)
        if picked is not None:
            try:
                return self._shared.models.resolved(picked), True
            except ModelsError:
                return picked, True
        try:
            return self._shared.models.default(), False
        except ModelsError:
            return None, False

    def set_conversation_model(self, conversation_id: str, choice: ModelChoice) -> None:
        """Keep the conversation's pick; it applies from its next turn, so a switch made while a
        turn runs waits for it. Raises `ModelsError` for a pick that cannot run, and
        `ConversationNotFoundError`."""
        self._shared.models.check(choice)
        self.conversations.set_model(conversation_id, choice)

    def _mcp_changed(self) -> None:
        for conversation in self._open.values():
            conversation.sync_mcp()
        self.on_mcp_changed()


class OpenConversation:
    """One conversation while someone talks in it: its history, its agent, and its turn."""

    def __init__(
        self,
        shared: _Shared,
        stored: Conversation,
        *,
        role: ConversationRole = ConversationRole.CONVERSATION,
        prompt: str | None = None,
        listener: Listener | None = None,
        tools: frozenset[str] | None = None,
    ) -> None:
        self.id = stored.id
        self._shared = shared
        #: A routine's run: nobody answers questions or approvals, the loop guards halt, and no
        #: background review follows (~30K tokens a run nobody would use). A subagent: nobody
        #: answers either, but its parent watches, and it runs one turn.
        self.role = role
        self._prompt = prompt
        #: The only tools of Atlas's own it is given, when a plugin's agent names them.
        self._only_tools = tools
        self._listener = listener or shared.listener
        #: When the turn last did something, for a routine's inactivity watchdog.
        self.last_activity = shared.clock()
        #: How the last turn ended.
        self.last_ended: TurnEnded | None = None
        self._stored = stored
        info = shared.conversations.info(stored.id)
        #: The bot the conversation runs as; `None`, the main agent.
        self.bot_name = info.bot
        #: A bot's fixed conversation: it lasts forever.
        self.fixed = info.bot is not None and info.source == BOT
        self._bot: Bot | None = None
        self._inbox: list[UserMessage] = []
        #: Steers whose files a reader is reading before they are sent (step 17a).
        self._reading: set[asyncio.Task[None]] = set()
        #: Notices that came while a turn ran: each starts a turn after it as a follow-up,
        #: never steering the one running.
        self._notices: list[_Notice] = []
        #: How many messages went between agents to start the turn running, since the user
        #: last spoke (`atlas.assistant.messages`); 0 in a turn the user started.
        self.agent_hops = 0
        #: The message from another agent the turn running answers, if one started it.
        self._answering: _Notice | None = None
        self._turn: asyncio.Task[None] | None = None
        self._review: asyncio.Task[None] | None = None
        self._titling: asyncio.Task[None] | None = None
        #: It has a title, so the list shows it: once named, a turn needs no write for it.
        self._named = False
        self._client: ModelClient | None = None
        self._loop: AgentLoop | None = None
        self._gate: ApprovalGate | None = None
        self._toolset: Toolset | None = None
        self._commands: list[Commands] = []
        #: The plugins' monitors running for it (step 16k): only the user's own conversations.
        self._monitors = (
            ConversationMonitors(self.notice, lambda: shared.workspace)
            if role is ConversationRole.CONVERSATION
            else None
        )

    @property
    def attended(self) -> bool:
        """The user's own conversation: questions and approvals reach someone."""
        return self.role is ConversationRole.CONVERSATION

    @property
    def running(self) -> bool:
        return self._turn is not None and not self._turn.done()

    @property
    def model(self) -> ModelChoice | None:
        """The model the conversation's turns run on now, once a turn made it."""
        client = self._client
        if client is None:
            return None
        return ModelChoice(client.provider.id, client.model, client.effort)

    @property
    def context(self) -> tuple[int | None, int | None]:
        """The model's input window and what the provider counted at the last call."""
        loop = self._loop
        return (None, None) if loop is None else (loop.window, loop.last_input_tokens)

    def use_trust(self, settings: TrustSettings) -> None:
        """The user's trust rules changed: from the next request."""
        if self._gate is not None:
            self._gate.use_settings(settings)

    @property
    def busy(self) -> bool:
        """A turn or a review is running."""
        return self.running or (self._review is not None and not self._review.done())

    @property
    def history(self) -> list[Message]:
        return self._stored.history

    def terminal(self, command: str, output: str, exit_code: int | None, status: str) -> Sent:
        """A command the user ran from the conversation and its output (step 16f): a terminal
        row, cleaned as a tool's result is (no secret the vault knows, the whole output in the
        spill folder past the limit), which the agent answers on its own, in a turn of its own or
        entering the one running."""
        shared = self._shared
        cleaned = shared.vault.redact(output)
        if len(cleaned) > SPILL_CHARS:
            saved = shared.spill.save(f"terminal-{secrets.token_hex(4)}", cleaned)
            cleaned = cut(cleaned, None if saved is None else str(saved))
        return self.send(terminal_text(command.strip(), cleaned, exit_code, status))

    def send(self, text: str | UserMessage) -> Sent:
        """Start a turn with `text` (or a message with its attachments), or steer the one
        running. `/name` and words after it, `name` a skill the user invokes here, loads the
        skill: its invocation message in its place, the words the user's instruction, from
        the app and from a channel alike."""
        message = text if isinstance(text, UserMessage) else UserMessage(text)
        if self.running and self._unread(message):
            # A steer reaches the loop between its calls, with no wait for a reader: it is read
            # first, and sent then, steering the turn or starting one after it.
            reading = asyncio.create_task(self._read_then_send(message))
            self._reading.add(reading)
            reading.add_done_callback(self._reading.discard)
            return Sent.STEERED
        return self._deliver(message)

    def _deliver(self, message: UserMessage) -> Sent:
        message = self._invoked(message)
        if self.running:
            self._inbox.append(message)
            return Sent.STEERED
        self._turn = asyncio.create_task(self._drive(message))
        return Sent.STARTED

    def _invoked(self, message: UserMessage) -> UserMessage:
        """The message with the skill it names loaded; as it is when it names none."""
        text = message.text.strip()
        if not text.startswith("/") or len(text) < 2 or text[1].isspace():
            return message
        named, *rest = text[1:].split(maxsplit=1)
        # A Telegram group names the bot after the command: `/name@bot`.
        name = named.split("@", 1)[0]
        arguments = rest[0] if rest else ""
        shared = self._shared
        skill = next((s for s in shared.invocable(self.bot_name) if s.name == name), None)
        if skill is None:
            return message
        files = "" if skill.command else support_files(skill.folder, SKILL_FILES_NAMED)
        body = skill.body.replace("${CLAUDE_SKILL_DIR}", str(skill.folder))
        body = body.replace("$ARGUMENTS", arguments.strip())
        loaded = skill_text(skill.name, body, str(skill.folder), files, arguments)
        # A load is a use, for the curator, as the use_skill tool's is.
        bot = None if self.bot_name is None else shared.bots.get(self.bot_name)
        skills = shared.skills_of(bot)
        counted = skills if isinstance(skills, SkillShelves) else shared.usage
        counted.used(skill.name, shared.clock())
        self._skill_used(skill.name)
        return replace(message, text=loaded)

    async def interrupt(self) -> bool:
        """Stop the running turn, and the subagents it delegated to; `False` when no turn runs.
        Every call it made keeps a result."""
        task = self._turn
        if task is None or task.done():
            return False
        await stop(task)
        if self._shared.subagents is not None:
            await self._shared.subagents.stop_all(self.id)
        return True

    def unsent(self) -> list[str]:
        """The messages sent during the last turn that never entered it: a subagent's late
        steers."""
        return [message.text for message in self._take()]

    async def ask_first(self, tool: str, target: str, reason: str) -> str | None:
        """The user's yes, every time, to something Atlas may do only when they ask for it; what
        the model is told when they refuse, or `None`."""
        gate = self._gate
        if gate is None:
            return f"[Atlas: BLOCKED: the user approves this every time ({reason}). Ask them.]"
        return await gate.ask_first(tool, target, reason)

    async def review(self, command: str, reason: str) -> Verdict:
        """The guardian's verdict, for an outside executor that asks to act (step 13); the user
        decides when the conversation has had no turn to make its gate."""
        gate = self._gate
        return Verdict.ESCALATE if gate is None else await gate.review(command, reason)

    async def answer_for_user(self, instruction: str, asked: str) -> str | None:
        """One call to the conversation's model, which reads the conversation's last words and
        answers what an outside executor asks the user (step 13); `None` when it cannot."""
        client = self._client
        if client is None:
            return None
        return await answer_for_user(client, instruction, self.history, asked, self.id)

    async def wait(self) -> None:
        """Until the turn, and the review that followed it, end; a steer being read (step 17a)
        too, with the turn it may start."""
        while (
            self.busy
            or (self._titling is not None and not self._titling.done())
            or any(not reading.done() for reading in self._reading)
        ):
            tasks = (self._turn, self._review, self._titling, *self._reading)
            await asyncio.wait({task for task in tasks if task is not None and not task.done()})

    async def close(self) -> None:
        for task in (*self._reading, self._turn, self._review, self._titling):
            if task is not None and not task.done():
                await stop(task)
        for commands in self._commands:
            await commands.close()
        await self.stop_monitors()

    def background_commands(self) -> list[BackgroundCommand]:
        """Its background commands still running, for the agent map."""
        return [view for commands in self._commands for view in commands.background()]

    def command_output(self, command_id: str) -> str | None:
        """The end of a background command's output, for the agent map; `None` for one it does
        not know."""
        for commands in self._commands:
            text = commands.screen_output(command_id)
            if text is not None:
                return text
        return None

    async def stop_command(self, command_id: str) -> bool:
        """Stop a background command from the agent map, tree and all; `False` when none of its
        own runs by that id."""
        for commands in self._commands:
            if any(view.id == command_id for view in commands.background()):
                await commands.stop(command_id)
                return True
        return False

    def sync_mcp(self) -> None:
        """Take the MCP servers' tools as they are now."""
        if self._toolset is not None:
            _sync(self._toolset, self._shared.mcp_tools, _mcp_allowed(self._bot))
            install_tool_search(self._toolset, self._shared.mcp_sources(), self._window)

    # -- the turn ---------------------------------------------------------------------------------

    @property
    def _window(self) -> int | None:
        return input_window(self._client.info) if self._client is not None else None

    def _emit(self, event: ConversationEvent) -> None:
        self.last_activity = self._shared.clock()
        if isinstance(event, TurnEnded):
            self.last_ended = event
        self._listener(self.id, event)

    def _take(self) -> list[UserMessage]:
        taken, self._inbox = self._inbox, []
        return taken

    def notice(self, text: str, *, hops: int = 0) -> None:
        """Something happened outside a turn (a background command ended): it starts a turn now,
        or after the one running, as a follow-up turn. `hops` is how many messages between agents
        led to it."""
        self._put(_Notice(text, hops))

    def deliver(
        self, text: str, hops: int, origin: str
    ) -> tuple["asyncio.Future[TurnEnded | None]", Callable[[], bool]]:
        """A message from another agent, `origin`: it starts a turn as a notice does. The
        future gives how that turn ended, for the answer to go back (`None` when it was
        stopped before it ended); the call says whether the turn already answered with
        message_agent."""
        done: asyncio.Future[TurnEnded | None] = asyncio.get_running_loop().create_future()
        notice = _Notice(text, hops, done, origin)
        self._put(notice)
        return done, lambda: notice.replied

    def replying_to(self, handle: str) -> bool:
        """Whether the turn running answers a message `handle` sent: a message_agent to them
        now is that answer (the receiver writes back with the tool), and the turn's text
        does not go back as well."""
        answering = self._answering
        if answering is None or answering.origin != handle:
            return False
        answering.replied = True
        return True

    def _put(self, notice: "_Notice") -> None:
        if self.running:
            self._notices.append(notice)
            return
        self._turn = asyncio.create_task(self._drive(UserMessage(notice.text), notice))

    async def _drive(self, message: UserMessage, notice: "_Notice | None" = None) -> None:
        said: UserMessage | None = message
        try:
            while said is not None:
                self.agent_hops = 0 if notice is None else notice.hops
                self._answering = notice
                if notice is None:
                    names = ", ".join(attachment.name for attachment in said.attachments)
                    self._start_title(said.text if said.text.strip() else names)
                await self._run_turn(said)
                self._answering = None
                if notice is not None:
                    notice.settle(self.last_ended)
                if self.role is ConversationRole.SUBAGENT:
                    # One turn: what came too late is its parent's to hear (`missed_steer`).
                    return
                said, notice = _joined(self._take()), None
                if said is None and self._notices:
                    notice = self._notices.pop(0)
                    said = UserMessage(notice.text)
        finally:
            # Stopped: whoever waits for an answer hears there is none.
            for waiting in (notice, *self._notices):
                if waiting is not None:
                    waiting.settle(None)

    def _plugin_command(self, text: str) -> tuple[LoadedCommand | None, str]:
        """The plugin's slash command `text` types (`/video:status rest`), and what follows it."""
        typed = text.strip()
        if not typed.startswith("/"):
            return None, ""
        named, *rest = typed[1:].split(maxsplit=1)
        return self._shared.slash.get(named.split("@", 1)[0]), rest[0] if rest else ""

    async def _run_plugin_command(
        self, command: LoadedCommand, arguments: str, message: UserMessage
    ) -> None:
        """A plugin's slash command (step 16l): its code runs, and what it returns is the reply,
        with no model call. Kept in the conversation, so the model reads what the user saw at its
        next turn (Atlas's own choice, rather than printing it aside)."""
        self._emit(TurnStarted(message.text, message.attachments))
        said: object
        try:
            said = await command.run(arguments, _CommandHost(self.id))
            outcome = TurnOutcome.ANSWERED
        except asyncio.CancelledError:
            self._emit(TurnStopped(()))
            raise
        except Exception as error:  # a plugin's own code: its failure is said, and logged
            _logger.warning(
                "A plugin's command failed.", extra={"plugin": command.plugin}, exc_info=True
            )
            said = f"/{command.typed} failed: {type(error).__name__}: {error}"
            outcome = TurnOutcome.FAILED
        reply = str(said)  # a plugin may return something else than text
        self.history.append(message)
        self.history.append(AssistantMessage(reply))
        self._stored.save()
        self._emit(TurnEnded(outcome, reply, 0, Decimal(0)))
        self._shared.active()

    def _name_before_start(self, message: UserMessage) -> None:
        """A conversation with no title takes its first message's before the turn starts: the
        screens read the list as a turn starts, and the list shows only titled conversations."""
        if self._named:
            return
        named = self._shared.conversations.name_untitled(self.id, message)
        # The user's own words that named nothing found a title already there.
        self._named = named or not from_atlas(message)

    def _skill_used(self, name: str) -> None:
        """A skill was loaded: the monitors waiting for it start."""
        if self._monitors is not None:
            self._monitors.start(self._shared.monitors, skill=name)

    async def stop_monitors(self) -> None:
        """Stop its monitors; each starts again when its time comes (the plugins changed)."""
        if self._monitors is not None:
            await self._monitors.stop()

    async def _run_turn(self, message: UserMessage) -> None:
        # The turn runs in a task of its own, so this reaches its tool calls and no other turn's.
        _CONVERSATION.set(self.id)
        self._name_before_start(message)
        # A new message brings an archived conversation back to the list (step 9e), before the
        # screens read the list again as the turn starts.
        self._shared.conversations.unarchive(self.id)
        command, arguments = self._plugin_command(message.text)
        if command is not None:
            await self._run_plugin_command(command, arguments, message)
            return
        if self._monitors is not None:
            self._monitors.start(self._shared.monitors)
        self._emit(TurnStarted(message.text, message.attachments))
        try:
            loop = self._agent()
        except (ModelsError, BotError) as error:
            # Kept as a turn whose model call failed keeps it: what the user said stays in the
            # conversation, also when no model could be chosen (an empty container, step 10).
            self.history.append(message)
            self._stored.save()
            kind = type(error).__name__
            self._emit(TurnEnded(TurnOutcome.FAILED, str(error), 0, Decimal(0), kind))
            return
        started: set[str] = set()
        finished: set[str] = set()
        try:
            message = await self._described(await self._read(message), loop.client)
            events = loop.run(self.history, message, scope=self.id, steering=self._take)
            async with aclosing(events) as stream:
                async for event in stream:
                    if isinstance(event, ToolStarted):
                        started.add(event.call_id)
                    elif isinstance(event, ToolFinished):
                        finished.add(event.call_id)
                    self._emit(event)
                    if not isinstance(event, _STREAMED):
                        self._stored.save()
        except asyncio.CancelledError:
            self._stored.save()
            for event in _stopped_calls(self.history, started, finished):
                self._emit(event)
            self._emit(TurnStopped(tuple(message.text for message in self._take())))
            raise
        except Exception as error:  # the turn's edge: a task nobody awaits, logged
            _logger.exception("A turn failed.", extra={"conversation": self.id})
            self._stored.save()
            said = f"The turn failed inside Atlas: {type(error).__name__}: {error}"
            self._emit(TurnEnded(TurnOutcome.FAILED, said, 0, None, type(error).__name__))
            return
        finally:
            self._shared.active()
        self._after_turn()

    def _agent(self) -> AgentLoop:
        """The conversation's agent loop, made at its first turn, on the model it picked or the
        default; a later turn goes on with a new pick. Raises `ModelsError` when no model can be
        used; the next turn asks again."""
        shared = self._shared
        bot = None if self.bot_name is None else shared.bots.get(self.bot_name)
        picked = shared.conversations.model(self.id) or _bot_model(bot)
        if self._loop is not None:
            self._follow(picked)
            return self._loop
        self._bot = bot
        client = self._client_for(picked)
        self._client = client
        attended = self.attended
        delegator = (
            None
            if shared.subagents is None or self.role is ConversationRole.SUBAGENT
            else shared.subagents.delegator(self)
        )
        messenger = None if shared.messages is None else shared.messages.messenger(self)
        # Only the main agent, in a conversation the user holds, makes a bot.
        maker = (
            _Maker(shared) if bot is None and self.role is ConversationRole.CONVERSATION else None
        )
        toolset, commands, workspace = shared.tools(
            self.id,
            self.history,
            shared.asker(self.id) or _nobody if attended else None,
            self._window,
            self.notice,
            role=self.role,
            delegator=delegator,
            browser=sees_images(client.info),
            bot=bot,
            messenger=messenger,
            maker=maker,
            only=self._only_tools,
            on_skill=self._skill_used,
            on_command=lambda view: self._listener(self.id, view),
            on_preview=(
                (lambda action, target, label: self._emit(PreviewAsked(action, target, label)))
                if self.role is ConversationRole.CONVERSATION
                else None
            ),
        )
        self._toolset = toolset
        self._commands.append(commands)
        gate = ApprovalGate(
            conversation=self.id,
            approver=shared.approver(self.id) if attended else None,
            guardian=client,
            store=shared.trust,
            settings=shared.settings.trust,
            home=shared.home.root,
            resolve=workspace.resolve,
            clock=shared.clock,
        )
        child = self.role is ConversationRole.SUBAGENT
        instructions: Callable[[], str]
        if bot is not None and not child:
            instructions = _BotInstructions(
                shared, bot.name, str(workspace.root), messaging=messenger is not None
            )
        else:
            # As the conversation starts: a bot made later is not in this roster, and the tool
            # checks the live one.
            roster = roster_section(None, shared.bots.list()) if messenger is not None else ""
            if child:
                # A subagent has no soul and no memory snapshot: it reads no context files and no
                # memory.
                me = identity(str(workspace.root), _system(), self._started())
                instructions = _Fixed(
                    "\n\n".join(part for part in (me, roster, self._prompt or "") if part)
                )
            else:
                instructions = _MainInstructions(
                    shared, str(workspace.root), self._started(), roster
                )
        self._gate = gate
        self._loop = AgentLoop(
            client,
            toolset,
            spill=shared.spill,
            gate=gate,
            instructions=instructions,
            purpose="subagent" if child else "turn",
            limit=_CHILD_LIMIT if child else None,
            attended=self.role is not ConversationRole.ROUTINE,
            refresh_instructions=self.fixed,
            attachments=shared.attachments.view,
            hooks=ConversationHooks(
                lambda: shared.hooks,
                session=self.id,
                cwd=workspace.root,
                auto=lambda: shared.trust.auto_mode(self.id),
                wake=self.notice,
            ),
        )
        return self._loop

    def _unread(self, message: UserMessage) -> bool:
        """Whether the message holds a file a plugin's reader reads that it has not read yet."""
        readers = self._shared.readers
        return any(
            attachment.kind.value in readers and not attachment.description
            for attachment in message.attachments
        )

    async def _read(self, message: UserMessage) -> UserMessage:
        """The message with its files read by the plugins' readers (step 17a), an audio file
        written down: once, as it is sent, and kept with it. A reader that fails or says nothing
        leaves the file's note."""
        if not self._unread(message):
            return message
        shared = self._shared
        read: list[Attachment] = []
        for attachment in message.attachments:
            reader = shared.readers.get(attachment.kind.value)
            path = shared.attachments.path(attachment.id)
            if reader is not None and path is not None and not attachment.description:
                said = await _run_reader(reader, path, attachment)
                if said:
                    attachment = replace(attachment, description=said)
            read.append(attachment)
        return replace(message, attachments=tuple(read))

    async def _read_then_send(self, message: UserMessage) -> None:
        # Delivered as read, even when the reader failed: never read again in a loop.
        self._deliver(await self._read(message))

    async def _described(self, message: UserMessage, client: ModelClient) -> UserMessage:
        """The message with its images described, when the conversation's model does not see
        images: once, as it is sent, and kept with it."""
        shared = self._shared
        if sees_images(client.info) or not any(
            attachment.kind is AttachmentKind.IMAGE and not attachment.description
            for attachment in message.attachments
        ):
            return message
        described: list[Attachment] = []
        for attachment in message.attachments:
            image = (
                shared.attachments.image(attachment)
                if attachment.kind is AttachmentKind.IMAGE and not attachment.description
                else None
            )
            if image is not None:
                seen = await describe_image(
                    shared.models, image, scope=self.id, quarantine=shared.titles
                )
                if seen is not None:
                    attachment = replace(attachment, description=seen)
            described.append(attachment)
        return replace(message, attachments=tuple(described))

    def _client_for(self, picked: ModelChoice | None) -> ModelClient:
        models = self._shared.models
        if picked is None:
            return models.client()
        return models.client(picked.provider, picked.model, effort=picked.effort)

    def _follow(self, picked: ModelChoice | None) -> None:
        """Move the loop to the model the conversation uses now, between turns, keeping its tools.
        A new model leaves a note in the history, the newest only; a new effort alone leaves
        none."""
        current = self._client
        loop = self._loop
        if current is None or loop is None:
            return
        models = self._shared.models
        wanted = models.default() if picked is None else models.resolved(picked)
        if (wanted.provider, wanted.model, wanted.effort) == (
            current.provider.id,
            current.model,
            current.effort,
        ):
            return
        client = self._client_for(picked)
        loop.switch(client)
        if self._gate is not None:
            self._gate.use_guardian(client)
        self._client = client
        if (client.provider.id, client.model) != (current.provider.id, current.model):
            history = self.history
            history[:] = [message for message in history if not is_model_switch(message)]
            history.append(model_switch_note(client.model, client.provider.name))
            _logger.info(
                "The conversation switched models.",
                extra={"conversation": self.id, "provider": client.provider.id},
            )

    def _started(self) -> str:
        info = self._shared.conversations.info(self.id)
        moment = datetime.fromisoformat(info.started_at.replace("Z", "+00:00")).astimezone()
        return f"{moment:%A, %d %B %Y}"

    def _start_title(self, text: str) -> None:
        """A model's title, in the background as the turn starts (atlas.assistant.titles): in the
        user's own conversations, in their first turns, while the title is still the instant one."""
        if self.role is not ConversationRole.CONVERSATION or self.fixed:
            return
        if self._titling is not None and not self._titling.done():
            return
        said = sum(1 for m in self.history if isinstance(m, UserMessage) and not from_atlas(m))
        if said >= TITLE_TURNS:
            return
        if self._shared.conversations.title_source(self.id) is not TitleSource.DERIVED:
            return
        self._titling = asyncio.create_task(self._title(typed_text(text)))

    async def _title(self, text: str) -> None:
        shared = self._shared
        try:
            title = await write_title(shared.models, text, scope=self.id, quarantine=shared.titles)
            if title is not None and shared.conversations.retitle(self.id, title, TitleSource.LLM):
                self._listener(self.id, Titled(title))
        except Exception:  # the title's edge: a task nobody awaits, logged
            _logger.exception("A title failed.", extra={"conversation": self.id})

    # -- after the turn ---------------------------------------------------------------------------

    def _after_turn(self) -> None:
        if not self.attended:
            return
        if self._review is not None and not self._review.done():
            return
        memory_due = review_due(self.history)
        skills_due = skill_review_due(self.history)
        if memory_due or skills_due:
            self._review = asyncio.create_task(
                self._run_review(list(self.history), memory_due, skills_due)
            )

    async def _run_review(self, history: list[Message], memory_due: bool, skills_due: bool) -> None:
        shared = self._shared
        client = self._client
        # The turn's own tools, so the review's request starts as the turn's did, byte for byte,
        # and reads the provider's cache instead of paying the whole conversation again. The
        # review's gate refuses all but its memory and skill tools.
        toolset = self._toolset
        if client is None or toolset is None:  # pragma: no cover - the turn before made them
            return
        try:
            review = BackgroundReview(
                client,
                _review_tools(toolset, shared.mcp_sources(), self._window),
                spill=shared.spill,
            )
            ended = await review.run(history, memory=memory_due, skills=skills_due, scope=self.id)
        except Exception:  # the review's edge: a task nobody awaits, logged
            _logger.exception("A background review failed.", extra={"conversation": self.id})
            return
        finally:
            shared.active()
        actions = review_actions(
            {target.value for target in ended.saved},
            {proposal.target.value for proposal in ended.proposals},
            ended.created,
            ended.updated,
        )
        made = tuple(proposal.id for proposal in ended.proposals)
        diffs = {
            skill_action(name, created=name in ended.created): diff
            for name, diff in ended.diffs.items()
        }
        _logger.info(
            "A background review ended.",
            extra={
                "conversation": self.id,
                "outcome": ended.turn.outcome.value,
                "calls": ended.turn.model_calls,
                "actions": list(actions),
                "proposals": list(made),
            },
        )
        if actions:
            self._emit(Reviewed(actions, made, diffs))


async def stop(task: "asyncio.Task[Any]", again: float = STOP_AGAIN_SECONDS) -> None:
    """Cancel `task` until it ends. A cancel that lands while the HTTP client guards a connection
    is lost: 3 of 300 stops in the first milliseconds of a model call were, measured on
    10/10/2026, and the turn ran to its end. So the cancel is sent again until the task ends."""
    while not task.done():
        task.cancel()
        await asyncio.wait({task}, timeout=again)


@dataclass(slots=True)
class _Notice:
    """What starts a turn outside the user's typing: a notice, or another agent's message."""

    text: str
    #: How many messages between agents led to it, since the user last spoke.
    hops: int = 0
    #: Set for another agent's message: how the turn it started ended.
    done: "asyncio.Future[TurnEnded | None] | None" = None
    #: Who sent that message (`atlas` or a bot's name), and whether the turn already
    #: answered them with message_agent, so its text does not go back a second time.
    origin: str | None = None
    replied: bool = False

    def settle(self, ended: TurnEnded | None) -> None:
        if self.done is not None and not self.done.done():
            self.done.set_result(ended)


@dataclass(frozen=True, slots=True)
class _Fixed:
    """Instructions that do not change: a subagent's, with no memory to read again."""

    text: str

    def __call__(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class _MainInstructions:
    """The main agent's instructions, its soul and its memory read from disk each time."""

    shared: _Shared
    workspace: str
    started: str
    #: The bots it may message, as the conversation started; empty without message_agent.
    roster: str = ""

    def __call__(self) -> str:
        soul = self.shared.soul
        me = identity(
            self.workspace, _system(), self.started, soul=soul.prompt(), soul_file=str(soul.path)
        )
        if self.roster:
            me = f"{me}\n\n{self.roster}"
        return Instructions(self.shared.memory, me)()


@dataclass(frozen=True, slots=True)
class _BotInstructions:
    """A bot's instructions, its definition and its memory read from disk each time."""

    shared: _Shared
    name: str
    workspace: str
    #: Its conversation has message_agent: the roster is part of what it reads.
    messaging: bool = False

    def __call__(self) -> str:
        bot = self.shared.bots.get(self.name)
        me = bot_identity(bot.body, self.workspace, _system())
        if self.messaging:
            me = f"{me}\n\n{roster_section(bot, self.shared.bots.list())}"
        return Instructions(bot_memory(self.shared.home, bot), me)()


@dataclass(frozen=True, slots=True)
class _Maker:
    """The main agent's way to make a bot (`atlas.tools.BotMaker`)."""

    shared: _Shared

    def create(self, name: str, title: str, description: str, mission: str) -> str:
        if self.shared.make_bot is None:  # pragma: no cover - set once the assistant is made
            return "[Atlas: bots cannot be made here.]"
        try:
            bot = self.shared.make_bot(name, title=title, description=description, body=mission)
        except BotError as error:
            return f"[Atlas: {error}]"
        return (
            f"Made the bot @{bot.name} ({bot.title}). It is in the user's Bots list, with its own "
            "conversation. Reach it with message_agent in a conversation started from now on; "
            f"its definition is {bot.folder / 'BOT.md'}."
        )


def _joined(messages: Sequence[UserMessage]) -> UserMessage | None:
    """The messages sent while a turn ran, as the next turn's one message: their words in order,
    and every file they came with; `None` when there are none."""
    if not messages:
        return None
    return UserMessage(
        "\n\n".join(message.text for message in messages if message.text.strip()),
        tuple(attachment for message in messages for attachment in message.attachments),
    )


async def _run_reader(reader: LoadedReader, path: Path, attachment: Attachment) -> str:
    """What a plugin's reader made of a file, within its deadline; empty when it failed, which
    is logged: the file keeps its note."""
    try:
        # Stripped inside: a reader that returns no text fails as one that raises.
        return (
            await asyncio.wait_for(reader.run(path, attachment.media_type), READ_SECONDS)
        ).strip()
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 - a plugin's own code: the note stays, it is logged
        _logger.warning(
            "%s could not read %s: %s",
            reader.plugin,
            attachment.name,
            str(error) or type(error).__name__,
        )
        return ""


def _stopped_calls(
    history: Sequence[Message], started: set[str], finished: set[str]
) -> list[ToolStarted | ToolFinished]:
    """The calls a stop cut, told as cancelled: what the stored conversation will show.

    Stopping closes the loop's stream, so the loop cannot say it; it leaves a cancelled result
    for every call of the round in course at the end of the history, which this reads. A call
    that never started is told as started first, so the live screen holds the same calls.
    """
    results: list[ToolResult] = []
    for message in reversed(history):
        if not isinstance(message, ToolResult):
            break
        results.append(message)
    at = len(history) - len(results) - 1
    reply = history[at] if at >= 0 else None
    if not isinstance(reply, AssistantMessage):
        return []
    calls = {call.id: call for call in reply.tool_calls}
    events: list[ToolStarted | ToolFinished] = []
    for result in reversed(results):
        call = calls.get(result.call_id)
        if call is None or result.status is not ToolStatus.CANCELLED or call.id in finished:
            continue
        if call.id not in started:
            events.append(ToolStarted(call.id, call.name, call.arguments))
        events.append(ToolFinished(call.id, call.name, result.text, ToolStatus.CANCELLED))
    return events


def _agents(files: Mapping[str, Sequence[Path]]) -> dict[str, AgentDefinition]:
    """The plugins' agents from their files; one that cannot be read is logged and left out."""
    found: dict[str, AgentDefinition] = {}
    for plugin, paths in files.items():
        for path in paths:
            try:
                agent = parse_agent(path.read_text(encoding="utf-8"), plugin, path.stem)
            except (OSError, DelegationError) as error:
                _logger.warning("A plugin's agent was left out: %s", error)
                continue
            found.setdefault(agent.name, agent)
    return found


def _bot_model(bot: Bot | None) -> ModelChoice | None:
    """The model a bot's definition names, `provider/model`; `None` when it names none."""
    if bot is None or bot.model is None:
        return None
    provider, _, model = bot.model.partition("/")
    if not provider or not model:
        raise BotError(
            f"The bot {bot.name}'s model is {bot.model!r}; it must be provider/model, such as "
            f"openai/gpt-6-sol. Change it in {bot.folder / 'BOT.md'}."
        )
    return ModelChoice(provider, model, None)


def _review_tools(toolset: Toolset, sources: Mapping[str, str], window: int | None) -> Toolset:
    """The tools a background review is given: the turn's, without the secrets tool (step 16d:
    the review is not given it). Only a turn that had the tool gets a copy, with its tool search
    made again over the copy, so the review's request stays the turn's otherwise (the provider's
    cache holds)."""
    if toolset.get(SECRETS_TOOL) is None:
        return toolset
    copy = Toolset(
        tool for tool in (*toolset.always, *toolset.deferred) if tool.name != SECRETS_TOOL
    )
    install_tool_search(copy, sources, window)
    return copy


def _mcp_allowed(bot: Bot | None) -> frozenset[str] | None:
    """The sources of the MCP servers a bot may use; `None`, every one."""
    if bot is None or bot.mcp_servers is None:
        return None
    return frozenset(f"{_MCP_SOURCE}{server}" for server in bot.mcp_servers)


async def _nobody(questions: tuple[Question, ...]) -> Answers:
    """The asker when nobody is there, as the review's copy of ask_user has."""
    del questions
    return Answers(Outcome.UNDELIVERED, notice="Nobody is there to answer.")


def _sync(toolset: Toolset, mcp_tools: Toolset, allowed: frozenset[str] | None = None) -> None:
    """Put the MCP servers' tools in `toolset` as they are now, source by source; only the
    `allowed` sources, when a bot's definition names its servers."""
    offered = tuple(
        tool
        for tool in (*mcp_tools.always, *mcp_tools.deferred)
        if allowed is None or tool.source in allowed
    )
    held = {
        tool.source
        for tool in (*toolset.always, *toolset.deferred)
        if tool.source.startswith((_MCP_SOURCE, _PLUGIN_SOURCE))
    }
    for source in sorted(held | {tool.source for tool in offered}):
        try:
            toolset.replace(source, [tool for tool in offered if tool.source == source])
        except ToolSetError as error:
            _logger.warning("The tools of %s were left out: %s", source, error)


#: The conversation whose turn runs in this task, for a plugin's tool to know where it runs.
_CONVERSATION: contextvars.ContextVar[str] = contextvars.ContextVar("atlas_conversation")


@dataclass(frozen=True, slots=True)
class _CommandHost:
    """Atlas as a plugin's slash command sees it: the conversation it was typed in."""

    conversation: str


class _ToolHost:
    """Atlas as a plugin's running tool sees it: its progress goes under the call, the
    conversation it runs in, and the files it sends the user."""

    def __init__(self, send: Callable[[str, Path], Awaitable[str]]) -> None:
        self._send = send

    def progress(self, text: str) -> None:
        report_progress(text)

    @property
    def conversation(self) -> str:
        return _CONVERSATION.get("")

    async def send_file(self, path: Path) -> str:
        return await self._send(self.conversation, Path(path))


def _plugin_tool(loaded: LoadedTool, send: Callable[[str, Path], Awaitable[str]]) -> Tool:
    """A plugin's tool as the loop offers it: behind the tool search, as an MCP server's, its
    source the plugin, through the same gate as every tool."""

    async def run(arguments: dict[str, Any]) -> str:
        return await loaded.run(arguments, _ToolHost(send))

    return Tool(
        name=loaded.name,
        description=loaded.description,
        parameters=loaded.parameters,
        run=run,
        read_only=loaded.read_only,
        deferred=True,
        source=f"{_PLUGIN_SOURCE}{loaded.plugin}",
    )


def _installed_browser() -> Path | None:
    """The Chrome, Edge or Chromium installed on this computer."""
    return find_chrome()


def _system() -> str:

    return {"Darwin": "macOS"}.get(platform.system(), platform.system()) or "an unknown system"
