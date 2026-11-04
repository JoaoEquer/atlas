"""Subagents for this Atlas: the tasks a conversation delegates, each run as a conversation of its
own in the subagent role, and their summaries brought back to the parent.

Decided with the user on 12/10/2026 (docs/ARCHITECTURE.md, Subagents):

- **A child is a conversation** of source `subagent`, with its parent's id and its parent's model,
  that runs one turn: its goal. It is a routine's run in shape (`RoutineRunner`), watched instead
  of unattended: a watchdog stops it after 450 seconds without activity, or 1,200 inside one tool,
  as `stalled`.
- **One call is one batch**: its children run in parallel and, when the last ends, one notice
  brings every summary to the parent. The notice starts a turn, or waits for the one running, as
  a background command's notice does. A routine's call waits instead, and so does a call past 10
  batches in the background.
- **The parent steers and stops its own children**: a steer enters after the child's current tool
  calls as an out-of-band user message, and one that lands too late is said in the summary; a
  stop keeps what the child last said. Stopping the parent's turn stops its children.
- **Each child is written down** when it starts and when it ends (`jobs_subagents`). A process that
  ends with children running leaves them `running`; the next one marks them `unknown`, never
  `failed`, and delivers every batch that never reached its parent, for 48 hours.
- **Its activity reaches the parent's dock** (`SubagentChanged`), and counts as the parent's
  activity: a routine waiting on its children is not idle.
"""

import asyncio
import dataclasses
import json
import logging
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from atlas.agent import ToolFinished, ToolStarted, TurnOutcome
from atlas.assistant.executors import (
    ExecutorRun,
    account_name,
    default_executor_effort,
    other_accounts,
    split_account,
)
from atlas.assistant.model import (
    AccountView,
    ConversationRole,
    Decides,
    ExecutorUsage,
    InstanceActivity,
    InstanceState,
    Owner,
    SubagentChanged,
)
from atlas.assistant.workbench import MAIN_ACCOUNT, limit_reason, not_compacted
from atlas.conversations import APP, SUBAGENT, ConversationNotFoundError, title_from
from atlas.home import format_utc, time_zone
from atlas.hooks import CLAUDE_CODE_NAMES
from atlas.jobs import (
    MAX_BACKGROUND,
    RECOVERY_HOURS,
    STALE_IDLE_SECONDS,
    STALE_IN_TOOL_SECONDS,
    STEER_CLOSE,
    STEER_OPEN,
    WAITED_NOTES,
    AgentDefinition,
    DelegationError,
    Subagent,
    SubagentStatus,
    Task,
    batch_notice,
    child_prompt,
    dispatched,
    is_steer,
    listed,
    missed_steer,
    not_live,
    steer_message,
    summary_cap,
    trimmed_summary,
)
from atlas.models import AssistantMessage, Message, ModelChoice
from atlas.plugins import (
    Account,
    Called,
    Executor,
    ExecutorError,
    ExecutorEvent,
    ExecutorSpec,
    Finished,
    LimitReached,
    NextStop,
    Returned,
)

if TYPE_CHECKING:
    from atlas.assistant.service import Assistant, ConversationEvent, OpenConversation

_logger = logging.getLogger("atlas.assistant")

#: How often the watchdog looks.
_LOOK_SECONDS = 1.0
#: What an executor is told when it goes on after a stop.
CONTINUE = "Continue the task from where you stopped."
#: Why nothing was handed to an executor when it has no login.
NOT_LOGGED_IN = (
    "[Atlas: {title} is not logged in on this computer, so nothing was delegated. See its "
    "other accounts with executor_accounts, or ask the user which account to use and log it in "
    "with delegate(action='account').]"
)
#: How the model names an executor's main account in a target name (`claude-code@main`).
MAIN_TARGET = "main"
#: The executors whose own automatic mode a delegation runs in (decided with the user on
#: 17/10/2026; the default since 20/10/2026): Claude Code's `auto`, where its classifier reviews
#: each action under its own rules and the user's settings, Atlas adding none, and since step 16o
#: Codex's reviewer (`auto_review`), its subagent that judges each request by its risk, inside
#: its sandbox. Since 21/10/2026 the task is not told to keep to its folder either, asked for by
#: the user. Another executor's always asks: one whose automatic mode reviews nothing never
#: enters here, with two exceptions the user chose on 21/10/2026: the Gemini CLI's `yolo` (step
#: 16p) and DeepSeek's `danger-full-access` (step 16q), which approve every tool, preferred over
#: Atlas's guardian.
AUTO_EXECUTORS = frozenset({"claude-code", "codex", "deepseek", "gemini-cli"})


class TaskWorkspaces(Protocol):
    """The workspaces a delegated task may ask for (step 21), as the server makes them: a
    worktree of a project's on a branch of its own, prepared before the task starts in it."""

    async def make(self, folder: Path, name: str) -> tuple[Path, str]:
        """A new workspace of `folder`'s project named `name`, once its setup script ended: its
        folder and its branch. Raises `DelegationError` with what the model is told."""
        ...

    async def changes(self, folder: Path) -> str:
        """What the workspace at `folder` changed since it was made, in a line."""
        ...


@dataclass
class _Child:
    """A running child and what its dock shows."""

    record: Subagent
    parent: "OpenConversation"
    #: Atlas's own subagent runs as a conversation; an outside executor as a run of its own.
    conversation: "OpenConversation | None" = None
    run: ExecutorRun | None = None
    #: What an outside executor is given: the task, or what it goes on with.
    prompt: str = ""
    task: "asyncio.Task[None] | None" = None
    #: When an outside executor last did something, for its watchdog.
    last_activity: datetime | None = None
    last_tool: str | None = None
    tools: int = 0
    #: Tool calls started and not finished: the watchdog then waits longer.
    in_tool: int = 0
    #: Its parent, or the user, asked it to stop.
    stopping: bool = False
    #: The branch of the new workspace it works in (step 21); empty, it works in its folder.
    workspace: str = ""


