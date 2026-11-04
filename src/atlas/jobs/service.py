"""Routines: what the user and the agent do with them, and the scheduler that fires the ones due.

The server asks every minute; a routine due within its grace fires late, past it once for all it
missed; a routine still running does not start again; a one-shot that ran is done; a pause keeps
the slot that came due meanwhile, so resuming runs it once. Running the prompt or the command is
the executor's (the assistant's, or a command's), so this subject stays below them.

**A routine with a probe** (step 23): when it comes due its probe runs first, with no model, and
its prompt runs only when the probe printed something new, that text carried by the run as its
event. Nothing new, and no run is written: the routine just looks again at its next time. While
it runs, its probe does not, so what arrives meanwhile is heard by the next look, all at once,
never lost and never a run per event; with an interval of at least a minute, it runs at most once
a minute. A probe that fails wakes nothing; at its third failure in a row the user is told once,
as a failed run, and told again only after it worked and failed anew. A machine that slept looks
once on waking, as any routine runs once for all it missed.
"""

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Protocol

from atlas.jobs.errors import JobsError
from atlas.jobs.model import (
    Creator,
    Lateness,
    Routine,
    Run,
    RunStatus,
    Schedule,
    ScheduleKind,
    grace_seconds,
    heard,
    lateness,
    next_run,
    parse_schedule,
)
from atlas.jobs.store import RoutineStore
from atlas.trust import Scope, first_threat_message

_logger = logging.getLogger("atlas.jobs")

#: The longest a routine's name may be.
MAX_NAME_CHARS = 80


class Executor(Protocol):
    """Runs a routine's prompt or command and says how the run ended."""

    async def run(self, routine: Routine, run: Run) -> Run: ...


class Prober(Protocol):
    """Runs a routine's probe (step 23): whether it succeeded, and what it printed."""

    async def probe(self, routine: Routine) -> tuple[bool, str]: ...


