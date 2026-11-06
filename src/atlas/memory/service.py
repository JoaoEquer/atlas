"""The instructions that carry the memory, and the background review.

**The instructions** are the conversation's system prompt: who the agent is (the caller says),
the guidance on what to keep, on skills and on recalling past conversations, and the profile and
the memory as they are on disk now. The agent loop asks for them when a conversation starts and
after each compaction (`AgentLoop(instructions=...)`).

**The background review**: after a turn, a copy of the agent rereads the conversation and keeps
what it taught.

- **When:** the memory half once 10 user messages went by without a memory write; the skill half
  once 10 model calls went by without a skill change. When both are due, one review runs the
  combined prompt.
- **How:** it gets the conversation as it stands plus the prompt. It runs on the same model with
  the turn's own tools, so the provider's cache holds (a review with tools of its own pays again
  for what the turn before had read from the cache), and the gate refuses at run time every tool
  but the memory and skill ones: memory (only when the memory half is due), skill_manage, and
  use_skill, read_file and find_files to read (denying the read tools starves the review).
- **It never replaces or removes an entry on its own.** An unattended review may not: one could
  delete standing rules after the "consolidate now" hint. It runs inside `proposing`, so its
  memory call that would take an entry out becomes a memory proposal the user approves or
  discards (`Proposals`).
- **Skills:** it runs inside `atlas.skills.reviewing`, so a skill it creates is the review's, it
  changes a file only after loading it, and it deletes nothing.
- **Bounded:** at most 16 calls, and input tokens up to three quarters of the model's window,
  never past 600,000 (one review can otherwise replay well over a million) and 120,000
  when the window is unknown (`TurnLimit`). Its calls are `background_review` in the call ledger.
  Nothing it says reaches the conversation.

**The proposals** are pending memory writes, decided on 11/10/2026 (docs/ARCHITECTURE.md, Memory
proposals):

- **Made only from a call that would apply now**, checked as the write itself would be, each
  replace and remove recording the whole entry it selects.
- **Approved, it applies to exactly those entries, or nothing changes and it stays**; what it
  overwrote or removed is said back in full.
- **Listed, each one is tried against the files as they are now**, and one that can no longer
  apply says why, so the screen offers only to discard it, rather than the user finding out only
  on approving (decided on 11/10/2026).
- **They do not expire**.
"""

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from atlas.agent import (
    AgentLoop,
    SpillFolder,
    Tool,
    ToolGate,
    Toolset,
    TurnEnded,
    TurnLimit,
    from_atlas,
    input_window,
)
from atlas.home import MemorySettings, format_utc, utc_now
from atlas.memory.model import (
    CONVERSATION_SEARCH_GUIDANCE,
    MEMORY_GUIDANCE,
    MEMORY_GUIDANCE_WITHOUT_SKILLS,
    MEMORY_REVIEW_PROMPT,
    MEMORY_ROUTING,
    Applied,
    Operation,
    Proposal,
    Target,
    pinned,
)
from atlas.memory.store import MemoryFiles, ProposalStore
from atlas.models import AssistantMessage, Message, ModelClient, UserMessage
from atlas.skills import (
    COMBINED_SKILLS_PART,
    SKILL_MANAGE,
    SKILL_REVIEW_EVERY,
    SKILL_REVIEW_PROMPT,
    SKILLS_GUIDANCE,
    USE_SKILL,
    reviewing,
)

#: The memory tool's name, which the review is allowed and whose calls reset the count.
MEMORY_TOOL = "memory"
#: User messages without a memory write before a memory review.
REVIEW_EVERY = 10
#: The review's budget: its calls, and its input tokens as a share of the model's window,
#: capped, with a fallback for a window nobody knows.
REVIEW_CALLS = 16
REVIEW_INPUT_SHARE = 0.75
REVIEW_INPUT_CAP = 600_000
REVIEW_INPUT_UNKNOWN = 120_000
#: How the review's calls are named in the call ledger.
REVIEW_PURPOSE = "background_review"
#: The tools a review may use to read, besides use_skill.
_READERS = frozenset({USE_SKILL, "read_file", "find_files"})

