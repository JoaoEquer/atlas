"""The list's search through the server (step 9e): `conversation.search` finds a conversation by
its title or its messages, and says where, as the sidebar asks while the user types.

A real server on a temporary home, and a client on a real WebSocket.
"""

from atlas.conversations import TitleSource
from atlas.models import AssistantMessage, UserMessage
from server_support import AtlasServer, Serve


def _said(server: AtlasServer, said: str, answer: str, title: str = "") -> str:
    store = server.gateway.assistant.conversations
    conversation = store.create()
    conversation.history.extend([UserMessage(said), AssistantMessage(answer)])
    conversation.save()
    if title:
        assert store.retitle(conversation.id, title, TitleSource.LLM)
    return conversation.id


def test_the_search_finds_by_title_and_by_message_and_says_where(serve: Serve) -> None:
    server = serve()
    walk = _said(server, "How was the walk?", "Then a quokka crossed the trail.")
    named = _said(server, "tell me about that animal", "It lives on an island.", "Quokka facts")
    _said(server, "plan a trip", "Here is a plan.")

    with server.client() as client:
        found = client.result("conversation.search", {"query": "quok"})

    assert [hit["conversation"]["id"] for hit in found["hits"]] == [named, walk]
    by_title, by_message = found["hits"]
    assert by_title["title"] == ["", "Quokka", " facts"]
    assert by_title["snippet"] == []
    assert by_message["title"] == ["How was the walk?"]
    assert by_message["snippet"] == ["Then a ", "quokka", " crossed the trail."]
    assert by_message["said_by"] == "assistant"
    assert found["more"] is False


def test_a_search_with_nothing_to_search_finds_nothing(serve: Serve) -> None:
    server = serve()
    _said(server, "anything", "Something.")

    with server.client() as client:
        found = client.result("conversation.search", {"query": "  ()  "})

    assert found == {"hits": [], "more": False}
