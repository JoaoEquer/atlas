"""A routine's run for this Atlas (the jobs subject's `Executor`): a prompt runs as a new
conversation nobody watches; a command runs in the working folder.

A fresh session per run, titled after the job, with no clarify and approvals denied, no background
review, the loop guards halting, and an inactivity watchdog (600 seconds idle) rather than a
wall-clock cap; a script runs for at most an hour, its output delivered as it is, and an empty one
is silent.

**A routine's probe** (step 23) runs as a routine's command does, for at most two minutes, with
`ATLAS_PROBE_STATE` naming a folder of its own in the home where it may keep what it saw. What it
heard reaches the prompt it woke as text from outside, never as the user's words. A probe named
`plugin:<plugin>:<name>` is a plugin's (SDK version 30): its Python looks, with the same folder and
the same two minutes, and what it returns is what it heard.

**An executor's routine** (step 25) runs its prompt as a new session of an outside executor in
its folder, or in a new workspace of it each run, in the mode chosen when it was made. Nobody
answers what it asks: the first question or permission stops the run, which counts as one that
needed the user, its session kept to go on by hand. A run's workspace that changed nothing is
archived; one that changed something is kept, and opened as a draft pull request when the routine
asks for one. A prompt run that met an approval nobody was there to give counts so too.

**A bot's routine** (docs/ARCHITECTURE.md, Bots) runs as the bot, in a conversation of its own
that belongs to the bot, and what it answered enters the bot's fixed conversation as a message the
bot reads and answers: the recipient is the bot itself, which receives the output as an incoming
message, acts on anything that needs action, and responds in its conversation. A silent run and a
failed one deliver nothing; the routines' list says how each run ended.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from atlas.agent import TurnOutcome
from atlas.assistant.executors import ExecutorRun, default_executor_effort
from atlas.assistant.model import ConversationRole, Owner, routine_notice
from atlas.bots import BotError
from atlas.conversations import ROUTINE, Role, StoredMessage, title_from
from atlas.home import private_folder
from atlas.jobs import (
    PROBE_SECONDS,
    DelegationError,
    Routine,
    RoutineExecutor,
    Run,
    RunStatus,
    is_silent,
)
from atlas.models import ModelChoice
from atlas.plugins import PLUGIN_PROBE, ExecutorError, ExecutorEvent, ExecutorSpec, Finished
from atlas.tools import Commands, Workspace
from atlas.trust import NOBODY_TO_APPROVE, Verdict, untrusted

if TYPE_CHECKING:
    from atlas.assistant.service import Assistant

_logger = logging.getLogger("atlas.assistant")

#: Seconds a routine's turn may go without doing anything before it is stopped.
IDLE_SECONDS = 600.0
#: Seconds a routine's command may run.
COMMAND_SECONDS = 3600
#: How often the watchdog looks.
_LOOK_SECONDS = 1.0


class _Nobody:
    """Who answers for the user in a routine's executor: nobody, since nobody was asked to."""

    async def review(self, command: str, reason: str) -> Verdict:
        return Verdict.ESCALATE

    async def answer_for_user(self, instruction: str, asked: str) -> str | None:
        return None


_NOBODY = _Nobody()


def _refused_for_want_of_someone(messages: Sequence[StoredMessage]) -> bool:
    """Whether a run met an approval nobody was there to give (step 25)."""
    return any(
        message.role is Role.TOOL and NOBODY_TO_APPROVE in message.text for message in messages
    )


