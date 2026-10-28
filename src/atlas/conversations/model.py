"""Stored conversations and their search: the records, and the cleaning of a search query. Pure.

**Sessions, for good.** Every message of a conversation is kept raw in the database, with
a number that only grows. A compaction archives the messages it replaced instead of deleting them,
and they stay searchable; the conversation keeps its id for life (in-place compaction). Atlas
never deletes transcripts, because recalling what was said is what replaces a notebook here
(docs/ARCHITECTURE.md, Memory).

**A query is cleaned before it reaches FTS5**: FTS5 has its own grammar, and a stray `"`, `:`,
`(` or `/` raises instead of finding nothing. Balanced quoted phrases stay; a word joined by `-`,
`.` or `_` (`chat-send`, `P2.2`) becomes a phrase, or FTS5 would split it; a trailing `*` stays a
prefix search; `AND`, `OR` and `NOT` stay operators between words. Every other character outside
a phrase becomes a space: stripping a measured list would let anything off the list (a final `.`,
a leading `-`) reach FTS5 raw and raise, which a search turns into zero results.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Self

#: Where a conversation came from. The user's own are searched; a review's or a subagent's are not
#: the user's history.
APP = "app"
#: A subagent's conversation (step 12b), hidden from the user's history.
SUBAGENT = "subagent"
#: What an executor instance of the user's said and did, on the workbench (step 16c): opened from
#: its card, hidden like a subagent's.
WORKBENCH = "workbench"
HIDDEN_SOURCES = ("review", SUBAGENT, WORKBENCH)
#: Searched, but after the user's own conversations: a scheduled job repeats the same words every
#: run and, under plain ranking, crowds out what the user said.
#: A routine's run (step 12).
ROUTINE = "routine"
DEMOTED_SOURCES = (ROUTINE,)
#: A bot's fixed conversation (step 13b): it lives in the bots' list, not
#: among the user's conversations.
BOT = "bot"
#: Conversations a search may be told to leave out, at most.
MAX_EXCLUDED = 20

_RELATIVE_BOUND = re.compile(r"^(\d+)\s*(h|d|w)$", re.IGNORECASE)
_UNIT_HOURS = {"h": 1, "d": 24, "w": 24 * 7}

#: The longest query read; nobody searches with more.
MAX_QUERY_CHARS = 2_048
#: The longest title, cut from the first thing the user said.
TITLE_CHARS = 80

OPERATORS = frozenset({"AND", "OR", "NOT"})
_JOINED_WORD = re.compile(r"\b\w+(?:[._-]\w+)+\b")
_NOT_A_WORD = re.compile(r"[^\w\s*]")
#: A stored time as Atlas writes it (`atlas.home.format_utc`).
_STORED_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
#: How the list's search marks a match in a snippet (step 9e): characters nobody types, so the
#: words of a message never pass for a mark.
MATCH_MARKS = ("\x02", "\x03")
_MARKED = re.compile("\x02([^\x02\x03]*)\x03")
#: A word of a title, as the search index's tokenizer cuts words: letters and digits.
_TITLE_WORD = re.compile(r"[^\W_]+")
_TERM = re.compile(r'"[^"]*"|\S+')


class TitleSource(StrEnum):
    """Who wrote a conversation's title, its provenance: a title replaces only one of lower
    authority, so the model's never replaces a name someone gave, and the instant one never
    replaces the model's."""

    #: The instant title, cut from what the user said, or the model's placeholder for a greeting.
    DERIVED = "derived"
    #: A small model's (atlas.assistant.titles).
    LLM = "llm"
    #: A name someone gave: a bot's, a routine's, a subagent's goal, or the user's.
    USER = "user"

    @property
    def rank(self) -> int:
        return tuple(TitleSource).index(self)


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Kind(StrEnum):
    """What a stored message is, beyond its role."""

    MESSAGE = "message"
    #: A compaction's summary of the messages it archived.
    SUMMARY = "summary"
    #: The note a model switch adds to the history: the
    #: user never wrote it, so the screen, the search and the title leave it out.
    MODEL_SWITCH = "model_switch"
    #: A notice that started a turn, such as a background command that ended: Atlas wrote it, so
    #: the search, the title and the review leave it out, and the screen shows it as a notice.
    NOTICE = "notice"
    #: A command the user ran from the conversation and its output (step 16f): the screen draws
    #: it as a terminal row.
    TERMINAL = "terminal"
    #: A skill the user invoked with `/name` (its invocation message, the skill's text
    #: loaded): the screen shows the `/name` they typed, and the title is made from it.
    SKILL = "skill"


