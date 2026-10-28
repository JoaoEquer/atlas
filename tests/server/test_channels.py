"""Channels end to end: the Telegram plugin Atlas ships, turned on in a real server, talking to a
fake Telegram over HTTP, with the fake model answering. Who may talk, a turn, an approval by
button, a question answered by typing, the commands, and the Plugins screen's methods.
"""

import json
import time
from collections.abc import Callable, Iterator
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import main
from atlas.home import Database, Home
from atlas.plugins import Plugins
from atlas.secrets import Vault
from claude_code_support import claude_code
from fake_model import SPOKEN, FakeCall, FakeModelServer, FakeReply
from fake_telegram import TOKEN, FakeTelegram
from server_support import Client, Serve

ANA = 100
BIA = 200
STRANGER = 300
CHAT = "/v1/chat/completions"
#: A PNG's first bytes: what Atlas reads an image by, whatever Telegram names it.
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def telegram() -> Iterator[FakeTelegram]:
    with FakeTelegram() as server:
        yield server


def _until[T](found: Callable[[], T], seconds: float = 10) -> T:
    deadline = time.monotonic() + seconds
    while not (value := found()):
        if time.monotonic() > deadline:
            raise AssertionError("It did not happen in time.")
        time.sleep(0.02)
    return value


def _telegram(client: Client) -> dict[str, Any]:
    """Telegram's row on the Plugins screen, among the plugins Atlas ships."""
    rows: list[dict[str, Any]] = client.result("plugin.list")["plugins"]
    return next(row for row in rows if row["name"] == "telegram")


def _turn_on(home: Home, vault: Vault, telegram: FakeTelegram, token: str = TOKEN) -> Plugins:
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("telegram", "api_url", telegram.url)
    plugins.set_secret("telegram", "TELEGRAM_BOT_TOKEN", token)
    manifest = plugins.view("telegram").manifest
    assert manifest is not None
    plugins.enable("telegram", manifest.fingerprint())
    return plugins


def _paired(client: Client, telegram: FakeTelegram, user: int = ANA) -> None:
    """`user` writes, gets a code, and the user approves it on the Plugins screen."""
    telegram.say(user, "hi")
    said = _until(lambda: [t for t in telegram.sent(user) if "pairing code" in t])
    waiting = client.result("pairing.list")["pairings"]
    [row] = [p for p in waiting if p["user"] == str(user)]
    assert row["code"] in said[0]
    client.result("pairing.approve", {"code": row["code"]})


def _manual(client: Client, telegram: FakeTelegram, user: int = ANA) -> None:
    """`user`'s chat gets its conversation, which starts in "Auto", and the app turns it off."""
    before = len(telegram.sent(user))
    telegram.say(user, "hello")
    _until(lambda: len(telegram.sent(user)) > before)
    [row] = client.result("conversation.list")["conversations"]
    client.result("conversation.auto_mode", {"conversation_id": row["id"], "enabled": False})


