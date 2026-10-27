"""Conversations in the home's database: every message kept raw, archived but never deleted, and
searched with FTS5.

**Saving follows the history the loop keeps.** The agent loop changes a conversation's history in
place: it appends messages, and a compaction replaces the older ones with a summary
(`atlas.agent`). `Conversation.save` compares the history with what is stored, by identity: a
message the store already holds stays, a new one is inserted where it stands, and a stored one that
left the history is archived (`active = 0`, `compacted = 1`), never deleted. Each message has a
position, so a summary inserted before the kept messages reads in its place without renumbering
anything; scrolling reads all of them, archived ones included, in that order.

**Search** is FTS5 over the text of each message, with the `unicode61` tokenizer removing accents
(`memoria` finds `memória`), kept in step by triggers. A connection is opened for each
operation and closed after it, as the call ledger does.
"""

import logging
import secrets
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from atlas.agent import from_atlas, typed_text
from atlas.conversations.codec import decode, encode, kind_of, role_of, shown
from atlas.conversations.errors import ConversationNotFoundError
from atlas.conversations.model import (
    APP,
    BOT,
    DEMOTED_SOURCES,
    HIDDEN_SOURCES,
    MATCH_MARKS,
    ConversationInfo,
    Cursor,
    Found,
    Hit,
    Kind,
    Role,
    StoredMessage,
    TitleSource,
    given_title,
    query_terms,
    snippet_pieces,
    title_from,
    title_pieces,
    title_words,
    typed_query,
)
from atlas.home import Database, Migration, Schema, format_utc, transaction, utc_now
from atlas.models import AssistantMessage, Message, ModelChoice, ToolResult, UserMessage

SCHEMA = Schema(
    "conversations",
    (
        Migration(
            1,
            "conversations and their messages",
            """
CREATE TABLE conversations_threads (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    parent_id TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
CREATE INDEX conversations_threads_by_time ON conversations_threads (updated_at);
CREATE TABLE conversations_messages (
    number INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations_threads (id) ON DELETE CASCADE,
    position REAL NOT NULL,
    role TEXT NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    tool_name TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX conversations_messages_in_order ON conversations_messages (conversation_id, position);
CREATE VIRTUAL TABLE conversations_search USING fts5(
    text,
    content = 'conversations_messages',
    content_rowid = 'number',
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TRIGGER conversations_search_insert AFTER INSERT ON conversations_messages BEGIN
    INSERT INTO conversations_search (rowid, text) VALUES (new.number, new.text);
END;
CREATE TRIGGER conversations_search_delete AFTER DELETE ON conversations_messages BEGIN
    INSERT INTO conversations_search (conversations_search, rowid, text)
    VALUES ('delete', old.number, old.text);
END;
""",
        ),
        Migration(
            2,
            "the model a conversation picked",
            """
ALTER TABLE conversations_threads ADD COLUMN model_provider TEXT;
ALTER TABLE conversations_threads ADD COLUMN model_name TEXT;
ALTER TABLE conversations_threads ADD COLUMN model_effort TEXT;
""",
        ),
        Migration(
            3,
            "the bot a conversation belongs to",
            """
ALTER TABLE conversations_threads ADD COLUMN bot TEXT;
CREATE INDEX conversations_threads_by_bot ON conversations_threads (bot);
""",
        ),
        Migration(
            4,
            "who wrote a conversation's title",
            """
ALTER TABLE conversations_threads ADD COLUMN title_source TEXT NOT NULL DEFAULT 'derived';
UPDATE conversations_threads SET title_source = 'user' WHERE source != 'app';
""",
        ),
    ),
)

#: Rows FTS5 ranks before they are grouped by conversation.
SCAN_ROWS = 300
#: Words around a match in its snippet.
SNIPPET_TOKENS = 24

_MESSAGE_COLUMNS = (
    "number, conversation_id, role, kind, text, tool_name, created_at, active"  # stored message
)
_INFO_COLUMNS = (
    "t.id, t.title, t.source, t.started_at, t.updated_at, "
    "(SELECT count(*) FROM conversations_messages c WHERE c.conversation_id = t.id "
    "AND c.role != 'system'), t.bot"
)

