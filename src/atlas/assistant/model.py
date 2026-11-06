"""What the assistant reports about a conversation besides the turn's own events, and the rules
that need no I/O. Pure.

**The events** a conversation's listener gets: the agent loop's (`atlas.agent.TurnEvent`), plus
when a turn starts, when the user stopped one, what the background review changed, how its
subagents and outside executors are doing, and what an executor just did.

**The review's summary**: one line per thing the review changed, joined with " · ", and nothing
when it changed nothing. Atlas reads what the review's own tools noted while it ran
(`atlas.memory.ReviewEnded`): until step 9b it compared the files before and after, and counted
what another conversation or the user changed meanwhile. A proposal the review made has its line
too: the summary is where it surfaces, or not at all. A skill's line carries its skill diff, from
the same notes, so a client shows what changed in a click: the review's calls are not shown, so
the line is all the user gets.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the type only: model.py imports no other subject at run time
    from atlas.models import Attachment

#: How many idle conversations stay open; past it, the one used longest ago is let go. A
#: conversation is opened again from the database when someone comes back to it.
KEPT_IDLE = 16

#: Atlas's default agent identity. It is also what a new soul holds: when it changes, the old
#: text goes on a list, and a soul still holding exactly it is upgraded (docs/ARCHITECTURE.md, The
#: soul).
IDENTITY = (
    "You are Atlas, a personal agent. Speak plainly and size each reply to what was asked: a "
    "one-line question gets one line back, and when work is done, report briefly what changed, "
    "what you verified and what remains — never a retelling of how you got there. Skip filler "
    '("Great question," "I\'d be happy to"), do not echo the request, do not sum up again what '
    "you already said, and do not narrate tool calls the user can already see. Prefer plain "
    "statements to adjectives, and say so simply when you are not sure. Agree with the user when "
    "they are right, not because they said it. Go deep only when it is earned — when the user "
    "asks for detail, when you are teaching, or when the stakes call for it — never by default."
)


#: Where the soul is kept, and leave to change it when the user asks. Without the leave, the real
#: proof of 15/10/2026 found gpt-6-luna refusing, three times of three, to edit its soul when
#: asked ("Não posso trocar essa regra"): an instruction of the system prompt outranks the user's.
#: The leave holds only when the user asks.
SOUL_NOTE = (
    "Your identity is kept in {path}, read when a conversation starts. When the user asks you to "
    "change who you are or how you speak, edit that file, keeping the rest of it, and tell them "
    "the change applies from the next conversation."
)


def identity(
    workspace: str, system: str, started: str, *, soul: str = "", soul_file: str | None = None
) -> str:
    """The identity, with where the agent works appended. The main agent's is its soul, whole,
    or Atlas's when it has none, and then `SOUL_NOTE`. A subagent's is Atlas's: a subagent has no
    soul."""
    where = (
        f"The user's working folder is {workspace}, on {system}; relative paths start there. "
        f"This conversation started on {started}."
    )
    if soul_file is not None:
        where = f"{where} {SOUL_NOTE.format(path=soul_file)}"
    return f"{soul.strip() or IDENTITY}\n\n{where}"


def bot_identity(mission: str, workspace: str, system: str) -> str:
    """A bot's identity: its mission and rules in the place of Atlas's own, or Atlas's when it
    has none. No start date: its conversation lasts forever, and a frozen start date becomes
    misinformation."""
    return (
        f"{mission.strip() or IDENTITY}\n\nThe user's working folder is {workspace}, on "
        f"{system}; relative paths start there."
    )


def routine_notice(name: str, output: str) -> str:
    """What a bot reads in its fixed conversation when one of its routines ran."""
    return (
        f'[Cronjob "{name}" output — sent by a scheduled job, not by the user. Read it, do '
        f"whatever in it calls for action, and sum it up for the chat.]\n\n{output}"
    )


class ConversationRole(StrEnum):
    """Who a conversation's turns run for. It decides the tools, who answers approvals, the loop
    guards' posture and what follows a turn."""

    #: The user's: questions and approvals reach the app, the guards only warn, the review follows.
    CONVERSATION = "conversation"
    #: A routine's run (step 12): nobody answers, the guards halt, no review, no routine made.
    ROUTINE = "routine"
    #: A subagent (step 12b): nobody answers, but its parent watches, so the guards only warn;
    #: no memory, no review, no delegation, no routine.
    SUBAGENT = "subagent"


