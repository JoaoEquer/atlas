"""The assistant: a conversation's turn, the review after it, the curator, MCP tools, and stopping.

The model is the fake model server through the real adapter; the memory, skills and database are
real, in a temporary home.
"""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import MODEL_SWITCH_MARK, TurnEnded
from atlas.assistant import (
    IDENTITY,
    SOUL_NOTE,
    Assistant,
    ConversationEvent,
    Reviewed,
    TurnStarted,
    identity,
    review_actions,
    stop,
)
from atlas.conversations import Kind
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate, utc_now
from atlas.memory import Action, MemoryFiles, Operation, Proposal, Target
from atlas.models import (
    TOOL_SEARCH,
    AssistantMessage,
    CallLedger,
    ModelChoice,
    ModelsError,
    UserMessage,
)
from atlas.secrets import Vault
from atlas.skills import CuratorClaim, Origin, SkillLibrary, SkillOperation, SkillUsage
from atlas.tools import Connection
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
MCP_SERVER = Path(__file__).parent.parent / "mcp_server.py"


class _Clock:
    def __init__(self) -> None:
        self.now = utc_now()

    def __call__(self) -> datetime:
        return self.now


type Make = Callable[..., tuple[Assistant, list[tuple[str, ConversationEvent]]]]


@pytest.fixture
def make(home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path) -> Make:
    migrate(Database(home.database_file), SCHEMAS)

    def make(**options: Any) -> tuple[Assistant, list[tuple[str, ConversationEvent]]]:
        chosen = ModelSettings(provider="openrouter")
        ledger = CallLedger(Database(home.database_file))
        models = connect(fake, "openrouter", settings=chosen, ledger=ledger)
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
        heard: list[tuple[str, ConversationEvent]] = []
        assistant = Assistant(
            home,
            settings,
            models,
            vault,
            listener=lambda conversation, event: heard.append((conversation, event)),
            **options,
        )
        return assistant, heard

    return make


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


# -- a turn and the review after it --------------------------------------------------------------


def test_a_turn_is_heard_stored_and_carries_who_the_agent_is(
    make: Make, fake: FakeModelServer, tmp_path: Path
) -> None:
    fake.reply(FakeReply(text="Hi!"))
    assistant, heard = make()

    async def scenario() -> None:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Hello")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    assert isinstance(heard[0][1], TurnStarted)
    assert isinstance(heard[-1][1], TurnEnded)
    system = fake.bodies(CHAT)[0]["messages"][0]["content"]
    assert system.startswith("You are Atlas, a personal agent.")
    assert f"The user's working folder is {tmp_path / 'work'}" in system
    tools = {tool["function"]["name"] for tool in fake.bodies(CHAT)[0]["tools"]}
    assert {"read_file", "run_command", "memory", "ask_user", "skill_manage"} <= tools
    search = next(
        t for t in fake.bodies(CHAT)[0]["tools"] if t["function"]["name"] == "tool_search"
    )
    assert "cronjob_manage" in search["function"]["description"]  # deferred
    stored = assistant.conversations.messages(heard[0][0])
    assert [m.text for m in stored] == ["Hello", "Hi!"]