class Routines:
    """The routines of one home: made, changed, paused, run now, and fired when due."""

    def __init__(self, store: RoutineStore, *, zone: tzinfo, clock: Callable[[], datetime]) -> None:
        self.store = store
        self._zone = zone
        self._clock = clock

    @property
    def zone(self) -> tzinfo:
        return self._zone

    def use_zone(self, zone: tzinfo) -> None:
        """`[time] zone` changed on the settings screen (step 15b): the next time each routine
        is scheduled; the run already scheduled keeps its moment."""
        self._zone = zone

    def create(
        self,
        name: str,
        schedule: str,
        *,
        prompt: str | None = None,
        command: str | None = None,
        created_by: Creator = Creator.USER,
        model: tuple[str, str, str | None] | None = None,
        bot: str | None = None,
        probe: str | None = None,
    ) -> Routine:
        """A new routine: a prompt the agent runs, or a command, which runs with no model. `bot` is
        the bot a prompt runs as; a command is nobody's. `probe`, for a prompt, is the command
        whose new output wakes it (step 23), on a schedule that repeats."""
        name = _name(name)
        if (prompt is None) == (command is None) or not (prompt or command or "").strip():
            raise JobsError("A routine runs a prompt or a command: give one of them.")
        if command is not None and bot is not None:
            raise JobsError("A bot's routine runs a prompt: a command runs as nobody.")
        now = self._clock()
        parsed = parse_schedule(schedule, now.astimezone(self._zone))
        probe = _probe(probe, prompt, parsed)
        if prompt is not None:
            _scan(prompt)
        routine = Routine(
            id="",
            name=name,
            schedule=parsed,
            prompt=None if prompt is None else prompt.strip(),
            command=None if command is None else command.strip(),
            enabled=True,
            created_at=now,
            created_by=created_by,
            next_run_at=next_run(parsed, self._zone, now),
            model_provider=None if model is None else model[0],
            model_name=None if model is None else model[1],
            model_effort=None if model is None else model[2],
            bot=bot,
            probe=probe,
        )
        if routine.next_run_at is None:
            raise JobsError(f"{parsed.display} is in the past, so it would never run.")
        made = self.store.add(routine)
        _logger.info("A routine was made.", extra={"routine": made.id, "by": created_by.value})
        return made

    def update(
        self,
        key: str,
        *,
        name: str | None = None,
        schedule: str | None = None,
        prompt: str | None = None,
        command: str | None = None,
        probe: str | None = None,
    ) -> Routine:
        """`probe` changes the probe of a routine that has one; empty, the routine has none."""
        routine = self.store.get(key)
        changes: dict[str, object] = {}
        if name is not None:
            changes["name"] = _name(name)
        if prompt is not None:
            if routine.prompt is None:
                raise JobsError(f"{routine.name} runs a command, not a prompt.")
            if not prompt.strip():
                raise JobsError("A routine's prompt cannot be empty.")
            _scan(prompt)
            changes["prompt"] = prompt.strip()
        if command is not None:
            if routine.command is None:
                raise JobsError(f"{routine.name} runs a prompt, not a command.")
            if not command.strip():
                raise JobsError("A routine's command cannot be empty.")
            changes["command"] = command.strip()
        if schedule is not None:
            now = self._clock()
            parsed = parse_schedule(schedule, now.astimezone(self._zone))
            changes["schedule"] = parsed
            changes["next_run_at"] = next_run(parsed, self._zone, now)
        if probe is not None or schedule is not None:
            wanted = routine.probe if probe is None else (probe.strip() or None)
            changes["probe"] = _probe(
                wanted,
                routine.prompt,
                changes.get("schedule", routine.schedule),  # type: ignore[arg-type]
            )
        changed = dataclasses.replace(routine, **changes)  # type: ignore[arg-type]
        self.store.save(changed)
        return changed

    def pause(self, key: str) -> Routine:
        routine = dataclasses.replace(self.store.get(key), enabled=False)
        self.store.save(routine)
        return routine

    def resume(self, key: str) -> Routine:
        """Run again. A slot that came due while paused is kept, so it runs once; a recurring
        routine with no next run gets one."""
        routine = self.store.get(key)
        upcoming = routine.next_run_at
        if upcoming is None and routine.schedule.kind is not ScheduleKind.ONCE:
            upcoming = next_run(routine.schedule, self._zone, self._clock())
        if upcoming is None:
            raise JobsError(f"{routine.name} ran once already; make a new one to run it again.")
        resumed = dataclasses.replace(routine, enabled=True, next_run_at=upcoming)
        self.store.save(resumed)
        return resumed

    def remove(self, key: str) -> Routine:
        routine = self.store.get(key)
        self.store.remove(routine.id)
        return routine

    def trigger(self, key: str) -> Routine:
        """Run it at the scheduler's next look, ahead of its schedule, which stays."""
        routine = dataclasses.replace(self.store.get(key), run_now=True)
        self.store.save(routine)
        return routine

    def due(self, now: datetime, running: set[str]) -> list[tuple[Routine, Lateness]]:
        """The routines to fire now, with how late each is; one still running waits."""
        found: list[tuple[Routine, Lateness]] = []
        for routine in self.store.all():
            if routine.id in running:
                continue
            if routine.run_now:
                found.append((routine, Lateness.MANUAL))
            elif routine.enabled and routine.next_run_at is not None and routine.next_run_at <= now:
                grace = grace_seconds(routine.schedule, self._zone, now)
                found.append((routine, lateness(routine.next_run_at, now, grace)))
        return found

    def begin(self, routine: Routine, kind: Lateness, now: datetime, event: str = "") -> Run:
        """Advance the routine past this run and write the run as begun. The next run is counted
        from now, so missed slots collapse into this one; a run asked for now leaves the schedule
        as it was, and a one-shot that ran is done. `event` is what its probe heard."""
        return self.store.start_run(self._advanced(routine, kind, now), now, kind, event)

    def looked(self, routine: Routine, kind: Lateness, now: datetime) -> None:
        """Advance a routine whose probe heard nothing new, as a run would, with no run."""
        self.store.advance(self._advanced(routine, kind, now))

    def _advanced(self, routine: Routine, kind: Lateness, now: datetime) -> Routine:
        upcoming, enabled = routine.next_run_at, routine.enabled
        if kind is not Lateness.MANUAL or (upcoming is not None and upcoming <= now):
            once = routine.schedule.kind is ScheduleKind.ONCE
            upcoming = None if once else next_run(routine.schedule, self._zone, now)
            enabled = enabled and not once
        return dataclasses.replace(
            routine, next_run_at=upcoming, last_run_at=now, enabled=enabled, run_now=False
        )