#: What the list of recent conversations leaves out.
_UNLISTED = (*HIDDEN_SOURCES, BOT)

_logger = logging.getLogger("atlas.conversations")


@dataclass(slots=True)
class _Known:
    """A stored message the history still holds: the object, its number, its position."""

    message: Message
    number: int
    position: float


class Conversation:
    """One conversation: its history, as the agent loop changes it, and what saving it stores."""

    def __init__(
        self, database: Database, conversation_id: str, history: list[Message], known: list[_Known]
    ) -> None:
        self._database = database
        self.id = conversation_id
        #: The history the loop runs on. `save` stores what changed in it.
        self.history = history
        self._known = known

    def save(self) -> int:
        """Store what changed in the history since the last save; returns the new messages."""
        by_object = {id(known.message): known for known in self._known}
        kept: set[int] = set()
        positions: list[float | None] = []
        for message in self.history:
            known = by_object.get(id(message))
            if known is not None and known.message is message:
                kept.add(known.number)
                positions.append(known.position)
            else:
                positions.append(None)
        if all(position is not None for position in positions) and len(kept) == len(self._known):
            return 0
        names = _tool_names(self.history)
        now = format_utc(utc_now())
        known_now: list[_Known] = []
        inserted = 0
        connection = self._database.connect()
        try:
            with transaction(connection):
                archived = [k.number for k in self._known if k.number not in kept]
                connection.executemany(
                    "UPDATE conversations_messages SET active = 0, compacted = 1 WHERE number = ?",
                    [(number,) for number in archived],
                )
                stored = [known.position for known in self._known]
                previous: float | None = None
                for index, message in enumerate(self.history):
                    position = positions[index]
                    if position is None:
                        following = _next(positions, index)
                        # After every stored message before the next kept one, so a summary lands
                        # after the messages it archived, not among them.
                        bounds = [p for p in stored if following is None or p < following]
                        if previous is not None:
                            bounds.append(previous)
                        position = _between(max(bounds) if bounds else None, following)
                        positions[index] = position
                        number = _insert(connection, self.id, message, position, names, now)
                        known_now.append(_Known(message, number, position))
                        inserted += 1
                    else:
                        known_now.append(by_object[id(message)])
                    previous = position
                title = _title(self.history)
                connection.execute(
                    "UPDATE conversations_threads SET updated_at = ?, "
                    "title = CASE WHEN title = '' THEN ? ELSE title END WHERE id = ?",
                    (now, title, self.id),
                )
        finally:
            connection.close()
        self._known = known_now
        return inserted


