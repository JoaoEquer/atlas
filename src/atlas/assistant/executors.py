"""An executor instance for this Atlas: an executor plugin's instance on the workbench, what it does
kept as a conversation of its own, and what it asks answered by the user, or by Atlas for them.

Decided with the user on 12/10/2026 (docs/ARCHITECTURE.md, Outside executors): the goal is that
nothing the user would answer Claude Code in a terminal is lost; it reaches them through Atlas.
Since step 16c each executor is a plugin's (`atlas.plugins.sdk`), and this run is its host: the
plugin only carries, and who answers what is decided here. The run lives from its start to its
stop, with as many turns as it is prompted: by the user on its card, or by Atlas through
`delegate`, whose task is one turn.

- **What it does is kept**: its words, its tool calls and their results become its stored
  conversation, which the screen opens.
- **What it asks waits for an answer, with no deadline**: the defect most reported by the products
  that delegate in the background is the permission request lost or answered in silence. A request
  is answered by the user, on its card, or, with "Atlas answers" on, by Atlas: a permission by the
  guardian, which hands the user what it is unsure of; a question by a model that reads what the
  user asked for. In the harness's automatic mode a permission it still asks is the user's alone:
  what its own rules and the user's settings keep for a person (decided with the user on
  17/10/2026).
- **A refusal does not end the task**: the executor reads why and goes on.
- **Each turn is recorded** (`atlas.jobs`, the delegation record): its prompt and steers, what it
  asked and who answered, its final text and the files its tool calls wrote.
"""

import asyncio
import dataclasses
import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol, cast

from atlas.assistant.model import (
    ExecutorUsage,
    InstanceActivity,
    InstanceChanged,
    InstanceState,
    Owner,
    SlashCommandView,
    UsageWindowView,
    WorkbenchEvent,
)
from atlas.conversations import Conversation
from atlas.home import format_utc, utc_now
from atlas.jobs import Answerer, Exchange, InstanceRecord, InstanceStore, TurnRecord
from atlas.models import (
    AssistantMessage,
    Attachment,
    Message,
    ToolCall,
    ToolResult,
    UserMessage,
    default_effort,
)
from atlas.plugins import (
    Answers,
    Called,
    CommandsChanged,
    Compacted,
    Executor,
    ExecutorError,
    ExecutorEvent,
    ExecutorFile,
    ExecutorInstance,
    ExecutorSpec,
    Finished,
    LimitReached,
    Permission,
    PlanUsage,
    Question,
    Returned,
    Said,
    SlashCommand,
    Started,
    Thought,
    UsageChanged,
    UsageWindow,
)
from atlas.plugins import Verdict as ExecutorVerdict
from atlas.trust import Verdict

_logger = logging.getLogger("atlas.assistant")

#: The credentials Claude Code names when it bills the user's own login.
_OWN_LOGIN = ("none", "/login managed key", "")
#: What joins an executor's name and one of its other accounts' id into the name of its executor
#: on that account (`claude-code@1a2b3c4d`, step 16h): on the screens, the workbench and the
#: delegation record, it is one executor more.
ACCOUNT_MARK = "@"


def account_name(executor: str, account: str) -> str:
    """The name of `executor` on `account`; empty `account`, its own (the main account)."""
    return f"{executor}{ACCOUNT_MARK}{account}" if account else executor


def split_account(name: str) -> tuple[str, str]:
    """An executor's name and its account's id from the name of its executor on that account;
    empty for the main account."""
    executor, _, account = name.partition(ACCOUNT_MARK)
    return executor, account


async def default_executor_effort(executor: Executor, model: str | None) -> str | None:
    """The effort an instance Atlas starts runs on when nobody chose one: Atlas's default
    (`DEFAULT_EFFORT`, decided by the user on 17/10/2026) onto the levels of `model`, or of the
    harness's default model, the first it lists, as its plugin reads them. `None` for an executor
    with no models to choose, a model that takes no effort, or a list that cannot be read: the
    harness's own default then, rather than no start."""
    if not executor.capabilities().models:
        return None
    try:
        offered = list(await executor.models())
    except ExecutorError as error:
        _logger.warning("An executor's models could not be read for its effort: %s", error)
        return None
    except Exception:  # a plugin's own code: the harness keeps its own effort
        _logger.exception("An executor's models could not be read for its effort.")
        return None
    chosen = next(
        (found for found in offered if found.id == model),
        offered[0] if offered and model is None else None,
    )
    return None if chosen is None else default_effort(None, chosen.efforts)


