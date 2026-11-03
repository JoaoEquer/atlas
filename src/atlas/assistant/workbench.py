"""The workbench for this Atlas (step 16c): every executor instance, started by the user on its
card or by Atlas through `delegate`, the same panel in every conversation.

Decided with the user on 14/10/2026 (ROADMAP.md, Bancada de harnesses): step 13 made Atlas the
harness's only way in, its prompt always Atlas's and its questions answered only through Atlas.
The workbench lets the user work with the harness directly, Atlas passing on what another does
better:

- **One card per instance** (harness, account, folder), not per harness: the same harness may run
  twice, in two folders. The card's prompt starts a turn, or steers the one running; what the
  harness answers stays on the card, never in a conversation.
- **Two switches per instance, apart:** the harness's own automatic mode (Claude Code's `auto`),
  which asks nothing and so passes over Atlas's guardian; and "Atlas answers" (off by default, step
  13's "Atlas decides" as a mode): the guardian for permissions, a model for questions, and what
  either is unsure of still reaches the user.
- **The main account is the harness's, one per installation:** switching it logs the harness in
  again on the user's browser, never while one of its instances on it works, and the instance goes
  on with the same session in the same folder (decided with the user on 12/10/2026).
- **Other accounts run at once** (step 16h, decided with the user on 18/10/2026 and redone on
  19/10/2026): each is the harness's own folder for it (Claude Code's `~/.claude-<nickname>`),
  with its own login, settings and sessions, under a nickname the user gives; Atlas keeps no
  credential. On the screens each is an executor of its own, `claude-code@<id>`, which its plugin
  makes on that folder (`Executor.for_account`). A folder may name the account its new sessions
  start on; one that names none starts on the main account. A session goes on from one account to
  another in a copy (`use_account`): the other account's harness reads the first one's session and
  keeps its copy (decided with the user on 19/10/2026).
- **The session a copy replaced is deleted** (asked for by the user on 22/10/2026): the copy has
  all it had, and going back to its account copies the copy, so it would be left dead there. It
  goes once the copy's harness lists the copy, and only when no instance here and no other program
  has it open; a fork the user asked for, and a session that went on with another executor, whose
  handoff packet is not the whole of it, keep theirs. Claude Code's and Codex's session files keep
  what a compaction replaced (checked on 22/10/2026), and the card keeps every message in Atlas.
- **An account at its plan's limit hands over to the next** (asked for by the user on
  21/10/2026): the session goes on in a copy on the next account in the order the user set under
  the session's box, passing over one not logged in or at its own limit, and continues from where
  it stopped, after compacting there when the user asked for it. The user's sessions and Atlas's
  delegations alike.
- **A session's own route goes across executors** (asked for by the user on 21/10/2026): the
  user may give a session the destinations it goes on to at a plan's limit, in their order,
  accounts of its executor and of others (Claude Code · main, Claude Code · company B, Codex ·
  main). An account of the same executor goes on in a copy, as above; another executor cannot
  open the session, so a new one starts there, on the same card, its first prompt a `handoff
  packet` put together from what the first one kept, with no model call (`handoff.py`). A
  session with a route follows it instead of its executor's order.
- **A session at its plan's limit that nothing takes over goes on by itself** (decided with the
  user on 03/11/2026): once, a minute after the limit resets, with the prompt to continue; never
  when the user took it up meanwhile.
- **An instance of Atlas's** runs one delegated task (the subagents start and end it); the user
  sees it on the workbench too, and may steer it. Atlas steers or stops the user's own only when
  the user asks (part 3).

Step 16f moves the cards to the executors tab (decided with the user on 15/10/2026, ROADMAP.md,
Aba Executores e painel lateral): each executor's sessions, wherever they began, read by its plugin;
a session opens as a conversation, and continuing it starts an instance on it. What runs underneath
stays this workbench.
"""

import asyncio
import dataclasses
import logging
import os
import re
import secrets
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from atlas.assistant.executors import (
    ExecutorDesk,
    ExecutorRun,
    account_name,
    default_executor_effort,
    slash_view,
    split_account,
    usage_view,
)
from atlas.assistant.git import GitState, add_worktree, git_state
from atlas.assistant.handoff import Handoff, entries_of, handoff_packet
from atlas.assistant.model import (
    AccountList,
    AccountSwitch,
    AccountView,
    ExecutorUsage,
    InstanceActivity,
    InstanceState,
    Owner,
    RouteStop,
    RouteView,
    SessionFolderView,
    SessionView,
    SlashCommandView,
    WorkbenchEvent,
    merged_usage,
)
from atlas.conversations import WORKBENCH, Conversation, title_from
from atlas.home import format_utc, utc_now
from atlas.jobs import (
    AccountFailover,
    ExecutorAccount,
    JobsError,
    RouteTarget,
    SessionRoute,
)
from atlas.models import (
    EFFORT_LADDER,
    AssistantMessage,
    Attachment,
    Message,
    ModelClient,
    ModelRequest,
    ModelsError,
    SystemMessage,
    UserMessage,
    clamp_effort,
)
from atlas.plugins import (
    Connector,
    Executor,
    ExecutorError,
    ExecutorFile,
    ExecutorSpec,
    LimitReached,
    ModelChoice,
    SessionInfo,
    Transcript,
    TranscriptEntry,
)
from atlas.trust import ApprovalGate, Verdict

if TYPE_CHECKING:
    from atlas.assistant.service import Assistant

_logger = logging.getLogger("atlas.assistant")

#: How many of the last messages Atlas reads to answer an executor for the user.
ANSWER_CONTEXT_MESSAGES = 30
#: The call ledger's purpose for those answers.
EXECUTOR_ANSWER_PURPOSE = "executor_answer"
#: The main account's id: the harness's own login.
MAIN_ACCOUNT = ""
#: A nickname for another account, which names its folder too (`~/.claude-company-b`): letters,
#: digits, `-` and `_`, at most 40.
ACCOUNT_NICKNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
#: What a session is told when it goes on on another account after a plan's limit.
CONTINUE = "Continue the task from where you stopped."
#: How long an account that reached its limit without saying when it resets is passed over.
LIMIT_UNSAID_SECONDS = 3600
#: How long after its plan's limit resets a session that stopped at it goes on: the harness's
#: clock and this one may differ by some seconds.
RESUME_AFTER_SECONDS = 60
#: How often a session waiting for its plan reads the time. By the wall clock, not by a timer: a
#: computer that slept past the reset goes on when it wakes.
RESUME_CHECK_SECONDS = 30.0


async def answer_for_user(
    client: ModelClient, instruction: str, history: Sequence[Message], asked: str, scope: str
) -> str | None:
    """One call to a model, which reads the last words of `history` and answers what an outside
    executor asks the user (step 13); `None` when it cannot."""
    said = [
        f"{'User' if isinstance(message, UserMessage) else 'Atlas'}: {message.text}"
        for message in history[-ANSWER_CONTEXT_MESSAGES:]
        if isinstance(message, UserMessage | AssistantMessage) and message.text.strip()
    ]
    request = ModelRequest(
        (
            SystemMessage(instruction),
            UserMessage(
                f"The conversation so far:\n\n{chr(10).join(said)}\n\nThe questions:\n{asked}"
            ),
        ),
        purpose=EXECUTOR_ANSWER_PURPOSE,
        scope=scope,
    )
    try:
        reply = await client.reply(request)
    except ModelsError as error:
        _logger.warning("Atlas could not answer an executor for the user: %s", error)
        return None
    return reply.text


