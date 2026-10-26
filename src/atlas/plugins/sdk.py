"""The plugin SDK: all a plugin's Python may import from Atlas (docs/ARCHITECTURE.md, Plugins).

**The contract only grows**: nothing here is removed or
renamed, a new parameter is optional and keyword-only, and a change that must break anything
keeps the old way working, with a warning, for two versions. `API_VERSION` counts the additions;
a plugin names the one it was written for in its manifest, and Atlas refuses a plugin newer than
itself.

A plugin's `__init__.py` defines `register(ctx: PluginContext) -> None`, which runs once when the
plugin is turned on and only registers: the work starts later, when Atlas calls what it
registered. Version 1 registers channels (`Channel`): a way for the user to talk to Atlas from
somewhere else, such as Telegram. Version 2 registers executors (`Executor`): an outside harness,
such as Claude Code, that the user and Atlas hand work to on the workbench (step 16c). Version 3
lets an executor list its sessions, wherever they began (`Executor.sessions`, step 16f), for the
executors tab. Version 4 lets it offer its models and their efforts, and switch them while it runs
(`Executor.models`, `ExecutorInstance.set_model`), for the session's box. Version 5 lets it list its
connectors (`Executor.connectors`), for the side panel. Version 6 says when an executor compacts its
session (`Compacted`, and a `compacted` transcript entry). Version 7 lets a channel hand Atlas the
files people send (`Inbound.files`, step 16g): a photo, a document, a recording. Version 8 lets an
executor list its folders apart from their sessions, and the sessions of one folder
(`Executor.folders`, `Executor.sessions(folder=)`), so the executors tab reads a folder's
sessions only when it is opened. Version 9 lets an executor say how much of its plan's limits the
account has used (`Executor.usage`, and `UsageChanged` as it works), for the side panel. Version 10
lets it describe its slash commands, list them before a session runs, and say when they change
(`SlashCommand`, `Executor.commands`, `CommandsChanged`), for the box's `/`. Version 12 lets an
executor run on several accounts at once, each in the harness's own folder for it
(`Executor.for_account`, Claude Code's `CLAUDE_CONFIG_DIR`), go on with a session of one account
in a copy on another (`ExecutorSpec.resume_from`), and delete a session (`Executor.delete_session`),
step 16h. It takes back version 11's credential kept by Atlas (`ExecutorSpec.credential`,
`Executor.credential_recipe`), the one break of the promise above: it lasted a day (18/10/2026),
only the Claude Code plugin Atlas ships used it, and the user chose a folder per account instead.
Version 13 lets an executor take the files the user attaches to a prompt (`ExecutorFile`,
`ExecutorInstance.prompt(files=)`, `ExecutorSpec.directories`), and say which a session's prompts
came with (`TranscriptEntry.files`), step 16i. Version 14 lets a plugin add tools the model calls
(`PluginContext.register_tool`) and stop what it started when it is turned off
(`PluginContext.on_unload`), step 16j. Version 15 lets its Python hook into the loop's events
(`PluginContext.register_hook`, in Claude Code's events and JSON, the same a plugin's
`hooks/hooks.json` commands get), step 16k. Version 16 brings the rest of a plugin's Python, step
16l: a slash command with code of its own (`PluginContext.register_command`), a terminal command
(`register_cli_command`), a completion on the user's model (`PluginContext.llm`), a message that
starts a turn in a conversation (`PluginContext.notify`), and the conversation a tool runs in
(`ToolHost.conversation`). Each is declared in the manifest, as everything else.
Version 17 lets the plugin's screen in the app (`desktop/plugin.js`, step 16m) call its Python
(`PluginContext.register_call`, over Atlas's own connection, so a screen of a home on another
machine reaches it too).
Version 18 adds the Agent Client Protocol's client (`atlas.plugins.acp`, step 16p), apart from
this module because it starts processes: a plugin for a harness that speaks ACP (the Gemini CLI,
DeepSeek) imports it instead of writing the channel.
Version 19 lets an executor say whether its plan pays on past its limits (`PlanUsage.extra_usage`,
Claude's extra usage, Codex's credits), so a limit that bills rather than stops is shown
(21/10/2026). Version 20 lets it say how many tokens of context a session holds
(`Transcript.context`), so a switch of account says what its first message reads again
(22/10/2026). Version 21 lets a plugin read one kind of attachment into text before the turn
(`PluginContext.register_reader`, step 17a): the voice plugin writes down what an audio file says,
from the app and from every channel alike. Version 22 lets a tool send the user a file
(`ToolHost.send_file`): Atlas keeps it in the home, shows it in the app, and hands it to a channel
that sends files (`FileChannel.send_file`); the voice plugin's spoken answers go so. Version 24
lets a plugin bring themes (`atlas.themes`): `themes/<name>.json`, colors for the app,
with no Python and no screen; the app offers them beside light and dark.

**What costs credit is declared and shown:** `llm` and `notify` call the user's model on the
plugin's own initiative, so the manifest says `"llm": true` or `"notify": true` and the consent
says what it costs.

**A tool only does its work**, as an MCP server's does: Atlas offers it to the model behind the
tool search (`plugin:<name>` is its source), and approvals, the loop's guards and the spill of a
long result are Atlas's, the same as for its own tools. It may not take the name of one of them.

**A channel is one more client of Atlas**, as the app is: Atlas hands it its conversations'
replies, questions and approvals, and it hands Atlas what people send. Who may talk, the commands
(`/new`, `/stop`), which conversation a chat continues and when a question is answered are Atlas's,
the same for every channel; the channel only
carries text, buttons and files. What a model reads of a file (itself, a description, a note) is
Atlas's too, the same as for a file dropped in the app; a channel only downloads it.

**An executor only carries too**, as a channel does: it starts its harness, reads what the harness
says into `ExecutorEvent`s, and writes back the answers Atlas gives it. Who answers a question or a
permission (the user on the card, the guardian, the conversation's model) is Atlas's, and the
plugin never learns which. Each harness is spoken to over its own channel (Claude Code's
stream-json, Codex's `app-server`, the Gemini CLI's ACP), and its sessions are read back in one
shape for all of them (`TranscriptEntry`), so a harness that changes its format changes only its
plugin.

Pure: no I/O, nothing from the rest of Atlas, so a plugin depends on nothing that may change.
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from logging import Logger
from pathlib import Path
from typing import Any, Literal, Protocol

#: What this Atlas offers plugins: 1, channels; 2, executors; 3, an executor's sessions; 4, its
#: models and efforts; 5, its connectors; 6, its compactions; 7, the files a channel receives; 8,
#: an executor's folders apart from their sessions; 9, the plan's limits it has used; 10, its
#: slash commands described; 11, an extra account's credential, taken back by 12, its accounts'
#: folders; 13, the files attached to its prompts; 14, tools, and what stops when it is turned off;
#: 15, hooks; 16, commands, terminal commands, the user's model, and messages to a conversation;
#: 17, the calls its screen makes; 18, the ACP client (`atlas.plugins.acp`); 19, whether the plan
#: pays on past its limits; 20, a session's tokens of context; 21, attachment readers; 22, files
#: a tool sends the user, and a channel that sends them; 23, a screen's desktop powers (a global
#: shortcut, pasting into the program in focus).
API_VERSION = 24


@dataclass(frozen=True, slots=True)
class InboundFile:
    """A file someone sent through a channel (version 7), as the channel downloaded it: its name
    and its bytes, nothing of the platform's own. Atlas keeps it in the home and tells what it is
    from its bytes and its name, as it does for a file dropped in the app, once for every
    channel."""

    #: Its name, or one the channel made up for it (`photo.jpg`, `voice.ogg`).
    name: str
    data: bytes


@dataclass(frozen=True, slots=True)
class Inbound:
    """A message someone sent through a channel."""

    #: Where it came from and where the reply goes: a chat's id, as the channel names it.
    chat: str
    #: Who sent it, a stable id the channel gives (Telegram's user id), never their name.
    user: str
    #: Their name as shown, for the user's screen when they ask to pair.
    name: str
    #: What they wrote: the message, or a file's caption; empty for a file sent alone.
    text: str
    #: The files the message came with (version 7).
    files: tuple[InboundFile, ...] = ()


@dataclass(frozen=True, slots=True)
class Prompt:
    """A question or an approval Atlas asks through a channel: shown with a button per choice."""

    #: Atlas's id for it; a button press names it back (`ChannelHost.choose`).
    id: str
    text: str
    choices: tuple[str, ...]
    #: A typed message answers it too: a question with a free answer.
    typed: bool = False


class ChannelHost(Protocol):
    """Atlas, as a channel sees it."""

    async def receive(self, message: Inbound) -> None:
        """Someone sent `message`. Atlas decides whether they may talk, and answers through
        `Channel.send`."""
        ...

    async def choose(self, prompt: str, choice: int, user: str) -> str | None:
        """`user` pressed the button for `choice` of `prompt`: `None` when it is taken, or what
        to tell them when it is not (someone not allowed, or a prompt already answered)."""
        ...

    def failed(self, reason: str) -> None:
        """The channel stopped and cannot go on by itself (a refused token): the user sees
        `reason` on the plugin's screen."""
        ...


