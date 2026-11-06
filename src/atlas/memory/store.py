"""The profile and the memory on disk: `memory/USER.md` and `memory/MEMORY.md` in the home.

The guards, each against a loss:

- **Read, change and write under one lock**, re-reading the file inside it, so another process's
  write (a review in the background, a second conversation) is never written over with a stale
  view (`atlas.home.locked`).
- **A file that exists but cannot be read is not an empty store.** Reading it as empty and saving
  would wipe it, so the write is refused. A file starting with a UTF-8 byte-order mark (Notepad's)
  reads as if it had none; any other byte that is not UTF-8 makes the file unreadable rather than
  read with replacement characters a save would then keep.
- **A file with an entry longer than the whole limit** was written outside the tool (an append
  without `§`): it is copied to `<file>.bak.<time>` and left alone, and the write is refused with
  where the copy is. Adding only appends, so it goes ahead.
- **Writes replace the file whole** (`atlas.home.replace_file`), so a reader sees the old file or
  the new one.
- **Text that looks like an injection is refused** (the threat patterns, strict scope:
  `atlas.trust`): the memory goes into every conversation's prompt. An entry that holds one anyway,
  written outside the tool, reaches the snapshot as `[BLOCKED: ...]`, while the file keeps it so
  the user can see and remove it (dropping it silently would hide the attack).
- **A check is the write without the writing**: the same lock, the same
  refusals, the same walk over the operations. A proposal is checked so when it is made, which
  refuses at once what its approval would refuse, and whenever it is listed.

**The proposals** are rows of the home's database (docs/ARCHITECTURE.md, Persistence). A row that
cannot be read is logged and left out.

**The soul** is `SOUL.md` (docs/ARCHITECTURE.md, The soul). It is seeded once and never written
over; unreadable, it is no soul, and the default identity stands in. It is the user's own file, so
a threat pattern in it is logged and the file still loads.
"""

import json
import logging
import secrets
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from atlas.home import (
    Database,
    DatabaseError,
    FileBusyError,
    Migration,
    Schema,
    locked,
    replace_file,
    transaction,
)
from atlas.memory.model import (
    FILES,
    LIMITS,
    SOUL_LIMIT,
    Action,
    Applied,
    Operation,
    Proposal,
    Target,
    apply,
    cut_soul,
    drifted,
    parse_entries,
    render_block,
    render_entries,
)
from atlas.trust import Scope, first_threat_message, scan_for_threats

SCHEMA = Schema(
    "memory",
    (
        Migration(
            1,
            "the background review's proposals",
            """
CREATE TABLE memory_proposals (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    operations TEXT NOT NULL,
    conversation_id TEXT,
    created_at TEXT NOT NULL
) STRICT;
""",
        ),
        Migration(
            2,
            "the bot a proposal comes from",
            """
ALTER TABLE memory_proposals ADD COLUMN bot TEXT;
""",
        ),
    ),
)

_logger = logging.getLogger("atlas.memory")


@dataclass(frozen=True, slots=True)
class _Read:
    raw: str
    #: The file exists and could not be read: its entries are unknown.
    failed: bool = False


