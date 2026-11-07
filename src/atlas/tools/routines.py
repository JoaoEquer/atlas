"""cronjob_manage: the agent makes, lists, changes, pauses, resumes, removes and runs routines.

Decided on 10/10/2026: deferred behind tool search, with the fields Atlas keeps: no delivery
target (the run is a conversation and a notification), no pinned model (the user pins one). The
prompt is scanned for threats before it is kept, and a routine's run is never given this tool, so a
routine cannot make routines.

A command routine, which runs with no model, was the user's alone until 25/10/2026, when the user
asked for the agent to make one too: its command passes the approval gate as a `run_command` would,
when it is made and when it is changed (`atlas.trust`), so the gate that would stop the command now
stops it from being scheduled. A bot makes only prompt routines: a command runs as nobody.

A prompt routine's probe (step 23) is a command too, so it passes the same gate, and a bot gives
none.
"""

from typing import Any

from atlas.agent import Tool
from atlas.jobs import Creator, JobsError, Routine, RoutineExecutor, Routines

_ACTIONS = ("create", "list", "update", "pause", "resume", "remove", "run")

_DESCRIPTION = (
    "Manages scheduled routines. action='create' schedules one from a prompt or a command; "
    "'list' shows them; "
    "'update', 'pause', 'resume', 'remove' and 'run' act on one by job_id (list first; never guess "
    "an id). 'run' fires it within a minute, in the background: its outcome arrives as its own "
    "conversation and a notification, so do not wait. A routine runs in a new conversation with "
    "none of this one's context, so its prompt must stand alone, and its final reply is what the "
    "user sees; it cannot ask questions. A reply of [SILENT] notifies nobody. A command routine "
    "runs a shell command with no model, for a script or a check that needs no thinking: its "
    "output is what the user gets, and an empty output notifies nobody. A probe, beside a prompt "
    "and a repeating schedule, is a command run first with no model: the prompt runs only when "
    "the probe prints something new, which it receives. An executor (claude-code, codex...) with "
    "a folder runs the prompt as its own session there instead of you; the user approves it once "
    "when it is made, and a question it asks stops the run for the user. Prefer updating a "
    "routine over making a near-duplicate."
)

_SCHEDULE = (
    "Required for create. Forms: every 30m or every 2h (repeats); in 30m (once that far from now: "
    "use it for 'remind me in'; never compute a time yourself); every monday 9am, weekdays at 9am, "
    "every day at 18:30 (the user's local time); cron syntax, 0 9 * * *; or an ISO time, "
    "2026-06-01T09:00 (once)."
)


