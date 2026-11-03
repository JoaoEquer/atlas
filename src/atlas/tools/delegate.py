"""delegate: hands tasks to subagents, which work on them with their own context while the
conversation goes on, and lets the model list, steer and stop them.

Decided with the user on 12/10/2026 (docs/ARCHITECTURE.md, Subagents):

- **Always on.**
- **One call is one delegation**: its tasks run in parallel, and their results come back together,
  as one notice, after the parent's turn. In a routine nobody receives that notice, so the call
  waits for them and returns them.
- **The control actions** (`list`, `steer`, `stop`) return at once and act only on the
  conversation's own children.
- **The tool exists only where a subagent can be made**: a subagent itself is not given it (one
  level). The outside executors of step 13 join it as an optional `executor`.
- **Atlas picks the account and the route** (asked for by the user on 21/10/2026): `executor` is a
  `target name`, an executor alone or on one of its accounts (`claude-code@work`); `route` is
  where a task goes on at a plan's limit, accounts of several executors in order; and
  `executor_accounts`, deferred, tells who each account is and how much of its plan is left.
"""

import copy
import json
from collections.abc import Sequence
from typing import Any, Protocol, cast

from atlas.agent import Tool
from atlas.jobs import MAX_TASKS, DelegationError, Task, read_tasks

NAME = "delegate"


class Delegator(Protocol):
    """A conversation's subagents, as its delegate tool reaches them. Each method answers with
    what the model reads."""

    async def spawn(
        self,
        tasks: tuple[Task, ...],
        executor: str | None = None,
        *,
        auto: bool = False,
        route: Sequence[str] = (),
    ) -> str:
        """`auto`: the executor's own automatic mode, where it has one Atlas may turn on. `route`:
        the target names a task goes on to at a plan's limit, in order."""
        ...

    def executors(self) -> list[str]:
        """The outside executors it can hand tasks to (step 13), and their accounts by target
        name (`claude-code@work`) where they have other accounts."""
        ...

    async def resume(
        self,
        subagent_id: str,
        message: str,
        executor: str | None = None,
        route: Sequence[str] = (),
    ) -> str:
        """Go on with an outside executor's session that ended: on `executor` when it names
        another account or executor, with `route` as its route from now on."""
        ...

    async def account(self, email: str, executor: str | None = None) -> str:
        """Log an account of an outside executor in: `executor` names it, or the one executor
        that switches accounts. `email` empty: whichever the user signs in to."""
        ...

    async def accounts(self, executor: str | None = None) -> str:
        """The executors' accounts, their logins, plan usage and limits."""
        ...

    def list(self) -> str: ...

    async def steer(self, subagent_id: str, message: str) -> str: ...

    async def stop(self, subagent_id: str) -> str: ...


DESCRIPTION = (
    "Hands work to subagents. Each runs apart, with its own conversation and tools, and only its "
    "closing summary comes back to you. Every task goes in `tasks`: an entry starts one "
    "subagent, and several run side by side (the tasks description gives the limit).\n\n"
    "With no one to take a later result (a routine), the children run in parallel and this call "
    "returns their results. Elsewhere they run in the background, and their results come back as "
    "a new message when they finish, one message per call. It arrives only BETWEEN your turns: "
    "do what does not need it, say in one line where things stand, then END YOUR TURN. Never "
    "wait for a child or poll files for it. While children run, `action` (list/steer/stop) "
    "controls them.\n\n"
    "GOOD FOR: subtasks that take heavy reasoning, work whose output would fill your context, "
    "and lines of work independent of each other.\n"
    "NOT FOR (do this instead):\n"
    "- One tool call -> make that call yourself\n"
    "- Work that needs the user to answer -> a subagent cannot ask questions\n"
    "- Work that must outlast this session -> cronjob_manage or run_command(background, "
    "notify_on_complete). Ending your turn or Atlas exiting stops running subagents, and each "
    "returns an 'interrupted' completion with what it had so far.\n\n"
    "RULES:\n"
    "- A child has seen none of this conversation: put all it needs in 'context', with the "
    'language, tone or style its answer must have (e.g. "respond in Chinese").\n'
    '- What children report are SELF-REPORTS, not checked facts: "uploaded successfully" or '
    '"file written" can be false. For effects outside (an upload, a remote write, a '
    "publication), require a handle you can check (a URL, an ID, an absolute path) and check it "
    "yourself before telling the user it worked.\n"
    "- Children have no delegate, ask_user, memory or cronjob_manage.\n"
    "- Children run on your model."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tasks": {
            "type": "array",
            "minItems": 1,
            "description": (
                f"What to hand over, at most {MAX_TASKS} tasks at once. Every entry starts its "
                "own subagent in a context of its own; one task is an array of one. Needed to "
                "spawn."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": (
                            "The outcome this subagent must reach, stated precisely and in full: "
                            "it has seen nothing of your conversation."
                        ),
                    },
                    "context": {
                        "type": "string",
                        "description": (
                            "What THIS child must know to do it: file paths, error messages, "
                            "constraints. A child sees its own context and nothing else, so "
                            "background two tasks share goes into both."
                        ),
                    },
                },
                "required": ["goal"],
            },
        },
        "action": {
            "type": "string",
            "enum": ["spawn", "list", "steer", "stop"],
            "description": (
                "'spawn' unless set. The others act on running children: 'list' gives their "
                "ids, goals and status; 'steer' queues a correction for one child "
                "(subagent_id + message) and lets it go on; 'stop' ends one child before it is "
                "done (subagent_id), and what it had still comes back. These return at once, "
                "and tasks counts only for a spawn."
            ),
        },
        "subagent_id": {
            "type": "string",
            "description": (
                "The child that action='steer'/'stop' acts on, an id from the spawn reply or "
                "from action='list'."
            ),
        },
        "message": {
            "type": "string",
            "description": (
                "For action='steer': the correction. The child reads it once its current tool "
                "calls end. Say plainly and precisely what to do."
            ),
        },
    },
    "required": [],
}