@dataclass(frozen=True, slots=True)
class SubagentChanged:
    """A subagent of this conversation started, used a tool or ended: what its dock shows (its
    progress events, folded into one)."""

    subagent_id: str
    delegation_id: str
    goal: str
    model: str
    status: str
    started_at: str
    #: The tool it is running or ran last, and how many it ran.
    last_tool: str | None = None
    tools: int = 0
    ended_at: str | None = None
    #: The outside executor running it (`claude-code`), whose presence the screen shows apart
    #: from the dock (step 13); `None` for Atlas's own subagent.
    executor: str | None = None
    #: Who answers what the executor asks: `user`, or `atlas` (the guardian and the
    #: conversation's model).
    decides: str = "user"
    #: Requests of it waiting for an answer.
    waiting: int = 0
    #: Its stored conversation: what it said and did.
    conversation_id: str = ""
    #: The account the executor runs on (Claude Code's login email).
    account: str = ""
    #: The folder an outside executor works in and its session there, which the executors tab
    #: opens (step 16f); empty until it says.
    folder: str = ""
    session_id: str = ""


class Owner(StrEnum):
    """Who started an executor instance (step 16c)."""

    #: On its card: Atlas steers or stops it only when the user asks.
    USER = "user"
    #: Through `delegate`, for one of its conversations.
    ATLAS = "atlas"


class InstanceState(StrEnum):
    """Where an executor instance is."""

    STARTING = "starting"
    #: Waiting for its next prompt.
    IDLE = "idle"
    #: A turn runs.
    WORKING = "working"
    #: Stopped, for good.
    ENDED = "ended"
    #: Its harness stopped by itself, or could not start.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class InstanceChanged:
    """An executor instance on the workbench, as its card shows it: told whenever it changes,
    to every screen (the workbench is the same in every conversation)."""

    instance_id: str
    #: The executor's name (`claude-code`) and how the screen calls it.
    executor: str
    title: str
    owner: str
    folder: str
    #: The account it runs on, as the screen names it: the main account's login when its harness
    #: says, or an extra account's name.
    account: str
    state: str
    #: Its own mode that asks no permission, and whether Atlas answers what it asks.
    auto: bool
    atlas_answers: bool
    #: Requests of it waiting for an answer.
    waiting: int = 0
    last_tool: str | None = None
    tools: int = 0
    #: Why it failed, or what went wrong with the last thing asked of it.
    problem: str = ""
    #: The conversation it works for, when Atlas delegated; empty for the user's.
    parent_id: str = ""
    #: Its stored conversation: what it said and did, which opens like any other.
    transcript_id: str = ""
    #: Its subagent, when Atlas delegated: the delegation's record.
    subagent_id: str = ""
    session_id: str = ""
    #: What its harness can do over its channel.
    steer: bool = False
    can_auto: bool = False
    accounts: bool = False
    #: The slash commands its channel takes, names without the slash (step 16f).
    commands: tuple[str, ...] = ()
    #: It offers models and efforts, and switches them while it runs.
    can_model: bool = False
    #: The model its last turn ran as, as its harness says (`claude-opus-5-5`).
    model: str = ""
    #: The model and the effort chosen for it, as its harness names them; empty, its default.
    chosen_model: str = ""
    #: Its extra account's id (step 16h); empty, the main account.
    account_id: str = ""
    effort: str = ""
    #: Its harness's permission modes, and the one it runs in (SDK version 28); empty, none.
    mode: str = ""
    modes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InstanceActivity:
    """What an executor instance just did, for its session's conversation: `said`, `thought`,
    `called` (a tool and its input), `returned` (its result), `prompted` (a prompt it was given),
    `steered` (a prompt that entered the turn running), `answered` (Atlas answered one of its
    requests for the user), `compacted` (it compacted its session), `account` (its account
    reached its plan's limit and it went on on the account `text` names or, `failed`, why it
    could not go on anywhere, by a route or not), `route` (it went on, by its session's route,
    in a new session of the executor and account `text` names, or, `failed`, why that failed),
    `note` (what the screen says of such a switch: the model it went on with, a compaction that
    failed) or `resume` (it stopped at its plan's limit and goes on by itself at the UTC time
    `text` says or, `failed`, the limit said no reset and it waits for the user)."""

    instance_id: str
    kind: str
    text: str
    failed: bool = False
    #: The call a `called` and its `returned` share, and the tool called (step 16f).
    call_id: str = ""
    tool: str = ""
    #: The files the user attached to a `prompted` or a `steered` (step 16i).
    attachments: "tuple[Attachment, ...]" = ()


@dataclass(frozen=True, slots=True)
class SlashCommandView:
    """A command the box offers as `/` is typed: an executor's (its plugin's `SlashCommand`), or
    Atlas's own and its skills in a conversation of Atlas's or a bot's."""

    #: Without the slash.
    name: str
    description: str = ""
    #: What it takes after its name (`<file>`), when it says.
    hint: str = ""
    #: `command` (Atlas's own, or the harness's own) or `skill` (a skill, or a command a user,
    #: project or plugin defined): the box lists them apart, commands first.
    group: str = "skill"