class MemoryFiles:
    """The two stores an agent reads and writes: the home's profile, and its own memory. The
    main agent's memory is beside the profile; a bot's is in the bot's folder (`memory_folder`),
    and the profile is still the home's, one for all (docs/ARCHITECTURE.md, Bots).

    `limits` is kept as given, not copied: the home's stores share one, which the settings screen
    changes in place (`[memory]`), so the next write is checked against the new limit."""

    def __init__(
        self,
        folder: Path,
        limits: dict[Target, int] | None = None,
        *,
        memory_folder: Path | None = None,
    ) -> None:
        self.folder = folder
        self.memory_folder = folder if memory_folder is None else memory_folder
        self.limits = dict(LIMITS) if limits is None else limits

    def path(self, target: Target) -> Path:
        folder = self.folder if target is Target.USER else self.memory_folder
        return folder / FILES[target]

    def entries(self, target: Target) -> list[str]:
        """The entries now on disk; none when the file is missing or cannot be read."""
        return parse_entries(self._read(target).raw)

    def snapshot(self) -> str:
        """Both stores as the system prompt carries them: the profile first, then the memory."""
        blocks = [
            render_block(
                target,
                [_screened(entry, FILES[target]) for entry in self.entries(target)],
                self.limits[target],
            )
            for target in (Target.USER, Target.MEMORY)
        ]
        return "\n\n".join(block for block in blocks if block)

    def change(self, target: Target, operations: Sequence[Operation]) -> Applied:
        """Apply `operations` to a store, all or nothing, under its lock."""
        return self._run(target, operations, write=True)

    def check(self, target: Target, operations: Sequence[Operation]) -> Applied:
        """What `change` would do now, refusals included, without writing anything."""
        return self._run(target, operations, write=False)

    def _run(self, target: Target, operations: Sequence[Operation], *, write: bool) -> Applied:
        for operation in operations:
            if operation.action is not Action.REMOVE and operation.content:
                threat = first_threat_message(operation.content, Scope.STRICT)
                if threat is not None:
                    return Applied(None, threat, show_entries=False)
        path = self.path(target)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with locked(path.with_name(path.name + ".lock")):
                read = self._read(target)
                if read.failed:
                    return Applied(
                        None,
                        f"{path.name} exists but cannot be read right now (locked by another "
                        "program, permissions, or bytes that are not UTF-8). Saving over it would "
                        "wipe it, so nothing was changed; try again later.",
                        show_entries=False,
                    )
                limit = self.limits[target]
                adding_only = all(op.action is Action.ADD for op in operations)
                if not adding_only and drifted(read.raw, limit):
                    where = self._set_aside(path, read.raw) if write else "nothing was copied"
                    return Applied(None, _drifted_message(path, where), show_entries=False)
                applied = apply(parse_entries(read.raw), list(operations), limit)
                if write and applied.entries is not None:
                    replace_file(path, render_entries(applied.entries).encode("utf-8"))
                return applied
        except (OSError, FileBusyError) as error:
            _logger.warning("The %s store could not be changed: %s", target, error)
            return Applied(
                None,
                f"{path.name} could not be written: {error}. Nothing changed.",
                show_entries=False,
            )

    def _read(self, target: Target) -> _Read:
        path = self.path(target)
        try:
            return _Read(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return _Read("")
        except (OSError, UnicodeDecodeError) as error:
            _logger.warning("%s cannot be read: %s", path, error)
            return _Read("", failed=True)

    @staticmethod
    def _set_aside(path: Path, raw: str) -> str:
        """Copy a file changed outside the tool aside; where the copy is, in words."""
        copy = path.with_name(f"{path.name}.bak.{int(time.time())}")
        try:
            copy.write_text(raw, encoding="utf-8")
        except OSError as error:
            _logger.warning("A changed memory file could not be copied aside: %s", error)
            return f"a copy could not be made ({error}); the file is unchanged"
        _logger.warning("A memory file changed outside the tool was copied to %s.", copy)
        return f"a copy is at {copy}"


class SoulFile:
    """The main agent's soul, `SOUL.md` at the top of the home."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def text(self) -> str | None:
        """What the file holds, for the screen that edits it; `None` when there is none. Raises
        `OSError` or `UnicodeDecodeError` when it exists and cannot be read."""
        try:
            return self.path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            return None

    def prompt(self) -> str:
        """The soul as the instructions carry it, stripped and cut; empty when the file is
        missing, blank or unreadable, and then the default identity takes its place."""
        try:
            content = (self.text() or "").strip()
        except (OSError, UnicodeDecodeError) as error:
            _logger.warning(
                "%s cannot be read, so the default identity stands in: %s", self.path, error
            )
            return ""
        if not content:
            return ""
        found = scan_for_threats(content, Scope.CONTEXT)
        if found:
            _logger.warning(
                "%s matched threat pattern(s) %s; loaded anyway because it is the user's own file. "
                "Review it if you did not write that text.",
                self.path,
                ", ".join(found),
            )
        cut = cut_soul(content, str(self.path))
        if cut != content:
            _logger.warning(
                "%s holds %d characters, past the %d the prompt carries; its middle is left out.",
                self.path,
                len(content),
                SOUL_LIMIT,
            )
        return cut

    def seed(self, default: str) -> bool:
        """Write `default` when there is no soul yet; one that exists, even empty, is never
        written over. Whether it was written."""
        if self.path.exists():
            return False
        replace_file(self.path, f"{default.strip()}\n".encode())
        return True

    def write(self, text: str) -> None:
        """Put `text` in the file whole: a reader sees the old soul or the new one."""
        replace_file(self.path, text.encode("utf-8"))


class ProposalStore:
    """The open memory proposals, oldest first. A connection is opened for each operation, as the
    other stores do."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def add(
        self,
        target: Target,
        operations: Sequence[Operation],
        conversation_id: str | None,
        created_at: str,
        bot: str | None = None,
    ) -> Proposal:
        """Keep a proposal under a new id: 8 hexadecimal characters."""
        connection = self._database.connect()
        try:
            for _ in range(_ID_TRIES):
                proposal = Proposal(
                    secrets.token_hex(4),
                    target,
                    tuple(operations),
                    conversation_id,
                    created_at,
                    bot,
                )
                try:
                    with transaction(connection):
                        connection.execute(
                            "INSERT INTO memory_proposals (id, target, operations, "
                            "conversation_id, created_at, bot) VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                proposal.id,
                                target.value,
                                _encoded(operations),
                                conversation_id,
                                created_at,
                                bot,
                            ),
                        )
                except sqlite3.IntegrityError:
                    continue
                return proposal
        finally:
            connection.close()
        raise DatabaseError(f"No free id for a memory proposal after {_ID_TRIES} tries.")

    def get(self, proposal_id: str) -> Proposal | None:
        connection = self._database.connect()
        try:
            row = connection.execute(
                f"SELECT {_COLUMNS} FROM memory_proposals WHERE id = ?",  # noqa: S608
                (proposal_id,),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _proposal(row)

    def all(self) -> list[Proposal]:
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_COLUMNS} FROM memory_proposals ORDER BY created_at, id"  # noqa: S608
            ).fetchall()
        finally:
            connection.close()
        return [proposal for row in rows if (proposal := _proposal(row)) is not None]

    def delete(self, proposal_id: str) -> bool:
        """Drop a proposal; `False` when there was none with this id."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                deleted = connection.execute(
                    "DELETE FROM memory_proposals WHERE id = ?", (proposal_id,)
                ).rowcount
        finally:
            connection.close()
        return deleted > 0


_COLUMNS = "id, target, operations, conversation_id, created_at, bot"
#: Tries for a free id; eight hexadecimal characters collide once in billions.
_ID_TRIES = 5


def _encoded(operations: Sequence[Operation]) -> str:
    return json.dumps(
        [
            {
                "action": operation.action.value,
                "content": operation.content,
                "old_text": operation.old_text,
                "entry": operation.entry,
            }
            for operation in operations
        ],
        ensure_ascii=False,
    )


def _proposal(row: tuple[Any, ...]) -> Proposal | None:
    """A row as a proposal; `None`, logged, when it cannot be read (written by hand, say)."""
    proposal_id, target, operations, conversation_id, created_at, bot = tuple(row)
    try:
        items = cast("list[dict[str, Any]]", json.loads(operations))
        decoded = tuple(
            Operation(
                Action(item["action"]),
                str(item.get("content") or ""),
                str(item.get("old_text") or ""),
                None if item.get("entry") is None else str(item["entry"]),
            )
            for item in items
        )
        return Proposal(str(proposal_id), Target(target), decoded, conversation_id, created_at, bot)
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        _logger.warning("The memory proposal %s cannot be read: %s", proposal_id, error)
        return None


def _drifted_message(path: Path, where: str) -> str:
    return (
        f"{path.name} was not written: one of its entries is longer than the store's whole char "
        "limit, so loose text was written without § delimiters (by another tool, a shell "
        "append or a hand edit). Writing over the file would lose that text, so nothing "
        f"changed; {where}. Make {path.name} a clean list of entries split by § lines again, or "
        "move the extra text elsewhere, and retry."
    )


def _screened(entry: str, filename: str) -> str:
    """An entry as the prompt may carry it: one with a threat pattern becomes a placeholder."""
    if entry.startswith("[BLOCKED:"):
        return entry
    found = scan_for_threats(entry, Scope.STRICT)
    if not found:
        return entry
    _logger.warning("A memory entry was kept out of the prompt: %s", ", ".join(found))
    return (
        f"[BLOCKED: {filename} entry contained threat pattern(s): {', '.join(found)}. Removed from "
        "the system prompt; use memory(action=remove) to delete the original.]"
    )