#: What the tool says when an outside executor is installed (step 13), Atlas's words: the
#: executors' part costs the always-on budget only where one exists. Since step 16o it names the
#: executors installed, each a coding agent of the user's own (Claude Code, Codex); since
#: 21/10/2026 it says how to pick an account and a route.
EXECUTORS_DESCRIPTION = (
    "\n\nOUTSIDE EXECUTORS: executor={names} hands the tasks to the user's own coding agent "
    "instead of subagents, for coding work in a project: give each task its project folder. "
    "'name@account' picks the account (executor_accounts: logins, plan usage). What it asks "
    "reaches the user, or Atlas in Auto; its result returns as a subagent's does. At its plan "
    "limit it goes on by itself on `route`, else its next account; if none can, tell the user. "
    "action='resume' goes on later, elsewhere if `executor` says. Only when the user asks: "
    "action='account' logs an account in."
)
ROUTE_DESCRIPTION = (
    "Where to go on at a plan limit, in order; another executor starts from a handoff."
)

#: Approval modes for an outside executor's delegation: its own automatic mode approves for it
#: (the default since 20/10/2026, asked for by the user), or it asks the user.
MANUAL, AUTO = "manual", "auto"
APPROVAL_DESCRIPTION = (
    "Default 'auto': the executor approves its own permission requests under its own rules. "
    "'manual' only when the user asked: it asks the user before what it does."
)


#: What the tool says when plugins bring agents (step 16k), Atlas's words.
AGENTS_DESCRIPTION = (
    "\n\nPREDEFINED SUBAGENTS: a task's `agent` runs it as one of these, with its own "
    "instructions and tools, in place of a plain subagent:\n"
)