#: Atlas's own slash commands in a conversation's box, which the screen runs itself (the
#: channels' `/new` and `/stop`); a bot's conversation is the bot's one, so it
#: has no `/new`.
ATLAS_COMMANDS = (
    SlashCommandView("new", "Start a new conversation", group="command"),
    SlashCommandView("stop", "Stop the turn running", group="command"),
)


@dataclass(frozen=True, slots=True)
class UsageWindowView:
    """One of a plan's limits, as the side panel shows it: its plugin's `UsageWindow`."""

    id: str
    title: str
    #: How much of it is used, 0 to 100.
    used: float
    #: When it starts again, UTC text; empty when the harness does not say.
    resets_at: str = ""
    #: How long it lasts, in minutes, which names it on screen; 0 when not said.
    minutes: int = 0
    #: The model it counts alone, or empty for every model.
    model: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorUsage:
    """How much of its plan's limits the account an executor is logged in as has used (asked for
    by the user on 18/10/2026): read when the side panel shows the executor, and told to every
    screen as its instances work. From an instance, only the windows its harness just named."""

    executor: str
    windows: tuple[UsageWindowView, ...] = ()
    #: The plan, when the harness says (`pro`, `max`, `team`).
    plan: str = ""
    #: Why the login has no plan limits (an API key), when it has none.
    unavailable: str = ""
    #: When it was last read or told, UTC text.
    read_at: str = ""
    #: Past its limits the plan bills rather than stops (Claude's extra usage); `None`, unknown.
    extra_usage: bool | None = None


def merged_usage(kept: ExecutorUsage | None, told: ExecutorUsage) -> ExecutorUsage:
    """What an instance told, over what was kept: a window it names replaces the one with the
    same id, in its place; the others stay as last read, and so do the plan and why none."""
    if kept is None:
        return told
    named = {window.id: window for window in told.windows}
    windows = [named.pop(window.id, window) for window in kept.windows]
    return ExecutorUsage(
        told.executor,
        (*windows, *named.values()),
        kept.plan or told.plan,
        "" if told.windows else kept.unavailable,
        told.read_at or kept.read_at,
        kept.extra_usage if told.extra_usage is None else told.extra_usage,
    )


type WorkbenchEvent = InstanceChanged | InstanceActivity | ExecutorUsage


@dataclass(frozen=True, slots=True)
class SessionView:
    """One session of an executor, as the executors tab lists it (step 16f): read by its plugin
    wherever it began, with what Atlas knows of it."""

    executor: str
    session_id: str
    folder: str
    title: str
    updated_at: str
    started_at: str
    #: Its instance now, when one runs on it in Atlas: `starting`, `idle`, `working`, `failed`;
    #: empty when none does.
    state: str = ""
    instance_id: str = ""
    #: What it asks waits for an answer.
    waiting: int = 0
    #: Atlas delegated it (`delegate`): the list marks it "by Atlas".
    by_atlas: bool = False
    #: Where it is open right now outside Atlas (`claude-vscode`, `cli`): continuing it here too
    #: would fork it, so the screen warns first (decided with the user on 15/10/2026).
    elsewhere: str = ""


@dataclass(frozen=True, slots=True)
class SessionFolderView:
    """A folder an executor's sessions work in, as the executors tab lists it before its sessions
    are read."""

    executor: str
    folder: str
    #: When its most recent session last changed, UTC text.
    updated_at: str
    #: The other account its new sessions start on (step 16h); empty, the main account.
    account_id: str = ""


@dataclass(frozen=True, slots=True)
class AccountView:
    """An account an executor runs on (step 16h), as the executors tab lists it: the main one, in
    the harness's own folder, or another, in the folder the harness keeps for it."""

    executor: str
    #: Empty for the main account.
    account_id: str
    #: The name of the executor on it, as the screens and the workbench call it
    #: (`claude-code@1a2b3c4d`; the executor's own for the main account).
    name: str
    #: The user's nickname for another account; empty for the main one.
    nickname: str
    #: The harness's folder for it.
    folder: str
    #: Who the harness is logged in as there, when it says.
    login: str = ""
    logged_in: bool = False


@dataclass(frozen=True, slots=True)
class AccountSwitch:
    """What changes if an instance goes on on another account (step 16h), for the screen to say
    before the user switches: who that account is logged in as, and the connectors the account it
    runs on has that the other lacks."""

    executor: str
    account_id: str
    login: str = ""
    missing: tuple[str, ...] = ()
    #: Both accounts' connectors were read; when not, nothing is known of what is missing.
    compared: bool = False
    #: About how many tokens the first message on the other account reads again, when the
    #: harness says.
    tokens: int | None = None


