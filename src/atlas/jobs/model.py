"""Routines: their schedules, when they run next, how late a run is, and what a run said; and
subagents: their tasks, their summaries and the notice that brings them back. Pure.

- **Schedules**: `every 30m` (or a bare `30m`) repeats; `in 2h` runs once; `every monday 9am`,
  `weekdays at 9am` and a 5-field cron expression run on the wall clock; an ISO time runs once.
- **A missed run** fires late within a grace of half its period, between 2 minutes and 2 hours;
  past it the missed runs collapse into one catch-up run, so a daily routine is not skipped after
  2 minutes. The next run is counted from the run that fired, so a long outage costs
  one run, not one per slot.
- **Cron runs on the wall clock** of the routine's zone, across daylight saving: croniter is given
  the zone's naive wall time, and a repeated autumn hour takes the first instant after the last run.
"""

import dataclasses
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import Decimal
from enum import StrEnum
from typing import cast

import yaml
from croniter import croniter

from atlas.jobs.errors import DelegationError, ScheduleError

#: Minutes in a unit, by the unit's first letter.
_MINUTES = {"m": 1, "h": 60, "d": 1440}
#: A number, which may be left out, then a unit of minutes, hours or days: its letter alone, a
#: short form or the whole word, singular or plural.
_DURATION = re.compile(r"^(\d*)\s*(m(?:in(?:ute)?s?)?|h(?:(?:ou)?rs?)?|d(?:ays?)?)$", re.IGNORECASE)
#: Each day's names, in cron's numbering from Sunday as 0.
_DAY_NAMES = (
    "sunday sun",
    "monday mon",
    "tuesday tue tues",
    "wednesday wed weds",
    "thursday thu thur thurs",
    "friday fri",
    "saturday sat",
)
_WEEKDAYS = {name: str(number) for number, names in enumerate(_DAY_NAMES) for name in names.split()}
#: Words that name a set of days at once, as a cron day-of-week field.
_DAY_SETS = (("*", "day daily everyday"), ("1-5", "weekday weekdays"), ("0,6", "weekend weekends"))
_DAYSPECS = {word: field for field, words in _DAY_SETS for word in words.split()}
_CRON_FIELD = re.compile(r"^[A-Za-z\d*\-,/]+$")
#: How late a run may be and still catch up rather than be counted as one catch-up run.
MIN_GRACE_SECONDS = 120
MAX_GRACE_SECONDS = 7200
#: A one-shot created a moment after its time still runs.
ONESHOT_GRACE_SECONDS = 120
#: A run within this of its time is on time: a busy minute ticker slips.
LATE_TOLERANCE_SECONDS = 300
#: What a reply says to stay quiet: the whole reply, or its first or last line.
SILENT_MARKS = ("[SILENT]", "SILENT", "NO_REPLY")


#: How long a probe may run, its failures in a row before the user is told, and the most of
#: its output an event carries.
PROBE_SECONDS = 120
PROBE_FAILURES = 3
MAX_EVENT_CHARS = 20_000


class ScheduleKind(StrEnum):
    ONCE = "once"
    INTERVAL = "interval"
    CRON = "cron"


@dataclass(frozen=True, slots=True)
class Schedule:
    """When a routine runs: `minutes` for an interval, `expr` for cron, `run_at` (UTC text) once."""

    kind: ScheduleKind
    display: str
    minutes: int | None = None
    expr: str | None = None
    run_at: datetime | None = None


class Lateness(StrEnum):
    """How a run fired against its time."""

    ON_TIME = "on_time"
    #: Within the grace: the run fires late, as scheduled.
    LATE = "late"
    #: Past the grace: the runs missed collapse into this one.
    CATCH_UP = "catch_up"
    #: The user or the agent asked for it now.
    MANUAL = "manual"


class RunStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    #: The reply asked for silence: kept, nobody notified.
    SILENT = "silent"
    ERROR = "error"
    #: Atlas stopped while it ran; whether it finished is unknown, never assumed.
    UNKNOWN = "unknown"
    #: It stopped at a question or an approval nobody was there to give (step 25): what it asked
    #: waits for the user, its session kept to go on by hand.
    NEEDED = "needed"


def parse_duration(text: str) -> int:
    """`30m` → 30, `2h` → 120, `1d` → 1440, a bare `hour` → 60: minutes."""
    found = _DURATION.match(text.strip())
    if found is None:
        raise ScheduleError(
            f"{text!r} is not a duration. Use 30m, 2h or 1d, or a bare unit such as hour."
        )
    return (int(found.group(1)) if found.group(1) else 1) * _MINUTES[found.group(2)[0].lower()]