def test_a_stranger_gets_a_code_and_once_approved_talks_to_atlas(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    _turn_on(home, vault, telegram)
    fake.reply(FakeReply(text="Hello, **Ana**!", when="You are Atlas"))
    with serve().client() as client:
        _until(lambda: telegram.called("getUpdates"))
        telegram.say(ANA, "hi", name="Ana")
        _until(lambda: telegram.sent(ANA))
        assert client.event("plugins.changed")["payload"] == {"waiting": 1}
        [code_text] = telegram.sent(ANA)
        assert code_text.startswith("Atlas does not know you yet. Your pairing code is ")
        assert fake.requests == []  # the stranger's message reached no model

        [waiting] = client.result("pairing.list")["pairings"]
        assert (waiting["channel"], waiting["user"], waiting["name"]) == ("telegram", "100", "Ana")
        assert waiting["approved"] is False
        approved = client.result("pairing.approve", {"code": waiting["code"]})
        assert approved["approved"] is True

        telegram.say(ANA, "hello")
        _until(lambda: len(telegram.sent(ANA)) == 2)
        listed = client.result("conversation.list")["conversations"]
        auto = client.result("conversation.auto_mode", {"conversation_id": listed[0]["id"]})

    assert telegram.sent(ANA)[-1] == "Hello, <b>Ana</b>!"
    assert telegram.called("sendChatAction")
    [row] = listed
    assert (row["source"], row["title"]) == ("telegram", "hello")
    assert auto == {"enabled": True}, "a chat's new conversation starts in Auto"


def test_the_interim_texts_come_in_order_before_the_answer_and_no_reasoning(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    # Step 9c: each interim text as a message of its own, the typing notice held until the end.
    _turn_on(home, vault, telegram)
    look = '{"path": "notes.md"}'
    fake.reply(
        FakeReply(
            reasoning="The secret plan.",
            text="Looking at the notes.",
            calls=[FakeCall("read_file", look, id="r1")],
            when="You are Atlas",
        ),
        FakeReply(text="One more look.", calls=[FakeCall("read_file", look, id="r2")]),
        FakeReply(calls=[FakeCall("read_file", look, id="r3")]),  # no text, nothing sent
        FakeReply(text="There are no notes."),
    )
    with serve().client() as client:
        _paired(client, telegram)
        before = len(telegram.sent(ANA))
        typing = len(telegram.called("sendChatAction"))
        telegram.say(ANA, "what is in my notes?")
        _until(lambda: "There are no notes." in telegram.sent(ANA))

    assert telegram.sent(ANA)[before:] == [
        "Looking at the notes.",
        "One more look.",
        "There are no notes.",
    ]
    # The turn's start, and again after each interim message.
    assert len(telegram.called("sendChatAction")) - typing >= 3


def test_an_approval_is_answered_by_button_and_only_by_someone_let_in(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    _turn_on(home, vault, telegram)
    fake.reply(
        FakeReply(text="Hello."),
        FakeReply(calls=[FakeCall("run_command", '{"command": "git reset --hard"}', id="c1")]),
        FakeReply(text="ESCALATE"),
        FakeReply(text="Reset done."),
    )
    with serve().client() as client:
        _paired(client, telegram)
        _manual(client, telegram)
        telegram.say(ANA, "reset the repo")
        [buttons] = _until(lambda: telegram.buttons(ANA))
        assert [b.rsplit(":", 1)[1] for b in buttons] == ["0", "1", "2", "3"]
        asked = telegram.sent(ANA)[-1]
        assert "Atlas asks to use run_command" in asked
        assert "git reset --hard" in asked

        telegram.press(STRANGER, buttons[0])
        _until(lambda: telegram.called("answerCallbackQuery"))
        assert telegram.called("answerCallbackQuery")[0]["text"] == (
            "Only people this Atlas lets in can answer."
        )
        telegram.press(ANA, buttons[0])
        _until(lambda: "Reset done." in telegram.sent(ANA))
        telegram.press(ANA, buttons[3])  # too late: it was answered
        _until(lambda: len(telegram.called("answerCallbackQuery")) == 3)

    answers = [a["text"] for a in telegram.called("answerCallbackQuery")]
    assert answers[1:] == ["", "This no longer waits for an answer."]
    [edit] = telegram.called("editMessageText")
    assert edit["text"].endswith("You chose: Allow once.")


def test_a_question_is_answered_by_typing_part_by_part(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    _turn_on(home, vault, telegram)
    questions = {
        "questions": [
            {"question": "Which city?", "choices": ["Lisbon", "Porto"]},
            {"question": "How many days?"},
        ]
    }
    fake.reply(
        FakeReply(calls=[FakeCall("ask_user", json.dumps(questions), id="a1")]),
        FakeReply(text="Porto for 3 days, then."),
    )
    with serve().client() as client:
        _paired(client, telegram)
        telegram.say(ANA, "plan a trip")
        [first] = _until(lambda: telegram.buttons(ANA))
        telegram.press(ANA, first[1])  # Porto
        _until(lambda: "How many days?" in telegram.sent(ANA)[-1])
        telegram.say(ANA, "3")
        _until(lambda: telegram.sent(ANA)[-1] == "Porto for 3 days, then.")

    results = [
        m["content"]
        for m in fake.bodies("/v1/chat/completions")[-1]["messages"]
        if m["role"] == "tool"
    ]
    assert "Porto" in results[0]
    assert "3" in results[0]


def test_what_claude_code_asks_for_a_delegation_from_telegram_is_answered_there_or_in_the_app(
    serve: Serve,
    fake: FakeModelServer,
    telegram: FakeTelegram,
    home: Home,
    vault: Vault,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decided with the user on 17/10/2026: what Claude Code asks for a delegation the chat's
    conversation made reaches the chat as Atlas's own approvals do, and the first answer, there or
    on the app's card, takes it (Claude Code's own permission relay)."""
    runner = [
        {"question": "Which runner?", "header": "Runner", "options": [{"label": "Jest"},
         {"label": "Vitest"}], "multiSelect": False},
    ]  # fmt: skip
    script = tmp_path / "claude-script.json"
    steps = [
        {"tool": "Bash", "input": {"command": "pytest -x"}},
        {"questions": runner},
        {"result": "Green on Vitest."},
    ]
    script.write_text(json.dumps(steps), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    _turn_on(home, vault, telegram)
    arguments = {
        "executor": "claude-code",
        "tasks": [{"goal": "Run the tests.", "folder": "."}],
        "approval": "manual",
    }
    fake.reply(
        FakeReply(calls=[FakeCall("delegate", json.dumps(arguments))], when="Skills come"),
        FakeReply(text="Claude Code runs them.", when="Skills come"),
        FakeReply(text="All green on Vitest.", when="Skills come"),
    )
    with serve(executors={"claude-code": claude_code()}).client() as client:
        _paired(client, telegram)
        telegram.say(ANA, "run my tests with Claude Code")
        [allow_deny] = _until(lambda: telegram.buttons(ANA))
        asked = telegram.sent(ANA)[-1]
        assert "Claude Code asks to use Bash:" in asked
        assert "pytest -x" in asked
        telegram.press(ANA, allow_deny[0])
        _until(lambda: len(telegram.buttons(ANA)) == 2)
        assert "Claude Code asks (Runner): Which runner?" in telegram.sent(ANA)[-1]
        [request] = _until(lambda: client.result("workbench.list")["open_requests"])
        assert request["method"] == "executor.question", "the app's card has it too"
        client.answer(request["id"], {"answers": {"Which runner?": "Vitest"}})
        _until(lambda: "All green on Vitest." in telegram.sent(ANA), seconds=20)

    assert len(allow_deny) == 2
    edits = [str(e["text"]) for e in telegram.called("editMessageText")]
    assert edits[0].endswith("You chose: Allow.")
    assert edits[1].endswith("Answered."), "answered in the app, it is settled in the chat"


#: What Claude Code asks in the user's own session: a permission, then a question.
_SESSION_STEPS = [
    {"tool": "Bash", "input": {"command": "npm test"}},
    {
        "questions": [
            {"question": "Which runner?", "header": "Runner", "options": [{"label": "Jest"},
             {"label": "Vitest"}], "multiSelect": False},
        ]
    },
    {"result": "Green on Vitest."},
]  # fmt: skip


def _settled(telegram: FakeTelegram) -> set[tuple[str, str]]:
    """Each chat's prompts as they were settled: the chat, and the words that replaced the
    buttons."""
    return {
        (str(edit["chat_id"]), str(edit["text"]).rsplit("\n\n", 1)[-1])
        for edit in telegram.called("editMessageText")
    }


def test_what_a_session_of_the_users_asks_reaches_everyone_let_in_the_first_answer_taking_it(
    serve: Serve,
    telegram: FakeTelegram,
    home: Home,
    vault: Vault,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked for by the user on 18/10/2026: a session the user started in the app belongs to no
    chat, so what its Claude Code asks reaches the chat of everyone let in, as Claude Code's own
    Telegram relays a permission to every paired sender, naming the session's folder. The first
    answer, in a chat or in the app, takes it, and the other chat is settled."""
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps(_SESSION_STEPS), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    _turn_on(home, vault, telegram)
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        _paired(client, telegram, ANA)
        _paired(client, telegram, BIA)
        card = client.result(
            "workbench.start", {"executor": "claude-code", "folder": ".", "auto": False}
        )
        client.result("workbench.prompt", {"instance_id": card["instance_id"], "text": "Test."})
        [ana_allow] = _until(lambda: telegram.buttons(ANA))
        [bia_allow] = _until(lambda: telegram.buttons(BIA))
        assert "Claude Code (work) asks to use Bash:\nnpm test" in telegram.sent(ANA)[-1]
        assert "Claude Code (work) asks to use Bash:\nnpm test" in telegram.sent(BIA)[-1]
        assert ana_allow != bia_allow, "each chat's prompt is its own"
        [request] = client.result("workbench.list")["open_requests"]
        assert request["method"] == "executor.approval", "the app has it too"

        telegram.press(BIA, bia_allow[0])
        _until(lambda: len(telegram.buttons(ANA)) == 2 and len(telegram.buttons(BIA)) == 2)
        assert telegram.sent(ANA)[-1].startswith("Claude Code (work) asks (Runner): Which runner?")
        telegram.say(ANA, "Vitest")
        _until(lambda: client.result("workbench.list")["instances"][0]["state"] == "idle")

    assert _settled(telegram) == {
        (str(BIA), "You chose: Allow."),
        (str(ANA), "Answered."),
        (str(ANA), "You answered: Vitest"),
        (str(BIA), "Answered."),
    }


def test_what_waits_when_the_channel_starts_reaches_it_then(
    serve: Serve,
    telegram: FakeTelegram,
    home: Home,
    vault: Vault,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outside executor's request waits with no deadline: asked while the channel was off,
    it reaches the chat when the channel starts, and is answered there."""
    script = tmp_path / "claude-script.json"
    steps = [{"tool": "Bash", "input": {"command": "make"}}, {"result": "Made."}]
    script.write_text(json.dumps(steps), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("telegram", "api_url", telegram.url)
    plugins.set_secret("telegram", "TELEGRAM_BOT_TOKEN", TOKEN)
    plugins.approve(str(plugins.admit("telegram", str(ANA), "Ana", str(ANA)).code))
    with serve(executors={"claude-code": claude_code()}).client() as client:
        card = client.result(
            "workbench.start", {"executor": "claude-code", "folder": ".", "auto": False}
        )
        client.result("workbench.prompt", {"instance_id": card["instance_id"], "text": "Make."})
        _until(lambda: client.result("workbench.list")["open_requests"])
        assert telegram.sent(ANA) == [], "the channel is off"

        client.result(
            "plugin.enable", {"name": "telegram", "consent": _telegram(client)["consent"]}
        )
        [allow_deny] = _until(lambda: telegram.buttons(ANA))
        telegram.press(ANA, allow_deny[0])
        _until(lambda: client.result("workbench.list")["instances"][0]["state"] == "idle")

    assert _settled(telegram) == {(str(ANA), "You chose: Allow.")}


def test_new_starts_another_conversation_and_stop_says_when_nothing_runs(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    _turn_on(home, vault, telegram)
    fake.reply(
        FakeReply(text="One.", when="You are Atlas"), FakeReply(text="Two.", when="You are Atlas")
    )
    with serve().client() as client:
        _paired(client, telegram)
        telegram.say(ANA, "/start")
        telegram.say(ANA, "first")
        _until(lambda: "One." in telegram.sent(ANA))
        telegram.say(ANA, "/stop")
        telegram.say(ANA, "/new")
        telegram.say(ANA, "second")
        _until(lambda: "Two." in telegram.sent(ANA))
        listed = client.result("conversation.list")["conversations"]

    sent = telegram.sent(ANA)
    assert "Atlas here. Write what you need." in sent
    assert "Nothing is running." in sent
    assert "A new conversation starts with your next message." in sent
    assert sorted(row["title"] for row in listed) == ["first", "second"]


def test_a_plugin_of_skills_turned_on_from_the_screen_brings_them_and_runs(
    serve: Serve, home: Home
) -> None:
    """Step 16j: a plugin that brings only skills runs once consented to, its skills listed for
    the conversations, and leaves with them when it is turned off."""
    folder = home.plugins_folder / "shouting"
    (folder / ".claude-plugin").mkdir(parents=True)
    (folder / ".claude-plugin" / "plugin.json").write_text('{"name": "shouting"}', "utf-8")
    (folder / "skills" / "shout").mkdir(parents=True)
    (folder / "skills" / "shout" / "SKILL.md").write_text(
        "---\nname: shout\ndescription: Say it loud.\n---\nCapitals.\n", "utf-8"
    )
    server = serve()
    library = server.gateway.assistant._shared.library  # pyright: ignore[reportPrivateUsage]
    with server.client() as client:
        [row] = [r for r in client.result("plugin.list")["plugins"] if r["name"] == "shouting"]
        assert "Adds the skills shouting:shout" in " ".join(row["declaration"])
        on = client.result("plugin.enable", {"name": "shouting", "consent": row["consent"]})
        assert (on["enabled"], on["running"], on["problem"]) == (True, True, None)
        assert library.catalog.get("shouting:shout") is not None
        off = client.result("plugin.disable", {"name": "shouting"})
        assert off["running"] is False
        assert library.catalog.get("shouting:shout") is None


def test_the_plugins_screen_turns_it_on_with_consent_and_shows_why_it_stopped(
    serve: Serve, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    with serve().client() as client:
        row = _telegram(client)
        assert (row["name"], row["enabled"], row["running"], row["bundled"]) == (
            "telegram",
            False,
            False,
            True,
        )
        assert row["secrets"] == [
            {
                "name": "TELEGRAM_BOT_TOKEN",
                "description": row["secrets"][0]["description"],
                "url": "https://t.me/BotFather",
                "stored": False,
            }
        ]
        assert any("no sandbox" in line for line in row["declaration"])
        # A setting with no choices is typed; the voice's are picked from lists (SDK 25).
        assert all(s["choices"] == [] and s["choices_from"] == "" for s in row["settings"])
        voice = next(r for r in client.result("plugin.list")["plugins"] if r["name"] == "voice")
        listed = {s["name"]: (s["choices"], s["choices_from"]) for s in voice["settings"]}
        assert listed["provider"] == (["openai", "gemini"], "")
        assert listed["transcription_model"] == ([], "plugin")
        assert listed["dictation_shortcut"] == ([], "")

        refused = client.call("plugin.enable", {"name": "telegram", "consent": "stale"})
        assert "other than what was shown" in refused["error"]["message"]
        client.result(
            "plugin.setting", {"name": "telegram", "setting": "api_url", "value": telegram.url}
        )

        # On without a token: it stops, saying what to do.
        on = client.result("plugin.enable", {"name": "telegram", "consent": row["consent"]})
        assert on["enabled"] is True
        stopped = _until(lambda: (r := _telegram(client))["problem"] and r)
        assert "@BotFather" in stopped["problem"]
        assert stopped["running"] is False

        # The token stored: it starts again by itself.
        stored = client.result(
            "plugin.secret", {"name": "telegram", "secret": "TELEGRAM_BOT_TOKEN", "value": TOKEN}
        )
        assert stored["secrets"][0]["stored"] is True
        running = _until(lambda: (r := _telegram(client))["running"] and r)
        assert running["problem"] is None
        _until(lambda: telegram.called("getUpdates"))

        off = client.result("plugin.disable", {"name": "telegram"})
        assert (off["enabled"], off["running"]) == (False, False)
        missing = client.call("plugin.disable", {"name": "nope"})
        assert missing["error"]["code"] == -32004 or "no plugin" in missing["error"]["message"]


def test_a_conversation_started_in_the_app_answers_in_telegram_too(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    _turn_on(home, vault, telegram)
    fake.reply(
        FakeReply(text="From the phone.", when="You are Atlas"),
        FakeReply(text="From the app.", when="You are Atlas"),
    )
    with serve().client() as client:
        _paired(client, telegram)
        telegram.say(ANA, "on the phone")
        _until(lambda: "From the phone." in telegram.sent(ANA))
        [row] = client.result("conversation.list")["conversations"]
        client.result("conversation.send", {"conversation_id": row["id"], "text": "at the desk"})
        client.event("message.complete")
        _until(lambda: "From the app." in telegram.sent(ANA))


def test_the_terminal_turns_it_on_and_approves(
    home: Home, vault: Vault, telegram: FakeTelegram
) -> None:
    environ = {"ATLAS_HOME": str(home.root)}
    assert main(["plugins", "set", "telegram", "api_url", telegram.url], environ) == 0
    assert (
        main(["plugins", "secret", "telegram", "TELEGRAM_BOT_TOKEN"], environ, StringIO(TOKEN)) == 0
    )
    assert main(["plugins", "enable", "telegram"], environ, StringIO("")) == 1  # asks first
    assert main(["plugins", "enable", "telegram", "--yes"], environ) == 0
    assert main(["plugins", "list"], environ) == 0
    on = Plugins(home, Database(home.database_file), vault).view("telegram")
    assert (on.enabled, on.consented) == (True, True)
    code = Plugins(home, Database(home.database_file), vault).admit("telegram", "7", "Bia").code
    assert main(["pairing", "approve", str(code)], environ) == 0
    assert main(["pairing", "list"], environ) == 0
    assert main(["pairing", "forget", "telegram", "7"], environ) == 0
    assert main(["pairing", "forget", "telegram", "7"], environ) == 1
    assert main(["plugins", "disable", "telegram"], environ) == 0


def test_a_photo_and_a_document_from_telegram_reach_the_model_as_a_dropped_file_does(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    """Step 16g through SDK version 7: the plugin downloads, Atlas keeps the files in the home and
    builds what the model reads, as for the app; a caption is the message's text."""
    _turn_on(home, vault, telegram)
    png = PNG + b"\x00" * 64
    pdf = b"%PDF-1.7\n" + b"x" * 300
    fake.reply(
        FakeReply(text="Hi.", when="You are Atlas"),
        FakeReply(text="A chart.", when="You are Atlas"),
        FakeReply(text="A contract.", when="You are Atlas"),
    )
    with serve().client() as client:
        _paired(client, telegram)
        telegram.say(ANA, "hello")
        _until(lambda: "Hi." in telegram.sent(ANA))
        telegram.send_photo(ANA, png, caption="Compare them")
        _until(lambda: "A chart." in telegram.sent(ANA))
        telegram.send_file(ANA, "document", pdf, name="contrato.pdf")
        _until(lambda: "A contract." in telegram.sent(ANA))
        [row] = client.result("conversation.list")["conversations"]
        opened = client.result("conversation.open", {"conversation_id": row["id"]})

    photo_turn = next(b for b in fake.bodies(CHAT) if "Compare them" in json.dumps(b))
    said = photo_turn["messages"][-1]["content"]
    assert [part["type"] for part in said] == ["text", "image_url"]
    assert said[0]["text"].endswith("Compare them")
    users = [m for m in opened["messages"] if m["role"] == "user"]
    assert [[a["name"] for a in m["attachments"]] for m in users][-2:] == [
        ["photo.jpg"],
        ["contrato.pdf"],
    ]
    assert users[-2]["attachments"][0]["kind"] == "image"
    assert users[-1]["attachments"][0]["kind"] == "document"
    stored = home.attachments_folder / users[-1]["attachments"][0]["id"] / "contrato.pdf"
    assert stored.read_bytes() == pdf


def test_a_voice_message_from_telegram_reaches_the_model_written_down(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    """Step 17a: with the voice plugin on, the reader writes the voice note down before the turn,
    through the same piece as a file dropped in the app; the fake model server is OpenAI's
    transcription too."""
    plugins = _turn_on(home, vault, telegram)
    plugins.set_setting("voice", "api_url", fake.base_url)
    plugins.set_secret("voice", "OPENAI_API_KEY", "fake-key")
    manifest = plugins.view("voice").manifest
    assert manifest is not None
    fake.transcribe("Book a table for two at eight.")
    fake.reply(
        FakeReply(text="Hi.", when="You are Atlas"),
        FakeReply(text="Booked for eight.", when="You are Atlas"),
    )
    # OpenAI too on the fake: the key stored for the plugin is the title model's as well.
    with serve(providers=("openai",)).client() as client:
        # Turned on as the Plugins screen does, which answers once the plugins are loaded.
        client.result("plugin.enable", {"name": "voice", "consent": manifest.fingerprint()})
        _paired(client, telegram)
        telegram.say(ANA, "hello")
        _until(lambda: "Hi." in telegram.sent(ANA))
        telegram.send_file(ANA, "voice", b"OggS" + b"\x00" * 64)
        _until(lambda: "Booked for eight." in telegram.sent(ANA))

    [heard] = fake.heard
    assert (heard["file_name"], heard["model"]) == ("voice.ogg", "gpt-transcribe")
    voice_turn = next(b for b in fake.bodies(CHAT) if "voice.ogg" in json.dumps(b))
    assert "Book a table for two at eight." in json.dumps(voice_turn["messages"][-1])


def test_an_answer_asked_for_in_audio_reaches_telegram_as_a_voice_message(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    """Step 17a: the voice plugin's tool speaks the answer and sends it (SDK version 22), and the
    Telegram plugin sends it as Telegram's voice message, which plays in the chat."""
    plugins = _turn_on(home, vault, telegram)
    plugins.set_setting("voice", "api_url", fake.base_url)
    plugins.set_secret("voice", "OPENAI_API_KEY", "fake-key")
    manifest = plugins.view("voice").manifest
    assert manifest is not None
    speak = json.dumps({"text": "It is sunny."})
    fake.reply(
        FakeReply(text="Hi.", when="You are Atlas"),
        FakeReply(
            calls=[FakeCall("tool_search", '{"query": "speak"}', id="s1")], when="You are Atlas"
        ),
        FakeReply(calls=[FakeCall("voice_speak", speak, id="c1")], when="You are Atlas"),
        FakeReply(text="Sent you an audio.", when="You are Atlas"),
    )
    with serve(providers=("openai",)).client() as client:
        client.result("plugin.enable", {"name": "voice", "consent": manifest.fingerprint()})
        _paired(client, telegram)
        telegram.say(ANA, "hello")
        _until(lambda: "Hi." in telegram.sent(ANA))
        telegram.say(ANA, "Tell me the weather in audio")
        _until(lambda: "Sent you an audio." in telegram.sent(ANA))

    [voice] = telegram.called("sendVoice")
    assert (voice["chat_id"], voice["voice_type"], voice["voice_bytes"]) == (
        str(ANA),
        "audio/ogg",
        SPOKEN,
    )
    assert str(voice["voice_name"]).endswith(".ogg")


def test_an_answer_asked_for_in_audio_in_the_app_comes_as_a_file_of_the_turn(
    serve: Serve, fake: FakeModelServer, home: Home, vault: Vault
) -> None:
    """Step 17a: in the app, the spoken answer is an event of the turn (`message.file`), its row
    the attachment's, kept in the home."""
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("voice", "api_url", fake.base_url)
    plugins.set_secret("voice", "OPENAI_API_KEY", "fake-key")
    manifest = plugins.view("voice").manifest
    assert manifest is not None
    fake.reply(
        FakeReply(
            calls=[FakeCall("tool_search", '{"query": "speak"}', id="s1")], when="You are Atlas"
        ),
        FakeReply(
            calls=[FakeCall("voice_speak", '{"text": "Hi."}', id="c1")], when="You are Atlas"
        ),
        FakeReply(text="Sent.", when="You are Atlas"),
    )
    with serve(providers=("openai",)).client() as client:
        client.result("plugin.enable", {"name": "voice", "consent": manifest.fingerprint()})
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": "Say hi"})
        sent = client.event("message.file")
        client.event("message.complete")

    row = sent["payload"]
    assert (sent["conversation_id"], row["kind"], row["media_type"]) == (
        conversation,
        "audio",
        "audio/ogg",
    )
    assert (home.attachments_folder / row["id"] / row["name"]).read_bytes() == SPOKEN