class Channel(Protocol):
    """A way to talk to Atlas from elsewhere."""

    async def run(self, host: ChannelHost) -> None:
        """Receive until cancelled, handing what arrives to `host`. Returns or raises only when
        the channel cannot go on; Atlas then says so, and does not restart it."""
        ...

    async def send(self, chat: str, text: str) -> None:
        """Say `text` (Markdown, as the models write it) in `chat`."""
        ...

    async def working(self, chat: str, busy: bool) -> None:
        """A turn started or ended in `chat`: show that Atlas is working, or stop showing it."""
        ...

    async def ask(self, chat: str, prompt: Prompt) -> None:
        """Show `prompt` in `chat`, with its buttons."""
        ...

    async def settle(self, chat: str, prompt: str, said: str) -> None:
        """`prompt` no longer waits (answered, here or in the app, or expired): replace its
        buttons with `said`."""
        ...


class FileChannel(Channel, Protocol):
    """A channel that sends files too (version 22): a channel without `send_file` gets a line
    naming the file instead, so a channel written before keeps working unchanged."""

    async def send_file(self, chat: str, path: Path, media_type: str) -> None:
        """Send the file at `path` in `chat`: an audio file as the platform's voice message when
        it is one Telegram plays as such (`audio/ogg`), anything else as a document."""
        ...