#: The combined review's prompt, its two halves from the two subjects.
COMBINED_REVIEW_PROMPT = (
    "Review the conversation above and update two things:\n\n**Memory**: "
    + MEMORY_ROUTING
    + "**Skills**: "
    + COMBINED_SKILLS_PART
)


def limits_of(settings: MemorySettings) -> dict[Target, int]:
    """The stores' limits a home's `[memory]` sets, in characters."""
    return {Target.USER: settings.user_limit, Target.MEMORY: settings.memory_limit}


class Instructions:
    """A conversation's system prompt, built from what is on disk each time it is asked for."""

    def __init__(
        self,
        memory: MemoryFiles,
        identity: str,
        *,
        conversation_search: bool = True,
        skills: bool = True,
    ) -> None:
        self._memory = memory
        self._identity = identity
        self._conversation_search = conversation_search
        self._skills = skills

    def __call__(self) -> str:
        guidance = MEMORY_GUIDANCE if self._skills else MEMORY_GUIDANCE_WITHOUT_SKILLS
        parts = [self._identity.strip(), guidance]
        if self._skills:
            parts.append(SKILLS_GUIDANCE)
        if self._conversation_search:
            parts.append(CONVERSATION_SEARCH_GUIDANCE)
        snapshot = self._memory.snapshot()
        if snapshot:
            parts.append(snapshot)
        return "\n\n".join(part for part in parts if part)


def review_due(history: Sequence[Message], every: int = REVIEW_EVERY) -> bool:
    """Whether the memory half is due: `every` user messages (or a multiple of it) since the agent
    last saved memory."""
    count = 0
    for message in reversed(history):
        if isinstance(message, AssistantMessage) and any(
            call.name == MEMORY_TOOL for call in message.tool_calls
        ):
            break
        if isinstance(message, UserMessage) and not from_atlas(message):
            count += 1
    return count > 0 and count % every == 0


def skill_review_due(history: Sequence[Message], every: int = SKILL_REVIEW_EVERY) -> bool:
    """Whether the skill half is due after the turn that ends `history`: the model calls since the
    agent last changed a skill crossed a multiple of `every` during that turn.

    The calls are counted in the history, as `review_due` does, so a restart forgets nothing; a
    review is due when the last turn carried the count past 10, 20, 30. The calls left over after a
    review are kept toward the next one."""
    since = 0
    before_turn = None
    for message in reversed(history):
        if isinstance(message, AssistantMessage):
            if any(call.name == SKILL_MANAGE for call in message.tool_calls):
                break
            since += 1
        elif isinstance(message, UserMessage) and before_turn is None:
            before_turn = since
    earlier = since - (before_turn if before_turn is not None else since)
    return since // every > earlier // every


class BackgroundReview:
    """Runs reviews on one model with the conversation's tools."""

    def __init__(
        self, client: ModelClient, tools: Toolset, *, spill: SpillFolder | None = None
    ) -> None:
        self._client = client
        self._tools = tools
        self._spill = spill

    async def run(
        self,
        history: Sequence[Message],
        *,
        memory: bool = True,
        skills: bool = False,
        scope: str | None = None,
    ) -> "ReviewEnded":
        """Review a conversation for its memory, its skills, or both. Its history is copied and
        stays as it was."""
        if memory and skills:
            prompt = COMBINED_REVIEW_PROMPT
        elif memory:
            prompt = MEMORY_REVIEW_PROMPT
        else:
            prompt = SKILL_REVIEW_PROMPT
        allowed = "memory and skill" if memory else "skill"
        prompt += (
            f"\n\nYou can only call {allowed} management tools. Other tools will be denied at "
            "runtime — do not attempt them."
        )
        loop = AgentLoop(
            self._client,
            self._tools,
            spill=self._spill,
            gate=_gate(memory=memory),
            purpose=REVIEW_PURPOSE,
            limit=review_limit(self._client),
        )
        with reviewing() as review, proposing(scope) as unattended:
            ended = await loop.reply(list(history), prompt, scope=scope)
        return ReviewEnded(
            ended,
            frozenset(unattended.saved),
            tuple(unattended.made),
            tuple(review.created),
            tuple(review.updated),
            review.diffs(),
        )