class ConversationStore:
    """Every conversation of the home."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        *,
        source: str = APP,
        parent_id: str | None = None,
        title: str = "",
        bot: str | None = None,
    ) -> Conversation:
        """A new, empty conversation; one with no title takes its first message's, and one given
        a title keeps it (`TitleSource.USER`). `bot` is the bot it belongs to; none, the main
        agent's."""
        now = utc_now()
        conversation_id = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        stamp = format_utc(now)
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "INSERT INTO conversations_threads "
                    "(id, source, title, parent_id, started_at, updated_at, bot, title_source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        conversation_id,
                        source,
                        title,
                        parent_id,
                        stamp,
                        stamp,
                        bot,
                        (TitleSource.USER if title else TitleSource.DERIVED).value,
                    ),
                )
        finally:
            connection.close()
        return Conversation(self._database, conversation_id, [], [])

    def open(self, conversation_id: str) -> Conversation:
        """A stored conversation, its history as the model last had it: the active messages."""
        connection = self._database.connect()
        try:
            self._info(connection, conversation_id)
            rows: list[tuple[int, float, str, str]] = connection.execute(
                "SELECT number, position, role, body FROM conversations_messages "
                "WHERE conversation_id = ? AND active = 1 ORDER BY position",
                (conversation_id,),
            ).fetchall()
        finally:
            connection.close()
        known = [
            _Known(decode(role, body), number, position) for number, position, role, body in rows
        ]
        history = [entry.message for entry in known]
        return Conversation(self._database, conversation_id, history, known)

    def model(self, conversation_id: str) -> ModelChoice | None:
        """The model the conversation picked in the app, or `None` when it picked none."""
        connection = self._database.connect()
        try:
            self._info(connection, conversation_id)
            row: tuple[str | None, str | None, str | None] = connection.execute(
                "SELECT model_provider, model_name, model_effort FROM conversations_threads "
                "WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        finally:
            connection.close()
        provider, model, effort = row
        if provider is None or model is None:
            return None
        return ModelChoice(provider, model, effort)

    def set_model(self, conversation_id: str, choice: ModelChoice) -> None:
        """Keep the model the conversation picked, so it survives a restart."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                self._info(connection, conversation_id)
                connection.execute(
                    "UPDATE conversations_threads SET model_provider = ?, model_name = ?, "
                    "model_effort = ? WHERE id = ?",
                    (choice.provider, choice.model, choice.effort, conversation_id),
                )
        finally:
            connection.close()

    def title_source(self, conversation_id: str) -> TitleSource:
        """Who wrote the conversation's title."""
        connection = self._database.connect()
        try:
            self._info(connection, conversation_id)
            row: tuple[str] = connection.execute(
                "SELECT title_source FROM conversations_threads WHERE id = ?", (conversation_id,)
            ).fetchone()
        finally:
            connection.close()
        return TitleSource(row[0])

    def retitle(self, conversation_id: str, title: str, source: TitleSource) -> bool:
        """Give the conversation `title`, unless its title came from a higher authority (the check
        and the write in one transaction, so a name given meanwhile is never overwritten). `False`
        when it kept its title."""
        title = title.strip()
        if not title:
            return False
        connection = self._database.connect()
        try:
            with transaction(connection):
                self._info(connection, conversation_id)
                row: tuple[str] = connection.execute(
                    "SELECT title_source FROM conversations_threads WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
                if TitleSource(row[0]).rank > source.rank:
                    return False
                connection.execute(
                    "UPDATE conversations_threads SET title = ?, title_source = ? WHERE id = ?",
                    (title, source.value, conversation_id),
                )
        finally:
            connection.close()
        return True

    def rename(self, conversation_id: str, title: str) -> str:
        """Name a conversation as the user chose (step 9e): `title`'s words on one line, cut to a
        title's length, as `user`, which no title model replaces. The name kept; empty, and
        nothing changed, when `title` holds no words."""
        name = given_title(title)
        if name:
            self.retitle(conversation_id, name, TitleSource.USER)
        return name

    def name_untitled(self, conversation_id: str, said: UserMessage) -> bool:
        """Give a conversation with no title yet the one its first message makes, as that
        message's turn starts: the list shows only titled conversations, and the save that wrote
        the title came after the screens read the list, so a new conversation stayed out of it
        while its first turn ran, waiting for an approval as well (found by the UI review on
        25/10/2026). `False` when it had a title, or the message makes none (Atlas's own)."""
        title = _title([said])
        if not title:
            return False
        connection = self._database.connect()
        try:
            with transaction(connection):
                named = connection.execute(
                    "UPDATE conversations_threads SET title = ? WHERE id = ? AND title = ''",
                    (title, conversation_id),
                ).rowcount
        finally:
            connection.close()
        return named > 0

    def info(self, conversation_id: str) -> ConversationInfo:
        connection = self._database.connect()
        try:
            return self._info(connection, conversation_id)
        finally:
            connection.close()

    def fixed(self, bot: str) -> ConversationInfo | None:
        """A bot's fixed conversation, or `None` when it has none yet."""
        connection = self._database.connect()
        try:
            row = connection.execute(
                f"SELECT {_INFO_COLUMNS} FROM conversations_threads t "  # noqa: S608
                "WHERE t.source = ? AND t.bot = ? ORDER BY t.started_at DESC LIMIT 1",
                (BOT, bot),
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else ConversationInfo(*row)

    def bots(self) -> dict[str, str]:
        """The bot of each conversation that belongs to one, by the conversation's id."""
        connection = self._database.connect()
        try:
            rows: list[tuple[str, str]] = connection.execute(
                "SELECT id, bot FROM conversations_threads WHERE bot IS NOT NULL"
            ).fetchall()
        finally:
            connection.close()
        return dict(rows)

    def release(self, bot: str) -> int:
        """Let go of a deleted bot's conversations: they are kept, as every conversation is, and
        a bot later made under the same name starts its own. How many there were."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                return connection.execute(
                    "UPDATE conversations_threads SET bot = NULL WHERE bot = ?", (bot,)
                ).rowcount
        finally:
            connection.close()

    def recent(
        self,
        limit: int,
        *,
        exclude: str | None = None,
        bot: str | None = None,
        with_bots: bool = False,
    ) -> list[ConversationInfo]:
        """The conversations with the latest activity. By default the user's own with the main
        agent, as the list of conversations shows them: a bot's fixed conversation is in the
        bots' list. `bot` gives that bot's alone; `with_bots`, every agent's, as the main
        agent's search reads them."""
        unlisted = HIDDEN_SOURCES if bot is not None or with_bots else _UNLISTED
        owned = "" if bot is None else "AND t.bot = ? "
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_INFO_COLUMNS} FROM conversations_threads t "  # noqa: S608
                f"WHERE t.source NOT IN ({_marks(unlisted)}) AND t.id != ? AND t.title != '' "
                f"{owned}ORDER BY t.updated_at DESC, t.id DESC LIMIT ?",
                (*unlisted, exclude or "", *(() if bot is None else (bot,)), limit),
            ).fetchall()
        finally:
            connection.close()
        return [ConversationInfo(*row) for row in rows]

    def page(
        self, limit: int, after: Cursor | None = None
    ) -> tuple[list[ConversationInfo], Cursor | None]:
        """A page of the list of conversations, as the app's sidebar shows it (step 9e): the
        user's own with the main agent, the latest activity first and the id breaking a tie, from
        the top or right after `after`. With it, where the page that follows starts; `None` when
        this one is the last."""
        bound = "" if after is None else "AND (t.updated_at, t.id) < (?, ?) "
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT {_INFO_COLUMNS} FROM conversations_threads t "  # noqa: S608
                f"WHERE t.source NOT IN ({_marks(_UNLISTED)}) AND t.title != '' {bound}"
                "ORDER BY t.updated_at DESC, t.id DESC LIMIT ?",
                (*_UNLISTED, *(() if after is None else (after.at, after.id)), limit + 1),
            ).fetchall()
        finally:
            connection.close()
        found = [ConversationInfo(*row) for row in rows[:limit]]
        if len(rows) <= limit or not found:
            return found, None
        return found, Cursor(found[-1].updated_at, found[-1].id)

    def search(
        self,
        query: str,
        *,
        limit: int,
        roles: Sequence[Role] = (Role.USER, Role.ASSISTANT),
        current: str | None = None,
        excluded: Sequence[str] = (),
        after: str | None = None,
        before: str | None = None,
        newest_first: bool | None = None,
        bot: str | None = None,
        listed: bool = False,
        marks: tuple[str, str] = ("»", "«"),
    ) -> list[Hit]:
        """The conversations whose messages match `query` (cleaned with `fts_query`), each through
        its best match: by relevance, or by the match's time when `newest_first` is set.

        `current` is the conversation the search runs in: only its archived messages are searched,
        because the rest is in the model's context already. `after` and `before` bound when a
        conversation started, in Atlas's UTC text. A scheduled job's conversations come after the
        user's own. `bot` keeps the search inside that bot's conversations; unset, every agent's
        are searched, as the main agent searches (docs/ARCHITECTURE.md, Bots). `listed` keeps it
        inside the list of conversations, as the app's sidebar shows it (step 9e); `marks` open
        and close a match in the snippet."""
        where, values = _filters(roles, current, excluded, after, before, bot, listed=listed)
        connection = self._database.connect()
        try:
            rows = connection.execute(
                f"SELECT m.number, m.conversation_id, m.role, m.kind, m.text, m.tool_name, "  # noqa: S608
                f"m.created_at, m.active, t.source, "
                f"snippet(conversations_search, 0, ?, ?, ' … ', {SNIPPET_TOKENS}) "
                "FROM conversations_search "
                "JOIN conversations_messages m ON m.number = conversations_search.rowid "
                "JOIN conversations_threads t ON t.id = m.conversation_id "
                f"WHERE conversations_search MATCH ? AND {where} "
                "ORDER BY bm25(conversations_search) LIMIT ?",
                (*marks, query, *values, SCAN_ROWS),
            ).fetchall()
            rows.sort(key=lambda row: str(row[8]) in DEMOTED_SOURCES)  # stable: keeps the ranking
            best: dict[str, tuple[StoredMessage, str]] = {}
            for row in rows:
                match = _stored(row[:8])
                best.setdefault(match.conversation_id, (match, str(row[9])))
            found = list(best.values())
            if newest_first is not None:
                found.sort(key=lambda hit: hit[0].created_at, reverse=newest_first)
            return [
                Hit(self._info(connection, match.conversation_id), match, snippet)
                for match, snippet in found[:limit]
            ]
        finally:
            connection.close()

    def find(self, text: str, limit: int) -> tuple[list[Found], bool]:
        """The list's search (step 9e): the conversations of the list whose title or messages hold
        every word of `text`, each word also matching the words it begins (`typed_query`). Those
        found by their title come first, the latest activity first, then those found by a message,
        the best match first; with them, whether more than `limit` matched.

        No index of its own: the messages are read through the search index, and the titles as
        they are, compared word by word as the index compares words (`title_words`)."""
        query = typed_query(text)
        if query is None:
            return [], False
        words = title_words(query)
        titled: list[tuple[str, list[str]]] = []
        connection = self._database.connect()
        try:
            if words:
                rows: list[tuple[str, str]] = connection.execute(
                    "SELECT t.id, t.title FROM conversations_threads t "  # noqa: S608
                    f"WHERE t.source NOT IN ({_marks(_UNLISTED)}) AND t.title != '' "
                    "ORDER BY t.updated_at DESC, t.id DESC",
                    _UNLISTED,
                ).fetchall()
                for conversation_id, title in rows:
                    pieces = title_pieces(title, words)
                    if pieces is not None:
                        titled.append((conversation_id, pieces))
            found = [
                Found(self._info(connection, conversation_id), tuple(pieces))
                for conversation_id, pieces in titled[:limit]
            ]
        finally:
            connection.close()
        named = {conversation_id for conversation_id, _ in titled}
        hits = [
            hit
            for hit in self.search(query, limit=SCAN_ROWS, listed=True, marks=MATCH_MARKS)
            if hit.conversation.id not in named
        ]
        found += [
            Found(
                hit.conversation,
                (hit.conversation.title,),
                tuple(snippet_pieces(hit.snippet)),
                hit.match.role.value,
            )
            for hit in hits[: max(0, limit - len(found))]
        ]
        return found, len(titled) + len(hits) > limit

    def missing_terms(
        self,
        query: str,
        *,
        roles: Sequence[Role] = (Role.USER, Role.ASSISTANT),
        current: str | None = None,
        excluded: Sequence[str] = (),
        after: str | None = None,
        before: str | None = None,
        bot: str | None = None,
    ) -> list[str]:
        """The words of a cleaned query that no message a search with the same filters reads
        contains."""
        where, values = _filters(roles, current, excluded, after, before, bot)
        connection = self._database.connect()
        try:
            return [
                term
                for term in query_terms(query)
                if connection.execute(
                    "SELECT 1 FROM conversations_search "  # noqa: S608
                    "JOIN conversations_messages m ON m.number = conversations_search.rowid "
                    "JOIN conversations_threads t ON t.id = m.conversation_id "
                    f"WHERE conversations_search MATCH ? AND {where} LIMIT 1",
                    (term, *values),
                ).fetchone()
                is None
            ]
        finally:
            connection.close()

    def titled(
        self,
        title: str,
        *,
        current: str | None = None,
        excluded: Sequence[str] = (),
        after: str | None = None,
        before: str | None = None,
        bot: str | None = None,
    ) -> ConversationInfo | None:
        """The user's latest conversation whose title is exactly `title` (models quote a title the
        list of recent conversations showed them), within the filters."""
        title = title.strip().strip("`'\"")
        if not title:
            return None
        clauses = [f"t.source NOT IN ({_marks(HIDDEN_SOURCES)})", "t.title = ?", "t.id != ?"]
        values: list[object] = [*HIDDEN_SOURCES, title, current or ""]
        if excluded:
            clauses.append(f"t.id NOT IN ({_marks(excluded)})")
            values += excluded
        if after is not None:
            clauses.append("t.started_at >= ?")
            values.append(after)
        if before is not None:
            clauses.append("t.started_at < ?")
            values.append(before)
        if bot is not None:
            clauses.append("t.bot = ?")
            values.append(bot)
        connection = self._database.connect()
        try:
            row = connection.execute(
                f"SELECT {_INFO_COLUMNS} FROM conversations_threads t "  # noqa: S608
                f"WHERE {' AND '.join(clauses)} ORDER BY t.started_at DESC LIMIT 1",
                values,
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else ConversationInfo(*row)

    def around(self, number: int, size: int) -> tuple[StoredMessage | None, list[StoredMessage]]:
        """The stored message `number` and up to `size` messages on each side of it, in their
        order, archived ones included and instructions left out."""
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT conversation_id, position FROM conversations_messages WHERE number = ?",
                (number,),
            ).fetchone()
            if row is None:
                return None, []
            conversation_id, position = row
            before = connection.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM conversations_messages "  # noqa: S608
                "WHERE conversation_id = ? AND role != 'system' AND position <= ? "
                "ORDER BY position DESC LIMIT ?",
                (conversation_id, position, size + 1),
            ).fetchall()
            after = connection.execute(
                f"SELECT {_MESSAGE_COLUMNS} FROM conversations_messages "  # noqa: S608
                "WHERE conversation_id = ? AND role != 'system' AND position > ? "
                "ORDER BY position LIMIT ?",
                (conversation_id, position, size),
            ).fetchall()
        finally:
            connection.close()
        window = [_stored(row) for row in reversed(before)] + [_stored(row) for row in after]
        anchor = next((message for message in window if message.number == number), None)
        return anchor, window

    def last_said(self, conversation_id: str) -> str:
        """The last thing the user or the agent said in a conversation; nothing when it is
        empty or unknown."""
        connection = self._database.connect()
        try:
            row: tuple[str] | None = connection.execute(
                "SELECT text FROM conversations_messages WHERE conversation_id = ? "
                "AND role IN ('user', 'assistant') AND kind = 'message' AND text != '' "
                "ORDER BY position DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        finally:
            connection.close()
        return "" if row is None else row[0]

    def messages(self, conversation_id: str) -> list[StoredMessage]:
        """Every stored message of a conversation in order, archived ones included, instructions
        left out."""
        connection = self._database.connect()
        try:
            self._info(connection, conversation_id)
            rows = connection.execute(
                f"SELECT {_MESSAGE_COLUMNS}, body FROM conversations_messages "  # noqa: S608
                "WHERE conversation_id = ? AND role != 'system' ORDER BY position",
                (conversation_id,),
            ).fetchall()
        finally:
            connection.close()
        return [shown(_stored(row[:-1]), str(row[-1])) for row in rows]

    @staticmethod
    def _info(connection: sqlite3.Connection, conversation_id: str) -> ConversationInfo:
        row = connection.execute(
            f"SELECT {_INFO_COLUMNS} FROM conversations_threads t WHERE t.id = ?",  # noqa: S608
            (conversation_id,),
        ).fetchone()
        if row is None:
            raise ConversationNotFoundError(
                f"There is no conversation {conversation_id!r}. Copy the id exactly as a search or "
                "the list of recent conversations shows it."
            )
        return ConversationInfo(*row)


def _insert(
    connection: sqlite3.Connection,
    conversation_id: str,
    message: Message,
    position: float,
    names: dict[str, str],
    now: str,
) -> int:
    role = role_of(message)
    tool_name: str | None = None
    if isinstance(message, ToolResult):
        tool_name = names.get(message.call_id)
    elif isinstance(message, AssistantMessage) and message.tool_calls:
        tool_name = ", ".join(call.name for call in message.tool_calls)
    cursor = connection.execute(
        "INSERT INTO conversations_messages "
        "(conversation_id, position, role, kind, text, tool_name, body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            conversation_id,
            position,
            role.value,
            kind_of(message).value,
            message.text,
            tool_name,
            encode(message),
            now,
        ),
    )
    number = cursor.lastrowid
    if number is None:  # pragma: no cover - an INSERT always has a row id
        raise RuntimeError("SQLite gave no number to a stored message.")
    return number


