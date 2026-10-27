"""Stored conversations: saving follows the history, a compaction archives instead of deleting,
and search finds the words, archived messages included.

Real SQLite on a file in a temporary home, migrated as Atlas migrates it. The history changes the
way the agent loop changes it: messages appended, and a compaction replacing the older part in
place (`history[:] = [*head, summary, *tail]`, `atlas.agent.AgentLoop._compact`).
"""

import pytest

from atlas.agent import SUMMARY_MARK
from atlas.conversations import (
    BOT,
    SCHEMA,
    ConversationNotFoundError,
    ConversationStore,
    Cursor,
    Kind,
    Role,
    StoredCall,
    StoredMessageError,
    TitleSource,
    decode,
    encode,
    fts_query,
)
from atlas.home import Database, Home, Schema, migrate
from atlas.models import (
    AssistantMessage,
    Image,
    Message,
    Origin,
    SystemMessage,
    ToolCall,
    ToolResult,
    ToolStatus,
    UserMessage,
)


@pytest.fixture
def store(home: Home) -> ConversationStore:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    return ConversationStore(database)


def _query(text: str) -> str:
    query = fts_query(text)
    assert query is not None
    return query


EVERY_KIND: list[Message] = [
    SystemMessage("You are Atlas."),
    UserMessage("Qual é a memória do servidor?"),
    AssistantMessage(
        text="",
        tool_calls=(
            ToolCall("c1", "run_command", '{"command": "free -h"}', {"extra_content": {"s": "x"}}),
        ),
        reasoning="Check the machine.",
        origin=Origin("gemini", "gemini-3.8-flash"),
        provider_fields={"reasoning_details": [{"type": "text", "text": "r"}]},
    ),
    ToolResult("c1", "Mem: 16Gi", ("mcp__notes__add",), (Image("image/png", b"\x89PNG"),)),
    AssistantMessage(
        "O servidor tem 16 GB de memória.", origin=Origin("gemini", "gemini-3.8-flash")
    ),
]