class Subagents:
    """Every subagent of one Atlas."""

    def __init__(
        self,
        assistant: "Assistant",
        *,
        idle: float = STALE_IDLE_SECONDS,
        in_tool: float = STALE_IN_TOOL_SECONDS,
        look: float = _LOOK_SECONDS,
    ) -> None:
        self._assistant = assistant
        #: The watchdog: seconds without activity, idle or inside a tool, and how often it looks.
        self.idle = idle
        self.in_tool = in_tool
        self.look = look
        self._live: dict[str, _Child] = {}
        self._batches: set[asyncio.Task[None]] = set()
        self._background = 0
        self._closing = False

    def delegator(self, parent: "OpenConversation") -> "_Delegator":
        """What a conversation's delegate tool reaches."""
        return _Delegator(self, parent)

    def live(self, parent_id: str) -> list[SubagentChanged]:
        """A conversation's running children, as its dock shows them."""
        return [
            self._view(child)
            for child in self._live.values()
            if child.record.parent_id == parent_id
        ]

    # -- spawning -------------------------------------------------------------------------------

    def executors(self) -> list[str]:
        """The outside executors installed on this computer, by name, and, for one with other
        accounts, each account by its `target name` (`claude-code@main`, `claude-code@work`)."""
        assistant = self._assistant
        names: list[str] = []
        for name in assistant.executors():
            names.append(name)
            found = assistant.executor(name)
            if found is None or not found.capabilities().account_folders:
                continue
            kept = other_accounts(assistant.accounts, name)
            if kept:
                names.append(target_name(name, ""))
                names.extend(target_name(name, account.nickname) for account in kept)
        return names

    def _resolve(self, name: str) -> tuple[str, str | None] | str:
        """A `target name` as the model writes it (`claude-code`, `claude-code@main`,
        `claude-code@work`, or an account's id after the `@`) as its executor and account: `None`
        when it names no account (the folder's then), `""` for the main one; or what the model
        is told when there is no such executor or account."""
        assistant = self._assistant
        base, wanted = split_account(name.strip())
        found = assistant.executor(base)
        if found is None:
            known = ", ".join(assistant.executors()) or "none"
            return f"[Atlas: There is no executor {base!r} on this computer. Installed: {known}.]"
        if not wanted:
            return base, None
        if wanted.lower() == MAIN_TARGET:
            return base, MAIN_ACCOUNT
        kept = (
            other_accounts(assistant.accounts, base) if found.capabilities().account_folders else []
        )
        for account in kept:
            if wanted.lower() in (account.nickname.lower(), account.id):
                return base, account.id
        names = ", ".join([MAIN_TARGET, *(account.nickname for account in kept)])
        return (
            f"[Atlas: {found.title} has no account {wanted!r}. Its accounts: {names}; "
            "executor_accounts shows each.]"
        )

    def _route(self, names: Sequence[str]) -> tuple[str, ...] | str:
        """A delegation's route from the target names the model gave, each as its executor on
        its account (`claude-code@1a2b3c4d`, a bare executor being its own login); or what the
        model is told about one Atlas does not know, or when no plugin keeps routes."""
        if names and self._assistant.accounts is None:
            return (
                "[Atlas: A route needs the accounts plugin, which is not on here. Delegate "
                "without a route, or ask the user to turn the plugin on.]"
            )
        targets: list[str] = []
        for name in names:
            resolved = self._resolve(name)
            if isinstance(resolved, str):
                return resolved
            base, account = resolved
            targets.append(account_name(base, account or MAIN_ACCOUNT))
        return tuple(dict.fromkeys(targets))

    def _keep_route(self, conversation_id: str, route: Sequence[str]) -> None:
        """A delegated task's route, kept by the accounts plugin for its stored conversation as a
        card's is, so it holds when the task is resumed."""
        accounts = self._assistant.accounts
        if not route or accounts is None:
            return
        try:
            accounts.keep_route(conversation_id, route)
        except Exception:  # a plugin's own code: the task goes without its route, logged
            _logger.exception("The accounts plugin could not keep a delegated task's route.")

    async def spawn(
        self,
        parent: "OpenConversation",
        tasks: Sequence[Task],
        executor: str | None = None,
        *,
        auto: bool = False,
        route: Sequence[str] = (),
    ) -> str:
        """Start a batch, on Atlas's subagents or on an outside executor: its children run now;
        the answer says they did, or holds their results when the call waits for them. `auto`
        runs the executor in its own automatic mode, where it has one Atlas may turn on.
        `executor` is a `target name`: on the account it names, else each folder's. `route` is
        where each task goes on at a plan's limit, in order, in place of the executor's own
        order of its accounts."""
        assistant = self._assistant
        targets: tuple[str, ...] = ()
        account: str | None = None
        if executor is not None:
            resolved = self._resolve(executor)
            if isinstance(resolved, str):
                return _nothing_delegated(resolved)
            executor, account = resolved
            routed = self._route(route)
            if isinstance(routed, str):
                return _nothing_delegated(routed)
            targets = routed
        elif route:
            return "[Atlas: A route is for an outside executor's tasks. Nothing was delegated.]"
        elif any(task.workspace for task in tasks):
            return (
                "[Atlas: A new workspace is for an outside executor's tasks: give `executor`. "
                "Nothing was delegated.]"
            )
        #: Each task's predefined subagent, a plugin's agent (step 16k), or `None`.
        agents: list[AgentDefinition | None] = []
        for task in tasks:
            if not task.agent:
                agents.append(None)
                continue
            if executor is not None:
                return (
                    f"[Atlas: {task.agent} runs on Atlas's own subagents, not on an outside "
                    "executor, so nothing was delegated.]"
                )
            agent = assistant.agent(task.agent)
            if agent is None:
                return (
                    f"[Atlas: There is no predefined subagent {task.agent!r}, so nothing was "
                    "delegated.]"
                )
            agents.append(agent)
        found: Executor | None = None
        folders: list[Path] = []
        #: The branch of the new workspace each task asked for (step 21), empty for none.
        branches: list[str] = [""] * len(tasks)
        #: Each task's executor on its folder's account (step 16h), its name and its login.
        accounts: list[tuple[Executor, str, Account]] = []
        if executor is not None:
            found = assistant.executor(executor)
            if found is None:
                return (
                    f"[Atlas: {executor} is not installed on this computer, so nothing was "
                    "delegated. Do the work with your own tools or Atlas's subagents.]"
                )
            if parent.role is not ConversationRole.CONVERSATION:
                return (
                    f"[Atlas: A routine's run cannot hand work to {executor} yet: what it still "
                    "asks, even in its automatic mode, would have nobody to answer it. Nothing was "
                    "delegated; do the work with your own tools or Atlas's subagents.]"
                )
            for task in tasks:
                folder = Path(task.folder).expanduser() if task.folder else assistant.workspace
                folder = folder if folder.is_absolute() else assistant.workspace / folder
                if not folder.is_dir():
                    return (
                        f"[Atlas: The folder {folder} does not exist, so nothing was delegated. "
                        "Give each task the project folder it works in.]"
                    )
                folders.append(folder)
            made = await self._workspaces(tasks, folders)
            if isinstance(made, str):
                return made
            folders, branches = made
            on = await self._accounts(executor, folders, account)
            if isinstance(on, str):
                return on
            accounts = on
            auto = auto and _may_auto(found, executor)
        elif auto:
            return "[Atlas: Atlas's own subagents have no automatic mode. Nothing was delegated.]"
        choice = parent.model
        # In a conversation in Auto, Atlas answers what an executor it delegated to asks in its
        # automatic mode (decided with the user on 21/10/2026); one the user asked for in
        # 'manual' asks the user, and so does the user's own session.
        answers = auto and assistant.auto_mode(parent.id)
        # An outside executor runs on Atlas's default effort, as the user's own sessions start.
        effort = None if found is None else await default_executor_effort(found, None)
        now = assistant.clock()
        delegation_id = f"d-{secrets.token_hex(4)}"
        children: list[_Child] = []
        for index, task in enumerate(tasks):
            # A bot's subagent is the bot's: it inherits what the bot may use, no more.
            stored = assistant.conversations.create(
                source=SUBAGENT,
                parent_id=parent.id,
                title=title_from(task.goal),
                bot=parent.bot_name,
            )
            if found is not None and executor is not None:
                on_account, name, logged = accounts[index]
                record = Subagent(
                    id=f"sa-{secrets.token_hex(4)}",
                    delegation_id=delegation_id,
                    parent_id=parent.id,
                    conversation_id=stored.id,
                    index=index,
                    count=len(tasks),
                    goal=task.goal,
                    context=task.context,
                    model=found.title,
                    started_at=now,
                    executor=name,
                    folder=str(folders[index]),
                    account=logged.login,
                )
                self._keep_route(stored.id, targets)
                spec = ExecutorSpec(folders[index], auto=auto, effort=effort)
                run = self._executor_run(
                    on_account, name, spec, parent, record, atlas_answers=answers
                )
                prompt = f"{task.goal}\n\n{task.context}".strip()
                children.append(
                    _Child(
                        record,
                        parent,
                        run=run,
                        prompt=prompt,
                        last_activity=now,
                        workspace=branches[index],
                    )
                )
                continue
            agent = agents[index]
            model = choice if agent is None else agent_model(agent) or choice
            if model is not None:
                assistant.conversations.set_model(stored.id, model)
            record = Subagent(
                id=f"sa-{secrets.token_hex(4)}",
                delegation_id=delegation_id,
                parent_id=parent.id,
                conversation_id=stored.id,
                index=index,
                count=len(tasks),
                goal=task.goal,
                context=task.context,
                model=model.model if model is not None else "",
                started_at=now,
            )
            prompt = child_prompt(task.context)
            conversation = assistant.conversation(
                stored.id,
                role=ConversationRole.SUBAGENT,
                prompt=prompt if agent is None else f"{agent.prompt}\n\n{prompt}",
                listener=self._listener,
                tools=None if agent is None else agent_tools(agent),
            )
            children.append(_Child(record, parent, conversation=conversation))
        shared = self._shared_folders(children)
        return await self._launch(parent, delegation_id, children, shared)

    async def _workspaces(
        self, tasks: Sequence[Task], folders: list[Path]
    ) -> tuple[list[Path], list[str]] | str:
        """Each task's folder, a new workspace's where it asked for one, and that workspace's
        branch (empty for the others); or what the model is told when one cannot be made, the
        ones made already left for the user, listed in the app."""
        maker = self._assistant.task_workspaces
        branches = [""] * len(tasks)
        if not any(task.workspace for task in tasks):
            return folders, branches
        if maker is None:
            return "[Atlas: Workspaces are not available here. Nothing was delegated.]"
        made = list(folders)
        for index, task in enumerate(tasks):
            if not task.workspace:
                continue
            name = f"{title_from(task.goal)[:40]}-{secrets.token_hex(2)}"
            try:
                made[index], branches[index] = await maker.make(folders[index], name)
            except DelegationError as error:
                return f"[Atlas: {error} Nothing was delegated.]"
        return made, branches

    def _shared_folders(self, children: Sequence[_Child]) -> list[str]:
        """The folders of a batch's tasks where another agent works now: a session of the
        user's, or another delegated task (decided with the user on 03/11/2026, in place of a
        lock on the folder)."""
        busy = {
            run.spec.folder.resolve()
            for run in self._assistant.workbench.runs()
            if run.state in (InstanceState.STARTING, InstanceState.WORKING)
        }
        mine = {child.record.id for child in children}
        busy |= {
            Path(other.record.folder).resolve()
            for other in self._live.values()
            if other.record.folder and other.record.id not in mine and other.run is not None
        }
        return sorted(
            {
                child.record.folder
                for child in children
                if child.record.folder and Path(child.record.folder).resolve() in busy
            }
        )

    async def resume(
        self,
        parent: "OpenConversation",
        subagent_id: str,
        message: str,
        executor: str | None = None,
        route: Sequence[str] = (),
    ) -> str:
        """Go on with an outside executor's session that ended (its plan's limit, a stop), in its
        folder, on the account it ran on (its folder keeps the session), logged in as that
        account's harness is now: its own history continues (decided with the user on
        12/10/2026). When that account is still at its plan's limit, on the next destination of
        the task's route, else the next account in the user's order (21/10/2026). `executor`, a
        `target name`, moves it there instead: another account of its executor goes on in a
        copy, another executor in a new session whose first prompt is the handoff packet.
        `route` replaces the task's route."""
        assistant = self._assistant
        record = assistant.subagent_store.get(subagent_id)
        if record is None or record.parent_id != parent.id:
            return f"[Atlas: There is no subagent '{subagent_id}' of this conversation.]"
        if record.executor is None or not record.session_id:
            return f"[Atlas: '{subagent_id}' has no outside executor's session to resume.]"
        if subagent_id in self._live or not record.done:
            return f"[Atlas: '{subagent_id}' is still running; it needs no resuming.]"
        if route:
            routed = self._route(route)
            if isinstance(routed, str):
                return routed
            await asyncio.to_thread(self._keep_route, record.conversation_id, routed)
        workbench = assistant.workbench
        base, current = split_account(record.executor)
        target: str | None = None
        if executor is not None:
            resolved = self._resolve(executor)
            if isinstance(resolved, str):
                return resolved
            chosen_base, chosen = resolved
            if chosen is None:
                # Its own executor named alone stays on its account; another, on the folder's.
                chosen = (
                    current
                    if chosen_base == base
                    else await asyncio.to_thread(
                        workbench.folder_account, chosen_base, record.folder
                    )
                )
            target = account_name(chosen_base, chosen)
        elif workbench.limited(record.executor):
            found_next = await self._next(record.executor, record.conversation_id, None)
            target = None if found_next is None else found_next.executor
        name = record.executor if target is None else target
        crossing = split_account(name)[0] != base
        resume_from = None
        if not crossing and name != record.executor:
            kept_in = assistant.executor(record.executor)
            if kept_in is None:
                return (
                    f"[Atlas: The account '{subagent_id}' ran on is gone, so its session cannot "
                    "be copied to another account. Resume it on another executor instead.]"
                )
            resume_from = kept_in.account_folder()
        found = assistant.executor(name)
        if found is None:
            return f"[Atlas: {name} is no longer installed on this computer.]"
        logged = await _logged_in(found)
        if logged is None:
            return NOT_LOGGED_IN.format(title=found.title)
        prompt = message.strip() or CONTINUE
        if crossing:
            source = await asyncio.to_thread(workbench.label, record.executor)
            reason = (
                f"Atlas moved this task here from {source}, which it ran on before."
                if executor is not None
                else limit_reason(source, None, "the task's route")
            )
            history = assistant.conversations.open(record.conversation_id).history
            packet = await workbench.handoff(
                record.executor, record.session_id, Path(record.folder), history, name, reason
            )
            prompt = f"{packet}\n\n{message.strip()}" if message.strip() else packet
        now = assistant.clock()
        delegation_id = f"d-{secrets.token_hex(4)}"
        resumed = dataclasses.replace(
            record,
            id=f"sa-{secrets.token_hex(4)}",
            delegation_id=delegation_id,
            index=0,
            count=1,
            started_at=now,
            status=SubagentStatus.RUNNING,
            ended_at=None,
            summary="",
            error=None,
            model_calls=0,
            cost_usd=Decimal(0),
            truncated=False,
            delivered=False,
            executor=name,
            account=logged.login,
            model=found.title,
            session_id=None if crossing else record.session_id,
        )
        # In its automatic mode, as every delegation starts.
        auto = _may_auto(found, name)
        spec = ExecutorSpec(
            Path(record.folder),
            resume=None if crossing else record.session_id,
            auto=auto,
            effort=await default_executor_effort(found, None),
            resume_from=resume_from,
        )
        answers = auto and assistant.auto_mode(parent.id)
        run = self._executor_run(found, name, spec, parent, resumed, atlas_answers=answers)
        child = _Child(resumed, parent, run=run, prompt=prompt, last_activity=now)
        return await self._launch(parent, delegation_id, [child])

    async def _accounts(
        self, name: str, folders: Sequence[Path], chosen: str | None = None
    ) -> list[tuple[Executor, str, Account]] | str:
        """Each task's account: the `chosen` one (the main one as `""`), else its folder's (step
        16h); the executor on it, its name and who it is logged in as; or what the model is told
        when one cannot run."""
        assistant = self._assistant
        accounts: list[tuple[Executor, str, Account]] = []
        for folder in folders:
            account = (
                chosen
                if chosen is not None
                else await asyncio.to_thread(assistant.workbench.folder_account, name, folder)
            )
            named = account_name(name, account)
            found = assistant.executor(named)
            if found is None:
                return f"[Atlas: {named} is not installed on this computer. Nothing was delegated.]"
            logged = await _logged_in(found)
            if logged is None:
                return NOT_LOGGED_IN.format(title=found.title)
            accounts.append((found, named, logged))
        return accounts

    def _executor_run(
        self,
        executor: Executor,
        name: str,
        spec: ExecutorSpec,
        parent: "OpenConversation",
        record: Subagent,
        *,
        atlas_answers: bool = False,
    ) -> ExecutorRun:
        """A delegated task's instance, Atlas's, on the workbench while it works; what it asks
        is answered by the user on its card, or, with `atlas_answers`, for them with the parent's
        guardian and model."""
        assistant = self._assistant
        workbench = assistant.workbench
        run = ExecutorRun(
            instance_id=workbench.new_id(),
            executor=executor,
            name=name,
            spec=spec,
            transcript=assistant.conversations.open(record.conversation_id),
            owner=Owner.ATLAS,
            answering=parent,
            desk=assistant.desk,
            notify=workbench.notify,
            account=record.account,
            parent_id=parent.id,
            subagent_id=record.id,
            record=assistant.instance_store,
            clock=assistant.clock,
        )
        run.atlas_answers = atlas_answers
        return run

    async def switch_account(self, email: str, executor: str | None = None) -> str:
        """Log an account of an executor in, by the harness's own login on the user's browser
        (decided with the user on 12/10/2026), only when the user asks: `executor` is a `target
        name` (its main account when it names none); with none, the one executor installed that
        switches accounts. `email` empty: whichever account the user signs in to. Never while one
        of its tasks works, which would change accounts mid-task; and never for an email another
        of its accounts is logged in as already, which a delegation reaches by its name."""
        assistant = self._assistant
        if executor:
            resolved = self._resolve(executor)
            if isinstance(resolved, str):
                return resolved
            names = [account_name(resolved[0], resolved[1] or MAIN_ACCOUNT)]
        else:
            names = assistant.executors()
        named = [
            (name, found)
            for name in names
            if (found := assistant.executor(name)) is not None and found.capabilities().accounts
        ]
        if not named:
            return "[Atlas: No executor that switches accounts is installed on this computer.]"
        if len(named) > 1:
            listed = ", ".join(name for name, _found in named)
            return f"[Atlas: Several executors switch accounts ({listed}): name one in executor.]"
        name, found = named[0]
        title = found.title
        already = await self._logged_elsewhere(name, email)
        if already is not None:
            return (
                f"[Atlas: {email} is logged in already on {already}: delegate with "
                f"executor='{already}' (or resume with it) instead of logging in again.]"
            )
        # Its own tasks, and the user's sessions on the same login, would change accounts
        # mid-task.
        working = any(run.name == name and run.working for run in assistant.workbench.runs())
        if working or any(child.record.executor == name for child in self._live.values()):
            return (
                f"[Atlas: {title} is still working on a task, which would change accounts "
                "mid-task. Wait for it or stop it, then switch.]"
            )
        try:
            logged = await found.log_in(email)
        except ExecutorError as error:
            return f"[Atlas: The login did not finish: {error}]"
        except Exception as error:  # a plugin's own code: its failure is told, not raised
            _logger.exception("An executor's login failed.")
            return f"[Atlas: The login did not finish: {type(error).__name__}: {error}]"
        wanted = email.strip().lower()
        if not logged.logged_in or (wanted and logged.login.lower() != wanted):
            return (
                f"[Atlas: {title} is logged in as {logged.login or 'nobody'}, not "
                f"{email or 'anyone'}. Ask the user to try again.]"
            )
        _logger.info("An executor switched accounts.", extra={"executor": name})
        return json.dumps(
            {
                "account": logged.login,
                "plan": logged.plan,
                "note": (
                    f"{title} now runs on this account. Resume a stopped task with "
                    "action='resume' and its subagent_id: its own history goes on."
                ),
            },
            ensure_ascii=False,
        )

    async def _logged_elsewhere(self, name: str, email: str) -> str | None:
        """The target name of another account of `name`'s executor logged in as `email`, when
        one is."""
        base = split_account(name)[0]
        if not email.strip():
            return None
        try:
            listed = await self._assistant.workbench.accounts(base)
        except ExecutorError:
            return None
        for view in listed.accounts:
            if view.name != name and view.logged_in and view.login.lower() == email.strip().lower():
                return target_name(base, view.nickname)
        return None

    async def accounts(self, executor: str | None = None) -> str:
        """Every account of the outside executors installed (or of `executor`'s alone), as
        executor_accounts tells the model: in the order they take over in, each by its `target
        name`, who it is logged in as, its plan's usage and whether it is at its limit now or
        bills past it, and the folders that start on it; read with no model call."""
        assistant = self._assistant
        names = assistant.executors()
        if executor and executor.strip():
            base = split_account(executor.strip())[0]
            if base not in names:
                known = ", ".join(names) or "none"
                return (
                    f"[Atlas: There is no executor {base!r} on this computer. Installed: {known}.]"
                )
            names = [base]
        listed = [await self._executor_accounts(name) for name in names]
        return json.dumps({"executors": listed, "note": ACCOUNTS_NOTE}, ensure_ascii=False)

    async def _executor_accounts(self, name: str) -> dict[str, object]:
        """One executor's accounts, as `accounts` lists them."""
        assistant = self._assistant
        workbench = assistant.workbench
        try:
            accounts = await workbench.accounts(name)
        except ExecutorError as error:
            return {"executor": name, "error": str(error)}
        alone = len(accounts.accounts) == 1

        async def row(view: AccountView) -> dict[str, object]:
            entry: dict[str, object] = {
                "name": name if alone else target_name(name, view.nickname),
                "login": view.login,
                "logged_in": view.logged_in,
            }
            if view.logged_in:
                try:
                    usage = await workbench.usage(view.name)
                except ExecutorError as error:
                    entry["usage_error"] = str(error)
                else:
                    entry |= _usage_entry(usage)
            entry["at_limit"] = workbench.limited(view.name)
            return entry

        found = assistant.executor(name)
        entry: dict[str, object] = {
            "executor": name,
            "title": name if found is None else found.title,
            "accounts": list(await asyncio.gather(*(row(view) for view in accounts.accounts))),
        }
        if accounts.can_add:
            entry["hand_over"] = {"on": accounts.failover, "compacts_first": accounts.compact}
        return entry

    async def _launch(
        self,
        parent: "OpenConversation",
        delegation_id: str,
        children: list[_Child],
        shared: Sequence[str] = (),
    ) -> str:
        """Start a batch's children: in the background, or waited for (a routine, a full pool).
        `shared`: the folders another agent works in, which the answer warns of."""
        assistant = self._assistant
        assistant.subagent_store.add([child.record for child in children])
        waits = None
        if parent.role is ConversationRole.ROUTINE:
            waits = "unattended"
        elif self._background >= MAX_BACKGROUND:
            waits = "at_capacity"
        for child in children:
            self._live[child.record.id] = child
            if child.conversation is not None:
                # Its turn starts here, so a steer sent at once finds it running.
                child.conversation.send(child.record.goal)
                child.task = asyncio.create_task(self._run(child))
            else:
                child.task = asyncio.create_task(self._run_executor(child))
            self._changed(child)
        _logger.info(
            "A conversation delegated.",
            extra={
                "conversation": parent.id,
                "delegation": delegation_id,
                "tasks": len(children),
                "waits": waits,
            },
        )
        if waits is not None:
            running = [child.task for child in children if child.task is not None]
            try:
                await asyncio.wait(running)
            except asyncio.CancelledError:
                # The parent's turn was stopped: its children stop with what they had.
                for child in children:
                    await self._halt(child)
                await asyncio.gather(*running, return_exceptions=True)
                raise
            records = self._trimmed(parent, [child.record for child in children])
            assistant.subagent_store.delivered(delegation_id)
            waited: dict[str, object] = {
                "results": [_entry(record) for record in records],
                "note": WAITED_NOTES[waits],
            }
            return json.dumps(_warned(waited, shared), ensure_ascii=False)
        self._background += 1
        batch = asyncio.create_task(self._deliver(parent.id, children))
        self._batches.add(batch)
        batch.add_done_callback(self._batches.discard)
        sent = dispatched([child.record for child in children])
        return json.dumps(_warned(sent, shared), ensure_ascii=False)

    async def _deliver(self, parent_id: str, children: Sequence[_Child]) -> None:
        """When the batch's last child ends, its one notice to the parent."""
        try:
            await asyncio.gather(
                *(child.task for child in children if child.task is not None),
                return_exceptions=True,
            )
        finally:
            self._background -= 1
        if not self._closing:
            self._notify(parent_id, [child.record for child in children])

    def _notify(self, parent_id: str, records: Sequence[Subagent]) -> None:
        """Bring a batch's summaries to its parent as one notice, and write it delivered."""
        assistant = self._assistant
        delegation_id = records[0].delegation_id
        try:
            parent = assistant.conversation(parent_id)
        except ConversationNotFoundError:
            _logger.warning(
                "A delegation's parent is gone; its results were kept and not delivered.",
                extra={"conversation": parent_id, "delegation": delegation_id},
            )
            assistant.subagent_store.delivered(delegation_id)
            return
        now = assistant.clock()
        zone = time_zone(assistant.settings.time.zone)
        parent.notice(batch_notice(self._trimmed(parent, records), now, zone))
        assistant.subagent_store.delivered(delegation_id)

    def _trimmed(self, parent: "OpenConversation", records: Sequence[Subagent]) -> list[Subagent]:
        """The summaries cut to what the parent's context can take, each whole one in the spill
        folder."""
        window, last_input = parent.context
        cap = summary_cap(window, last_input, sum(1 for record in records if record.summary))
        kept: list[Subagent] = []
        for record in records:
            if len(record.summary) > cap:
                saved = self._assistant.spill.save(f"subagent-{record.id}", record.summary)
                cut = trimmed_summary(record.summary, cap, None if saved is None else str(saved))
                record = dataclasses.replace(record, summary=cut)
            kept.append(record)
        return kept

    # -- one child ------------------------------------------------------------------------------

    async def _run(self, child: _Child) -> None:
        """Run a child's turn under the watchdog, and write how it ended."""
        assistant = self._assistant
        conversation = child.conversation
        if conversation is None:  # pragma: no cover - an executor runs in _run_executor
            return
        stalled: float | None = None
        try:
            while conversation.running:
                await asyncio.sleep(self.look)
                idle = (assistant.clock() - conversation.last_activity).total_seconds()
                limit = self.in_tool if child.in_tool > 0 else self.idle
                if conversation.running and idle > limit:
                    stalled = idle
                    _logger.warning(
                        "A subagent stopped making progress and was stopped.",
                        extra={"subagent": child.record.id, "idle": round(idle)},
                    )
                    await conversation.interrupt()
            await conversation.wait()
        finally:
            await conversation.close()
            assistant.forget(conversation.id)
            child.record = self._ended(child, stalled)
            assistant.subagent_store.finish(child.record)
            self._live.pop(child.record.id, None)
            self._changed(child)

    async def _run_executor(self, child: _Child) -> None:
        """Run an outside executor on its task under the watchdog, which leaves alone an
        executor waiting for the user's answer, and write how it ended."""
        assistant = self._assistant
        run = child.run
        if run is None:  # pragma: no cover - only an executor runs here
            return
        first: ExecutorRun = run
        stalled: list[float] = []

        def heard(event: ExecutorEvent) -> None:
            child.last_activity = child.parent.last_activity = assistant.clock()
            if isinstance(event, Called):
                child.last_tool = event.tool
                child.tools += 1
                child.in_tool += 1
                self._changed(child)
            elif isinstance(event, Returned):
                child.in_tool = max(0, child.in_tool - 1)

        async def watch() -> None:
            while True:
                await asyncio.sleep(self.look)
                # The instance on the next account, once the first one reached its limit.
                watched = child.run or first
                if watched.waiting:
                    child.last_activity = assistant.clock()
                    continue
                idle = (
                    assistant.clock() - (child.last_activity or assistant.clock())
                ).total_seconds()
                if idle > (self.in_tool if child.in_tool > 0 else self.idle):
                    stalled.append(idle)
                    _logger.warning(
                        "An outside executor stopped making progress and was stopped.",
                        extra={"subagent": child.record.id, "idle": round(idle)},
                    )
                    await watched.stop()
                    return

        watchdog = asyncio.create_task(watch())
        finished: Finished | None = None
        error: str | None = None
        try:
            assistant.workbench.adopt(run)
            finished = await run.run(child.prompt, heard)
            while run.limited is not None and not (child.stopping or self._closing or stalled):
                handed = await self._hand_over(child, heard)
                if handed is None:
                    break
                run, finished = handed
        except ExecutorError as failure:
            error = str(failure)
        except Exception as failure:  # a plugin's own code: its failure ends the task, told
            _logger.exception("An executor failed.", extra={"subagent": child.record.id})
            error = f"{type(failure).__name__}: {failure}"
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
            await run.stop()
            assistant.workbench.drop(run)
            child.record = self._executor_ended(child, finished, error, stalled)
            if child.workspace:
                child.record = await self._workspace_said(child)
            assistant.subagent_store.finish(child.record)
            self._live.pop(child.record.id, None)
            self._changed(child)

    async def _next(
        self, name: str, conversation_id: str, limit: LimitReached | None
    ) -> NextStop | None:
        """Where a delegated task goes on after `name` reached its plan's limit, as the accounts
        plugin says (its route, else the next of its executor's accounts); `None` when none can,
        or no plugin brings other accounts."""
        workbench = self._assistant.workbench
        workbench.mark_limited(name, limit)
        found = await workbench.next_stop(name, conversation_id)
        return found if isinstance(found, NextStop) else None

    async def _hand_over(
        self, child: _Child, heard: Callable[[ExecutorEvent], None]
    ) -> tuple[ExecutorRun, Finished | None] | None:
        """The task's account reached its plan's limit: go on from where it stopped on the next
        destination of its route, else the next account in the user's order (asked for by the
        user on 21/10/2026); `None` when none can. An account of the same executor goes on with
        its session in a copy, compacted first when the user asked for it; another executor
        starts a session of its own whose first prompt is the handoff packet."""
        assistant = self._assistant
        workbench = assistant.workbench
        old = child.run
        if old is None or old.session_id is None:
            return None
        stop = await self._next(old.name, child.record.conversation_id, old.limited)
        if stop is None:
            return None
        name, compact = stop.executor, stop.compact
        found = assistant.executor(name)
        logged = None if found is None else await _logged_in(found)
        if found is None or logged is None:
            return None
        crossing = split_account(name)[0] != split_account(old.name)[0]
        # The model and the effort it ran on go on with it (decided with the user on 25/10/2026).
        model, effort, note = await workbench.carried(old, name, stop.model, stop.effort)
        packet = ""
        if crossing:
            source = await asyncio.to_thread(workbench.label, old.name)
            packet = await workbench.handoff(
                old.name,
                old.session_id,
                old.spec.folder,
                old.history,
                name,
                limit_reason(source, old.limited, "the task's route"),
                instances=(old.id,),
            )
        await old.stop()
        workbench.drop(old)
        child.record = dataclasses.replace(
            child.record, executor=name, account=logged.login, model=found.title
        )
        if crossing:
            spec = ExecutorSpec(
                old.spec.folder,
                auto=old.auto and _may_auto(found, name),
                model=model,
                effort=effort
                if effort is not None
                else await default_executor_effort(found, model),
            )
        else:
            spec = ExecutorSpec(
                old.spec.folder,
                resume=old.session_id,
                auto=old.auto,
                model=model,
                effort=effort,
                resume_from=old.executor.account_folder(),
            )
        run = self._executor_run(
            found, name, spec, child.parent, child.record, atlas_answers=old.atlas_answers
        )
        child.run, child.last_activity = run, assistant.clock()
        workbench.adopt(run)
        self._changed(child)
        if note:
            workbench.notify(InstanceActivity(run.id, "note", note))
        if crossing:
            destination = await asyncio.to_thread(workbench.label, name)
            workbench.notify(InstanceActivity(run.id, "route", destination))
            _logger.info("A delegated task went on on another executor by its route.")
            return run, await run.run(packet, heard)
        workbench.notify(InstanceActivity(run.id, "account", logged.login or name))
        _logger.info("A delegated task went on on another account after a plan limit.")
        # It compacts first where its harness has `/compact`; one that fails leaves it as it was.
        if not (compact and "compact" in old.commands):
            return run, await run.run(CONTINUE, heard)
        try:
            await run.run("/compact", heard)
        except ExecutorError as error:
            if run.instance is None:
                raise
            workbench.notify(InstanceActivity(run.id, "note", not_compacted(error)))
        return run, await run.turn(CONTINUE)

    async def _workspace_said(self, child: _Child) -> Subagent:
        """A task's result, with the workspace it worked in: its branch, and what it changed."""
        record = child.record
        maker = self._assistant.task_workspaces
        changes = ""
        if maker is not None:
            try:
                changes = await maker.changes(Path(record.folder))
            except Exception:  # a git that fails: the result goes without its changes, logged
                _logger.exception("A workspace's changes could not be read.")
        said = f"[Atlas: It worked in the workspace {child.workspace}, at {record.folder}"
        said += f"; changed: {changes}.]" if changes else ".]"
        return dataclasses.replace(record, summary=f"{record.summary}\n\n{said}".strip())

    def _executor_ended(
        self,
        child: _Child,
        finished: Finished | None,
        error: str | None,
        stalled: list[float],
    ) -> Subagent:
        """How an outside executor ended: its result, or what it last said and why it stopped."""
        run = child.run
        last_words = _last_words(run.history if run is not None else [])
        status, summary = SubagentStatus.FAILED, last_words
        if stalled:
            status = SubagentStatus.STALLED
            error = (
                f"It stopped making progress — no activity for {int(stalled[0])}s; it was stopped."
            )
        elif child.stopping:
            status, error = SubagentStatus.INTERRUPTED, "Stopped before it finished."
        elif self._closing:
            status, error = SubagentStatus.INTERRUPTED, "Atlas stopped while it ran."
        elif run is not None and run.limited is not None:
            error = self._limit_said(child.record.id, child.record.account, run.limited, run.title)
        elif finished is None:
            failure = run.failure if run is not None else ""
            error = error or f"It ended without a result. {failure}".strip()
        elif finished.failed:
            error = finished.text
        else:
            status, summary = SubagentStatus.COMPLETED, finished.text
        return dataclasses.replace(
            child.record,
            status=status,
            ended_at=self._assistant.clock(),
            summary=summary,
            error=error,
            model_calls=finished.turns if finished is not None else 0,
            cost_usd=finished.cost_usd if finished is not None else None,
            session_id=run.session_id if run is not None else None,
        )

    def _ended(self, child: _Child, stalled: float | None) -> Subagent:
        """How a child ended, and its status: its answer, or what it last said."""
        conversation = child.conversation
        if conversation is None:  # pragma: no cover - an executor ends in _executor_ended
            return child.record
        ended = conversation.last_ended
        last_words = _last_words(conversation.history)
        missed = [_unwrapped(text) for text in conversation.unsent() if is_steer(text)]
        status, summary, error, truncated = SubagentStatus.FAILED, last_words, None, False
        if stalled is not None:
            status = SubagentStatus.STALLED
            error = (
                f"Subagent stopped making progress — no activity for {int(stalled)}s; it was "
                "stopped."
            )
        elif ended is None:
            if child.stopping:
                status, error = SubagentStatus.INTERRUPTED, "Stopped before it answered."
            elif self._closing:
                status, error = SubagentStatus.INTERRUPTED, "Atlas stopped while it ran."
            else:
                error = "The subagent never answered."
        elif ended.outcome is TurnOutcome.ANSWERED:
            status, summary = SubagentStatus.COMPLETED, ended.text
        elif ended.outcome is TurnOutcome.LIMITED:
            status, truncated = SubagentStatus.COMPLETED, True
            summary = last_words or ended.text
        else:
            error = ended.text
        return dataclasses.replace(
            child.record,
            status=status,
            ended_at=self._assistant.clock(),
            summary=missed_steer(summary, missed),
            error=error,
            model_calls=ended.model_calls if ended is not None else 0,
            cost_usd=ended.cost_usd if ended is not None else Decimal(0),
            truncated=truncated,
        )

    def _listener(self, conversation_id: str, event: "ConversationEvent") -> None:
        """A child's events: what its dock shows, and activity for its parent."""
        child = next(
            (c for c in self._live.values() if c.record.conversation_id == conversation_id), None
        )
        if child is None:
            return
        child.parent.last_activity = self._assistant.clock()
        if isinstance(event, ToolStarted):
            child.last_tool = event.name
            child.tools += 1
            child.in_tool += 1
            self._changed(child)
        elif isinstance(event, ToolFinished):
            child.in_tool = max(0, child.in_tool - 1)

    def _changed(self, child: _Child) -> None:
        self._assistant.notify(child.record.parent_id, self._view(child))

    def _view(self, child: _Child) -> SubagentChanged:
        record = child.record
        return SubagentChanged(
            subagent_id=record.id,
            delegation_id=record.delegation_id,
            goal=record.goal,
            model=record.model,
            status=record.status.value,
            started_at=format_utc(record.started_at),
            last_tool=child.last_tool,
            tools=child.tools,
            ended_at=None if record.ended_at is None else format_utc(record.ended_at),
            executor=record.executor,
            decides=_decides(child.run).value,
            waiting=child.run.waiting if child.run is not None else 0,
            conversation_id=record.conversation_id,
            account=record.account,
            folder=record.folder,
            session_id=record.session_id
            or (child.run.session_id or "" if child.run is not None else ""),
        )

    def session(self, parent_id: str, subagent_id: str) -> tuple[str, str, str] | None:
        """The executor, folder and session of an outside executor's task of this conversation,
        running or ended, which its `delegate` line opens in the executors tab (step 16f);
        `None` when it is no such task, or its session is not known yet."""
        live = self._live.get(subagent_id)
        store = self._assistant.subagent_store
        record = live.record if live is not None else store.get(subagent_id)
        if record is None or record.parent_id != parent_id or record.executor is None:
            return None
        session = record.session_id or (
            live.run.session_id if live is not None and live.run is not None else None
        )
        return (record.executor, record.folder, session) if session else None

    # -- control --------------------------------------------------------------------------------

    def listing(self, parent_id: str) -> str:
        """The conversation's live children, and the workbench's instances with whose each is
        (step 16c)."""
        children = [c.record for c in self._live.values() if c.record.parent_id == parent_id]
        found = listed(children, self._assistant.clock())
        runs = self._assistant.workbench.runs()
        if runs:
            found["workbench"] = [_instance_entry(run, parent_id) for run in runs]
            found["workbench_note"] = WORKBENCH_NOTE
        return json.dumps(found, ensure_ascii=False)

    async def steer(self, parent_id: str, subagent_id: str, message: str) -> str:
        """Queue a course correction into a running child, or into an
        executor instance on the workbench."""
        child = self._owned(parent_id, subagent_id)
        if child is None:
            return await self._steer_instance(parent_id, subagent_id, message)
        if child.conversation is None:
            run = child.run
            if run is None or not run.working or not run.can_steer:
                return (
                    f"[Atlas: Subagent '{subagent_id}' takes no course correction now. Stop it "
                    "and delegate again with what you know now.]"
                )
            try:
                await run.prompt(message)
            except ExecutorError as error:
                return f"[Atlas: {error}]"
            return json.dumps(
                {"action": "steer", "subagent_id": subagent_id, "status": "delivered",
                 "note": f"{run.title} took it into the turn it is running."},
                ensure_ascii=False,
            )  # fmt: skip
        if child.stopping or not child.conversation.running:
            return (
                f"[Atlas: Subagent '{subagent_id}' is finishing or has finished, so it takes no "
                "more steering. Its result comes back in an ordinary completion message; if more "
                "is left to do, delegate a follow-up task.]"
            )
        child.conversation.send(steer_message(message))
        return json.dumps(
            {
                "action": "steer",
                "subagent_id": subagent_id,
                "status": "queued",
                "note": (
                    "Queued. The subagent reads it once its current tool calls are over; a tool "
                    "call under way is never interrupted. Should the child end with no point left "
                    "to hand it over, its completion entry says the text was missed."
                ),
            },
            ensure_ascii=False,
        )

    async def stop(self, parent_id: str, subagent_id: str) -> str:
        """End a running child early; what it had still comes back. An
        instance of the user's on the workbench stops only with their yes."""
        child = self._owned(parent_id, subagent_id)
        if child is None:
            return await self._stop_instance(parent_id, subagent_id)
        await self._halt(child)
        return json.dumps(
            {
                "action": "stop",
                "subagent_id": subagent_id,
                "status": "interrupt_requested",
                "note": (
                    "The subagent stopped. What it had done still comes back into the "
                    "conversation in a completion message, so do not wait or poll for it."
                ),
            },
            ensure_ascii=False,
        )

    # -- the workbench's instances (step 16c) -----------------------------------------------

    def _instance(self, parent_id: str, instance_id: str) -> "ExecutorRun | None":
        """An instance on the workbench this conversation may reach: the user's, or its own."""
        found = next((r for r in self._assistant.workbench.runs() if r.id == instance_id), None)
        if found is None or (found.owner is Owner.ATLAS and found.parent_id != parent_id):
            return None
        return found

    async def _yes(self, parent_id: str, run: "ExecutorRun", what: str) -> str | None:
        """For the user's own instance, their yes (decided with the user on 14/10/2026: Atlas
        steers or stops it only when they ask); `None` when it may go on."""
        if run.owner is not Owner.USER:
            return None
        parent = self._assistant.conversation(parent_id)
        return await parent.ask_first(
            "delegate",
            f"{what} — {run.title} in {run.spec.folder}",
            "it is the user's own instance on the workbench",
        )

    async def _steer_instance(self, parent_id: str, instance_id: str, message: str) -> str:
        run = self._instance(parent_id, instance_id)
        if run is None or not run.live:
            return f"[Atlas: {not_live(instance_id)}]"
        refused = await self._yes(parent_id, run, f"send it: {message}")
        if refused is not None:
            return refused
        try:
            steered = await run.prompt(message)
        except ExecutorError as error:
            return f"[Atlas: {error}]"
        note = "took it into the turn it is running" if steered else "started a turn on it"
        return json.dumps(
            {"action": "steer", "instance_id": instance_id, "status": "delivered",
             "note": f"{run.title} {note}. Its answer stays on its card; read it with "
                     "delegation_search when you need it."},
            ensure_ascii=False,
        )  # fmt: skip

    async def _stop_instance(self, parent_id: str, instance_id: str) -> str:
        run = self._instance(parent_id, instance_id)
        if run is None or not run.live:
            return f"[Atlas: {not_live(instance_id)}]"
        refused = await self._yes(parent_id, run, "stop it")
        if refused is not None:
            return refused
        await self._assistant.workbench.stop(run.id)
        return json.dumps(
            {"action": "stop", "instance_id": instance_id, "status": "stopped"},
            ensure_ascii=False,
        )

    async def stop_all(self, parent_id: str) -> None:
        """Stop every running child of a conversation."""
        for child in [c for c in self._live.values() if c.record.parent_id == parent_id]:
            await self._halt(child)

    async def _halt(self, child: _Child) -> None:
        """Stop one child: Atlas's subagent at its turn, an outside executor for good, with what
        it asked still open withdrawn."""
        child.stopping = True
        if child.conversation is not None:
            await child.conversation.interrupt()
        elif child.run is not None:
            await child.run.stop()

    def _limit_said(self, subagent_id: str, email: str, limit: LimitReached, title: str) -> str:
        """Why an executor stopped at its plan's limit, and what may follow."""
        when = ""
        if limit.resets_at is not None:
            zone = time_zone(self._assistant.settings.time.zone)
            moment = datetime.fromtimestamp(limit.resets_at, tz=UTC).astimezone(zone)
            when = f" It resets at {moment:%H:%M} ({moment:%d/%m})."
        return (
            f"The account {email or f'{title} is logged in as'} reached its {title} plan "
            f"limit.{when} Nothing else could go on with it now: its route, or its executor's "
            "accounts in the user's order, were each tried, or the user turned that off. Tell "
            "the user; when an account is free again, or they ask, go on with "
            f"delegate(action='resume', subagent_id='{subagent_id}'), on another account or "
            "executor with `executor`."
        )

    def set_decides(self, parent_id: str, subagent_id: str, decides: Decides) -> bool:
        """Who answers what an outside executor of this conversation asks from now on; `False`
        when there is no such executor running."""
        child = self._owned(parent_id, subagent_id)
        if child is None or child.run is None:
            return False
        child.run.set_atlas_answers(decides is Decides.ATLAS)
        self._changed(child)
        return True

    def _owned(self, parent_id: str, subagent_id: str) -> _Child | None:
        """A live child of this conversation; another's is not found."""
        child = self._live.get(subagent_id)
        return child if child is not None and child.record.parent_id == parent_id else None

    # -- the process ----------------------------------------------------------------------------

    def recover(self) -> None:
        """What an earlier process left: its running children marked unknown, and every batch
        that never reached its parent delivered, for 48 hours. Needs a
        running event loop, since a notice starts a turn."""
        assistant = self._assistant
        store = assistant.subagent_store
        now = assistant.clock()
        abandoned = store.abandon_running(now)
        if abandoned:
            _logger.warning(
                "Subagents an earlier Atlas left running are marked unknown.",
                extra={"count": abandoned},
            )
        for batch in store.undelivered(now - timedelta(hours=RECOVERY_HOURS)):
            parent_id = batch[0].parent_id
            try:
                source = assistant.conversations.info(parent_id).source
            except ConversationNotFoundError:
                source = None
            if source != APP:
                # A routine's run waited for its children, and nobody is there to hear more.
                store.delivered(batch[0].delegation_id)
                continue
            self._notify(parent_id, batch)

    async def settled(self) -> None:
        """Until no child runs and every batch was delivered."""
        while self._live or self._batches:
            tasks = [child.task for child in self._live.values() if child.task is not None]
            await asyncio.gather(*tasks, *self._batches, return_exceptions=True)

    async def close(self) -> None:
        """Stop every child; their batches are delivered by the next process."""
        self._closing = True
        tasks = [child.task for child in self._live.values() if child.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, *self._batches, return_exceptions=True)