def _between(previous: float | None, following: float | None) -> float:
    """A position after `previous` and before `following`."""
    if previous is None:
        return 0.0 if following is None else following - 1.0
    if following is None:
        return previous + 1.0
    return (previous + following) / 2


def _next(positions: Sequence[float | None], index: int) -> float | None:
    return next((p for p in positions[index + 1 :] if p is not None), None)


def _tool_names(history: Iterable[Message]) -> dict[str, str]:
    return {
        call.id: call.name
        for message in history
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
    }


def _title(history: Iterable[Message]) -> str:
    first = next((m for m in history if isinstance(m, UserMessage) and not from_atlas(m)), None)
    if first is None:
        return ""
    if not first.text.strip() and first.attachments:
        # Files sent with no words are called by their names (step 16g).
        return title_from(", ".join(attachment.name for attachment in first.attachments))
    # A conversation begun with a command the user ran is called by the command (step 16f), one
    # begun with a skill by `/name` and what came with it.
    return title_from(typed_text(first.text))


def _stored(row: Sequence[object]) -> StoredMessage:
    number, conversation_id, role, kind, text, tool_name, created_at, active = row
    return StoredMessage(
        int(str(number)),
        str(conversation_id),
        Role(str(role)),
        Kind(str(kind)),
        str(text),
        None if tool_name is None else str(tool_name),
        str(created_at),
        bool(active),
    )