class _OwnAnswering:
    """Atlas answering for the user on an instance of the user's, which works for no
    conversation: the guardian and the chosen model, reading the prompts the user gave it."""

    def __init__(self, assistant: "Assistant", transcript: Conversation) -> None:
        self._assistant = assistant
        self._transcript = transcript

    def _client(self) -> ModelClient | None:
        try:
            return self._assistant.models.client()
        except ModelsError as error:
            _logger.warning("Atlas has no model to answer an executor with: %s", error)
            return None

    async def review(self, command: str, reason: str) -> Verdict:
        client = self._client()
        if client is None:
            return Verdict.ESCALATE
        gate = ApprovalGate(conversation=self._transcript.id, guardian=client)
        return await gate.review(command, reason)

    async def answer_for_user(self, instruction: str, asked: str) -> str | None:
        client = self._client()
        if client is None:
            return None
        history = self._transcript.history
        return await answer_for_user(client, instruction, history, asked, self._transcript.id)


class Workbench:
    """Every executor instance of one Atlas."""

    def __init__(self, assistant: "Assistant", notify: Callable[[WorkbenchEvent], None]) -> None:
        self._assistant = assistant
        self._tell = notify
        self._runs: dict[str, ExecutorRun] = {}
        #: Each executor's plan usage, one per account, as last read or told by one of its
        #: instances.
        self._usage: dict[str, ExecutorUsage] = {}
        #: The accounts that reached their plan's limit, by name, and when each resets (a Unix
        #: time): the hand-over passes over them until then.
        self._limited: dict[str, float] = {}
        #: The hand-overs of the user's sessions under way, and the deletions of the sessions
        #: their copies replaced.
        self._handing: set[asyncio.Task[None]] = set()
        #: The user's sessions waiting for their plan's limit to reset, to go on then.
        self._resuming: set[asyncio.Task[None]] = set()

    def notify(self, event: WorkbenchEvent) -> None:
        """Hears every change of every instance, and tells the screens. What an instance says of
        its plan's usage names only some windows: it is told merged with what was kept."""
        if isinstance(event, ExecutorUsage):
            kept = self._usage.get(event.executor)
            event = self._usage[event.executor] = merged_usage(kept, event)
        self._tell(event)

    @property
    def desk(self) -> ExecutorDesk | None:
        return self._assistant.desk

    def runs(self) -> list[ExecutorRun]:
        """The instances on the workbench, the oldest first."""
        return list(self._runs.values())

    def get(self, instance_id: str) -> ExecutorRun:
        found = self._runs.get(instance_id)
        if found is None:
            raise ExecutorError(f"There is no instance {instance_id!r} on the workbench.")
        return found

    def adopt(self, run: ExecutorRun) -> None:
        """An instance of Atlas's, delegated: on the workbench while it works."""
        self._runs[run.id] = run

    def drop(self, run: ExecutorRun) -> None:
        """Take an ended instance off the workbench."""
        if self._runs.pop(run.id, None) is not None:
            run.changed()

    def new_id(self) -> str:
        return f"in-{secrets.token_hex(4)}"

    # -- the user's ----------------------------------------------------------------------------

    async def start(
        self,
        name: str,
        folder: Path,
        *,
        resume: str | None = None,
        transcript: Conversation | None = None,
        auto: bool = False,
        instance_id: str | None = None,
        fork: bool = False,
        model: str | None = None,
        effort: str | None = None,
        account: str | None = None,
        resume_from: Path | None = None,
    ) -> ExecutorRun:
        """Start an instance of the user's, in `folder`; with `instance_id`, in the place of the
        one of that id, so its card stays; with `resume`, on that session, unless an instance
        already works on it here, which is the one returned: one process per session in Atlas.
        With `fork`, on a copy of `resume`: a session of its own, the original left as it was;
        with `resume_from`, the copy of another account's session, kept in that folder. With
        `model`, on it, else on the harness's default model; with `effort`, on it, else on Atlas's
        default effort for the model. `name` is an executor's, or its executor on an account
        (`claude-code@1a2b3c4d`); with `account`, on that account of its executor (empty, the
        main one); an executor's own name with neither starts a new session on the folder's. On
        an account other than the one `name` keeps `resume` in, it goes on in a copy. Raises
        `ExecutorError`."""
        base, named = split_account(name)
        given = name
        if account is None and not named and resume is None:
            # The folder's account is its new sessions': one that exists stays on its own.
            account = await asyncio.to_thread(self.folder_account, base, self._folder(folder))
        if account is not None:
            name = account_name(base, account)
        executor = self._executor(name)
        if resume is not None and resume_from is None and name != given:
            # `resume` is the session of the account `name` was given as: another account goes on
            # with it in a copy, as a switch does (found on 19/10/2026: a session not running,
            # switched in the box, was looked for in the other account's folder).
            resume_from = self._executor(given).account_folder()
        if fork and (resume is None or not executor.capabilities().fork):
            raise ExecutorError(f"{executor.title} does not go on with a session in a copy.")
        if (model or effort) and not executor.capabilities().models:
            raise ExecutorError(f"{executor.title} has no models Atlas can choose.")
        if effort is None:
            effort = await default_executor_effort(executor, model)
        # "Auto" is the box's, for every executor's new session: one with no automatic mode asks.
        auto = auto and executor.capabilities().auto
        if resume is not None and instance_id is None and not fork:
            running = self.on_session(name, resume)
            if running is not None:
                return running
        folder = self._folder(folder)
        label = await _login(executor)
        assistant = self._assistant
        if transcript is None:
            stored = assistant.conversations.create(
                source=WORKBENCH, title=title_from(f"{executor.title} · {folder.name or folder}")
            )
            transcript = assistant.conversations.open(stored.id)
            if resume is not None:
                await asyncio.to_thread(self._inherit_route, resume, transcript.id)
        run = ExecutorRun(
            instance_id=instance_id or self.new_id(),
            executor=executor,
            name=name,
            spec=ExecutorSpec(
                folder,
                resume=resume,
                auto=auto,
                fork=fork,
                model=model,
                effort=effort,
                resume_from=resume_from,
                # The files attached to its prompts are read where the home keeps them.
                directories=(
                    (assistant.attachments.folder,) if executor.capabilities().files else ()
                ),
            ),
            transcript=transcript,
            owner=Owner.USER,
            answering=_OwnAnswering(assistant, transcript),
            desk=self.desk,
            notify=self.notify,
            account=label,
            record=assistant.instance_store,
            clock=assistant.clock,
            on_limit=self._limit_reached,
            on_copied=self.copy_kept,
        )
        self._runs[run.id] = run
        try:
            await run.open()
        except ExecutorError:
            # Its card stays, saying why, until the user takes it down.
            _logger.warning("An executor instance could not start.", extra={"instance": run.id})
        return run

    async def prompt(
        self, instance_id: str, text: str, attachments: Sequence[Attachment] = ()
    ) -> bool:
        """The card's prompt, with the files the user attached (step 16i): a new turn, or a steer
        when one runs (`True`)."""
        if not text.strip() and not attachments:
            raise ExecutorError("The prompt is empty.")
        kept = self._assistant.attachments
        files: list[ExecutorFile] = []
        for attachment in attachments:
            path = kept.path(attachment.id)
            if path is None:
                raise ExecutorError(f"{attachment.name} is gone; attach it again.")
            files.append(ExecutorFile(attachment.name, attachment.media_type, path))
        return await self.get(instance_id).prompt(text.strip(), attachments, files)

    async def set_auto(self, instance_id: str, on: bool) -> None:
        await self.get(instance_id).set_auto(on)

    async def set_mode(self, instance_id: str, mode: str) -> None:
        await self.get(instance_id).set_mode(mode)

    async def set_model(self, instance_id: str, model: str | None, effort: str | None) -> None:
        """The instance's model and effort from its next request on; no effort, Atlas's default
        for the model."""
        found = self.get(instance_id)
        if effort is None:
            effort = await default_executor_effort(found.executor, model)
        await found.set_model(model, effort)

    def set_atlas_answers(self, instance_id: str, on: bool) -> None:
        self.get(instance_id).set_atlas_answers(on)

    async def duplicate(self, instance_id: str, folder: Path) -> ExecutorRun:
        """The same executor again, the user's, in `folder`."""
        found = self.get(instance_id)
        return await self.start(found.name, folder, account=found.account_id)

    # -- accounts (step 16h) ---------------------------------------------------------------------

    async def accounts(self, name: str) -> AccountList:
        """`name`'s accounts, the main one first, each with who its harness is logged in as;
        only the main one for an executor whose plugin runs on no other. Raises
        `ExecutorError`."""
        executor = self._executor(name)
        if not executor.capabilities().account_folders:
            logged = await _logged(executor)
            return AccountList(name, (AccountView(name, MAIN_ACCOUNT, name, "", "", *logged),))
        kept = await asyncio.to_thread(self._assistant.account_store.accounts, name)
        found = [(found, self._executor(account_name(name, found.id))) for found in kept]
        logins = await asyncio.gather(_logged(executor), *(_logged(on) for _kept, on in found))
        main = AccountView(name, MAIN_ACCOUNT, name, "", str(executor.account_folder()), *logins[0])
        others = tuple(
            _account_view(account, logged)
            for (account, _on), logged in zip(found, logins[1:], strict=True)
        )
        failover = await asyncio.to_thread(self._assistant.account_store.failover, name)
        listed = _in_order((main, *others), failover.order, lambda view: view.account_id)
        return AccountList(
            name, listed, can_add=True, failover=failover.enabled, compact=failover.compact
        )

    # -- an account's plan limit (asked for by the user on 21/10/2026) --------------------------

    async def set_failover(
        self, name: str, order: Sequence[str], *, enabled: bool, compact: bool
    ) -> AccountList:
        """The order `name`'s accounts take over in at a plan's limit, the main one as `""`,
        whether they do, and whether a session compacts before it goes on. Raises
        `ExecutorError` for an account `name` does not have."""
        executor = self._executor(name)
        if not executor.capabilities().account_folders:
            raise ExecutorError(f"{executor.title} runs on no account but its own.")
        store = self._assistant.account_store
        kept = await asyncio.to_thread(store.accounts, name)
        known = {MAIN_ACCOUNT, *(account.id for account in kept)}
        unknown = [account for account in order if account not in known]
        if unknown:
            raise ExecutorError(f"{executor.title} has no account {unknown[0]!r}.")
        failover = AccountFailover(name, enabled, compact, tuple(dict.fromkeys(order)))
        await asyncio.to_thread(store.set_failover, failover)
        return await self.accounts(name)

    def failover(self, name: str) -> AccountFailover:
        """How the accounts of `name`'s executor take over from one another."""
        return self._assistant.account_store.failover(split_account(name)[0])

    async def next_account(self, name: str, limit: LimitReached | None) -> str | None:
        """The account to go on on after `name` reached its plan's limit: the next one in the
        user's order after it, round to the start, logged in and not at its own limit; `None`
        when there is none, or the user turned the hand-over off. With `limit`, `name` is passed
        over from now until it resets."""
        base, current = split_account(name)
        self._mark_limited(name, limit)
        executor = self._assistant.executor(base)
        if executor is None or not executor.capabilities().account_folders:
            return None
        store = self._assistant.account_store
        failover = await asyncio.to_thread(store.failover, base)
        if not failover.enabled:
            return None
        kept = await asyncio.to_thread(store.accounts, base)
        ids = _in_order((MAIN_ACCOUNT, *(a.id for a in kept)), failover.order, lambda a: a)
        at = ids.index(current) if current in ids else -1
        for account in (*ids[at + 1 :], *ids[: max(at, 0)]):
            on = account_name(base, account)
            if self.limited(on):
                continue
            found = self._assistant.executor(on)
            if found is not None and (await _logged(found))[1]:
                return account
        return None

    def _mark_limited(self, name: str, limit: LimitReached | None) -> None:
        """`name` reached its plan's limit: passed over from now until it resets."""
        if limit is not None:
            now = self._assistant.clock().timestamp()
            resets = limit.resets_at
            self._limited[name] = float(resets) if resets else now + LIMIT_UNSAID_SECONDS

    def limited(self, name: str) -> bool:
        """The account `name` is at its plan's limit: it reached it here and has not reset, or
        its usage, as last read, says a limit of every model is used up."""
        now = self._assistant.clock().timestamp()
        if self._limited.get(name, 0) > now:
            return True
        usage = self._usage.get(name)
        for window in () if usage is None else usage.windows:
            if window.model or window.used < 100:
                continue
            resets = _reset_time(window.resets_at)
            if resets is None or resets > now:
                return True
        return False

    def _limit_reached(self, run: ExecutorRun) -> None:
        """A user's session stopped at its account's plan limit: it is handed over; when nothing
        takes it, it goes on where it is once the limit resets."""
        task = asyncio.create_task(self._hand_over(run))
        self._handing.add(task)
        task.add_done_callback(self._handing.discard)

        def handed(done: asyncio.Task[None]) -> None:
            if done.cancelled() or self._runs.get(run.id) is not run:
                return  # closing, or it went on elsewhere
            waiting = asyncio.create_task(self._resume_at_reset(run))
            self._resuming.add(waiting)
            waiting.add_done_callback(self._resuming.discard)

        task.add_done_callback(handed)

    async def _resume_at_reset(self, run: ExecutorRun) -> None:
        """Go on with `run`'s session, once, a minute after its plan's limit resets, with the
        prompt to continue (decided with the user on 03/11/2026): only when it stopped mid-turn,
        and only when nobody took it up meanwhile (a prompt, another account, a stop). A limit
        that does not say when it resets is told and left to the user."""
        limit = run.limited
        if limit is None or run.state is not InstanceState.IDLE:
            return
        if limit.resets_at is None:
            self.notify(InstanceActivity(run.id, "resume", "", failed=True))
            return
        at = limit.resets_at + RESUME_AFTER_SECONDS
        said = len(run.history)
        self.notify(InstanceActivity(run.id, "resume", format_utc(datetime.fromtimestamp(at, UTC))))
        while self._assistant.clock().timestamp() < at:
            await asyncio.sleep(RESUME_CHECK_SECONDS)
            if self._runs.get(run.id) is not run:
                return
        if self._runs.get(run.id) is not run or run.state is not InstanceState.IDLE:
            return
        if len(run.history) != said:
            return  # the user went on by hand
        _logger.info("A session went on after its plan's limit reset.", extra={"instance": run.id})
        try:
            await run.prompt(CONTINUE)
        except ExecutorError as error:
            _logger.warning("A session could not go on after its plan's limit: %s", error)

    def copy_kept(self, run: ExecutorRun) -> None:
        """A turn of `run`, a copy of another account's session, ended: the session it replaces
        is deleted there, in the background, once nothing needs it."""
        replaced = run.replaces
        if replaced is None:
            return
        run.replaces = None
        task = asyncio.create_task(self._drop_replaced(run, replaced))
        self._handing.add(task)
        task.add_done_callback(self._handing.discard)

    async def _drop_replaced(self, run: ExecutorRun, replaced: str) -> None:
        """Delete `replaced`, the session `run`'s copy took the place of on another account of
        its executor, once the copy's harness lists the copy (until then, the next turn tries
        again); never one an instance here or another program has open, nor where either
        harness cannot say."""
        copy, folder = run.session_id, run.spec.folder
        source = await asyncio.to_thread(self._kept_in, run)
        if copy is None or source is None:
            return
        kept = self._executor(source)
        if not (kept.capabilities().delete_sessions and run.executor.capabilities().sessions):
            return
        try:
            if await self._session(run.executor, copy, folder) is None:
                run.replaces = replaced
                return
            if any(
                other.session_id == replaced and other.name == source and other.live
                for other in self._runs.values()
            ):
                return
            found = await self._session(kept, replaced, folder)
            if found is None or found.elsewhere:
                return
            await kept.delete_session(replaced, folder)
        except ExecutorError as error:
            _logger.warning("The session a copy replaced was not deleted: %s", error)
            return
        except Exception:  # a plugin's own code: the session stays, logged
            _logger.exception("An executor could not delete the session a copy replaced.")
            return
        _logger.info("The session a copy replaced was deleted on its account.")

    def _kept_in(self, run: ExecutorRun) -> str | None:
        """The name of the account of `run`'s executor its copy was made from, by its folder."""
        origin = run.spec.resume_from
        if origin is None:
            return None
        base = split_account(run.name)[0]
        kept = self._assistant.account_store.accounts(base)
        for name in (base, *(account_name(base, account.id) for account in kept)):
            found = self._assistant.executor(name)
            if found is not None and name != run.name and found.account_folder() == origin:
                return name
        return None

    async def _session(self, executor: Executor, session: str, folder: Path) -> SessionInfo | None:
        """`session`, as `executor` lists it in `folder`; `None` when it does not."""
        if executor.capabilities().folders:
            listed = await executor.sessions(folder=folder)
        else:
            listed = await executor.sessions()
        return next((found for found in listed if found.id == session), None)

    async def _hand_over(self, run: ExecutorRun) -> None:
        """Go on with the user's session on the next account, in a copy there, compacted first
        when the user asked for it, from where it stopped; by its own route, when it has one."""
        try:
            store = self._assistant.account_store
            route = await asyncio.to_thread(store.route, run.transcript.id)
            if route is not None and route.enabled and route.targets:
                await self._follow_route(run, route)
                return
            account = await self.next_account(run.name, run.limited)
            if self._runs.get(run.id) is not run or run.working:
                return  # the user took it over meanwhile
            if account is None:
                if self.failover(run.name).enabled:
                    told = "No other account can go on now."
                    self.notify(InstanceActivity(run.id, "account", told, failed=True))
                return
            name = account_name(split_account(run.name)[0], account)
            model, effort, note = await self.carried(run, name)
            switched = await self.use_account(run.id, account, carried=(model, effort))
            self.notify(InstanceActivity(switched.id, "account", switched.account or account))
            if note:
                self.notify(InstanceActivity(switched.id, "note", note))
            _logger.info("A session went on on another account after a plan limit.")
            await self._compact(switched, run, self.failover(run.name).compact)
            await switched.prompt(CONTINUE)
        except ExecutorError as error:
            _logger.warning("A session could not go on on another account: %s", error)
            self.notify(InstanceActivity(run.id, "account", str(error), failed=True))

    # -- a session's route, across executors (asked for by the user on 21/10/2026) -------------

    async def route(self, instance_id: str) -> RouteView:
        """The route of the instance's session, empty until the user sets one."""
        found = self.get(instance_id)
        store = self._assistant.account_store
        route = await asyncio.to_thread(store.route, found.transcript.id)
        if route is None:
            return RouteView(found.id)
        stops = [await asyncio.to_thread(self._stop, target) for target in route.targets]
        return RouteView(found.id, tuple(stops), enabled=route.enabled, compact=route.compact)

    async def set_route(
        self,
        instance_id: str,
        targets: Sequence[RouteTarget],
        *,
        enabled: bool,
        compact: bool = True,
    ) -> RouteView:
        """The destinations the instance's session goes on to at a plan's limit, in order, each an
        executor installed here and one of its accounts (the main one as `""`) with the model and
        the effort it goes on with there, whether it does, and whether a copy on another account
        compacts first. Kept for its card, so the instances that take its place follow it too.
        Raises `ExecutorError` for an executor, an account or an effort Atlas does not know."""
        found = self.get(instance_id)
        store = self._assistant.account_store
        for target in targets:
            if split_account(target.executor)[1]:
                raise ExecutorError(f"{target.executor!r} names an account: give it apart.")
            executor = self._executor(target.executor)
            known = target.account == MAIN_ACCOUNT or (
                await asyncio.to_thread(store.get, target.executor, target.account) is not None
            )
            if not known:
                raise ExecutorError(f"{executor.title} has no account {target.account!r}.")
            if target.effort is not None and target.effort not in EFFORT_LADDER:
                raise ExecutorError(f"There is no effort {target.effort!r}.")
        route = SessionRoute(
            found.transcript.id, tuple(dict.fromkeys(targets)), enabled=enabled, compact=compact
        )
        await asyncio.to_thread(store.set_route, route)
        return await self.route(instance_id)

    def _inherit_route(self, session: str, conversation: str) -> None:
        """A session opened again keeps the route the user gave its card."""
        kept = self._assistant.instance_store.conversation_of(session)
        store = self._assistant.account_store
        route = None if kept is None else store.route(kept)
        if route is not None:
            store.set_route(dataclasses.replace(route, conversation=conversation))

    def _stop(self, target: RouteTarget) -> RouteStop:
        """A destination as the screen lists it."""
        name = account_name(target.executor, target.account)
        executor = self._assistant.executor(name)
        store = self._assistant.account_store
        kept = store.get(target.executor, target.account) if target.account else None
        title = target.executor if executor is None else executor.title
        nickname = "" if kept is None else kept.name
        return RouteStop(
            target.executor,
            target.account,
            name,
            title,
            nickname,
            installed=executor is not None,
            model=target.model,
            effort=target.effort,
            limited=self.limited(name),
        )

    async def next_target(
        self, name: str, route: SessionRoute, limit: LimitReached | None = None
    ) -> RouteTarget | None:
        """The destination after `name` in `route`, round to the start: installed, logged in,
        not at its own limit, and not `name` itself; `None` when none is. With `limit`, `name` is
        passed over from now until it resets."""
        self._mark_limited(name, limit)
        base, current = split_account(name)
        targets = route.targets
        here = RouteTarget(base, current)
        at = targets.index(here) if here in targets else -1
        for target in (*targets[at + 1 :], *targets[: max(at, 0)]):
            on = account_name(target.executor, target.account)
            if on == name or self.limited(on):
                continue
            found = self._assistant.executor(on)
            if found is not None and (await _logged(found))[1]:
                return target
        return None

    async def _follow_route(self, run: ExecutorRun, route: SessionRoute) -> None:
        """The session goes on to the next destination of its route: on an account of the same
        executor in a copy, as an account's hand-over does; on another executor in a new session,
        on the same card, whose first prompt is the handoff packet."""
        target = await self.next_target(run.name, route, run.limited)
        if self._runs.get(run.id) is not run or run.working:
            return  # the user took it over meanwhile
        if target is None:
            told = "No destination in the session's route can go on now."
            self.notify(InstanceActivity(run.id, "route", told, failed=True))
            return
        name = account_name(target.executor, target.account)
        model, effort, note = await self.carried(run, name, target)
        if target.executor == split_account(run.name)[0]:
            switched = await self.use_account(run.id, target.account, carried=(model, effort))
            told = switched.account or target.account
            self.notify(InstanceActivity(switched.id, "account", told))
            if note:
                self.notify(InstanceActivity(switched.id, "note", note))
            _logger.info("A session went on on another account by its route.")
            await self._compact(switched, run, route.compact)
            await switched.prompt(CONTINUE)
            return
        packet = await self._packet(run, target)
        await self._end(run)
        switched = await self.start(
            target.executor,
            run.spec.folder,
            transcript=run.transcript,
            auto=run.auto,
            instance_id=run.id,
            model=model,
            effort=effort,
            account=target.account,
        )
        switched.set_atlas_answers(run.atlas_answers)
        destination = await asyncio.to_thread(self.label, switched.name)
        self.notify(InstanceActivity(switched.id, "route", destination))
        if note:
            self.notify(InstanceActivity(switched.id, "note", note))
        _logger.info("A session went on on another executor by its route.")
        await switched.prompt(packet)

    async def carried(
        self, run: ExecutorRun, name: str, target: RouteTarget | None = None
    ) -> tuple[str | None, str | None, str]:
        """The model and the effort `run`'s session goes on with on `name` at a switch, kept at
        every one (decided with the user on 25/10/2026), and what the screen says when they
        change: the destination's own when its route gives them; else, on the same harness, the
        model the user chose, or the one it ran as when it ran on the harness's default, which may
        be another model on another account's plan, and on another harness its default; and the
        effort it ran on, onto the levels the model takes there. A model the destination does not
        offer gives way to its default. When its models cannot be read, the user's own choices go
        as they were, and the effort it ran on only where it ran."""
        same = split_account(name)[0] == split_account(run.name)[0]
        given = target is not None and target.model is not None
        chosen = target.model if target is not None and target.model else None
        if not given and same:
            chosen = run.chosen_model
        stop_effort = target.effort if target is not None and target.effort else None
        effort = stop_effort or run.effort
        executor = self._assistant.executor(name)
        if executor is None or not executor.capabilities().models:
            return None, None, ""
        try:
            offered = await self.models(name)
        except ExecutorError as error:
            _logger.warning("A switch could not read its destination's models: %s", error)
            offered = []
        if not offered:
            return chosen, (effort if same or stop_effort else None), ""
        # On its harness's default, it keeps the model it ran as.
        wanted = chosen or (run.model if same and run.model else None)
        found = next((m for m in offered if wanted in (m.id, m.resolved)), None) if wanted else None
        note = ""
        if wanted is not None and found is None:
            label = await asyncio.to_thread(self.label, name)
            note = f"{label} does not offer {wanted}: the session goes on on its default model."
        # Its default is the first listed: found there, it goes on on the default, as it ran.
        model = found or offered[0]
        if effort is not None:
            effort = clamp_effort(effort, model.efforts) if model.efforts else None
        return (None if found is None or found is offered[0] else found.id), effort, note

    async def _compact(self, switched: ExecutorRun, before: ExecutorRun, wanted: bool) -> None:
        """Compact the copy on another account before it goes on, when the user wants it (on by
        default, decided with the user on 25/10/2026: at the switch, never before the limit) and
        its harness has `/compact`; one that fails is said, and the session goes on as it was."""
        if not wanted or "compact" not in before.commands:
            return
        try:
            await switched.turn("/compact")
        except ExecutorError as error:
            _logger.warning("A switched session could not be compacted: %s", error)
            self.notify(InstanceActivity(switched.id, "note", not_compacted(error)))

    async def _packet(self, run: ExecutorRun, target: RouteTarget) -> str:
        """The handoff packet for `run`'s session going on on `target`."""
        source = await asyncio.to_thread(self.label, run.name)
        return await self.handoff(
            run.name,
            run.session_id or "",
            run.spec.folder,
            run.history,
            account_name(target.executor, target.account),
            limit_reason(source, run.limited, "the session's route"),
            instances=(run.id,),
        )

    async def handoff(
        self,
        name: str,
        session: str,
        folder: Path,
        history: Sequence[Message],
        target: str,
        reason: str,
        *,
        instances: Sequence[str] = (),
    ) -> str:
        """The handoff packet for the session `session` of `name` in `folder` going on on the
        executor `target` (an account's, `claude-code@1a2b3c4d`), for `reason`: its steps as its
        harness kept them, or as Atlas kept them (`history`) when the harness cannot read them;
        with the files the turns of `instances` changed. A delegated task's route uses it too."""
        entries: Sequence[TranscriptEntry] = ()
        executor = self._assistant.executor(name)
        if session and executor is not None and executor.capabilities().sessions:
            try:
                read = await self.transcript(name, session, folder)
                entries = read.entries
            except ExecutorError as error:
                _logger.warning("A session's steps could not be read for its handoff: %s", error)
        if not entries:
            kept = self._assistant.attachments
            paths = {
                attachment.id: str(path)
                for message in history
                if isinstance(message, UserMessage)
                for attachment in message.attachments
                if (path := kept.path(attachment.id)) is not None
            }
            entries = entries_of(history, paths)
        store = self._assistant.instance_store
        changed = [
            path
            for instance in instances
            for turn in await asyncio.to_thread(store.turns, instance)
            for path in turn.files
        ]
        handoff = Handoff(
            source=await asyncio.to_thread(self.label, name),
            target=await asyncio.to_thread(self.label, target),
            folder=str(folder),
            reason=reason,
            entries=tuple(entries),
            session=session,
            changed=tuple(changed),
        )
        return handoff_packet(handoff)

    def label(self, name: str) -> str:
        """An executor on an account as the user knows it: "Claude Code · company-b"."""
        base, account = split_account(name)
        executor = self._assistant.executor(name)
        title = base if executor is None else executor.title
        kept = self._assistant.account_store.get(base, account) if account else None
        return f"{title} · {'main account' if kept is None else kept.name}"

    async def add_account(self, name: str, nickname: str) -> AccountView:
        """Keep another account of `name`: the user's nickname for it, and the harness's folder
        for it beside its own, made empty or taken as it is when it is there already (the user
        may have used it in a terminal). Not logged in yet: the harness's own login does it
        (`switch_executor_account`). Raises `ExecutorError` for a nickname that is empty, taken,
        or cannot name a folder."""
        executor = self._executor(name)
        if not executor.capabilities().account_folders:
            raise ExecutorError(f"{executor.title} runs on no account but its own.")
        nickname = nickname.strip()
        if not ACCOUNT_NICKNAME.fullmatch(nickname):
            raise ExecutorError(
                "Give the account a nickname of letters, digits, - or _, at most 40, such as "
                "company-b: it names its folder too."
            )
        folder = executor.new_account_folder(nickname)
        store = self._assistant.account_store
        kept = await asyncio.to_thread(store.accounts, name)
        taken = {folder_key(executor.account_folder()), *(folder_key(k.folder) for k in kept)}
        if folder_key(folder) in taken:
            raise ExecutorError(f"The folder {folder} is another account's already.")
        try:
            await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        except OSError as error:
            raise ExecutorError(f"The folder {folder} could not be made: {error}") from error
        try:
            made = await asyncio.to_thread(
                store.add, name, nickname, str(folder), self._assistant.clock()
            )
        except JobsError as error:
            raise ExecutorError(str(error)) from error
        _logger.info("Another account of %s was added.", name, extra={"account": made.id})
        return _account_view(made, ("", False))

    async def remove_account(self, name: str, account: str) -> None:
        """Forget another account: its nickname, and the folders that started on it, which start
        on the main account again. Its folder stays as it is, with its login and its sessions,
        which are the harness's. Never while an instance runs on it. Raises `ExecutorError`."""
        executor = self._executor(name)
        store = self._assistant.account_store
        found = await asyncio.to_thread(store.get, name, account)
        if found is None:
            raise ExecutorError(f"{executor.title} has no account {account!r}.")
        on = account_name(name, account)
        if any(run.name == on and run.live for run in self.runs()):
            raise ExecutorError(
                f"A {executor.title} session runs on {found.name}. Stop it, or switch it to "
                "another account, then remove the account."
            )
        await asyncio.to_thread(store.delete, name, account)
        self._usage.pop(on, None)

    def folder_account(self, name: str, folder: Path | str) -> str:
        """The account `folder`'s new sessions start on: the other one it names, when that one
        is still there, else the main one."""
        named = self._assistant.account_store.folder_accounts(name)
        wanted = folder_key(folder)
        return next((found for kept, found in named.items() if folder_key(kept) == wanted), "")

    async def set_folder_account(self, name: str, folder: str, account: str) -> None:
        """The account `folder`'s new sessions start on; empty, the main account. Raises
        `ExecutorError` for an account `name` does not have."""
        executor = self._executor(name)
        store = self._assistant.account_store
        if account and await asyncio.to_thread(store.get, name, account) is None:
            raise ExecutorError(f"{executor.title} has no account {account!r}.")
        named = await asyncio.to_thread(store.folder_accounts, name)
        for kept in named:
            # One row per folder, however its path was written (`C:` or `c:`).
            if folder_key(kept) == folder_key(folder) and kept != folder:
                await asyncio.to_thread(store.set_folder_account, name, kept, "")
        await asyncio.to_thread(store.set_folder_account, name, folder, account)

    async def account_switch(self, instance_id: str, account: str) -> AccountSwitch:
        """What changes if the instance goes on on `account` of its executor (empty, the main
        one), for the screen to say before the user switches: who that account is logged in as,
        and the connectors this account has that it lacks, the conversation's calls to which it
        could not make again, and how many tokens its first message there reads again. Raises
        `ExecutorError`."""
        found = self.get(instance_id)
        base, _current = split_account(found.name)
        other = self._executor(account_name(base, account))
        login, tokens = await asyncio.gather(_login(other), self._context(found))
        if not other.capabilities().connectors:
            return AccountSwitch(found.name, account, login, tokens=tokens)
        try:
            here, there = await asyncio.gather(
                self.connectors(found.name), self.connectors(account_name(base, account))
            )
        except ExecutorError as error:
            _logger.warning("The accounts' connectors could not be compared: %s", error)
            return AccountSwitch(found.name, account, login, tokens=tokens)
        names = {c.name for c in there or ()}
        missing = tuple(sorted({c.name for c in here or ()} - names))
        return AccountSwitch(found.name, account, login, missing, compared=True, tokens=tokens)

    async def _context(self, found: ExecutorRun) -> int | None:
        """How many tokens of context the instance's session holds, when its harness says."""
        if not found.session_id:
            return None
        try:
            read = await self.transcript(found.name, found.session_id, found.spec.folder)
        except ExecutorError as error:
            _logger.warning("The session's context could not be read: %s", error)
            return None
        return read.context

    async def move(self, instance_id: str, folder: Path) -> ExecutorRun:
        """The same card in another folder: the harness keeps its sessions by folder, so it starts
        a new one there. Never while it works."""
        found = self._idle(instance_id, "change its folder")
        folder = self._folder(folder)
        await self._end(found)
        moved = await self.start(
            found.name,
            folder,
            transcript=found.transcript,
            auto=found.auto,
            instance_id=found.id,
            model=found.chosen_model,
            effort=found.effort,
            account=found.account_id,
        )
        moved.set_atlas_answers(found.atlas_answers)
        return moved

    async def switch_account(self, instance_id: str, login: str) -> ExecutorRun:
        """Log the instance's harness into `login` on the user's browser, in the folder of the
        account it runs on, then go on with the same session in the same folder. Never while
        another of its instances on that account works."""
        found = self._idle(instance_id, "switch accounts")
        if not found.can_switch:
            raise ExecutorError(f"{found.title} does not switch accounts.")
        if any(run.name == found.name and run.working for run in self.runs()):
            raise ExecutorError(
                f"{found.title} is working in another folder, which would change accounts "
                "mid-task. Wait for it or stop it, then switch."
            )
        await _log_in_as(found.executor, login)
        # Another login has a plan of its own: the last read was the other's.
        self._usage.pop(found.name, None)
        return await self._go_on(found)

    async def use_account(
        self,
        instance_id: str,
        account: str,
        *,
        carried: tuple[str | None, str | None] | None = None,
    ) -> ExecutorRun:
        """Go on with the instance's conversation in the same folder on another account of its
        executor (empty, the main one), only when the user asks: that account's harness reads
        this one's session and keeps a copy of its own (decided with the user on 19/10/2026), the
        original deleted once the copy is kept (`copy_kept`), and the card stays. Never while it
        works; the instances on other accounts go on as they are. On the model and the effort
        `carried`, a plan limit's switch's (`carried`), else on the ones chosen for it."""
        found = self._idle(instance_id, "switch accounts")
        base, current = split_account(found.name)
        if account == current and found.live:
            return found
        target = account_name(base, account)
        other = self._executor(target)
        if not other.capabilities().account_folders:
            raise ExecutorError(f"{other.title} runs on no account but its own.")
        session = found.session_id
        model, effort = carried if carried is not None else (found.chosen_model, found.effort)
        await self._end(found)
        switched = await self.start(
            target,
            found.spec.folder,
            resume=session,
            transcript=found.transcript,
            auto=found.auto,
            instance_id=found.id,
            model=model,
            effort=effort,
            account=account,
            resume_from=None if session is None else found.executor.account_folder(),
        )
        switched.set_atlas_answers(found.atlas_answers)
        return switched

    async def switch_executor_account(self, name: str, login: str) -> list[ExecutorRun]:
        """Log `name`'s harness into `login` on the user's browser (empty: whichever account the
        user signs in to), from the executor in the sidebar rather than a session (asked for by
        the user on 18/10/2026): `name` may be its executor on another account, whose folder
        keeps the login. Then each of the user's instances of it here goes on with the same
        session on the new login, as one switched on its own does. Never while one of them
        works."""
        executor = self._executor(name)
        if not executor.capabilities().accounts:
            raise ExecutorError(f"{executor.title} does not switch accounts.")
        if any(run.name == name and run.working for run in self.runs()):
            raise ExecutorError(
                f"{executor.title} is working, which would change accounts mid-task. Wait for it "
                "or stop it, then switch."
            )
        await _log_in_as(executor, login)
        self._usage.pop(name, None)
        live = [
            run
            for run in self._runs.values()
            if run.name == name
            and run.owner is Owner.USER
            and run.state not in (InstanceState.ENDED, InstanceState.FAILED)
        ]
        return [await self._go_on(run) for run in live]

    async def _go_on(self, found: ExecutorRun) -> ExecutorRun:
        """End `found` and start it again on the same session in the same folder, on the same
        account, with what was chosen for it (the login being whatever its harness is logged into
        now)."""
        await self._end(found)
        switched = await self.start(
            found.name,
            found.spec.folder,
            resume=found.session_id,
            transcript=found.transcript,
            auto=found.auto,
            instance_id=found.id,
            model=found.chosen_model,
            effort=found.effort,
            account=found.account_id,
        )
        switched.set_atlas_answers(found.atlas_answers)
        return switched

    async def delete_session(self, name: str, session: str, folder: str) -> None:
        """Delete one session of `name`, in its account's folder only, as the user asked on
        19/10/2026: the copy a switch left on another account, or any other. Its card, when one
        is idle on it here, closes first; never while it works. The delegation record keeps its
        turns, which are Atlas's. Raises `ExecutorError`."""
        executor = self._executor(name)
        if not executor.capabilities().delete_sessions:
            raise ExecutorError(f"{executor.title} does not delete its sessions.")
        run = self.on_session(name, session)
        if run is not None:
            if run.working:
                raise ExecutorError(f"{run.title} is working on it; stop it, then delete it.")
            await run.stop()
            self.drop(run)
        try:
            await executor.delete_session(session, Path(folder))
        except ExecutorError:
            raise
        except Exception as error:  # a plugin's own code: its failure is told
            _logger.exception("An executor could not delete a session.")
            raise ExecutorError(
                f"{executor.title} could not delete the session: {type(error).__name__}: {error}"
            ) from error

    async def stop(self, instance_id: str) -> None:
        """Stop an instance for good, and take its card down."""
        found = self.get(instance_id)
        await found.stop()
        self.drop(found)

    async def close(self) -> None:
        waiting = [*self._handing, *self._resuming]
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
        for run in list(self._runs.values()):
            if run.owner is Owner.USER:
                await run.stop()
                self.drop(run)

    # -- the executors tab (step 16f) -----------------------------------------------------------

    def on_session(self, name: str, session: str) -> ExecutorRun | None:
        """The instance working on a session here, when one is."""
        return next(
            (run for run in self._shown(name) if run.session_id == session),
            None,
        )

    async def folders(self, name: str) -> list[SessionFolderView]:
        """The folders `name`'s sessions work in, the one changed last first, with none of their
        sessions read; first, those of the instances here in a folder not listed yet (one just
        started). A plugin that does not list them apart (before version 8) has its sessions
        listed, and their folders taken. Raises `ExecutorError`."""
        executor = self._executor(name)
        capabilities = executor.capabilities()
        listed: dict[str, str] = {}
        if capabilities.folders:
            for found in await _listed(executor, "folders", executor.folders):
                listed.setdefault(str(found.folder), found.updated_at)
        elif capabilities.sessions:
            for info in await _listed(executor, "sessions", executor.sessions):
                listed.setdefault(str(info.folder), info.updated_at)
        now = format_utc(self._assistant.clock())
        fresh: dict[str, str] = {}
        for run in self._shown(name):
            if str(run.spec.folder) not in listed:
                fresh.setdefault(str(run.spec.folder), now)
        base = split_account(name)[0]
        named = await asyncio.to_thread(self._assistant.account_store.folder_accounts, base)
        accounts = {folder_key(folder): account for folder, account in named.items()}
        return [
            SessionFolderView(name, folder, updated_at, accounts.get(folder_key(folder), ""))
            for folder, updated_at in [*fresh.items(), *listed.items()]
        ]

    async def sessions(self, name: str, folder: str | None = None) -> list[SessionView]:
        """Every session of `name`, wherever it began, the most recent first, each with the
        instance working on it here; first, the instances whose session the harness has not
        kept yet (one just started). With `folder`, only that folder's, as `folders` named it.
        Raises `ExecutorError`."""
        executor = self._executor(name)
        capabilities = executor.capabilities()
        listed: Sequence[SessionInfo] = ()
        if capabilities.sessions and folder is not None and capabilities.folders:
            chosen = Path(folder)
            listed = await _listed(executor, "sessions", lambda: executor.sessions(folder=chosen))
        elif capabilities.sessions:
            listed = await _listed(executor, "sessions", executor.sessions)
            if folder is not None:
                listed = [found for found in listed if str(found.folder) == folder]
        store = self._assistant.instance_store
        delegated = await asyncio.to_thread(store.delegated_sessions, name)
        runs = self._shown(name)
        on: dict[str, ExecutorRun] = {run.session_id: run for run in runs if run.session_id}
        views: list[SessionView] = []
        for found in listed:
            run = on.pop(found.id, None)
            view = _view(name, found.id, str(found.folder), found.title, found.updated_at, run)
            views.append(
                dataclasses.replace(
                    view,
                    started_at=found.started_at,
                    by_atlas=view.by_atlas or found.id in delegated,
                    elsewhere=found.elsewhere,
                )
            )
        now = format_utc(self._assistant.clock())
        fresh = [
            _view(name, run.session_id or "", str(run.spec.folder), _first_prompt(run), now, run)
            for run in runs
            if (not run.session_id or run.session_id in on)
            and (folder is None or str(run.spec.folder) == folder)
        ]
        return [*fresh, *views]

    async def transcript(self, name: str, session: str, folder: Path) -> Transcript:
        """A session's steps, read by its plugin where its harness keeps them. Raises
        `ExecutorError`."""
        executor = self._executor(name)
        try:
            return await executor.transcript(session, folder)
        except ExecutorError:
            raise
        except Exception as error:  # a plugin's own code: its failure is told
            _logger.exception("An executor could not read a session.")
            raise ExecutorError(
                f"{executor.title} could not read the session: {type(error).__name__}: {error}"
            ) from error

    async def models(self, name: str) -> list[ModelChoice]:
        """The models `name` offers and their efforts, as its plugin reads them; none from one
        that does not say it has them. Raises `ExecutorError`."""
        executor = self._executor(name)
        if not executor.capabilities().models:
            return []
        try:
            return list(await executor.models())
        except ExecutorError:
            raise
        except Exception as error:  # a plugin's own code: its failure is told
            _logger.exception("An executor could not list its models.")
            raise ExecutorError(
                f"{executor.title} could not list its models: {type(error).__name__}: {error}"
            ) from error

    async def connectors(self, name: str) -> list[Connector] | None:
        """The connectors `name` has for its account and the state of each, as its plugin reads
        them; `None` from one that does not say it lists them. Raises `ExecutorError`."""
        executor = self._executor(name)
        if not executor.capabilities().connectors:
            return None
        try:
            return list(await executor.connectors())
        except ExecutorError:
            raise
        except Exception as error:  # a plugin's own code: its failure is told
            _logger.exception("An executor could not list its connectors.")
            raise ExecutorError(
                f"{executor.title} could not list its connectors: {type(error).__name__}: {error}"
            ) from error

    async def commands(
        self, name: str, folder: Path, instance_id: str = ""
    ) -> list[SlashCommandView]:
        """The slash commands `name` takes in `folder`, for the box: those its instance there said,
        or, with none running, those its plugin reads with no session (a harness started there
        and closed at once); the last instance's names from a plugin that does not list them.
        Raises `ExecutorError`."""
        executor = self._executor(name)
        run = self._runs.get(instance_id)
        if run is not None and run.name == name and run.live and run.slash:
            return list(run.slash)
        if not executor.capabilities().commands:
            known = next((r for r in reversed(self.runs()) if r.name == name and r.slash), None)
            return [] if known is None else list(known.slash)
        try:
            return list(slash_view(await executor.commands(folder=self._folder(folder))))
        except ExecutorError:
            raise
        except Exception as error:  # a plugin's own code: its failure is told
            _logger.exception("An executor could not list its slash commands.")
            raise ExecutorError(
                f"{executor.title} could not list its commands: {type(error).__name__}: {error}"
            ) from error

    async def usage(self, name: str) -> ExecutorUsage | None:
        """How much of its plan's limits the account `name` is logged in as has used (its
        executor on another account: that one's), read by its plugin with no model call, and told
        to every screen; `None` from one that does not say it reads it. Raises
        `ExecutorError`."""
        executor = self._executor(name)
        if not executor.capabilities().usage:
            return None
        try:
            read = await executor.usage()
        except ExecutorError:
            raise
        except Exception as error:  # a plugin's own code: its failure is told
            _logger.exception("An executor could not read its plan's usage.")
            raise ExecutorError(
                f"{executor.title} could not read its plan's usage: {type(error).__name__}: {error}"
            ) from error
        found = self._usage[name] = usage_view(name, read, format_utc(utc_now()))
        self._tell(found)
        return found

    def _shown(self, name: str) -> list[ExecutorRun]:
        """The instances of `name` the executors tab shows: all but the ended."""
        return [
            run
            for run in self._runs.values()
            if run.name == name and run.state is not InstanceState.ENDED
        ]

    # -- helpers --------------------------------------------------------------------------------

    def _executor(self, name: str) -> Executor:
        found = self._assistant.executor(name)
        if found is None:
            raise ExecutorError(f"{name} is not installed on this computer.")
        return found

    async def git(self, folder: Path) -> GitState:
        """The folder's git, for the session's environment: its branch, and whether it is a
        worktree."""
        return await asyncio.to_thread(git_state, self._folder(folder))

    async def add_worktree(self, folder: Path, name: str) -> Path:
        """A new worktree of the folder's repository, beside it, for a session apart from it."""
        return await asyncio.to_thread(add_worktree, self._folder(folder), name)

    async def context(self, instance_id: str) -> tuple[int | None, int | None]:
        """How many tokens of context the instance's session holds, when its harness says, and
        the window of the model it runs as, when the models' catalog knows it."""
        found = self.get(instance_id)
        return await self._context(found), self._window(found.model or found.chosen_model or "")

    def _window(self, model: str) -> int | None:
        if model.endswith("[1m]"):
            # Claude Code's name for a model on its million-token window.
            return 1_000_000
        shipped = self._assistant.models.catalog.shipped
        windows = [info.context_window for (_, found), info in shipped.items() if found == model]
        return next((window for window in windows if window), None)

    def _folder(self, folder: Path) -> Path:
        chosen = folder.expanduser()
        if not chosen.is_absolute():
            chosen = self._assistant.workspace / chosen
        if not chosen.is_dir():
            raise ExecutorError(f"The folder {chosen} does not exist.")
        return chosen

    def _idle(self, instance_id: str, what: str) -> ExecutorRun:
        found = self.get(instance_id)
        if found.working:
            raise ExecutorError(f"{found.title} is working; wait for it or stop it to {what}.")
        if found.owner is not Owner.USER:
            raise ExecutorError(f"This {found.title} works for Atlas; only the user's {what}.")
        return found

    async def _end(self, run: ExecutorRun) -> None:
        if run.state is not InstanceState.ENDED:
            await run.stop()


