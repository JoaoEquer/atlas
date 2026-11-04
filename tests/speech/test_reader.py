"""Atlas's own reader of audio (step 26): a recording a message carries is written down before
the turn, once, and kept with it; one that cannot be leaves its note and the turn goes on; one sent
mid-turn is written down before it steers. The conversations run on the assistant against the
fake model server, which is OpenAI's transcription too."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.assistant import Assistant
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import CallLedger, UserMessage
from atlas.plugins import LoadedReader
from atlas.secrets import Vault
from atlas.speech import OPENAI, ModelStore, Speech
from fake_model import FakeError, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
#: A voice note's first bytes, as Telegram sends one: an Ogg page.
VOICE = b"OggS\x00\x02" + b"\x00" * 64
SAID = "Remind me to call the dentist on Thursday."
#: What Atlas's own system prompt holds, and a title's does not.
ATLAS = "You are Atlas"


def _assistant(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    key: str | None = "fake-key",
) -> Assistant:
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    models = connect(
        fake, "openrouter", settings=chosen, ledger=CallLedger(Database(home.database_file))
    )
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    assistant = Assistant(home, settings, models, vault, listener=lambda _id, _event: None)
    speech = Speech(
        ModelStore(tmp_path / "speech"),
        lambda: "openai",
        lambda _name: key,
        openai=replace(OPENAI, api_url=fake.base_url),
    )
    assistant.use_reader(LoadedReader("atlas", "audio", speech.transcribe))
    return assistant


def _said(body: dict[str, Any]) -> str:
    content = body["messages"][-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


def test_an_audio_file_reaches_the_model_written_down_once(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.transcribe(SAID)
    fake.reply(
        FakeReply(text="I will remind you.", when=ATLAS),
        FakeReply(text="Thursday, yes.", when=ATLAS),
    )

    async def scenario() -> str:
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        conversation.send("Which day was it?")
        await conversation.wait()
        await assistant.close()
        return conversation.id

    conversation_id = asyncio.run(scenario())

    (heard,) = fake.heard
    assert (heard["file_name"], heard["file_type"], heard["file_bytes"]) == (
        "voice.ogg",
        "audio/ogg",
        VOICE,
    )
    first, second = fake.bodies(CHAT)
    assert SAID in _said(first)
    assert "may have misheard" in _said(first)
    assert SAID in json.dumps(second["messages"]), "the next call reads it again, unheard"
    history = assistant.conversations.open(conversation_id).history
    user = next(message for message in history if isinstance(message, UserMessage))
    assert user.attachments[0].description == SAID, "kept with the file"


@pytest.mark.parametrize(
    ("key", "answer"),
    [(None, None), ("fake-key", FakeError(500, "The transcription service is down."))],
    ids=["no key in the vault", "OpenAI fails"],
)
def test_audio_not_written_down_keeps_its_note_and_the_turn_goes_on(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    key: str | None,
    answer: FakeError | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path, key)
    if answer is not None:
        fake.transcribe(answer)
    fake.reply(FakeReply(text="I could not hear it.", when=ATLAS))

    async def scenario() -> None:
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        await assistant.close()

    asyncio.run(scenario())

    (turn,) = fake.bodies(CHAT)
    assert "an audio file attachment: 'voice.ogg'" in _said(turn), "the note, as before"
    expected = "no OPENAI_API_KEY" if key is None else "HTTP 500"
    assert any(
        "atlas could not read voice.ogg" in record.getMessage() and expected in record.getMessage()
        for record in caplog.records
    )


def test_audio_sent_while_a_turn_runs_is_written_down_before_it_steers(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.transcribe(SAID)
    fake.reply(
        FakeReply(text="Working on the first thing.", pause=0.05, when=ATLAS),
        FakeReply(text="Noted.", when=ATLAS),
    )

    async def scenario() -> None:
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Plan my week.")
        assert conversation.running
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        await assistant.close()

    asyncio.run(scenario())

    assert len(fake.heard) == 1
    bodies = fake.bodies(CHAT)
    assert SAID not in _said(bodies[0])
    assert any(SAID in _said(body) for body in bodies[1:]), "it reached the model, written down"