def _filters(
    roles: Sequence[Role],
    current: str | None,
    excluded: Sequence[str],
    after: str | None,
    before: str | None,
    bot: str | None = None,
    *,
    listed: bool = False,
) -> tuple[str, list[object]]:
    """The WHERE clauses a search and its missing words share, and their values; `listed`, only
    the conversations of the list (step 9e)."""
    clauses = [
        "m.kind = 'message'",
        f"m.role IN ({_marks(roles)})",
        f"t.source NOT IN ({_marks(HIDDEN_SOURCES)})",
        "(m.conversation_id != ? OR m.active = 0)",
    ]
    values: list[object] = [*roles, *HIDDEN_SOURCES, current or ""]
    if excluded:
        clauses.append(f"m.conversation_id NOT IN ({_marks(excluded)})")
        values += excluded
    if after is not None:
        clauses.append("t.started_at >= ?")
        values.append(after)
    if before is not None:
        clauses.append("t.started_at < ?")
        values.append(before)
    if bot is not None:
        clauses.append("t.bot = ?")
        values.append(bot)
    if listed:
        # A bot's conversation lives in the bots' list, and one with no title yet in none.
        clauses += ["t.source != ?", "t.title != ''"]
        values.append(BOT)
    return " AND ".join(clauses), values


def _marks(values: Sequence[object]) -> str:
    return ", ".join("?" * len(values))
