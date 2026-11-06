"""The profile and the memory: entries, the operations that change them, and the texts that teach
the model to keep them. Pure, no I/O.

The built-in memory (docs/ARCHITECTURE.md, Memory):

- **Two small files, always in the prompt.** `USER.md`, who the user is (1,375 characters), and
  `MEMORY.md`, facts about the environment (2,200), by default: the home's `[memory]` changes
  them. Limits are in characters, not tokens, because characters do not depend on the model.
  Everything else is recalled from past conversations, and what is learned doing a kind of task
  belongs in that task's skill.
- **Entries separated by a line holding `§`**, each written whole by the model.
- **One call, several changes, checked once.** Operations apply in order to a copy and the limit is
  checked on the result only, so a single call can free room and add. One that fails leaves
  everything as it was. A plain single change is a batch of one.
- **Finding the entry:** an entry equal to `old_text` first, so a short entry stays reachable when
  a longer one contains it; then the entries containing it; then the same with quotes, dashes and
  spaces folded, because models retype `“` as `"` and `—` as `-`.
- **Over the limit, or when nothing matches, a single change shows the current entries**, so the
  model consolidates in the same turn. A failed batch does not repeat them: the model already has
  them, and repeating them grows the context the consolidation is meant to shrink. When nothing
  matches, the closest entries are named.
- **What a replace or a remove overwrote is said back in full**, so a whole-entry write is never
  silent about what it lost.
- **A file with an entry longer than the whole limit** was written outside the tool (an append
  without `§`), and rewriting it would cut that text, so it is left alone. Blank lines and spacing
  are not that: parsing only strips whitespace.

**A memory proposal** is a staged write (docs/ARCHITECTURE.md, Memory proposals): a background
review's call that replaces or removes an entry, kept whole for the user. Each replace and remove
records the whole entry it selected when the call was made, and approval applies to exactly that
entry or not at all: replaying the search later could hit a newer entry that still contains
`old_text`, one nobody reviewed.

**The soul** is `SOUL.md` (docs/ARCHITECTURE.md, The soul): the main agent's identity, at the head
of its instructions. Past 20,000 characters, its head and its tail stay, with a marker naming the
file to read whole.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

ENTRY_DELIMITER = "\n§\n"


class Target(StrEnum):
    """Which of the two stores an operation changes."""

    #: Who the user is: persona, preferences, communication and work style.
    USER = "user"
    #: Facts about the environment: tool quirks, conventions, paths that matter.
    MEMORY = "memory"


FILES = {Target.USER: "USER.md", Target.MEMORY: "MEMORY.md"}
#: The limits, in characters, when the home's `[memory]` sets none.
LIMITS = {Target.USER: 1_375, Target.MEMORY: 2_200}
HEADERS = {
    Target.USER: "USER PROFILE (who the user is)",
    Target.MEMORY: "MEMORY (your personal notes)",
}
#: The closest entries named when nothing matches, and how many of `old_text`'s words they share.
CLOSEST = 3
CLOSEST_SHARE = 0.3

#: Quotes and dashes a model retypes differently from the stored entry (curly quotes, en and em
#: dashes, the minus sign), folded to one straight form.
_QUOTES = '\u201c\u201d\u2018\u2019\u201e\u201f\u201a\u201b"`'
_DASHES = "\u2212\u2013\u2014\u2010\u2011\u2012\u2015"
_FOLD = str.maketrans(_QUOTES + _DASHES, "'" * len(_QUOTES) + "-" * len(_DASHES))


class Action(StrEnum):
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"


@dataclass(frozen=True, slots=True)
class Operation:
    action: Action
    #: The entry to add, or the whole new text of the entry replaced.
    content: str = ""
    #: A short piece of the entry to replace or remove, unique among the entries.
    old_text: str = ""
    #: For a proposed replace or remove, the whole entry it selected when it was proposed: it
    #: applies to exactly that entry, or not at all.
    entry: str | None = None


@dataclass(frozen=True, slots=True)
class Applied:
    """The result of a batch: the new entries, or why nothing changed."""

    entries: list[str] | None
    #: What the model reads when nothing changed. `None` on success.
    error: str | None = None
    #: The batch would leave the store over its limit, or one change found nothing to change:
    #: a failure of consolidating, which the tool counts.
    consolidation: bool = False
    #: The model should see the current entries to retry: a single change over the limit or
    #: finding nothing, or missing its old_text.
    show_entries: bool = False
    #: When nothing matched: the entries that share the most words with old_text.
    closest: list[str] = field(default_factory=list[str])
    #: When old_text was ambiguous: the start of each entry it is in.
    matches: list[str] = field(default_factory=list[str])
    #: The entries differ from what they were; an entry added twice changes nothing.
    changed: bool = True
    #: The whole text each replace and remove overwrote, by the operation's number.
    replaced: dict[int, str] = field(default_factory=dict[int, str])
    removed: dict[int, str] = field(default_factory=dict[int, str])


def parse_entries(raw: str) -> list[str]:
    """A file's entries, stripped, the empty and repeated ones left out."""
    entries = [entry.strip() for entry in raw.split(ENTRY_DELIMITER)]
    return list(dict.fromkeys(entry for entry in entries if entry))