@dataclass(frozen=True, slots=True)
class RouteStop:
    """One destination of a session's route, as the screen lists it: the executor and its account
    (the main one as `""`), the name of its executor on that account, and how the user knows it."""

    executor: str
    account_id: str
    name: str
    title: str
    #: The account's nickname; empty for the main account.
    nickname: str = ""
    #: Its executor is installed here: one that is not is passed over.
    installed: bool = True
    #: The model and the effort the session goes on with there; `None`, the ones it ran on.
    model: str | None = None
    effort: str | None = None
    #: It reached its plan's limit and has not reset: passed over until it does.
    limited: bool = False


@dataclass(frozen=True, slots=True)
class RouteView:
    """A session's route (asked for by the user on 21/10/2026): the destinations it goes on to,
    in order, when the one it runs on reaches its plan's limit. With none, or `enabled` off, its
    executor's own accounts take over as they are ordered there."""

    instance_id: str
    stops: tuple[RouteStop, ...] = ()
    enabled: bool = True
    #: It compacts the copy on another account of the same executor before it goes on.
    compact: bool = True


@dataclass(frozen=True, slots=True)
class AccountList:
    """An executor's accounts, in the order they take over in when one reaches its plan's limit:
    the main one first until the user orders them."""

    executor: str
    accounts: tuple[AccountView, ...]
    #: Its plugin runs on other accounts, so one may be added.
    can_add: bool = False
    #: A session at its account's limit goes on on the next account (on by default).
    failover: bool = True
    #: It compacts there before it goes on (on by default).
    compact: bool = True


class Decides(StrEnum):
    """Who answers what an outside executor asks."""

    USER = "user"
    #: The guardian for permissions, the conversation's model for questions; what the guardian is
    #: unsure of still reaches the user (an option of Atlas's own, asked for by the user).
    ATLAS = "atlas"


class Sent(StrEnum):
    """What became of a message sent to a conversation."""

    #: A turn started with it.
    STARTED = "started"
    #: A turn was running: the message enters it after the round of tools in course, or starts the
    #: next turn.
    STEERED = "steered"


@dataclass(frozen=True, slots=True)
class TurnStarted:
    """A turn began with what the user said, and the files they attached."""

    text: str
    attachments: "tuple[Attachment, ...]" = ()


@dataclass(frozen=True, slots=True)
class TurnStopped:
    """The user stopped the turn. `unsent` are the messages sent meanwhile that never entered it,
    handed back to the input box."""

    unsent: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreviewAsked:
    """The agent asks the app to show something beside the conversation, or to close it
    (`desktop_preview`, step 16f): `action` is `open` or `close`."""

    action: str
    target: str
    label: str = ""


@dataclass(frozen=True, slots=True)
class FileSent:
    """A tool sent the user a file (`ToolHost.send_file`, SDK version 22), kept in the home's
    attachments: the app shows it in the conversation, and a channel sends it in the chat."""

    attachment: "Attachment"


@dataclass(frozen=True, slots=True)
class Titled:
    """The conversation has a new title, a model's (atlas.assistant.titles)."""

    title: str


@dataclass(frozen=True, slots=True)
class Reviewed:
    """The background review changed something; one line per change."""

    actions: tuple[str, ...]
    #: The memory proposals it made, which wait for the user.
    proposals: tuple[str, ...] = ()
    #: The skill diff behind each skill's line, by that line.
    diffs: Mapping[str, str] = field(default_factory=dict[str, str])

    @property
    def text(self) -> str:
        return " · ".join(self.actions)


def skill_action(name: str, *, created: bool) -> str:
    """The summary's line for a skill."""
    return f"Skill '{name}' {'created' if created else 'updated'}"


_MEMORY_LABELS = {"memory": "Memory updated", "user": "User profile updated"}
_PROPOSAL_LABELS = {"memory": "Memory change proposed", "user": "User profile change proposed"}


def review_actions(
    saved: Collection[str],
    proposed: Collection[str],
    created: Sequence[str],
    updated: Sequence[str],
) -> tuple[str, ...]:
    """What a review did: the stores (`memory`, `user`) it saved to, those it
    proposed changes to, then each skill it created or changed, by name."""
    actions = [label for target, label in _MEMORY_LABELS.items() if target in saved]
    actions += [label for target, label in _PROPOSAL_LABELS.items() if target in proposed]
    skills = {name: skill_action(name, created=False) for name in updated}
    skills |= {name: skill_action(name, created=True) for name in created}
    actions += [skills[name] for name in sorted(skills)]
    return tuple(actions)
