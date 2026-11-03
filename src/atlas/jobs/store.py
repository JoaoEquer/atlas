"""Routines and their runs in the home's database (`jobs_` tables), by the Persistence rule: records
the program writes, lists and crosses live there: the routines and the ledger of their runs, in
the one database.

Subagents too (`jobs_subagents`): each child's task and how it ended, written when it ends, so a
server that starts again finds what an earlier one left running, and the batch that never reached
its parent.

The delegation record (`jobs_instances`, `jobs_turns`; step 16c): each executor instance on the
workbench and each of its turns, lean, a short summary and a pointer: the prompt, what was asked
and who answered, the final text and the files changed. It outlives the harness's own transcript,
and its text is searched with FTS5, as the conversations are (`unicode61`, accents removed).

An executor's other accounts (`jobs_executor_accounts`; step 16h), each a nickname and the
harness's own folder for it, and the account each folder's sessions start on
(`jobs_folder_accounts`), beside the instances that run on them: small state that changes little,
kept in the database as the approvals kept for good are. No credential is kept: each account's
login is the harness's, in its folder. With them, the order the accounts take over in when one
reaches its plan's limit (`jobs_account_failover`), asked for by the user on 21/10/2026, and each
session's own route across executors (`jobs_session_routes`), asked for the same day.

Since step 22 (03/11/2026) the accounts plugin keeps all four, in its own data folder: these
tables are read once, by `AccountStore.export`, when an update finds them filled, and written no
more. They stay in the database, unread: taking a table back is a riskier migration than a table
left behind."""

import dataclasses
import json
import secrets
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast

from atlas.home import (
    Database,
    DatabaseError,
    Migration,
    Schema,
    format_utc,
    parse_utc,
    transaction,
)
from atlas.jobs.errors import JobsError, RoutineNotFoundError
from atlas.jobs.model import (
    UNKNOWN_ERROR,
    AccountFailover,
    Creator,
    ExecutorAccount,
    InstanceRecord,
    Lateness,
    RouteTarget,
    Routine,
    Run,
    RunStatus,
    Schedule,
    ScheduleKind,
    SessionRoute,
    Subagent,
    SubagentStatus,
    TurnRecord,
    exchanges_of,
    exchanges_text,
)