@dataclass(frozen=True, slots=True)
class ConversationInfo:
    """A conversation as lists show it."""

    id: str
    title: str
    source: str
    started_at: str
    updated_at: str
    #: Stored messages, archived ones included, instructions left out.
    messages: int
    #: The bot whose conversation it is; `None` for the main agent's.
    bot: str | None = None
    #: When the user archived it, out of the list of conversations (step 9e); `None` while it is
    #: in the list. Not its archived messages, which a compaction replaced.
    archived_at: str | None = None


@dataclass(frozen=True, slots=True)
class Cursor:
    """Where a page of the list of conversations starts: right after the row with this time and
    id, the list running from the latest time down and the id breaking a tie (step 9e). A
    conversation whose activity moves it up meanwhile lands above the cursor, so the next page
    neither repeats it nor skips one."""

    #: The time the list is sorted by, as stored.
    at: str
    id: str

    @property
    def text(self) -> str:
        """The cursor as a client hands it back."""
        return f"{self.at} {self.id}"

    @classmethod
    def read(cls, text: str) -> Self | None:
        """The cursor `text` holds, or `None` when it holds none."""
        at, _, conversation_id = text.partition(" ")
        if (
            not _STORED_TIME.fullmatch(at)
            or not conversation_id
            or conversation_id.split() != [conversation_id]
        ):
            return None
        return cls(at, conversation_id)


@dataclass(frozen=True, slots=True)
class StoredCall:
    """A tool call an assistant message made, as the screen shows it again."""

    id: str
    name: str
    #: The arguments as the model wrote them, JSON text.
    arguments: str


@dataclass(frozen=True, slots=True)
class StoredAttachment:
    """A file a user message came with, as the screen shows it again."""

    id: str
    name: str
    media_type: str
    size: int
    #: `image`, `document`, `text`, `video`, `audio` or `other` (`atlas.models.AttachmentKind`).
    kind: str


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """One message of a conversation as the database keeps it, for showing and scrolling."""

    #: The message's number, unique in the home, which scrolling starts from.
    number: int
    conversation_id: str
    role: Role
    kind: Kind
    #: The searchable text: what the user or the agent said, or what a tool returned.
    text: str
    #: The tool a result came from, or the tools an assistant message called.
    tool_name: str | None
    created_at: str
    #: In the history the model gets; an archived message was replaced by a compaction.
    active: bool
    #: What the screen shows of the body when a conversation is reopened as it was seen live:
    #: an assistant message's readable reasoning and its calls, a result's call and
    #: how it ended. Read only for a conversation's own messages, never for a search.
    reasoning: str = ""
    calls: tuple[StoredCall, ...] = ()
    call_id: str | None = None
    status: str | None = None
    #: The files a user message came with.
    attachments: tuple[StoredAttachment, ...] = ()


@dataclass(frozen=True, slots=True)
class Hit:
    """A conversation a search found, through its best matching message."""

    conversation: ConversationInfo
    match: StoredMessage
    #: The match's words in their surroundings, marked with » and « (or the marks asked for).
    snippet: str


@dataclass(frozen=True, slots=True)
class Found:
    """A conversation the list's search found, and where it matched (step 9e): its title in pieces,
    the odd ones the words that matched; and, when its title did not match, the words around the
    match in its best message, in pieces as well, with who said them."""

    conversation: ConversationInfo
    title: tuple[str, ...]
    snippet: tuple[str, ...] = ()
    #: The role of the message the snippet comes from.
    said_by: str = ""


def fts_query(text: str) -> str | None:
    """`text` as a query FTS5 accepts, or `None` when nothing searchable is left."""
    terms: list[str] = []
    for piece, is_phrase in _pieces(text[:MAX_QUERY_CHARS]):
        if is_phrase:
            terms.append(f'"{piece}"')
            continue
        for token in _NOT_A_WORD.sub(" ", piece).split():
            if token in OPERATORS:
                if terms and terms[-1] not in OPERATORS:
                    terms.append(token)
                continue
            word = token.replace("*", "")
            if word:
                terms.append(word + ("*" if token.endswith("*") else ""))
    while terms and terms[-1] in OPERATORS:
        terms.pop()
    return " ".join(terms) or None


