"""The list's search (step 9e): the sidebar finds a conversation by its title or its messages, as
the user types, through the conversations' own search index.

Real SQLite on a file in a temporary home, migrated as Atlas migrates it.
"""

import pytest

from atlas.conversations import (
    BOT,
    SCHEMA,
    ConversationStore,
    Role,
    TitleSource,
    typed_query,
)
from atlas.home import Database, Home, migrate
from atlas.models import AssistantMessage, UserMessage


@pytest.fixture
def store(home: Home) -> ConversationStore:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    return ConversationStore(database)


def _said(store: ConversationStore, said: str, answer: str, title: str = "") -> str:
    """A conversation of the list: what the user said and the answer, named `title` by a title
    model when one is given; its id."""
    conversation = store.create()
    conversation.history.extend([UserMessage(said), AssistantMessage(answer)])
    conversation.save()
    if title:
        assert store.retitle(conversation.id, title, TitleSource.LLM)
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


def _matched(pieces: tuple[str, ...]) -> list[str]:
    return list(pieces[1::2])


def test_each_word_typed_also_finds_the_words_it_begins() -> None:
    assert typed_query("quok") == "quok*"
    assert typed_query('trip "road ma" deploy*') == 'trip* "road ma"* deploy*'
    assert typed_query("alpha OR beta") == "alpha* OR beta*"
    assert typed_query("  ()  ") is None


def test_a_title_is_found_whatever_its_case_and_accents_cut_where_it_matched(
    store: ConversationStore,
) -> None:
    named = _said(store, "the server is slow", "Let us look.", title="Configuração do Servidor")
    _said(store, "plan a trip", "Here is a plan.")

    found, more = store.find("config servi", 10)

    assert [hit.conversation.id for hit in found] == [named]
    assert found[0].title == ("", "Configuração", " do ", "Servidor", "")
    assert found[0].snippet == ()
    assert more is False


def test_a_message_is_found_with_the_words_that_matched_and_who_said_them(
    store: ConversationStore,
) -> None:
    trail = _said(
        store,
        "How was the walk?",
        "First we looked at the map. Then a quokka crossed the trail near the river.",
    )

    [hit], _ = store.find("quok", 10)

    assert hit.conversation.id == trail
    assert hit.title == ("How was the walk?",)
    assert _matched(hit.snippet) == ["quokka"]
    assert "".join(hit.snippet).startswith("First we looked at the map. Then a quokka crossed")
    assert hit.said_by == Role.ASSISTANT


def test_every_word_typed_must_be_in_the_title_or_in_one_message(
    store: ConversationStore,
) -> None:
    _said(store, "Fix the bike", "Check the bike brakes first.")

    [hit], _ = store.find("brakes bike", 10)

    assert _matched(hit.snippet) == ["bike", "brakes"]
    assert store.find("bike chain", 10) == ([], False)


def test_the_titles_come_first_by_activity_then_the_messages_by_relevance(
    store: ConversationStore, home: Home
) -> None:
    once = _said(
        store, "a long talk", "One mention of the lighthouse here, among many other words."
    )
    older_title = _said(store, "a story", "Once upon a time.", title="The lighthouse keeper")
    often = _said(store, "tell me more", "The lighthouse, the lighthouse, the lighthouse.")
    newer_title = _said(store, "a painting", "Blue and white.", title="Painting a lighthouse")
    _in_order(home, once, older_title, often, newer_title)

    found, _ = store.find("lighthouse", 10)

    assert [hit.conversation.id for hit in found] == [newer_title, older_title, often, once]
    # A conversation found by its title shows no snippet: the title is where it matched.
    assert [bool(hit.snippet) for hit in found] == [False, False, True, True]


def test_only_the_lists_conversations_are_searched(store: ConversationStore) -> None:
    listed = _said(store, "the quarterly report", "Here it is.")
    bot = store.create(source=BOT, bot="finance", title="Finance")
    bot.history.extend([UserMessage("the quarterly report"), AssistantMessage("Done.")])
    bot.save()
    subagent = store.create(source="subagent")
    subagent.history.extend([UserMessage("the quarterly report"), AssistantMessage("Done.")])
    subagent.save()

    found, _ = store.find("quarterly", 10)

    assert [hit.conversation.id for hit in found] == [listed]


def test_more_than_the_limit_says_so(store: ConversationStore) -> None:
    for number in range(4):
        _said(store, f"garden note {number}", "Water the plants.")

    found, more = store.find("garden", 3)

    assert len(found) == 3
    assert more is True
    assert store.find("garden", 4)[1] is False


def test_a_message_holding_the_marks_keeps_its_pieces_in_their_place(
    store: ConversationStore,
) -> None:
    _said(store, "odd text", "A stray \x02 mark, then a quokka, then \x03 another.")

    [hit], _ = store.find("quokka", 10)

    assert _matched(hit.snippet) == ["quokka"]
    assert "\x02" not in "".join(hit.snippet)
    assert "\x03" not in "".join(hit.snippet)


def test_nothing_searchable_finds_nothing(store: ConversationStore) -> None:
    _said(store, "anything", "Something.")

    assert store.find("  ()  ", 10) == ([], False)
    assert store.find("", 10) == ([], False)