SCHEMA = Schema(
    "jobs",
    (
        Migration(
            1,
            "routines and their runs",
            """
CREATE TABLE jobs_routines (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    schedule_kind TEXT NOT NULL,
    schedule_display TEXT NOT NULL,
    minutes INTEGER,
    expr TEXT,
    run_at TEXT,
    prompt TEXT,
    command TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT,
    model_provider TEXT,
    model_name TEXT,
    model_effort TEXT,
    run_now INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE jobs_runs (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id TEXT NOT NULL REFERENCES jobs_routines (id) ON DELETE CASCADE,
    started_at TEXT NOT NULL,
    lateness TEXT NOT NULL,
    status TEXT NOT NULL,
    ended_at TEXT,
    conversation_id TEXT,
    output TEXT NOT NULL DEFAULT '',
    error TEXT
) STRICT;
CREATE INDEX jobs_runs_by_routine ON jobs_runs (routine_id, number);
""",
        ),
        Migration(
            2,
            "subagents",
            """
CREATE TABLE jobs_subagents (
    id TEXT PRIMARY KEY,
    delegation_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    task_index INTEGER NOT NULL,
    task_count INTEGER NOT NULL,
    goal TEXT NOT NULL,
    context TEXT NOT NULL,
    model TEXT NOT NULL,
    started_at TEXT NOT NULL,
    status TEXT NOT NULL,
    ended_at TEXT,
    summary TEXT NOT NULL DEFAULT '',
    error TEXT,
    model_calls INTEGER NOT NULL DEFAULT 0,
    cost_usd TEXT,
    truncated INTEGER NOT NULL DEFAULT 0,
    delivered INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX jobs_subagents_by_delegation ON jobs_subagents (delegation_id, task_index);
CREATE INDEX jobs_subagents_by_status ON jobs_subagents (status);
""",
        ),
        Migration(
            3,
            "outside executors",
            """
ALTER TABLE jobs_subagents ADD COLUMN executor TEXT;
ALTER TABLE jobs_subagents ADD COLUMN folder TEXT NOT NULL DEFAULT '';
ALTER TABLE jobs_subagents ADD COLUMN session_id TEXT;
""",
        ),
        Migration(
            4,
            "an executor's account",
            "ALTER TABLE jobs_subagents ADD COLUMN account TEXT NOT NULL DEFAULT '';",
        ),
        Migration(
            5,
            "the bot a routine runs as",
            "ALTER TABLE jobs_routines ADD COLUMN bot TEXT;",
        ),
        Migration(
            6,
            "the delegation record: executor instances and their turns",
            """
CREATE TABLE jobs_instances (
    id TEXT PRIMARY KEY,
    executor TEXT NOT NULL,
    title TEXT NOT NULL,
    owner TEXT NOT NULL,
    folder TEXT NOT NULL,
    account TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '',
    parent_id TEXT NOT NULL DEFAULT '',
    subagent_id TEXT NOT NULL DEFAULT '',
    transcript_id TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE INDEX jobs_instances_by_update ON jobs_instances (updated_at);
CREATE TABLE jobs_turns (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL REFERENCES jobs_instances (id) ON DELETE CASCADE,
    turn INTEGER NOT NULL,
    prompt TEXT NOT NULL,
    folder TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    text TEXT NOT NULL DEFAULT '',
    failed INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',
    exchanges TEXT NOT NULL DEFAULT '[]',
    UNIQUE (instance_id, turn)
) STRICT;
CREATE VIRTUAL TABLE jobs_turns_search USING fts5(
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);
""",
        ),
        Migration(
            7,
            "an executor's extra accounts, and the account of each folder",
            """
CREATE TABLE jobs_executor_accounts (
    id TEXT PRIMARY KEY,
    executor TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at TEXT NOT NULL,
    expires_at TEXT,
    usage TEXT NOT NULL DEFAULT '',
    usage_at TEXT,
    UNIQUE (executor, name)
) STRICT;
CREATE TABLE jobs_folder_accounts (
    executor TEXT NOT NULL,
    folder TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES jobs_executor_accounts (id) ON DELETE CASCADE,
    PRIMARY KEY (executor, folder)
) STRICT;
""",
        ),
        Migration(
            8,
            "an executor's accounts as the harness's folders, not credentials",
            """
DROP TABLE jobs_folder_accounts;
DROP TABLE jobs_executor_accounts;
CREATE TABLE jobs_executor_accounts (
    id TEXT PRIMARY KEY,
    executor TEXT NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    folder TEXT NOT NULL,
    added_at TEXT NOT NULL,
    UNIQUE (executor, name)
) STRICT;
CREATE TABLE jobs_folder_accounts (
    executor TEXT NOT NULL,
    folder TEXT NOT NULL,
    account_id TEXT NOT NULL REFERENCES jobs_executor_accounts (id) ON DELETE CASCADE,
    PRIMARY KEY (executor, folder)
) STRICT;
""",
        ),
        Migration(
            9,
            "the order an executor's accounts take over in when one reaches its plan's limit",
            """
CREATE TABLE jobs_account_failover (
    executor TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    compact INTEGER NOT NULL DEFAULT 0,
    accounts TEXT NOT NULL DEFAULT '[]'
) STRICT;
""",
        ),
        Migration(
            10,
            "the destinations a session goes on to at a plan's limit, across executors",
            """
CREATE TABLE jobs_session_routes (
    conversation TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    targets TEXT NOT NULL DEFAULT '[]'
) STRICT;
""",
        ),
        Migration(
            11,
            "whether a session's route compacts its copy before it goes on, on by default",
            """
ALTER TABLE jobs_session_routes ADD COLUMN compact INTEGER NOT NULL DEFAULT 1;
""",
        ),
    ),
)

#: Runs kept per routine, the newest.
RUNS_KEPT = 50
_ID_TRIES = 8
_ROUTINE_COLUMNS = (
    "id, name, schedule_kind, schedule_display, minutes, expr, run_at, prompt, command, enabled, "
    "created_at, created_by, next_run_at, last_run_at, last_status, model_provider, model_name, "
    "model_effort, run_now, bot"
)
_RUN_COLUMNS = (
    "number, routine_id, started_at, lateness, status, ended_at, conversation_id, output, error"
)
_SUBAGENT_COLUMNS = (
    "id, delegation_id, parent_id, conversation_id, task_index, task_count, goal, context, model, "
    "started_at, status, ended_at, summary, error, model_calls, cost_usd, truncated, delivered, "
    "executor, folder, session_id, account"
)