class Scheduler:
    """Fires due routines, each in a task of its own, and hears how each ended."""

    def __init__(
        self,
        routines: Routines,
        executor: Executor,
        *,
        clock: Callable[[], datetime],
        on_finished: Callable[[Routine, Run], None] | None = None,
        prober: Prober | None = None,
    ) -> None:
        """`prober` runs the routines' probes; without it, a routine with a probe never fires."""
        self.routines = routines
        self._executor = executor
        self._clock = clock
        self._on_finished = on_finished
        self._prober = prober
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def start(self) -> int:
        """Mark what a previous Atlas left running as unknown; returns how many."""
        return self.routines.store.abandon_running()

    def tick(self) -> list[Run]:
        """Fire every routine due now; returns the runs begun."""
        now = self._clock()
        begun: list[Run] = []
        for routine, kind in self.routines.due(now, set(self._tasks)):
            if routine.probe is not None:
                if self._prober is not None:
                    task = asyncio.create_task(self._look(routine, kind, now, self._prober))
                    self._tasks[routine.id] = task
                continue
            run = self.routines.begin(routine, kind, now)
            self._tasks[routine.id] = asyncio.create_task(self._run(routine, run))
            begun.append(run)
            _logger.info("A routine fired.", extra={"routine": routine.id, "lateness": kind.value})
        return begun

    async def _look(self, routine: Routine, kind: Lateness, now: datetime, prober: Prober) -> None:
        """A routine's probe, then its run when the probe heard something new (step 23)."""
        store = self.routines.store
        try:
            ok, output = await prober.probe(routine)
        except asyncio.CancelledError:
            self._tasks.pop(routine.id, None)
            raise
        except Exception as error:  # a probe of the user's: its failure counts, the others go on
            _logger.exception("A routine's probe failed.", extra={"routine": routine.id})
            ok, output = False, f"{type(error).__name__}: {error}"
        result = heard(store.probe_state(routine.id), ok, output)
        store.save_probe_state(result.state)
        if result.warn:
            run = self.routines.begin(routine, kind, now)
            failed = dataclasses.replace(
                run,
                status=RunStatus.ERROR,
                output=output[-2000:],
                error=f"Its probe failed {result.state.failures} times in a row.",
            )
            self._finish(routine, failed)
            return
        if not result.event:
            if kind is Lateness.MANUAL:
                # Asked for now, it says so rather than nothing.
                run = self.routines.begin(routine, kind, now)
                quiet = "Its probe heard nothing new." if ok else "Its probe failed."
                silent = dataclasses.replace(run, status=RunStatus.SILENT, output=quiet)
                self._finish(routine, silent)
                return
            self.routines.looked(routine, kind, now)
            self._tasks.pop(routine.id, None)
            return
        run = self.routines.begin(routine, kind, now, result.event)
        _logger.info("A routine's probe woke it.", extra={"routine": routine.id})
        await self._run(routine, run)

    @property
    def running(self) -> set[str]:
        return set(self._tasks)

    async def wait(self) -> None:
        """Until the runs going on end."""
        while self._tasks:
            await asyncio.wait(set(self._tasks.values()))

    async def close(self) -> None:
        """Stop the runs going on; each is written as unknown, as a restart would leave it."""
        for task in list(self._tasks.values()):
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, routine: Routine, run: Run) -> None:
        try:
            ended = await self._executor.run(routine, run)
        except asyncio.CancelledError:
            ended = dataclasses.replace(
                run,
                status=RunStatus.UNKNOWN,
                error="Atlas stopped while this run ran; whether it finished is unknown.",
            )
            self.routines.store.finish_run(dataclasses.replace(ended, ended_at=self._clock()))
            self._tasks.pop(routine.id, None)
            raise
        except Exception as error:  # the scheduler's edge: a run must not stop the others
            _logger.exception("A routine's run failed.", extra={"routine": routine.id})
            ended = dataclasses.replace(
                run, status=RunStatus.ERROR, error=f"{type(error).__name__}: {error}"
            )
        self._finish(routine, ended)

    def _finish(self, routine: Routine, ended: Run) -> None:
        ended = dataclasses.replace(ended, ended_at=self._clock())
        self.routines.store.finish_run(ended)
        self._tasks.pop(routine.id, None)
        if self._on_finished is not None:
            self._on_finished(routine, ended)


def _name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise JobsError("A routine needs a name.")
    if len(cleaned) > MAX_NAME_CHARS:
        raise JobsError(f"A routine's name has at most {MAX_NAME_CHARS} characters.")
    return cleaned


def _probe(probe: str | None, prompt: str | None, schedule: Schedule) -> str | None:
    """A routine's probe, checked: it wakes a prompt, on a schedule that repeats."""
    if probe is None or not probe.strip():
        return None
    if prompt is None:
        raise JobsError("A probe wakes a prompt: a command routine has none.")
    if schedule.kind is ScheduleKind.ONCE:
        raise JobsError("A probe looks again and again: give it a schedule that repeats.")
    return probe.strip()


def _scan(prompt: str) -> None:
    """Scan a routine's prompt for injection and exfiltration before keeping it, with Atlas's
    threat patterns, as for memory writes."""
    refused = first_threat_message(prompt, Scope.STRICT)
    if refused is not None:
        raise JobsError(refused)
