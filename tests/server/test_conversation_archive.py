"""Archiving a conversation through the server (step 9e): it leaves every client's list for the
archived ones, comes back when brought back or when a new message comes, and stays archived across
a restart and a backup.

A real server on a temporary home, clients on real WebSockets, and the fake model.
"""

import time
from pathlib import Path
from typing import Any

from atlas.__main__ import SCHEMAS
from atlas.conversations import ConversationStore
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import AssistantMessage, UserMessage
from atlas.secrets import Vault
from atlas.server import NOT_FOUND
from fake_model import FakeModelServer, FakeReply
from model_support import Connect
from server_support import AtlasServer, Client, Serve

#: JSON-RPC's invalid params: what the server answers a request it refuses.
INVALID_PARAMS = -32602


def _said(server: AtlasServer, said: str) -> str:
    store = server.gateway.assistant.conversations
    conversation = store.create()
    conversation.history.extend([UserMessage(said), AssistantMessage("Sure.")])
    conversation.save()
    return conversation.id


def _titles(client: Client, **params: Any) -> list[str]:
    return [row["title"] for row in client.result("conversation.list", params)["conversations"]]


def test_an_archived_conversation_leaves_every_clients_list_for_the_archived_ones(
    serve: Serve,
) -> None:
    server = serve()
    trip = _said(server, "plan the trip")
    _said(server, "fix the bike")
    with server.client() as archiving, server.client() as other:
        archived = archiving.result("conversation.archive", {"conversation_id": trip})
        told = other.event("conversations.changed")["payload"]
        listed = other.result("conversation.list")
        shelf = other.result("conversation.list", {"archived": True})["conversations"]

        assert archived["archived_at"]
        assert told["conversation_id"] == trip
        assert [row["title"] for row in listed["conversations"]] == ["fix the bike"]
        assert listed["archived"] == 1
        assert [(row["title"], row["archived_at"]) for row in shelf] == [
            ("plan the trip", archived["archived_at"])
        ]
        # The list's search still finds it, and says it is archived.
        (hit,) = other.result("conversation.search", {"query": "trip"})["hits"]
        assert hit["conversation"]["archived_at"] == archived["archived_at"]

        archiving.result("conversation.unarchive", {"conversation_id": trip})
        other.event("conversations.changed")

        assert _titles(other) == ["fix the bike", "plan the trip"]
        assert other.result("conversation.list")["archived"] == 0
        assert (
            archiving.call("conversation.archive", {"conversation_id": "nope"})["error"]["code"]
            == NOT_FOUND
        )


def test_a_new_message_brings_an_archived_conversation_back_to_the_top_of_the_list(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="Back again."))
    server = serve()
    trip = _said(server, "plan the trip")
    _said(server, "fix the bike")
    with server.client() as client:
        client.result("conversation.archive", {"conversation_id": trip})
        assert client.event("conversations.changed")["payload"]["status"] == ""
        client.result("conversation.send", {"conversation_id": trip, "text": "One more thing"})
        started = client.event("conversations.changed")["payload"]
        at_start = client.result("conversation.list")
        client.events_until("message.complete")
        listed = client.result("conversation.list")

    # Back in the list as its turn starts, when every screen reads the list again...
    assert started == {"conversation_id": trip, "status": "started", "title": ""}
    assert sorted(row["title"] for row in at_start["conversations"]) == [
        "fix the bike",
        "plan the trip",
    ]
    assert at_start["archived"] == 0
    # ...and on top once the turn kept its new messages.
    assert [row["title"] for row in listed["conversations"]] == ["plan the trip", "fix the bike"]
    assert listed["conversations"][0]["archived_at"] == ""


def test_a_conversation_whose_turn_runs_is_not_archived(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="Slowly.", wait_before=2.0))
    server = serve()
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": "Take time"})
        deadline = time.monotonic() + 10
        while not server.gateway.assistant.running(conversation):
            assert time.monotonic() < deadline, "the turn never started"
            time.sleep(0.05)

        refused = client.call("conversation.archive", {"conversation_id": conversation})
        client.events_until("message.complete")
        listed = client.result("conversation.list")

    assert refused["error"]["code"] == INVALID_PARAMS
    assert "turn" in refused["error"]["message"]
    assert [row["id"] for row in listed["conversations"]] == [conversation]


def test_an_archived_conversation_stays_archived_across_a_restart_and_a_backup(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, work: Path, tmp_path: Path
) -> None:
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    models = connect(fake, "openrouter", settings=chosen)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    with AtlasServer(home, settings, models, vault) as first:
        trip = _said(first, "plan the trip")
        _said(first, "fix the bike")
        with first.client() as client:
            client.result("conversation.archive", {"conversation_id": trip})

    # The server starts again on the same home, as after a restart.
    with AtlasServer(home, settings, models, vault) as again, again.client() as client:
        assert _titles(client) == ["fix the bike"]
        assert _titles(client, archived=True) == ["plan the trip"]
        backup = tmp_path / "backup.zip"
        client.result("home.backup", {"path": str(backup)})
        client.result("home.import", {"path": str(backup), "name": "copy"})

    copy = ConversationStore(Database(home.root / "homes" / "copy" / "atlas.db"))
    assert [info.title for info in copy.page(50)[0]] == ["fix the bike"]
    assert [info.id for info in copy.page(50, archived=True)[0]] == [trip]
