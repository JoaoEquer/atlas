"""Renaming a conversation through the server (step 9e): the name the user gives is its title from
then on, every client hears it, and a title model's title never replaces it.

A real server on a temporary home, clients on real WebSockets, and the fake model.
"""

import json
import time

import pytest

from atlas.conversations import TitleSource
from atlas.models import AssistantMessage, UserMessage
from atlas.server import NOT_FOUND
from fake_model import FakeModelServer, FakeReply
from model_support import Connect
from server_support import AtlasServer, Serve

NAMING = "Your job is naming chat sessions."


def _said(server: AtlasServer, said: str) -> str:
    store = server.gateway.assistant.conversations
    conversation = store.create()
    conversation.history.extend([UserMessage(said), AssistantMessage("Sure.")])
    conversation.save()
    return conversation.id


def test_a_name_given_is_the_title_and_every_client_hears_it(serve: Serve) -> None:
    server = serve()
    trip = _said(server, "plan the trip")
    with server.client() as renaming, server.client() as other:
        renamed = renaming.result(
            "conversation.rename", {"conversation_id": trip, "title": "  Trip to\n  Lisbon "}
        )
        told = other.event("conversations.changed")
        listed = other.result("conversation.list")["conversations"]

    assert renamed == {"title": "Trip to Lisbon"}
    assert told["payload"]["conversation_id"] == trip
    assert told["payload"]["title"] == "Trip to Lisbon"
    assert listed[0]["title"] == "Trip to Lisbon"
    assert server.gateway.assistant.conversations.title_source(trip) is TitleSource.USER


def test_a_name_is_cut_to_a_titles_length_and_never_empty(serve: Serve) -> None:
    server = serve()
    trip = _said(server, "plan the trip")
    with server.client() as client:
        long = client.result(
            "conversation.rename", {"conversation_id": trip, "title": "word " * 40}
        )
        empty = client.call("conversation.rename", {"conversation_id": trip, "title": " \n "})
        unknown = client.call("conversation.rename", {"conversation_id": "nope", "title": "Mine"})
        listed = client.result("conversation.list")["conversations"]

    assert long["title"] == ("word " * 16).strip()
    assert empty["error"]["code"] == -32602
    assert unknown["error"]["code"] == NOT_FOUND
    assert listed[0]["title"] == long["title"]


def test_a_title_model_never_replaces_a_name_given_while_it_wrote(
    serve: Serve, fake: FakeModelServer, connect: Connect
) -> None:
    connect(fake, "anthropic")  # its key in the vault: Haiku titles the conversation
    fake.reply(
        FakeReply(text='{"title": "Plan a week in Lisbon"}', when=NAMING, wait_before=1.0),
        FakeReply(text="Gladly.", when="You are Atlas"),
    )
    with serve().client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": "lisbon?"})
        # The title model is asked, and the user names the conversation before it answers.
        deadline = time.monotonic() + 10
        while not any(NAMING in json.dumps(body) for body in fake.bodies("/v1/messages")):
            assert time.monotonic() < deadline, "the title model was never asked"
            time.sleep(0.05)
        client.result("conversation.rename", {"conversation_id": conversation, "title": "My trip"})
        client.events_until("message.complete")
        time.sleep(1.5)
        with pytest.raises(TimeoutError):
            client.event("title.changed", timeout=0.5)
        listed = client.result("conversation.list")["conversations"]

    assert listed[0]["title"] == "My trip"