def _pieces(text: str) -> list[tuple[str, bool]]:
    """`text` cut into plain pieces and phrases (`True`): the balanced quoted ones, and the words
    joined by `-`, `.` or `_`. An unbalanced quote is dropped."""
    pieces: list[tuple[str, bool]] = []
    start = 0
    while (opening := text.find('"', start)) != -1:
        closing = text.find('"', opening + 1)
        if closing == -1:
            break
        pieces.extend(_joined(text[start:opening]))
        phrase = text[opening + 1 : closing].strip()
        if phrase:
            pieces.append((phrase, True))
        start = closing + 1
    pieces.extend(_joined(text[start:].replace('"', " ")))
    return pieces


def _joined(text: str) -> list[tuple[str, bool]]:
    pieces: list[tuple[str, bool]] = []
    start = 0
    for match in _JOINED_WORD.finditer(text):
        pieces.append((text[start : match.start()], False))
        pieces.append((match.group(0), True))
        start = match.end()
    pieces.append((text[start:], False))
    return pieces


def query_terms(query: str) -> list[str]:
    """The words and phrases of a cleaned query, operators left out."""
    terms: list[str] = []
    for match in _TERM.finditer(query):
        term = match.group(0)
        if term not in OPERATORS:
            terms.append(term)
    return terms


def typed_query(text: str) -> str | None:
    """What a search field finds as the user types (step 9e): `fts_query`'s query, each of its
    words, and a phrase's last word, also matching the longer words it begins; `None` when nothing
    searchable is left."""
    query = fts_query(text)
    if query is None:
        return None
    return " ".join(
        term if term in OPERATORS or term.endswith("*") else f"{term}*"
        for term in _TERM.findall(query)
    )


def folded(text: str) -> str:
    """`text` as a search compares words: in lower case and without accents, as the search index's
    tokenizer reads them (`remove_diacritics 2`)."""
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


def title_words(query: str) -> list[str]:
    """The words a title is compared with, folded, from a cleaned query: none when the query holds
    an operator, which only the search index reads."""
    terms = _TERM.findall(query)
    if any(term in OPERATORS for term in terms):
        return []
    return [folded(word) for term in terms for word in _TITLE_WORD.findall(term)]


def title_pieces(title: str, words: Sequence[str]) -> list[str] | None:
    """`title` cut where its words begin with one of `words`: the odd pieces are those words,
    whole. `None` when one of `words` begins none of its words, or there are none (step 9e)."""
    if not words:
        return None
    pieces: list[str] = []
    start = 0
    begun: set[str] = set()
    for match in _TITLE_WORD.finditer(title):
        word = folded(match.group(0))
        begins = {typed for typed in words if word.startswith(typed)}
        if begins:
            begun |= begins
            pieces += [title[start : match.start()], match.group(0)]
            start = match.end()
    if begun != set(words):
        return None
    pieces.append(title[start:])
    return pieces


def snippet_pieces(snippet: str) -> list[str]:
    """A snippet marked with `MATCH_MARKS`, cut into pieces: the odd ones are what matched. A mark
    a message held itself is dropped, and never shifts a piece (step 9e)."""
    start, end = MATCH_MARKS
    return [piece.replace(start, "").replace(end, "") for piece in _MARKED.split(snippet)]


def time_bound(text: str, now: datetime) -> datetime | None:
    """A search's time bound: `7d`, `24h` or `2w` is that long before `now`;
    otherwise an ISO date or date and time, UTC when no zone is given (a date alone is midnight
    UTC). `None` when it is neither."""
    text = text.strip()
    relative = _RELATIVE_BOUND.match(text)
    if relative:
        return now - timedelta(
            hours=int(relative.group(1)) * _UNIT_HOURS[relative.group(2).lower()]
        )
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def title_from(text: str) -> str:
    """A conversation's title: the first line the user wrote, cut."""
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if len(line) <= TITLE_CHARS:
        return line
    return line[: TITLE_CHARS - 1].rstrip() + "…"


def given_title(text: str) -> str:
    """A name the user gave a conversation (step 9e): its words on one line, cut to a title's
    length; empty when it has none."""
    return " ".join(text.split())[:TITLE_CHARS].rstrip()