def render_entries(entries: list[str]) -> str:
    return ENTRY_DELIMITER.join(entries)


def size(entries: list[str]) -> int:
    """The characters the entries take, delimiters included: what the limit counts."""
    return len(render_entries(entries))


def usage(entries: list[str], limit: int) -> str:
    current = size(entries)
    percent = min(100, current * 100 // limit) if limit > 0 else 0
    return f"{percent}% — {current:,}/{limit:,} chars"


def drifted(raw: str, limit: int) -> bool:
    """Whether a file holds an entry longer than the whole limit: text written outside the tool,
    which rewriting the file would cut."""
    return max(map(len, parse_entries(raw)), default=0) > limit


def find(entries: list[str], old_text: str) -> tuple[int | None, list[int]]:
    """The entry `old_text` selects, and every entry it matched. An equal entry wins; then the
    entries containing it; then the same with quotes, dashes and spaces folded."""
    matches = [i for i, entry in enumerate(entries) if entry == old_text]
    if not matches:
        matches = [i for i, entry in enumerate(entries) if old_text in entry]
    if not matches:
        needle = _folded(old_text)
        if any(c.isalnum() for c in needle):  # punctuation alone would select a whole entry
            matches = [i for i, entry in enumerate(entries) if needle in _folded(entry)]
    if len({entries[i] for i in matches}) > 1:
        return None, matches
    return (matches[0] if matches else None), matches


def closest(entries: list[str], old_text: str) -> list[str]:
    """The entries sharing the most words with `old_text`, at least 30% of them."""
    words = _words(old_text)
    if not words:
        return []
    scored = sorted(
        ((len(words & _words(entry)) / len(words), i) for i, entry in enumerate(entries)),
        key=lambda pair: (-pair[0], pair[1]),
    )
    return [entries[i] for score, i in scored[:CLOSEST] if score >= CLOSEST_SHARE]


def apply(entries: list[str], operations: list[Operation], limit: int) -> Applied:
    """Apply `operations` in order to a copy of `entries`, and check the limit on the result."""
    if not operations:
        return Applied(None, "There were no operations to apply.")
    single = len(operations) == 1
    tail = "" if single else " No operations were applied (the batch is all-or-nothing)."
    working = list(entries)
    replaced: dict[int, str] = {}
    removed: dict[int, str] = {}
    for number, operation in enumerate(operations, start=1):
        where = "" if single else f"Operation {number} ({operation.action}): "
        content, old_text = operation.content.strip(), operation.old_text.strip()
        if operation.action is Action.ADD:
            if not content:
                return Applied(None, f"{where}content is required to add an entry.{tail}")
            if content not in working:
                working.append(content)
            continue
        if not old_text:
            return Applied(
                None,
                f"{where}'{operation.action}' needs old_text: a short unique piece of the entry to "
                f"{operation.action}. Reissue it with old_text set to part of one of the current "
                f"entries.{tail}",
                show_entries=single,
            )
        if operation.action is Action.REPLACE and not content:
            return Applied(
                None,
                f"{where}content is required to replace (use remove to delete an entry).{tail}",
            )
        if operation.entry is not None:
            if operation.entry not in working:
                # Read by the user who approves, never by a model: no operation numbers.
                return Applied(None, _stale(operation.entry))
            index = working.index(operation.entry)
            if operation.action is Action.REPLACE:
                replaced[number] = working[index]
                working[index] = content
            else:
                removed[number] = working.pop(index)
            continue
        index, matched = find(working, old_text)
        if index is None and matched:
            return Applied(
                None,
                f"{where}'{old_text}' matched {len(matched)} different entries. Be more "
                f"specific.{tail}",
                matches=[_preview(working[i]) for i in matched],
            )
        if index is None:
            spans = (
                " old_text must come from ONE entry (§ separates entries)."
                if "§" in old_text
                else ""
            )
            near = closest(working, old_text)
            pointer = " (the closest entries are below)" if near else ""
            return Applied(
                None,
                f"{where}no entry matched '{old_text}'.{spans} Retry with old_text copied verbatim "
                f"from the intended entry{pointer}.{tail}",
                consolidation=True,
                show_entries=single,
                closest=near,
            )
        if operation.action is Action.REPLACE:
            replaced[number] = working[index]
            working[index] = content
        else:
            removed[number] = working.pop(index)
    if size(working) > limit:
        over = size(working) - limit
        if single:
            message = (
                f"Memory would be at {size(working):,}/{limit:,} chars, over the limit by "
                f"{over:,}. Retry as ONE operations batch that removes or shortens (replace) stale "
                f"entries from the current entries below to free at least {over:,} chars AND makes "
                "this change; the limit is checked only on the batch result."
            )
        else:
            message = (
                f"Applying all {len(operations)} operations would put memory at "
                f"{size(working):,}/{limit:,} chars: free at least {over:,} chars more. Add "
                "removes or shorter replacements of other stale entries to the same batch, then "
                f"retry.{tail}"
            )
        return Applied(None, message, consolidation=True, show_entries=single)
    return Applied(working, changed=working != entries, replaced=replaced, removed=removed)


def _stale(entry: str) -> str:
    """Why a proposal no longer applies: the entry it recorded changed."""
    return (
        f"An entry changed since this was proposed, so nothing was applied: '{entry}' is no "
        "longer there as it was."
    )


# -- memory proposals ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Proposal:
    """A background review's memory call that replaces or removes an entry, kept for the user to
    approve or discard."""

    id: str
    target: Target
    #: The call's operations in order, each replace and remove with the whole entry it selected.
    operations: tuple[Operation, ...]
    #: The conversation the review read; `None` when it read none.
    conversation_id: str | None
    #: When it was made, as UTC text.
    created_at: str
    #: The bot whose review made it, and whose memory a `memory` target is; `None` for the
    #: main agent's. The profile is the home's, whoever proposes.
    bot: str | None = None


def replaces_or_removes(operations: Sequence[Operation]) -> bool:
    """Whether a call would take an entry out: what an unattended review may only propose."""
    return any(operation.action is not Action.ADD for operation in operations)


def pinned(operations: Sequence[Operation], applied: Applied) -> tuple[Operation, ...]:
    """The operations with the whole entry each replace and remove selected in `applied`, the
    result of applying them: what a proposal keeps."""
    selected = {**applied.replaced, **applied.removed}
    return tuple(
        operation if operation.action is Action.ADD else replace(operation, entry=selected[number])
        for number, operation in enumerate(operations, start=1)
    )


def render_block(target: Target, entries: list[str], limit: int) -> str:
    """A store as the system prompt carries it, with how full it is; empty when it is."""
    if not entries:
        return ""
    line = "═" * 46
    header = f"{HEADERS[target]} [{usage(entries, limit)}]"
    return f"{line}\n{header}\n{line}\n{render_entries(entries)}"


def _folded(text: str) -> str:
    return " ".join(text.replace("\\n", " ").translate(_FOLD).split())


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _preview(entry: str) -> str:
    return entry[:80] + ("..." if len(entry) > 80 else "")


# -- what the model reads ------------------------------------------------------------------------

#: The memory guidance, its opening and its scope. It does not repeat what the memory tool's
#: description teaches.
_MEMORY_FRAME = (
    "Your memory persists from one conversation to the next: each new conversation starts with it "
    "in context, and what goes in it is set out in the memory tool's description. "
)
_MEMORY_SCOPE = (
    "Memory is kept narrow, for the facts true in EVERY conversation whatever the task (who the "
    "user is, facts of the environment, standing conventions no task owns). Its character budget "
    "is hard: once it is full, replace or merge stale entries instead of giving up on the save. "
    "An entry states a fact; it is not an order to yourself: 'User prefers concise responses' ✓ "
    "— 'Always respond concisely' ✗ (a later conversation rereads an imperative as a directive, "
    "and it can win over what the user is asking now). A fact that goes stale within a week is "
    "for the conversation history; procedures and workflows are for skills."
)
#: The memory guidance for a conversation with skill_manage.
MEMORY_GUIDANCE = (
    _MEMORY_FRAME + "Skills come first: a lesson learned while doing a task — a procedure, a "
    "pitfall, the user's preferences and corrections for that kind of work — goes into the skill "
    "you used or made for that task (skill_manage), which loads only when it is relevant. "
    + _MEMORY_SCOPE
)
#: The same, for a conversation that cannot write skills.
MEMORY_GUIDANCE_WITHOUT_SKILLS = (
    _MEMORY_FRAME + "What is specific to a task — its procedures and pitfalls, the user's "
    "preferences and corrections for that kind of work — is for skills and stays out of memory, "
    "even when you cannot write skills. " + _MEMORY_SCOPE
)

#: The guidance on recalling past conversations.
CONVERSATION_SEARCH_GUIDANCE = (
    "Before asking the user to say something again, look it up with conversation_search: when "
    "they mention an earlier conversation, or when you suspect another conversation holds "
    "context that matters here."
)

#: Which store a fact goes to, shared by the memory review and the combined one: without it the
#: reviewer writes profile data into MEMORY.md and the same lesson into both stores.
MEMORY_ROUTING = (
    "TWO separate stores, and each fact belongs in the right one:\n"
    "  • USER.md (memory tool, target='user'): the user as a person — who they are, their "
    "preferences, how they communicate and work, personal details they shared, and how they "
    "expect you to behave.\n"
    "  • MEMORY.md (memory tool, target='memory'): the ENVIRONMENT you work in — quirks of "
    "tools, a project's conventions, configuration traps, the paths and endpoints that "
    "matter.\n\n"
    "Each fact is written to ONE store, never both. Written twice, it swells both files toward "
    "their size limits and pushes out the facts that count; in the wrong store, the next "
    "conversation will not look for it there. When the tool's description offers a single "
    "target, only that store is on: write there and leave the other alone.\n\n"
)

#: The memory review's prompt, with its routing block.
MEMORY_REVIEW_PROMPT = (
    "Read back through the conversation above and decide whether anything should go to "
    "memory.\n\n"
    "Memory has " + MEMORY_ROUTING + "When something stands out, save it a single time, to the "
    "right store, with the memory tool and the target that matches. When nothing deserves a "
    "save, say 'Nothing to save.' and stop."
)

#: The cap on a context file such as `SOUL.md`, in characters, and the shares of it kept from the
#: head and the tail of a longer one.
SOUL_LIMIT = 20_000
_SOUL_HEAD = 0.7
_SOUL_TAIL = 0.2


def cut_soul(text: str, path: str, limit: int = SOUL_LIMIT) -> str:
    """A soul as the prompt carries it: whole within `limit`, else its head and its tail around a
    marker naming the file to read whole, not the headings the middle held."""
    if len(text) <= limit:
        return text
    head, tail = int(limit * _SOUL_HEAD), int(limit * _SOUL_TAIL)
    marker = (
        f"\n\n[...SOUL.md cut short: kept {head}+{tail} of {len(text)} chars, without the "
        "middle. For the instructions in full, open the whole file with the read_file "
        f"tool: {path}]\n\n"
    )
    return text[:head] + marker + text[-tail:]
