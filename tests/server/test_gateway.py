"""The server end to end: a real uvicorn, a real WebSocket, the real assistant, and the fake model.

Every test talks to Atlas as the desktop will: JSON-RPC frames over the socket.
"""

import base64
import dataclasses
import json
import os
import sqlite3
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from websockets.exceptions import InvalidStatus

from atlas.bots import Bots
from atlas.conversations import ConversationStore
from atlas.home import Database, Home, utc_now
from atlas.memory import Action, MemoryFiles, Operation, Proposal, Target
from atlas.models import AssistantMessage, UserMessage
from atlas.plugins import Capabilities, Plugins
from atlas.secrets import Vault
from atlas.server import EVENTS, METHODS, NOT_FOUND
from atlas.tools import system_shell
from claude_code_support import FAKE, claude_code
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect
from server_support import Client, Serve

CHAT = "/v1/chat/completions"


def _ask(arguments: dict[str, Any], call_id: str = "a1") -> FakeReply:
    return FakeReply(calls=[FakeCall("ask_user", json.dumps(arguments), id=call_id)])


def _tool_results(fake: FakeModelServer, call: int) -> list[str]:
    """The tool results the model got on its `call`-th call."""
    messages = fake.bodies(CHAT)[call]["messages"]
    return [m["content"] for m in messages if m["role"] == "tool"]


def _conversation(client: Client, *, auto: bool = True) -> str:
    """A new conversation, in "Auto" as it starts unless `auto` is off."""
    created = client.result("conversation.create")["conversation_id"]
    if not auto:
        client.result("conversation.auto_mode", {"conversation_id": created, "enabled": False})
    return created


# -- a turn --------------------------------------------------------------------------------------


def test_a_turn_streams_its_events_in_order_and_is_stored(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="Hello there, Ana."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)

        assert client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Hi"}
        ) == {"status": "started"}
        events = client.events_until("message.complete")

        kinds = [event["type"] for event in events]
        assert (kinds[0], kinds[-1]) == ("message.start", "message.complete")
        assert "".join(e["payload"]["text"] for e in events if e["type"] == "message.delta") == (
            "Hello there, Ana."
        )
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        assert all(event["conversation_id"] == conversation for event in events)
        assert events[-1]["payload"]["status"] == "answered"
        assert events[-1]["payload"]["model_calls"] == 1

        opened = client.result("conversation.open", {"conversation_id": conversation})
        assert [(m["role"], m["text"]) for m in opened["messages"]] == [
            ("user", "Hi"),
            ("assistant", "Hello there, Ana."),
        ]
        assert opened["running"] is False
        assert opened["seq"] == len(events)
        assert opened["epoch"] == client.ready["params"]["payload"]["epoch"]
        listed = client.result("conversation.list")["conversations"]
        assert [row["id"] for row in listed] == [conversation]
        assert listed[0]["title"] == "Hi"


def test_a_terminal_row_enters_the_conversation_and_the_agent_answers_it(
    serve: Serve, fake: FakeModelServer, vault: Vault
) -> None:
    """Step 16f: a command the user ran (the ▶, or `!`) and its output enter as a terminal row in
    Claude Code's bash-mode tags, cleaned as a tool's result is, and the agent answers it, in a
    turn that ends: the conversation is not left running."""
    vault.set("DEPLOY_TOKEN", "tok-very-secret-123")
    fake.reply(FakeReply(text="The test fails: 0.1 + 0.2 is not 0.3."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        output = "FAIL total.test.js\nexpected 0.3 got 0.30000000000000004\ntok-very-secret-123"
        sent = client.result(
            "conversation.terminal",
            {"conversation_id": conversation, "command": "npm test", "output": output,
             "exit_code": 1},
        )  # fmt: skip
        events = client.events_until("message.complete")
        long = client.result(
            "conversation.terminal",
            {"conversation_id": conversation, "command": "npm run dev", "output": "x" * 60_000,
             "status": "running"},
        )  # fmt: skip
        client.events_until("message.complete")
        opened = client.result("conversation.open", {"conversation_id": conversation})
        empty = client.call(
            "conversation.terminal", {"conversation_id": conversation, "command": " ", "output": ""}
        )

    assert sent == {"status": "started"}
    assert long == {"status": "started"}
    start = events[0]
    assert (start["type"], start["payload"]["kind"]) == ("message.start", "terminal")
    assert events[-1]["payload"]["status"] == "answered"
    asked = json.dumps(fake.bodies("/v1/chat/completions")[0])
    assert "<bash-input>npm test</bash-input>\\n<bash-stdout>FAIL total.test.js" in asked
    assert "<bash-exit-code>1</bash-exit-code>" in asked
    assert "tok-very-secret-123" not in asked, "no secret the vault knows"
    terminal = [m for m in opened["messages"] if m["kind"] == "terminal"]
    assert len(terminal) == 2
    assert "It is still running after 30 seconds" in terminal[1]["text"]
    assert "The whole result is in" in terminal[1]["text"], "a long output is spilled"
    assert opened["running"] is False
    assert opened["folder"]
    assert opened["conversation"]["title"] == "npm test", "called by its command, not its tags"
    assert "command is empty" in empty["error"]["message"]


def test_the_trust_gate_says_which_command_asks_before_a_play(serve: Serve) -> None:
    """Step 16f: the ▶ asks the server first; the trust gate's own detection decides."""
    with serve().client() as client:
        safe = client.result("trust.check", {"command": "npm test"})
        dangerous = client.result("trust.check", {"command": "rm -rf ~/projects"})
        hardline = client.result("trust.check", {"command": "rm -rf /"})

    assert safe == {"dangerous": False, "reason": ""}
    assert dangerous["dangerous"] is True
    assert dangerous["reason"]
    assert hardline["dangerous"] is True


def test_a_models_title_reaches_the_client_and_the_list(
    serve: Serve, fake: FakeModelServer, connect: Connect
) -> None:
    connect(fake, "anthropic")  # its key in the vault: Haiku titles the conversation
    fake.reply(
        FakeReply(
            text='{"title": "Plan a week in Lisbon"}', when="Your job is naming chat sessions."
        ),
        FakeReply(text="Gladly.", when="You are Atlas"),
    )
    with serve().client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "lisbon?"})
        titled = client.event("title.changed")
        client.events_until("message.complete")
        listed = client.result("conversation.list")["conversations"]

    assert titled["conversation_id"] == conversation
    assert titled["payload"] == {"title": "Plan a week in Lisbon"}
    assert listed[0]["title"] == "Plan a week in Lisbon"


def test_a_model_that_cannot_be_used_ends_the_turn_with_why(
    serve: Serve, fake: FakeModelServer, vault: Any
) -> None:
    server = serve()
    vault.delete("OPENROUTER_API_KEY")
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Hi"})
        done = client.event("message.complete")["payload"]

    assert done["status"] == "failed"
    assert done["error"] == "ModelKeyError"
    assert "atlas secrets set OPENROUTER_API_KEY" in done["text"]


# -- ask_user ------------------------------------------------------------------------------------