# -- executors (version 2) ------------------------------------------------------------------------


class ExecutorError(Exception):
    """A harness could not start, answer or go on: what an executor raises, its message shown."""


@dataclass(frozen=True, slots=True)
class Account:
    """Who a harness is logged in as, read with no effect on it (`claude auth status`)."""

    logged_in: bool
    #: The login as the harness names it: an email, for Claude Code.
    login: str = ""
    #: The plan, when the harness says (`max`, `pro`).
    plan: str = ""


@dataclass(frozen=True, slots=True)
class Detection:
    """What `Executor.detect` found on this computer."""

    installed: bool
    #: Its version as it says it (`claude --version`); empty when not installed.
    version: str = ""
    #: The oldest version the plugin drives.
    minimum: str = ""
    #: Installed, and at least `minimum`: only then Atlas offers it.
    supported: bool = False
    #: Who it is logged in as, when it says.
    account: Account | None = None
    #: Why it cannot be used, for the user's screen.
    problem: str = ""


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a harness does over its channel; the screen offers only what is true here."""

    #: It asks the user questions over its channel (Claude Code's `AskUserQuestion`). When not,
    #: its questions come as text at the end of a turn, and the next prompt answers them.
    questions: bool
    #: It asks permission over its channel.
    permissions: bool
    #: A prompt sent while a turn runs enters that turn (`ExecutorInstance.steer`).
    steer: bool
    #: Its own mode that asks no permission is switched while it runs
    #: (`ExecutorInstance.set_auto`).
    auto: bool
    #: It logs into another account (`Executor.log_in`).
    accounts: bool = False
    #: It lists its sessions, wherever they began (`Executor.sessions`, version 3).
    sessions: bool = False
    #: It goes on with a session in a copy, leaving the original as it was (`ExecutorSpec.fork`,
    #: version 3).
    fork: bool = False
    #: It offers its models and their efforts (`Executor.models`), starts on the ones chosen
    #: (`ExecutorSpec.model`) and switches them while it runs (`ExecutorInstance.set_model`),
    #: version 4.
    models: bool = False
    #: It lists its connectors (`Executor.connectors`, version 5).
    connectors: bool = False
    #: It lists the folders its sessions work in apart from them (`Executor.folders`), and the
    #: sessions of one folder (`Executor.sessions(folder=)`), version 8.
    folders: bool = False
    #: It says how much of its plan's limits the account has used (`Executor.usage`, and
    #: `UsageChanged` while it works), version 9.
    usage: bool = False
    #: It lists its slash commands with no session running (`Executor.commands`), version 10.
    commands: bool = False
    #: It runs on other accounts, each in the harness's own folder for it (`Executor.for_account`),
    #: and goes on with another account's session in a copy (`ExecutorSpec.resume_from`), version
    #: 12.
    account_folders: bool = False
    #: It deletes a session it keeps (`Executor.delete_session`), version 12.
    delete_sessions: bool = False
    #: It takes the files the user attaches to a prompt (`ExecutorInstance.prompt(files=)`),
    #: and reads them from `ExecutorSpec.directories` with no permission asked, version 13.
    files: bool = False


#: A connector's state, in Claude Code's words: up with its tools; waiting for the user's own
#: login, which only the harness asks; down, with why; still starting; turned off by the user.
ConnectorStatus = Literal["connected", "needs-auth", "failed", "pending", "disabled"]
#: Where a connector comes from: the account's hosted connectors (claude.ai's), a plugin of the
#: harness, the user's own settings, a project's, one folder's only, or the organization's.
ConnectorOrigin = Literal["account", "plugin", "user", "project", "local", "managed"]


@dataclass(frozen=True, slots=True)
class Connector:
    """An MCP server the harness has for this account, as its own `/mcp` lists it (version 5)."""

    #: As the harness shows it, without the prefix its origin gives ("Gmail", not "claude.ai
    #: Gmail").
    name: str
    status: ConnectorStatus
    origin: ConnectorOrigin
    #: The plugin that brings it, where `origin` is `plugin`.
    plugin: str = ""
    #: How many tools it offers, once connected.
    tools: int = 0
    #: Why it failed, as the harness says.
    error: str = ""


@dataclass(frozen=True, slots=True)
class SlashCommand:
    """A command the harness takes typed as `/name` in a prompt, as its own `/` menu offers it
    (version 10): Claude Code's `SlashCommand` (its `initialize`'s `commands`), ACP's
    `AvailableCommand`."""

    #: Without the slash (`review`, `marketing:seo-audit`).
    name: str
    description: str = ""
    #: What it takes after its name (`<file>`), when it says.
    hint: str = ""
    #: The harness's own command, not a skill or a command a user, project or plugin defines: the
    #: box lists it among the commands, the others among the skills.
    builtin: bool = False


@dataclass(frozen=True, slots=True)
class UsageWindow:
    """One of a plan's limits, as the harness's own usage screen shows it (version 9): Claude
    Code's "Current session" (five hours) and "Current week", Codex's primary and secondary
    windows: one shape for every harness."""

    #: The harness's own name for it, stable between reads (`five_hour`, `seven_day`); a later
    #: read or a `UsageChanged` with the same id replaces it.
    id: str
    #: As the harness's usage screen says it ("Current session"), for a screen that does not
    #: know `minutes`.
    title: str
    #: How much of it is used, 0 to 100.
    used: float
    #: When it starts again, ISO 8601 in UTC; empty when the harness does not say.
    resets_at: str = ""
    #: How long it lasts, in minutes (300, five hours; 10,080, a week); 0 when not said. A screen
    #: names the window by it, in its own language.
    minutes: int = 0
    #: The model it counts alone ("Opus"), or empty for every model.
    model: str = ""


@dataclass(frozen=True, slots=True)
class PlanUsage:
    """How much of its plan's limits the account the harness is logged in as has used (version
    9). No windows and `unavailable`: the login has no plan limits (an API key), and why."""

    windows: tuple[UsageWindow, ...] = ()
    #: The plan, when the harness says (`pro`, `max`, `team`).
    plan: str = ""
    unavailable: str = ""
    #: Past its limits the plan goes on and bills (Claude's extra usage) rather than stopping;
    #: `None` when the harness does not say (version 19).
    extra_usage: bool | None = None


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """A model the harness offers, as its own picker shows it (version 4)."""

    #: What chooses it: Claude Code's alias (`opus`) or a model's full id.
    id: str
    #: How its picker names it ("Opus 5.5").
    name: str
    description: str = ""
    #: The efforts it takes, the lowest first; none, a model that takes no effort.
    efforts: tuple[str, ...] = ()
    #: The model it runs as, when the harness says (`claude-opus-5-5` for `default`): what a
    #: turn's `Started.model` names.
    resolved: str = ""


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """One session a harness keeps, as the executors tab lists it (version 3)."""

    id: str
    #: The folder it works in, as the harness recorded it.
    folder: Path
    #: Its title: the one the harness kept, or the start of its first prompt. Never a model's
    #: call made for Atlas.
    title: str
    #: When it last changed and when it began, ISO 8601 in UTC; `started_at` empty when the
    #: harness did not keep it.
    updated_at: str
    started_at: str = ""
    #: Where it is open right now outside Atlas, as the harness names the program
    #: (`claude-vscode`, `cli`); empty when nowhere. Continuing it here too would fork it.
    elsewhere: str = ""


@dataclass(frozen=True, slots=True)
class SessionFolder:
    """A folder a harness's sessions work in, listed without reading them (version 8)."""

    #: As the harness recorded it, the same as its sessions' `SessionInfo.folder`.
    folder: Path
    #: When its most recent session last changed, ISO 8601 in UTC.
    updated_at: str