def test_with_no_key_the_turn_says_so_keeps_the_message_and_the_next_key_answers(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    # An empty container: no model chosen, no key. The key comes while Atlas runs.
    migrate(Database(home.database_file), SCHEMAS)
    models = connect(fake, "openrouter", key=None)
    settings = Settings(home=home.root, workspace=WorkspaceSettings(tmp_path))
    heard: list[tuple[str, ConversationEvent]] = []
    assistant = Assistant(home, settings, models, vault, listener=lambda c, e: heard.append((c, e)))
    fake.reply(FakeReply(text="Here now."))

    async def scenario() -> str:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Are you there?")
        await conversation.wait()
        vault.set("OPENROUTER_API_KEY", "fake-key")
        conversation.send("And now?")
        await conversation.wait()
        await assistant.close()
        return stored.id

    conversation = _run(scenario)

    failed, answered = [event for _, event in heard if isinstance(event, TurnEnded)]
    assert "atlas secrets set OPENAI_API_KEY" in failed.text
    assert answered.text == "Here now."
    stored = assistant.conversations.messages(conversation)
    assert [m.text for m in stored] == ["Are you there?", "And now?", "Here now."]


def test_the_review_runs_after_the_turn_and_says_what_it_changed(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    remember = json.dumps({"action": "add", "target": "user", "content": "Name is Rui."})
    fake.reply(
        FakeReply(text="Noted."),  # the turn
        FakeReply(calls=[FakeCall("memory", remember, id="m1")]),  # the review
        FakeReply(text="Saved the name."),
    )
    assistant, heard = make()

    async def scenario() -> None:
        stored = assistant.conversations.create()
        for n in range(9):
            stored.history += [UserMessage(f"message {n}"), AssistantMessage("ok")]
        stored.save()
        conversation = assistant.conversation(stored.id)
        conversation.send("My name is Rui.")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    reviewed = [event for _, event in heard if isinstance(event, Reviewed)]
    assert [event.actions for event in reviewed] == [("User profile updated",)]
    assert reviewed[0].text == "User profile updated"
    assert MemoryFiles(home.memory_folder).entries(Target.USER) == ["Name is Rui."]
    assert fake.bodies(CHAT)[1]["messages"][-1]["role"] == "user"  # the review's prompt


def test_a_review_that_changes_nothing_says_nothing(make: Make, fake: FakeModelServer) -> None:
    fake.reply(FakeReply(text="Noted."), FakeReply(text="Nothing to keep."))
    assistant, heard = make()

    async def scenario() -> None:
        stored = assistant.conversations.create()
        for n in range(9):
            stored.history += [UserMessage(f"message {n}"), AssistantMessage("ok")]
        stored.save()
        conversation = assistant.conversation(stored.id)
        conversation.send("Last one.")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    assert len(fake.bodies(CHAT)) == 2
    assert not any(isinstance(event, Reviewed) for _, event in heard)


def test_the_review_starts_as_the_turn_did_so_the_cache_holds(
    make: Make, fake: FakeModelServer
) -> None:
    # 14/10/2026: a review given tools of its own (no delegate, no bot_create, no browser) began
    # its request differently from the turn's, and paid 138,000 tokens the turn had read from the
    # cache four seconds before. The review keeps the turn's tools.
    fake.reply(FakeReply(text="Noted."), FakeReply(text="Nothing to keep."))
    assistant, _ = make()

    async def scenario() -> None:
        stored = assistant.conversations.create()
        for n in range(9):
            stored.history += [UserMessage(f"message {n}"), AssistantMessage("ok")]
        stored.save()
        conversation = assistant.conversation(stored.id)
        conversation.send("Last one.")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    turn, review = fake.bodies(CHAT)
    assert review["tools"] == turn["tools"]
    assert review["messages"][: len(turn["messages"])] == turn["messages"]


def test_what_a_review_did_is_said_one_line_per_change() -> None:
    said = review_actions({"memory"}, {"user"}, ["new"], ["patched", "new"])

    assert said == (
        "Memory updated",
        "User profile change proposed",
        "Skill 'new' created",
        "Skill 'patched' updated",
    )
    assert review_actions(set(), set(), [], []) == ()


def test_a_review_that_would_take_an_entry_out_proposes_it(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    files = MemoryFiles(home.memory_folder)
    files.change(Target.USER, [Operation(Action.ADD, "Lives in Porto.")])
    moved = json.dumps(
        {"action": "replace", "target": "user", "old_text": "Porto", "content": "Lives in Lisbon."}
    )
    fake.reply(
        FakeReply(text="Noted."),  # the turn
        FakeReply(calls=[FakeCall("memory", moved, id="m1")]),  # the review
        FakeReply(text="Proposed the move."),
    )
    assistant, heard = make()

    async def scenario() -> str:
        stored = assistant.conversations.create()
        for n in range(9):
            stored.history += [UserMessage(f"message {n}"), AssistantMessage("ok")]
        stored.save()
        conversation = assistant.conversation(stored.id)
        conversation.send("I moved to Lisbon.")
        await conversation.wait()
        await assistant.close()
        return stored.id

    conversation_id = _run(scenario)

    [reviewed] = [event for _, event in heard if isinstance(event, Reviewed)]
    [proposal] = assistant.proposals.all()
    assert reviewed.actions == ("User profile change proposed",)
    assert reviewed.proposals == (proposal.id,)
    assert proposal.conversation_id == conversation_id
    assert proposal.operations[0].entry == "Lives in Porto."
    assert files.entries(Target.USER) == ["Lives in Porto."]


def test_a_review_says_only_what_it_did_itself(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    """What another writer changes while a review runs is not the review's. Step 9b's proof found
    two conversations' reviews overlapping: comparing the files before and after, one said it had
    created the skill the other created, and a profile the proof rewrote by hand."""
    files = MemoryFiles(home.memory_folder)
    files.change(Target.USER, [Operation(Action.ADD, "Lives in Porto.")])
    files.change(Target.MEMORY, [Operation(Action.ADD, "Uses tabs.")])
    moved = json.dumps(
        {"action": "replace", "target": "user", "old_text": "Porto", "content": "Lives in Lisbon."}
    )
    noted = json.dumps({"action": "add", "target": "memory", "content": "Uses metric units."})
    fake.reply(
        FakeReply(text="Noted."),  # the turn
        FakeReply(calls=[FakeCall("memory", noted, id="m1")]),  # the review saves
        FakeReply(calls=[FakeCall("memory", moved, id="m2")]),  # and proposes
        FakeReply(text="Done.", wait_before=1.0),  # time for the others to write
    )
    assistant, heard = make()
    library = SkillLibrary(home.skills_folder, SkillUsage(Database(home.database_file)))
    skill = "---\nname: notes\ndescription: Use when writing notes.\n---\nWrite in bullets.\n"

    async def scenario() -> str:
        stored = assistant.conversations.create()
        for n in range(9):
            stored.history += [UserMessage(f"message {n}"), AssistantMessage("ok")]
        stored.save()
        conversation = assistant.conversation(stored.id)
        conversation.send("I moved to Lisbon.")
        while not assistant.proposals.all():
            await asyncio.sleep(0.01)
        # Others write while the review waits for its last reply: another conversation's review
        # proposes, the user saves to the profile, and a skill is written by hand.
        other = assistant.proposals.propose(
            Target.MEMORY, [Operation(Action.REMOVE, old_text="tabs")], "another-conversation"
        )
        assert isinstance(other, Proposal)
        files.change(Target.USER, [Operation(Action.ADD, "Likes tea.")])
        library.apply([SkillOperation("create", "notes", content=skill)], Origin.USER)
        await conversation.wait()
        await assistant.close()
        return other.id

    other = _run(scenario)

    [reviewed] = [event for _, event in heard if isinstance(event, Reviewed)]
    assert reviewed.actions == ("Memory updated", "User profile change proposed")
    assert len(reviewed.proposals) == 1
    assert other not in reviewed.proposals


def test_the_identity_says_where_and_when() -> None:
    said = identity("/home/ana", "Linux", "Saturday, 10 October 2026")

    assert "working folder is /home/ana, on Linux" in said
    assert "started on Saturday, 10 October 2026" in said


def test_the_identity_is_the_soul_whole_and_says_where_it_is_kept() -> None:
    said = identity("/w", "Linux", "Saturday", soul="  You are dry.  ", soul_file="/h/SOUL.md")
    blank = identity("/w", "Linux", "Saturday", soul="\n", soul_file="/h/SOUL.md")
    child = identity("/w", "Linux", "Saturday")

    assert said.startswith("You are dry.\n\nThe user's working folder is /w")
    assert said.endswith(SOUL_NOTE.format(path="/h/SOUL.md"))
    assert "When the user asks you to change who you are" in said  # leave, only when asked
    assert blank.startswith(IDENTITY)
    assert child.startswith(IDENTITY)
    assert "SOUL.md" not in child  # a subagent's: the soul is its parent's


def _turn_systems(fake: FakeModelServer) -> list[str]:
    """The instructions of each turn's model call, the title model's left out."""
    systems = [str(body["messages"][0]["content"]) for body in fake.bodies(CHAT)]
    return [system for system in systems if "The user's working folder is" in system]


def test_the_soul_is_seeded_once_and_an_edit_reaches_the_next_conversation_only(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    fake.reply(FakeReply(text="One."), FakeReply(text="Two."), FakeReply(text="Three."))
    assistant, _ = make()
    assert home.soul_file.read_text(encoding="utf-8") == f"{IDENTITY}\n"

    async def scenario() -> None:
        first = assistant.conversation(assistant.conversations.create().id)
        first.send("Hello")
        await first.wait()
        home.soul_file.write_text("You are Kestrel, dry and terse.", encoding="utf-8")
        first.send("Still there?")
        await first.wait()
        second = assistant.conversation(assistant.conversations.create().id)
        second.send("Who are you?")
        await second.wait()
        await assistant.close()

    _run(scenario)

    before, still, after = _turn_systems(fake)
    assert before.startswith(f"{IDENTITY}\n\n")
    assert still == before  # the open conversation keeps its instructions, so the cache holds
    assert after.startswith("You are Kestrel, dry and terse.\n\nThe user's working folder is")
    assert IDENTITY not in after
    assert f"Your identity is kept in {home.soul_file}, read when a conversation starts." in after


def test_a_soul_there_is_stays_even_blank_and_atlass_identity_stands_in(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    home.soul_file.write_text("  \n", encoding="utf-8")
    fake.reply(FakeReply(text="Hi."))
    assistant, _ = make()

    async def scenario() -> None:
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Hello")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    [system] = _turn_systems(fake)
    assert system.startswith(f"{IDENTITY}\n\n")
    assert home.soul_file.read_text(encoding="utf-8") == "  \n"


# -- the curator ---------------------------------------------------------------------------------


def test_the_curator_waits_for_two_idle_hours(make: Make) -> None:
    clock = _Clock()
    assistant, _ = make(clock=clock)

    async def scenario() -> list[Any]:
        runs = [await assistant.curate()]
        clock.now += timedelta(hours=3)
        runs.append(await assistant.curate())
        await assistant.close()
        return runs

    soon, later = _run(scenario)

    assert soon is None
    assert later is not None
    assert later.claim is CuratorClaim.SEEDED


def test_the_curator_does_not_run_while_a_turn_runs(make: Make, fake: FakeModelServer) -> None:
    clock = _Clock()
    fake.reply(FakeReply(text="Slow.", wait_before=1))
    assistant, _ = make(clock=clock)

    async def scenario() -> Any:
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Hi")
        clock.now += timedelta(hours=3)
        found = await assistant.curate()
        await assistant.close()
        return found

    assert _run(scenario) is None


# -- MCP -----------------------------------------------------------------------------------------


def test_the_mcp_servers_tools_join_the_conversation(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    servers = {"notes": {"command": sys.executable, "args": [str(MCP_SERVER)]}}
    home.mcp_file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    fake.reply(FakeReply(text="Hi."))
    assistant, _ = make()

    async def scenario() -> None:
        assistant.start()
        for _ in range(200):
            if assistant._shared.mcp_tools.deferred:  # pyright: ignore[reportPrivateUsage]
                break
            await asyncio.sleep(0.05)
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Hello")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    tools = {tool["function"]["name"]: tool for tool in fake.bodies(CHAT)[0]["tools"]}
    assert "mcp__notes__today" in tools  # always loaded, as its server asks
    assert "mcp__notes__add_note" in tools[TOOL_SEARCH]["function"]["description"]


# -- stopping ------------------------------------------------------------------------------------


def test_a_stop_is_sent_again_until_the_task_ends() -> None:
    caught: list[int] = []

    async def stubborn() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            caught.append(1)  # lost, as the HTTP client loses one at times
        await asyncio.sleep(10)

    async def scenario() -> bool:
        task = asyncio.create_task(stubborn())
        await asyncio.sleep(0)
        await stop(task, again=0.01)
        return task.cancelled()

    assert _run(scenario) is True
    assert caught == [1]


def test_stopping_a_conversation_with_no_turn_says_so(make: Make) -> None:
    assistant, _ = make()

    async def scenario() -> bool:
        conversation = assistant.conversation(assistant.conversations.create().id)
        stopped = await conversation.interrupt()
        await assistant.close()
        return stopped

    assert _run(scenario) is False


# -- a conversation's model ----------------------------------------------------------------------


def test_a_conversation_switches_models_between_turns_and_keeps_its_pick(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    """A switch: the same conversation and tools go on with another model from the next
    turn, with a note naming it, the newest only; the pick survives a restart."""
    migrate(Database(home.database_file), SCHEMAS)
    models = connect(fake, "openrouter", "gemini")
    settings = Settings(home=home.root, workspace=WorkspaceSettings(tmp_path))

    def assistant() -> Assistant:
        return Assistant(home, settings, models, vault, listener=lambda c, e: None)

    fake.reply(
        FakeReply(text="On the default."),
        FakeReply(text="On Gemini."),
        FakeReply(text="On another Gemini."),
        FakeReply(text="Thinking harder."),
    )
    first = assistant()

    async def turn(on: Assistant, conversation_id: str, text: str) -> None:
        conversation = on.conversation(conversation_id)
        conversation.send(text)
        await conversation.wait()

    async def scenario() -> str:
        stored = first.conversations.create()
        assert first.conversation_model(stored.id) == (
            ModelChoice("openrouter", "openai/gpt-6-luna", "medium"),
            False,
        )
        await turn(first, stored.id, "Hello")
        first.set_conversation_model(stored.id, ModelChoice("gemini", "gemini-3.8-flash", "low"))
        await turn(first, stored.id, "Who are you now?")
        first.set_conversation_model(stored.id, ModelChoice("gemini", "gemini-3.7-flash"))
        await turn(first, stored.id, "And now?")
        await first.close()
        again = assistant()
        # Picked with no effort, it runs on Atlas's default, and says so.
        assert again.conversation_model(stored.id) == (
            ModelChoice("gemini", "gemini-3.7-flash", "medium"),
            True,
        )
        again.set_conversation_model(stored.id, ModelChoice("gemini", "gemini-3.7-flash", "high"))
        await turn(again, stored.id, "Think harder.")
        await again.close()
        return stored.id

    conversation_id = _run(scenario)

    bodies = fake.bodies(CHAT)
    assert [body["model"] for body in bodies] == [
        "openai/gpt-6-luna",
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.7-flash",
    ]
    thinking = bodies[1]["extra_body"]["google"]["thinking_config"]
    assert thinking["thinking_level"] == "low"
    assert bodies[2]["extra_body"]["google"]["thinking_config"]["thinking_level"] == "medium"
    said = [m["content"] for m in bodies[2]["messages"] if m["role"] == "user"]
    notes = [text for text in said if text.startswith(MODEL_SWITCH_MARK)]
    assert notes == [
        f"{MODEL_SWITCH_MARK}gemini-3.7-flash via provider Google Gemini. From now on, whenever "
        "you are asked which model or provider is answering, go by this note.]"
    ]
    assert said[-2:] == [notes[0], "And now?"]
    # A new effort alone leaves no note.
    last = [m["content"] for m in bodies[3]["messages"] if m["role"] == "user"]
    assert sum(text.startswith(MODEL_SWITCH_MARK) for text in last) == 1
    assert bodies[3]["extra_body"]["google"]["thinking_config"]["thinking_level"] == "high"
    shown = assistant().conversations.messages(conversation_id)
    switches = [m for m in shown if m.kind is Kind.MODEL_SWITCH]
    assert [m.active for m in switches] == [False, True]
    assert assistant().conversations.info(conversation_id).title == "Hello"


def test_a_pick_that_cannot_run_is_refused_and_nothing_is_kept(
    home: Home, vault: Vault, connect: Connect, tmp_path: Path
) -> None:
    migrate(Database(home.database_file), SCHEMAS)
    models = connect(None, "gemini")
    settings = Settings(home=home.root, workspace=WorkspaceSettings(tmp_path))
    assistant = Assistant(home, settings, models, vault, listener=lambda c, e: None)
    stored = assistant.conversations.create()

    with pytest.raises(ModelsError, match="does not accept the effort"):
        assistant.set_conversation_model(
            stored.id, ModelChoice("gemini", "gemini-3.8-flash", "max")
        )
    with pytest.raises(ModelsError, match="no key for OpenAI"):
        assistant.set_conversation_model(stored.id, ModelChoice("openai", "gpt-6-luna"))

    assert assistant.conversation_model(stored.id) == (
        ModelChoice("gemini", "gemini-3.8-flash", "medium"),
        False,
    )


# -- a background command's notice -----------------------------------------------------------------


def test_a_background_command_that_ends_starts_a_turn_with_its_notice(
    make: Make, fake: FakeModelServer
) -> None:
    """The completion notice, dispatched as a turn after the one that started the command."""
    start = json.dumps({"command": "echo finished", "background": True, "notify_on_complete": True})
    fake.reply(
        FakeReply(calls=[FakeCall("run_command", start, id="r1")]),
        FakeReply(text="Started it; I will look when it ends."),
        FakeReply(text="It finished."),
    )
    assistant, heard = make()

    async def scenario() -> str:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Run it in the background")
        for _ in range(400):
            await conversation.wait()
            if sum(isinstance(e, TurnEnded) for _, e in heard) >= 2:
                break
            await asyncio.sleep(0.05)
        await assistant.close()
        return stored.id

    conversation_id = _run(scenario)

    started = [e.text for _, e in heard if isinstance(e, TurnStarted)]
    assert started[0] == "Run it in the background"
    assert started[1].startswith(
        "[IMPORTANT: Background command cmd-1 completed normally (exit code 0)."
    )
    assert "finished" in started[1]
    last = fake.bodies(CHAT)[2]["messages"][-1]
    assert last["role"] == "user"
    assert last["content"].startswith("[IMPORTANT: Background command cmd-1")
    stored = assistant.conversations.messages(conversation_id)
    assert [m.kind for m in stored if m.role.value == "user"] == [Kind.MESSAGE, Kind.NOTICE]
    assert assistant.conversations.info(conversation_id).title == "Run it in the background"


# -- a connection offered in the conversation (05/11/2026) -----------------------------------------


def test_a_connection_not_ready_is_offered_in_the_users_conversation_and_drawn_as_a_card(
    make: Make, fake: FakeModelServer
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall("offer_connection", '{"connection": "google"}')]),
        FakeReply(text="Connect it on the card, and I will read your inbox."),
    )
    assistant, _ = make()
    ready = {"google": False, "github": True}
    assistant.use_connections(
        lambda: [
            Connection("google", "Gmail and Calendar", "their email", ready["google"]),
            Connection("github", "GitHub", "their repositories", ready["github"]),
        ]
    )

    async def scenario() -> None:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("What came in my email today?")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    first = fake.bodies(CHAT)[0]
    [offer] = [t for t in first["tools"] if t["function"]["name"] == "offer_connection"]
    assert offer["function"]["parameters"]["properties"]["connection"]["enum"] == ["google"]
    assert "just starting" in offer["function"]["description"]
    said = fake.bodies(CHAT)[1]["messages"][-1]["content"]
    assert said.startswith("[Atlas: The user sees a card to connect Gmail and Calendar here")


def test_no_connection_is_offered_when_every_one_is_ready(
    make: Make, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="Hi!"))
    assistant, _ = make()
    assistant.use_connections(lambda: [Connection("github", "GitHub", "repositories", True)])

    async def scenario() -> None:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Hello")
        await conversation.wait()
        await assistant.close()

    _run(scenario)

    tools = {tool["function"]["name"] for tool in fake.bodies(CHAT)[0]["tools"]}
    assert "offer_connection" not in tools
