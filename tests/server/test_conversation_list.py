"""The list of conversations through the server (step 9e): page after page by cursor.

A real server on a temporary home, and a client on a real WebSocket, as the app's sidebar talks to
it; the conversations are stored the way turns store them, a minute apart.
"""

from atlas.home import Database, Home
from atlas.models import UserMessage
from server_support import AtlasServer, Serve


def _said(server: AtlasServer, home: Home, count: int) -> list[str]:
    """`count` titled conversations a minute apart, the oldest first; their ids."""
    store = server.gateway.assistant.conversations
    ids: list[str] = []
    connection = Database(home.database_file).connect()
    try:
        for minute in range(count):
            conversation = store.create()
            conversation.history.append(UserMessage(f"Topic {minute}"))
            conversation.save()
            at = f"2026-10-27T{10 + minute // 60:02d}:{minute % 60:02d}:00.000Z"
            with connection:
                connection.execute(
                    "UPDATE conversations_threads SET updated_at = ? WHERE id = ?",
                    (at, conversation.id),
                )
            ids.append(conversation.id)
    finally:
        connection.close()
    return ids


def test_the_list_comes_a_page_at_a_time_each_conversation_once(serve: Serve, home: Home) -> None:
    server = serve()
    ids = _said(server, home, 60)
    with server.client() as client:
        # The app of before asks with no cursor, and gets the first page as it did.
        first = client.result("conversation.list")
        rest = client.result("conversation.list", {"cursor": first["next_cursor"]})
        # A client reads again what it holds with a limit of as many rows.
        again = client.result("conversation.list", {"limit": 60})

    assert len(first["conversations"]) == 50
    assert first["next_cursor"]
    assert rest["next_cursor"] == ""
    listed = [row["id"] for row in first["conversations"] + rest["conversations"]]
    assert listed == ids[::-1]
    assert [row["id"] for row in again["conversations"]] == listed
    assert again["next_cursor"] == ""


def test_a_cursor_the_server_never_gave_is_refused(serve: Serve) -> None:
    with serve().client() as client:
        reply = client.call("conversation.list", {"cursor": "page 2"})

    assert reply["error"]["code"] == -32602
    assert "cursor" in reply["error"]["message"]