@dataclass(frozen=True, slots=True)
class ExecutorSpec:
    """What an executor instance runs as."""

    folder: Path
    #: The harness's session to go on with, or `None` for a new one.
    resume: str | None = None
    #: Its own mode that asks no permission, from the start.
    auto: bool = False
    #: Go on with `resume` in a copy, a session of its own; only where `Capabilities.fork`
    #: (version 3).
    fork: bool = False
    #: The model and the effort it starts on, a `ModelChoice`'s id and one of its efforts;
    #: `None`, the harness's own default. Only where `Capabilities.models` (version 4).
    model: str | None = None
    effort: str | None = None
    #: The account folder `resume` is kept in, when it is another account's than this executor's
    #: (`Executor.account_folder`): it goes on in a copy here, the original left as it was, so
    #: `fork` is set too. Only where `Capabilities.account_folders` (version 12).
    resume_from: Path | None = None
    #: Folders it may read besides `folder`, as its own (the home's attachments, where an
    #: `ExecutorFile` is kept). Only where `Capabilities.files` (version 13).
    directories: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutorFile:
    """A file the user attached to a prompt (version 13), as Atlas keeps it in the home: what it is
    from its first bytes, and where. The harness gets it its own way: an image as itself, any
    file by its path."""

    name: str
    media_type: str
    path: Path


