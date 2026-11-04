"""A routine's probe (step 23): a command run with no model when the routine comes due, whose new
output wakes the prompt and nothing else does.

The prober is a stand-in that prints what it is told: what is proven here is what a probe's
output means and what is written about it. The real probe (a command with its own folder) is
proven in tests/assistant/test_routines.py.
"""

import asyncio
import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.home import Database, migrate
from atlas.jobs import (
    MAX_EVENT_CHARS,
    PROBE_FAILURES,
    SCHEMA,
    JobsError,
    Lateness,
    ProbeState,
    Routine,
    Routines,
    RoutineStore,
    Run,
    RunStatus,
    Scheduler,
    heard,
)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 4, 13, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class _Executor:
    """Records the events that woke each run."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def run(self, routine: Routine, run: Run) -> Run:
        self.events.append(run.event)
        return dataclasses.replace(run, status=RunStatus.OK, output="Answered.")


class _Prober:
    """Answers each probe with the next of `said`: (succeeded, printed)."""

    def __init__(self, *said: tuple[bool, str]) -> None:
        self.said = list(said)
        self.probed = 0

    async def probe(self, routine: Routine) -> tuple[bool, str]:
        self.probed += 1
        return self.said.pop(0)


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def routines(tmp_path: Path, clock: _Clock) -> Routines:
    database = Database(tmp_path / "atlas.db")
    migrate(database, (SCHEMA,))
    return Routines(RoutineStore(database), zone=UTC, clock=clock)


def _looks(scheduler: Scheduler, clock: _Clock, times: int) -> None:
    async def scenario() -> None:
        for _ in range(times):
            clock.now += timedelta(minutes=5)
            scheduler.tick()
            await scheduler.wait()

    asyncio.run(scenario())


# -- what one run of a probe means ---------------------------------------------------------------


def test_new_output_wakes_the_same_output_twice_wakes_once_and_nothing_wakes_nothing() -> None:
    first = heard(ProbeState("r1"), True, "  1 new mail from ana@example.com\n")
    assert first.event == "1 new mail from ana@example.com"
    again = heard(first.state, True, "1 new mail from ana@example.com")
    assert again.event == ""
    assert heard(again.state, True, "").event == ""
    assert heard(again.state, True, "(no output)").event == ""
    assert heard(again.state, True, "2 new mails").event == "2 new mails"


def test_a_long_output_wakes_with_its_end() -> None:
    long = "x" * MAX_EVENT_CHARS + "the end"
    assert heard(ProbeState("r1"), True, long).event.endswith("the end")
    assert len(heard(ProbeState("r1"), True, long).event) == MAX_EVENT_CHARS


def test_failures_in_a_row_warn_once_and_again_only_after_it_worked() -> None:
    result = heard(ProbeState("r1"), False, "connection refused")
    warned = [result.warn]
    for _ in range(PROBE_FAILURES + 1):
        result = heard(result.state, False, "connection refused")
        warned.append(result.warn)
    assert warned == [False, False, True, False, False]
    assert result.event == ""
    result = heard(result.state, True, "")
    assert (result.state.failures, result.state.warned) == (0, False)
    for _ in range(PROBE_FAILURES):
        result = heard(result.state, False, "down")
    assert result.warn


# -- made and changed ----------------------------------------------------------------------------


def test_a_probe_wakes_a_prompt_on_a_schedule_that_repeats(routines: Routines) -> None:
    made = routines.create("mail", "every 5m", prompt="Triage the new mail.", probe=" ./unread ")
    assert routines.store.get(made.id).probe == "./unread"
    cases = [
        (lambda: routines.create("a", "every 5m", command="ls", probe="./unread"), "a prompt"),
        (lambda: routines.create("b", "in 30m", prompt="Once.", probe="./unread"), "repeats"),
        (lambda: routines.update(made.id, schedule="in 30m"), "repeats"),
    ]
    for make, why in cases:
        with pytest.raises(JobsError, match=why):
            make()
    assert routines.update(made.id, probe="./unread --all").probe == "./unread --all"
    assert routines.update(made.id, name="inbox").probe == "./unread --all"
    assert routines.update(made.id, probe="").probe is None


# -- fired by the scheduler ----------------------------------------------------------------------


def test_only_something_new_wakes_the_prompt_with_it_as_the_runs_event(
    routines: Routines, clock: _Clock
) -> None:
    executor = _Executor()
    prober = _Prober((True, "mail 1"), (True, "mail 1"), (True, ""), (True, "mail 2"))
    scheduler = Scheduler(routines, executor, clock=clock, prober=prober)
    made = routines.create("mail", "every 5m", prompt="Triage.", probe="./unread")

    _looks(scheduler, clock, 4)

    assert prober.probed == 4
    assert executor.events == ["mail 1", "mail 2"]
    runs = routines.store.runs(made.id)
    assert [run.event for run in runs] == ["mail 2", "mail 1"]
    assert routines.store.get(made.id).next_run_at == clock.now + timedelta(minutes=5)
    assert routines.store.get(made.id).last_run_at == clock.now


def test_a_probe_that_keeps_failing_is_one_failed_run_that_tells_the_user(
    routines: Routines, clock: _Clock
) -> None:
    finished: list[tuple[RunStatus, str | None]] = []
    prober = _Prober(*[(False, "connection refused")] * (PROBE_FAILURES + 1))
    scheduler = Scheduler(
        routines,
        _Executor(),
        clock=clock,
        prober=prober,
        on_finished=lambda _, run: finished.append((run.status, run.error)),
    )
    made = routines.create("mail", "every 5m", prompt="Triage.", probe="./unread")

    _looks(scheduler, clock, PROBE_FAILURES + 1)

    assert finished == [(RunStatus.ERROR, f"Its probe failed {PROBE_FAILURES} times in a row.")]
    [run] = routines.store.runs(made.id)
    assert run.output == "connection refused"


def test_a_probe_that_raises_counts_as_a_failure(routines: Routines, clock: _Clock) -> None:
    class Raising:
        async def probe(self, routine: Routine) -> tuple[bool, str]:
            raise OSError("no shell")

    scheduler = Scheduler(routines, _Executor(), clock=clock, prober=Raising())
    made = routines.create("mail", "every 5m", prompt="Triage.", probe="./unread")

    _looks(scheduler, clock, 1)

    assert routines.store.probe_state(made.id).failures == 1
    assert routines.store.runs(made.id) == []


def test_run_now_with_nothing_new_says_so(routines: Routines, clock: _Clock) -> None:
    executor = _Executor()
    scheduler = Scheduler(routines, executor, clock=clock, prober=_Prober((True, "")))
    made = routines.create("mail", "every 1h", prompt="Triage.", probe="./unread")
    routines.trigger(made.id)

    async def scenario() -> None:
        clock.now += timedelta(minutes=1)
        scheduler.tick()
        await scheduler.wait()

    asyncio.run(scenario())

    [run] = routines.store.runs(made.id)
    assert (run.status, run.output, run.lateness) == (
        RunStatus.SILENT,
        "Its probe heard nothing new.",
        Lateness.MANUAL,
    )
    assert executor.events == []


def test_without_a_prober_a_probed_routine_waits(routines: Routines, clock: _Clock) -> None:
    executor = _Executor()
    scheduler = Scheduler(routines, executor, clock=clock)
    made = routines.create("mail", "every 5m", prompt="Triage.", probe="./unread")

    _looks(scheduler, clock, 1)

    assert executor.events == []
    assert routines.store.runs(made.id) == []