def agent_tools(agent: AgentDefinition) -> frozenset[str] | None:
    """The tools of Atlas's own a plugin's agent names, Claude Code's names read as Atlas's
    (`Bash` is `run_command`, `Glob` and `Grep` are `find_files`); `None` when it names none."""
    if agent.tools is None:
        return None
    atlas_names = {claude: atlas for atlas, claude in CLAUDE_CODE_NAMES.items()} | {
        "Glob": "find_files"
    }
    return frozenset(atlas_names.get(name, name) for name in agent.tools)


def agent_model(agent: AgentDefinition) -> ModelChoice | None:
    """A plugin's agent's own model, `provider/model`; `None`, its parent's."""
    if agent.model is None:
        return None
    provider, _, model = agent.model.partition("/")
    return ModelChoice(provider, model, None)


def target_name(executor: str, nickname: str) -> str:
    """An executor on one of its accounts as the model names it (`claude-code@work`), the main
    account as `main`."""
    return f"{executor}@{nickname or MAIN_TARGET}"


def _nothing_delegated(said: str) -> str:
    """What the model is told, ending in that nothing was delegated."""
    return f"{said.removesuffix(']')} Nothing was delegated.]"


def _may_auto(executor: Executor, name: str) -> bool:
    """Whether a delegation to `name` runs in its own automatic mode: one Atlas may turn on,
    on any of its accounts (`claude-code@1a2b3c4d`)."""
    return split_account(name)[0] in AUTO_EXECUTORS and executor.capabilities().auto