@dataclass(frozen=True, slots=True)
class ReviewEnded:
    """How a review ended, and what it did itself, as its own tools noted while it ran. Its
    summary says only this, read from the review's own results: comparing the files before
    and after counted what another conversation or the user changed meanwhile (step 9b's proof,
    11/10/2026)."""

    turn: TurnEnded
    #: The stores it changed, adding.
    saved: frozenset[Target]
    #: The memory proposals it made, in order.
    proposals: tuple[Proposal, ...]
    #: The skills it created, and the others it changed, by name, in order.
    created: tuple[str, ...]
    updated: tuple[str, ...]
    #: The skill diff of each skill it changed, by name.
    diffs: Mapping[str, str]


def review_limit(client: ModelClient) -> TurnLimit:
    """The review's budget on `client`'s model."""
    window = input_window(client.info)
    if window is None:
        tokens = REVIEW_INPUT_UNKNOWN
    else:
        tokens = min(REVIEW_INPUT_CAP, max(1, int(window * REVIEW_INPUT_SHARE)))
    return TurnLimit(model_calls=REVIEW_CALLS, input_tokens=tokens)


def _gate(*, memory: bool) -> ToolGate:
    """The tools a review may use. The memory tool itself turns a replace or a remove into a
    proposal while the review runs (`proposing`), so the gate lets it through."""

    async def gate(tool: Tool, arguments: dict[str, Any]) -> str | None:
        del arguments
        if tool.name in _READERS or tool.name == SKILL_MANAGE:
            return None
        if tool.name == MEMORY_TOOL and memory:
            return None
        allowed = "use_skill/read_file/find_files to read, skill_manage to change skills"
        if memory:
            allowed += " and memory for notes (add only)"
        return (
            f"[Atlas: background review denied non-whitelisted tool: {tool.name}. Allowed here: "
            f"{allowed}. Do not retry {tool.name}.]"
        )

    return gate


# -- memory proposals ----------------------------------------------------------------------------


@dataclass(slots=True)
class Proposing:
    """A review nobody watches is running: what it would take out of the memory, it proposes.
    What it saved and proposed is noted here for its summary."""

    #: The conversation the review reads.
    conversation_id: str | None
    #: The stores it changed, adding.
    saved: set[Target] = field(default_factory=set[Target])
    #: The proposals it made, in order.
    made: list[Proposal] = field(default_factory=list[Proposal])


_proposing: ContextVar[Proposing | None] = ContextVar("atlas_memory_proposing", default=None)


@contextmanager
def proposing(conversation_id: str | None) -> Generator[Proposing]:
    """Run the enclosed code as an unattended review of a conversation."""
    running = Proposing(conversation_id)
    token = _proposing.set(running)
    try:
        yield running
    finally:
        _proposing.reset(token)


def current_proposing() -> Proposing | None:
    return _proposing.get()


@dataclass(frozen=True, slots=True)
class OpenProposal:
    proposal: Proposal
    #: Why it cannot apply now (an entry changed, the result no longer fits); `None` when it can.
    problem: str | None


@dataclass(frozen=True, slots=True)
class Approval:
    """What approving a proposal did."""

    applied: bool
    #: Why nothing was applied; the proposal stays, unless there was none.
    error: str | None = None
    #: The whole text of each entry the change overwrote, and of each it removed, in order.
    replaced: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