#: The name a harness's permission modes give its mode that asks no permission (SDK version 28).
AUTO_MODE = "auto"


def _mode_of(modes: tuple[str, ...], auto: bool) -> str:
    """The mode an instance runs in when only its automatic mode was switched: that one, else its
    default; empty for a harness with no modes."""
    if not modes:
        return ""
    return AUTO_MODE if auto and AUTO_MODE in modes else modes[0]


class ExecutorDesk(Protocol):
    """How what an executor instance asks reaches the user: its card. Each waits as long as the
    user takes."""

    async def ask(self, asker: InstanceChanged, questions: Sequence[Question]) -> Answers: ...

    async def approve(self, asker: InstanceChanged, permission: Permission) -> ExecutorVerdict: ...


class Answering(Protocol):
    """Atlas answering for the user: the guardian, and a model that reads what the user asked
    for (the conversation an instance works for, or the instance's own prompts)."""

    async def review(self, command: str, reason: str) -> Verdict: ...

    async def answer_for_user(self, instruction: str, asked: str) -> str | None: ...


#: What the model is told when it answers a question for the user.
ANSWER_FOR_USER = (
    "{title} is working on a task you delegated in this conversation, and it asks the user the "
    "questions below. The user chose to let you answer for them: answer as they would, from what "
    "they asked for in this conversation. Reply with only a JSON object mapping each question's "
    "exact text to the label of the option you choose (a list of labels for a multiple choice), or "
    "to a short answer of your own when no option fits."
)


