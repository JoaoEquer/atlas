"""Archived conversations (step 9e): out of the list of conversations, in their own section, back
in the list when brought back or when a new message comes, and never touched otherwise.

Real SQLite on a file in a temporary home, migrated as Atlas migrates it.
"""

import pytest

from atlas.conversations import (
    SCHEMA,
    ConversationNotFoundError,
    ConversationStore,
)
from atlas.home import Database, Home, Schema, migrate
from atlas.models import AssistantMessage, UserMessage


@pytest.fixture
def store(home: Home) -> ConversationStore:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    return ConversationStore(database)


def _said(store: ConversationStore, said: str) -> str:
    conversation = store.create()
    conversation.history.extend([UserMessage(said), AssistantMessage("Sure.")])
    conversation.save()
    return conversation.id


def _in_order(home: Home, *ids: str) -> None:
    """Give the conversations their last activity a minute apart, in this order."""
    connection = Database(home.database_file).connect()
    try:
        with connection:
            for minute, conversation_id in enumerate(ids):
                connection.execute(
                    "UPDATE conversations_threads SET updated_at = ? WHERE id = ?",
                    (f"2026-10-27T10:{minute:02d}:00.000Z", conversation_id),
                )
    finally:
        connection.close()


def _listed(store: ConversationStore, *, archived: bool = False) -> list[str]:
    found, _ = store.page(50, archived=archived)
    return [info.title for info in found]


def test_an_archived_conversation_leaves_the_list_for_the_archived_ones(
    store: ConversationStore, home: Home
) -> None:
    trip, bike, book = (_said(store, said) for said in ("plan the trip", "fix the bike", "a book"))
    _in_order(home, trip, bike, book)

    archived_at = store.archive(bike)

    assert _listed(store) == ["a book", "plan the trip"]
    assert _listed(store, archived=True) == ["fix the bike"]
    assert store.archived_count() == 1
    assert store.info(bike).archived_at == archived_at
    assert store.info(trip).archived_at is None
    # Archived again, it keeps the time it was archived at.
    assert store.archive(bike) == archived_at
    # Its messages are as they were: read, and searched by the list's search.
    assert [m.text for m in store.messages(bike)] == ["fix the bike", "Sure."]
    found, _ = store.find("bike", 50)
    assert [(hit.conversation.id, hit.conversation.archived_at) for hit in found] == [
        (bike, archived_at)
    ]


def test_brought_back_it_is_in_the_list_where_its_activity_puts_it(
    store: ConversationStore, home: Home
) -> None:
    trip, bike, book = (_said(store, said) for said in ("plan the trip", "fix the bike", "a book"))
    _in_order(home, trip, bike, book)
    store.archive(bike)

    assert store.unarchive(bike)

    assert _listed(store) == ["a book", "fix the bike", "plan the trip"]
    assert _listed(store, archived=True) == []
    assert store.archived_count() == 0
    # One not archived is left as it is.
    assert not store.unarchive(bike)


def test_the_archived_ones_come_a_page_at_a_time_too(store: ConversationStore, home: Home) -> None:
    ids = [_said(store, f"topic {number:02d}") for number in range(1, 6)]
    _in_order(home, *ids)
    for conversation_id in ids:
        store.archive(conversation_id)

    first, cursor = store.page(2, archived=True)
    assert cursor is not None
    second, cursor = store.page(2, cursor, archived=True)
    assert cursor is not None
    third, cursor = store.page(2, cursor, archived=True)

    assert [info.title for info in (*first, *second, *third)] == [
        "topic 05",
        "topic 04",
        "topic 03",
        "topic 02",
        "topic 01",
    ]
    assert cursor is None
    assert _listed(store) == []


def test_an_unknown_conversation_is_neither_archived_nor_brought_back(
    store: ConversationStore,
) -> None:
    with pytest.raises(ConversationNotFoundError):
        store.archive("nope")
    with pytest.raises(ConversationNotFoundError):
        store.unarchive("nope")


def test_the_migration_leaves_every_conversation_in_the_list(home: Home) -> None:
    database = Database(home.database_file)
    migrate(database, (Schema(SCHEMA.subject, SCHEMA.migrations[:4]),))
    connection = database.connect()
    try:
        with connection:
            connection.execute(
                "INSERT INTO conversations_threads (id, source, title, started_at, updated_at) "
                "VALUES ('a', 'app', 'plan the trip', '2026-10-27T00:00:00Z', "
                "'2026-10-27T00:00:00Z')"
            )
    finally:
        connection.close()
    migrate(database, (SCHEMA,))
    store = ConversationStore(database)

    assert _listed(store) == ["plan the trip"]
    assert store.archived_count() == 0