def _view(
    name: str, session: str, folder: str, title: str, updated_at: str, run: ExecutorRun | None
) -> SessionView:
    """A session's row, with the instance on it."""
    if run is None:
        return SessionView(name, session, folder, title, updated_at, "")
    return SessionView(
        name,
        session,
        folder,
        title,
        updated_at,
        "",
        state=run.state.value,
        instance_id=run.id,
        waiting=run.waiting,
        by_atlas=run.owner is Owner.ATLAS,
    )


async def _listed[T](
    executor: Executor, what: str, read: Callable[[], Awaitable[Sequence[T]]]
) -> Sequence[T]:
    """What a plugin lists of its harness; its failure is told, so the screen says it."""
    try:
        return await read()
    except ExecutorError:
        raise
    except Exception as error:  # a plugin's own code: its failure is told
        _logger.exception("An executor could not list its %s.", what)
        raise ExecutorError(
            f"{executor.title} could not list its {what}: {type(error).__name__}: {error}"
        ) from error


def _reset_time(text: str) -> float | None:
    """When a usage window resets, as a Unix time, however its harness wrote it (Codex's ISO 8601
    has no fraction of a second: found on 21/10/2026, when it broke the hand-over); `None` when
    it does not say, or says it in a way not read."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        _logger.warning("A usage window's reset could not be read: %r", text)
        return None


def limit_reason(source: str, limit: LimitReached | None, by: str) -> str:
    """Why a session went on elsewhere, in one sentence: `by` says what chose where."""
    resets = None if limit is None else limit.resets_at
    when = ""
    if resets:
        moment = datetime.fromtimestamp(resets, UTC)
        when = f" It resets at {moment:%H:%M} UTC on {moment:%d/%m/%Y}."
    return (
        f"{source} reached its plan's limit in the middle of this task, so Atlas moved it here by "
        f"{by}.{when}"
    )


def not_compacted(error: ExecutorError) -> str:
    """What the session says when its copy on another account could not be compacted first."""
    return f"It could not be compacted first ({error}); it goes on as it was."


def _in_order[T](
    items: Sequence[T], order: Sequence[str], key: Callable[[T], str]
) -> tuple[T, ...]:
    """`items` in the user's `order` of their keys; those it does not name after, as they were."""
    rank = {account: index for index, account in enumerate(order)}
    return tuple(sorted(items, key=lambda item: rank.get(key(item), len(rank))))