class _Delegator:
    """One conversation's delegate tool, reaching only its own children."""

    def __init__(self, subagents: Subagents, parent: "OpenConversation") -> None:
        self._subagents = subagents
        self._parent = parent

    async def spawn(
        self,
        tasks: tuple[Task, ...],
        executor: str | None = None,
        *,
        auto: bool = False,
        route: Sequence[str] = (),
    ) -> str:
        return await self._subagents.spawn(self._parent, tasks, executor, auto=auto, route=route)

    def executors(self) -> list[str]:
        return self._subagents.executors()

    async def resume(
        self,
        subagent_id: str,
        message: str,
        executor: str | None = None,
        route: Sequence[str] = (),
    ) -> str:
        return await self._subagents.resume(self._parent, subagent_id, message, executor, route)

    async def account(self, email: str, executor: str | None = None) -> str:
        return await self._subagents.switch_account(email, executor)

    async def accounts(self, executor: str | None = None) -> str:
        return await self._subagents.accounts(executor)

    def list(self) -> str:
        return self._subagents.listing(self._parent.id)

    async def steer(self, subagent_id: str, message: str) -> str:
        return await self._subagents.steer(self._parent.id, subagent_id, message)

    async def stop(self, subagent_id: str) -> str:
        return await self._subagents.stop(self._parent.id, subagent_id)