def parse_schedule(text: str, now: datetime) -> Schedule:
    """Read a schedule; `now` is aware, in the routine's zone, for `in 2h` and
    for an ISO time that names no zone."""
    original = text.strip()
    lower = original.lower()
    every = lower.startswith("every ")
    rest = original[6:].strip() if every else lower
    natural = _natural_to_cron(rest)
    if natural is not None:
        return _cron(natural, original)
    if every:
        minutes = parse_duration(rest)
        return Schedule(ScheduleKind.INTERVAL, f"every {minutes}m", minutes=minutes)
    fields = original.split()
    if len(fields) == 5 and all(_CRON_FIELD.match(field) for field in fields):
        return _cron(original, original)
    if "T" in original or re.match(r"^\d{4}-\d{2}-\d{2}", original):
        try:
            moment = datetime.fromisoformat(original)
        except ValueError as error:
            raise ScheduleError(f"{original!r} is not a time: {error}.") from error
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=now.tzinfo)
        return Schedule(
            ScheduleKind.ONCE, f"once at {moment:%Y-%m-%d %H:%M}", run_at=moment.astimezone(UTC)
        )
    if lower.startswith("in "):
        minutes = parse_duration(original[3:])
        moment = now.astimezone(UTC) + timedelta(minutes=minutes)
        return Schedule(ScheduleKind.ONCE, f"once in {original[3:].strip()}", run_at=moment)
    try:
        minutes = parse_duration(original)
    except ScheduleError:
        pass
    else:
        return Schedule(ScheduleKind.INTERVAL, f"every {minutes}m", minutes=minutes)
    raise ScheduleError(
        f"{original!r} is not a schedule. Use: every 30m (repeats), in 2h (once), every monday "
        "9am or weekdays at 9am, a cron expression such as 0 9 * * *, or a time such as "
        "2026-10-20T14:00."
    )


def next_run(
    schedule: Schedule, zone: tzinfo, after: datetime, *, ran: bool = False
) -> datetime | None:
    """The next run strictly after `after` (aware), in UTC; `None` when there is none. A one-shot
    that `ran` never runs again; one created just past its time still does."""
    if schedule.kind is ScheduleKind.ONCE:
        if ran or schedule.run_at is None:
            return None
        late = (after - schedule.run_at).total_seconds()
        return schedule.run_at if late <= ONESHOT_GRACE_SECONDS else None
    if schedule.kind is ScheduleKind.INTERVAL:
        return after.astimezone(UTC) + timedelta(minutes=schedule.minutes or 0)
    wall = after.astimezone(zone).replace(tzinfo=None)
    found = croniter(schedule.expr or "", wall)
    base = after.timestamp()
    candidate = found.get_next(datetime)
    for _ in range(2):
        for fold in (0, 1):
            moment = candidate.replace(tzinfo=zone, fold=fold)
            if moment.timestamp() > base:
                return moment.astimezone(UTC)
        candidate = found.get_next(datetime)
    return candidate.replace(tzinfo=zone).astimezone(UTC)


