"""Conversations put into a home before the app opens, for the end-to-end tests of the list of
conversations (step 9e): each a question and its answer, a minute apart, the last one the newest.

    python tests/seed_conversations.py <home> <conversations.json>

The file holds `[{"said": ..., "answer": ...}, ...]`, oldest first; each conversation is titled by
what was said, as Atlas titles one. The database is migrated first, as `atlas serve` would.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

from atlas.__main__ import SCHEMAS
from atlas.conversations import ConversationStore
from atlas.home import Database, format_utc, migrate, transaction, utc_now
from atlas.models import AssistantMessage, UserMessage


def seed(home: Path, conversations: list[dict[str, str]]) -> None:
    database = Database(home / "atlas.db")
    migrate(database, SCHEMAS)
    store = ConversationStore(database)
    start = utc_now() - timedelta(minutes=len(conversations) + 1)
    for index, spec in enumerate(conversations):
        conversation = store.create()
        conversation.history.extend([UserMessage(spec["said"]), AssistantMessage(spec["answer"])])
        conversation.save()
        # A minute apart, so the list's order is the file's, newest last.
        stamp = format_utc(start + timedelta(minutes=index))
        connection = database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "UPDATE conversations_threads SET started_at = ?, updated_at = ? WHERE id = ?",
                    (stamp, stamp, conversation.id),
                )
        finally:
            connection.close()


if __name__ == "__main__":
    seed(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