class RoutineStore:
    """Every routine of the home. A connection is opened for each operation, as the other stores
    do."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, routine: Routine) -> Routine:
        """Keep a new routine under a fresh id (8 hexadecimal characters); its name must be new."""
        connection = self._database.connect()
        try:
            for _ in range(_ID_TRIES):
                made = dataclasses.replace(routine, id=secrets.token_hex(4))
                try:
                    with transaction(connection):
                        connection.execute(
                            f"INSERT INTO jobs_routines ({_ROUTINE_COLUMNS}) "  # noqa: S608
                            f"VALUES ({', '.join('?' * 20)})",
                            _routine_row(made),
                        )
                except sqlite3.IntegrityError as error:
                    if "name" in str(error):
                        raise JobsError(
                            f"A routine is named {routine.name!r} already. Choose another name, or "
                            "change that one."
                        ) from error
                    continue
                return made
        finally:
            connection.close()
        raise DatabaseError(f"No free id for a routine after {_ID_TRIES} tries.")

    def save(self, routine: Routine) -> None:
        connection = self._database.connect()
        try:
            with transaction(connection):
                found = connection.execute(
                    "UPDATE jobs_routines SET name = ?, schedule_kind = ?, schedule_display = ?, "
                    "minutes = ?, expr = ?, run_at = ?, prompt = ?, command = ?, enabled = ?, "
                    "created_at = ?, created_by = ?, next_run_at = ?, last_run_at = ?, "
                    "last_status = ?, model_provider = ?, model_name = ?, model_effort = ?, "
                    "run_now = ?, bot = ? WHERE id = ?",
                    (*_routine_row(routine)[1:], routine.id),
                ).rowcount
        except sqlite3.IntegrityError as error:
            raise JobsError(f"A routine is named {routine.name!r} already.") from error
        finally:
            connection.close()
        if not found:
            raise RoutineNotFoundError(f"There is no routine {routine.id}.")

    def get(self, key: str) -> Routine:
        """A routine by its id or its name."""
        connection = self._database.connect()
        try:
            row = connection.execute(
                f"SELECT {_ROUTINE_COLUMNS} FROM jobs_routines WHERE id = ? OR name = ?",  # noqa: S608
                (key, key),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RoutineNotFoundError(
                f"There is no routine {key!r}. List them with `atlas routines list`."
            )
        return _routine(row)

    def all(self) -> list[Routine]:
        """Every routine, by name."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_ROUTINE_COLUMNS} FROM jobs_routines ORDER BY name"  # noqa: S608
            ).fetchall()
        finally:
            connection.close()
        return [_routine(row) for row in rows]

    def remove(self, routine_id: str) -> None:
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute("DELETE FROM jobs_routines WHERE id = ?", (routine_id,))
        finally:
            connection.close()

    def start_run(self, routine: Routine, started: datetime, lateness: Lateness) -> Run:
        """Write the routine as advanced (its next run, its last) and its run as begun, together:
        the row exists before the run does anything (V3's `execution_jobs`: "a linha nasce antes da
        execução")."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "UPDATE jobs_routines SET next_run_at = ?, last_run_at = ?, enabled = ?, "
                    "run_now = 0 WHERE id = ?",
                    (
                        _text(routine.next_run_at),
                        _text(routine.last_run_at),
                        int(routine.enabled),
                        routine.id,
                    ),
                )
                cursor = connection.execute(
                    "INSERT INTO jobs_runs (routine_id, started_at, lateness, status) "
                    "VALUES (?, ?, ?, ?)",
                    (routine.id, format_utc(started), lateness.value, RunStatus.RUNNING.value),
                )
                number = cursor.lastrowid
                connection.execute(
                    "DELETE FROM jobs_runs WHERE routine_id = ? AND number NOT IN (SELECT number "
                    "FROM jobs_runs WHERE routine_id = ? ORDER BY number DESC LIMIT ?)",
                    (routine.id, routine.id, RUNS_KEPT),
                )
        finally:
            connection.close()
        if number is None:  # pragma: no cover - an INSERT always has a row id
            raise DatabaseError("SQLite gave no number to a routine run.")
        return Run(number, routine.id, started, lateness)

    def finish_run(self, run: Run) -> None:
        """Write how a run ended, and the routine's last status."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "UPDATE jobs_runs SET status = ?, ended_at = ?, conversation_id = ?, "
                    "output = ?, error = ? WHERE number = ?",
                    (
                        run.status.value,
                        _text(run.ended_at),
                        run.conversation_id,
                        run.output,
                        run.error,
                        run.number,
                    ),
                )
                connection.execute(
                    "UPDATE jobs_routines SET last_status = ? WHERE id = ?",
                    (run.status.value, run.routine_id),
                )
        finally:
            connection.close()

    def runs(self, routine_id: str, limit: int = RUNS_KEPT) -> list[Run]:
        """A routine's runs, newest first."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_RUN_COLUMNS} FROM jobs_runs WHERE routine_id = ? "  # noqa: S608
                "ORDER BY number DESC LIMIT ?",
                (routine_id, limit),
            ).fetchall()
        finally:
            connection.close()
        return [_run(row) for row in rows]

    def running(self) -> set[str]:
        """The routines with a run still marked running."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT DISTINCT routine_id FROM jobs_runs WHERE status = ?",
                (RunStatus.RUNNING.value,),
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in rows}

    def abandon_running(self) -> int:
        """Mark the runs a previous Atlas left running as unknown: whether they finished is not
        known, so they are neither assumed done nor run again."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                count = connection.execute(
                    "UPDATE jobs_runs SET status = ?, error = ? WHERE status = ?",
                    (
                        RunStatus.UNKNOWN.value,
                        "Atlas stopped while this run ran; whether it finished is unknown.",
                        RunStatus.RUNNING.value,
                    ),
                ).rowcount
        finally:
            connection.close()
        return count


_INSTANCE_COLUMNS = (
    "id, executor, title, owner, folder, account, state, started_at, updated_at, session_id, "
    "parent_id, subagent_id, transcript_id"
)
_TURN_COLUMNS = (
    "instance_id, turn, prompt, folder, started_at, ended_at, text, failed, reason, session_id, "
    "files, exchanges"
)
_ACCOUNT_COLUMNS = "id, executor, name, folder, added_at"


class AccountStore:
    """An executor's other accounts, and the account each folder's sessions start on."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def export(self) -> dict[str, object] | None:
        """Everything kept here, as the accounts plugin reads it the first time it starts (step
        22): each executor's accounts, the order they take over in and its folders' accounts, and
        each session's route; `None` when no account was ever added."""
        connection = self._database.connect()
        try:
            executors = [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT executor FROM jobs_executor_accounts ORDER BY executor"
                )
            ]
            routes = connection.execute("SELECT conversation FROM jobs_session_routes").fetchall()
        finally:
            connection.close()
        if not executors:
            return None
        kept: dict[str, object] = {}
        for executor in executors:
            failover = self.failover(executor)
            kept[executor] = {
                "accounts": [
                    {
                        "id": account.id,
                        "nickname": account.name,
                        "folder": account.folder,
                        "added_at": format_utc(account.added_at),
                    }
                    for account in self.accounts(executor)
                ],
                "hand_over": {
                    "enabled": failover.enabled,
                    "compact": failover.compact,
                    "order": list(failover.order),
                },
                "folders": self.folder_accounts(executor),
            }
        routed: dict[str, object] = {}
        for (conversation,) in routes:
            route = self.route(str(conversation))
            if route is None:
                continue
            routed[route.conversation] = {
                "enabled": route.enabled,
                "compact": route.compact,
                "stops": [
                    {
                        "executor": target.executor,
                        "account": target.account,
                        **({"model": target.model} if target.model else {}),
                        **({"effort": target.effort} if target.effort else {}),
                    }
                    for target in route.targets
                ],
            }
        return {"version": 1, "executors": kept, "routes": routed}

    def accounts(self, executor: str) -> list[ExecutorAccount]:
        """`executor`'s other accounts, the oldest first."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_ACCOUNT_COLUMNS} FROM jobs_executor_accounts "  # noqa: S608
                "WHERE executor = ? ORDER BY added_at, name",
                (executor,),
            ).fetchall()
        finally:
            connection.close()
        return [_account(row) for row in rows]

    def get(self, executor: str, account_id: str) -> ExecutorAccount | None:
        connection = self._database.connect()
        try:
            row = connection.execute(
                f"SELECT {_ACCOUNT_COLUMNS} FROM jobs_executor_accounts "  # noqa: S608
                "WHERE executor = ? AND id = ?",
                (executor, account_id),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _account(row)

    def add(self, executor: str, name: str, folder: str, added_at: datetime) -> ExecutorAccount:
        """Keep a new account under a fresh id (8 hexadecimal characters); its name must be new
        for the executor, whatever its case."""
        connection = self._database.connect()
        try:
            for _ in range(_ID_TRIES):
                made = ExecutorAccount(secrets.token_hex(4), executor, name, folder, added_at)
                try:
                    with transaction(connection):
                        connection.execute(
                            f"INSERT INTO jobs_executor_accounts ({_ACCOUNT_COLUMNS}) "  # noqa: S608
                            "VALUES (?, ?, ?, ?, ?)",
                            _account_row(made),
                        )
                except sqlite3.IntegrityError as error:
                    if "name" in str(error):
                        raise JobsError(
                            f"An account is named {name!r} already. Choose another name."
                        ) from error
                    continue
                return made
        finally:
            connection.close()
        raise DatabaseError(f"No free id for an account after {_ID_TRIES} tries.")

    def delete(self, executor: str, account_id: str) -> bool:
        """Forget an account, and the folders that started on it. Returns whether it existed."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                found = connection.execute(
                    "DELETE FROM jobs_executor_accounts WHERE executor = ? AND id = ?",
                    (executor, account_id),
                ).rowcount
        finally:
            connection.close()
        return bool(found)

    def folder_accounts(self, executor: str) -> dict[str, str]:
        """Each folder of `executor` that starts on an extra account, and that account's id."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT folder, account_id FROM jobs_folder_accounts WHERE executor = ?",
                (executor,),
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]): str(row[1]) for row in rows}

    def set_folder_account(self, executor: str, folder: str, account_id: str) -> None:
        """The account `folder`'s new sessions start on; empty, the main account."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                if account_id:
                    connection.execute(
                        "INSERT INTO jobs_folder_accounts (executor, folder, account_id) "
                        "VALUES (?, ?, ?) ON CONFLICT (executor, folder) "
                        "DO UPDATE SET account_id = excluded.account_id",
                        (executor, folder, account_id),
                    )
                else:
                    connection.execute(
                        "DELETE FROM jobs_folder_accounts WHERE executor = ? AND folder = ?",
                        (executor, folder),
                    )
        finally:
            connection.close()

    def failover(self, executor: str) -> AccountFailover:
        """How `executor`'s accounts take over from one another at a plan's limit; on, in the
        default order, until the user sets it."""
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT enabled, compact, accounts FROM jobs_account_failover WHERE executor = ?",
                (executor,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return AccountFailover(executor)
        order = json.loads(str(row[2]))
        return AccountFailover(
            executor,
            enabled=bool(row[0]),
            compact=bool(row[1]),
            order=tuple(str(account) for account in order if isinstance(account, str)),
        )

    def set_failover(self, failover: AccountFailover) -> None:
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "INSERT INTO jobs_account_failover (executor, enabled, compact, accounts) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (executor) DO UPDATE SET "
                    "enabled = excluded.enabled, compact = excluded.compact, "
                    "accounts = excluded.accounts",
                    (
                        failover.executor,
                        int(failover.enabled),
                        int(failover.compact),
                        json.dumps(list(failover.order)),
                    ),
                )
        finally:
            connection.close()

    def route(self, conversation: str) -> SessionRoute | None:
        """The route of the session whose card keeps `conversation`, when the user set one."""
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT enabled, targets, compact FROM jobs_session_routes WHERE conversation = ?",
                (conversation,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        targets: list[RouteTarget] = []
        for found in json.loads(str(row[1])):
            if isinstance(found, dict):
                kept = cast("dict[str, object]", found)
                model, effort = kept.get("model"), kept.get("effort")
                targets.append(
                    RouteTarget(
                        str(kept.get("executor", "")),
                        str(kept.get("account", "")),
                        model=model if isinstance(model, str) and model else None,
                        effort=effort if isinstance(effort, str) and effort else None,
                    )
                )
        return SessionRoute(
            conversation, tuple(targets), enabled=bool(row[0]), compact=bool(row[2])
        )

    def set_route(self, route: SessionRoute) -> None:
        targets = [
            {
                "executor": t.executor,
                "account": t.account,
                **({"model": t.model} if t.model else {}),
                **({"effort": t.effort} if t.effort else {}),
            }
            for t in route.targets
        ]
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "INSERT INTO jobs_session_routes (conversation, enabled, targets, compact) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (conversation) DO UPDATE SET "
                    "enabled = excluded.enabled, targets = excluded.targets, "
                    "compact = excluded.compact",
                    (
                        route.conversation,
                        int(route.enabled),
                        json.dumps(targets),
                        int(route.compact),
                    ),
                )
        finally:
            connection.close()