def grace_seconds(schedule: Schedule, zone: tzinfo, now: datetime) -> int:
    """Half the period, within 2 minutes and 2 hours."""
    period = cadence_seconds(schedule, zone, now)
    if not period:
        return MIN_GRACE_SECONDS
    return max(MIN_GRACE_SECONDS, min(int(period) // 2, MAX_GRACE_SECONDS))


def cadence_seconds(schedule: Schedule, zone: tzinfo, now: datetime) -> float | None:
    """About how long between two runs; `None` for a one-shot."""
    if schedule.kind is ScheduleKind.INTERVAL:
        return float((schedule.minutes or 0) * 60) or None
    if schedule.kind is ScheduleKind.CRON:
        first = next_run(schedule, zone, now)
        second = None if first is None else next_run(schedule, zone, first)
        if first is not None and second is not None:
            return (second - first).total_seconds() or None
    return None


def lateness(due: datetime, now: datetime, grace: int) -> Lateness:
    """How a run due at `due` fires at `now`."""
    late = (now - due).total_seconds()
    if late > grace:
        return Lateness.CATCH_UP
    if late > LATE_TOLERANCE_SECONDS:
        return Lateness.LATE
    return Lateness.ON_TIME


def is_silent(reply: str) -> bool:
    """Whether a run's reply asks to notify nobody: the mark as the whole reply, or alone on its
    first or last line."""
    text = reply.strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return text in SILENT_MARKS or lines[0] in SILENT_MARKS or lines[-1] in SILENT_MARKS


class Creator(StrEnum):
    """Who made a routine: the user (the terminal, the app) or the agent (`cronjob_manage`)."""

    USER = "user"
    AGENT = "agent"


@dataclass(frozen=True, slots=True)
class RoutineExecutor:
    """Where a prompt routine runs when an outside executor runs it (step 25): a session of the
    executor in a folder, each run a new one."""

    #: The executor's name, as the workbench lists it (`claude-code`).
    name: str
    #: The folder it works in, absolute.
    folder: str
    #: It runs in the executor's automatic mode, chosen at creation; otherwise in its default,
    #: where what it asks stops the run.
    auto: bool = False
    #: Each run works in a new workspace of the folder (step 21), archived when it changed nothing:
    #: for a routine that changes code. Without, it works in the folder itself: one that only
    #: reads.
    workspace: bool = False
    #: A run that changed something opens a draft pull request of its workspace (the GitHub
    #: plugin's); off unless the user turned it on.
    draft: bool = False


@dataclass(frozen=True, slots=True)
class Routine:
    """A scheduled prompt, or a scheduled command when `command` is set.
    Times are aware, in UTC."""

    id: str
    name: str
    schedule: Schedule
    prompt: str | None
    command: str | None
    enabled: bool
    created_at: datetime
    created_by: Creator
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    last_status: "RunStatus | None" = None
    #: The model the user pinned on it (provider, model, effort); `None` follows the settings.
    model_provider: str | None = None
    model_name: str | None = None
    model_effort: str | None = None
    #: Asked to run now by the user or the agent, ahead of its schedule.
    run_now: bool = False
    #: The bot it runs as, whose fixed conversation reads its result (step 13b); `None`, the
    #: main agent.
    bot: str | None = None
    #: Its probe (step 23): a command run with no model on its schedule, whose output wakes its
    #: prompt only when it is something new; `None`, the schedule fires the prompt itself.
    probe: str | None = None
    #: The executor its prompt runs in, in a folder (step 25); `None`, Atlas's model runs it.
    executor: RoutineExecutor | None = None


@dataclass(frozen=True, slots=True)
class Scoreboard:
    """This week's runs of every routine, at the top of the Routines screen (step 25)."""

    #: Since when: this week's Monday at midnight, the user's time.
    since: datetime
    #: Ended with nothing asked: answered, or silent.
    alone: int
    #: Stopped at a question or an approval nobody gave.
    needed: int
    failed: int


@dataclass(frozen=True, slots=True)
class ProbeState:
    """What a routine's probe heard last (step 23)."""

    routine_id: str
    #: The digest of its last output that woke the routine, so the same output twice wakes once.
    digest: str = ""
    #: Its failures in a row, and whether the user was told of them.
    failures: int = 0
    warned: bool = False


@dataclass(frozen=True, slots=True)
class Heard:
    """What a probe's one run means."""

    state: ProbeState
    #: The text that wakes the routine; empty, it sleeps on.
    event: str = ""
    #: Tell the user it failed too many times in a row: once per run of failures.
    warn: bool = False


def heard(state: ProbeState, ok: bool, output: str) -> Heard:
    """A probe's run read: a failure counts and warns once at `PROBE_FAILURES` in a row; a success
    that printed nothing, or what woke the routine last time, wakes nothing; anything new wakes it
    with that text, cut to its last `MAX_EVENT_CHARS`."""
    if not ok:
        failures = state.failures + 1
        warn = failures >= PROBE_FAILURES and not state.warned
        return Heard(
            dataclasses.replace(state, failures=failures, warned=state.warned or warn), warn=warn
        )
    calm = dataclasses.replace(state, failures=0, warned=False)
    text = output.strip()
    if not text or text == "(no output)":
        return Heard(calm)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if digest == state.digest:
        return Heard(calm)
    return Heard(dataclasses.replace(calm, digest=digest), event=text[-MAX_EVENT_CHARS:])


@dataclass(frozen=True, slots=True)
class Run:
    """One firing of a routine."""

    number: int
    routine_id: str
    started_at: datetime
    lateness: Lateness
    status: RunStatus = RunStatus.RUNNING
    ended_at: datetime | None = None
    #: The conversation the run is, for a prompt routine.
    conversation_id: str | None = None
    #: The reply, or the command's output.
    output: str = ""
    error: str | None = None
    #: What its probe heard, which woke it (step 23); empty for a run its schedule fired.
    event: str = ""


# -- subagents ------------------------------------------------------------------------------------

#: Tasks in one call, and delegations in the background at once in one process: one cap for both.
MAX_TASKS = 10
MAX_BACKGROUND = 10
#: Model calls a subagent may make.
MAX_CHILD_CALLS = 250
#: The longest summary returned whole, and the least a summary keeps when the parent is nearly full.
MAX_SUMMARY_CHARS = 24_000
MIN_SUMMARY_CHARS = 2_000
#: The share of the parent's remaining context the batch's summaries may take.
SUMMARY_HEADROOM = 0.5
#: Seconds without activity before a child counts as frozen: idle, or inside one tool.
STALE_IDLE_SECONDS = 450.0
STALE_IN_TOOL_SECONDS = 1_200.0
#: How long a batch left by an earlier process is still delivered.
RECOVERY_HOURS = 48
#: A batch's goals shorter than this are usually unexpanded templates.
MIN_BATCH_GOAL_CHARS = 10
#: A goal that is only a stand-in: `todo`, `task 3`.
_PLACEHOLDER_GOAL = re.compile(r"^(?:todo|task\s*\d+)$", re.IGNORECASE)
#: Two or more words joined by a space, `_` or `-`, inside `<...>` or `{...}`: a template slot
#: nobody filled (`<file name>`, `{user_id}`).
_SLOT_WORDS = r"[A-Za-z][A-Za-z0-9]*(?:[ _-][A-Za-z0-9]+)+"
_TEMPLATE_MARKER = re.compile(rf"<{_SLOT_WORDS}>|\{{{_SLOT_WORDS}\}}")

#: The subagent prompt, with no workspace block: a child works in its parent's folder, which the
#: identity already names.
CHILD_PROMPT_OPENING = "You are a focused subagent working on a specific delegated task."
CHILD_COMPLETION = (
    "Carry the task out with the tools you have. Once it is done, end with a short, clear summary "
    "that covers:\n- What you did\n- What you found or achieved\n- The files you created or "
    "changed\n- The problems you ran into\n\nKeep that summary lean: outcomes first, bullet points "
    "rather than paragraphs, and no retelling of every step you took. Your answer goes back to "
    "the parent agent as its summary, and a long one takes room the parent's context window "
    "needs."
)
#: The mark around a steer.
STEER_OPEN = (
    "[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered once at this position; "
    "not tool output and not a new delivery when replayed from conversation history]"
)
STEER_CLOSE = "[/OUT-OF-BAND USER MESSAGE]"

_TRUNCATED_NOTE = (
    "[TRUNCATED — subagent hit its iteration cap, so the summary that follows may leave things "
    "out. Check it before you build on it, or dispatch again the part left undone.]"
)
UNKNOWN_ERROR = "Delegation owner exited before recording a terminal result; outcome unknown."


class SubagentStatus(StrEnum):
    """Where a subagent is."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    #: Frozen past the watchdog's limit, and stopped.
    STALLED = "stalled"
    #: Its process ended before it did: never `failed`, since it may have worked.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Subagent:
    """One child of a delegation. Times are aware, in UTC."""

    id: str
    delegation_id: str
    #: The conversation that delegated, and the child's own.
    parent_id: str
    conversation_id: str
    #: Its place in its call's tasks, from 0, and how many the call had.
    index: int
    count: int
    goal: str
    context: str
    model: str
    started_at: datetime
    status: SubagentStatus = SubagentStatus.RUNNING
    ended_at: datetime | None = None
    summary: str = ""
    error: str | None = None
    model_calls: int = 0
    #: What its calls cost, `None` when a price was unknown.
    cost_usd: Decimal | None = Decimal(0)
    #: It hit its model-call cap: the summary may be incomplete.
    truncated: bool = False
    #: Its batch's notice reached the parent.
    delivered: bool = False
    #: The outside executor that ran it (`claude-code`); `None` for Atlas's own subagent.
    executor: str | None = None
    #: The folder the executor worked in, and its session there, to open it again.
    folder: str = ""
    session_id: str | None = None
    #: The executor's account it ran on (Claude Code's login email).
    account: str = ""

    @property
    def done(self) -> bool:
        return self.status is not SubagentStatus.RUNNING


@dataclass(frozen=True, slots=True)
class Task:
    """One task the model gave."""

    goal: str
    context: str = ""
    #: The folder an outside executor works in (its project); a subagent works in Atlas's.
    folder: str = ""
    #: The predefined subagent it runs as (a plugin's agent, `video:editor`, step 16k); empty,
    #: the plain subagent.
    agent: str = ""
    #: For an outside executor: it works in a new workspace of `folder`'s project (step 21), a
    #: worktree on a branch of its own, and not in `folder` itself.
    workspace: bool = False


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """A predefined subagent: Claude Code's `agents/<name>.md` (step 16k), a helper for one kind
    of task, with no memory and no conversation of its own; `delegate` starts it by name."""

    #: As `delegate` names it: its plugin's name and its own (`video:editor`).
    name: str
    #: When to use it, for the model choosing.
    description: str
    #: Its instructions, which come before the task's context.
    prompt: str
    #: The tools it may use, as its file names them (Claude Code's names or Atlas's); `None`,
    #: every tool a subagent has.
    tools: tuple[str, ...] | None = None
    #: Its model as `provider/model`; `None`, its parent's.
    model: str | None = None


def parse_agent(text: str, plugin: str, stem: str) -> AgentDefinition:
    """A plugin's agent from its file, read as Claude Code reads it: YAML frontmatter with `name`
    (by default the file's), `description`, `tools` (a list, or names split by commas) and
    `model`, then its instructions. Claude Code's model aliases (`sonnet`, `inherit`) name no
    provider of Atlas's, so only a `provider/model` is taken; any other runs on the parent's.
    Raises `DelegationError` naming what is wrong."""
    fields: dict[str, object] = {}
    body = text.lstrip("\ufeff")
    lines = body.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
        if end is None:
            raise DelegationError(f"The agent {stem} has a frontmatter that never closes.")
        try:
            loaded: object = yaml.safe_load("\n".join(lines[1:end])) or {}
        except yaml.YAMLError as error:
            raise DelegationError(f"The agent {stem}'s frontmatter is not YAML: {error}") from error
        if not isinstance(loaded, dict):
            raise DelegationError(f"The agent {stem}'s frontmatter is not a table.")
        fields = cast("dict[str, object]", loaded)
        body = "\n".join(lines[end + 1 :])
    name = str(fields.get("name") or stem).strip()
    if not _AGENT.fullmatch(name):
        raise DelegationError(f"The agent {stem} is named {name!r}; use letters, digits and -.")
    description = " ".join(str(fields.get("description") or "").split())
    if not description:
        raise DelegationError(f"The agent {name} has no description, which says when to use it.")
    if not body.strip():
        raise DelegationError(f"The agent {name} has no instructions.")
    raw_tools = fields.get("tools")
    tools: tuple[str, ...] | None = None
    if isinstance(raw_tools, str):
        tools = tuple(part.strip() for part in raw_tools.split(",") if part.strip())
    elif isinstance(raw_tools, list):
        tools = tuple(str(part).strip() for part in cast("list[object]", raw_tools))
    model = str(fields.get("model") or "").strip()
    return AgentDefinition(
        f"{plugin}:{name}",
        description,
        body.strip(),
        tools,
        model if "/" in model else None,
    )


#: An agent's name, as Claude Code takes it.
_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


#: A task's `workspace` that asks for a new one (step 21).
NEW_WORKSPACE = "new"


def read_tasks(tasks: object) -> tuple[Task, ...]:
    """The tasks of a spawn, checked. Raises `DelegationError` with a message for the model."""
    if isinstance(tasks, str):
        raw = tasks.strip()
        try:
            tasks = json.loads(raw) if raw else []
        except ValueError as error:
            raise DelegationError(
                "tasks must be a JSON array of task objects, and this string does not parse as "
                f"JSON ({error})."
            ) from error
    if not isinstance(tasks, list) or not tasks:
        raise DelegationError(
            "No tasks provided. Give tasks=[{goal: '...', context: '...'}, ...], an entry for "
            "each subagent; one task is an array of one entry."
        )
    entries = cast("list[object]", tasks)
    if len(entries) > MAX_TASKS:
        raise DelegationError(
            f"Too many tasks: {len(entries)} given, and no more than {MAX_TASKS} can run at "
            "once. Send fewer, or spread them over several delegate calls."
        )
    read: list[Task] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise DelegationError(f"Task {i} must be an object, got {type(entry).__name__}.")
        fields = cast("dict[str, object]", entry)
        goal, context = fields.get("goal"), fields.get("context")
        if not isinstance(goal, str) or not goal.strip():
            raise DelegationError(f"Task {i} is missing a 'goal'.")
        folder, agent = fields.get("folder"), fields.get("agent")
        workspace = fields.get("workspace")
        if workspace not in (None, "", NEW_WORKSPACE):
            raise DelegationError(
                f"Task {i}: 'workspace' is '{NEW_WORKSPACE}' (a new workspace) or left out (the "
                "folder itself)."
            )
        read.append(
            Task(
                goal.strip(),
                context.strip() if isinstance(context, str) else "",
                folder.strip() if isinstance(folder, str) else "",
                agent.strip() if isinstance(agent, str) else "",
                workspace == NEW_WORKSPACE,
            )
        )
    for i, task in enumerate(read if len(read) >= 2 else ()):
        if _PLACEHOLDER_GOAL.match(" ".join(task.goal.lower().split())):
            raise DelegationError(
                f"Task {i} has a placeholder goal ({task.goal!r}). Write in its place what this "
                "subagent must achieve, precisely and with everything it needs to know."
            )
        marker = _TEMPLATE_MARKER.search(task.goal)
        if marker:
            raise DelegationError(
                f"Task {i}'s goal still holds a template marker ({marker.group(0)!r}). Put the "
                "real value there before calling delegate: a subagent has no way to fill a "
                "placeholder in."
            )
        if len(task.goal) < MIN_BATCH_GOAL_CHARS:
            raise DelegationError(
                f"Task {i} goal is too short ({task.goal!r}). A goal takes "
                f"{MIN_BATCH_GOAL_CHARS} characters at least, precise and complete in itself, "
                "for the subagent to know exactly what to do."
            )
    return tuple(read)


def child_prompt(context: str) -> str:
    """What a child is told after its identity: the subagent prompt."""
    parts = [CHILD_PROMPT_OPENING]
    if context.strip():
        parts.append(f"\nCONTEXT:\n{context}")
    parts.append("\n" + CHILD_COMPLETION)
    return "\n".join(parts)


def steer_message(text: str) -> str:
    """A steer as the child gets it: an out-of-band user message."""
    return f"{STEER_OPEN}\n{text}\n{STEER_CLOSE}"


def is_steer(text: str) -> bool:
    return text.startswith(STEER_OPEN)


def missed_steer(summary: str, steers: Sequence[str]) -> str:
    """A summary that says which steers came too late to land."""
    if not steers:
        return summary
    missed = " / ".join(steers)
    return (
        f"{summary}\n\n[steer did not land — the subagent finished before it could be delivered: "
        f"{missed}]"
    )


def summary_cap(window: int | None, last_input: int | None, summaries: int) -> int:
    """The longest summary the parent gets whole. Half the parent's remaining context, split over
    the batch at four characters a token, never under 2,000; never over 24,000. The remaining
    context is counted from the parent's last call, never a sum over its calls (a sum would push
    every summary down to the floor); unknown, only the ceiling holds."""
    if window is None or last_input is None or last_input <= 0:
        return MAX_SUMMARY_CHARS
    headroom = window - last_input
    if headroom <= 0:
        return MIN_SUMMARY_CHARS
    budget = max(MIN_SUMMARY_CHARS, int(headroom * SUMMARY_HEADROOM) // max(1, summaries) * 4)
    return min(MAX_SUMMARY_CHARS, budget)


def trimmed_summary(summary: str, cap: int, saved: str | None) -> str:
    """An over-cap summary as the parent gets it: about
    three quarters of the head and a quarter of the tail, snapped to lines, and a footer saying
    where the whole text is (`saved`) and the `read_file` offset of the omitted middle."""
    front = int(cap * 0.75)
    head = _opening_lines(summary, front)
    tail = _closing_lines(summary, cap - front)
    rule = "─" * 8
    footer = [
        "",
        f"{rule} [SUMMARY TRUNCATED] {rule}",
        f"Shown: {len(head):,} chars (head) + {len(tail):,} chars (tail) of {len(summary):,} in "
        "all, cut so that the parent's context window keeps its room.",
    ]
    if saved is None:
        footer.append(
            "The whole output could not be written to disk; nothing was kept beyond the head and "
            "tail above."
        )
    else:
        first_omitted = head.count("\n") + 2
        footer += [
            f"Full subagent output saved to: {saved}",
            f'The middle left out: read_file path="{saved}" offset={first_omitted} limit=200  '
            "(the file holds the whole summary; move offset up or down to page through it).",
        ]
    footer.append("─" * 37)
    return f"{head}\n\n[... middle omitted — see footer ...]\n\n{tail}" + "\n".join(footer)


def _opening_lines(text: str, budget: int) -> str:
    """The first `budget` characters, ended at the last line break when one falls in their
    second half."""
    head = text[:budget]
    end = head.rfind("\n")
    return head[:end] if end > budget * 0.5 else head


def _closing_lines(text: str, budget: int) -> str:
    """The last `budget` characters (the whole text when `budget` is 0), begun after the first
    line break when one falls in their first half."""
    tail = text[-budget:]
    start = tail.find("\n")
    return tail[start + 1 :] if 0 <= start < budget * 0.5 else tail


def _age(seconds: float) -> str:
    """An age: '45s', '18m', '2h3m'."""
    total = int(max(0, seconds))
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m"
    hours, minutes = divmod(total // 60, 60)
    return f"{hours}h{minutes}m" if minutes else f"{hours}h"


def batch_notice(children: Sequence[Subagent], now: datetime, zone: tzinfo) -> str:
    """The notice that brings a delegation's results back: one block for the whole call, each task
    with its status and its summary, or its partial output, or why there is none."""
    first = min(children, key=lambda child: child.index)
    count = first.count
    dispatched = first.started_at
    ended = max((child.ended_at or now for child in children), default=now)
    lines = [
        f"[ASYNC DELEGATION BATCH COMPLETE — {first.delegation_id}]",
        f"The {len(children)} subagent(s) you sent off earlier in the background are done, and "
        "all their results follow. Things may have moved on since you sent them: act on these "
        "results, or send the work out again if the situation changed.",
        "",
        f"Dispatched: {dispatched.astimezone(zone):%Y-%m-%d %H:%M:%S} "
        f"({_age((now - dispatched).total_seconds())} ago)",
        f"Role: leaf   Model: {first.model}   Total duration: "
        f"{int((ended - dispatched).total_seconds())}s",
    ]
    if any(child.status is SubagentStatus.UNKNOWN for child in children):
        lines.append(
            "Atlas restarted while this batch ran: the outcomes it had not recorded remain "
            "unknown; inspect the evidence before retrying side effects."
        )
    for child in sorted(children, key=lambda child: child.index):
        completed = child.status is SubagentStatus.COMPLETED
        icon = "⚠" if child.truncated else ("✓" if completed else "✗")
        seconds = int(((child.ended_at or now) - child.started_at).total_seconds())
        header = (
            f"--- {icon} TASK {child.index + 1}/{count}: {child.goal}  (status={child.status}"
            + (f", api_calls={child.model_calls}" if child.model_calls else "")
            + f", {seconds}s"
            + (
                ", TRUNCATED: hit max_iterations — work may be incomplete"
                if child.truncated
                else ""
            )
            + ") ---"
        )
        lines += ["", header]
        if completed and child.summary:
            if child.truncated:
                lines.append(_TRUNCATED_NOTE)
            lines.append(child.summary)
        elif child.summary:
            if child.error:
                lines.append(f"({child.status}: {child.error})")
            lines += ["Partial output:", child.summary]
        else:
            reason = f": {child.error}" if child.error else ""
            lines.append(f"(no summary — status={child.status}{reason})")
    return "\n".join(lines)


def dispatched(children: Sequence[Subagent]) -> dict[str, object]:
    """What a spawn returns at once."""
    count = len(children)
    if count == 1:
        note = (
            "The subagent works in the background. When it finishes, its whole result comes into "
            "the conversation as a new message, and only once you END YOUR TURN: first do "
            "whatever does not need it, then stop with one line saying where things stand. Do "
            "not poll for it."
        )
    else:
        note = (
            f"{count} subagents work side by side in the background. When the last of them "
            "finishes, their results come into the conversation together, as one new message, "
            "and only once you END YOUR TURN: first do whatever does not need them, then stop "
            "with one line saying where things stand. Do not poll for them."
        )
    return {
        "status": "dispatched",
        "mode": "background",
        "count": count,
        "delegation_id": children[0].delegation_id if children else "",
        "goals": [child.goal for child in children],
        "note": note,
        "subagent_ids": [child.id for child in children],
        "control_hint": (
            "This same tool controls a child while it runs: delegate(action='list') shows the "
            "live children, action='steer' with subagent_id + message turns one in a new "
            "direction, and action='stop' with subagent_id ends one before it is done."
        ),
    }


#: Why a spawn waited for its children instead of returning at once.
WAITED_NOTES = {
    "unattended": (
        "This run cannot delegate in the background: once the turn ends, no one is there to take "
        "a result (a routine). So the subagent(s) ran SYNCHRONOUSLY, and the result is above."
    ),
    "at_capacity": (
        "The background already held as many delegations as it can take "
        f"({MAX_BACKGROUND}), so the subagent(s) ran SYNCHRONOUSLY, and the result is above."
    ),
}


def listed(children: Sequence[Subagent], now: datetime) -> dict[str, object]:
    """What `action='list'` returns."""
    live = [child for child in children if not child.done]
    result: dict[str, object] = {
        "action": "list",
        "count": len(live),
        "subagents": [
            {
                "subagent_id": child.id,
                "goal": child.goal,
                "model": child.model,
                "status": str(child.status),
                "running_seconds": int((now - child.started_at).total_seconds()),
                "accepting_steer": True,
            }
            for child in live
        ],
    }
    if not live:
        result["note"] = (
            "No live subagents right now. The children that finished have sent their results, or "
            "soon will, in ordinary completion messages; none is left to steer or stop."
        )
    return result


def not_live(subagent_id: str) -> str:
    """The answer for a steer or a stop with no live child behind it."""
    return (
        f"No live subagent '{subagent_id}' among those this conversation spawned. Perhaps it is "
        "already done, and then its result comes in an ordinary completion message. "
        "action='list' shows the children still running."
    )


def _natural_to_cron(rest: str) -> str | None:
    """`monday 9am`, `weekdays at 9am`, `monday, wednesday at 18:30` as a cron expression, or
    `None`."""
    tokens = rest.lower().replace(",", " ").split()
    if not tokens:
        return None
    day = _DAYSPECS.get(tokens[0])
    index = 1
    if day is None:
        days: list[str] = []
        index = len(tokens)
        for position, token in enumerate(tokens):
            if token == "and":  # noqa: S105 - a word of the schedule, not a password
                continue
            mapped = _WEEKDAYS.get(token)
            if mapped is None:
                index = position
                break
            if mapped not in days:
                days.append(mapped)
        if not days:
            return None
        day = ",".join(days)
    clock = tokens[index:]
    if clock and clock[0] == "at":
        clock = clock[1:]
    parsed = _clock(" ".join(clock)) if clock else None
    if parsed is None:
        return None
    hour, minute = parsed
    return f"{minute} {hour} * * {day}"


def _clock(text: str) -> tuple[int, int] | None:
    """`9am`, `9:30am`, `14:00`, `7` (24h), `noon`, `midnight` as (hour, minute)."""
    compact = text.strip().lower().replace(" ", "")
    if compact in ("noon", "midday"):
        return 12, 0
    if compact == "midnight":
        return 0, 0
    found = re.match(r"^(\d{1,2})(?::(\d{2}))?(am|pm)?$", compact)
    if found is None:
        return None
    hours, minutes, half = found.groups()
    hour = int(hours)
    minute = int(minutes) if minutes else 0
    if half is not None:
        # 12am is midnight and 12pm noon; a 12-hour clock has no 0 and no 13.
        if hour < 1 or hour > 12:
            return None
        hour %= 12
        if half == "pm":
            hour += 12
    return (hour, minute) if hour <= 23 and minute <= 59 else None


def _cron(expr: str, display: str) -> Schedule:
    if not croniter.is_valid(expr):
        raise ScheduleError(f"{display!r} is not a valid cron expression.")
    return Schedule(ScheduleKind.CRON, display, expr=expr)


# -- the delegation record (step 16c) --------------------------------------------------------------


class Answerer(StrEnum):
    """Who answered what an executor instance asked."""

    USER = "user"
    #: The guardian or a model, with "Atlas answers" on.
    ATLAS = "atlas"
    #: Nobody: no screen was there, or the instance stopped first.
    NOBODY = "nobody"


@dataclass(frozen=True, slots=True)
class Exchange:
    """One thing said to an instance during a turn besides its prompt: a permission or a
    question it asked and its answer, or a steer it was given."""

    #: `permission`, `question` or `steer`.
    kind: str
    asked: str
    answer: str = ""
    by: Answerer = Answerer.USER


@dataclass(frozen=True, slots=True)
class InstanceRecord:
    """An executor instance, as Atlas keeps it: it outlives the harness's own transcript (Claude
    Code deletes a session after 30 days). Times are aware, in UTC."""

    id: str
    executor: str
    title: str
    #: `user` or `atlas`.
    owner: str
    folder: str
    account: str
    state: str
    started_at: datetime
    updated_at: datetime
    session_id: str = ""
    #: The conversation it worked for, when Atlas delegated, and that delegation's subagent.
    parent_id: str = ""
    subagent_id: str = ""
    #: Its stored conversation.
    transcript_id: str = ""


@dataclass(frozen=True, slots=True)
class ExecutorAccount:
    """Another account of an executor (step 16h), as Atlas keeps it: the user's nickname for it and
    the harness's own folder for it, which keeps its login, settings and sessions. Atlas never
    keeps a credential. `added_at` is aware, in UTC."""

    id: str
    executor: str
    #: The user's nickname for it ("company-b"), one per executor, which names its folder too.
    name: str
    #: The harness's folder for it (`~/.claude-company-b`), absolute.
    folder: str
    added_at: datetime


@dataclass(frozen=True, slots=True)
class AccountFailover:
    """How an executor's accounts take over from one another when one reaches its plan's limit
    (asked for by the user on 21/10/2026): the session goes on in a copy on the next account, in
    the user's order, and, with `compact`, compacts there before it goes on (on unless the user
    turned it off, decided with the user on 25/10/2026)."""

    executor: str
    enabled: bool = True
    compact: bool = True
    #: The accounts' ids in the order they take over, the main one as `""`; an account missing
    #: from it comes after, in the accounts' own order.
    order: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteTarget:
    """One destination of a `SessionRoute`: an executor and one of its accounts together (the main
    one as `""`), so two executors' accounts never collide, and the model and the effort the session
    goes on with there (asked for by the user on 25/10/2026); `None`, the ones it ran on. A
    destination is its executor and account: two with the same differ only in what they choose."""

    executor: str
    account: str = ""
    model: str | None = field(default=None, compare=False)
    effort: str | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class SessionRoute:
    """The destinations one session goes on to when its current one reaches its plan's limit, in
    the user's order (asked for by the user on 21/10/2026): accounts of the same executor, where it
    goes on in a copy, and of other executors, where a new session starts from a `handoff packet`.
    Kept for the session's card, by the conversation Atlas keeps of it; with `enabled` off, or
    no destinations, the executor's own `AccountFailover` holds."""

    conversation: str
    targets: tuple[RouteTarget, ...] = ()
    enabled: bool = True
    #: Compact the copy on another account of the same executor before it goes on, where the
    #: harness has `/compact` (on by default, decided with the user on 25/10/2026): at the switch,
    #: never before the limit.
    compact: bool = True


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One turn of an instance: its prompt, what was asked and answered, its final text and the
    files it changed (from its tool calls)."""

    instance_id: str
    number: int
    prompt: str
    folder: str
    started_at: datetime
    ended_at: datetime | None = None
    text: str = ""
    failed: bool = False
    #: How it ended, as the harness said (`completed`, `aborted_streaming`).
    reason: str = ""
    session_id: str = ""
    files: tuple[str, ...] = ()
    exchanges: tuple[Exchange, ...] = ()

    @property
    def searched(self) -> str:
        """What the record's text search reads of it: everything said, and the files."""
        said = [self.prompt, *(f"{e.asked} {e.answer}" for e in self.exchanges), self.text]
        return "\n".join([*said, *self.files])


def exchanges_text(exchanges: Sequence[Exchange]) -> str:
    """Exchanges as the database keeps them, JSON."""
    return json.dumps(
        [
            {"kind": e.kind, "asked": e.asked, "answer": e.answer, "by": e.by.value}
            for e in exchanges
        ],
        ensure_ascii=False,
    )


def exchanges_of(text: str) -> tuple[Exchange, ...]:
    """Exchanges read back; what cannot be read is left out."""
    try:
        raw: object = json.loads(text)
    except ValueError:
        return ()
    if not isinstance(raw, list):
        return ()
    found: list[Exchange] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        by = str(entry.get("by", "user"))
        found.append(
            Exchange(
                str(entry.get("kind", "")),
                str(entry.get("asked", "")),
                str(entry.get("answer", "")),
                Answerer(by) if by in set(Answerer) else Answerer.USER,
            )
        )
    return tuple(found)