class ExecutorRun:
    """One executor instance, and the host its plugin's instance reports to."""

    def __init__(
        self,
        *,
        instance_id: str,
        executor: Executor,
        name: str,
        spec: ExecutorSpec,
        transcript: Conversation,
        owner: Owner,
        answering: Answering,
        desk: ExecutorDesk | None,
        notify: Callable[[WorkbenchEvent], None],
        account: str = "",
        parent_id: str = "",
        subagent_id: str = "",
        record: InstanceStore | None = None,
        clock: Callable[[], datetime] = utc_now,
        on_limit: "Callable[[ExecutorRun], None] | None" = None,
        on_copied: "Callable[[ExecutorRun], None] | None" = None,
    ) -> None:
        """`on_limit` hears a turn from its card stopped at the account's plan limit;
        `on_copied`, a turn that ended with its session a copy of another account's, the one it
        `replaces`."""
        self.id = instance_id
        self._on_limit = on_limit
        self._on_copied = on_copied
        self.name = name
        self.executor = executor
        self.spec = spec
        self.transcript = transcript
        self.owner = owner
        self.answering = answering
        self.parent_id = parent_id
        self.subagent_id = subagent_id
        self.account = account
        self._desk = desk
        self._notify = notify
        self._heard: Callable[[ExecutorEvent], None] = lambda _event: None
        self._turn: asyncio.Task[Finished | None] | None = None
        self.instance: ExecutorInstance | None = None
        self.state = InstanceState.STARTING
        self.auto = spec.auto
        #: "Atlas answers", off by default (step 16c: step 13's "Atlas decides", as a mode).
        self.atlas_answers = False
        #: Requests waiting for an answer: the watchdog leaves a waiting executor alone.
        self.waiting = 0
        self.last_tool: str | None = None
        self.tools = 0
        self.problem = ""
        # A copy's id is its own, which its harness says when it starts: another account's
        # session goes on in a copy too.
        copy = spec.fork or spec.resume_from is not None
        self.session_id: str | None = None if copy else spec.resume
        #: The other account's session its copy replaces, until it is deleted there (asked for
        #: by the user on 22/10/2026); a fork the user asked for replaces none.
        self.replaces: str | None = (
            spec.resume if spec.resume_from is not None and not spec.fork else None
        )
        self.model = ""
        #: The slash commands its channel takes, once its harness said (step 16f): their
        #: names, on its card, and described, for the box (version 10 of the SDK).
        self.commands: tuple[str, ...] = ()
        self.slash: tuple[SlashCommandView, ...] = ()
        self.finished: Finished | None = None
        #: The account's plan limit stopped it (decided with the user on 12/10/2026).
        self.limited: LimitReached | None = None
        #: Why the instance could not go on, as its plugin said.
        self.failure = ""
        capabilities = executor.capabilities()
        self.can_steer = capabilities.steer
        self.can_auto = capabilities.auto
        self.can_switch = capabilities.accounts
        self.can_model = capabilities.models
        self.can_files = capabilities.files
        #: Its harness's permission modes (SDK version 28), and the one it runs in: `auto` when
        #: its automatic mode is on, else its default until another is chosen.
        self.modes = capabilities.modes
        self.mode = _mode_of(self.modes, spec.auto)
        #: The model and the effort chosen for it; `None`, its harness's default.
        self.chosen_model = spec.model
        self.effort = spec.effort
        self._record = record
        self._clock = clock
        self.started_at = clock()
        #: The turn running, as the record keeps it.
        self._current: TurnRecord | None = None

    @property
    def title(self) -> str:
        return self.executor.title

    @property
    def history(self) -> list[Message]:
        """What it said and did, as kept."""
        return self.transcript.history

    @property
    def account_id(self) -> str:
        """The other account it runs on (step 16h); empty, the main account."""
        return split_account(self.name)[1]

    @property
    def working(self) -> bool:
        return self.state is InstanceState.WORKING

    @property
    def live(self) -> bool:
        return self.state in (InstanceState.STARTING, InstanceState.IDLE, InstanceState.WORKING)

    def view(self) -> InstanceChanged:
        return InstanceChanged(
            instance_id=self.id,
            executor=self.name,
            title=self.title,
            owner=self.owner.value,
            folder=str(self.spec.folder),
            account=self.account,
            state=self.state.value,
            auto=self.auto,
            atlas_answers=self.atlas_answers,
            waiting=self.waiting,
            last_tool=self.last_tool,
            tools=self.tools,
            problem=self.problem,
            parent_id=self.parent_id,
            transcript_id=self.transcript.id,
            subagent_id=self.subagent_id,
            session_id=self.session_id or "",
            steer=self.can_steer,
            can_auto=self.can_auto,
            accounts=self.can_switch,
            commands=self.commands,
            can_model=self.can_model,
            model=self.model,
            chosen_model=self.chosen_model or "",
            effort=self.effort or "",
            account_id=self.account_id,
            mode=self.mode,
            modes=self.modes,
        )

    def changed(self) -> None:
        self._notify(self.view())
        self._keep_record()

    # -- the record -------------------------------------------------------------------------------

    def _keep_record(self) -> None:
        store = self._record
        if store is None:
            return
        store.save(
            InstanceRecord(
                id=self.id,
                executor=self.name,
                title=self.title,
                owner=self.owner.value,
                folder=str(self.spec.folder),
                account=self.account,
                state=self.state.value,
                started_at=self.started_at,
                updated_at=self._clock(),
                session_id=self.session_id or "",
                parent_id=self.parent_id,
                subagent_id=self.subagent_id,
                transcript_id=self.transcript.id,
            )
        )

    def _begin_turn(self, text: str) -> None:
        store = self._record
        number = 1 if store is None else store.turn_count(self.id) + 1
        self._current = TurnRecord(
            self.id,
            number,
            text,
            str(self.spec.folder),
            self._clock(),
            session_id=self.session_id or "",
        )
        self._keep_turn()

    def _exchange(self, exchange: Exchange) -> None:
        current = self._current
        if current is not None:
            self._current = dataclasses.replace(current, exchanges=(*current.exchanges, exchange))
            self._keep_turn()

    def _end_turn(self) -> None:
        current = self._current
        if current is None:
            return
        finished = self.finished
        self._current = dataclasses.replace(
            current,
            ended_at=self._clock(),
            text=finished.text if finished is not None else self.problem,
            failed=finished.failed if finished is not None else True,
            reason=finished.reason if finished is not None else "ended without a result",
            session_id=self.session_id or "",
        )
        self._keep_turn()
        self._current = None

    def _keep_turn(self) -> None:
        if self._record is not None and self._current is not None:
            self._record.save_turn(self._current)

    # -- its life ---------------------------------------------------------------------------------

    async def open(self) -> None:
        """Start its harness. Raises `ExecutorError`; a plugin's own failure is told the same."""
        try:
            self.instance = await self.executor.start(self.spec, self)
        except ExecutorError as error:
            self._fail(str(error))
            raise
        except Exception as error:  # a plugin's own code: its failure is told, as the SDK's
            _logger.exception("An executor could not start.", extra={"instance": self.id})
            self._fail(f"{type(error).__name__}: {error}")
            raise ExecutorError(self.problem) from error
        self.state = InstanceState.IDLE
        self.changed()

    async def turn(
        self, text: str, attachments: Sequence[Attachment] = (), files: Sequence[ExecutorFile] = ()
    ) -> Finished | None:
        """Run one turn on `text`, kept, and how it ended (`None` when it ended without saying).
        `attachments` are the user's files as Atlas keeps them, and `files` the same as its
        harness gets them (step 16i). Raises `ExecutorError` when its harness ends without a
        result."""
        instance = self.instance
        if instance is None:
            raise ExecutorError(f"{self.title} is not running.")
        self.finished, self.limited, self.failure, self.problem = None, None, "", ""
        self.state = InstanceState.WORKING
        self.history.append(UserMessage(text, tuple(attachments)))
        self.transcript.save()
        self._notify(InstanceActivity(self.id, "prompted", text, attachments=tuple(attachments)))
        self.changed()
        self._begin_turn(text)
        try:
            if files:
                await instance.prompt(text, files=files)
            else:
                await instance.prompt(text)
        except ExecutorError as error:
            self.problem = str(error)
            raise
        except Exception as error:  # a plugin's own code: its failure ends the turn, told
            _logger.exception("An executor's turn failed.", extra={"instance": self.id})
            self.problem = f"{type(error).__name__}: {error}"
            raise ExecutorError(self.problem) from error
        finally:
            self._copied()
            if self.state is InstanceState.WORKING:
                self.state = InstanceState.IDLE
            self._end_turn()
            self.changed()
        return self.finished

    def _copied(self) -> None:
        """After a turn: its copy has an id of its own now, and may be kept by its harness."""
        session, replaced = self.session_id, self.replaces
        if self._on_copied is not None and replaced and session and session != replaced:
            self._on_copied(self)

    async def run(self, task: str, on_event: Callable[[ExecutorEvent], None]) -> Finished | None:
        """A delegated task: start the instance, run the task as one turn, and how it ended."""
        self._heard = on_event
        await self.open()
        return await self.turn(task)

    async def prompt(
        self, text: str, attachments: Sequence[Attachment] = (), files: Sequence[ExecutorFile] = ()
    ) -> bool:
        """A prompt from its card, with the files attached as for `turn`: a steer into the turn
        that runs (`True`), or a new turn, run in the background (`False`). Raises
        `ExecutorError` when it can take neither."""
        if files and not self.can_files:
            raise ExecutorError(f"{self.title} takes no files.")
        if self.working:
            instance = self.instance
            if instance is None or not self.can_steer:
                raise ExecutorError(f"{self.title} takes no message while it works.")
            if files:
                await instance.steer(text, files=files)
            else:
                await instance.steer(text)
            self.history.append(UserMessage(text, tuple(attachments)))
            self.transcript.save()
            self._notify(InstanceActivity(self.id, "steered", text, attachments=tuple(attachments)))
            self._exchange(Exchange("steer", text))
            return True
        if self.state is not InstanceState.IDLE:
            raise ExecutorError(f"{self.title} is not running.")
        # Working from now, not from when the task first runs: a steer or an account switch asked
        # at once sees the turn.
        self.state = InstanceState.WORKING
        self._turn = asyncio.create_task(self._card_turn(text, attachments, files))
        return False

    async def _card_turn(
        self, text: str, attachments: Sequence[Attachment], files: Sequence[ExecutorFile]
    ) -> Finished | None:
        try:
            return await self.turn(text, attachments, files)
        except ExecutorError:
            return None
        finally:
            if self.limited is not None and self._on_limit is not None:
                self._on_limit(self)

    async def set_auto(self, on: bool) -> None:
        """Its own mode that asks no permission, on or off."""
        if not self.can_auto:
            raise ExecutorError(f"{self.title} has no automatic mode Atlas can switch.")
        if self.instance is not None and self.live:
            await self.instance.set_auto(on)
        self.auto = on
        self.mode = _mode_of(self.modes, on)
        self.changed()

    async def set_mode(self, mode: str) -> None:
        """One of its harness's permission modes; `auto` is its automatic mode, as `set_auto`."""
        if mode not in self.modes:
            raise ExecutorError(f"{self.title} has no mode {mode} Atlas switches.")
        if self.instance is not None and self.live:
            await self.instance.set_mode(mode)
        self.mode = mode
        self.auto = mode == AUTO_MODE
        self.changed()

    async def set_model(self, model: str | None, effort: str | None) -> None:
        """The model and the effort from its next request on; `None`, its harness's default.
        Kept for the instance that takes its place (another account, another folder)."""
        if not self.can_model:
            raise ExecutorError(f"{self.title} has no models Atlas can switch.")
        if self.instance is not None and self.live:
            await self.instance.set_model(model, effort)
        self.chosen_model, self.effort = model, effort
        self.changed()

    def set_atlas_answers(self, on: bool) -> None:
        self.atlas_answers = on
        self.changed()

    async def stop(self) -> None:
        """Stop it for good, with what it asked still open withdrawn."""
        instance = self.instance
        if instance is not None and self.state is not InstanceState.ENDED:
            try:
                await instance.stop()
            except Exception:  # a plugin's own code: the stop goes on, logged
                _logger.exception("An executor did not stop cleanly.", extra={"instance": self.id})
        if self._turn is not None and not self._turn.done():
            await asyncio.gather(self._turn, return_exceptions=True)
        if self.state is not InstanceState.FAILED:
            self.state = InstanceState.ENDED
        self.changed()

    def _fail(self, reason: str) -> None:
        self.state, self.problem = InstanceState.FAILED, reason
        self.changed()

    # -- the host ---------------------------------------------------------------------------------

    def event(self, event: ExecutorEvent) -> None:
        self._heard(event)
        self._keep(event)
        if isinstance(event, Started):
            self.session_id = event.session or self.session_id
            self.model = event.model or self.model
            if event.credential not in _OWN_LOGIN:
                _logger.info("%s bills %s.", self.title, event.credential)
            if event.commands and event.commands != self.commands:
                self.commands = event.commands
                if {command.name for command in self.slash} != set(event.commands):
                    self.slash = tuple(SlashCommandView(name) for name in event.commands)
            # Its session is known now: the executors tab lists it under its id.
            self.changed()
        elif isinstance(event, Called):
            self.last_tool, self.tools = event.tool, self.tools + 1
            current = self._current
            if current is not None and event.files:
                files = tuple(dict.fromkeys((*current.files, *event.files)))
                self._current = dataclasses.replace(current, files=files)
            self.changed()
        elif isinstance(event, LimitReached):
            # The rate-limit event says when it resets; the error that follows does not.
            if self.limited is None or event.resets_at is not None:
                self.limited = event
        elif isinstance(event, Finished):
            self.finished = event
            self.session_id = event.session or self.session_id
        elif isinstance(event, CommandsChanged):
            self.slash = slash_view(event.commands)
            names = tuple(command.name for command in event.commands)
            if names != self.commands:
                # The box reads them again when the names on the card change.
                self.commands = names
                self.changed()
        elif isinstance(event, UsageChanged):
            # The account's, not the instance's: the side panel shows it for the executor.
            told = ExecutorUsage(self.name, windows_view(event.windows))
            self._notify(dataclasses.replace(told, read_at=format_utc(self._clock())))

    def failed(self, reason: str) -> None:
        self.failure = reason
        if self.state is not InstanceState.ENDED:
            self._fail(reason)

    async def ask(self, questions: Sequence[Question]) -> Answers:
        asked = "\n".join(question.text for question in questions)
        self._waiting(1)
        answers: Answers = None
        by = Answerer.NOBODY
        try:
            if self.atlas_answers:
                answers = await self._answer_for_user(questions)
                by = Answerer.ATLAS if answers is not None else by
            if answers is None and self._desk is not None:
                answers = await self._desk.ask(self.view(), questions)
                by = Answerer.USER if answers is not None else by
            return answers
        finally:
            self._waiting(-1)
            said = "(unanswered)" if answers is None else json.dumps(answers, ensure_ascii=False)
            self._exchange(Exchange("question", asked, said, by))

    async def approve(self, permission: Permission) -> ExecutorVerdict:
        asked = f"{permission.tool}: {permission.summary}"
        self._waiting(1)
        verdict: ExecutorVerdict | None = None
        by = Answerer.NOBODY
        try:
            # In its automatic mode, what it still asks is what its own rules and the user's
            # settings keep for a person: the user answers, never the guardian.
            if self.atlas_answers and not self.auto:
                verdict = await self._review(permission)
                by = Answerer.ATLAS if verdict is not None else by
            if verdict is None and self._desk is not None:
                verdict = await self._desk.approve(self.view(), permission)
                by = Answerer.USER
            if verdict is None:
                verdict = ExecutorVerdict(
                    False, "Nobody is there to answer this request, so it is refused."
                )
            return verdict
        finally:
            self._waiting(-1)
            said = "(unanswered)" if verdict is None else _verdict_text(verdict)
            self._exchange(Exchange("permission", asked, said, by))

    def _waiting(self, step: int) -> None:
        self.waiting += step
        self.changed()

    def _keep(self, event: ExecutorEvent) -> None:
        """What the executor did, into its conversation, and onto its card."""
        history = self.history
        match event:
            case Said(text):
                history.append(AssistantMessage(text))
                self._notify(InstanceActivity(self.id, "said", text))
            case Thought(text):
                history.append(AssistantMessage(reasoning=text))
                self._notify(InstanceActivity(self.id, "thought", text))
            case Called(call_id, tool, arguments):
                history.append(AssistantMessage(tool_calls=(ToolCall(call_id, tool, arguments),)))
                called = InstanceActivity(
                    self.id, "called", f"{tool} {arguments}", call_id=call_id, tool=tool
                )
                self._notify(called)
            case Returned(call_id, text, failed):
                history.append(ToolResult(call_id, text))
                self._notify(InstanceActivity(self.id, "returned", text, failed, call_id=call_id))
            case Compacted():
                # The harness's own context, not Atlas's record of it: only the screen is told.
                self._notify(InstanceActivity(self.id, "compacted", event.text))
                return
            case _:
                return
        self.transcript.save()

    # -- Atlas answers ----------------------------------------------------------------------------

    async def _answer_for_user(self, questions: Sequence[Question]) -> Answers:
        """Atlas's answers, or `None` when the user must give them."""
        asked = [_asked(question) for question in questions]
        reply = await self.answering.answer_for_user(
            ANSWER_FOR_USER.format(title=self.title), json.dumps(asked, ensure_ascii=False)
        )
        answers = _answers(reply)
        if answers is None:
            return None
        self._notify(InstanceActivity(self.id, "answered", json.dumps(answers, ensure_ascii=False)))
        return answers

    async def _review(self, permission: Permission) -> ExecutorVerdict | None:
        """The guardian's verdict, or `None` when it is unsure and the user decides."""
        tool = permission.tool
        reason = permission.title or f"{self.title} asks to use {tool}"
        verdict = await self.answering.review(f"{tool}: {permission.summary}", reason)
        if verdict is Verdict.APPROVE:
            self._notify(InstanceActivity(self.id, "answered", f"allowed {tool}"))
            return ExecutorVerdict(True)
        if verdict is Verdict.DENY:
            self._notify(InstanceActivity(self.id, "answered", f"denied {tool}"))
            return ExecutorVerdict(
                False, "Atlas's guardian refused this as dangerous. Find another way, or stop."
            )
        return None


