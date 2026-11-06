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
  19/10/2026; the accounts plugin's since step 22, 03/11/2026): each is the harness's own folder
  for it (Claude Code's `~/.claude-<nickname>`), with its own login, settings and sessions, under a
  nickname the user gives; Atlas keeps no credential. On the screens each is an executor of its
  own, `claude-code@<id>`, which its plugin makes on that folder (`Executor.for_account`), the
  plugin naming the accounts (`Accounts`). A folder may name the account its new sessions start
  on. A session goes on from one account to another in a copy (`use_account`): the other
  account's harness reads the first one's session and keeps its copy, and the original stays as
  it was (Atlas deletes no session, decided with the user on 03/11/2026).
- **At a plan's limit the accounts plugin says where the session goes** (`next_stop`): on the
  next account of its executor in a copy, compacted first when asked, or, by the session's own
  route, on another executor in a new session on the same card, whose first prompt is a `handoff
  packet` put together from what the first one kept, with no model call (`handoff.py`). The
  user's sessions and Atlas's delegations alike; carrying the session is this module's.
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
import secrets
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from atlas.assistant.executors import (
    ExecutorDesk,
    ExecutorRun,
    account_name,
    account_nickname,
    default_executor_effort,
    other_accounts,
    slash_view,
    split_account,
    usage_view,
)
from atlas.assistant.git import GitState, git_state
from atlas.assistant.handoff import Handoff, entries_of, handoff_packet
from atlas.assistant.model import (
    AccountList,
    AccountSwitch,
    AccountView,
    ExecutorUsage,
    InstanceActivity,
    InstanceState,
    Owner,
    SessionFolderView,
    SessionView,
    SlashCommandView,
    WorkbenchEvent,
    merged_usage,
)
from atlas.conversations import WORKBENCH, Conversation, title_from
from atlas.home import format_utc, utc_now
from atlas.models import (
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
    HandOver,
    LimitReached,
    ModelChoice,
    NextStop,
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

    # -- accounts (step 16h; since step 22, the accounts plugin's) ------------------------------

    async def accounts(self, name: str) -> AccountList:
        """`name`'s accounts: its own login, then the others the accounts plugin keeps, in its
        order, each with who its harness is logged in as. Raises `ExecutorError`."""
        executor = self._executor(name)
        plugin = self._assistant.accounts
        kept = other_accounts(plugin, name) if executor.capabilities().account_folders else []
        found = [
            (account, self._assistant.executor(account_name(name, account.id))) for account in kept
        ]
        logins = await asyncio.gather(
            _logged(executor),
            *(_nobody() if on is None else _logged(on) for _kept, on in found),
        )
        folder = str(executor.account_folder()) if executor.capabilities().account_folders else ""
        main = AccountView(name, MAIN_ACCOUNT, name, "", folder, *logins[0])
        others = tuple(
            AccountView(name, account.id, account_name(name, account.id), account.nickname,
                        str(account.folder), *logged)
            for (account, _on), logged in zip(found, logins[1:], strict=True)
        )  # fmt: skip
        hand_over = self.hand_over(name)
        rank = {account: at for at, account in enumerate(hand_over.order)}
        listed = sorted((main, *others), key=lambda view: rank.get(view.account_id, len(rank)))
        return AccountList(
            name,
            tuple(listed),
            can_add=plugin is not None and executor.capabilities().account_folders,
            failover=hand_over.enabled and bool(kept),
            compact=hand_over.compact,
        )

    def hand_over(self, name: str) -> HandOver:
        """Whether the accounts of `name`'s executor take over at a plan's limit: off with no
        accounts plugin."""
        plugin = self._assistant.accounts
        if plugin is None:
            return HandOver()
        try:
            return plugin.hand_over(split_account(name)[0])
        except Exception:  # a plugin's own code: no hand-over, logged
            _logger.exception("The accounts plugin could not say how its accounts take over.")
            return HandOver()

    # -- an account's plan limit (asked for by the user on 21/10/2026) --------------------------

    def mark_limited(self, name: str, limit: LimitReached | None) -> None:
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

    async def available(self, name: str) -> bool:
        """`name` may take a session over: installed, logged in, and not at its plan's limit."""
        if self.limited(name):
            return False
        found = self._assistant.executor(name)
        return found is not None and (await _logged(found))[1]

    def running(self, name: str) -> bool:
        """A session runs on `name` here now."""
        return any(run.name == name and run.live for run in self._runs.values())

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

    async def next_stop(self, name: str, conversation: str) -> NextStop | str | None:
        """Where the session of `conversation`, which ran on `name`, goes on at its plan's limit,
        as the accounts plugin says (`Accounts.next_stop`); `None` with no plugin, or when it
        fails, which is logged."""
        plugin = self._assistant.accounts
        if plugin is None:
            return None
        try:
            return await plugin.next_stop(name, conversation)
        except Exception:  # a plugin's own code: nothing hands the session over, logged
            _logger.exception("The accounts plugin could not say where a session goes on.")
            return None

    async def _hand_over(self, run: ExecutorRun) -> None:
        """Go on with the user's session where the accounts plugin says: on another account of
        its executor in a copy there, compacted first when asked, or on another executor in a new
        session whose first prompt is the handoff packet; from where it stopped."""
        self.mark_limited(run.name, run.limited)
        stop = await self.next_stop(run.name, run.transcript.id)
        if self._runs.get(run.id) is not run or run.working:
            return  # the user took it over meanwhile
        if stop is None:
            return
        if isinstance(stop, str):
            self.notify(InstanceActivity(run.id, "account", stop, failed=True))
            return
        kind = "route" if stop.route else "account"
        try:
            await self._go_to(run, stop, kind)
        except ExecutorError as error:
            _logger.warning("A session could not go on after a plan limit: %s", error)
            self.notify(InstanceActivity(run.id, kind, str(error), failed=True))

    async def _go_to(self, run: ExecutorRun, stop: NextStop, kind: str) -> None:
        """Carry `run`'s session to `stop`: on an account of the same executor in a copy; on
        another executor in a new session, on the same card, whose first prompt is the handoff
        packet."""
        name = stop.executor
        model, effort, note = await self.carried(run, name, stop.model, stop.effort)
        base, account = split_account(name)
        if base == split_account(run.name)[0]:
            switched = await self.use_account(run.id, account, carried=(model, effort))
            # The same session, on another account: the card says so, by a route or not.
            self.notify(InstanceActivity(switched.id, "account", switched.account or account))
            if note:
                self.notify(InstanceActivity(switched.id, "note", note))
            _logger.info("A session went on on another account after a plan limit.")
            await self._compact(switched, run, stop.compact)
            await switched.prompt(CONTINUE)
            return
        packet = await self._packet(run, name)
        await self._end(run)
        switched = await self.start(
            base,
            run.spec.folder,
            transcript=run.transcript,
            auto=run.auto,
            instance_id=run.id,
            model=model,
            effort=effort,
            account=account,
        )
        switched.set_atlas_answers(run.atlas_answers)
        destination = await asyncio.to_thread(self.label, switched.name)
        self.notify(InstanceActivity(switched.id, kind, destination))
        if note:
            self.notify(InstanceActivity(switched.id, "note", note))
        _logger.info("A session went on on another executor after a plan limit.")
        await switched.prompt(packet)

    def _inherit_route(self, session: str, conversation: str) -> None:
        """A session opened again keeps the route the user gave its card."""
        plugin = self._assistant.accounts
        kept = self._assistant.instance_store.conversation_of(session)
        if plugin is None or kept is None:
            return
        try:
            plugin.carry_route(kept, conversation)
        except Exception:  # a plugin's own code: the route stays behind, logged
            _logger.exception("The accounts plugin could not carry a session's route.")

    async def carried(
        self, run: ExecutorRun, name: str, model: str | None = None, effort: str | None = None
    ) -> tuple[str | None, str | None, str]:
        """The model and the effort `run`'s session goes on with on `name` at a switch, kept at
        every one (decided with the user on 25/10/2026), and what the screen says when they
        change: `model` and `effort` when the stop gives them; else, on the same harness, the
        model the user chose, or the one it ran as when it ran on the harness's default, which may
        be another model on another account's plan, and on another harness its default; and the
        effort it ran on, onto the levels the model takes there. A model the destination does not
        offer gives way to its default. When its models cannot be read, the user's own choices go
        as they were, and the effort it ran on only where it ran."""
        same = split_account(name)[0] == split_account(run.name)[0]
        chosen = model or (run.chosen_model if same and model is None else None)
        stop_effort = effort
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
        chose = found or offered[0]
        if effort is not None:
            effort = clamp_effort(effort, chose.efforts) if chose.efforts else None
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

    async def _packet(self, run: ExecutorRun, target: str) -> str:
        """The handoff packet for `run`'s session going on on the executor `target`."""
        source = await asyncio.to_thread(self.label, run.name)
        return await self.handoff(
            run.name,
            run.session_id or "",
            run.spec.folder,
            run.history,
            target,
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
        base, _account = split_account(name)
        executor = self._assistant.executor(name)
        title = base if executor is None else executor.title
        nickname = account_nickname(self._assistant.accounts, name)
        return f"{title} · {nickname or 'main account'}"

    def folder_account(self, name: str, folder: Path | str) -> str:
        """The account `folder`'s new sessions start on, as the accounts plugin keeps it, when
        that account is still there; else the harness's own login."""
        plugin = self._assistant.accounts
        if plugin is None:
            return MAIN_ACCOUNT
        try:
            found = plugin.folder_account(name, Path(folder))
        except Exception:  # a plugin's own code: the harness's own login, logged
            _logger.exception("The accounts plugin could not say a folder's account.")
            return MAIN_ACCOUNT
        known = {account.id for account in other_accounts(plugin, name)}
        return found if found in known else MAIN_ACCOUNT

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
        original left as it was (decided with the user on 03/11/2026: Atlas deletes no session),
        and the card stays. Never while it
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
        return [
            SessionFolderView(name, folder, updated_at, self.folder_account(base, folder))
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


async def _nobody() -> tuple[str, bool]:
    """An account whose executor is gone: logged in as nobody."""
    return "", False


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