def _account_row(account: ExecutorAccount) -> tuple[object, ...]:
    return (
        account.id,
        account.executor,
        account.name,
        account.folder,
        format_utc(account.added_at),
    )


def _account(row: Sequence[object]) -> ExecutorAccount:
    return ExecutorAccount(
        id=str(row[0]),
        executor=str(row[1]),
        name=str(row[2]),
        folder=str(row[3]),
        added_at=parse_utc(str(row[4])),
    )


#: The states of an instance still running, which a new Atlas finds ended.
_LIVE_STATES = ("starting", "idle", "working")
#: Matches the text search reads before keeping the best per instance (the conversations' cap).
_SCAN_ROWS = 200
#: Tokens of a match's snippet.
_SNIPPET_TOKENS = 24


@dataclass(frozen=True, slots=True)
class TurnHit:
    """An instance the record's text search found, its best matching turn and the match."""

    instance: InstanceRecord
    turn: TurnRecord
    snippet: str


class InstanceStore:
    """The delegation record: every executor instance of the home, and its turns."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def save(self, record: InstanceRecord) -> None:
        """Keep an instance as it is now."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    f"INSERT INTO jobs_instances ({_INSTANCE_COLUMNS}) "  # noqa: S608
                    f"VALUES ({', '.join('?' * 13)}) ON CONFLICT (id) DO UPDATE SET "
                    "folder = excluded.folder, account = excluded.account, "
                    "state = excluded.state, updated_at = excluded.updated_at, "
                    "session_id = excluded.session_id",
                    _instance_row(record),
                )
        finally:
            connection.close()

    def get(self, instance_id: str) -> InstanceRecord | None:
        connection = self._database.connect()
        try:
            row = connection.execute(
                f"SELECT {_INSTANCE_COLUMNS} FROM jobs_instances WHERE id = ?",  # noqa: S608
                (instance_id,),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _instance(row)

    def recent(
        self,
        limit: int,
        *,
        executor: str | None = None,
        folder: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[InstanceRecord]:
        """The instances used last, the newest first; `folder` matches part of the path."""
        where, values = _filters(executor, folder, after, before)
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_INSTANCE_COLUMNS} FROM jobs_instances i WHERE {where} "  # noqa: S608
                "ORDER BY updated_at DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        finally:
            connection.close()
        return [_instance(row) for row in rows]

    def delegated_sessions(self, executor: str) -> set[str]:
        """The sessions of `executor` Atlas worked in for a conversation (`delegate`), which the
        executors tab marks "by Atlas" (step 16f)."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                "SELECT DISTINCT session_id FROM jobs_instances "
                "WHERE executor = ? AND owner = 'atlas' AND session_id != ''",
                (executor,),
            ).fetchall()
        finally:
            connection.close()
        return {str(row[0]) for row in rows}

    def conversation_of(self, session_id: str) -> str | None:
        """The conversation of the user's card that worked on `session_id` last, when one did: a
        session opened again from the list keeps that card's route."""
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT transcript_id FROM jobs_instances WHERE session_id = ? AND owner = 'user' "
                "AND transcript_id != '' ORDER BY updated_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else str(row[0])

    def save_turn(self, turn: TurnRecord) -> None:
        """Keep a turn as it is now, and what the text search reads of it."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                row = connection.execute(
                    f"INSERT INTO jobs_turns ({_TURN_COLUMNS}) "  # noqa: S608
                    f"VALUES ({', '.join('?' * 12)}) ON CONFLICT (instance_id, turn) DO UPDATE "
                    "SET ended_at = excluded.ended_at, text = excluded.text, "
                    "failed = excluded.failed, reason = excluded.reason, "
                    "session_id = excluded.session_id, files = excluded.files, "
                    "exchanges = excluded.exchanges RETURNING number",
                    _turn_row(turn),
                ).fetchone()
                number = int(row[0])
                connection.execute("DELETE FROM jobs_turns_search WHERE rowid = ?", (number,))
                connection.execute(
                    "INSERT INTO jobs_turns_search (rowid, body) VALUES (?, ?)",
                    (number, turn.searched),
                )
        finally:
            connection.close()

    def turns(self, instance_id: str) -> list[TurnRecord]:
        """An instance's turns, in order."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_TURN_COLUMNS} FROM jobs_turns WHERE instance_id = ? "  # noqa: S608
                "ORDER BY turn",
                (instance_id,),
            ).fetchall()
        finally:
            connection.close()
        return [_turn(row) for row in rows]

    def turn_count(self, instance_id: str) -> int:
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT coalesce(max(turn), 0) FROM jobs_turns WHERE instance_id = ?",
                (instance_id,),
            ).fetchone()
        finally:
            connection.close()
        return int(row[0])

    def search(
        self,
        query: str,
        limit: int,
        *,
        executor: str | None = None,
        folder: str | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> list[TurnHit]:
        """The instances whose turns hold `query` (an FTS5 query), the best match each."""
        where, values = _filters(executor, folder, after, before)
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT t.instance_id, t.turn, "  # noqa: S608
                f"snippet(jobs_turns_search, 0, '»', '«', ' … ', {_SNIPPET_TOKENS}) "
                "FROM jobs_turns_search "
                "JOIN jobs_turns t ON t.number = jobs_turns_search.rowid "
                "JOIN jobs_instances i ON i.id = t.instance_id "
                f"WHERE jobs_turns_search MATCH ? AND {where} "
                "ORDER BY bm25(jobs_turns_search) LIMIT ?",
                (query, *values, _SCAN_ROWS),
            ).fetchall()
        except sqlite3.OperationalError as error:
            raise JobsError(f"The search could not read the query: {error}") from error
        finally:
            connection.close()
        best: dict[str, tuple[int, str]] = {}
        for instance_id, turn, snippet in rows:
            best.setdefault(str(instance_id), (int(turn), str(snippet)))
        hits: list[TurnHit] = []
        for instance_id, (number, snippet) in list(best.items())[:limit]:
            record = self.get(instance_id)
            turn = next((t for t in self.turns(instance_id) if t.number == number), None)
            if record is not None and turn is not None:
                hits.append(TurnHit(record, turn, snippet))
        return hits

    def abandon_running(self, now: datetime) -> int:
        """What an earlier Atlas left running ended with it: its processes are gone."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                count = connection.execute(
                    "UPDATE jobs_instances SET state = 'ended', updated_at = ? "  # noqa: S608
                    f"WHERE state IN ({', '.join('?' * len(_LIVE_STATES))})",
                    (format_utc(now), *_LIVE_STATES),
                ).rowcount
                connection.execute(
                    "UPDATE jobs_turns SET ended_at = ?, failed = 1, reason = 'unknown' "
                    "WHERE ended_at IS NULL",
                    (format_utc(now),),
                )
        finally:
            connection.close()
        return count


def _filters(
    executor: str | None, folder: str | None, after: str | None, before: str | None
) -> tuple[str, list[object]]:
    clauses: list[str] = ["1 = 1"]
    values: list[object] = []
    if executor:
        clauses.append("i.executor = ?")
        values.append(executor)
    if folder:
        clauses.append("i.folder LIKE ? ESCAPE '\\'")
        escaped = folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        values.append(f"%{escaped}%")
    if after:
        clauses.append("i.updated_at >= ?")
        values.append(after)
    if before:
        clauses.append("i.started_at < ?")
        values.append(before)
    return " AND ".join(clauses), values


def _instance_row(record: InstanceRecord) -> tuple[object, ...]:
    return (
        record.id,
        record.executor,
        record.title,
        record.owner,
        record.folder,
        record.account,
        record.state,
        format_utc(record.started_at),
        format_utc(record.updated_at),
        record.session_id,
        record.parent_id,
        record.subagent_id,
        record.transcript_id,
    )


def _instance(row: Sequence[object]) -> InstanceRecord:
    return InstanceRecord(
        id=str(row[0]),
        executor=str(row[1]),
        title=str(row[2]),
        owner=str(row[3]),
        folder=str(row[4]),
        account=str(row[5]),
        state=str(row[6]),
        started_at=parse_utc(str(row[7])),
        updated_at=parse_utc(str(row[8])),
        session_id=str(row[9]),
        parent_id=str(row[10]),
        subagent_id=str(row[11]),
        transcript_id=str(row[12]),
    )


def _turn_row(turn: TurnRecord) -> tuple[object, ...]:
    return (
        turn.instance_id,
        turn.number,
        turn.prompt,
        turn.folder,
        format_utc(turn.started_at),
        _text(turn.ended_at),
        turn.text,
        int(turn.failed),
        turn.reason,
        turn.session_id,
        json.dumps(list(turn.files), ensure_ascii=False),
        exchanges_text(turn.exchanges),
    )


def _turn(row: Sequence[object]) -> TurnRecord:
    try:
        raw: object = json.loads(str(row[10]))
    except ValueError:
        raw = []
    files = tuple(str(f) for f in cast("list[object]", raw)) if isinstance(raw, list) else ()
    return TurnRecord(
        instance_id=str(row[0]),
        number=int(cast("int", row[1])),
        prompt=str(row[2]),
        folder=str(row[3]),
        started_at=parse_utc(str(row[4])),
        ended_at=_moment(row[5]),
        text=str(row[6]),
        failed=bool(row[7]),
        reason=str(row[8]),
        session_id=str(row[9]),
        files=files,
        exchanges=exchanges_of(str(row[11])),
    )


class SubagentStore:
    """Every subagent of the home, written when it starts and when it ends."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def add(self, children: Sequence[Subagent]) -> None:
        """Keep a delegation's children, together, before any of them runs."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.executemany(
                    f"INSERT INTO jobs_subagents ({_SUBAGENT_COLUMNS}) "  # noqa: S608
                    f"VALUES ({', '.join('?' * 22)})",
                    [_subagent_row(child) for child in children],
                )
        finally:
            connection.close()

    def finish(self, child: Subagent) -> None:
        """Write how a child ended, and where: an outside executor's task may have gone on on
        another account or executor (its route)."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "UPDATE jobs_subagents SET status = ?, ended_at = ?, summary = ?, error = ?, "
                    "model_calls = ?, cost_usd = ?, truncated = ?, session_id = ?, executor = ?, "
                    "account = ?, model = ? WHERE id = ?",
                    (
                        child.status.value,
                        _text(child.ended_at),
                        child.summary,
                        child.error,
                        child.model_calls,
                        None if child.cost_usd is None else str(child.cost_usd),
                        int(child.truncated),
                        child.session_id,
                        child.executor,
                        child.account,
                        child.model,
                        child.id,
                    ),
                )
        finally:
            connection.close()

    def delivered(self, delegation_id: str) -> None:
        """The batch's notice reached its parent."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "UPDATE jobs_subagents SET delivered = 1 WHERE delegation_id = ?",
                    (delegation_id,),
                )
        finally:
            connection.close()

    def delegation(self, delegation_id: str) -> list[Subagent]:
        """A delegation's children, in the order of its tasks."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_SUBAGENT_COLUMNS} FROM jobs_subagents "  # noqa: S608
                "WHERE delegation_id = ? ORDER BY task_index",
                (delegation_id,),
            ).fetchall()
        finally:
            connection.close()
        return [_subagent(row) for row in rows]

    def get(self, subagent_id: str) -> Subagent | None:
        """One child, by its id."""
        connection = self._database.connect()
        try:
            row = connection.execute(
                f"SELECT {_SUBAGENT_COLUMNS} FROM jobs_subagents WHERE id = ?",  # noqa: S608
                (subagent_id,),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _subagent(row)

    def abandon_running(self, now: datetime) -> int:
        """Mark the children an earlier Atlas left running as unknown, never failed: whether they
        finished is not known."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                count = connection.execute(
                    "UPDATE jobs_subagents SET status = ?, error = ?, ended_at = ? "
                    "WHERE status = ?",
                    (
                        SubagentStatus.UNKNOWN.value,
                        UNKNOWN_ERROR,
                        format_utc(now),
                        SubagentStatus.RUNNING.value,
                    ),
                ).rowcount
        finally:
            connection.close()
        return count

    def undelivered(self, since: datetime) -> list[list[Subagent]]:
        """The ended delegations whose notice never reached their parent, started since `since`,
        each as its children in the order of its tasks."""
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_SUBAGENT_COLUMNS} FROM jobs_subagents "  # noqa: S608
                "WHERE delegation_id IN (SELECT delegation_id FROM jobs_subagents "
                "GROUP BY delegation_id HAVING max(delivered) = 0 AND sum(status = ?) = 0 "
                "AND min(started_at) >= ?) ORDER BY started_at, delegation_id, task_index",
                (SubagentStatus.RUNNING.value, format_utc(since)),
            ).fetchall()
        finally:
            connection.close()
        batches: dict[str, list[Subagent]] = {}
        for row in rows:
            child = _subagent(row)
            batches.setdefault(child.delegation_id, []).append(child)
        return list(batches.values())


def _text(moment: datetime | None) -> str | None:
    return None if moment is None else format_utc(moment)


def _moment(value: object) -> datetime | None:
    return None if value is None else parse_utc(str(value))


def _routine_row(routine: Routine) -> tuple[object, ...]:
    schedule = routine.schedule
    return (
        routine.id,
        routine.name,
        schedule.kind.value,
        schedule.display,
        schedule.minutes,
        schedule.expr,
        _text(schedule.run_at),
        routine.prompt,
        routine.command,
        int(routine.enabled),
        format_utc(routine.created_at),
        routine.created_by.value,
        _text(routine.next_run_at),
        _text(routine.last_run_at),
        None if routine.last_status is None else routine.last_status.value,
        routine.model_provider,
        routine.model_name,
        routine.model_effort,
        int(routine.run_now),
        routine.bot,
    )


def _routine(row: Sequence[object]) -> Routine:
    (
        routine_id, name, kind, display, minutes, expr, run_at, prompt, command, enabled,
        created_at, created_by, next_run_at, last_run_at, last_status, provider, model, effort,
        run_now, bot,
    ) = row  # fmt: skip
    schedule = Schedule(
        ScheduleKind(str(kind)),
        str(display),
        minutes=None if minutes is None else int(str(minutes)),
        expr=None if expr is None else str(expr),
        run_at=_moment(run_at),
    )
    created = _moment(created_at)
    if created is None:  # pragma: no cover - NOT NULL
        raise DatabaseError(f"Routine {routine_id} has no creation time.")
    return Routine(
        id=str(routine_id),
        name=str(name),
        schedule=schedule,
        prompt=None if prompt is None else str(prompt),
        command=None if command is None else str(command),
        enabled=bool(enabled),
        created_at=created,
        created_by=Creator(str(created_by)),
        next_run_at=_moment(next_run_at),
        last_run_at=_moment(last_run_at),
        last_status=None if last_status is None else RunStatus(str(last_status)),
        model_provider=None if provider is None else str(provider),
        model_name=None if model is None else str(model),
        model_effort=None if effort is None else str(effort),
        run_now=bool(run_now),
        bot=None if bot is None else str(bot),
    )


def _run(row: Sequence[object]) -> Run:
    number, routine_id, started_at, lateness, status, ended_at, conversation_id, output, error = row
    started = _moment(started_at)
    if started is None:  # pragma: no cover - NOT NULL
        raise DatabaseError(f"Run {number} has no start time.")
    return Run(
        number=int(str(number)),
        routine_id=str(routine_id),
        started_at=started,
        lateness=Lateness(str(lateness)),
        status=RunStatus(str(status)),
        ended_at=_moment(ended_at),
        conversation_id=None if conversation_id is None else str(conversation_id),
        output=str(output),
        error=None if error is None else str(error),
    )


def _subagent_row(child: Subagent) -> tuple[object, ...]:
    return (
        child.id,
        child.delegation_id,
        child.parent_id,
        child.conversation_id,
        child.index,
        child.count,
        child.goal,
        child.context,
        child.model,
        format_utc(child.started_at),
        child.status.value,
        _text(child.ended_at),
        child.summary,
        child.error,
        child.model_calls,
        None if child.cost_usd is None else str(child.cost_usd),
        int(child.truncated),
        int(child.delivered),
        child.executor,
        child.folder,
        child.session_id,
        child.account,
    )


def _subagent(row: Sequence[object]) -> Subagent:
    (
        child_id, delegation_id, parent_id, conversation_id, index, count, goal, context, model,
        started_at, status, ended_at, summary, error, calls, cost, truncated, delivered,
        executor, folder, session_id, account,
    ) = row  # fmt: skip
    started = _moment(started_at)
    if started is None:  # pragma: no cover - NOT NULL
        raise DatabaseError(f"Subagent {child_id} has no start time.")
    return Subagent(
        id=str(child_id),
        delegation_id=str(delegation_id),
        parent_id=str(parent_id),
        conversation_id=str(conversation_id),
        index=int(str(index)),
        count=int(str(count)),
        goal=str(goal),
        context=str(context),
        model=str(model),
        started_at=started,
        status=SubagentStatus(str(status)),
        ended_at=_moment(ended_at),
        summary=str(summary),
        error=None if error is None else str(error),
        model_calls=int(str(calls)),
        cost_usd=None if cost is None else Decimal(str(cost)),
        truncated=bool(truncated),
        delivered=bool(delivered),
        executor=None if executor is None else str(executor),
        folder=str(folder),
        session_id=None if session_id is None else str(session_id),
        account=str(account),
    )