def test_ask_user_reaches_the_client_and_its_answer_reaches_the_model(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(
        _ask({"questions": [{"question": "Which city?", "choices": ["Lisbon", "Porto"]}]}),
        FakeReply(text="Lisbon it is."),
    )
    server = serve()
    with server.client() as client, server.client() as other:
        conversation = _conversation(client)
        other.result("conversation.open", {"conversation_id": conversation})
        client.result("conversation.send", {"conversation_id": conversation, "text": "Plan a trip"})

        asked = client.request("ask_user")
        seen_by_other = other.request("ask_user")
        assert seen_by_other["id"] == asked["id"]
        assert asked["params"]["questions"] == [
            {
                "id": "q0",
                "text": "Which city?",
                "choices": ["Lisbon (Recommended)", "Porto"],
                "multi_select": False,
            }
        ]
        client.answer(asked["id"], {"answers": {"q0": "Lisbon (Recommended)"}})

        cancel = other.event("request.cancel")["payload"]
        assert cancel == {"id": asked["id"], "method": "ask_user", "reason": "resolved"}
        assert client.event("message.complete")["payload"]["text"] == "Lisbon it is."

    result = json.loads(_tool_results(fake, 1)[0])
    assert result["outcome"] == "submitted"
    assert result["responses"][0]["user_response"] == "Lisbon"


def test_a_question_with_no_client_attached_is_undelivered_at_once(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(
        FakeReply(
            text="",
            calls=[FakeCall("ask_user", '{"questions": [{"question": "Ok?"}]}')],
            wait_before=0.5,
        ),
        FakeReply(text="Nobody answered, so I went on."),
    )
    server = serve(ask_seconds=60)
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Go"})
    # The client left before the model asked; nobody is attached when the question comes.
    deadline = time.monotonic() + 20
    while len(fake.bodies(CHAT)) < 2 or server.gateway.assistant.running(conversation):
        assert time.monotonic() < deadline, "the turn waited for an answer nobody could give"
        time.sleep(0.05)
    with server.client() as later:
        replay = later.result("conversation.events", {"conversation_id": conversation, "after": 0})

    assert replay["events"][-1]["payload"]["text"] == "Nobody answered, so I went on."
    assert json.loads(_tool_results(fake, 1)[0])["outcome"] == "undelivered"


def test_a_client_that_comes_back_gets_the_question_still_open(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(
        _ask({"questions": [{"question": "Name?"}]}),
        FakeReply(text="Thanks, Bia."),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Hello"})
        asked = client.request("ask_user")
        seen = client.events_until("tool.start")[-1]["seq"]
    with server.client() as back:
        replay = back.result(
            "conversation.events", {"conversation_id": conversation, "after": seen}
        )
        assert replay["truncated"] is False
        assert [r["id"] for r in replay["open_requests"]] == [asked["id"]]
        assert replay["open_requests"][0]["params"]["questions"][0]["text"] == "Name?"
        back.answer(asked["id"], {"answers": {"q0": "Bia"}})
        assert back.event("message.complete")["payload"]["text"] == "Thanks, Bia."


# -- approvals -----------------------------------------------------------------------------------


def _git_reset(call_id: str = "c1") -> FakeReply:
    return FakeReply(calls=[FakeCall("run_command", '{"command": "git reset --hard"}', id=call_id)])


def test_a_flagged_command_asks_the_client_and_runs_once_approved(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(_git_reset(), FakeReply(text="ESCALATE"), FakeReply(text="Done."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client, auto=False)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Reset"})
        asked = client.request("approval")
        params = asked["params"]
        assert (params["tool"], params["target"]) == ("run_command", "git reset --hard")
        assert "destroys uncommitted changes" in params["reason"]
        # The call it holds, so the screen marks that call waiting rather than running.
        assert params["call_id"] == "c1"
        client.answer(asked["id"], {"choice": "once"})
        complete = client.events_until("message.complete")

    finished = next(e for e in complete if e["type"] == "tool.complete")["payload"]
    # It ran: outside a repository git exits non-zero, a failed call, never a denied one.
    assert finished["status"] != "denied"
    assert "exit code" in finished["result_text"]


def test_a_refused_command_does_not_run_and_the_model_hears_why(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(_git_reset(), FakeReply(text="ESCALATE"), FakeReply(text="Understood."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client, auto=False)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Reset"})
        asked = client.request("approval")
        client.answer(asked["id"], {"choice": "deny", "reason": "not on this folder"})
        complete = client.events_until("message.complete")

    finished = next(e for e in complete if e["type"] == "tool.complete")["payload"]
    assert finished["status"] == "denied"
    assert "not on this folder" in finished["result_text"]


def test_auto_mode_runs_a_flagged_command_without_asking(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(_git_reset(), FakeReply(text="Done."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        assert client.result(
            "conversation.auto_mode", {"conversation_id": conversation, "enabled": True}
        ) == {"enabled": True}
        client.result("conversation.send", {"conversation_id": conversation, "text": "Reset"})
        complete = client.events_until("message.complete")
        reopened = client.result("conversation.open", {"conversation_id": conversation})

    assert not any(e["type"] == "request.cancel" for e in complete)
    assert len(fake.bodies(CHAT)) == 2  # no guardian call
    assert reopened["auto_mode"] is True


# -- stopping and steering -----------------------------------------------------------------------


def test_stopping_a_turn_withdraws_its_question_and_leaves_the_history_whole(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    fake.reply(_ask({"questions": [{"question": "Sure?"}]}), FakeReply(text="Again."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Start"})
        asked = client.request("ask_user")

        assert client.result("conversation.interrupt", {"conversation_id": conversation}) == {
            "interrupted": True
        }
        cancel = client.event("request.cancel")["payload"]
        assert (cancel["id"], cancel["reason"]) == (asked["id"], "withdrawn")
        assert client.event("message.complete")["payload"]["status"] == "interrupted"
        assert client.result("conversation.interrupt", {"conversation_id": conversation}) == {
            "interrupted": False
        }

        client.result("conversation.send", {"conversation_id": conversation, "text": "Once more"})
        assert client.event("message.complete")["payload"]["text"] == "Again."

    sent = fake.bodies(CHAT)[1]["messages"]
    tool = next(m for m in sent if m["role"] == "tool")
    assert tool["tool_call_id"] == "a1"  # the stopped call kept a result
    history = ConversationStore(Database(home.database_file)).open(conversation).history
    assert isinstance(history[-1], AssistantMessage)


def test_a_message_sent_mid_turn_enters_after_the_round_of_tools(
    serve: Serve, fake: FakeModelServer, work: Path
) -> None:
    (work / "notes.md").write_text("buy bread", encoding="utf-8")
    fake.reply(
        FakeReply(calls=[FakeCall("read_file", '{"path": "notes.md"}', id="r1")], wait_before=0.5),
        FakeReply(text="Pão."),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Read notes"})
        steered = client.result(
            "conversation.send", {"conversation_id": conversation, "text": "In Portuguese."}
        )
        events = client.events_until("message.complete")

    assert steered == {"status": "steered"}
    kinds = [e["type"] for e in events if e["type"] not in ("message.delta", "reasoning.delta")]
    assert kinds == [
        "message.start",
        "tool.start",
        "tool.complete",
        "message.steered",
        "message.complete",
    ]
    assert fake.bodies(CHAT)[1]["messages"][-1] == {"role": "user", "content": "In Portuguese."}


def test_a_message_sent_after_the_last_round_starts_the_next_turn(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="First answer.", wait_before=0.5), FakeReply(text="Second answer."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "One"})
        client.result("conversation.send", {"conversation_id": conversation, "text": "Two"})
        first = client.events_until("message.complete")
        second = client.events_until("message.complete")

    assert first[-1]["payload"]["text"] == "First answer."
    assert second[0] == {
        **second[0],
        "type": "message.start",
        "payload": {"text": "Two", "kind": "message", "attachments": []},
    }
    assert second[-1]["payload"]["text"] == "Second answer."


def test_stopping_hands_back_the_messages_that_never_entered(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="Slow.", wait_before=5))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "One"})
        client.result("conversation.send", {"conversation_id": conversation, "text": "Two"})
        client.result("conversation.interrupt", {"conversation_id": conversation})
        done = client.event("message.complete")["payload"]

    assert done["status"] == "interrupted"
    assert done["unsent"] == ["Two"]


# -- the protocol --------------------------------------------------------------------------------


def test_the_server_serves_exactly_the_declared_methods(serve: Serve) -> None:
    assert serve().gateway.handled == frozenset(METHODS)
    assert {"message.start", "message.complete", "request.cancel"} <= set(EVENTS)


@pytest.mark.parametrize(
    ("frame", "code"),
    [
        ({"jsonrpc": "2.0", "id": 1, "method": "nope"}, -32601),
        (
            {"jsonrpc": "2.0", "id": 1, "method": "conversation.send", "params": {"text": "x"}},
            -32602,
        ),
        (
            {"jsonrpc": "2.0", "id": 1, "method": "conversation.list", "params": {"limit": "5"}},
            -32602,
        ),
        ({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"extra": 1}}, -32602),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "conversation.open",
                "params": {"conversation_id": "x"},
            },
            NOT_FOUND,
        ),
        ({"jsonrpc": "2.0", "id": 1}, -32600),
    ],
)
def test_a_wrong_frame_gets_its_error_code(serve: Serve, frame: dict[str, Any], code: int) -> None:
    with serve().client() as client:
        client.send(frame)
        error = client._take(lambda f: f.get("id") == 1)  # pyright: ignore[reportPrivateUsage]

    assert error["error"]["code"] == code


def test_a_frame_that_is_not_json_gets_a_parse_error(serve: Serve) -> None:
    with serve().client() as client:
        client.socket.send("{not json")
        error = client._take(lambda f: "error" in f)  # pyright: ignore[reportPrivateUsage]
        assert client.result("ping") == {}

    assert error["error"]["code"] == -32700


def test_an_empty_message_is_refused(serve: Serve) -> None:
    with serve().client() as client:
        conversation = _conversation(client)
        response = client.call("conversation.send", {"conversation_id": conversation, "text": "  "})

    assert response["error"]["code"] == -32602


# -- who may connect -----------------------------------------------------------------------------


def test_a_client_without_the_token_is_refused(serve: Serve) -> None:
    server = serve()
    with pytest.raises(InvalidStatus), server.client(token="wrong"):
        pass


def test_a_page_from_another_site_is_refused(serve: Serve) -> None:
    server = serve()
    with pytest.raises(InvalidStatus), server.client(origin="http://evil.example"):  # pyright: ignore[reportArgumentType]
        pass
    with server.client(origin="http://localhost:5173") as client:  # pyright: ignore[reportArgumentType]
        assert client.result("ping") == {}


def test_the_history_of_a_stored_conversation_is_shown_on_open(serve: Serve, home: Home) -> None:
    store = ConversationStore(Database(home.database_file))
    stored = store.create()
    stored.history += [UserMessage("Earlier"), AssistantMessage("Reply")]
    stored.save()
    with serve().client() as client:
        opened = client.result("conversation.open", {"conversation_id": stored.id})

    assert [m["text"] for m in opened["messages"]] == ["Earlier", "Reply"]
    assert opened["conversation"]["id"] == stored.id


def test_the_review_after_a_turn_reaches_the_client(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    store = ConversationStore(Database(home.database_file))
    stored = store.create()
    for n in range(9):
        stored.history += [UserMessage(f"message {n}"), AssistantMessage("ok")]
    stored.save()
    remember = json.dumps({"action": "add", "target": "memory", "content": "Uses metric units."})
    fake.reply(
        FakeReply(text="Noted."),
        FakeReply(calls=[FakeCall("memory", remember, id="m1")]),
        FakeReply(text="Kept it."),
    )
    with serve().client() as client:
        client.result("conversation.open", {"conversation_id": stored.id})
        client.result("conversation.send", {"conversation_id": stored.id, "text": "In km, please."})
        summary = client.event("review.summary")["payload"]

    assert summary == {
        "text": "Memory updated",
        "actions": ["Memory updated"],
        "proposals": [],
        "diffs": {},
    }


def test_a_skill_the_review_changed_reaches_the_client_with_its_diff(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    conversation = _review_due(home)
    skill = home.skills_folder / "notes" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: notes\ndescription: Use when writing notes.\n---\nWrite in bullets.\n",
        encoding="utf-8",
        newline="\n",
    )
    patch = json.dumps(
        {
            "operations": [
                {
                    "action": "patch",
                    "name": "notes",
                    "old_string": "Write in bullets.",
                    "new_string": "Write in bullets, at most five.",
                }
            ]
        }
    )
    fake.reply(
        FakeReply(text="Noted."),
        FakeReply(calls=[FakeCall("use_skill", json.dumps({"name": "notes"}), id="s1")]),
        FakeReply(calls=[FakeCall("skill_manage", patch, id="s2")]),
        FakeReply(text="Patched notes."),
    )
    with serve().client() as client:
        client.result("conversation.open", {"conversation_id": conversation})
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Five bullets at most."}
        )
        summary = client.event("review.summary")["payload"]

    assert summary["actions"] == ["Skill 'notes' updated"]
    assert summary["diffs"]["Skill 'notes' updated"].splitlines() == [
        "--- a/SKILL.md",
        "+++ b/SKILL.md",
        "@@ -4,2 +4,2 @@",
        " ---",
        "-Write in bullets.",
        "+Write in bullets, at most five.",
    ]


# -- memory proposals ----------------------------------------------------------------------------


def _review_due(home: Home) -> str:
    """A stored conversation whose next turn makes the memory review due."""
    stored = ConversationStore(Database(home.database_file)).create()
    for n in range(9):
        stored.history += [UserMessage(f"message {n}"), AssistantMessage("ok")]
    stored.save()
    return stored.id


def test_a_proposal_reaches_every_client_and_is_approved_from_any(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    files = MemoryFiles(home.memory_folder)
    files.change(Target.USER, [Operation(Action.ADD, "Lives in Porto.")])
    conversation = _review_due(home)
    moved = json.dumps(
        {"action": "replace", "target": "user", "old_text": "Porto", "content": "Lives in Lisbon."}
    )
    fake.reply(
        FakeReply(text="Noted."),
        FakeReply(calls=[FakeCall("memory", moved, id="m1")]),
        FakeReply(text="Proposed."),
    )
    server = serve()
    with server.client() as watching, server.client() as elsewhere:
        watching.result("conversation.open", {"conversation_id": conversation})
        watching.result(
            "conversation.send", {"conversation_id": conversation, "text": "I moved to Lisbon."}
        )
        summary = watching.event("review.summary")["payload"]
        assert watching.event("proposals.changed")["payload"] == {"count": 1}
        # A client attached to no conversation hears it too, for its count.
        assert elsewhere.event("proposals.changed")["payload"] == {"count": 1}
        [row] = elsewhere.result("memory.proposals")["proposals"]
        approved = elsewhere.result("memory.approve", {"proposal_id": row["id"]})
        assert watching.event("proposals.changed")["payload"] == {"count": 0}
        listed = elsewhere.result("memory.proposals")

    assert summary["text"] == "User profile change proposed"
    assert summary["proposals"] == [row["id"]]
    assert row["target"] == "user"
    assert row["conversation_id"] == conversation
    assert row["changes"] == [
        {"action": "replace", "content": "Lives in Lisbon.", "entry": "Lives in Porto."}
    ]
    assert row["problem"] is None
    assert approved == {
        "applied": True,
        "error": None,
        "replaced": ["Lives in Porto."],
        "removed": [],
    }
    assert listed == {"proposals": []}
    assert files.entries(Target.USER) == ["Lives in Lisbon."]


def test_a_proposal_whose_entry_changed_is_marked_and_only_discarded(
    serve: Serve, home: Home
) -> None:
    files = MemoryFiles(home.memory_folder)
    files.change(Target.MEMORY, [Operation(Action.ADD, "Staging DB: pg-staging-2")])
    server = serve()
    proposal = server.gateway.assistant.proposals.propose(
        Target.MEMORY, [Operation(Action.REMOVE, old_text="Staging DB")], None
    )
    assert isinstance(proposal, Proposal)
    newer = [Operation(Action.REPLACE, "Staging DB: pg-staging-3", "pg-staging-2")]
    assert files.change(Target.MEMORY, newer).entries is not None
    with server.client() as client:
        [row] = client.result("memory.proposals")["proposals"]
        refused = client.result("memory.approve", {"proposal_id": proposal.id})
        kept = client.result("memory.proposals")["proposals"]
        discarded = client.result("memory.discard", {"proposal_id": proposal.id})
        # The refused approval changed nothing, so the first count heard is the discard's.
        changed = client.event("proposals.changed")["payload"]
        again = client.result("memory.discard", {"proposal_id": proposal.id})
        gone = client.result("memory.approve", {"proposal_id": proposal.id})

    assert "changed since this was proposed" in row["problem"]
    assert refused == {"applied": False, "error": row["problem"], "replaced": [], "removed": []}
    assert [found["id"] for found in kept] == [proposal.id]
    assert discarded == {"discarded": True}
    assert changed == {"count": 0}
    assert again == {"discarded": False}
    assert gone["applied"] is False
    assert "no open proposal" in gone["error"]
    assert files.entries(Target.MEMORY) == ["Staging DB: pg-staging-3"]


def test_dismissed_questions_reach_the_model_as_cancelled(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(_ask({"questions": [{"question": "Color?"}]}), FakeReply(text="Ok."))
    with serve().client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Paint"})
        client.answer(client.request("ask_user")["id"], {})
        client.event("message.complete")

    assert json.loads(_tool_results(fake, 1)[0])["outcome"] == "cancelled"


def test_an_error_answer_and_a_wrong_answer_do_not_settle_a_question_wrongly(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(_ask({"questions": [{"question": "Color?"}]}), FakeReply(text="Ok."))
    with serve().client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Paint"})
        asked = client.request("ask_user")
        client.answer(asked["id"], {"answers": "blue"})  # not the declared shape: ignored
        client.send({"jsonrpc": "2.0", "id": asked["id"], "error": {"code": 1, "message": "no"}})
        client.answer(asked["id"], {"answers": {"q0": "late"}})  # the wait already ended
        client.event("message.complete")

    assert json.loads(_tool_results(fake, 1)[0])["outcome"] == "cancelled"


def test_a_question_nobody_answers_times_out(serve: Serve, fake: FakeModelServer) -> None:
    fake.reply(_ask({"questions": [{"question": "Color?"}]}), FakeReply(text="Ok."))
    with serve(ask_seconds=0.3).client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Paint"})
        asked = client.request("ask_user")
        cancel = client.event("request.cancel")["payload"]
        client.event("message.complete")

    assert cancel == {"id": asked["id"], "method": "ask_user", "reason": "timeout"}
    assert json.loads(_tool_results(fake, 1)[0])["outcome"] == "timed_out"


def test_auto_mode_is_read_without_changing_it_and_unknown_conversations_are_said(
    serve: Serve,
) -> None:
    with serve().client() as client:
        conversation = _conversation(client)
        read_only = client.result("conversation.auto_mode", {"conversation_id": conversation})
        missing = [
            client.call(method, params)["error"]["code"]
            for method, params in [
                ("conversation.auto_mode", {"conversation_id": "nope"}),
                ("conversation.auto_mode", {"conversation_id": "nope", "enabled": True}),
                ("conversation.events", {"conversation_id": "nope", "after": 0}),
                ("conversation.interrupt", {"conversation_id": "nope"}),
                ("conversation.send", {"conversation_id": "nope", "text": "hi"}),
            ]
        ]

    assert read_only == {"enabled": True}, "a new conversation starts in Auto"
    assert missing == [NOT_FOUND] * 5


def test_the_key_screen_stores_a_key_the_provider_takes_and_never_sends_it_back(
    serve: Serve, fake: FakeModelServer, vault: Vault, caplog: pytest.LogCaptureFixture
) -> None:
    """The key screen (docs/ARCHITECTURE.md, The installer), through the real socket: a refused
    key is kept out, a good one stored, and the next turn runs on it."""
    fake.reply(FakeReply(text="Connected."))
    server = serve()
    vault.delete("OPENROUTER_API_KEY")
    with server.client() as client:
        before = client.result("provider.list")["providers"]
        empty = client.result("model.options")["providers"]
        refused = client.result("provider.connect", {"provider": "openrouter", "key": "wrong"})
        frames = [
            json.dumps(
                client.call("provider.connect", {"provider": "openrouter", "key": "fake-key"})
            )
        ]
        after = client.result("provider.list")["providers"]
        options = client.result("model.options")
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Hi"})
        client.event("message.complete")
        unknown = client.call("provider.connect", {"provider": "nope", "key": "x"})["error"]

    assert [(p["id"], p["stored"]) for p in before] == [
        ("openai", False),
        ("openrouter", False),
        ("gemini", False),
        ("anthropic", False),
    ]
    assert before[1]["key_page"] == "https://openrouter.ai/keys"
    assert empty == []  # what makes the app open on the key screen
    assert (refused["status"], refused["stored"]) == ("refused", False)
    connected = json.loads(frames[0])["result"]
    assert (connected["status"], connected["stored"]) == ("valid", True)
    assert connected["detail"] == "The key opens OpenRouter."
    assert "fake-key" not in frames[0]
    assert "fake-key" not in caplog.text
    assert [(p["id"], p["stored"]) for p in after][1] == ("openrouter", True)
    assert [p["id"] for p in options["providers"]] == ["openrouter"]
    assert fake.bodies(CHAT)[0]["model"] == "openai/gpt-6-luna"
    assert unknown["code"] == -32602
    assert "There is no provider 'nope'" in unknown["message"]


def test_the_picker_lists_the_keyed_providers_and_a_pick_runs_the_next_turn(
    serve: Serve, fake: FakeModelServer
) -> None:
    """model.options and the per-session pick, through the real socket."""
    fake.reply(FakeReply(text="Default here."), FakeReply(text="Sonnet here."))
    with serve().client() as client:
        listed = client.result("model.options")
        options = listed["providers"]
        conversation = _conversation(client)
        before = client.result("conversation.model", {"conversation_id": conversation})
        client.result("conversation.send", {"conversation_id": conversation, "text": "Hi"})
        client.event("message.complete")
        picked = client.result(
            "conversation.model",
            {
                "conversation_id": conversation,
                "provider": "openrouter",
                "model": "anthropic/claude-sonnet-5.5",
                "effort": "low",
            },
        )
        client.result("conversation.send", {"conversation_id": conversation, "text": "Again"})
        client.event("message.complete")
        opened = client.result("conversation.open", {"conversation_id": conversation})
        refused = [
            client.call("conversation.model", params)["error"]
            for params in [
                {"conversation_id": conversation, "provider": "openrouter"},
                {
                    "conversation_id": conversation,
                    "provider": "openrouter",
                    "model": "anthropic/claude-sonnet-5.5",
                    "effort": "extreme",
                },
                {"conversation_id": conversation, "provider": "openai", "model": "gpt-6-luna"},
            ]
        ]
        missing = client.call("conversation.model", {"conversation_id": "nope"})["error"]

    assert [provider["id"] for provider in options] == ["openrouter"]
    # Nobody chose an effort: Atlas's default, which the picker shows and the turn sends.
    assert listed["default"] == {
        "provider": "openrouter",
        "model": "openai/gpt-6-luna",
        "effort": "medium",
        "picked": False,
    }
    sonnet = next(m for m in options[0]["models"] if m["id"] == "anthropic/claude-sonnet-5.5")
    assert sonnet["reasoning"] is True
    assert "low" in sonnet["efforts"]
    assert sonnet["default_effort"] == "medium"
    assert before == {
        "provider": "openrouter",
        "model": "openai/gpt-6-luna",
        "effort": "medium",
        "picked": False,
    }
    assert fake.bodies(CHAT)[0]["reasoning"] == {"effort": "medium"}
    assert picked == {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-5.5",
        "effort": "low",
        "picked": True,
    }
    assert [body["model"] for body in fake.bodies(CHAT)] == [
        "openai/gpt-6-luna",
        "anthropic/claude-sonnet-5.5",
    ]
    assert [m["kind"] for m in opened["messages"]] == [
        "message",
        "message",
        "model_switch",
        "message",
        "message",
    ]
    assert [error["code"] for error in refused] == [-32602] * 3
    assert "Pick a provider and a model together." in refused[0]["message"]
    assert "does not accept the effort 'extreme'" in refused[1]["message"]
    assert "no key for OpenAI" in refused[2]["message"]
    assert missing["code"] == NOT_FOUND


def test_a_new_conversation_is_listed_as_its_first_turn_starts(
    serve: Serve, fake: FakeModelServer
) -> None:
    # The screens read the list as a turn starts: a new conversation stayed out of it until its
    # first save, a whole turn later (found by the UI review on 25/10/2026, while it waited for an
    # approval).
    fake.reply(FakeReply(text="Here.", wait_before=2))
    server = serve()
    with server.client() as client, server.client() as sidebar:
        conversation = _conversation(client)
        text = "Plan a trip\nto Lisbon"
        client.result("conversation.send", {"conversation_id": conversation, "text": text})
        assert sidebar.event("conversations.changed")["payload"]["status"] == "started"
        listed = sidebar.result("conversation.list")["conversations"]
        client.event("message.complete")

    assert [(row["id"], row["title"], row["running"]) for row in listed] == [
        (conversation, "Plan a trip", True)
    ]


def test_every_client_hears_a_turn_begin_and_end_and_the_list_says_which_runs(
    serve: Serve, fake: FakeModelServer
) -> None:
    # The sidebar's dots (asked for by the user on 20/10/2026): a conversation no screen opened,
    # as a Telegram one, still shows that it works and that it ended.
    fake.reply(_ask({"questions": [{"question": "Which city?"}]}), FakeReply(text="Lisbon."))
    server = serve()
    with server.client() as client, server.client() as sidebar:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Plan a trip"})

        started = sidebar.event("conversations.changed")["payload"]
        assert started == {"conversation_id": conversation, "status": "started", "title": ""}
        asked = client.request("ask_user")
        listed = sidebar.result("conversation.list")["conversations"]
        assert [(row["id"], row["running"]) for row in listed] == [(conversation, True)]

        client.answer(asked["id"], {"answers": {"q0": "Lisbon"}})
        ended = sidebar.event("conversations.changed")["payload"]
        assert ended == {"conversation_id": conversation, "status": "answered", "title": ""}
        client.event("message.complete")
        deadline = time.monotonic() + 10
        while server.gateway.assistant.running(conversation):
            assert time.monotonic() < deadline, "the turn never ended"
            time.sleep(0.05)
        listed = sidebar.result("conversation.list")["conversations"]
        assert [row["running"] for row in listed] == [False]

    kept = sidebar._kept  # pyright: ignore[reportPrivateUsage]
    assert not any(f["params"]["type"].startswith("message.") for f in kept if "params" in f)


def test_a_client_that_left_gets_no_more_events(serve: Serve, fake: FakeModelServer) -> None:
    fake.reply(FakeReply(text="One."), FakeReply(text="Two."))
    server = serve()
    with server.client() as client, server.client() as watcher:
        conversation = _conversation(client)
        watcher.result("conversation.open", {"conversation_id": conversation})
        client.result("conversation.send", {"conversation_id": conversation, "text": "1"})
        left_at = watcher.event("message.complete")["seq"]
        client.event("message.complete")
        assert watcher.result("conversation.leave", {"conversation_id": conversation}) == {}
        client.result("conversation.send", {"conversation_id": conversation, "text": "2"})
        client.event("message.complete")
        assert watcher.result("ping") == {}

    kept = watcher._kept  # pyright: ignore[reportPrivateUsage]
    # Only the lists' notice, which every client hears, comes after.
    later = [f["params"] for f in kept if f.get("method") == "event" and "seq" not in f["params"]]
    assert {event["type"] for event in later} <= {"conversations.changed"}
    assert all(
        f["params"]["seq"] <= left_at
        for f in kept
        if f.get("method") == "event" and "seq" in f["params"]
    )


def test_the_curator_is_asked_while_the_server_runs(serve: Serve, home: Home) -> None:
    moments = [utc_now()]
    serve(housekeeping_seconds=0.05, clock=lambda: moments[-1])
    moments.append(moments[0] + timedelta(hours=3))  # two idle hours went by
    deadline = time.monotonic() + 10
    database = sqlite3.connect(home.database_file)
    try:
        while not database.execute("SELECT count(*) FROM skills_curator_runs").fetchone()[0]:
            assert time.monotonic() < deadline, "the curator was never asked"
            time.sleep(0.05)
    finally:
        database.close()


# -- the soul --------------------------------------------------------------------------------------


def test_the_soul_screen_reads_the_seeded_soul_and_writes_it_whole(
    serve: Serve, home: Home
) -> None:
    server = serve()
    with server.client() as client:
        seeded = client.result("soul.read")
        written = client.result("soul.write", {"text": "You are Kestrel, dry and terse.\n"})
        blank = client.result("soul.write", {"text": ""})

    assert seeded["text"] == f"{seeded['default']}\n"
    assert seeded["default"].startswith("You are Atlas, a personal agent.")
    assert (seeded["path"], seeded["exists"]) == (str(home.soul_file), True)
    assert written["text"] == "You are Kestrel, dry and terse.\n"
    # Blank is the user's choice: the file stays, and the default stands in.
    assert (blank["text"], blank["exists"]) == ("", True)
    assert home.soul_file.read_text(encoding="utf-8") == ""


def test_a_soul_that_cannot_be_read_is_an_error_and_is_left_alone(serve: Serve, home: Home) -> None:
    server = serve()
    home.soul_file.write_bytes(b"You are \xff broken.")
    with server.client() as client:
        response = client.call("soul.read")

    assert "SOUL.md cannot be read" in response["error"]["message"]
    assert home.soul_file.read_bytes() == b"You are \xff broken."


def test_the_home_keeps_a_picture_a_png_and_small_and_takes_it_away(
    serve: Serve, home: Home
) -> None:
    """Asked for by the user on 18/10/2026: beside the home's name in the app, kept in the home."""
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    with serve().client() as client:
        none = client.result("home.picture")
        kept = client.result("home.set_picture", {"png": base64.b64encode(png).decode()})
        read = client.result("home.picture")
        not_png = client.call("home.set_picture", {"png": base64.b64encode(b"GIF89a").decode()})
        heavy = client.call(
            "home.set_picture", {"png": base64.b64encode(png + b"\x00" * 600_000).decode()}
        )
        garbled = client.call("home.set_picture", {"png": "not base64!"})
        on_disk = home.picture_file.read_bytes()
        gone = client.result("home.set_picture", {"png": None})

    assert none == {"png": None}
    assert base64.b64decode(kept["png"]) == png
    assert read == kept
    assert "not a PNG" in not_png["error"]["message"]
    assert "takes at most 512 KB" in heavy["error"]["message"]
    assert garbled["error"]["code"] == -32602
    assert on_disk == png, "what was refused left the picture as it was"
    assert gone == {"png": None}
    assert not home.picture_file.exists()


# -- routines --------------------------------------------------------------------------------------


def test_a_routine_runs_now_and_every_client_hears_how_it_ended(
    serve: Serve, fake: FakeModelServer
) -> None:
    """Routines through the socket: run now, the run a conversation, the notice for all."""
    fake.reply(FakeReply(text="Three headlines."))
    server = serve()
    made = server.gateway.assistant.routines.create("news", "every day at 9am", prompt="News.")
    with server.client() as client:
        listed = client.result("routine.list")["routines"]
        ran = client.result("routine.run", {"routine_id": "news"})
        finished = client.event("routine.finished")["payload"]
        runs = client.result("routine.runs", {"routine_id": made.id})["runs"]
        conversations = client.result("conversation.list")["conversations"]
        paused = client.result("routine.pause", {"routine_id": made.id})
        resumed = client.result("routine.resume", {"routine_id": made.id})
        removed = client.result("routine.remove", {"routine_id": made.id})
        missing = client.call("routine.run", {"routine_id": "nope"})["error"]
        after = client.result("routine.list")["routines"]

    assert [(r["name"], r["schedule"], r["enabled"], r["created_by"]) for r in listed] == [
        ("news", "every day at 9am", True, "user")
    ]
    assert ran["name"] == "news"
    assert finished == {
        "routine_id": made.id,
        "name": "news",
        "status": "ok",
        "conversation_id": finished["conversation_id"],
        "text": "Three headlines.",
    }
    [run] = runs
    assert (run["status"], run["lateness"], run["output"]) == ("ok", "manual", "Three headlines.")
    assert run["conversation_id"] == finished["conversation_id"]
    assert any(c["id"] == run["conversation_id"] and c["title"] == "news" for c in conversations)
    assert (paused["enabled"], resumed["enabled"]) == (False, True)
    assert removed == {"removed": True}
    assert missing["code"] == NOT_FOUND
    assert after == []


def test_the_server_fires_a_due_routine_every_minute(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    fake.reply(FakeReply(text="On schedule."))
    moments = [utc_now()]
    server = serve(housekeeping_seconds=0.05, clock=lambda: moments[-1])
    server.gateway.assistant.routines.create("tick", "every 30m", prompt="Tick.")
    with server.client() as client:
        moments.append(moments[0] + timedelta(minutes=31))
        finished = client.event("routine.finished")["payload"]

    assert (finished["name"], finished["status"], finished["text"]) == (
        "tick",
        "ok",
        "On schedule.",
    )


# -- subagents (step 12b) ------------------------------------------------------------------------


def test_a_conversations_subagents_reach_its_dock_and_take_steer_and_stop(
    serve: Serve, fake: FakeModelServer
) -> None:
    """The desktop's Subagents section through the socket: the updates, the list, steer and
    stop, and the batch's notice starting the parent's next turn."""
    tasks = [{"goal": "Index the photo folder.", "context": "ctx-dock"}]
    fake.reply(
        FakeReply(calls=[FakeCall("delegate", json.dumps({"tasks": tasks}))], when="Skills come"),
        FakeReply(text="A helper is indexing.", when="Skills come"),
        FakeReply(text="never sent", when="ctx-dock", wait_before=2),
        FakeReply(text="The indexing was stopped.", when="Skills come"),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Index."})
        started = client.event("subagent.update")
        listed = client.result("subagent.list", {"conversation_id": conversation})["subagents"]
        child = listed[0]["subagent_id"]
        steered = client.result(
            "subagent.steer",
            {"conversation_id": conversation, "subagent_id": child, "message": "Skip videos."},
        )
        stranger = client.result(
            "subagent.steer",
            {"conversation_id": conversation, "subagent_id": "sa-none", "message": "Hi."},
        )
        stopped = client.result(
            "subagent.interrupt", {"conversation_id": conversation, "subagent_id": child}
        )
        ended = client.event("subagent.update")
        while ended["payload"]["status"] == "running":
            ended = client.event("subagent.update")
        notice = client.event("message.start")
        while notice["payload"]["kind"] != "notice":
            notice = client.event("message.start")
        after = client.result("subagent.list", {"conversation_id": conversation})["subagents"]

    assert started["conversation_id"] == conversation
    assert (started["payload"]["goal"], started["payload"]["status"]) == (
        "Index the photo folder.",
        "running",
    )
    assert [row["goal"] for row in listed] == ["Index the photo folder."]
    assert steered["accepted"] is True
    assert stranger["accepted"] is False
    assert "No live subagent" in stranger["text"]
    assert stopped["accepted"] is True
    assert ended["payload"]["status"] == "interrupted"
    assert notice["payload"]["text"].startswith("[ASYNC DELEGATION BATCH COMPLETE")
    assert after == []


# -- background commands in the agent map ----------------------------------------------------------


def _sleeper(folder: Path) -> str:
    """A command that prints and then waits a minute, in the system shell's syntax; a script file,
    since `python -c` asks for approval."""
    script = folder / "sleeper.py"
    script.write_text("import time\nprint(42, flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    words = f"'{sys.executable}' '{script}'"
    return f"& {words}" if system_shell().name == "PowerShell" else words


def test_a_conversations_background_commands_reach_its_agent_map_and_stop_from_it(
    serve: Serve, fake: FakeModelServer, tmp_path: Path
) -> None:
    """Claude Code's background tasks through the socket: the update when one starts and when it
    ends, the list, the end of its output, and a stop that hands the output back."""
    command = _sleeper(tmp_path)
    start = json.dumps({"command": command, "background": True, "description": "Wait a minute"})
    fake.reply(
        FakeReply(calls=[FakeCall("run_command", start)]),
        FakeReply(text="It runs in the background."),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Wait."})
        started = client.event("command.update")
        listed = client.result("command.list", {"conversation_id": conversation})["commands"]
        ref = {"conversation_id": conversation, "command_id": "cmd-1"}
        output = client.result("command.output", ref)
        for _ in range(400):
            if "42" in output["text"]:
                break
            time.sleep(0.05)
            output = client.result("command.output", ref)
        stopped = client.result("command.stop", ref)
        ended = client.event("command.update")
        again = client.result("command.stop", ref)
        after = client.result("command.list", {"conversation_id": conversation})["commands"]
        unknown = client.result("command.output", {**ref, "command_id": "cmd-9"})
        stranger = client.call("command.list", {"conversation_id": "missing"})

    assert started["conversation_id"] == conversation
    row = started["payload"]
    assert (row["command_id"], row["description"], row["status"]) == (
        "cmd-1",
        "Wait a minute",
        "running",
    )
    assert row["command"] == command
    assert [found["command_id"] for found in listed] == ["cmd-1"]
    assert output["found"] is True
    assert "42" in output["text"]
    assert stopped["stopped"] is True
    assert "it was stopped" in stopped["text"]
    assert (ended["payload"]["status"], ended["payload"]["exit_code"] is not None) == (
        "stopped",
        True,
    )
    assert ended["payload"]["ended_at"] is not None
    assert again == {"stopped": False, "text": ""}
    assert after == []
    assert unknown == {"found": False, "text": ""}
    assert stranger["error"]["code"] == NOT_FOUND


# -- outside executors (step 13) -----------------------------------------------------------------


def test_claude_codes_request_waits_for_an_app_and_its_answer_reaches_it(
    serve: Serve, fake: FakeModelServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What Claude Code asks is a server request with no deadline: asked while no app is
    connected, it waits, reaches the next app on its workbench card (step 16c: the workbench is
    every conversation's), and the answer goes back."""
    script = tmp_path / "claude-script.json"
    script.write_text(
        json.dumps([{"tool": "Bash", "input": {"command": "pytest"}}, {"result": "Green."}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))

    arguments = {
        "executor": "claude-code",
        "tasks": [{"goal": "Run the tests.", "folder": "."}],
        "approval": "manual",
    }
    fake.reply(
        FakeReply(calls=[FakeCall("delegate", json.dumps(arguments))], when="Skills come"),
        FakeReply(text="Claude Code runs them.", when="Skills come"),
        FakeReply(text="All green.", when="Skills come"),
    )
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as first:
        conversation = _conversation(first)
        first.result("conversation.send", {"conversation_id": conversation, "text": "Test it."})
        first.events_until("message.complete")
    time.sleep(2)
    with server.client() as client:
        client.result("conversation.open", {"conversation_id": conversation})
        [request] = client.result("workbench.list")["open_requests"]
        [card] = client.result("workbench.list")["instances"]
        assert (card["owner"], card["parent_id"]) == ("atlas", conversation)
        # Its delegate line opens its session in the executors tab (step 16f).
        [task] = client.result("subagent.list", {"conversation_id": conversation})["subagents"]
        assert task["session_id"] == "fake-session-0001"
        ref = {"conversation_id": conversation, "subagent_id": task["subagent_id"]}
        assert client.result("subagent.session", ref) == {
            "found": True,
            "executor": "claude-code",
            "folder": task["folder"],
            "session_id": "fake-session-0001",
        }
        other = {**ref, "conversation_id": "another"}
        assert client.result("subagent.session", other)["found"] is False
        client.answer(request["id"], {"allow": True})
        notice = client.event("message.start", timeout=20)
        while notice["payload"]["kind"] != "notice":
            notice = client.event("message.start", timeout=20)

    assert request["method"] == "executor.approval"
    assert request["params"]["summary"] == "pytest"
    assert request["params"]["executor"] == "claude-code"
    assert "Green." in notice["payload"]["text"]


def test_claude_code_is_found_through_its_plugin_and_only_in_a_version_it_drives(
    serve: Serve, home: Home, vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Step 16c: with no executors given, the server looks for them through the plugins that
    are on, as it does at every start; the plugin's detection decides whether one is offered."""
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("claude-code", "program", json.dumps(FAKE))
    manifest = plugins.view("claude-code").manifest
    assert manifest is not None
    plugins.enable("claude-code", manifest.fingerprint())
    server = serve()
    assistant = server.gateway.assistant
    deadline = time.monotonic() + 30
    while not assistant.executors() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert assistant.executors() == ["claude-code"]
    detection = server.gateway.detections["claude-code"]
    assert (detection.version, detection.supported) == ("2.1.140", True)
    assert detection.account is not None
    assert detection.account.login == "someone@example.com"

    with server.client() as client:
        [row] = [p for p in client.result("plugin.list")["plugins"] if p["name"] == "claude-code"]
        assert row["running"]
        client.result("plugin.disable", {"name": "claude-code"})
        assert assistant.executors() == [], "off: no longer offered"
        client.result("plugin.enable", {"name": "claude-code", "consent": row["consent"]})
        assert assistant.executors() == ["claude-code"]
        monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.0.1")
        setting = {"name": "claude-code", "setting": "program", "value": json.dumps(FAKE)}
        client.result("plugin.setting", setting)

    assert assistant.executors() == [], "too old: detected, not offered"
    assert server.gateway.detections["claude-code"].problem.startswith("Claude Code 2.0.1 is")


@pytest.mark.offered_executors
def test_an_installed_harness_is_offered_while_its_plugin_is_off_and_turned_on_with_consent(
    serve: Serve, home: Home, vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Step 16c: at every start Atlas looks for the harnesses of the plugins it ships, off or on;
    the onboarding offers what it found, with what the plugin declares, and the user's yes there
    turns it on. A harness not installed is offered as not found."""
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("claude-code", "program", json.dumps(FAKE))
    # Codex and the Gemini CLI, shipped too, are looked for on programs that are not there, not
    # this computer's own.
    plugins.set_setting("codex", "program", '["no-such-codex"]')
    plugins.set_setting("gemini-cli", "program", '["no-such-gemini"]')
    plugins.set_setting("deepseek", "program", '["no-such-dsh"]')
    server = serve()
    with server.client() as client:
        offers = {row["name"]: row for row in client.result("workbench.list")["executors"]}
        offer = offers["claude-code"]
        assert (offers["codex"]["installed"], offers["codex"]["enabled"]) == (False, False)
        assert (offers["gemini-cli"]["installed"], offers["gemini-cli"]["enabled"]) == (
            False,
            False,
        )
        assert (offers["deepseek"]["installed"], offers["deepseek"]["enabled"]) == (False, False)
        assert (offer["name"], offer["enabled"], offer["installed"], offer["version"]) == (
            "claude-code",
            False,
            True,
            "2.1.140",
        )
        assert any("Adds the executor claude-code" in line for line in offer["declaration"])
        assert server.gateway.assistant.executors() == [], "offered, not running"

        client.result("plugin.enable", {"name": "claude-code", "consent": offer["consent"]})
        [running] = [row for row in client.result("workbench.list")["executors"] if row["enabled"]]
        assert (running["enabled"], running["account"]) == (True, "someone@example.com")
        assert server.gateway.assistant.executors() == ["claude-code"]

        client.result("plugin.disable", {"name": "claude-code"})
        client.result(
            "plugin.setting",
            {"name": "claude-code", "setting": "program", "value": '["no-such-claude"]'},
        )
        missing = {row["name"]: row for row in client.result("workbench.list")["executors"]}[
            "claude-code"
        ]
        assert (missing["installed"], missing["enabled"]) == (False, False)
        assert missing["problem"].startswith("Claude Code could not start"), "it says why"


def test_the_screen_looks_again_and_finds_a_harness_updated_while_atlas_runs(
    serve: Serve, home: Home, vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Detection runs at a start and whenever the screen asks (`executor.detect`, the executors
    tab's "Look again"): a Claude Code updated, or logged in, while Atlas runs is found without a
    restart. Before, only a start or a plugin turned on or off looked again."""
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"loggedIn": False}), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(auth))
    monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.0.1")
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("claude-code", "program", json.dumps(FAKE))
    manifest = plugins.view("claude-code").manifest
    assert manifest is not None
    plugins.enable("claude-code", manifest.fingerprint())
    server = serve()
    assistant = server.gateway.assistant
    with server.client() as client:
        [old] = client.result("executor.detect")["executors"]
        assert (old["installed"], old["supported"], old["logged_in"]) == (True, False, False)
        assert old["problem"].endswith("Update it: claude update")
        assert assistant.executors() == []

        monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.1.296")
        [updated] = client.result("executor.detect")["executors"]
        assert (updated["supported"], updated["logged_in"], updated["problem"]) == (True, False, "")
        assert assistant.executors() == ["claude-code"]

        auth.unlink()
        [logged] = client.result("executor.detect")["executors"]
        assert (logged["logged_in"], logged["account"]) == (True, "someone@example.com")


def test_a_look_that_fails_says_why_and_never_turns_its_plugin_off(
    serve: Serve, home: Home, vault: Vault, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A plugin that makes no executor (a `program` setting it refuses) is a row saying why, and
    its plugin stays on: before, the failure turned the plugin off and the harness left the
    screen until the plugin was turned on again. Fixed, the next look finds it."""
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("claude-code", "program", "not a list")
    manifest = plugins.view("claude-code").manifest
    assert manifest is not None
    plugins.enable("claude-code", manifest.fingerprint())
    server = serve()
    with server.client() as client:
        [refused] = client.result("executor.detect")["executors"]
        assert (refused["installed"], refused["title"], refused["plugin"]) == (
            False,
            "claude-code",
            "claude-code",
        )
        assert refused["problem"].startswith("The program setting is not a JSON list")
        [row] = [p for p in client.result("plugin.list")["plugins"] if p["name"] == "claude-code"]
        assert (row["enabled"], row["problem"]) == (True, None), "still on, nothing failed"

        setting = {"name": "claude-code", "setting": "program", "value": json.dumps(FAKE)}
        client.result("plugin.setting", setting)
        [found] = client.result("workbench.list")["executors"]
        assert (found["installed"], found["supported"], found["title"]) == (
            True,
            True,
            "Claude Code",
        )
    assert server.gateway.assistant.executors() == ["claude-code"]


def test_the_workbench_over_the_socket_its_cards_reach_every_screen(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, work: Path
) -> None:
    """Step 16c: an instance of the user's, started and prompted from its card; what it asks is
    a server request every client gets, attached to a conversation or not, and waits for an
    answer with no deadline; a client that comes back reads it in workbench.list."""
    script = tmp_path / "claude-script.json"
    script.write_text(
        json.dumps([{"tool": "Bash", "input": {"command": "make"}}, {"result": "Made."}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        listed = client.result("workbench.list")
        assert listed["instances"] == []
        [executor] = listed["executors"]
        assert (executor["name"], executor["title"], executor["version"]) == (
            "claude-code",
            "Claude Code",
            "2.1.140",
        )
        assert executor["account"] == "someone@example.com"

        card = client.result(
            "workbench.start", {"executor": "claude-code", "folder": ".", "auto": False}
        )
        assert (card["state"], card["owner"], card["folder"]) == ("idle", "user", str(work))
        refused = client.call("workbench.start", {"executor": "claude-code", "folder": "nope"})
        assert "does not exist" in refused["error"]["message"]

        prompted = client.result(
            "workbench.prompt", {"instance_id": card["instance_id"], "text": "Build it."}
        )
        assert prompted == {"steered": False}
        request = client.request("executor.approval")
        assert request["params"]["instance_id"] == card["instance_id"]
        assert (request["params"]["summary"], request["params"]["conversation_id"]) == ("make", "")

    with server.client() as back:
        again = back.result("workbench.list")
        [waiting] = again["open_requests"]
        assert waiting["id"] == request["id"], "it waited for a screen, with no deadline"
        back.answer(request["id"], {"allow": True})
        done = back.event("workbench.update", timeout=20)
        while done["payload"]["state"] != "idle" or done["payload"]["tools"] != 1:
            done = back.event("workbench.update", timeout=20)
        switched = back.result(
            "workbench.atlas_answers", {"instance_id": card["instance_id"], "enabled": True}
        )
        assert switched["atlas_answers"] is True
        auto = back.result("workbench.auto", {"instance_id": card["instance_id"], "enabled": True})
        assert auto["auto"] is True
        back.result("workbench.stop", {"instance_id": card["instance_id"]})
        assert back.result("workbench.list")["instances"] == []
        gone = back.call("workbench.stop", {"instance_id": card["instance_id"]})
        assert "no instance" in gone["error"]["message"]


def test_an_executor_switches_account_over_the_socket_and_its_row_says_the_new_one(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Asked for by the user on 18/10/2026: from the executor in the sidebar, with no session."""
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        [before] = client.result("workbench.list")["executors"]
        row = client.result(
            "executor.account", {"executor": "claude-code", "login": "other@example.com"}
        )
        [after] = client.result("workbench.list")["executors"]
        missing = client.call("executor.account", {"executor": "codex", "login": "a@b.c"})

    assert (before["account"], before["accounts"]) == ("someone@example.com", True)
    assert (row["account"], row["accounts"]) == ("other@example.com", True)
    assert after == row
    assert missing["error"]["code"] == -32602


def test_the_executors_tab_over_the_socket_lists_reads_and_continues_a_session(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, work: Path
) -> None:
    """Step 16f: every session Claude Code keeps, with the instance on it; a session read back
    by its plugin; continuing one starts an instance on it, or answers the one there."""
    session = "9c9c9c9c-0000-4000-8000-000000000001"
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps([{"say": "Building."}, {"result": "Built."}]), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", session)
    # The conftest points Claude Code's folder at a temporary one; the fake keeps its sessions
    # there, as Claude Code does.
    projects = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects"
    monkeypatch.setenv("FAKE_CLAUDE_PROJECTS", str(projects))
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        card = client.result("workbench.start", {"executor": "claude-code", "folder": "."})
        assert card["auto"] is True, "a session starts in its harness's automatic mode"
        client.result("workbench.prompt", {"instance_id": card["instance_id"], "text": "Build it."})
        done = client.event("workbench.update", timeout=20)
        while done["payload"]["state"] != "idle" or not done["payload"]["session_id"]:
            done = client.event("workbench.update", timeout=20)
        assert done["payload"]["commands"] == ["compact", "review"]

        [row] = client.result("executor.sessions", {"executor": "claude-code"})["sessions"]
        assert (row["session_id"], row["folder"], row["title"]) == (session, str(work), "Build it.")
        # The tab lists the folders first, and reads a folder's sessions once it is opened.
        [kept] = client.result("executor.folders", {"executor": "claude-code"})["folders"]
        assert (kept["executor"], kept["folder"]) == ("claude-code", str(work))
        assert kept["updated_at"]
        mine = {"executor": "claude-code", "folder": str(work)}
        assert client.result("executor.sessions", mine)["sessions"] == [row]
        none = {"executor": "claude-code", "folder": str(work / "elsewhere")}
        assert client.result("executor.sessions", none)["sessions"] == []
        assert (row["state"], row["instance_id"], row["by_atlas"]) == (
            "idle",
            card["instance_id"],
            False,
        )
        read = client.result(
            "executor.session",
            {"executor": "claude-code", "session_id": session, "folder": str(work)},
        )
        assert [(e["by"], e["text"]) for e in read["entries"]] == [
            ("user", "Build it."),
            ("harness", "Building."),
        ]
        assert read["expired"] is False

        same = client.result(
            "workbench.start", {"executor": "claude-code", "folder": ".", "session": session}
        )
        assert same["instance_id"] == card["instance_id"], "one process per session"
        copy = client.result(
            "workbench.start",
            {"executor": "claude-code", "folder": ".", "session": session, "fork": True},
        )
        assert copy["instance_id"] != card["instance_id"]
        unknown = client.call("executor.sessions", {"executor": "codex"})
        assert "not installed" in unknown["error"]["message"]


def test_an_executors_connectors_over_the_socket_with_no_prompt(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The side panel's executor tab: the MCP servers Claude Code has for its account, with the
    state of each; an executor whose plugin does not list them says so."""
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps([{"result": "Never run."}]), encoding="utf-8")
    log = tmp_path / "claude-log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))

    class Quiet:
        """Claude Code from a plugin that does not say it lists its connectors."""

        def __init__(self) -> None:
            self.inner = claude_code()
            self.title = "Quiet"

        def capabilities(self) -> Capabilities:
            return dataclasses.replace(self.inner.capabilities(), connectors=False)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    server = serve(executors={"claude-code": claude_code(), "quiet": Quiet()})
    with server.client() as client:
        found = client.result("executor.connectors", {"executor": "claude-code"})
        assert found["listed"] is True
        assert [(c["name"], c["status"], c["origin"]) for c in found["connectors"]] == [
            ("files", "connected", "user"),
            ("slack", "needs-auth", "plugin"),
            ("gmail", "failed", "plugin"),
            ("Google Drive", "connected", "account"),
            ("Gmail", "needs-auth", "account"),
        ]
        assert found["connectors"][1]["plugin"] == "marketing"
        assert found["connectors"][3]["tools"] == 1
        assert client.result("executor.connectors", {"executor": "quiet"}) == {
            "listed": False,
            "connectors": [],
        }
        unknown = client.call("executor.connectors", {"executor": "codex"})
        assert "not installed" in unknown["error"]["message"]
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert not [line for line in lines if line.get("type") == "user"], "no prompt, no model call"


def test_an_executors_plan_usage_over_the_socket_read_and_told(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The side panel's plan usage (asked for by the user on 18/10/2026): read with no prompt,
    then told to every screen as a turn's requests say it; an executor whose plugin does not read
    it says so."""
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps([{"usage": [0.5, 0.3]}, {"result": "Done."}]), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))

    class Quiet:
        """Claude Code from a plugin that does not say it reads its plan's usage."""

        def __init__(self) -> None:
            self.inner = claude_code()
            self.title = "Quiet"

        def capabilities(self) -> Capabilities:
            return dataclasses.replace(self.inner.capabilities(), usage=False)

        def __getattr__(self, name: str) -> Any:
            return getattr(self.inner, name)

    server = serve(executors={"claude-code": claude_code(), "quiet": Quiet()})
    with server.client() as client:
        read = client.result("executor.usage", {"executor": "claude-code"})
        assert (read["listed"], read["plan"]) == (True, "max")
        assert [(w["id"], w["used"], w["minutes"], w["model"]) for w in read["windows"]] == [
            ("five_hour", 45.0, 300, ""),
            ("seven_day", 25.0, 10080, ""),
            ("model:Fable", 60.0, 10080, "Fable"),
        ]
        assert read["windows"][0]["resets_at"] == "2026-10-18T19:10:00.378Z"
        assert client.event("executor.usage")["payload"] == read, "told to every screen"
        card = client.result("workbench.start", {"executor": "claude-code", "folder": "."})
        client.result("workbench.prompt", {"instance_id": card["instance_id"], "text": "Go."})
        told = client.event("executor.usage")["payload"]
        assert [(w["id"], w["used"]) for w in told["windows"]] == [
            ("five_hour", 50.0),
            ("seven_day", 30.0),
            ("model:Fable", 60.0),
        ]
        quiet = client.result("executor.usage", {"executor": "quiet"})
        assert (quiet["listed"], quiet["windows"]) == (False, [])
        unknown = client.call("executor.usage", {"executor": "codex"})
        assert "not installed" in unknown["error"]["message"]


def test_an_executors_slash_commands_over_the_socket(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session's box: the commands described, read before a session runs."""
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps([{"result": "Never run."}]), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        found = client.result("executor.commands", {"executor": "claude-code", "folder": "."})
        assert found["commands"][:2] == [
            {
                "name": "compact",
                "description": "Clear the conversation history but keep a summary",
                "hint": "<optional custom summarization instructions>",
                "group": "command",
            },
            {
                "name": "review",
                "description": "Review a pull request (user)",
                "hint": "[pr]",
                "group": "skill",
            },
        ]
        missing = client.call("executor.commands", {"executor": "claude-code", "folder": "nope"})
        assert "does not exist" in missing["error"]["message"]


def test_a_conversations_slash_list_is_atlass_commands_and_its_skills(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    """`conversation.commands` answers what the box offers; `/name` sent loads the skill and comes
    back, live and from the history, as a skill the user invoked."""
    for name in ("notes", "deploy"):
        (home.skills_folder / name).mkdir(parents=True)
        (home.skills_folder / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: The {name} skill.\n---\nWrite in bullets.\n",
            encoding="utf-8",
        )
    Bots(home.bots_folder).create("finance", body="You are the finance bot.", skills=["deploy"])
    fake.reply(FakeReply(text="Noted."))
    with serve().client() as client:
        new = client.result("conversation.commands")["commands"]
        fixed = client.result("bot.open", {"name": "finance"})["conversation_id"]
        bot = client.result("conversation.commands", {"conversation_id": fixed})["commands"]
        missing = client.call("conversation.commands", {"conversation_id": "ghost"})["error"]
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "/notes a b"})
        events = client.events_until("message.complete")
        opened = client.result("conversation.open", {"conversation_id": conversation})

    assert [(c["name"], c["group"]) for c in new] == [
        ("new", "command"),
        ("stop", "command"),
        ("deploy", "skill"),
        ("notes", "skill"),
    ]
    assert new[3] == {
        "name": "notes",
        "description": "The notes skill.",
        "hint": "",
        "group": "skill",
    }
    assert [c["name"] for c in bot] == ["stop", "deploy"]
    assert missing["code"] == NOT_FOUND
    started = next(e["payload"] for e in events if e["type"] == "message.start")
    assert started["kind"] == "skill"
    assert started["text"].endswith("alongside the skill invocation: a b")
    said = next(m for m in opened["messages"] if m["role"] == "user")
    assert (said["kind"], said["text"]) == ("skill", started["text"])


# -- the side panel's preview (step 16f) -----------------------------------------------------------


def test_the_agent_asks_the_app_to_show_a_page_beside_the_conversation(
    serve: Serve, fake: FakeModelServer, work: Path
) -> None:
    """`desktop_preview`, found by the tool search, reaches the app as `preview.open` and
    `preview.close`, the conversation's own."""
    (work / "page.html").write_text("<h1>Built</h1>", encoding="utf-8")
    fake.reply(
        FakeReply(calls=[FakeCall("tool_search", json.dumps({"query": "preview pane"}))]),
        FakeReply(
            calls=[FakeCall("desktop_preview", json.dumps({"action": "open", "url": "page.html"}))]
        ),
        FakeReply(calls=[FakeCall("desktop_preview", json.dumps({"action": "close"}))]),
        FakeReply(text="It is beside you."),
    )
    with serve().client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Show it."})
        events = client.events_until("message.complete")

    previews = [(e["type"], e["payload"]) for e in events if e["type"].startswith("preview.")]
    assert previews == [
        ("preview.open", {"target": str(work / "page.html"), "label": ""}),
        ("preview.close", {"target": "", "label": ""}),
    ]


# -- bots ----------------------------------------------------------------------------------------


def test_a_bots_fixed_conversation_is_opened_from_the_roster_and_answers_as_the_bot(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    Bots(home.bots_folder).create(
        "finance", title="Finance", description="Bills.", body="You are the finance bot."
    )
    Bots(home.bots_folder).create("work", model="openrouter/openai/gpt-6-luna")
    fake.reply(FakeReply(text="Paid on the 5th."))
    with serve().client() as client:
        before = client.result("bot.list")["bots"]
        fixed = client.result("bot.open", {"name": "finance"})["conversation_id"]
        again = client.result("bot.open", {"name": "finance"})["conversation_id"]
        client.result("conversation.send", {"conversation_id": fixed, "text": "Is the rent paid?"})
        events = client.events_until("message.complete")
        opened = client.result("conversation.open", {"conversation_id": fixed})
        after = client.result("bot.list")["bots"]
        listed = client.result("conversation.list")["conversations"]
        missing = client.call("bot.open", {"name": "ghost"})["error"]
        refused = client.call("bot.open", {"name": "Not A Name"})["error"]

    assert before == [
        {
            "name": "finance",
            "title": "Finance",
            "description": "Bills.",
            "model": None,
            "conversation_id": None,
            "updated_at": None,
            "preview": "",
            "running": False,
        },
        {
            "name": "work",
            "title": "work",
            "description": "",
            "model": "openrouter/openai/gpt-6-luna",
            "conversation_id": None,
            "updated_at": None,
            "preview": "",
            "running": False,
        },
    ]
    assert again == fixed
    assert events[-1]["payload"]["status"] == "answered"
    assert fake.bodies(CHAT)[0]["messages"][0]["content"].startswith("You are the finance bot.")
    assert [(m["role"], m["text"]) for m in opened["messages"]] == [
        ("user", "Is the rent paid?"),
        ("assistant", "Paid on the 5th."),
    ]
    assert (after[0]["conversation_id"], after[1]["conversation_id"]) == (fixed, None)
    assert after[0]["updated_at"] is not None
    assert after[0]["preview"] == "Paid on the 5th."
    # A bot's conversation is in the roster, not among the conversations.
    assert listed == []
    assert missing["code"] == NOT_FOUND
    assert "There is no bot 'ghost'" in missing["message"]
    assert "is not a bot name" in refused["message"]


def test_a_bot_is_made_changed_described_and_deleted_and_every_client_hears(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    server = serve()
    with server.client() as client, server.client() as other:
        made = client.result(
            "bot.create",
            {"name": "finance", "title": "Finance", "description": "Bills and taxes."},
        )
        heard = other.event("bots.changed")
        born = client.result("bot.describe", {"name": "finance"})
        changed = client.result(
            "bot.update",
            {
                "name": "finance",
                "title": "Money",
                "description": "Bills.",
                "mission": "Never pay late fees.",
                "model": "openrouter/openai/gpt-6-luna",
                "tools": ["read_file", "memory"],
                "mcp_servers": [],
                "skills": ["weekly-report"],
            },
        )
        described = client.result("bot.describe", {"name": "finance"})
        options = client.result("bot.options")
        taken = client.call("bot.create", {"name": "finance", "title": "", "description": ""})
        reserved = client.call("bot.create", {"name": "atlas", "title": "", "description": ""})
        ghost = client.call("bot.update", {"name": "ghost", "title": "", "description": ""})
        fixed = client.result("bot.open", {"name": "finance"})["conversation_id"]
        client.result("routine.list")
        deleted = client.result("bot.delete", {"name": "finance"})
        listed = client.result("bot.list")["bots"]
        gone = client.call("bot.delete", {"name": "finance"})["error"]
        opened = client.result("conversation.open", {"conversation_id": fixed})

    assert made["name"] == "finance"
    assert heard["payload"] == {"count": 1}
    # With no mission written, one is composed from the title and the description.
    assert born["mission"].startswith(
        "# Finance\n\n**Role:** Finance\n**Mission:** Bills and taxes."
    )
    assert (born["model"], born["tools"], born["mcp_servers"], born["skills"]) == (None,) * 4
    assert (changed["title"], changed["description"], changed["model"]) == (
        "Money",
        "Bills.",
        "openrouter/openai/gpt-6-luna",
    )
    assert described == {
        "name": "finance",
        "title": "Money",
        "description": "Bills.",
        "mission": "Never pay late fees.",
        "model": "openrouter/openai/gpt-6-luna",
        "tools": ["read_file", "memory"],
        "mcp_servers": [],
        "skills": ["weekly-report"],
    }
    assert {"read_file", "run_command", "memory", "delegate", "message_agent"} <= set(
        options["tools"]
    )
    assert "tool_search" not in options["tools"]
    assert (options["mcp_servers"], options["skills"]) == ([], [])
    assert "already a bot 'finance'" in taken["error"]["message"]
    assert "'atlas' is taken" in reserved["error"]["message"]
    assert ghost["error"]["code"] == NOT_FOUND
    assert deleted == {}
    assert listed == []
    assert gone["code"] == NOT_FOUND
    assert not (home.bots_folder / "finance").exists()
    # Its conversation is kept, as every conversation is.
    assert opened["conversation"]["id"] == fixed


def test_the_main_agent_makes_a_bot_when_asked_and_the_screens_hear(
    serve: Serve, fake: FakeModelServer, home: Home
) -> None:
    found = json.dumps({"query": "create a bot"})
    create = json.dumps(
        {"name": "inbox-triage", "title": "Inbox Triage", "description": "Sorts the inbox."}
    )
    fake.reply(
        FakeReply(calls=[FakeCall("tool_search", found, id="s1")]),
        FakeReply(calls=[FakeCall("bot_create", create, id="b1")]),
        FakeReply(text="Made the Inbox Triage bot."),
    )
    with serve().client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send",
            {"conversation_id": conversation, "text": "Yes, make the inbox bot."},
        )
        heard = client.event("bots.changed")
        client.events_until("message.complete")
        listed = client.result("bot.list")["bots"]

    assert heard["payload"] == {"count": 1}
    assert [(bot["name"], bot["title"], bot["description"]) for bot in listed] == [
        ("inbox-triage", "Inbox Triage", "Sorts the inbox.")
    ]
    result = _tool_results(fake, 2)[-1]
    assert result.startswith("Made the bot @inbox-triage (Inbox Triage).")
    search = next(
        tool for tool in fake.bodies(CHAT)[0]["tools"] if tool["function"]["name"] == "tool_search"
    )
    assert "bot_create" in search["function"]["description"]  # deferred: it costs a name


def test_an_executors_other_accounts_over_the_socket(
    serve: Serve, tmp_path: Path, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 16h: another account added from the app is one row more, logged in by Claude Code
    itself in its own folder; a folder's account starts its sessions there, every screen is told,
    and a session goes back to the main account in a copy."""
    script = tmp_path / "claude-script.json"
    steps = [{"account": True}, {"usage": [0.5, 0.3]}, {"result": "Ok."}]
    script.write_text(json.dumps(steps), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        [row] = client.result("workbench.list", {})["executors"]
        assert (row["accounts"], row["account_folders"], row["base"]) == (True, True, "")
        refused = client.call(
            "executor.add_account", {"executor": "claude-code", "nickname": "a b"}
        )
        assert "nickname of letters" in refused["error"]["message"]
        added = client.result(
            "executor.add_account", {"executor": "claude-code", "nickname": "work"}
        )
        assert (added["nickname"], added["logged_in"]) == ("work", False)
        assert Path(added["folder"]) == Path.home() / ".claude-work"
        assert client.event("executor.accounts")["payload"] == {"executor": "claude-code"}
        rows = client.result("workbench.list", {})["executors"]
        assert [(r["name"], r["base"], r["nickname"], r["title"]) for r in rows] == [
            ("claude-code", "", "", "Claude Code"),
            (added["name"], "claude-code", "work", "Claude Code · work"),
        ]
        logged = client.result("executor.account", {"executor": added["name"], "login": ""})
        assert (logged["name"], logged["account"]) == (added["name"], "other@example.com")
        listed = client.result("executor.accounts", {"executor": "claude-code"})
        assert [(a["account_id"], a["login"]) for a in listed["accounts"]] == [
            ("", "someone@example.com"),
            (added["account_id"], "other@example.com"),
        ]
        assert listed["can_add"]
        client.result(
            "executor.folder_account",
            {"executor": "claude-code", "folder": str(work), "account_id": added["account_id"]},
        )
        client.event("executor.accounts")
        card = client.result("workbench.start", {"executor": "claude-code", "folder": str(work)})
        assert (card["executor"], card["account"], card["account_id"]) == (
            added["name"],
            "other@example.com",
            added["account_id"],
        )
        client.result("workbench.prompt", {"instance_id": card["instance_id"], "text": "Go."})
        told = client.event("executor.usage")["payload"]
        assert told["executor"] == added["name"], "its own plan, apart from the main account's"
        for _ in range(200):
            rows = client.result("workbench.list", {})["instances"]
            if rows[0]["state"] == "idle":
                break
            time.sleep(0.02)
        check = client.result(
            "workbench.account_switch", {"instance_id": card["instance_id"], "account_id": ""}
        )
        assert (check["login"], check["compared"]) == ("someone@example.com", True)
        main = client.result(
            "workbench.use_account", {"instance_id": card["instance_id"], "account_id": ""}
        )
        assert (main["executor"], main["account_id"]) == ("claude-code", "")
        missing = client.call(
            "executor.delete_session",
            {"executor": added["name"], "session_id": "no-such-session", "folder": str(work)},
        )
        assert "no longer keeps" in missing["error"]["message"]
        client.result("workbench.stop", {"instance_id": card["instance_id"]})
        client.result(
            "executor.remove_account",
            {"executor": "claude-code", "account_id": added["account_id"]},
        )
        after = client.result("executor.accounts", {"executor": "claude-code"})
        assert [a["account_id"] for a in after["accounts"]] == [""]
        assert [r["name"] for r in client.result("workbench.list", {})["executors"]] == [
            "claude-code"
        ]


def test_a_sessions_route_over_the_socket(
    serve: Serve, tmp_path: Path, work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session's route (asked for by the user on 21/10/2026): set from the app, each
    destination an executor and its account with how the user knows it, one Atlas does not know
    refused, and read back for the card."""
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        added = client.result(
            "executor.add_account", {"executor": "claude-code", "nickname": "work"}
        )
        card = client.result("workbench.start", {"executor": "claude-code", "folder": str(work)})
        instance = card["instance_id"]
        assert client.result("workbench.route", {"instance_id": instance}) == {
            "instance_id": instance,
            "stops": [],
            "enabled": True,
            "compact": True,
        }
        refused = client.call(
            "workbench.set_route",
            {"instance_id": instance, "targets": [{"executor": "codex"}]},
        )
        assert "not installed" in refused["error"]["message"]
        unknown = client.call(
            "workbench.set_route",
            {"instance_id": instance, "targets": [{"executor": "claude-code", "effort": "turbo"}]},
        )
        assert "no effort 'turbo'" in unknown["error"]["message"]
        targets = [
            {
                "executor": "claude-code",
                "account_id": added["account_id"],
                "model": "opus",
                "effort": "high",
            },
            {"executor": "claude-code"},
        ]
        route = client.result(
            "workbench.set_route",
            {"instance_id": instance, "targets": targets, "enabled": True, "compact": False},
        )
        read = client.result("workbench.route", {"instance_id": instance})
        client.result("workbench.stop", {"instance_id": instance})

    assert route == read
    assert route["compact"] is False, "compacting first turned off, and kept"
    assert [
        (s["name"], s["title"], s["nickname"], s["installed"], s["model"], s["effort"])
        for s in route["stops"]
    ] == [
        (added["name"], "Claude Code", "work", True, "opus", "high"),
        ("claude-code", "Claude Code", "", True, None, None),
    ]