@dataclass(frozen=True, slots=True)
class Started:
    """A session began or went on: its id, its model, and which credential it bills."""

    session: str
    model: str = ""
    credential: str = ""
    #: The slash commands its channel takes (Claude Code's `slash_commands`), names without the
    #: slash (version 3).
    commands: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Said:
    text: str


@dataclass(frozen=True, slots=True)
class Thought:
    text: str


@dataclass(frozen=True, slots=True)
class Called:
    """It called one of its tools, its input as JSON. `files` names what the call writes, when
    the plugin can tell: the record's files changed come from here."""

    call: str
    tool: str
    arguments: str
    files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Returned:
    """One of its tools returned."""

    call: str
    text: str
    failed: bool = False


@dataclass(frozen=True, slots=True)
class LimitReached:
    """The account's plan limit stopped it, until it resets (a Unix time, when it says) or
    another account takes over."""

    resets_at: int | None = None


@dataclass(frozen=True, slots=True)
class Compacted:
    """The harness compacted its session: about `before` tokens of context became `after`, when it
    says (Claude Code's `compact_boundary`). `automatic` when it did so by itself, near its window,
    not on a `/compact` (version 6)."""

    before: int
    after: int | None = None
    automatic: bool = True

    @property
    def text(self) -> str:
        """It in one sentence, as Atlas's own loop says its own."""
        if self.after is None:
            return f"The conversation was compacted from about {self.before:,} tokens."
        return (
            f"The conversation was compacted from about {self.before:,} to {self.after:,} tokens."
        )


@dataclass(frozen=True, slots=True)
class Finished:
    """A turn ended; the instance waits for its next prompt."""

    text: str
    failed: bool = False
    session: str = ""
    cost_usd: Decimal | None = None
    #: How many model calls the turn took, when the harness says.
    turns: int = 0
    #: `completed`, or how the harness says it ended (`aborted_streaming`, `max_turns`).
    reason: str = "completed"


@dataclass(frozen=True, slots=True)
class CommandsChanged:
    """The slash commands its channel takes now, the whole list (version 10): what its
    `initialize` answered when it started, and again whenever it changes (Claude Code's
    `commands_changed`, a skill found in a folder it worked in; ACP's
    `available_commands_update`)."""

    commands: tuple[SlashCommand, ...]


@dataclass(frozen=True, slots=True)
class UsageChanged:
    """The harness said, while it worked, how much of its plan's limits are used now (Claude
    Code's `rate_limit_event`, after each request): the windows it names replace those with the
    same id, the others stay as last read (version 9)."""

    windows: tuple[UsageWindow, ...]


type ExecutorEvent = (
    Started
    | Said
    | Thought
    | Called
    | Returned
    | LimitReached
    | Compacted
    | Finished
    | UsageChanged
    | CommandsChanged
)


@dataclass(frozen=True, slots=True)
class Option:
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class Question:
    """A question a harness asks the user, with its options."""

    text: str
    header: str = ""
    options: tuple[Option, ...] = ()
    multiple: bool = False


@dataclass(frozen=True, slots=True)
class Permission:
    """A harness asks to use one of its tools."""

    tool: str
    #: The action in one line: the command, the file, or the tool's input.
    summary: str
    #: The harness's own sentence for it, and what it adds.
    title: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class Verdict:
    """The answer to a permission; on a refusal, `reason` reaches the harness."""

    allow: bool
    reason: str = ""


