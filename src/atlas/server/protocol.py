"""The client protocol: every method, event and server request, with its shape. Pure.

Decided on 10/10/2026 (docs/ARCHITECTURE.md, Server). This module is the single source: the server
refuses a name or a parameter not declared here, emits only declared events with their declared
payloads, and a test fails when what the server serves and what this module declares differ.
The app's TypeScript is generated from it; Atlas V3's M-51 found 8 methods its app used that its
declaration lacked.

**The wire is JSON-RPC 2.0** over a WebSocket:

- a **method** goes client to server: `{"jsonrpc": "2.0", "id": 1, "method": "conversation.send",
  "params": {...}}`, answered with `result` or `error`;
- an **event** goes server to client, unanswered: `{"jsonrpc": "2.0", "method": "event",
  "params": {"type": "message.delta", "conversation_id": "...", "seq": 7, "payload": {...}}}`;
- a **server request** goes server to client and waits for the answer with its `id`
  (`srq-<hex>`): `ask_user`'s questions and approvals.

Shapes are standard-library dataclasses (the core carries no Pydantic). A field with a default is
optional; `None` is sent as `null`.
"""

import dataclasses
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import (
    Any,
    Literal,
    TypeAliasType,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

#: The protocol's name, chosen as the WebSocket subprotocol. It changes only when a name is
#: removed or renamed; adding never breaks a client (the plugin contract's rule: it only grows).
#: Version 2 (step 9c, decided with the user on 15/10/2026): `tool.complete`'s `failed` became
#: `status`; an app and a server of different versions refuse each other until both update. The
#: `secret` server request (step 16d) only adds — the number did not have to move (the subprotocol
#: changes only when a name is removed or renamed).
PROTOCOL = "atlas.v2"

#: How a tool call ended (`atlas.models.ToolStatus`).
type ToolStatus = Literal["completed", "failed", "denied", "cancelled", "unanswered"]

#: Any JSON value, for payloads whose shape another part owns (a tool's progress).
type Json = object

# -- errors (JSON-RPC's codes, and one for a missing conversation) ---------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
#: No conversation has this id.
NOT_FOUND = 4001
#: Only the owner's own client may call it: never a channel's bridge (a bot's chat).
FORBIDDEN = 4003

#: The most raw bytes one `attachment.add` part carries: 4 MiB, a third of it again in base64,
#: well inside the 16 MiB a WebSocket frame holds (uvicorn's `ws_max_size`).
ATTACHMENT_PART_BYTES = 4 * 1024 * 1024


class ProtocolError(Exception):
    """A frame the protocol does not take: its code, and what is wrong in words."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# -- shapes shared by several entries --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Empty:
    pass


@dataclass(frozen=True, slots=True)
class ConversationRef:
    conversation_id: str


@dataclass(frozen=True, slots=True)
class ConversationRow:
    """A conversation as lists show it."""

    id: str
    title: str
    source: str
    started_at: str
    updated_at: str
    messages: int
    #: A turn of it runs now.
    running: bool = False


@dataclass(frozen=True, slots=True)
class CallRow:
    """A tool call of a stored assistant message."""

    id: str
    name: str
    #: The arguments as the model wrote them, JSON text.
    args_text: str


#: What an attachment is (`atlas.models.AttachmentKind`): the screen marks each its own way.
type AttachmentKind = Literal["image", "document", "text", "video", "audio", "other"]


@dataclass(frozen=True, slots=True)
class AttachmentRow:
    """A file attached to a message, as the screen shows it (step 16g)."""

    id: str
    name: str
    media_type: str
    size: int
    kind: AttachmentKind
    #: Where the file is on the server's machine, for the side panel to open it.
    path: str = ""


@dataclass(frozen=True, slots=True)
class MessageRow:
    """One stored message, for the screen: archived ones (replaced by a compaction) included.

    A conversation reopened shows what it showed live: an assistant message's readable
    reasoning and its calls, and a result's call and how it ended. Only the times are not kept.
    """

    number: int
    role: str
    kind: str
    text: str
    tool_name: str | None
    created_at: str
    active: bool
    reasoning: str = ""
    calls: list[CallRow] = field(default_factory=list[CallRow])
    #: A tool result's call, and how it ended.
    call_id: str | None = None
    status: ToolStatus | None = None
    #: The files a user message came with (step 16g).
    attachments: list[AttachmentRow] = field(default_factory=list[AttachmentRow])


@dataclass(frozen=True, slots=True)
class OpenRequest:
    """A server request still waiting for an answer, as it was sent."""

    id: str
    method: str
    params: dict[str, Json]


# -- methods ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ListParams:
    limit: int = 50
    #: The `next_cursor` of the page before, for the page that follows it; empty, the first page
    #: (step 9e).
    cursor: str = ""


@dataclass(frozen=True, slots=True)
class ConversationList:
    conversations: list[ConversationRow]
    #: Where the page after this one starts, handed back as `cursor`; empty when this page is the
    #: last.
    next_cursor: str = ""


@dataclass(frozen=True, slots=True)
class SearchParams:
    #: The words typed: each must be in the title or in one message, also as the start of a word.
    query: str
    limit: int = 50


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A conversation the list's search found, and where it matched (step 9e)."""

    conversation: ConversationRow
    #: Its title in pieces, the odd ones the words that matched; one piece when it did not match.
    title: list[str]
    #: When a message matched rather than the title, the words around the match, in pieces as
    #: the title; empty otherwise.
    snippet: list[str]
    #: Who said that message: `user` or `assistant`.
    said_by: str = ""


@dataclass(frozen=True, slots=True)
class SearchResults:
    hits: list[SearchHit]
    #: More conversations matched than `limit`: another word narrows them.
    more: bool = False


@dataclass(frozen=True, slots=True)
class RenameParams:
    conversation_id: str
    #: The name the user gave: its words are kept on one line, cut to a title's length.
    title: str


@dataclass(frozen=True, slots=True)
class Renamed:
    #: The name kept, as the lists show it.
    title: str


@dataclass(frozen=True, slots=True)
class Created:
    conversation_id: str
    #: The folder its file tools start from, where its terminal opens (step 16f).
    folder: str = ""


@dataclass(frozen=True, slots=True)
class Opened:
    conversation: ConversationRow
    messages: list[MessageRow]
    #: A turn is running now; its events follow.
    running: bool
    auto_mode: bool
    #: The process's numbering of events, and the last number of this conversation's events.
    epoch: str
    seq: int
    open_requests: list[OpenRequest]
    #: The folder its file tools start from, where its terminal opens (step 16f).
    folder: str = ""


@dataclass(frozen=True, slots=True)
class EventsParams:
    conversation_id: str
    #: The last event number the client saw.
    after: int


@dataclass(frozen=True, slots=True)
class Events:
    epoch: str
    #: Each event's `params`, as it was sent.
    events: list[dict[str, Json]]
    #: Events after `after` were dropped from the server's memory: read the conversation again.
    truncated: bool
    open_requests: list[OpenRequest]


@dataclass(frozen=True, slots=True)
class SendParams:
    conversation_id: str
    #: May be empty when files are attached.
    text: str
    #: The ids of files `attachment.add` took, in the order the user attached them (step 16g).
    attachments: list[str] = field(default_factory=list[str])


@dataclass(frozen=True, slots=True)
class AttachmentAddParams:
    """One part of a file the user attached (step 16g). A WebSocket frame holds 16 MiB, so a file
    comes in parts of at most `ATTACHMENT_PART_BYTES`: the first with `upload` empty, the rest
    with the id the first answered, each at its `offset`."""

    name: str
    #: The whole file's size in bytes.
    size: int
    #: This part, base64.
    data: str
    upload: str = ""
    offset: int = 0


@dataclass(frozen=True, slots=True)
class AttachmentAdded:
    upload: str
    #: The bytes received so far.
    received: int
    #: The attachment, once the part that completed the file came.
    attachment: AttachmentRow | None = None


@dataclass(frozen=True, slots=True)
class AttachmentLimits:
    """The most each kind of attachment may weigh, in bytes, so the app refuses a drop first."""

    image: int
    document: int
    text: int
    video: int
    audio: int
    other: int
    #: The most raw bytes one `attachment.add` part carries.
    part: int


@dataclass(frozen=True, slots=True)
class TerminalParams:
    """A command the user ran from the conversation, and what came of it (step 16f)."""

    conversation_id: str
    command: str
    #: Its output, as the terminal showed it with its escapes taken out.
    output: str
    #: Its exit code; `null` when the terminal could not tell (cmd, a shell with no marks).
    exit_code: int | None = None
    #: `completed`; `running` (still running after 30 seconds: its output so far);
    #: `unfinished` (the terminal never took it); `ended` (one that was still running ended).
    status: Literal["completed", "running", "unfinished", "ended"] = "completed"


@dataclass(frozen=True, slots=True)
class CommandParams:
    command: str


@dataclass(frozen=True, slots=True)
class CommandCheck:
    """What the trust gate's detection finds in a command, before a ▶ runs it (step 16f)."""

    dangerous: bool
    #: Why, in the gate's words; empty when it is not.
    reason: str = ""


@dataclass(frozen=True, slots=True)
class PreviewReadParams:
    #: An absolute path on the server's machine.
    path: str


@dataclass(frozen=True, slots=True)
class PreviewRead:
    """A file for the side panel's preview, read on the server's machine (step 15)."""

    kind: Literal["html", "markdown", "text", "image", "error"]
    #: The text of a page, Markdown or text; an image's data URL; why not, for an `error`.
    content: str


@dataclass(frozen=True, slots=True)
class TerminalAvailable:
    """Whether the server's machine opens terminals (step 15)."""

    available: bool
    #: Why not, in words the panel shows; empty when it does.
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TerminalOpenParams:
    """A shell on the server's machine for this client, in a folder of that machine's (step 15)."""

    #: The folder it starts in; the user's home folder there when it is no folder.
    cwd: str
    #: What its marks carry, so the app reads only the shell's own: 16 to 64 hex characters.
    nonce: str
    cols: int = 80
    rows: int = 24


@dataclass(frozen=True, slots=True)
class TerminalOpened:
    id: str
    #: The shell as the tab shows it (`pwsh`, `bash`, `cmd`).
    shell: str
    #: It writes marks: a command's end and exit code are known.
    marks: bool


@dataclass(frozen=True, slots=True)
class TerminalRef:
    id: str


@dataclass(frozen=True, slots=True)
class TerminalInputParams:
    id: str
    #: What was typed or pasted, as the terminal view sends it.
    data: str


@dataclass(frozen=True, slots=True)
class TerminalResizeParams:
    id: str
    cols: int
    rows: int


@dataclass(frozen=True, slots=True)
class SendResult:
    #: `started` a turn, or `steered` the one running.
    status: Literal["started", "steered"]


@dataclass(frozen=True, slots=True)
class Interrupted:
    interrupted: bool


@dataclass(frozen=True, slots=True)
class AutoModeParams:
    conversation_id: str
    #: Turn "Auto" on or off; left out, only read it.
    enabled: bool | None = None


@dataclass(frozen=True, slots=True)
class AutoMode:
    enabled: bool


@dataclass(frozen=True, slots=True)
class ProposalChange:
    """One operation of a memory proposal."""

    action: Literal["add", "replace", "remove"]
    #: What an add puts in, or the whole new text of the entry a replace overwrites; empty for a
    #: remove.
    content: str
    #: The whole entry a replace or a remove takes out; `null` for an add.
    entry: str | None


@dataclass(frozen=True, slots=True)
class ProposalRow:
    """A change the background review proposed to the memory, waiting for the user."""

    id: str
    #: `user` (the profile) or `memory`.
    target: Literal["user", "memory"]
    changes: list[ProposalChange]
    #: The conversation the review read; `null` when it read none.
    conversation_id: str | None
    created_at: str
    #: Why it can no longer apply (an entry changed, the result no longer fits); `null` when it
    #: can.
    problem: str | None
    #: The bot whose review made it, and whose memory a `memory` target is; `null`, the main
    #: agent's. The profile is the home's, whoever proposed.
    bot: str | None = None


@dataclass(frozen=True, slots=True)
class ProposalList:
    proposals: list[ProposalRow]


@dataclass(frozen=True, slots=True)
class ProposalRef:
    proposal_id: str


@dataclass(frozen=True, slots=True)
class ProposalApproved:
    #: The change was applied. When not, `error` says why, and the proposal stays.
    applied: bool
    error: str | None = None
    #: The whole text of each entry the change overwrote, and of each it removed.
    replaced: list[str] = field(default_factory=list[str])
    removed: list[str] = field(default_factory=list[str])


@dataclass(frozen=True, slots=True)
class HomeRow:
    name: str
    #: It opens when none is named (`atlas home use`).
    active: bool


@dataclass(frozen=True, slots=True)
class ModelOption:
    """One model of the picker."""

    id: str
    name: str
    #: Whether it reasons; `null` when the catalog does not know.
    reasoning: bool | None
    #: The effort levels it takes, in order; `null` when the catalog does not know.
    efforts: list[str] | None
    #: The effort it runs on when none is picked, Atlas's default onto its levels; `null` for one
    #: that takes none, or whose levels are not known.
    default_effort: str | None


@dataclass(frozen=True, slots=True)
class ProviderOption:
    """A provider with a key in the vault, and the models the picker offers from it."""

    id: str
    name: str
    models: list[ModelOption]


@dataclass(frozen=True, slots=True)
class ConversationModelParams:
    conversation_id: str
    #: Pick a provider and one of its models; left out, only read the conversation's model.
    provider: str | None = None
    model: str | None = None
    #: The effort; left out, the provider's default.
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class ConversationModel:
    #: `null` when no model can be chosen: the vault holds no provider's key.
    provider: str | None
    model: str | None
    effort: str | None
    #: The conversation picked it; otherwise it is the default of config.toml.
    picked: bool


@dataclass(frozen=True, slots=True)
class ModelOptions:
    providers: list[ProviderOption]
    #: What a conversation that picks nothing uses: config.toml's model, or the first provider with
    #: a key; its fields are `null` when no model can be chosen.
    default: ConversationModel


@dataclass(frozen=True, slots=True)
class KeyProvider:
    """A provider that takes a key, as the key screen offers it."""

    id: str
    name: str
    #: The page where the user makes a key; `null` when the catalog names none.
    key_page: str | None
    #: Whether the vault holds its key.
    stored: bool


@dataclass(frozen=True, slots=True)
class KeyProviders:
    #: In the catalog's order.
    providers: list[KeyProvider]


@dataclass(frozen=True, slots=True)
class ProviderConnectParams:
    provider: str
    #: The key the user pasted. It is never sent back.
    key: str


ConnectStatus = Literal["valid", "refused", "no_credit", "limited", "unreachable", "unexpected"]


@dataclass(frozen=True, slots=True)
class ProviderConnected:
    provider: str
    #: What the provider said, asked where it costs nothing.
    status: ConnectStatus
    #: The check's words, for the user.
    detail: str
    #: Whether the key was stored: always, unless the provider refused it.
    stored: bool


@dataclass(frozen=True, slots=True)
class RoutineRow:
    """A routine, as the screen lists it. Times are UTC text."""

    id: str
    name: str
    #: How the schedule reads: `weekdays at 9am`, `every 30m`.
    schedule: str
    enabled: bool
    #: What it runs: a prompt, or a command (the user's only).
    prompt: str | None
    command: str | None
    next_run_at: str | None
    last_run_at: str | None
    #: `ok`, `silent`, `error`, `unknown`, `running`; `null` before its first run.
    last_status: str | None
    #: `user` or `agent`.
    created_by: str
    running: bool
    #: The bot it runs as, whose fixed conversation reads its result; `null`, the main agent.
    bot: str | None = None


@dataclass(frozen=True, slots=True)
class RoutineList:
    routines: list[RoutineRow]


@dataclass(frozen=True, slots=True)
class RoutineRef:
    routine_id: str


@dataclass(frozen=True, slots=True)
class RunRow:
    """One run of a routine, the newest first in a list."""

    number: int
    started_at: str
    ended_at: str | None
    status: str
    #: `on_time`, `late`, `catch_up` or `manual`.
    lateness: str
    #: The conversation a prompt's run is; open it to read the run.
    conversation_id: str | None
    #: The reply or the command's output, cut to its first 2,000 characters.
    output: str
    error: str | None


@dataclass(frozen=True, slots=True)
class RunList:
    runs: list[RunRow]


@dataclass(frozen=True, slots=True)
class RoutineRemoved:
    removed: bool


@dataclass(frozen=True, slots=True)
class RoutinesChanged:
    #: How many routines there are.
    count: int


@dataclass(frozen=True, slots=True)
class RoutineFinished:
    """A routine's run ended: the screen notifies, unless it was silent."""

    routine_id: str
    name: str
    status: str
    conversation_id: str | None
    #: The start of the reply or the output, or why it failed.
    text: str


@dataclass(frozen=True, slots=True)
class BotRef:
    name: str


@dataclass(frozen=True, slots=True)
class BotRow:
    """A bot as the roster shows it."""

    name: str
    title: str
    description: str
    #: The model its definition names, `provider/model`; `None`, the home's.
    model: str | None
    #: Its fixed conversation, once it has one, and when that last changed.
    conversation_id: str | None
    updated_at: str | None
    #: The last thing said in it, cut for a row; empty before anything was.
    preview: str = ""
    #: A turn of its conversation runs now.
    running: bool = False


@dataclass(frozen=True, slots=True)
class BotList:
    bots: list[BotRow]


@dataclass(frozen=True, slots=True)
class BotDefinition:
    """A bot's definition, as its dialog reads and writes it whole."""

    name: str
    title: str
    description: str
    #: Its mission and rules; empty on create, one is composed from the title and the
    #: description.
    mission: str = ""
    #: `provider/model`; `null`, the home's.
    model: str | None = None
    #: What it may use; `null`, everything the home has.
    tools: list[str] | None = None
    mcp_servers: list[str] | None = None
    skills: list[str] | None = None


@dataclass(frozen=True, slots=True)
class BotOptions:
    """What a bot's definition may name."""

    tools: list[str]
    mcp_servers: list[str]
    skills: list[str]


@dataclass(frozen=True, slots=True)
class PluginSecretRow:
    name: str
    #: What it is and how to get it, from the plugin's manifest.
    description: str
    #: Where to get it; empty when the manifest names no page.
    url: str
    #: Whether the vault holds it. Its value is never sent.
    stored: bool


@dataclass(frozen=True, slots=True)
class PluginSettingRow:
    name: str
    value: str
    default: str
    description: str


@dataclass(frozen=True, slots=True)
class PluginRow:
    """A plugin, as the Plugins screen shows it."""

    name: str
    version: str
    description: str
    author: str
    #: Atlas ships it; otherwise the user added it to the home's `plugins/`.
    bundled: bool
    enabled: bool
    #: The user consented to what it declares now; an update that declares more clears it.
    consented: bool
    #: Its channels are running now.
    running: bool
    #: Why it cannot be turned on or stopped: what to fix.
    problem: str | None
    #: What the user consents to, a line each.
    declaration: list[str]
    #: The declaration's fingerprint, sent back with `plugin.enable`.
    consent: str
    channels: list[str]
    secrets: list[PluginSecretRow]
    settings: list[PluginSettingRow]


@dataclass(frozen=True, slots=True)
class PluginList:
    plugins: list[PluginRow]


@dataclass(frozen=True, slots=True)
class PluginRef:
    name: str


@dataclass(frozen=True, slots=True)
class PluginEnableParams:
    name: str
    #: The `consent` of the row the user saw: a plugin changed since is refused.
    consent: str


@dataclass(frozen=True, slots=True)
class PluginSecretParams:
    name: str
    secret: str
    #: The value the user pasted. It is never sent back.
    value: str


@dataclass(frozen=True, slots=True)
class PluginSettingParams:
    name: str
    setting: str
    #: `null` goes back to the default.
    value: str | None = None


@dataclass(frozen=True, slots=True)
class PluginScreenRow:
    """A plugin's screen (step 16m): its `desktop/plugin.js`, which the app loads as ESM."""

    plugin: str
    source: str
    #: What it may do outside the app's window (step 17b): `shortcut`, `paste`.
    desktop: list[str] = field(default_factory=list[str])


@dataclass(frozen=True, slots=True)
class PluginScreens:
    screens: list[PluginScreenRow]


@dataclass(frozen=True, slots=True)
class PluginThemeRow:
    """A theme a plugin brings (SDK version 24): colors for the app, which it offers
    beside light and dark."""

    plugin: str
    name: str
    label: str
    #: `light` or `dark`: the look the theme sits on.
    base: str
    #: Each named color of the app it repaints (`background`) and its hex color.
    colors: dict[str, str]
    #: The label in a language, by locale (`pt-BR`), where it has one.
    labels: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class PluginThemes:
    themes: list[PluginThemeRow]


@dataclass(frozen=True, slots=True)
class PluginCallParams:
    """A plugin's screen calls its Python (step 16m, SDK version 17)."""

    plugin: str
    #: One of the calls its manifest declares under `atlas.calls`.
    name: str
    params: Json = None


@dataclass(frozen=True, slots=True)
class PluginCallResult:
    result: Json = None


# -- the marketplace (step 16s) --------------------------------------------------------------------

type MarketplaceKind = Literal["skill", "mcp", "plugin", "executor"]
#: `installed` for a skill or an MCP server in the home; `off` and `on` for a plugin or an
#: executor; `retired` for an item the marketplace pulled after it was installed, now stopped.
type MarketplaceState = Literal["available", "installed", "off", "on", "retired"]


@dataclass(frozen=True, slots=True)
class MarketplaceRow:
    """An item as the marketplace's screen lists it (step 16s)."""

    name: str
    kind: MarketplaceKind
    title: str
    summary: str
    #: The GitHub account that made it; `Atlas` for what Atlas ships.
    author: str
    license: str
    #: `official`: made by the Atlas team.
    tier: Literal["official", "community"]
    version: str
    state: MarketplaceState
    #: Atlas ships it: it is turned on, never installed.
    shipped: bool
    #: The marketplace has another version than the one installed.
    update: bool
    #: Its picture, from its own repository at its commit; empty for none.
    image: str
    #: Where its code is read, at its commit; an MCP server's page.
    source_url: str
    #: What keeps it from installing or running, or why it was retired.
    problem: str | None


@dataclass(frozen=True, slots=True)
class MarketplaceList:
    name: str
    description: str
    items: list[MarketplaceRow]
    #: When the marketplace was last read well, in UTC; `null` when it never was.
    read_at: str | None
    #: Why it could not be read now: the last read stands in, or nothing does.
    problem: str | None


@dataclass(frozen=True, slots=True)
class MarketplaceListParams:
    #: Read the marketplace again now, not only once the last read is an hour old.
    refresh: bool = False


@dataclass(frozen=True, slots=True)
class MarketplaceRef:
    name: str


@dataclass(frozen=True, slots=True)
class MarketplaceNeed:
    """Something a skill says it needs: a program, an MCP server, or the systems it works on."""

    kind: Literal["program", "server", "system"]
    name: str
    #: `brought`: a server the marketplace installs along with the skill.
    state: Literal["met", "brought", "missing"]


@dataclass(frozen=True, slots=True)
class MarketplaceSecretRow:
    name: str
    description: str
    url: str
    #: The vault holds it. Its value is never sent.
    stored: bool


@dataclass(frozen=True, slots=True)
class MarketplacePage:
    """An item's page: its README at its commit (a skill's own `SKILL.md`), and what it needs."""

    item: MarketplaceRow
    #: Markdown, from the item's repository: raw HTML is not to be rendered.
    readme: str
    #: Where its relative pictures are read, and its relative links lead; empty for none.
    picture_base: str
    link_base: str
    needs: list[MarketplaceNeed]
    secrets: list[MarketplaceSecretRow]
    screenshots: list[str]
    homepage: str
    #: An MCP server's entry as JSON, exactly as it goes into `mcp.json`; empty for the others.
    entry: str


@dataclass(frozen=True, slots=True)
class MarketplaceUpdateParams:
    name: str
    #: Update a skill or a server changed in the home since it came; a skill keeps it as a
    #: version.
    force: bool = False


@dataclass(frozen=True, slots=True)
class MarketplaceDone:
    item: MarketplaceRow
    #: The items installed or updated, those brought along first.
    changed: list[str]
    #: Not updated: it was changed here since it came, so it waits for `force`.
    local_changes: bool


@dataclass(frozen=True, slots=True)
class MarketplaceSecretParams:
    #: The item, an MCP server, that reads the secret.
    name: str
    secret: str
    #: The value the user pasted. It is never sent back.
    value: str


@dataclass(frozen=True, slots=True)
class MarketplaceChanged:
    #: What was installed, updated, removed or retired; empty when the marketplace was read again.
    name: str = ""


@dataclass(frozen=True, slots=True)
class PairingRow:
    """Someone a channel let in, or who waits with a code."""

    channel: str
    #: Their id on the channel.
    user: str
    name: str
    code: str
    asked_at: str
    approved: bool


@dataclass(frozen=True, slots=True)
class PairingList:
    pairings: list[PairingRow]


@dataclass(frozen=True, slots=True)
class PairingApproveParams:
    code: str


@dataclass(frozen=True, slots=True)
class PairingForgetParams:
    channel: str
    user: str


@dataclass(frozen=True, slots=True)
class ConversationsChanged:
    #: The conversation that was made or moved, by a channel, or whose turn began or ended.
    conversation_id: str
    #: `started` as a turn began, or how it ended (`message.complete`'s status); empty, a channel
    #: made or moved it. Every client hears it, attached to the conversation or not, so a list
    #: shows which conversation works and which ended unseen.
    status: str = ""
    #: The name the user gave it, when it was renamed (step 9e): every client's tabs take it, the
    #: lists holding the conversation or not.
    title: str = ""


@dataclass(frozen=True, slots=True)
class PluginsChanged:
    #: How many people wait with a code.
    waiting: int


@dataclass(frozen=True, slots=True)
class BotsChanged:
    #: How many bots the home has.
    count: int


@dataclass(frozen=True, slots=True)
class HomeList:
    #: The home this server serves.
    current: str
    #: Every home of this machine, the default one first.
    homes: list[HomeRow]


@dataclass(frozen=True, slots=True)
class HomeCreateParams:
    name: str
    #: The home whose settings, models, MCP servers, keys, profile, memory and skills it copies
    #: (`atlas home create --clone`); `null`, an empty home.
    clone_from: str | None = None


@dataclass(frozen=True, slots=True)
class HomeBackupParams:
    #: Where the zip goes, an absolute path on the server's machine: what the app's save dialog
    #: gave.
    path: str
    #: A file already there is replaced, by a checked backup only: the save dialog asked the user.
    replace: bool = False


@dataclass(frozen=True, slots=True)
class BackupDownload:
    """A backup made on the server's machine for this client to take down in parts (step 15): a
    remote machine's home is backed up where this machine's save dialog chose."""

    id: str
    #: The files in it, the database counted as one.
    files: int
    #: Its size in bytes: the parts to ask for.
    size: int


@dataclass(frozen=True, slots=True)
class BackupPartParams:
    id: str
    #: Where the part starts, in bytes: each part is at most `attachment.limits`' `part`.
    offset: int


@dataclass(frozen=True, slots=True)
class BackupPart:
    #: The part's bytes, in base64.
    data: str
    #: It was the last one: the server let the backup go.
    done: bool


@dataclass(frozen=True, slots=True)
class BackupWritten:
    path: str
    #: The files in it, the database counted as one.
    files: int
    #: Its size in bytes.
    size: int


@dataclass(frozen=True, slots=True)
class BackupRef:
    #: A backup's zip, an absolute path on the server's machine: what the app's open dialog gave.
    path: str


@dataclass(frozen=True, slots=True)
class BackupInfo:
    """A backup checked whole, as the import dialog shows it."""

    #: The home it was taken from.
    home: str
    #: When it was taken, UTC.
    created_at: str
    #: The Atlas version that wrote it.
    atlas: str
    #: The conversations in it, a routine's runs and subagents counted.
    conversations: int
    #: A name no home has, for the new one: its home's, or `<home>-<date>` when that one exists.
    suggested_name: str


@dataclass(frozen=True, slots=True)
class HomeImportParams:
    #: The backup's zip, an absolute path on the server's machine.
    path: str
    #: The new home's name; no home may have it.
    name: str


@dataclass(frozen=True, slots=True)
class ProposalDiscarded:
    #: `false` when there was no open proposal with this id.
    discarded: bool


@dataclass(frozen=True, slots=True)
class Soul:
    """The main agent's soul, as its screen reads and writes it whole."""

    #: What `SOUL.md` holds; empty when there is no file.
    text: str
    #: Where the file is.
    path: str
    #: Atlas's default identity, which stands in while the soul is blank.
    default: str
    #: The file exists.
    exists: bool


@dataclass(frozen=True, slots=True)
class SoulText:
    text: str


type SettingKind = Literal["text", "lines", "number", "path", "zone"]


@dataclass(frozen=True, slots=True)
class SettingRow:
    """An editable setting of `config.toml`, as the settings screen shows it (step 15b)."""

    section: str
    key: str
    #: How it is edited: one line, one entry per line, a whole number, a full path, a time zone.
    kind: SettingKind
    #: What the file holds, a list one entry per line; `null` when it leaves the setting unset.
    value: str | None
    #: What applies when the file leaves it unset, in words the screen shows; empty when the
    #: screen says it itself.
    default: str
    #: The `ATLAS_*` variable that sets it and wins over the file: the screen shows it locked.
    variable: str | None


@dataclass(frozen=True, slots=True)
class SettingsList:
    settings: list[SettingRow]


@dataclass(frozen=True, slots=True)
class SettingChange:
    section: str
    key: str
    #: The new value as the screen edits it; `null` or blank takes the setting out of the file.
    value: str | None = None


@dataclass(frozen=True, slots=True)
class SettingsWrite:
    #: Written together, all or none.
    changes: list[SettingChange]


type MemoryTarget = Literal["user", "memory"]


@dataclass(frozen=True, slots=True)
class MemoryEntriesParams:
    #: `user` is "About you", `USER.md`; `memory` is the memory, `MEMORY.md`.
    target: MemoryTarget


@dataclass(frozen=True, slots=True)
class MemoryEntries:
    """One of the two memory files, entry by entry, as the settings screen shows it."""

    target: MemoryTarget
    entries: list[str]
    #: The characters the entries take, delimiters included, and the most they may take.
    used: int
    limit: int
    #: Where the file is.
    path: str


@dataclass(frozen=True, slots=True)
class MemoryEdit:
    target: MemoryTarget
    action: Literal["add", "replace", "remove"]
    #: The whole entry replaced or removed, as `memory.entries` gave it: the edit applies to
    #: exactly that entry, or not at all.
    entry: str | None = None
    #: The entry added, or the new text of the one replaced.
    content: str = ""


@dataclass(frozen=True, slots=True)
class HomePicture:
    """The home's picture, a square PNG in base64; `null` when it has none."""

    png: str | None


@dataclass(frozen=True, slots=True)
class SubagentRow:
    """A subagent as its parent's dock shows it."""

    subagent_id: str
    delegation_id: str
    goal: str
    model: str
    #: `running`, `completed`, `failed`, `interrupted`, `stalled` or `unknown`.
    status: str
    started_at: str
    #: The tool it is running or ran last, and how many it ran.
    last_tool: str | None = None
    tools: int = 0
    ended_at: str | None = None
    #: The outside executor running it (`claude-code`), shown apart from the dock; `null` for
    #: Atlas's own subagent.
    executor: str | None = None
    #: Who answers what the executor asks: `user`, or `atlas`.
    decides: str = "user"
    #: Its requests waiting for an answer.
    waiting: int = 0
    #: Its stored conversation, which opens with conversation.open.
    conversation_id: str = ""
    #: The account the executor runs on (Claude Code's login email).
    account: str = ""
    #: The folder an outside executor works in and its session there, which the executors tab
    #: opens (step 16f); empty until it says.
    folder: str = ""
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class SubagentList:
    subagents: list[SubagentRow]


@dataclass(frozen=True, slots=True)
class CommandRow:
    """A background command of the conversation, as the agent map lists it."""

    command_id: str
    #: What it does in a few words, as the model named it; empty when it gave none.
    description: str
    command: str
    #: `running`, `completed` (exit code 0), `failed` (another code) or `stopped`.
    status: str
    started_at: str
    exit_code: int | None = None
    ended_at: str | None = None


@dataclass(frozen=True, slots=True)
class CommandList:
    commands: list[CommandRow]


@dataclass(frozen=True, slots=True)
class CommandRef:
    conversation_id: str
    command_id: str


@dataclass(frozen=True, slots=True)
class CommandOutput:
    #: It is one of the conversation's background commands, running or among the last ended.
    found: bool
    #: The end of its output, the last 4,000 characters.
    text: str = ""


@dataclass(frozen=True, slots=True)
class CommandStopped:
    #: It was the conversation's and still running.
    stopped: bool
    #: The end of its output once stopped.
    text: str = ""


@dataclass(frozen=True, slots=True)
class SubagentRef:
    conversation_id: str
    subagent_id: str


@dataclass(frozen=True, slots=True)
class SubagentSteerParams:
    conversation_id: str
    subagent_id: str
    message: str


@dataclass(frozen=True, slots=True)
class SubagentDecidesParams:
    conversation_id: str
    subagent_id: str
    decides: Literal["user", "atlas"]


@dataclass(frozen=True, slots=True)
class InstanceRow:
    """An executor instance on the workbench, as its card shows it (step 16c)."""

    instance_id: str
    #: The executor's name (`claude-code`), and how the screen calls it.
    executor: str
    title: str
    #: `user` (started on its card) or `atlas` (delegated).
    owner: str
    folder: str
    #: The login it runs on, when its harness says.
    account: str
    #: `starting`, `idle`, `working`, `ended` or `failed`.
    state: str
    #: Its own mode that asks no permission, which passes over Atlas's guardian.
    auto: bool
    #: Atlas answers what it asks, and hands the user what it is unsure of.
    atlas_answers: bool
    waiting: int = 0
    last_tool: str | None = None
    tools: int = 0
    problem: str = ""
    #: The conversation it works for, when Atlas delegated.
    parent_id: str = ""
    #: Its stored conversation, which opens with conversation.open.
    transcript_id: str = ""
    subagent_id: str = ""
    session_id: str = ""
    #: What its harness can do: take a message mid-turn, switch its own mode, switch accounts.
    steer: bool = False
    can_auto: bool = False
    accounts: bool = False
    #: The slash commands its channel takes, names without the slash (step 16f).
    commands: list[str] = field(default_factory=list[str])
    #: It offers models and efforts (`executor.models`) and switches them while it runs.
    can_model: bool = False
    #: The model its last turn ran as, as its harness says (`claude-opus-5-5`); empty before one.
    model: str = ""
    #: The model and the effort chosen for it (`workbench.model`); empty, its harness's default.
    chosen_model: str = ""
    effort: str = ""
    #: The other account it runs on (step 16h, `executor.accounts`); empty, the main account.
    #: `executor` is then its executor on that account (`claude-code@1a2b3c4d`).
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorRow:
    """An executor a plugin drives, as its detection found it, for the workbench and the
    onboarding."""

    name: str
    title: str
    plugin: str
    installed: bool
    #: Installed in a version its plugin drives: only then it can start.
    supported: bool
    version: str = ""
    minimum: str = ""
    #: The login it runs on, and its plan, when it says.
    account: str = ""
    plan: str = ""
    problem: str = ""
    #: Its plugin is on: the workbench starts it. Off, the onboarding offers to turn it on, with
    #: what its plugin declares and the fingerprint plugin.enable takes as the consent.
    enabled: bool = True
    declaration: list[str] = field(default_factory=list[str])
    consent: str = ""
    #: It switches accounts (`executor.account`), from its place in the sidebar.
    accounts: bool = False
    #: Someone is logged in to its harness: until then a turn fails, so the screen asks for a
    #: login first.
    logged_in: bool = False
    #: Other accounts may be added to it (step 16h, `executor.accounts`), each one more row.
    account_folders: bool = False
    #: The row of its executor on another account (step 16h): `name` is `<base>@<account_id>`,
    #: and `nickname` the user's for the account; empty on the executor's own row.
    base: str = ""
    account_id: str = ""
    nickname: str = ""
    #: Its prompts take files (`workbench.prompt`'s `attachments`, step 16i).
    files: bool = False


@dataclass(frozen=True, slots=True)
class ExecutorList:
    executors: list[ExecutorRow]


@dataclass(frozen=True, slots=True)
class WorkbenchList:
    instances: list[InstanceRow]
    executors: list[ExecutorRow]
    #: What the instances asked and still wait on, as `conversation.open` hands a conversation's.
    open_requests: list[OpenRequest]


@dataclass(frozen=True, slots=True)
class InstanceRef:
    instance_id: str


@dataclass(frozen=True, slots=True)
class WorkbenchStartParams:
    executor: str
    #: The project folder it works in; a relative one starts from the workspace.
    folder: str
    #: A session of its harness to go on with (step 16f); left out, a new one. When an instance
    #: already works on it, that one is answered.
    session: str | None = None
    #: Its own mode that asks no permission, from the start; on unless the box turned it off.
    auto: bool = True
    #: Go on with `session` in a copy, a session of its own, leaving the original as it was
    #: (Claude Code's `--fork-session`): what the screen offers for one open elsewhere.
    fork: bool = False
    #: The model and the effort it starts on, as `executor.models` names them; left out, its
    #: harness's own default.
    model: str | None = None
    effort: str | None = None
    #: The account of `executor` it runs on, an `executor.accounts` id (empty, the main account);
    #: left out, the folder's for an executor's own name (step 16h).
    account_id: str | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchModelParams:
    instance_id: str
    #: A model of `executor.models`, and one of its efforts; `null`, the harness's default.
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutorModel:
    """A model an executor offers, as its own picker shows it."""

    #: What chooses it (Claude Code's alias, `opus`, or a full id).
    id: str
    name: str
    description: str = ""
    #: The efforts it takes, the lowest first; empty, none.
    efforts: list[str] = field(default_factory=list[str])
    #: The model it runs as, which an instance's `model` names; empty when the harness does not
    #: say.
    resolved: str = ""
    #: The effort an instance Atlas starts on it runs on when none is chosen, Atlas's default onto
    #: its efforts; empty for one that takes none.
    default_effort: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorModels:
    #: Its default first; empty for an executor that offers none.
    models: list[ExecutorModel]


@dataclass(frozen=True, slots=True)
class ExecutorConnector:
    """An MCP server an executor has for its account, as its own `/mcp` lists it."""

    #: As the harness shows it, without the prefix of its origin ("Gmail").
    name: str
    #: `connected`, `needs-auth` (the harness's own login, asked there), `failed`, `pending` or
    #: `disabled`.
    status: str
    #: `account` (the account's hosted connectors, claude.ai's), `plugin`, `user`, `project`,
    #: `local` or `managed`.
    origin: str
    #: The plugin that brings it, where `origin` is `plugin`.
    plugin: str = ""
    #: How many tools it offers, once connected.
    tools: int = 0
    error: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorConnectors:
    #: Its plugin lists its connectors; when not, `connectors` is empty and says nothing.
    listed: bool
    connectors: list[ExecutorConnector]


@dataclass(frozen=True, slots=True)
class SlashCommandRow:
    """A command the box offers as `/` is typed."""

    #: Without the slash.
    name: str
    description: str = ""
    #: What it takes after its name (`<file>`), when it says.
    hint: str = ""
    #: `command` (Atlas's own, or the harness's own) or `skill`: listed apart, commands first.
    group: str = "skill"


@dataclass(frozen=True, slots=True)
class SlashCommandList:
    commands: list[SlashCommandRow]


@dataclass(frozen=True, slots=True)
class ConversationCommandsParams:
    #: Empty for a conversation not made yet: the main agent's.
    conversation_id: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorCommandsParams:
    executor: str
    #: The session's folder: a project adds its own skills and commands.
    folder: str
    #: The instance working on the session, when one does: what its harness said is the answer.
    instance_id: str = ""


@dataclass(frozen=True, slots=True)
class UsageWindowRow:
    """One of a plan's limits, as the harness's own usage screen shows it."""

    id: str
    #: As the harness titles it ("Current session"), for a window the screen cannot name.
    title: str
    #: How much of it is used, 0 to 100.
    used: float
    #: When it starts again, UTC text; empty when the harness does not say.
    resets_at: str = ""
    #: How long it lasts, in minutes (300, 10080), which names it on screen; 0 when not said.
    minutes: int = 0
    #: The model it counts alone ("Opus"), or empty for every model.
    model: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorUsageRow:
    """How much of its plan's limits the account an executor is logged in as has used."""

    executor: str
    #: Its plugin reads it; when not, the rest is empty and says nothing.
    listed: bool
    windows: list[UsageWindowRow] = field(default_factory=list[UsageWindowRow])
    #: The plan, when the harness says (`pro`, `max`, `team`).
    plan: str = ""
    #: Why the login has no plan limits (an API key).
    unavailable: str = ""
    #: When it was read, or last told by one of its instances, UTC text.
    read_at: str = ""
    #: Past its limits the plan bills rather than stops (Claude's extra usage); `null`, unknown.
    extra_usage: bool | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchPromptParams:
    instance_id: str
    #: May be empty when files are attached.
    text: str
    #: The ids of files `attachment.add` took, in the order the user attached them; only to an
    #: executor that takes files (`ExecutorRow.files`, step 16i).
    attachments: list[str] = field(default_factory=list[str])


@dataclass(frozen=True, slots=True)
class WorkbenchPrompted:
    #: It was working, and took the prompt into that turn.
    steered: bool


@dataclass(frozen=True, slots=True)
class WorkbenchSwitchParams:
    instance_id: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class WorkbenchFolderParams:
    instance_id: str
    folder: str


@dataclass(frozen=True, slots=True)
class WorkbenchAccountParams:
    instance_id: str
    #: The account to log its harness into (an email, for Claude Code); empty, whichever the
    #: user signs in to on the browser.
    login: str


@dataclass(frozen=True, slots=True)
class WorkbenchUseAccountParams:
    instance_id: str
    #: An `executor.accounts` id; empty, the main account.
    account_id: str


@dataclass(frozen=True, slots=True)
class RouteTargetParams:
    """One destination of a session's route: an executor's own name and one of its
    `executor.accounts` ids (empty, the main account), and the model (an `executor.models` id)
    and the effort the session goes on with there; `None`, the ones it ran on."""

    executor: str
    account_id: str = ""
    model: str | None = None
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchRouteParams:
    instance_id: str
    #: In the order the session goes on to them at a plan's limit; empty, none.
    targets: list[RouteTargetParams]
    enabled: bool = True
    #: A copy on another account of the same executor compacts first, where its harness has
    #: `/compact` (on by default).
    compact: bool = True


@dataclass(frozen=True, slots=True)
class RouteStopRow:
    executor: str
    account_id: str
    #: Its executor on that account, as `workbench.executors` names it.
    name: str
    title: str
    #: The account's nickname; empty for the main account.
    nickname: str = ""
    #: Its executor is installed here; one that is not is passed over.
    installed: bool = True
    #: The model and the effort the session goes on with there; `None`, the ones it ran on.
    model: str | None = None
    effort: str | None = None
    #: It reached its plan's limit and has not reset: passed over until it does.
    limited: bool = False


@dataclass(frozen=True, slots=True)
class SessionRouteRow:
    """A session's route (asked for by the user on 21/10/2026): where it goes on, in order, when
    the destination it runs on reaches its plan's limit. With none, or `enabled` off, its
    executor's own accounts take over (`executor.failover`)."""

    instance_id: str
    stops: list[RouteStopRow] = field(default_factory=list[RouteStopRow])
    enabled: bool = True
    #: A copy on another account compacts first, where its harness has `/compact`.
    compact: bool = True


@dataclass(frozen=True, slots=True)
class AccountSwitchRow:
    """What changes if an instance goes on on another account (step 16h)."""

    executor: str
    account_id: str
    #: Who that account is logged in as, when its harness says.
    login: str = ""
    #: The connectors the instance's account has that the other lacks.
    missing: list[str] = field(default_factory=list[str])
    #: Both accounts' connectors were read; when not, nothing is known of what is missing.
    compared: bool = False
    #: About how many tokens the first message on that account reads again, when the harness
    #: says (asked for by the user on 22/10/2026).
    tokens: int | None = None


@dataclass(frozen=True, slots=True)
class AccountRow:
    """An account an executor runs on (step 16h): its own, or another in the harness's folder
    for it."""

    executor: str
    #: Empty for the main account.
    account_id: str
    #: The executor's name on it, an `ExecutorRow.name`.
    name: str
    #: The user's nickname for another account; empty for the main one.
    nickname: str
    #: The harness's folder for it.
    folder: str
    #: Who the harness is logged in as there, when it says.
    login: str = ""
    logged_in: bool = False


@dataclass(frozen=True, slots=True)
class ExecutorAccounts:
    executor: str
    #: In the order they take over in at a plan's limit: the main one first until the user orders
    #: them.
    accounts: list[AccountRow]
    #: Other accounts may be added.
    can_add: bool = False
    #: A session at its account's plan limit goes on on the next account, in this order.
    failover: bool = True
    #: It compacts there before it goes on.
    compact: bool = False


@dataclass(frozen=True, slots=True)
class ExecutorFailoverParams:
    executor: str
    #: Every account's `executor.accounts` id in the order they take over in, the main one as
    #: empty; one left out comes after.
    order: list[str]
    enabled: bool
    compact: bool


@dataclass(frozen=True, slots=True)
class ExecutorAddAccountParams:
    executor: str
    #: The user's nickname for it ("company-b"), which names its folder too.
    nickname: str


@dataclass(frozen=True, slots=True)
class SessionDeleteParams:
    #: An `ExecutorRow.name`: the executor on the account that keeps the session.
    executor: str
    session_id: str
    #: As `executor.sessions` names it.
    folder: str


@dataclass(frozen=True, slots=True)
class ExecutorAccountRef:
    executor: str
    account_id: str


@dataclass(frozen=True, slots=True)
class ExecutorFolderAccountParams:
    executor: str
    #: As `executor.folders` names it.
    folder: str
    #: An `executor.accounts` id; empty, the main account.
    account_id: str


@dataclass(frozen=True, slots=True)
class AccountsChanged:
    #: The executor whose accounts, or whose folders' accounts, changed.
    executor: str


@dataclass(frozen=True, slots=True)
class InstanceActivityPayload:
    instance_id: str
    #: `said`, `thought`, `called` (`text` is the tool and its input), `returned`, `prompted`,
    #: `steered` (a prompt that entered the turn running), `answered` (Atlas answered for the
    #: user), `compacted` (the harness compacted its session, `text` says how much), `account`
    #: (at a plan's limit it went on on the account `text` names), `route` (at a plan's limit
    #: it went on, by its session's route, on the executor and account `text` names), or `note`
    #: (what the screen says of such a switch: another model, a compaction that failed); a
    #: `failed` `account` or `route` says why it could not.
    kind: str
    text: str
    failed: bool = False
    #: The call a `called` and its `returned` share, and the tool called (step 16f).
    call_id: str = ""
    tool: str = ""
    #: The files attached to a `prompted` or a `steered` (step 16i).
    attachments: list[AttachmentRow] = field(default_factory=list[AttachmentRow])


@dataclass(frozen=True, slots=True)
class ExecutorRef:
    #: The executor's name (`claude-code`).
    executor: str


@dataclass(frozen=True, slots=True)
class ExecutorAccountParams:
    executor: str
    #: The account to log its harness into (an email, for Claude Code); empty, whichever the
    #: user signs in to on the browser.
    login: str


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One session of an executor, as the executors tab lists it (step 16f)."""

    executor: str
    session_id: str
    #: The folder it works in, as its harness recorded it.
    folder: str
    #: What its harness kept as its title, or the start of its first prompt.
    title: str
    #: UTC text; `started_at` empty when the harness did not keep it.
    updated_at: str
    started_at: str
    #: Its instance here: `starting`, `idle`, `working`, `failed`; empty when none runs on it.
    state: str
    instance_id: str
    #: What its instance asks waits for an answer.
    waiting: int
    #: Atlas delegated it.
    by_atlas: bool
    #: Where it is open right now outside Atlas, as its harness names the program
    #: (`claude-vscode`, `cli`); empty when nowhere. Continuing it here too would fork it.
    elsewhere: str = ""


@dataclass(frozen=True, slots=True)
class SessionList:
    #: The sessions whose harness has not kept them yet first, then the most recent first.
    sessions: list[SessionRow]


@dataclass(frozen=True, slots=True)
class SessionListParams:
    executor: str
    #: Only this folder's sessions, as `executor.folders` named it; left out, every folder's.
    folder: str | None = None


@dataclass(frozen=True, slots=True)
class SessionFolderRow:
    """A folder an executor's sessions work in, listed before its sessions are read."""

    executor: str
    folder: str
    #: When its most recent session last changed, UTC text.
    updated_at: str
    #: The other account its new sessions start on (step 16h); empty, the main account.
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class SessionFolderList:
    #: The folders of instances not kept yet first, then the one changed last first.
    folders: list[SessionFolderRow]


@dataclass(frozen=True, slots=True)
class SessionReadParams:
    executor: str
    session_id: str
    #: The folder it works in, where its harness looks first.
    folder: str


@dataclass(frozen=True, slots=True)
class TranscriptRow:
    """One step of a session, the same kinds for every harness."""

    #: `text`, `tool`, `question`, `answer`, `result`, or `compacted` (where the harness compacted
    #: it, `text` saying how much).
    kind: str
    text: str
    #: The tool, for a `tool` or a `question`.
    tool: str
    #: Who wrote a `text`: `user` or `harness`.
    by: str
    #: When, UTC, when the harness kept it.
    at: str
    #: The call a `tool` and its `result` share.
    call: str
    #: A `result` that says the tool failed.
    failed: bool
    #: The files the user attached to a `text` of theirs, as the home keeps them (step 16i).
    attachments: list[AttachmentRow] = field(default_factory=list[AttachmentRow])


@dataclass(frozen=True, slots=True)
class SessionRead:
    entries: list[TranscriptRow]
    #: The harness no longer keeps it (Claude Code deletes a session 30 days after its last use).
    expired: bool


@dataclass(frozen=True, slots=True)
class SubagentSession:
    """Where an outside executor's task of a conversation works, for its `delegate` line."""

    #: Its session is known: the executors tab opens it.
    found: bool
    executor: str = ""
    folder: str = ""
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class SubagentControl:
    #: The subagent took it: it was this conversation's and still running.
    accepted: bool
    #: What happened, in the words the model would get.
    text: str


# -- the update of the server's machine (docs/ARCHITECTURE.md, The update of a Compose host) ------


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """Whether the server's installation has an update, as the app's update button shows it
    (`atlas.installation.UpdateStatus`)."""

    supported: bool
    available: bool
    #: How it updates: `compose`, by its host updater; `native` installations update from the app
    #: on their own machine, so a remote one offers nothing here.
    kind: str
    reason: str = ""
    current: str = ""
    target: str = ""
    behind: int = 0
    commits: list[str] = field(default_factory=list[str])
    commits_pt_br: list[str] = field(default_factory=list[str])
    dirty: bool = False
    error: str = ""
    #: Why CI did not verify the target: the user confirms it explicitly. "" when verified.
    unverified: str = ""


@dataclass(frozen=True, slots=True)
class UpdateApplyParams:
    #: The commit the user was shown: the update refuses when `main` moved since.
    target: str
    #: The user confirmed a target CI did not verify.
    allow_unverified: bool = False


@dataclass(frozen=True, slots=True)
class UpdateApplied:
    #: The update was admitted, and runs on the host; the server restarts as it ends.
    ok: bool
    #: Why not, when not.
    error: str = ""


@dataclass(frozen=True, slots=True)
class UpdateEnded:
    """How the last update ended (`atlas.installation.HandedOff`)."""

    ok: bool
    before: str = ""
    after: str = ""
    commits: list[str] = field(default_factory=list[str])
    error: str = ""
    #: Where the log is, on the host.
    log: str = ""
    ended_at: str = ""
    reopen: str = ""


@dataclass(frozen=True, slots=True)
class UpdateProgress:
    #: An update runs on the host now.
    running: bool
    #: Its step: `building`, `snapshot`, `starting`, `rolling back`.
    step: str = ""
    started_at: str = ""
    target: str = ""
    #: How the last one ended, or `null` before any.
    ended: UpdateEnded | None = None


@dataclass(frozen=True, slots=True)
class UpdateLog:
    #: The tail of the last update's log, at most 64 KiB.
    text: str


@dataclass(frozen=True, slots=True)
class Method:
    name: str
    params: type[Any]
    result: type[Any]
    doc: str


METHODS: Mapping[str, Method] = {
    method.name: method
    for method in (
        Method("ping", Empty, Empty, "Answers at once; a client's heartbeat."),
        Method(
            "conversation.list",
            ListParams,
            ConversationList,
            "The user's conversations, the latest activity first, a page at a time: "
            "`next_cursor` reads the page that follows, and a `limit` of as many rows as a client "
            "holds reads them again (step 9e).",
        ),
        Method(
            "conversation.search",
            SearchParams,
            SearchResults,
            "The conversations of the list whose title, or one of whose messages, holds every word "
            "of `query`, each word also matching the words it begins, through the conversations' "
            "search index (step 9e): those found by their title first, the latest activity first, "
            "then those found by a message, the best match first; each says where it matched.",
        ),
        Method(
            "conversation.rename",
            RenameParams,
            Renamed,
            "Name a conversation as the user chose (step 9e): its title from now on, of source "
            "`user`, which no title model replaces; every client hears it "
            "(`conversations.changed`, with the name), and its lists and tabs take it.",
        ),
        Method(
            "conversation.create",
            Empty,
            Created,
            "A new, empty conversation; the client is attached to it.",
        ),
        Method(
            "conversation.open",
            ConversationRef,
            Opened,
            "A conversation's stored messages and state; the client is attached to it and gets "
            "its events and server requests from now on.",
        ),
        Method(
            "conversation.events",
            EventsParams,
            Events,
            "The events after a number, for a client that came back; it is attached again.",
        ),
        Method(
            "conversation.leave",
            ConversationRef,
            Empty,
            "Stop getting a conversation's events and server requests.",
        ),
        Method(
            "conversation.send",
            SendParams,
            SendResult,
            "Start a turn, or steer the one running: the message enters after the round of tools "
            "in course.",
        ),
        Method(
            "attachment.add",
            AttachmentAddParams,
            AttachmentAdded,
            "One part of a file the user attached, kept in the home's attachments/; the part that "
            "completes it answers with the attachment, for `conversation.send` (step 16g).",
        ),
        Method(
            "attachment.limits",
            Empty,
            AttachmentLimits,
            "The most each kind of attachment may weigh, and the size of a part (step 16g).",
        ),
        Method(
            "conversation.commands",
            ConversationCommandsParams,
            SlashCommandList,
            "What the box offers as `/` is typed in a conversation of Atlas's or a bot's: Atlas's "
            "own commands, which the screen runs (`/new`, not in a bot's; `/stop`), then the "
            "skills the user invokes there; `/name words` sent loads that skill.",
        ),
        Method(
            "conversation.terminal",
            TerminalParams,
            SendResult,
            "A command the user ran from the conversation (the ▶ on a shell block, or `!`) and its "
            "output: a terminal row, which the agent answers in a turn of its own or entering the "
            "one running (step 16f).",
        ),
        Method(
            "preview.read",
            PreviewReadParams,
            PreviewRead,
            "A file of the server's machine for the side panel's preview, at most 5 MB, by what "
            "it is: HTML, Markdown, an image or text (step 15).",
        ),
        Method(
            "terminal.available",
            Empty,
            TerminalAvailable,
            "Whether the server's machine opens terminals, and why not (step 15).",
        ),
        Method(
            "terminal.open",
            TerminalOpenParams,
            TerminalOpened,
            "A shell on the server's machine for this client; its output comes as terminal.output "
            "events and its end as terminal.exit. It closes with the connection (step 15).",
        ),
        Method(
            "terminal.input",
            TerminalInputParams,
            Empty,
            "Type into one of this client's terminals.",
        ),
        Method(
            "terminal.resize",
            TerminalResizeParams,
            Empty,
            "Give one of this client's terminals its view's size.",
        ),
        Method(
            "terminal.close",
            TerminalRef,
            Empty,
            "Stop one of this client's terminals; nothing when it is already gone.",
        ),
        Method(
            "trust.check",
            CommandParams,
            CommandCheck,
            "Whether the trust gate's detection finds a command dangerous, so the ▶ asks before it "
            "runs it (step 16f).",
        ),
        Method(
            "conversation.interrupt",
            ConversationRef,
            Interrupted,
            "Stop the running turn. Its open server requests are withdrawn.",
        ),
        Method(
            "conversation.auto_mode",
            AutoModeParams,
            AutoMode,
            "Read or set the conversation's 'Auto': the guardian and the questions are skipped; "
            "the floors still block, and the ask rules still ask.",
        ),
        Method(
            "memory.proposals",
            Empty,
            ProposalList,
            "The memory proposals waiting for the user, oldest first, each tried against the "
            "memory as it is now.",
        ),
        Method(
            "memory.approve",
            ProposalRef,
            ProposalApproved,
            "Apply a memory proposal, only if the entries it changes are as they were; it stays "
            "when it cannot apply.",
        ),
        Method(
            "memory.discard",
            ProposalRef,
            ProposalDiscarded,
            "Drop a memory proposal without applying it.",
        ),
        Method(
            "soul.read",
            Empty,
            Soul,
            "The main agent's soul, SOUL.md, for its screen.",
        ),
        Method(
            "soul.write",
            SoulText,
            Soul,
            "Write the main agent's soul whole; it applies from the next conversation.",
        ),
        Method(
            "settings.read",
            Empty,
            SettingsList,
            "The editable settings of config.toml, for the settings screen (step 15b), each with "
            "the variable that wins over it.",
        ),
        Method(
            "settings.write",
            SettingsWrite,
            SettingsList,
            "Write editable settings to config.toml, changing only their lines, and apply them "
            "without a restart; a refused value writes nothing.",
        ),
        Method(
            "memory.entries",
            MemoryEntriesParams,
            MemoryEntries,
            "USER.md or MEMORY.md entry by entry, for the settings screen's Personalization.",
        ),
        Method(
            "memory.edit",
            MemoryEdit,
            MemoryEntries,
            "Add, replace or remove one entry of USER.md or MEMORY.md through the memory tool's "
            "door; it applies from the next conversation.",
        ),
        Method(
            "home.picture",
            Empty,
            HomePicture,
            "The picture of the home this server serves, beside its name in the app's sidebar.",
        ),
        Method(
            "home.set_picture",
            HomePicture,
            HomePicture,
            "Keep a square PNG as the home's picture, or take it away with `null`; a backup "
            "carries it.",
        ),
        Method(
            "model.options",
            Empty,
            ModelOptions,
            "The models the picker offers: the providers with a key in the vault and their "
            "curated models, with the effort levels each takes.",
        ),
        Method(
            "provider.list",
            Empty,
            KeyProviders,
            "The providers that take a key, with the page to make one and whether the vault holds "
            "it: the key screen's list.",
        ),
        Method("plugin.list", Empty, PluginList, "The home's plugins, the shipped ones first."),
        Method(
            "plugin.enable",
            PluginEnableParams,
            PluginRow,
            "Turn a plugin on, with the consent to what it declares, and start its channels.",
        ),
        Method("plugin.disable", PluginRef, PluginRow, "Turn a plugin off and stop its channels."),
        Method(
            "plugin.secret",
            PluginSecretParams,
            PluginRow,
            "Store a secret a plugin declares in the vault, and start it again if it is on. The "
            "value is never sent back.",
        ),
        Method(
            "plugin.setting",
            PluginSettingParams,
            PluginRow,
            "Change a setting a plugin declares, and start it again if it is on.",
        ),
        Method(
            "plugin.reload",
            Empty,
            PluginList,
            "Load every plugin that is on again from its files (step 16n, Claude Code's "
            "/reload-plugins): what the server does by itself when a plugin's file is saved.",
        ),
        Method(
            "plugin.screens",
            Empty,
            PluginScreens,
            "The screens of the plugins that are on, for the app to load (step 16m); read again "
            "on plugins.changed.",
        ),
        Method(
            "plugin.themes",
            Empty,
            PluginThemes,
            "The themes of the plugins that are on, for the app to offer beside light and dark "
            "(SDK version 24); read again on plugins.changed.",
        ),
        Method(
            "plugin.call",
            PluginCallParams,
            PluginCallResult,
            "A plugin's screen calls its Python (step 16m): what it returns, or an error with "
            "the message it raised.",
        ),
        Method(
            "marketplace.list",
            MarketplaceListParams,
            MarketplaceList,
            "The marketplace (step 16s): what it offers, what Atlas ships and what came from it, "
            "read again when the last read is an hour old or when asked.",
        ),
        Method(
            "marketplace.page",
            MarketplaceRef,
            MarketplacePage,
            "An item's page: its README at its commit, and what it needs in this home.",
        ),
        Method(
            "marketplace.install",
            MarketplaceRef,
            MarketplaceDone,
            "Install an item, with the MCP servers a skill needs; a plugin arrives off, for the "
            "consent of plugin.enable.",
        ),
        Method(
            "marketplace.update",
            MarketplaceUpdateParams,
            MarketplaceDone,
            "Bring an installed item to the marketplace's version; one changed here waits for "
            "force.",
        ),
        Method(
            "marketplace.remove",
            MarketplaceRef,
            MarketplaceRow,
            "Take away an item installed from the marketplace: a skill is archived, a plugin's "
            "data stays.",
        ),
        Method(
            "marketplace.secret",
            MarketplaceSecretParams,
            MarketplacePage,
            "Store a secret an MCP server of the marketplace reads, and try the server again. "
            "The value is never sent back.",
        ),
        Method(
            "pairing.list",
            Empty,
            PairingList,
            "The people the channels let in, and those who wait with a code.",
        ),
        Method(
            "pairing.approve",
            PairingApproveParams,
            PairingRow,
            "Let in whoever holds a code; they send their message again.",
        ),
        Method(
            "pairing.forget",
            PairingForgetParams,
            Empty,
            "Remove someone from a channel, waiting or let in.",
        ),
        Method(
            "provider.connect",
            ProviderConnectParams,
            ProviderConnected,
            "Check a provider's key where it costs nothing, and store it in the vault unless the "
            "provider refuses it. The key is never sent back.",
        ),
        Method(
            "conversation.model",
            ConversationModelParams,
            ConversationModel,
            "Read or pick the conversation's model and effort. A pick is for this conversation "
            "only and applies from its next turn; a new conversation starts from config.toml.",
        ),
        Method("routine.list", Empty, RoutineList, "The home's routines, by name."),
        Method(
            "routine.runs", RoutineRef, RunList, "A routine's runs, the newest first, at most 50."
        ),
        Method(
            "routine.run",
            RoutineRef,
            RoutineRow,
            "Run a routine now, in the background, ahead of its schedule, which stays.",
        ),
        Method("routine.pause", RoutineRef, RoutineRow, "Stop a routine firing until resumed."),
        Method(
            "routine.resume",
            RoutineRef,
            RoutineRow,
            "Fire a routine again; a slot that came due while paused runs once.",
        ),
        Method("routine.remove", RoutineRef, RoutineRemoved, "Delete a routine and its runs."),
        Method(
            "home.list",
            Empty,
            HomeList,
            "The homes of this machine and the one this server serves.",
        ),
        Method(
            "home.create",
            HomeCreateParams,
            HomeList,
            "Make a named home, empty or a copy of another's settings, keys, memory and skills; "
            "the client then opens it.",
        ),
        Method(
            "home.backup",
            HomeBackupParams,
            BackupWritten,
            "Write this home's backup, checked, at a path on the server's machine: the zip of "
            "`atlas backup`, never the vault.",
        ),
        Method(
            "home.backup_download",
            Empty,
            BackupDownload,
            "Back up the home on the server's machine for this client to take down with "
            "home.backup_part, as a remote machine's home is backed up (step 15). It is this "
            "connection's, and goes with the last part or with the connection.",
        ),
        Method(
            "home.backup_part",
            BackupPartParams,
            BackupPart,
            "One part of a backup made by home.backup_download, at most 4 MiB, from `offset`.",
        ),
        Method(
            "home.inspect",
            BackupRef,
            BackupInfo,
            "Check a backup's zip whole and say what it holds, for the import dialog. Nothing is "
            "written.",
        ),
        Method(
            "home.import",
            HomeImportParams,
            HomeList,
            "Make a new named home from a backup, checked whole first and published only once "
            "restored; the client then opens it.",
        ),
        Method(
            "update.check",
            Empty,
            UpdateInfo,
            "Whether the server's installation has an update, for the update button when the "
            "server is another machine's: a Compose host's, through its host updater. The "
            "owner's client only.",
        ),
        Method(
            "update.apply",
            UpdateApplyParams,
            UpdateApplied,
            "Update the server's installation to the commit the user was shown: the host updater "
            "builds it while this server runs, then restarts it. The owner's client only.",
        ),
        Method(
            "update.status",
            Empty,
            UpdateProgress,
            "The update running on the server's host, and how the last one ended: asked again "
            "after the restart's reconnection.",
        ),
        Method("update.log", Empty, UpdateLog, "The tail of the last update's log, on the host."),
        Method("bot.list", Empty, BotList, "The home's bots, by name."),
        Method(
            "bot.open",
            BotRef,
            Created,
            "A bot's fixed conversation, made the first time it is asked for; "
            "the client attaches to it and opens it as any conversation.",
        ),
        Method(
            "bot.create",
            BotDefinition,
            BotRow,
            "Make a bot; with no mission, one is composed from its title and description.",
        ),
        Method(
            "bot.update",
            BotDefinition,
            BotRow,
            "Write a bot's definition anew, whole; its memory, skills and conversation stay.",
        ),
        Method("bot.describe", BotRef, BotDefinition, "A bot's definition, for its dialog."),
        Method(
            "bot.delete",
            BotRef,
            Empty,
            "Delete a bot, its memory, its skills and its routines; its conversations are kept.",
        ),
        Method(
            "bot.options",
            Empty,
            BotOptions,
            "The tools, MCP servers and skills a bot's definition may name.",
        ),
        Method(
            "subagent.list",
            ConversationRef,
            SubagentList,
            "A conversation's running subagents, for its dock.",
        ),
        Method(
            "subagent.steer",
            SubagentSteerParams,
            SubagentControl,
            "Queue a course correction into a running subagent; it enters after the subagent's "
            "current tool calls.",
        ),
        Method(
            "subagent.decides",
            SubagentDecidesParams,
            SubagentControl,
            "Who answers what an outside executor asks from now on: the user, or Atlas (the "
            "guardian for permissions, the conversation's model for questions).",
        ),
        Method(
            "subagent.interrupt",
            SubagentRef,
            SubagentControl,
            "Stop a running subagent; what it had still reaches its parent.",
        ),
        Method(
            "command.list",
            ConversationRef,
            CommandList,
            "A conversation's running background commands, for its agent map.",
        ),
        Method(
            "command.output",
            CommandRef,
            CommandOutput,
            "The end of a background command's output, running or among the last ended.",
        ),
        Method(
            "command.stop",
            CommandRef,
            CommandStopped,
            "Stop a running background command with everything it started, and hand back the end "
            "of its output.",
        ),
        Method(
            "workbench.list",
            Empty,
            WorkbenchList,
            "The workbench (step 16c): its executor instances, the executors the plugins found, "
            "and what the instances ask that still waits.",
        ),
        Method(
            "workbench.start",
            WorkbenchStartParams,
            InstanceRow,
            "Start an executor in a folder, the user's instance; one that cannot start stays on "
            "the workbench saying why.",
        ),
        Method(
            "workbench.prompt",
            WorkbenchPromptParams,
            WorkbenchPrompted,
            "A prompt from an instance's card, with the files attached: a new turn, or a steer "
            "when one runs.",
        ),
        Method(
            "workbench.auto",
            WorkbenchSwitchParams,
            InstanceRow,
            "The instance's own mode that asks no permission, on or off.",
        ),
        Method(
            "workbench.model",
            WorkbenchModelParams,
            InstanceRow,
            "The instance's model and effort from its next request on; an effort the model does "
            "not take is refused.",
        ),
        Method(
            "workbench.atlas_answers",
            WorkbenchSwitchParams,
            InstanceRow,
            "Whether Atlas answers what the instance asks: the guardian for permissions, a model "
            "for questions; what either is unsure of still reaches the user.",
        ),
        Method(
            "workbench.duplicate",
            WorkbenchFolderParams,
            InstanceRow,
            "The same executor again, the user's, in another folder.",
        ),
        Method(
            "workbench.folder",
            WorkbenchFolderParams,
            InstanceRow,
            "Move the user's idle instance to another folder, where its harness starts a new "
            "session.",
        ),
        Method(
            "workbench.account",
            WorkbenchAccountParams,
            InstanceRow,
            "Log the instance's harness into another account on the user's browser, then go on "
            "with the same session; never while one of its instances works.",
        ),
        Method(
            "workbench.use_account",
            WorkbenchUseAccountParams,
            InstanceRow,
            "Go on with the instance's conversation in the same folder on another account of its "
            "executor (step 16h): that account keeps a copy of its session, the original left as "
            "it was; never while it works.",
        ),
        Method(
            "workbench.route",
            InstanceRef,
            SessionRouteRow,
            "The instance's session's route: the destinations it goes on to at a plan's limit, "
            "accounts of its executor and of others, in order.",
        ),
        Method(
            "workbench.set_route",
            WorkbenchRouteParams,
            SessionRouteRow,
            "Set the instance's session's route, kept for its card. On an account of the same "
            "executor it goes on in a copy; on another executor in a new session, whose first "
            "prompt is a handoff packet Atlas puts together with no model call.",
        ),
        Method(
            "workbench.account_switch",
            WorkbenchUseAccountParams,
            AccountSwitchRow,
            "What changes if the instance goes on on another account: who it is logged in as, and "
            "the connectors it lacks. It takes seconds.",
        ),
        Method(
            "workbench.stop",
            InstanceRef,
            Empty,
            "Stop an instance for good and take its card down.",
        ),
        Method(
            "executor.detect",
            Empty,
            ExecutorList,
            "Look for the harnesses again, as at a start (installed, which version, which "
            'account): the executors tab asks when it shows, and on its "Look again".',
        ),
        Method(
            "executor.folders",
            ExecutorRef,
            SessionFolderList,
            "The folders an executor's sessions work in, read by its plugin without reading the "
            "sessions, for the executors tab, which reads a folder's sessions once it is opened.",
        ),
        Method(
            "executor.sessions",
            SessionListParams,
            SessionList,
            "Every session of an executor, or of one of its folders, wherever it began, read by "
            "its plugin with no effect on it, each with the instance working on it here (step "
            "16f, the executors tab).",
        ),
        Method(
            "executor.models",
            ExecutorRef,
            ExecutorModels,
            "The models an executor offers its account now and the efforts of each, its default "
            "first, read by its plugin with no prompt, for the session's box.",
        ),
        Method(
            "executor.connectors",
            ExecutorRef,
            ExecutorConnectors,
            "The MCP servers an executor has for its account, outside any project, with the state "
            "of each, read by its plugin with no prompt, for the side panel; it takes seconds.",
        ),
        Method(
            "executor.commands",
            ExecutorCommandsParams,
            SlashCommandList,
            "The slash commands an executor takes in a folder, described, for the session's box: "
            "what its instance there said, or with none, what its plugin reads with no prompt (it "
            "takes seconds). An instance's `commands` changing says to read them again.",
        ),
        Method(
            "executor.usage",
            ExecutorRef,
            ExecutorUsageRow,
            "How much of its plan's limits the account an executor is logged in as has used, "
            "read by its plugin with no prompt, for the side panel; also told as "
            "`executor.usage` while its instances work. It takes seconds.",
        ),
        Method(
            "executor.accounts",
            ExecutorRef,
            ExecutorAccounts,
            "An executor's accounts, in the order they take over in at a plan's limit, each with "
            "who it is logged in as (step 16h).",
        ),
        Method(
            "executor.add_account",
            ExecutorAddAccountParams,
            AccountRow,
            "Keep another account under a nickname, in the harness's folder for it, not logged "
            "in yet: `executor.account` on its row logs it in on the user's browser.",
        ),
        Method(
            "executor.remove_account",
            ExecutorAccountRef,
            Empty,
            "Forget another account; its folder stays, with its login and sessions, and its "
            "folders start on the main account again. Refused while an instance runs on it.",
        ),
        Method(
            "executor.delete_session",
            SessionDeleteParams,
            Empty,
            "Delete one session, in its account's folder only, after the user said yes; an idle "
            "instance on it closes first. Refused while it works.",
        ),
        Method(
            "executor.failover",
            ExecutorFailoverParams,
            ExecutorAccounts,
            "The order an executor's accounts take over in when one reaches its plan's limit, "
            "whether they do, and whether a session compacts before it goes on; told to every "
            "screen as `executor.accounts`.",
        ),
        Method(
            "executor.folder_account",
            ExecutorFolderAccountParams,
            Empty,
            "The account a folder's new sessions start on; empty, the main account.",
        ),
        Method(
            "executor.account",
            ExecutorAccountParams,
            ExecutorRow,
            "Log an executor's harness into another account on the user's browser, from its place "
            "in the sidebar; the user's instances of it go on with their sessions on it. Refused "
            "while one of its instances works.",
        ),
        Method(
            "executor.session",
            SessionReadParams,
            SessionRead,
            "A session's steps, as its plugin reads them where its harness keeps them; nothing is "
            "copied into Atlas (step 16f).",
        ),
        Method(
            "subagent.session",
            SubagentRef,
            SubagentSession,
            "The executor, folder and session of an outside executor's task of the conversation, "
            "running or ended, which its delegate line opens (step 16f).",
        ),
    )
}


# -- events ----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Ready:
    version: str
    epoch: str


@dataclass(frozen=True, slots=True)
class Said:
    text: str
    #: On `message.start` and `message.steered`: `notice` when Atlas started the turn (a
    #: background command ended), not the user; `terminal` when it is a terminal row, a command
    #: the user ran and its output, in Claude Code's bash-mode tags (step 16f); `skill` when
    #: the user invoked a skill with `/name`, its text loaded (the screen shows the `/name`).
    kind: Literal["message", "notice", "terminal", "skill"] = "message"


@dataclass(frozen=True, slots=True)
class UserSaid:
    """What the user said as a turn starts, or as it entered a running one: `Said`, and the files
    it came with (step 16g). Kept apart from `Said`, so the pieces of a reply carry no list."""

    text: str
    kind: Literal["message", "notice", "terminal", "skill"] = "message"
    attachments: list[AttachmentRow] = field(default_factory=list[AttachmentRow])


@dataclass(frozen=True, slots=True)
class Interim:
    #: The whole text of a model call that ended asking for tools; it already came in
    #: `message.delta`s.
    text: str


@dataclass(frozen=True, slots=True)
class ToolStart:
    tool_id: str
    name: str
    #: The arguments as the model wrote them, JSON text.
    args_text: str


@dataclass(frozen=True, slots=True)
class ToolProgressPayload:
    tool_id: str
    name: str
    event: Json


@dataclass(frozen=True, slots=True)
class ToolComplete:
    tool_id: str
    name: str
    #: What the model received.
    result_text: str
    #: `completed`; `failed` (it raised, or said it failed); `denied` (refused at approval);
    #: `cancelled` (the turn stopped first); `unanswered` (its approval was not answered in time).
    status: ToolStatus
    #: How long the call took; 0 for one that never ran.
    duration_s: float
    #: The file that holds the whole result, when it did not fit.
    spill_path: str | None = None
    #: Deferred tools the result loaded into the conversation.
    loaded: list[str] = field(default_factory=list[str])


@dataclass(frozen=True, slots=True)
class PreviewPayload:
    """What the agent asks the app's side panel to show or close (`desktop_preview`, step 16f)."""

    #: A web address, a local site, or a file's absolute path on the server's machine; empty
    #: on `preview.close`, every tab of the conversation.
    target: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    #: What happened: `compacted`.
    kind: str
    text: str


@dataclass(frozen=True, slots=True)
class MessageComplete:
    text: str
    #: `answered`, `halted` (the repeat guard), `failed`, `limited`, or `interrupted`.
    status: Literal["answered", "halted", "failed", "limited", "interrupted"]
    model_calls: int = 0
    #: What the turn cost in dollars, as text; `null` when a call's cost is unknown.
    cost_usd: str | None = None
    #: The kind of error that ended a failed turn.
    error: str | None = None
    #: Messages sent during a stopped turn that never entered it, for the input box.
    unsent: list[str] = field(default_factory=list[str])


@dataclass(frozen=True, slots=True)
class TitleChanged:
    #: The conversation's new title, a model's.
    title: str


@dataclass(frozen=True, slots=True)
class ReviewSummary:
    #: The line shown: "Memory updated · Skill 'x' updated".
    text: str
    actions: list[str]
    #: The memory proposals the review made, which wait for the user.
    proposals: list[str] = field(default_factory=list[str])
    #: The skill diff behind an action that changed a skill, by that action's text.
    diffs: dict[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class ProposalCount:
    #: How many memory proposals wait for the user.
    count: int


@dataclass(frozen=True, slots=True)
class TerminalOutput:
    id: str
    #: What the shell wrote, escapes and marks as they came.
    data: str


@dataclass(frozen=True, slots=True)
class TerminalExit:
    id: str
    #: Its shell's exit code; `null` when it could not be told.
    code: int | None = None


@dataclass(frozen=True, slots=True)
class RequestCancel:
    id: str
    method: str
    #: `resolved` (answered, here or by another client), `timeout`, or `withdrawn` (the turn
    #: stopped or stopped waiting).
    reason: Literal["resolved", "timeout", "withdrawn"]


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    payload: type[Any]
    doc: str
    #: The event belongs to a conversation: it carries its id and number.
    conversation: bool = True


EVENTS: Mapping[str, Event] = {
    event.name: event
    for event in (
        Event(
            "server.ready",
            Ready,
            "The first frame of a connection: the version, and the epoch of event numbers.",
            conversation=False,
        ),
        Event("message.start", UserSaid, "A turn began with what the user said."),
        Event("message.delta", Said, "A piece of the reply's text."),
        Event("reasoning.delta", Said, "A piece of the model's readable reasoning."),
        Event(
            "message.interim",
            Interim,
            "The text streamed since the last model call was interim: the call asked for tools. "
            "Said before its first tool.start.",
        ),
        Event("message.steered", UserSaid, "A message sent mid-turn entered the turn."),
        Event("tool.start", ToolStart, "A tool call began."),
        Event("tool.progress", ToolProgressPayload, "What a running tool reported."),
        Event("tool.complete", ToolComplete, "A tool call finished."),
        Event("status.update", StatusUpdate, "Something happened to the conversation."),
        Event("message.complete", MessageComplete, "The turn ended: its text and outcome."),
        Event("review.summary", ReviewSummary, "The background review changed something."),
        Event(
            "title.changed",
            TitleChanged,
            "A model titled the conversation; its row in the list takes the new title.",
        ),
        Event(
            "subagent.update",
            SubagentRow,
            "A subagent of the conversation started, ran a tool or ended.",
        ),
        Event(
            "command.update",
            CommandRow,
            "A background command of the conversation started or ended.",
        ),
        Event(
            "preview.open",
            PreviewPayload,
            "The agent asks to show something beside the conversation, in a tab of the app's side "
            "panel; never taking the focus from another conversation.",
        ),
        Event(
            "preview.close",
            PreviewPayload,
            "The agent closes a preview tab of the conversation, or all of them.",
        ),
        Event(
            "message.file",
            AttachmentRow,
            "A plugin's tool sent the user a file (SDK version 22, the voice plugin's spoken "
            "answer), kept in the home's attachments: shown in the turn, and sent in a channel's "
            "chat.",
        ),
        Event(
            "request.cancel",
            RequestCancel,
            "An open server request was withdrawn; take its card down.",
        ),
        Event(
            "terminal.output",
            TerminalOutput,
            "What one of this client's terminals wrote; only the client that opened it gets it.",
            conversation=False,
        ),
        Event(
            "terminal.exit",
            TerminalExit,
            "One of this client's terminals ended: its shell exited.",
            conversation=False,
        ),
        Event(
            "routines.changed",
            RoutinesChanged,
            "A routine was made, changed, paused, resumed, removed or ran; read routine.list.",
            conversation=False,
        ),
        Event(
            "routine.finished",
            RoutineFinished,
            "A routine's run ended; notify, unless its status is silent.",
            conversation=False,
        ),
        Event(
            "bots.changed",
            BotsChanged,
            "A bot was made, changed or deleted; read bot.list.",
            conversation=False,
        ),
        Event(
            "conversations.changed",
            ConversationsChanged,
            "A channel made a conversation, or a turn in one began or ended; read "
            "conversation.list.",
            conversation=False,
        ),
        Event(
            "plugins.changed",
            PluginsChanged,
            "A plugin was turned on, off or failed, or someone asked to pair; read plugin.list "
            "and pairing.list.",
            conversation=False,
        ),
        Event(
            "marketplace.changed",
            MarketplaceChanged,
            "An item was installed, updated, removed or retired, from any client or the "
            "terminal; read marketplace.list.",
            conversation=False,
        ),
        Event(
            "workbench.update",
            InstanceRow,
            "An executor instance on the workbench started, changed or ended; the workbench is "
            "the same in every conversation.",
            conversation=False,
        ),
        Event(
            "workbench.activity",
            InstanceActivityPayload,
            "An executor instance said, thought, called, got or was given something.",
            conversation=False,
        ),
        Event(
            "executor.usage",
            ExecutorUsageRow,
            "An executor's plan usage changed: read with executor.usage, or told by its harness "
            "as one of its instances worked.",
            conversation=False,
        ),
        Event(
            "executor.accounts",
            AccountsChanged,
            "An executor's accounts, or the account of one of its folders, changed; read "
            "executor.accounts and executor.folders.",
            conversation=False,
        ),
        Event(
            "proposals.changed",
            ProposalCount,
            "A memory proposal was made, approved or discarded; read them with memory.proposals.",
            conversation=False,
        ),
        Event(
            "update.ended",
            UpdateEnded,
            "The update of the server's host ended, in the minutes before this connection: the "
            "server that restarted tells each client as it connects.",
            conversation=False,
        ),
    )
}


# -- server requests -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuestionRow:
    #: The question's place (`q0`, `q1`…), which the answers are keyed by.
    id: str
    text: str
    #: The choices, the recommended first; empty for a free answer. The screen adds "Other".
    choices: list[str]
    multi_select: bool


@dataclass(frozen=True, slots=True)
class AskParams:
    conversation_id: str
    questions: list[QuestionRow]


@dataclass(frozen=True, slots=True)
class AskResult:
    """An answer per question id (a choice, the choices, typed text, or `null` for none); left out,
    the user dismissed the questions."""

    answers: dict[str, str | list[str] | None] | None = None


@dataclass(frozen=True, slots=True)
class ApprovalParams:
    conversation_id: str
    tool: str
    #: What would run: the command, or the call's arguments.
    target: str
    #: Why it was flagged.
    reason: str
    allow_conversation: bool
    allow_always: bool
    #: The guardian judged it dangerous; the user may only overrule it for this one call.
    guardian_denied: bool
    #: The tool call it holds (`tool.start`'s `call_id`), which the screen shows as waiting for the
    #: user rather than running; empty when it is asked outside a call.
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalResult:
    choice: Literal["once", "conversation", "always", "deny"]
    #: Why the user refused, passed on to the model.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutorApprovalParams:
    """What an executor instance asks to do, on its card (step 16c: every screen gets it)."""

    instance_id: str
    executor: str
    #: How the screen calls the executor.
    executor_title: str
    #: The conversation it works for, when Atlas delegated; empty for the user's.
    conversation_id: str
    tool: str
    #: The action in one line: the command, the file, or the tool's input.
    summary: str
    #: The executor's own sentence for the prompt, and what it adds.
    title: str
    description: str
    #: The folder its session works in: a chat names it for a session of the user's.
    folder: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorApprovalResult:
    allow: bool
    #: Why the user refused, passed on to the executor.
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutorChoiceRow:
    label: str
    description: str


@dataclass(frozen=True, slots=True)
class ExecutorQuestionRow:
    question: str
    header: str
    choices: list[ExecutorChoiceRow]
    multi_select: bool


@dataclass(frozen=True, slots=True)
class ExecutorQuestionParams:
    instance_id: str
    executor: str
    executor_title: str
    conversation_id: str
    questions: list[ExecutorQuestionRow]
    #: The folder its session works in: a chat names it for a session of the user's.
    folder: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorQuestionResult:
    """An answer per question, keyed by its text (a label, labels, or the user's own words); left
    out, the user dismissed the questions."""

    answers: dict[str, str | list[str]] | None = None


@dataclass(frozen=True, slots=True)
class SecretParams:
    """The agent asks the user for an API key by name (the secrets tool, step 16d). The card is
    masked, the value never comes back to the model, and the server writes it to the vault."""

    conversation_id: str
    #: UPPER_SNAKE_CASE, fits `${NAME}` in `mcp.json`.
    name: str
    #: One line the card shows under the name.
    reason: str
    #: The vault already holds a secret by this name; pasting replaces it.
    exists: bool
    #: When it was last stored (UTC); empty when the vault has none.
    updated_at: str = ""
    #: The bot the turn runs as, if any; the card tells the user the bots share the vault.
    bot: str = ""


@dataclass(frozen=True, slots=True)
class SecretResult:
    """What the app sends back. `value` is the key the user pasted; the server writes it to the
    vault and never sends it anywhere else. A dismiss sends `skipped=True` and no value."""

    value: str = ""
    skipped: bool = False


@dataclass(frozen=True, slots=True)
class ServerRequest:
    name: str
    params: type[Any]
    result: type[Any]
    doc: str


SERVER_REQUESTS: Mapping[str, ServerRequest] = {
    request.name: request
    for request in (
        ServerRequest(
            "ask_user",
            AskParams,
            AskResult,
            "The agent asks the user 1 to 5 questions (the ask_user tool).",
        ),
        ServerRequest(
            "approval",
            ApprovalParams,
            ApprovalResult,
            "A tool call waits for the user's approval.",
        ),
        ServerRequest(
            "secret",
            SecretParams,
            SecretResult,
            "The agent asks the user for an API key by name (the secrets tool). The card is "
            "masked; the value goes straight to the vault and is never sent back.",
        ),
        ServerRequest(
            "executor.approval",
            ExecutorApprovalParams,
            ExecutorApprovalResult,
            "An outside executor asks to use one of its tools; it waits, with no deadline, until "
            "the user answers or it is stopped.",
        ),
        ServerRequest(
            "executor.question",
            ExecutorQuestionParams,
            ExecutorQuestionResult,
            "An outside executor asks the user questions; it waits, with no deadline.",
        ),
    )
}


# -- reading and writing shapes --------------------------------------------------------------------


def read[T](shape: type[T], raw: object, where: str = "params") -> T:
    """`raw` as `shape`, every field checked; raises `ProtocolError` with what is wrong."""
    if not isinstance(raw, dict):
        raise ProtocolError(INVALID_PARAMS, f"{where} must be an object.")
    given: dict[str, Any] = dict(raw)  # pyright: ignore[reportUnknownArgumentType]
    hints = get_type_hints(shape)
    names = {f.name for f in dataclasses.fields(shape)}  # pyright: ignore[reportArgumentType]
    unknown = sorted(set(given) - names)
    if unknown:
        raise ProtocolError(INVALID_PARAMS, f"{where} has unknown keys: {', '.join(unknown)}.")
    values: dict[str, Any] = {}
    for item in dataclasses.fields(shape):  # pyright: ignore[reportArgumentType]
        place = f"{where}.{item.name}"
        if item.name not in given:
            required = (
                item.default is dataclasses.MISSING and item.default_factory is dataclasses.MISSING
            )
            if required:
                raise ProtocolError(INVALID_PARAMS, f"{place} is missing.")
            continue
        values[item.name] = _value(hints[item.name], given[item.name], place)
    return shape(**values)


def dump(value: object) -> Any:
    """A shape as JSON-ready values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: dump(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, list | tuple):
        items = cast("Sequence[object]", value)
        return [dump(item) for item in items]
    if isinstance(value, dict):
        entries = cast("Mapping[object, object]", value)
        return {str(key): dump(item) for key, item in entries.items()}
    return value


def _value(hint: Any, value: object, place: str) -> Any:
    if isinstance(hint, TypeAliasType):
        hint = hint.__value__
    if hint is object:  # Json: any value
        return value
    origin = get_origin(hint)
    if origin in (Union, types.UnionType):
        for option in get_args(hint):
            try:
                return _value(option, value, place)
            except ProtocolError:
                continue
        raise ProtocolError(INVALID_PARAMS, f"{place} has the wrong type.")
    if origin is Literal:
        if value in get_args(hint):
            return value
        allowed = ", ".join(str(choice) for choice in get_args(hint))
        raise ProtocolError(INVALID_PARAMS, f"{place} must be one of: {allowed}.")
    if origin is list:
        if not isinstance(value, list):
            raise ProtocolError(INVALID_PARAMS, f"{place} must be a list.")
        (inner,) = get_args(hint)
        items: list[Any] = value  # pyright: ignore[reportUnknownVariableType]
        return [_value(inner, item, f"{place}[{n}]") for n, item in enumerate(items)]
    if origin is dict:
        if not isinstance(value, dict):
            raise ProtocolError(INVALID_PARAMS, f"{place} must be an object.")
        _, inner = get_args(hint)
        entries: dict[Any, Any] = value  # pyright: ignore[reportUnknownVariableType]
        return {str(k): _value(inner, v, f"{place}.{k}") for k, v in entries.items()}
    if dataclasses.is_dataclass(hint) and isinstance(hint, type):
        return read(cast("type[Any]", hint), value, place)
    if hint is type(None):
        if value is None:
            return None
    elif hint is bool:
        if isinstance(value, bool):
            return value
    elif hint is int:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    elif isinstance(hint, type) and isinstance(value, hint):
        return value
    raise ProtocolError(INVALID_PARAMS, f"{place} has the wrong type.")