#: What executor_accounts says with the accounts.
ACCOUNTS_NOTE = (
    "Each executor's accounts are in the order they take over in at a plan's limit (hand_over). "
    "Delegate to one with executor=<its name>; a bare executor runs on the folder's account "
    "(folders), else the main one. A route may mix accounts of several executors. An account "
    "with extra_usage true bills past its limits instead of stopping."
)


def _usage_entry(usage: ExecutorUsage | None) -> dict[str, object]:
    """An account's plan usage, as executor_accounts lists it."""
    if usage is None:
        return {}
    entry: dict[str, object] = {}
    if usage.plan:
        entry["plan"] = usage.plan
    if usage.unavailable:
        entry["no_plan_limits"] = usage.unavailable
    limits: list[dict[str, object]] = []
    for window in usage.windows:
        limit: dict[str, object] = {"limit": window.title, "used_percent": round(window.used)}
        if window.resets_at:
            limit["resets_at"] = window.resets_at
        if window.model:
            limit["model"] = window.model
        limits.append(limit)
    if limits:
        entry["limits"] = limits
    if usage.extra_usage is not None:
        entry["extra_usage"] = usage.extra_usage
    return entry


#: What the list says of the workbench's instances.
WORKBENCH_NOTE = (
    "The workbench's instances: 'yours' you delegated; 'the user's' they started on their cards. "
    "Read what any of them did with delegation_search. Steer or stop the user's only when the "
    "user asks you to (pass its instance_id as subagent_id): they approve it every time."
)