#: An answer per question, keyed by its text: a label, labels, or someone's own words. `None`: the
#: questions went unanswered.
type Answers = dict[str, str | list[str]] | None


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    """One step of a session, the same kinds for every harness. A `compacted` says where the
    harness compacted it, what came before stood for by its summary (version 6)."""

    kind: Literal["text", "tool", "question", "answer", "result", "compacted"]
    text: str
    #: The tool's name, for a `tool` or a `question`.
    tool: str = ""
    #: Who wrote a `text`: the user's prompts, or the harness.
    by: Literal["user", "harness"] = "harness"
    #: When it happened, ISO 8601 in UTC, when the harness kept it.
    at: str = ""
    #: The call a `tool` (or a `question`) and its `result` (or `answer`) share (version 3).
    call: str = ""
    #: A `result` that says the tool failed (version 3).
    failed: bool = False
    #: The paths of the files the user attached to a `text` of theirs (version 13).
    files: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Transcript:
    """A session as its harness kept it. `expired`: the harness deleted it (Claude Code does
    after 30 days), and Atlas answers from its own record."""

    entries: tuple[TranscriptEntry, ...] = ()
    expired: bool = False
    #: About how many tokens of context the session holds, what its next message reads again,
    #: when the harness says (version 20).
    context: int | None = None


class ExecutorHost(Protocol):
    """Atlas, as an executor instance sees it. `ask` and `approve` wait as long as the answer
    takes, with no deadline; they are cancelled when the harness withdraws the request or the
    instance stops."""

    def event(self, event: ExecutorEvent) -> None:
        """Something happened: Atlas keeps it and shows it."""
        ...

    async def ask(self, questions: Sequence[Question]) -> Answers: ...

    async def approve(self, permission: Permission) -> Verdict: ...

    def failed(self, reason: str) -> None:
        """The instance cannot go on (its process died between turns): the user sees
        `reason`."""
        ...