def folder_key(folder: Path | str) -> str:
    """A folder as two of its spellings compare: Windows' paths ignore case (`C:` and `c:` are one
    folder), and a trailing separator names the same one."""
    return os.path.normcase(os.path.normpath(str(folder)))


def _first_prompt(run: ExecutorRun) -> str:
    """What a session not kept yet is called: its first prompt, on one line."""
    for message in run.history:
        if isinstance(message, UserMessage) and message.text.strip():
            return " ".join(message.text.split())[:200]
    return ""


async def _login(executor: Executor) -> str:
    """The login the executor runs on now, or empty when it cannot say."""
    return (await _logged(executor))[0]


async def _logged(executor: Executor) -> tuple[str, bool]:
    """Who the executor is logged in as now and whether it is, or nobody when it cannot say."""
    try:
        found = await executor.detect()
    except Exception:  # a plugin's own code: a failed look is no login
        _logger.exception("An executor could not say who it is logged in as.")
        return "", False
    if found.account is None:
        return "", False
    return found.account.login, found.account.logged_in


def _account_view(account: ExecutorAccount, logged: tuple[str, bool]) -> AccountView:
    """Another account, as the executors tab lists it."""
    on = account_name(account.executor, account.id)
    return AccountView(account.executor, account.id, on, account.name, account.folder, *logged)


async def _log_in_as(executor: Executor, login: str) -> None:
    """Log the executor's harness into `login` on the user's browser (empty: whichever account
    the user signs in to). Raises `ExecutorError` when the login does not finish, or ends on
    another account."""
    try:
        logged = await executor.log_in(login.strip())
    except ExecutorError:
        raise
    except Exception as error:  # a plugin's own code: its failure is told
        raise ExecutorError(f"The login did not finish: {type(error).__name__}: {error}") from error
    wanted = login.strip().lower()
    if not logged.logged_in or (wanted and logged.login.lower() != wanted):
        raise ExecutorError(
            f"{executor.title} is logged in as {logged.login or 'nobody'}, not {login or 'anyone'}."
        )