def _verdict_text(verdict: ExecutorVerdict) -> str:
    if verdict.allow:
        return "allowed"
    return f"denied: {verdict.reason}" if verdict.reason else "denied"


def _asked(question: Question) -> dict[str, object]:
    """A question as the model reads it, in Claude Code's own shape."""
    options: list[dict[str, str]] = []
    for option in question.options:
        shown = {"label": option.label}
        if option.description:
            shown["description"] = option.description
        options.append(shown)
    return {
        "question": question.text,
        "header": question.header,
        "options": options,
        "multiSelect": question.multiple,
    }


def _answers(reply: str | None) -> dict[str, str | list[str]] | None:
    """The model's answers, read from its JSON; `None` when there is nothing usable."""
    if not reply:
        return None
    text = reply.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        parsed: object = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    answers: dict[str, str | list[str]] = {}
    for question, answer in cast("dict[str, object]", parsed).items():
        if isinstance(answer, str):
            answers[question] = answer
        elif isinstance(answer, list):
            answers[question] = [str(item) for item in cast("list[object]", answer)]
    return answers or None


def slash_view(commands: Sequence[SlashCommand]) -> tuple[SlashCommandView, ...]:
    """A harness's slash commands for the box: its own among the commands, the rest (skills,
    and the commands a user, project or plugin defined) among the skills."""
    return tuple(
        SlashCommandView(c.name, c.description, c.hint, "command" if c.builtin else "skill")
        for c in commands
    )


def usage_view(name: str, usage: PlanUsage, read_at: str) -> ExecutorUsage:
    """An executor's plan usage, as its plugin read it, for the screens."""
    return ExecutorUsage(
        name,
        windows_view(usage.windows),
        usage.plan,
        usage.unavailable,
        read_at,
        # A plugin written before version 18 has no such field.
        getattr(usage, "extra_usage", None),
    )


def windows_view(windows: Sequence[UsageWindow]) -> tuple[UsageWindowView, ...]:
    return tuple(
        UsageWindowView(w.id, w.title, w.used, w.resets_at, w.minutes, w.model) for w in windows
    )