def test_every_kind_of_message_comes_back_the_same(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history.extend(EVERY_KIND)

    assert conversation.save() == len(EVERY_KIND)

    assert store.open(conversation.id).history == EVERY_KIND


def test_saving_again_stores_only_what_is_new(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history.append(UserMessage("first"))
    conversation.save()

    assert conversation.save() == 0
    conversation.history.append(AssistantMessage("answer"))
    assert conversation.save() == 1

    assert [m.text for m in store.messages(conversation.id)] == ["first", "answer"]


def test_a_result_keeps_how_its_call_ended_and_an_older_body_reads_as_completed() -> None:
    denied = ToolResult("c1", "[Atlas: denied]", status=ToolStatus.DENIED)
    assert decode("tool", encode(denied)) == denied
    # Stored before step 9c, with no status in the body: no migration, it reads as completed.
    old = decode("tool", '{"call_id": "c1", "text": "ok"}')
    assert isinstance(old, ToolResult)
    assert old.status is ToolStatus.COMPLETED


def test_a_reopened_conversation_shows_the_reasoning_the_calls_and_how_each_ended(
    store: ConversationStore,
) -> None:
    conversation = store.create()
    conversation.history.extend(EVERY_KIND)
    conversation.history.append(ToolResult("c2", "no", status=ToolStatus.CANCELLED))
    conversation.save()

    _, asked, result, answer, cancelled = store.messages(conversation.id)

    assert (asked.reasoning, asked.calls) == (
        "Check the machine.",
        (StoredCall("c1", "run_command", '{"command": "free -h"}'),),
    )
    assert (result.call_id, result.status) == ("c1", "completed")
    assert (answer.reasoning, answer.calls, answer.call_id) == ("", (), None)
    assert (cancelled.call_id, cancelled.status) == ("c2", "cancelled")


def test_the_title_is_the_first_thing_the_user_said(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history += [SystemMessage("rules"), UserMessage("Plan the trip\nwith details")]
    conversation.save()

    assert store.info(conversation.id).title == "Plan the trip"


def test_the_first_message_names_the_conversation_as_its_turn_starts(
    store: ConversationStore,
) -> None:
    conversation = store.create()

    # Atlas's own words name nothing; the user's first words do, once, as the save would.
    assert not store.name_untitled(conversation.id, UserMessage("[IMPORTANT: a command ended]"))
    assert store.name_untitled(conversation.id, UserMessage("Plan the trip\nwith details"))
    assert not store.name_untitled(conversation.id, UserMessage("Something else"))
    conversation.history += [UserMessage("Plan the trip\nwith details")]
    conversation.save()
    assert store.info(conversation.id).title == "Plan the trip"
    # Still the title a model may replace.
    assert store.title_source(conversation.id) is TitleSource.DERIVED
    named = store.create(title="My trip")
    assert not store.name_untitled(named.id, UserMessage("Plan the trip"))
    assert store.info(named.id).title == "My trip"


def _compact(history: list[Message], keep: int) -> AssistantMessage:
    """What the loop does: the leading instructions stay, the middle becomes a summary."""
    summary = AssistantMessage(f"{SUMMARY_MARK}\n\nThey talked about the trip.")
    head = 0
    while head < len(history) and isinstance(history[head], SystemMessage):
        head += 1
    history[:] = [*history[:head], summary, *history[-keep:]]
    return summary


def test_a_compaction_archives_the_replaced_messages_and_keeps_them_in_order(
    store: ConversationStore,
) -> None:
    conversation = store.create()
    conversation.history.append(SystemMessage("rules"))
    for n in range(4):
        conversation.history += [UserMessage(f"question {n}"), AssistantMessage(f"answer {n}")]
    conversation.save()

    summary = _compact(conversation.history, keep=2)
    assert conversation.save() == 1

    reopened = store.open(conversation.id).history
    assert reopened == [
        SystemMessage("rules"),
        summary,
        UserMessage("question 3"),
        AssistantMessage("answer 3"),
    ]
    stored = store.messages(conversation.id)
    assert [m.text[:10] for m in stored] == [
        "question 0", "answer 0", "question 1", "answer 1", "question 2", "answer 2",
        SUMMARY_MARK[:10], "question 3", "answer 3",
    ]  # fmt: skip
    assert [m.active for m in stored] == [False] * 6 + [True] * 3
    assert stored[6].kind is Kind.SUMMARY


def test_archived_messages_stay_searchable(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history += [UserMessage("the password policy"), AssistantMessage("noted")]
    conversation.history += [UserMessage("later"), AssistantMessage("ok")]
    conversation.save()
    _compact(conversation.history, keep=2)
    conversation.save()

    hits = store.search(_query("password policy"), limit=3)

    assert [hit.match.text for hit in hits] == ["the password policy"]
    assert hits[0].match.active is False


def test_saving_after_new_instructions_archives_the_old_ones(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history += [SystemMessage("old rules"), UserMessage("hi")]
    conversation.save()

    conversation.history[0] = SystemMessage("new rules")
    conversation.save()

    assert store.open(conversation.id).history == [SystemMessage("new rules"), UserMessage("hi")]


def test_accents_do_not_matter_to_search(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history += [UserMessage("Qual é a memória do servidor?")]
    conversation.save()

    assert store.search(_query("memoria"), limit=3)
    assert store.search(_query("MEMÓRIA servidor"), limit=3)


def test_search_reads_the_users_and_the_agents_words_not_tool_output(
    store: ConversationStore,
) -> None:
    conversation = store.create()
    conversation.history.extend(EVERY_KIND)
    conversation.save()

    assert store.search(_query("16Gi"), limit=3) == []
    assert store.search(_query("16Gi"), limit=3, roles=(Role.TOOL,))


def test_search_groups_by_conversation_and_shows_the_snippet(store: ConversationStore) -> None:
    first = store.create()
    first.history += [UserMessage("deploy the api"), AssistantMessage("the api deploy is done")]
    first.save()
    second = store.create()
    second.history += [UserMessage("deploy the site")]
    second.save()

    hits = store.search(_query("deploy"), limit=5)

    assert sorted(hit.conversation.id for hit in hits) == sorted([first.id, second.id])
    assert all("»deploy«" in hit.snippet for hit in hits)


def test_search_leaves_out_the_current_conversation_and_hidden_ones(
    store: ConversationStore,
) -> None:
    mine = store.create()
    mine.history.append(UserMessage("the blue whale"))
    mine.save()
    review = store.create(source="review")
    review.history.append(UserMessage("the blue whale"))
    review.save()

    assert store.search(_query("whale"), limit=3, current=mine.id) == []
    assert [h.conversation.id for h in store.search(_query("whale"), limit=3)] == [mine.id]


def test_the_current_conversation_is_searched_where_a_compaction_archived_it(
    store: ConversationStore,
) -> None:
    # An archived message left the model's context, so it is recall like any other.
    current = store.create()
    current.history += [UserMessage("the zebra plan"), AssistantMessage("ok")]
    current.history += [UserMessage("later"), AssistantMessage("fine")]
    current.save()
    _compact(current.history, keep=2)
    current.history.append(UserMessage("zebra again, in context"))
    current.save()

    hits = store.search(_query("zebra"), limit=3, current=current.id)

    assert [hit.match.text for hit in hits] == ["the zebra plan"]


def test_excluded_conversations_and_time_bounds_filter_the_search(
    store: ConversationStore,
) -> None:
    first = store.create()
    first.history.append(UserMessage("budget for march"))
    first.save()
    second = store.create()
    second.history.append(UserMessage("budget for april"))
    second.save()
    started = store.info(second.id).started_at

    assert [
        h.conversation.id for h in store.search(_query("budget"), limit=3, excluded=[first.id])
    ] == [second.id]
    assert store.search(_query("budget"), limit=3, after="2999-01-01T00:00:00.000Z") == []
    assert store.search(_query("budget"), limit=3, before="2000-01-01T00:00:00.000Z") == []
    assert len(store.search(_query("budget"), limit=3, after=started)) >= 1


def test_a_routines_conversations_come_after_the_users(store: ConversationStore) -> None:
    job = store.create(source="routine")
    job.history.append(UserMessage("daily report report report"))
    job.save()
    mine = store.create()
    mine.history.append(UserMessage("the report"))
    mine.save()

    hits = store.search(_query("report"), limit=3)

    assert [hit.conversation.id for hit in hits] == [mine.id, job.id]


def test_a_conversation_is_found_by_its_exact_title(store: ConversationStore) -> None:
    trip = store.create()
    trip.history.append(UserMessage("Plan the trip to Lisbon"))
    trip.save()

    assert store.titled('"Plan the trip to Lisbon"') == store.info(trip.id)
    assert store.titled("Plan the trip") is None
    assert store.titled("Plan the trip to Lisbon", current=trip.id) is None


def test_newest_first_orders_by_when_the_match_was_said(store: ConversationStore) -> None:
    ids: list[str] = []
    for n in range(3):
        conversation = store.create()
        conversation.history.append(UserMessage(f"budget review {n}"))
        conversation.save()
        ids.append(conversation.id)

    newest = store.search(_query("budget"), limit=3, newest_first=True)
    oldest = store.search(_query("budget"), limit=3, newest_first=False)

    assert [h.conversation.id for h in newest] == ids[::-1]
    assert [h.conversation.id for h in oldest] == ids


def test_missing_terms_names_the_words_nothing_contains(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history.append(UserMessage("the budget for march"))
    conversation.save()

    assert store.missing_terms(_query("budget zebra")) == ["zebra"]


def test_around_gives_the_window_in_order_without_instructions(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history.append(SystemMessage("rules"))
    conversation.history += [UserMessage(f"m{n}") for n in range(10)]
    conversation.save()
    stored = store.messages(conversation.id)

    anchor, window = store.around(stored[5].number, 2)

    assert anchor == stored[5]
    assert [m.text for m in window] == ["m3", "m4", "m5", "m6", "m7"]
    assert store.around(stored[0].number, 2)[1][0].text == "m0"
    assert store.around(999_999, 2) == (None, [])


def test_recent_lists_the_users_conversations_latest_first(store: ConversationStore) -> None:
    ids: list[str] = []
    for n in range(3):
        conversation = store.create()
        conversation.history.append(UserMessage(f"topic {n}"))
        conversation.save()
        ids.append(conversation.id)
    store.create()  # nothing said yet: no title, not listed
    hidden = store.create(source="subagent")
    hidden.history.append(UserMessage("work"))
    hidden.save()

    recent = store.recent(10, exclude=ids[0])

    assert [info.id for info in recent] == [ids[2], ids[1]]
    assert recent[0].messages == 1


# -- the list's pages (step 9e) -------------------------------------------------------------------


def _at(minute: int) -> str:
    return f"2026-10-27T10:{minute:02d}:00.000Z"


def _stamp(home: Home, conversation_id: str, at: str) -> None:
    """Give a conversation its last activity, as a save at `at` would."""
    connection = Database(home.database_file).connect()
    try:
        with connection:
            connection.execute(
                "UPDATE conversations_threads SET updated_at = ? WHERE id = ?",
                (at, conversation_id),
            )
    finally:
        connection.close()


def _said(store: ConversationStore, home: Home, minutes: range) -> list[str]:
    """A titled conversation per minute, the oldest first; their ids."""
    ids: list[str] = []
    for minute in minutes:
        conversation = store.create()
        conversation.history.append(UserMessage(f"topic {minute}"))
        conversation.save()
        _stamp(home, conversation.id, _at(minute))
        ids.append(conversation.id)
    return ids


def test_the_list_reads_page_after_page_each_conversation_once_and_in_order(
    store: ConversationStore, home: Home
) -> None:
    ids = _said(store, home, range(7))
    store.create()  # nothing said yet: no title, not listed
    hidden = store.create(source="subagent")
    hidden.history.append(UserMessage("work"))
    hidden.save()
    store.create(source=BOT, bot="finance", title="Finance")

    first, after = store.page(3)
    assert after is not None
    second, after = store.page(3, after)
    assert after is not None
    last, after = store.page(3, after)

    assert after is None
    assert [info.id for info in first + second + last] == ids[::-1]
    assert store.page(7)[1] is None


def test_activity_in_the_middle_neither_repeats_nor_skips_a_conversation(
    store: ConversationStore, home: Home
) -> None:
    ids = _said(store, home, range(6))
    first, cursor = store.page(3)
    assert cursor is not None
    # While the sidebar holds the first page, a conversation of the second gets a message and a
    # new one begins: both go up, above the cursor.
    _stamp(home, ids[1], _at(30))
    [new] = _said(store, home, range(31, 32))

    second, _ = store.page(3, cursor)
    assert [info.id for info in second] == [ids[2], ids[0]]
    # The refresh reads again as many rows as the list holds, and the next page goes on from it.
    held, cursor = store.page(len(first) + len(second))
    assert cursor is not None
    rest, cursor = store.page(3, cursor)

    assert cursor is None
    assert [info.id for info in held + rest] == [
        new,
        ids[1],
        ids[5],
        ids[4],
        ids[3],
        ids[2],
        ids[0],
    ]


def test_a_tie_in_activity_is_broken_by_the_id(store: ConversationStore, home: Home) -> None:
    ids = sorted(_said(store, home, range(3)))
    for conversation_id in ids:
        _stamp(home, conversation_id, _at(5))

    first, cursor = store.page(1)
    assert cursor is not None
    second, cursor = store.page(1, cursor)
    assert cursor is not None
    third, cursor = store.page(1, cursor)

    assert cursor is None
    assert [info.id for info in first + second + third] == ids[::-1]


def test_a_cursor_reads_back_only_what_a_page_gave() -> None:
    cursor = Cursor("2026-10-27T10:00:00.000Z", "20261027-100000-abcdef")

    assert Cursor.read(cursor.text) == cursor
    for text in (
        "",
        "20261027-100000-abcdef",
        "2026-10-27T10:00:00.000Z",
        "2026-10-27T10:00:00.000Z ",
        "2026-10-27 20261027-100000-abcdef",
        "2026-10-27T10:00:00.000Z two words",
    ):
        assert Cursor.read(text) is None, text


def test_an_unknown_conversation_says_so(store: ConversationStore) -> None:
    with pytest.raises(ConversationNotFoundError, match="no conversation"):
        store.open("nope")
    with pytest.raises(ConversationNotFoundError):
        store.messages("nope")


def test_a_damaged_body_says_it_cannot_be_read() -> None:
    assert decode("user", encode(UserMessage("ok"))) == UserMessage("ok")
    with pytest.raises(StoredMessageError, match="cannot be read back"):
        decode("assistant", '{"text": 1}')
    with pytest.raises(StoredMessageError):
        decode("tool", "not json")


# -- a bot's conversations (step 13b) ------------------------------------------------------------


def test_a_bots_fixed_conversation_is_found_by_the_bot_and_not_listed_with_the_users(
    store: ConversationStore,
) -> None:
    mine = store.create()
    mine.history.append(UserMessage("my own conversation"))
    mine.save()
    assert store.fixed("finance") is None

    fixed = store.create(source=BOT, bot="finance", title="Finance")
    fixed.history.append(UserMessage("is the rent paid?"))
    fixed.save()
    other = store.create(source=BOT, bot="work", title="Work")

    found = store.fixed("finance")
    assert found is not None
    assert (found.id, found.bot, found.source, found.title) == (fixed.id, "finance", BOT, "Finance")
    assert store.info(other.id).bot == "work"
    assert store.info(mine.id).bot is None
    # In the bots' list, never among the user's conversations.
    assert [info.id for info in store.recent(10)] == [mine.id]


def test_a_deleted_bots_conversations_are_kept_and_let_go(store: ConversationStore) -> None:
    fixed = store.create(source=BOT, bot="finance", title="Finance")
    fixed.history.append(UserMessage("is the rent paid?"))
    fixed.save()
    child = store.create(source="subagent", parent_id=fixed.id, bot="finance")

    assert store.release("finance") == 2
    assert store.release("finance") == 0

    assert store.fixed("finance") is None
    assert store.info(fixed.id).bot is None
    assert store.info(child.id).bot is None
    assert [m.text for m in store.messages(fixed.id)] == ["is the rent paid?"]
    # A bot made later under the same name starts its own.
    again = store.create(source=BOT, bot="finance")
    found = store.fixed("finance")
    assert found is not None
    assert found.id == again.id


# -- titles ---------------------------------------------------------------------------------------


def test_a_title_replaces_only_one_of_lower_authority(store: ConversationStore) -> None:
    stored = store.create()
    stored.history.append(UserMessage("help me with nginx"))
    stored.save()
    assert store.info(stored.id).title == "help me with nginx"
    assert store.title_source(stored.id) is TitleSource.DERIVED

    assert store.retitle(stored.id, "Configure nginx", TitleSource.LLM)
    # The instant title never comes back over the model's, nor a later save over either.
    assert not store.retitle(stored.id, "help me", TitleSource.DERIVED)
    stored.history.append(UserMessage("and the certificate?"))
    stored.save()
    assert store.info(stored.id).title == "Configure nginx"

    assert store.retitle(stored.id, "My server", TitleSource.USER)
    assert not store.retitle(stored.id, "Nginx and HTTPS", TitleSource.LLM)
    assert not store.retitle(stored.id, "   ", TitleSource.USER)
    assert (store.info(stored.id).title, store.title_source(stored.id)) == (
        "My server",
        TitleSource.USER,
    )
    named = store.create(source=BOT, title="Morning digest")
    assert store.title_source(named.id) is TitleSource.USER
    with pytest.raises(ConversationNotFoundError):
        store.retitle("nope", "x", TitleSource.LLM)


def test_the_migration_keeps_the_names_a_bot_or_a_routine_gave(home: Home) -> None:
    database = Database(home.database_file)
    migrate(database, (Schema(SCHEMA.subject, SCHEMA.migrations[:3]),))
    connection = database.connect()
    try:
        with connection:
            for conversation_id, source in (("a", "app"), ("r", "routine"), ("b", BOT)):
                connection.execute(
                    "INSERT INTO conversations_threads (id, source, title, started_at, updated_at) "
                    "VALUES (?, ?, 'x', '2026-10-14T00:00:00Z', '2026-10-14T00:00:00Z')",
                    (conversation_id, source),
                )
    finally:
        connection.close()
    migrate(database, (SCHEMA,))
    store = ConversationStore(database)
    assert [store.title_source(c) for c in "arb"] == [
        TitleSource.DERIVED,
        TitleSource.USER,
        TitleSource.USER,
    ]