def delegate(delegator: Delegator | None, *, agents: Sequence[tuple[str, str]] = ()) -> Tool | None:
    """The delegate tool over a conversation's subagents, or `None` where none can be made.
    `agents` are the plugins' predefined subagents, by name and when to use each (step 16k)."""
    if delegator is None:
        return None
    executors = delegator.executors()
    description, parameters = DESCRIPTION, PARAMETERS
    if agents:
        description += AGENTS_DESCRIPTION + "\n".join(f"- {n}: {d}" for n, d in agents)
        parameters = copy.deepcopy(PARAMETERS)
        parameters["properties"]["tasks"]["items"]["properties"]["agent"] = {
            "type": "string",
            "enum": [name for name, _ in agents],
            "description": "A predefined subagent to run this task as.",
        }
    if executors:
        bases = list(dict.fromkeys(name.partition("@")[0] for name in executors))
        description += EXECUTORS_DESCRIPTION.format(names=" or ".join(f"'{n}'" for n in bases))
        parameters = copy.deepcopy(parameters)
        parameters["properties"]["executor"] = {
            "type": "string",
            "enum": executors,
            "description": "An outside executor for the tasks, in place of subagents.",
        }
        parameters["properties"]["route"] = {
            "type": "array",
            "items": {"type": "string"},
            "description": ROUTE_DESCRIPTION,
        }
        parameters["properties"]["tasks"]["items"]["properties"]["folder"] = {
            "type": "string",
            "description": "For an outside executor: the project folder it works in.",
        }
        parameters["properties"]["action"]["enum"] = [
            "spawn", "list", "steer", "stop", "resume", "account",
        ]  # fmt: skip
        parameters["properties"]["account"] = {
            "type": "string",
            "description": "The email to log in as, for action='account'.",
        }
        parameters["properties"]["approval"] = {
            "type": "string",
            "enum": [MANUAL, AUTO],
            "description": APPROVAL_DESCRIPTION,
        }

    async def run(arguments: dict[str, Any]) -> str:
        action: object = arguments.get("action") or "spawn"
        if isinstance(action, str):
            action = action.strip().lower()
        if action == "spawn":
            try:
                tasks = read_tasks(arguments.get("tasks"))
            except DelegationError as error:
                return f"[Atlas: {error}]"
            executor = arguments.get("executor")
            approval: object = arguments.get("approval") or AUTO
            if isinstance(approval, str):
                approval = approval.strip().lower()
            if executors and approval not in (MANUAL, AUTO):
                return f"[Atlas: Unknown approval {approval!r}. Use 'auto' (default) or 'manual'.]"
            route = _route(arguments.get("route"), executors)
            if isinstance(route, str):
                return route
            if executor is None or executor == "":
                if route:
                    return "[Atlas: A route is for an outside executor's tasks: give `executor`.]"
                # `approval` is an outside executor's own mode: Atlas's subagents have none.
                return await delegator.spawn(tasks)
            if not _known(executor, executors):
                known = ", ".join(executors) or "none here"
                return f"[Atlas: There is no executor {executor!r}. Installed: {known}.]"
            return await delegator.spawn(
                tasks, str(executor).strip(), auto=approval == AUTO, route=route
            )
        if action == "list":
            return delegator.list()
        if executors and action == "account":
            email = arguments.get("account") or ""
            if not isinstance(email, str) or (email.strip() and "@" not in email):
                return "[Atlas: action='account' takes the account's email in 'account'.]"
            named = arguments.get("executor")
            executor = (
                named.strip() if isinstance(named, str) and _known(named, executors) else None
            )
            return await delegator.account(email.strip(), executor)
        if executors and action == "resume":
            subagent_id = arguments.get("subagent_id")
            if not isinstance(subagent_id, str) or not subagent_id.strip():
                return "[Atlas: action='resume' requires the subagent_id of the stopped task.]"
            route = _route(arguments.get("route"), executors)
            if isinstance(route, str):
                return route
            named = arguments.get("executor")
            executor = None
            if isinstance(named, str) and named.strip():
                if not _known(named, executors):
                    known = ", ".join(executors)
                    return f"[Atlas: There is no executor {named!r}. Installed: {known}.]"
                executor = named.strip()
            message = arguments.get("message")
            return await delegator.resume(
                subagent_id.strip(),
                message.strip() if isinstance(message, str) else "",
                executor,
                route,
            )

        if action not in ("steer", "stop"):
            return f"[Atlas: Unknown action {action!r}. Use spawn (default), list, steer, or stop.]"
        subagent_id = arguments.get("subagent_id")
        if not isinstance(subagent_id, str) or not subagent_id.strip():
            return (
                f"[Atlas: action='{action}' requires subagent_id; the spawn reply and "
                "action='list' give it.]"
            )
        if action == "stop":
            return await delegator.stop(subagent_id.strip())
        message = arguments.get("message")
        if not isinstance(message, str) or not message.strip():
            return (
                "[Atlas: action='steer' requires a non-empty 'message': the correction to pass "
                "to the child.]"
            )
        return await delegator.steer(subagent_id.strip(), message.strip())

    return Tool(name=NAME, description=description, parameters=parameters, run=run)


def _known(name: object, executors: Sequence[str]) -> bool:
    """`name` is an executor installed, alone or on an account: one added since the tool was made
    is checked by the delegator."""
    if not isinstance(name, str):
        return False
    return name.strip() in executors or name.strip().partition("@")[0] in executors


def _route(found: object, executors: Sequence[str]) -> tuple[str, ...] | str:
    """The route the model gave, as target names; a JSON string of them is read too. What the
    model is told when it is not a list of executors installed."""
    if found is None or found == "" or found == []:
        return ()
    if isinstance(found, str):
        try:
            found = json.loads(found)
        except ValueError:
            found = [found]
    if not isinstance(found, list):
        return "[Atlas: `route` is a list of executors, each alone or on an account.]"
    names: list[str] = []
    for name in cast("list[object]", found):
        if not _known(name, executors):
            known = ", ".join(executors)
            return (
                f"[Atlas: The route names {name!r}, which is no executor here. Installed: {known}.]"
            )
        names.append(str(name).strip())
    return tuple(names)


#: What executor_accounts says, Atlas's words; deferred, found by tool search.
ACCOUNTS_DESCRIPTION = (
    "Before delegating to an outside executor: each of its accounts by the name delegate's "
    "executor and route take ('claude-code@work'), who it is logged in as, its plan usage per "
    "limit and when each resets, whether it is at its limit now or bills past it (extra_usage), "
    "and the order its accounts take over in. No model call."
)


def executor_accounts(delegator: Delegator | None) -> Tool | None:
    """The tool that shows the outside executors' accounts, or `None` where there is none."""
    if delegator is None or not delegator.executors():
        return None

    async def run(arguments: dict[str, Any]) -> str:
        executor = arguments.get("executor")
        return await delegator.accounts(executor if isinstance(executor, str) else None)

    return Tool(
        name="executor_accounts",
        description=ACCOUNTS_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "executor": {
                    "type": "string",
                    "description": "Only this executor's accounts (`claude-code`).",
                },
            },
            "required": [],
        },
        run=run,
        read_only=True,
        deferred=True,
    )