def cronjob_manage(routines: Routines, *, bot: str | None = None) -> Tool:
    """The tool over the home's routines. A `bot` makes, lists and changes only its own, which
    run as it and report to its fixed conversation; the main agent reaches every routine."""

    def mine(key: str) -> str:
        found = routines.store.get(key)
        if bot is not None and found.bot != bot:
            raise JobsError(f"There is no routine {key!r} among yours. List the routines first.")
        return found.id

    async def run(arguments: dict[str, Any]) -> str:
        action = arguments.get("action")
        if action not in _ACTIONS:
            return f"[Atlas: action must be one of: {', '.join(_ACTIONS)}.]"
        key = arguments.get("job_id")
        try:
            if action == "list":
                return _listing([r for r in routines.store.all() if bot is None or r.bot == bot])
            probe = arguments.get("probe")
            probe = probe if isinstance(probe, str) else None
            if probe and bot is not None:
                return "[Atlas: a bot's routine has no probe: a probe is a command, run as nobody.]"
            executor = _executor(arguments)
            if executor is not None and bot is not None:
                return "[Atlas: a bot's routine runs on Atlas's model, never in an executor.]"
            if action == "create":
                prompt = _optional(arguments.get("prompt"))
                command = _optional(arguments.get("command"))
                made = routines.create(
                    _text(arguments.get("name")) or (prompt or command or "")[:40],
                    _text(arguments.get("schedule")),
                    prompt=prompt if prompt is not None or command is not None else "",
                    command=command,
                    created_by=Creator.AGENT,
                    bot=bot,
                    probe=probe,
                    executor=executor,
                )
                return f"Made routine {made.id} ({made.name}): {_line(made)}"
            if not isinstance(key, str) or not key.strip():
                return f"[Atlas: {action} needs job_id: list the routines first.]"
            key = mine(key)
            if action == "update":
                changed = routines.update(
                    key,
                    name=_optional(arguments.get("name")),
                    schedule=_optional(arguments.get("schedule")),
                    prompt=_optional(arguments.get("prompt")),
                    command=_optional(arguments.get("command")),
                    probe=probe,
                    executor=executor,
                )
                return f"Updated {changed.id}: {_line(changed)}"
            if action == "pause":
                return f"Paused {routines.pause(key).name}."
            if action == "resume":
                return f"Resumed: {_line(routines.resume(key))}"
            if action == "remove":
                return f"Removed {routines.remove(key).name}."
            fired = routines.trigger(key)
            return (
                f"{fired.name} will run within a minute, in the background. Its outcome arrives as "
                "its own conversation and a notification: do not wait for it."
            )
        except JobsError as error:
            return f"[Atlas: {error}]"

    return Tool(
        name="cronjob_manage",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "job_id": {
                    "type": "string",
                    "description": "The routine's id or name, for update, pause, resume, remove "
                    "and run.",
                },
                "name": {"type": "string", "description": "A short name."},
                "schedule": {"type": "string", "description": _SCHEDULE},
                "prompt": {
                    "type": "string",
                    "description": "For a routine the agent runs: the whole task, standing alone. "
                    "Give it or command, not both.",
                },
                "command": {
                    "type": "string",
                    "description": "For a routine that runs no model: a command in the system's "
                    "shell, run in the working folder. Give it or prompt, not both.",
                },
                "probe": {
                    "type": "string",
                    "description": "With a prompt: a command whose new output wakes it, e.g. one "
                    "that prints unread mail. It may keep state in $ATLAS_PROBE_STATE. Empty on "
                    "update removes it.",
                },
                "executor": {
                    "type": "string",
                    "description": "With a prompt and a folder: the outside executor that runs it "
                    "(claude-code, codex, gemini-cli). Empty on update gives it back to you.",
                },
                "folder": {
                    "type": "string",
                    "description": "With an executor: the project folder it works in, its whole "
                    "path.",
                },
                "auto": {
                    "type": "boolean",
                    "description": "With an executor: run in its automatic mode, which asks "
                    "nothing. Off, its first question stops the run for the user.",
                },
                "workspace": {
                    "type": "boolean",
                    "description": "With an executor: each run in a new workspace of the folder, "
                    "for a routine that changes code; one that only reads works in the folder.",
                },
                "draft": {
                    "type": "boolean",
                    "description": "With a workspace: open a draft pull request of what a run "
                    "changed (the GitHub plugin). Only when the user asks for it.",
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        run=run,
        deferred=True,
    )


def _listing(found: list[Routine]) -> str:
    if not found:
        return "No routines."
    return "\n".join(
        f"- {routine.id} {routine.name}{_whose(routine)}: {_line(routine)}" for routine in found
    )


def _whose(routine: Routine) -> str:
    return "" if routine.bot is None else f" (the bot {routine.bot}'s)"


def _line(routine: Routine) -> str:
    state = "on" if routine.enabled else "paused"
    upcoming = (
        "no next run"
        if routine.next_run_at is None
        else f"next run {routine.next_run_at.isoformat(timespec='minutes')}"
    )
    last = "" if routine.last_status is None else f", last run {routine.last_status.value}"
    what = routine.prompt if routine.prompt is not None else f"command: {routine.command}"
    probe = "" if routine.probe is None else f" Woken by the probe: {routine.probe}"
    runs = routine.executor
    where = "" if runs is None else f" Run by {runs.name} in {runs.folder}."
    return f"{routine.schedule.display}, {state}, {upcoming}{last}. {what}{probe}{where}"


def _executor(arguments: dict[str, Any]) -> RoutineExecutor | None:
    """The executor the arguments name (step 25); an empty name gives the routine back to the
    agent's model on update; `None` when they name none."""
    name = arguments.get("executor")
    if not isinstance(name, str):
        return None
    return RoutineExecutor(
        name.strip(),
        _text(arguments.get("folder")).strip(),
        auto=arguments.get("auto") is True,
        workspace=arguments.get("workspace") is True,
        draft=arguments.get("draft") is True,
    )


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None