class ExecutorInstance(Protocol):
    """One running harness: one account, one folder, one session, its turns."""

    async def prompt(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        """Run a turn on `text`, its events handed to the host, until its `Finished`. Raises
        `ExecutorError` when the harness ends without one. Atlas never calls it while a turn
        runs: it steers. `files`, attached by the user, only where `Capabilities.files`
        (version 13); `text` may then be empty."""
        ...

    async def steer(self, text: str, *, files: Sequence[ExecutorFile] = ()) -> None:
        """`text` into the turn that runs; only where `Capabilities.steer`. `files` as for
        `prompt`."""
        ...

    async def set_auto(self, on: bool) -> None:
        """Its own mode that asks no permission, on or off for the rest of the session; only
        where `Capabilities.auto`."""
        ...

    async def set_model(self, model: str | None, effort: str | None) -> None:
        """The model and the effort from its next request on, as `ExecutorSpec.model` and
        `effort` name them; only where `Capabilities.models` (version 4). Raises
        `ExecutorError` for an effort the model does not take."""
        ...

    async def stop(self) -> None:
        """Interrupt its turn and end it with every process it started. For good."""
        ...


class Executor(Protocol):
    """A harness a plugin drives."""

    #: How the screen and the delegate tool call it ("Claude Code").
    title: str

    async def detect(self) -> Detection:
        """Whether it is installed, its version and account, with no effect on it."""
        ...

    def capabilities(self) -> Capabilities: ...

    async def start(self, spec: ExecutorSpec, host: ExecutorHost) -> ExecutorInstance:
        """Start it in `spec.folder`. Raises `ExecutorError` when it cannot."""
        ...

    async def transcript(self, session: str, folder: Path) -> Transcript:
        """A session's steps, read where the harness keeps them."""
        ...

    async def sessions(self, *, folder: Path | None = None) -> Sequence[SessionInfo]:
        """Every session the harness keeps, wherever it began (its terminal, an editor, Atlas),
        the most recent first, read with no effect on it. Only where `Capabilities.sessions`
        (version 3): Atlas never calls it on a plugin that does not say so. With `folder`, only
        that folder's, as `folders` named it; Atlas passes it only where `Capabilities.folders`
        (version 8)."""
        ...

    async def folders(self) -> Sequence[SessionFolder]:
        """The folders its sessions work in, the one changed last first, read without reading
        each session. Only where `Capabilities.folders` (version 8)."""
        ...

    async def models(self) -> Sequence[ModelChoice]:
        """The models it offers now, for this account, its default first, read with no effect on
        it. Only where `Capabilities.models` (version 4). Raises `ExecutorError`."""
        ...

    async def connectors(self) -> Sequence[Connector]:
        """The MCP servers it has for this account, outside any project, with the state of each,
        read with no effect on it and no model call. Only where `Capabilities.connectors`
        (version 5). Raises `ExecutorError`."""
        ...

    async def commands(self, *, folder: Path) -> Sequence[SlashCommand]:
        """The slash commands it takes in `folder` (its own, the user's, the project's and its
        plugins'), read with no model call, before any session there runs. Only where
        `Capabilities.commands` (version 10). Raises `ExecutorError`."""
        ...

    async def usage(self) -> PlanUsage:
        """How much of its plan's limits the account it is logged in as has used, read with no
        model call. Only where `Capabilities.usage` (version 9). Raises `ExecutorError`."""
        ...

    async def log_in(self, login: str) -> Account:
        """The harness's own login for `login` (empty: whichever account the user signs in to),
        on the user's browser, kept in this account's folder: Atlas never sees the credentials.
        Only where `Capabilities.accounts`, and only when the user asks. Raises
        `ExecutorError` when the login fails."""
        ...

    def account_folder(self) -> Path:
        """The harness's own folder for the account it runs on: its login, its settings and its
        sessions (Claude Code's `~/.claude`, or the user's `CLAUDE_CONFIG_DIR`). Only where
        `Capabilities.account_folders` (version 12)."""
        ...

    def new_account_folder(self, nickname: str) -> Path:
        """Where the harness keeps another account the user calls `nickname`, beside its own
        folder (Claude Code's `~/.claude-<nickname>`), absolute and written one way. Only where
        `Capabilities.account_folders` (version 12)."""
        ...

    def for_account(self, folder: Path) -> "Executor":
        """The same harness on the account kept in `folder`: its login, settings, sessions and
        plan, the folder handed to each of its processes. Only where
        `Capabilities.account_folders` (version 12)."""
        ...

    async def delete_session(self, session: str, folder: Path) -> None:
        """Delete a session it keeps, and what the harness keeps of it alone, in this account's
        folder only; never one that runs. Only where `Capabilities.delete_sessions` (version
        12). Raises `ExecutorError`."""
        ...


# -- tools (version 14) ---------------------------------------------------------------------------


class ToolHost(Protocol):
    """Atlas, as a running tool sees it."""

    def progress(self, text: str) -> None:
        """Show `text` under the call while it runs (`Rendering: 40%`): for the user's screen,
        never the model's."""
        ...

    @property
    def conversation(self) -> str:
        """The conversation the call runs in (version 16), for `PluginContext.notify` later (a
        render that ends after the turn): Atlas's id for it, the hooks' `session_id`."""
        ...

    async def send_file(self, path: Path) -> str:
        """Send the user the file at `path` (version 22): Atlas copies it into the home, shows it
        in the conversation in the app, and sends it in the chat when the conversation is a
        channel's. Returns where the home keeps it. Raises `ValueError` for a file it cannot keep
        (missing, or past the limit of its kind)."""
        ...


class CommandHost(Protocol):
    """Atlas, as a plugin's slash command sees it (version 16)."""

    @property
    def conversation(self) -> str:
        """The conversation it was typed in."""
        ...


#: A slash command's work (version 16): what the user typed after its name, and what it returns
#: is shown in the conversation as the reply, with no model call. It is
#: kept in the conversation, so the model reads it at the next turn. Raising shows the message.
type CommandRun = Callable[[str, CommandHost], Awaitable[str]]

#: A terminal command's work (version 16): the arguments after `atlas <name>`, as typed, and its
#: exit code (`None` is 0). It writes to the terminal itself. Atlas hands the arguments, not an
#: argparse subparser, so nothing of argparse is in the contract.
type CliRun = Callable[[list[str]], Awaitable[int | None] | int | None]

#: A call from the plugin's screen (version 17): the parameters it sent, JSON, and what it
#: returns, JSON too (a dict, a list, text, a number, `None`). Raising answers the screen with the
#: message. It runs on the computer Atlas runs on, wherever the app is.
type CallRun = Callable[[Any], Awaitable[Any]]

#: What a reader makes of an attachment (version 21): the file's path on the computer Atlas runs
#: on and its media type (`audio/ogg`), and the text that stands for it to the model (what an
#: audio file says). Atlas keeps it with the attachment, so a file is read once. Raising, or empty
#: text, leaves the attachment's note as it was; it gets 2 minutes.
type ReadRun = Callable[[Path, str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class LlmMessage:
    """One message of a completion (version 16)."""

    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True, slots=True)
class LlmResult:
    """What a completion returned (version 16)."""

    text: str
    #: The JSON the model returned, parsed and checked against `json_schema`; `None` when no
    #: schema was asked for, or the model did not give valid JSON.
    parsed: object = None
    #: `provider/model`, the model that answered.
    model: str = ""


class PluginLlm(Protocol):
    """The user's model, as a plugin borrows it (version 16): the model a
    conversation that picked none uses, its key from the vault, never seen by the plugin. Each
    call is in the call ledger as the plugin's (`plugin:<name>`), with what it cost."""

    async def complete(
        self,
        prompt: str | Sequence[LlmMessage],
        *,
        system: str = "",
        max_tokens: int | None = None,
        json_schema: Mapping[str, Any] | None = None,
    ) -> LlmResult:
        """One completion, no tools, no reasoning asked for. With `json_schema`, the model is
        asked for JSON that fits it, and `LlmResult.parsed` holds it. Raises `LlmError`."""
        ...


class LlmError(Exception):
    """A completion could not be made: no model with a key, a refused key, no credit."""


#: A tool's work: the arguments the model passed (the model is told the schema, but nothing
#: checks them against it, so read them with care), and what it returns is the result the model
#: reads. Raising fails the call, and the model reads the message. Cancelled when the user stops
#: the turn: stop what it started.
type ToolRun = Callable[[dict[str, Any], ToolHost], Awaitable[str]]

#: A hook (version 15): the event's JSON, as Claude Code sends a command hook on its stdin
#: (`hook_event_name`, `session_id`, `cwd`, `tool_name`, `tool_input`, `prompt`...), and its answer
#: as Claude Code reads a hook's stdout (`{"decision": "block", "reason": ...}`,
#: `{"hookSpecificOutput": {"permissionDecision": "deny", ...}}`), or `None` to let it be. It may
#: be `async`; it gets 30 seconds.
type HookRun = Callable[
    [dict[str, Any]], Awaitable[Mapping[str, Any] | None] | Mapping[str, Any] | None
]


class PluginContext(Protocol):
    """What `register` is given."""

    #: The plugin's name, from its manifest.
    name: str
    #: A folder of its own in the home, kept across updates (Claude Code's
    #: `${CLAUDE_PLUGIN_DATA}`).
    data_folder: Path
    logger: Logger

    def secret(self, name: str) -> str | None:
        """A secret from the vault, among those the manifest declares; `None` when it is not
        stored yet. Asking for one the manifest does not declare raises."""
        ...

    def setting(self, name: str) -> str:
        """A setting the manifest declares: the user's value, or its default."""
        ...

    def register_channel(self, name: str, make: Callable[[], Channel]) -> None:
        """A channel the manifest declares: `make` builds it each time the channel starts, so it
        reads its secrets then."""
        ...

    def register_executor(self, name: str, make: Callable[[], "Executor"]) -> None:
        """An executor the manifest declares under `executors` (version 2): `make` builds it
        each time Atlas loads the plugin's executors, so it reads its settings then."""
        ...

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        run: ToolRun,
        *,
        read_only: bool = False,
    ) -> None:
        """A tool the manifest declares under `tools` (version 14): its name, what the model reads
        of when to call it, the JSON Schema of its arguments (an object), and its work.
        `read_only`: it reads and changes nothing, so it may run beside the round's other
        read-only calls."""
        ...

    def register_hook(self, event: str, run: HookRun, *, matcher: str = "") -> None:
        """A hook on one of the events the manifest names under `hooks` (version 15):
        `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure` or
        `Stop`. `matcher` is Claude Code's: a tool's name (`run_command`, or Claude Code's `Bash`),
        names joined by `|`, or a regular expression; empty, every one. A `PreToolUse` hook's
        `allow` lowers no approval: the user's gate still decides."""
        ...

    def on_unload(self, stop: Callable[[], Awaitable[None] | None]) -> None:
        """`stop` runs when the plugin is turned off, loaded again, or Atlas closes (version 14):
        end the servers and the processes it started (a preview, a render). A `stop` that raises
        is logged, and the others still run."""
        ...

    def register_command(
        self, name: str, run: CommandRun, description: str, *, hint: str = ""
    ) -> None:
        """A slash command the manifest names under `commands` (version 16): typed as
        `/<plugin>:<name>` in a conversation of the app or a channel, it runs `run` and shows what
        it returns, with no model call. `hint` says what it takes (`<file>`)."""
        ...

    def register_cli_command(self, name: str, run: CliRun, description: str) -> None:
        """A terminal command the manifest names under `cli` (version 16): `atlas <name> ...`
        runs `run` with the arguments. A name Atlas's own commands use is refused."""
        ...

    @property
    def llm(self) -> PluginLlm:
        """The user's model (version 16), when the manifest says `"llm": true`; otherwise each
        call raises `LlmError`."""
        ...

    async def notify(self, conversation: str, text: str) -> bool:
        """Bring `text` to `conversation` as a notice (version 16): it
        starts a turn there, or waits for the one running, as a background command's notice
        does. Only when the manifest says `"notify": true`. `False` when the conversation is not
        there, or Atlas is not serving (a terminal command)."""
        ...

    def register_call(self, name: str, run: CallRun) -> None:
        """A call the manifest names under `calls` (version 17), which the plugin's screen makes
        with `ctx.call(name, params)` in the app."""
        ...

    def register_reader(self, kind: Literal["audio"], run: ReadRun) -> None:
        """A reader for the kind of attachment the manifest names under `readers` (version 21):
        `audio`. Before a turn, Atlas hands it each such file the user sent, from the app or a
        channel, and gives the model what it returns in the file's note. One reader a kind: a
        second plugin's is left out and logged."""
        ...