def _instance_entry(run: ExecutorRun, parent_id: str) -> dict[str, object]:
    """An instance on the workbench, as the list shows it."""
    whose = (
        "the user's"
        if run.owner is Owner.USER
        else ("yours" if run.parent_id == parent_id else "another conversation's")
    )
    entry: dict[str, object] = {
        "instance_id": run.id,
        "executor": run.name,
        "folder": str(run.spec.folder),
        "account": run.account,
        "state": run.state.value,
        "whose": whose,
    }
    if run.waiting:
        entry["waiting_for_an_answer"] = run.waiting
    if run.last_tool:
        entry["last_tool"] = run.last_tool
    return entry


def _decides(run: ExecutorRun | None) -> Decides:
    """Who answers what a child asks: step 13's switch, the workbench's "Atlas answers"."""
    return Decides.ATLAS if run is not None and run.atlas_answers else Decides.USER


async def _logged_in(executor: Executor) -> Account | None:
    """Who the executor is logged in as, or `None` when nobody is."""
    try:
        found = await executor.detect()
    except Exception:  # a plugin's own code: a failed look is no login
        _logger.exception("An executor could not say who it is logged in as.")
        return None
    account = found.account
    return account if account is not None and account.logged_in else None


def _last_words(history: Sequence[Message]) -> str:
    """What a child last said: its last reply with text."""
    for message in reversed(history):
        if isinstance(message, AssistantMessage) and message.text.strip():
            return message.text.strip()
    return ""


def _unwrapped(steer: str) -> str:
    return steer.removeprefix(STEER_OPEN).removesuffix(STEER_CLOSE).strip()


def _warned(answer: dict[str, object], shared: Sequence[str]) -> dict[str, object]:
    """A spawn's answer, warning of the folders another agent works in."""
    if shared:
        answer["warning"] = (
            f"Another agent works in {', '.join(shared)} now: the two may change the same "
            'files. workspace: "new" gives a task a copy of its own.'
        )
    return answer


def _entry(record: Subagent) -> dict[str, object]:
    """A child's result in a call that waited."""
    seconds = int(((record.ended_at or record.started_at) - record.started_at).total_seconds())
    entry: dict[str, object] = {
        "task_index": record.index,
        "goal": record.goal,
        "status": record.status.value,
        "summary": record.summary,
        "api_calls": record.model_calls,
        "duration_seconds": seconds,
    }
    if record.error:
        entry["error"] = record.error
    if record.truncated:
        entry["truncated"] = True
    return entry