class Proposals:
    """The background review's memory proposals: made, listed, approved and discarded."""

    def __init__(
        self,
        files: MemoryFiles,
        store: ProposalStore,
        clock: Callable[[], datetime] = utc_now,
        *,
        bot: str | None = None,
        bot_files: Callable[[str], MemoryFiles | None] | None = None,
    ) -> None:
        """`files` are the stores of whoever proposes here: the main agent, or `bot`.
        `bot_files` gives a bot's stores by its name, `None` once the bot is gone, so a
        proposal is tried and applied on the memory it was made for."""
        self._files = files
        self._store = store
        self._clock = clock
        self._bot = bot
        self._bot_files = bot_files

    def for_bot(self, bot: str, files: MemoryFiles) -> "Proposals":
        """The same proposals, as a bot makes them: over its own memory."""
        return Proposals(files, self._store, self._clock, bot=bot, bot_files=self._bot_files)

    def propose(
        self, target: Target, operations: Sequence[Operation], conversation_id: str | None
    ) -> Proposal | Applied:
        """Keep `operations` for the user, each replace and remove with the whole entry it selects
        now; or, when they would not apply now, the refusal the write itself gives."""
        checked = self._files.check(target, operations)
        if checked.entries is None:
            return checked
        return self._store.add(
            target,
            pinned(operations, checked),
            conversation_id,
            format_utc(self._clock()),
            self._bot,
        )

    def all(self) -> list[Proposal]:
        """Every open proposal, oldest first."""
        return self._store.all()

    def checked(self) -> list[OpenProposal]:
        """Every open proposal, oldest first, each tried against the files as they are now."""
        return [OpenProposal(proposal, self._problem(proposal)) for proposal in self._store.all()]

    def get(self, proposal_id: str) -> Proposal | None:
        return self._store.get(proposal_id)

    def approve(self, proposal_id: str) -> Approval:
        proposal = self._store.get(proposal_id)
        if proposal is None:
            return Approval(False, _gone(proposal_id))
        files = self._files_of(proposal)
        if files is None:
            return Approval(False, _bot_gone(proposal))
        applied = files.change(proposal.target, proposal.operations)
        if applied.entries is None:
            return Approval(False, self._why(proposal, applied))
        self._store.delete(proposal_id)
        return Approval(
            True,
            replaced=tuple(applied.replaced.values()),
            removed=tuple(applied.removed.values()),
        )

    def discard(self, proposal_id: str) -> bool:
        """Drop a proposal unapplied; `False` when there was none with this id."""
        return self._store.delete(proposal_id)

    def _problem(self, proposal: Proposal) -> str | None:
        files = self._files_of(proposal)
        if files is None:
            return _bot_gone(proposal)
        checked = files.check(proposal.target, proposal.operations)
        return None if checked.entries is not None else self._why(proposal, checked)

    def _files_of(self, proposal: Proposal) -> MemoryFiles | None:
        """The stores a proposal changes; `None` when it is for the memory of a bot that is
        gone. The profile is the home's, whoever proposed."""
        if proposal.target is Target.USER or proposal.bot == self._bot:
            return self._files
        if proposal.bot is None or self._bot_files is None:
            return None
        return self._bot_files(proposal.bot)

    def _why(self, proposal: Proposal, applied: Applied) -> str:
        """Why a proposal does not apply, in words for the user, never the model's retry hints."""
        if applied.consolidation:
            where = "your profile" if proposal.target is Target.USER else "the memory"
            return (
                f"It no longer fits: {where} has changed since this was proposed, and the change "
                "would put it over its limit."
            )
        return applied.error or "It cannot apply now."


def _bot_gone(proposal: Proposal) -> str:
    whose = f"the bot {proposal.bot}" if proposal.bot else "the main agent"
    return f"It was made for the memory of {whose}, which cannot be reached now. Discard it."


def _gone(proposal_id: str) -> str:
    return f"There is no open proposal {proposal_id}: it was approved or discarded already."