class RoutineRunner:
    """Runs routines for one Atlas."""

    def __init__(self, assistant: "Assistant", *, idle: float = IDLE_SECONDS) -> None:
        self._assistant = assistant
        self._idle = idle

    async def run(self, routine: Routine, run: Run) -> Run:
        if routine.command is not None:
            return await self._command(routine.command, run)
        if routine.executor is not None:
            return await self._executor(routine, routine.executor, run)
        return await self._prompt(routine, run)

    async def probe(self, routine: Routine) -> tuple[bool, str]:
        """Run a routine's probe (the jobs subject's `Prober`): whether it ended well, and what it
        printed."""
        assistant = self._assistant
        state = private_folder(private_folder(assistant.home.probes_folder) / routine.id)
        probe = routine.probe or ""
        if probe.startswith(PLUGIN_PROBE):
            return await self._plugin_probe(probe, state)
        output, code = await self._commands().execute(
            probe, PROBE_SECONDS, {"ATLAS_PROBE_STATE": str(state)}
        )
        if code is None:
            return False, f"{output}\n(stopped after {PROBE_SECONDS} seconds)"
        return code == 0, output

    async def _plugin_probe(self, typed: str, state: Path) -> tuple[bool, str]:
        loaded = self._assistant.plugin_probe(typed)
        if loaded is None:
            return False, f"No plugin that is on brings the probe {typed}."
        try:
            heard = await asyncio.wait_for(loaded.run(state), PROBE_SECONDS)
        except TimeoutError:
            return False, f"(stopped after {PROBE_SECONDS} seconds)"
        except Exception as error:  # noqa: BLE001 - a plugin's failure is the probe's, counted
            return False, f"{type(error).__name__}: {error}"
        if not isinstance(heard, str):  # pyright: ignore[reportUnnecessaryIsInstance] - a plugin's
            return False, f"The probe {typed} returned {type(heard).__name__}, not text."
        return True, heard

    def _commands(self) -> Commands:
        assistant = self._assistant
        return Commands(
            Workspace(assistant.workspace),
            assistant.home.commands_folder,
            path=lambda: assistant.programs,
        )

    async def _prompt(self, routine: Routine, run: Run) -> Run:
        assistant = self._assistant
        stored = assistant.conversations.create(source=ROUTINE, title=routine.name, bot=routine.bot)
        if routine.model_provider is not None and routine.model_name is not None:
            choice = ModelChoice(routine.model_provider, routine.model_name, routine.model_effort)
            assistant.conversations.set_model(stored.id, choice)
        conversation = assistant.conversation(stored.id, role=ConversationRole.ROUTINE)
        run = dataclasses.replace(run, conversation_id=stored.id)
        try:
            conversation.send(self._with_event(routine, run))
            while conversation.running:
                await asyncio.sleep(_LOOK_SECONDS)
                idle = (assistant.clock() - conversation.last_activity).total_seconds()
                if conversation.running and idle > self._idle:
                    await conversation.interrupt()
                    _logger.warning(
                        "A routine was stopped without activity.", extra={"routine": routine.id}
                    )
                    return dataclasses.replace(
                        run,
                        status=RunStatus.ERROR,
                        error=f"Stopped after {self._idle:.0f} seconds without activity.",
                    )
            await conversation.wait()
        finally:
            await conversation.close()
            assistant.forget(stored.id)
        ended = conversation.last_ended
        if ended is None:
            return dataclasses.replace(run, status=RunStatus.ERROR, error="The run never answered.")
        if ended.outcome is not TurnOutcome.ANSWERED:
            return dataclasses.replace(
                run, status=RunStatus.ERROR, output=ended.text, error=ended.error or ended.outcome
            )
        status = RunStatus.SILENT if is_silent(ended.text) else RunStatus.OK
        if status is RunStatus.OK and routine.bot is not None:
            self._deliver(routine.bot, routine.name, ended.text)
        if _refused_for_want_of_someone(assistant.conversations.messages(stored.id)):
            status = RunStatus.NEEDED
        return dataclasses.replace(run, status=status, output=ended.text)

    async def _executor(self, routine: Routine, chosen: RoutineExecutor, run: Run) -> Run:
        """Run the prompt as a session of an outside executor in its folder, nobody there to
        answer what it asks."""
        assistant = self._assistant
        found = assistant.executor(chosen.name)
        if found is None:
            error = f"{chosen.name} is not installed on this computer, or is turned off."
            return dataclasses.replace(run, status=RunStatus.ERROR, error=error)
        folder = Path(chosen.folder)
        if not folder.is_dir():
            return dataclasses.replace(
                run, status=RunStatus.ERROR, error=f"The folder {folder} is not there."
            )
        workspaces = assistant.task_workspaces if chosen.workspace else None
        if chosen.workspace:
            if workspaces is None:
                error = "Workspaces are not available here."
                return dataclasses.replace(run, status=RunStatus.ERROR, error=error)
            try:
                folder, _ = await workspaces.make(folder, f"{title_from(routine.name)[:40]}")
            except DelegationError as refused:
                return dataclasses.replace(run, status=RunStatus.ERROR, error=str(refused))
        stored = assistant.conversations.create(source=ROUTINE, title=routine.name)
        run = dataclasses.replace(run, conversation_id=stored.id)
        workbench = assistant.workbench
        effort = await default_executor_effort(found, None)
        session = ExecutorRun(
            instance_id=workbench.new_id(),
            executor=found,
            name=chosen.name,
            spec=ExecutorSpec(folder, auto=chosen.auto, effort=effort),
            transcript=assistant.conversations.open(stored.id),
            owner=Owner.ATLAS,
            answering=_NOBODY,
            desk=None,
            notify=workbench.notify,
            record=assistant.instance_store,
            clock=assistant.clock,
        )
        asked: list[str] = []
        last = [assistant.clock()]

        def nobody(what: str) -> None:
            asked.append(what)
            stopping.append(asyncio.ensure_future(session.stop()))

        def heard(_event: ExecutorEvent) -> None:
            last[0] = assistant.clock()

        stopping: list[asyncio.Future[None]] = []
        session.on_nobody = nobody
        prompt = self._with_event(routine, run)
        finished: Finished | None = None
        error: str | None = None
        workbench.adopt(session)
        turn = asyncio.ensure_future(session.run(prompt, heard))
        try:
            while not turn.done():
                await asyncio.sleep(_LOOK_SECONDS)
                idle = (assistant.clock() - last[0]).total_seconds()
                if not turn.done() and not session.waiting and idle > self._idle:
                    error = f"Stopped after {self._idle:.0f} seconds without activity."
                    await session.stop()
            finished = await turn
        except ExecutorError as failure:
            error = error or str(failure)
        except Exception as failure:  # a plugin's own code: its failure ends the run, said
            _logger.exception("An executor's routine failed.", extra={"routine": routine.id})
            error = error or f"{type(failure).__name__}: {failure}"
        finally:
            await asyncio.gather(*stopping, return_exceptions=True)
            await session.stop()
            workbench.drop(session)
        said = finished.text if finished is not None else ""
        if asked:
            ended = dataclasses.replace(
                run,
                status=RunStatus.NEEDED,
                output=f"It stopped to ask: {asked[0]}\nIts session is kept: open it in "
                "Executors to answer and go on.",
            )
        elif error is not None or finished is None or finished.failed:
            failure = error or (said if finished is not None else "") or session.failure
            ended = dataclasses.replace(
                run, status=RunStatus.ERROR, output=said, error=failure or "It ended with nothing."
            )
        else:
            status = RunStatus.SILENT if is_silent(said) else RunStatus.OK
            ended = dataclasses.replace(run, status=status, output=said)
        if workspaces is not None:
            became = await workspaces.finish(folder, routine.name, draft=chosen.draft)
            if became:
                ended = dataclasses.replace(ended, output=f"{ended.output}\n\n{became}".strip())
        return ended

    @staticmethod
    def _with_event(routine: Routine, run: Run) -> str:
        prompt = routine.prompt or ""
        if run.event:
            heard = untrusted(f"probe: {routine.name}", run.event)
            prompt = f"{prompt}\n\nWhat its probe heard, which started this run:\n{heard}"
        return prompt

    def _deliver(self, bot: str, name: str, output: str) -> None:
        """Hand a run's answer to the bot's fixed conversation, where it starts a turn now or
        after the one running."""
        assistant = self._assistant
        try:
            fixed = assistant.bot_conversation(bot)
        except BotError as error:
            _logger.warning("A routine's result reached no bot: %s", error)
            return
        assistant.conversation(fixed).notice(routine_notice(name, output))

    async def _command(self, command: str, run: Run) -> Run:
        output, code = await self._commands().execute(command, COMMAND_SECONDS)
        if code is None:
            error = f"Stopped after {COMMAND_SECONDS} seconds, with every process it started."
            return dataclasses.replace(run, status=RunStatus.ERROR, output=output, error=error)
        if code != 0:
            return dataclasses.replace(
                run, status=RunStatus.ERROR, output=output, error=f"exit code {code}"
            )
        quiet = output.strip() in ("", "(no output)")
        return dataclasses.replace(
            run, status=RunStatus.SILENT if quiet else RunStatus.OK, output=output
        )
