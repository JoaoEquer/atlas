"""The voice plugin (step 17a): a plugin's reader (SDK version 21) and the voice plugin Atlas
ships, which writes down what an audio file says through OpenAI's transcription or Gemini's; the
answer spoken when the user asks, sent as a file (SDK version 22); the dictation; the live
conversation's server side, its session and the note each spoken turn carries; and the lists its
settings are picked from (SDK version 25), asked of the provider.

The voice plugin is the shipped folder, loaded as Atlas loads it, and it reaches the fake model
server over real HTTP, as it would reach OpenAI; the conversations run on the assistant against the
same server, so what reaches the model is what a provider would get.
"""

import asyncio
import base64
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.assistant import Assistant, ConversationEvent, FileSent
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.hooks import Hooks
from atlas.models import TOOL_SEARCH, AttachmentKind, CallLedger, UserMessage
from atlas.plugins import BUNDLED_FOLDER, ManifestError, Plugins
from atlas.plugins.bundled.voice.gemini import INLINE_BYTES
from atlas.plugins.model import parse_manifest
from atlas.secrets import Vault
from fake_model import (
    LIVE_ANSWER,
    LIVE_TOKEN,
    SPOKEN,
    SPOKEN_WAV,
    FakeCall,
    FakeError,
    FakeModelServer,
    FakeReply,
)
from model_support import Connect

CHAT = "/v1/chat/completions"
#: A voice note's first bytes, as Telegram sends one: an Ogg page.
VOICE = b"OggS\x00\x02" + b"\x00" * 64
SAID = "Remind me to call the dentist on Thursday."
#: What Atlas's own system prompt holds, and a title's does not.
ATLAS = "You are Atlas"


@pytest.fixture
def plugins(home: Home, vault: Vault) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, bundled_folder=BUNDLED_FOLDER)


def _voice_on(
    plugins: Plugins, fake: FakeModelServer, key: str | None = "fake-key", provider: str = "openai"
) -> None:
    plugins.set_setting("voice", "provider", provider)
    gemini = provider == "gemini"
    plugins.set_setting("voice", "api_url", fake.gemini_url if gemini else fake.base_url)
    if key is not None:
        plugins.set_secret("voice", "GEMINI_API_KEY" if gemini else "OPENAI_API_KEY", key)
    manifest = plugins.view("voice").manifest
    assert manifest is not None
    plugins.enable("voice", manifest.fingerprint())


def _assistant(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    heard: list[tuple[str, ConversationEvent]] | None = None,
) -> Assistant:
    chosen = ModelSettings(provider="openrouter")
    # OpenAI on the fake too: the key the plugin reads is the title model's as well.
    models = connect(
        fake,
        "openrouter",
        "openai",
        settings=chosen,
        ledger=CallLedger(Database(home.database_file)),
    )
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))

    def listener(conversation: str, event: ConversationEvent) -> None:
        if heard is not None:
            heard.append((conversation, event))

    return Assistant(home, settings, models, vault, listener=listener)


def _said(body: dict[str, Any]) -> str:
    content = body["messages"][-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


# -- the manifest ----------------------------------------------------------------------------------


def test_a_reader_is_declared_and_shown_at_consent() -> None:
    manifest = parse_manifest(
        json.dumps({"name": "ears", "atlas": {"api": 21, "readers": ["audio"]}}),
        {"__init__.py"},
        folder_name="ears",
    )
    assert manifest.readers == ("audio",)
    assert any("Reads the audio files you send" in line for line in manifest.declaration())
    without = parse_manifest(
        json.dumps({"name": "ears", "atlas": {"api": 21}}), {"__init__.py"}, folder_name="ears"
    )
    assert manifest.fingerprint() != without.fingerprint(), "a new reader asks consent again"


def test_desktop_powers_are_declared_and_shown_at_consent() -> None:
    def parsed(atlas: dict[str, Any]) -> Any:
        return parse_manifest(
            json.dumps({"name": "keys", "atlas": atlas}), set(), folder_name="keys", screen=True
        )

    manifest = parsed({"api": 23, "desktop": ["shortcut", "paste"]})
    assert manifest.desktop == ("shortcut", "paste")
    shown = manifest.declaration()
    assert any("listens for a key you choose in every program" in line for line in shown)
    assert any("presses Ctrl+V for you" in line for line in shown)
    assert manifest.fingerprint() != parsed({"api": 23}).fingerprint(), "asks consent again"


@pytest.mark.parametrize(
    ("atlas", "screen", "problem"),
    [
        ({"api": 23, "desktop": ["paste"]}, False, "brings no desktop/plugin.js"),
        ({"api": 22, "desktop": ["paste"]}, True, "need atlas.api 23"),
        ({"api": 23, "desktop": ["camera"]}, True, "desktop powers camera"),
    ],
)
def test_desktop_powers_declared_badly_are_refused(
    atlas: dict[str, Any], screen: bool, problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(
            json.dumps({"name": "keys", "atlas": atlas}), set(), folder_name="keys", screen=screen
        )


@pytest.mark.parametrize(
    ("atlas", "present", "problem"),
    [
        ({"api": 21, "readers": ["audio"]}, set(), "brings no __init__.py"),
        ({"api": 20, "readers": ["audio"]}, {"__init__.py"}, "need atlas.api 21"),
        ({"api": 21, "readers": ["video"]}, {"__init__.py"}, "readers for video"),
    ],
)
def test_a_reader_declared_badly_is_refused(
    atlas: dict[str, Any], present: set[str], problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(json.dumps({"name": "ears", "atlas": atlas}), present, folder_name="ears")


def _ears(root: Path, name: str, atlas: dict[str, Any]) -> None:
    folder = root / name
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": name, "atlas": atlas}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (folder / "__init__.py").write_text(
        "def register(ctx):\n"
        "    async def read(path, media_type):\n"
        f"        return {name!r}\n"
        "    ctx.register_reader('audio', read)\n",
        encoding="utf-8",
    )


def test_a_reader_it_does_not_declare_marks_the_plugin(plugins: Plugins, home: Home) -> None:
    # A call keeps it among the plugins whose Python the loop's parts load.
    _ears(home.plugins_folder, "ears", {"api": 21, "calls": ["ping"]})
    manifest = plugins.view("ears").manifest
    assert manifest is not None
    plugins.enable("ears", manifest.fingerprint())
    parts = asyncio.run(plugins.parts())
    assert parts.readers == []
    assert "a reader for audio, which it does not declare" in str(plugins.view("ears").problem)


def test_one_reader_a_kind_the_first_loaded(plugins: Plugins, home: Home) -> None:
    for name in ("ears", "more-ears"):
        _ears(home.plugins_folder, name, {"api": 21, "readers": ["audio"]})
        manifest = plugins.view(name).manifest
        assert manifest is not None
        plugins.enable(name, manifest.fingerprint())
    parts = asyncio.run(plugins.parts())
    assert [(reader.plugin, reader.kind) for reader in parts.readers] == [("ears", "audio")]


# -- the voice plugin ------------------------------------------------------------------------------


def test_an_audio_file_reaches_the_model_written_down_once(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _voice_on(plugins, fake)
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.transcribe(SAID)
    fake.reply(
        FakeReply(text="I will remind you.", when=ATLAS),
        FakeReply(text="Thursday, yes.", when=ATLAS),
    )

    async def scenario() -> str:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, readers=parts.readers)
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        conversation.send("Which day was it?")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()
        return conversation.id

    conversation_id = asyncio.run(scenario())

    (heard,) = fake.heard
    assert heard["model"] == "gpt-transcribe"
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
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    key: str | None,
    answer: FakeError | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _voice_on(plugins, fake, key)
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    if key is None:
        vault.delete("OPENAI_API_KEY")  # the one pointing OpenAI at the fake stored
    if answer is not None:
        fake.transcribe(answer)
    fake.reply(FakeReply(text="I could not hear it.", when=ATLAS))

    async def scenario() -> None:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, readers=parts.readers)
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        await assistant.close()
        await plugins.stop()

    asyncio.run(scenario())

    (turn,) = fake.bodies(CHAT)
    assert "an audio file attachment: 'voice.ogg'" in _said(turn), "the note, as before"
    expected = "no OPENAI_API_KEY" if key is None else "HTTP 500"
    assert any(
        "voice could not read voice.ogg" in record.getMessage() and expected in record.getMessage()
        for record in caplog.records
    )


def test_audio_sent_while_a_turn_runs_is_written_down_before_it_steers(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _voice_on(plugins, fake)
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.transcribe(SAID)
    fake.reply(
        FakeReply(text="Working on the first thing.", pause=0.05, when=ATLAS),
        FakeReply(text="Noted.", when=ATLAS),
    )

    async def scenario() -> None:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, readers=parts.readers)
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Plan my week.")
        assert conversation.running
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        await assistant.close()
        await plugins.stop()

    asyncio.run(scenario())

    assert len(fake.heard) == 1
    bodies = fake.bodies(CHAT)
    assert SAID not in _said(bodies[0])
    assert any(SAID in _said(body) for body in bodies[1:]), "it reached the model, written down"


# -- the answer spoken when asked ------------------------------------------------------------------


def test_an_answer_spoken_on_request_reaches_the_conversation_as_an_audio_file(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _voice_on(plugins, fake)
    heard: list[tuple[str, ConversationEvent]] = []
    assistant = _assistant(home, vault, fake, connect, tmp_path, heard)
    # Tied to Atlas's own prompt: the title model, on the fake too, takes no reply of these.
    speak = '{"text": "It is sunny.", "tone": "calm"}'
    fake.reply(
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "speak audio"}', id="s1")], when=ATLAS),
        FakeReply(calls=[FakeCall("voice_speak", speak, id="c1")], when=ATLAS),
        FakeReply(text="Sent.", when=ATLAS),
    )

    async def scenario() -> str:
        parts = await plugins.parts()
        await assistant.set_plugins(parts.tools, {}, {}, readers=parts.readers)
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Tell me the weather, in audio.")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()
        return conversation.id

    conversation_id = asyncio.run(scenario())

    (asked,) = fake.bodies("/v1/audio/speech")
    assert asked == {
        "model": "gpt-4o-mini-tts",
        "voice": "marin",
        "input": "It is sunny.",
        "response_format": "opus",
        "instructions": "calm",
    }
    [sent] = [e for c, e in heard if c == conversation_id and isinstance(e, FileSent)]
    assert sent.attachment.kind is AttachmentKind.AUDIO
    kept = assistant.attachments.path(sent.attachment.id)
    assert kept is not None
    assert kept.read_bytes() == SPOKEN
    assert list((plugins.data_folder("voice") / "speech").iterdir()) == [], "its copy goes"
    results = [m for m in fake.bodies(CHAT)[-1]["messages"] if m.get("role") == "tool"]
    assert "The audio message was sent to the user" in results[-1]["content"]


# -- the dictation and the live conversation's server side --------------------------------------


def _call(plugins: Plugins, name: str, params: Any) -> Any:
    """One of the calls the plugin's screen makes, as the server runs it."""

    async def run() -> Any:
        parts = await plugins.parts()
        [call] = [c for c in parts.calls if c.name == name]
        try:
            return await call.run(params)
        finally:
            await plugins.stop()

    return asyncio.run(run())


def test_the_screen_dictates_through_the_plugin(plugins: Plugins, fake: FakeModelServer) -> None:
    _voice_on(plugins, fake)
    fake.transcribe("Buy bread.")
    recorded = base64.b64encode(b"webm recording").decode()
    said = _call(plugins, "dictate", {"audio": recorded, "media_type": "audio/webm"})
    assert said["text"] == "Buy bread."
    (heard,) = fake.heard
    assert (heard["file_name"], heard["file_type"]) == ("dictation.webm", "audio/webm")
    assert heard["file_bytes"] == b"webm recording"
    with pytest.raises(ValueError, match="not base64"):
        _call(plugins, "dictate", {"audio": "not base64!"})


def test_every_dictation_is_kept_with_its_recording(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake)
    fake.transcribe("Buy bread.", FakeError(500, "The transcription service is down."))
    first = _call(
        plugins,
        "dictate",
        {"audio": base64.b64encode(b"first").decode(), "media_type": "audio/webm;codecs=opus"},
    )
    with pytest.raises(ValueError, match="service is down"):
        _call(
            plugins,
            "dictate",
            {
                "audio": base64.b64encode(b"second").decode(),
                "media_type": "audio/webm",
                "where": "anywhere",
            },
        )
    listed = _call(plugins, "dictations", None)["dictations"]
    # Both made within the same second: their order is not told apart.
    assert sorted((d["text"], d["where"]) for d in listed) == [
        ("", "anywhere"),
        ("Buy bread.", "box"),
    ]
    failed = next(d for d in listed if d["where"] == "anywhere")
    assert "service is down" in failed["error"], "kept with why, its speech not lost"
    assert first["id"] in {d["id"] for d in listed}
    audio = _call(plugins, "dictation_audio", {"id": first["id"]})
    assert base64.b64decode(audio["audio"]) == b"first"
    assert audio["media_type"] == "audio/webm;codecs=opus"
    fake.transcribe("Call the bank.")
    again = _call(plugins, "dictation_again", {"id": failed["id"]})
    assert again == {"id": failed["id"], "text": "Call the bank."}
    assert fake.heard[-1]["file_bytes"] == b"second", "its own recording, written down again"
    rewritten = next(
        d for d in _call(plugins, "dictations", None)["dictations"] if d["id"] == failed["id"]
    )
    assert (rewritten["text"], rewritten["error"]) == ("Call the bank.", "")
    assert _call(plugins, "dictation_delete", {"id": first["id"]}) is True
    folder = plugins.data_folder("voice") / "dictations"
    assert sorted(path.suffix for path in folder.iterdir()) == [".json", ".webm"]
    for wrong in ("", "../../vault", first["id"]):
        with pytest.raises(ValueError, match="no such dictation"):
            _call(plugins, "dictation_audio", {"id": wrong})


def test_the_dictation_status_names_the_shortcut(
    plugins: Plugins, vault: Vault, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake)
    assert _call(plugins, "dictation_status", None) == {
        "ready": True,
        "why": "",
        "shortcut": "Capslock",
    }
    plugins.set_setting("voice", "dictation_shortcut", "F9")
    vault.delete("OPENAI_API_KEY")
    status = _call(plugins, "dictation_status", None)
    assert (status["ready"], status["shortcut"]) == (False, "F9")
    assert "OPENAI_API_KEY" in status["why"]


def test_the_voice_screen_gets_its_desktop_powers(plugins: Plugins, fake: FakeModelServer) -> None:
    _voice_on(plugins, fake)
    [screen] = [s for s in plugins.screens() if s.plugin == "voice"]
    assert screen.desktop == ("shortcut", "paste")


def test_the_live_session_is_opened_here_with_the_key_and_the_history(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake)
    status = _call(plugins, "live_status", None)
    assert status == {
        "ready": True,
        "why": "",
        "provider": "openai",
        "turns": {"provider": "openai", "model": "gpt-6-luna", "effort": "none"},
        "quiet_seconds": 120,
    }
    history = [
        {"role": "user", "text": "What is on today?"},
        {"role": "assistant", "text": "Two meetings."},
        {"role": "tool", "text": "never sent"},
    ]
    offer = "v=0 the app's offer"
    opened = _call(plugins, "live_session", {"sdp": offer, "history": history})
    assert opened == {"session": "live_fake", "sdp": LIVE_ANSWER}
    (body,) = fake.bodies("/v1/live/sessions")
    session = body["session"]
    assert session["model"] == "gpt-live-1"
    assert session["delegation"] == {"type": "client"}
    assert session["audio"] == {"output": {"voice": "marin"}}
    assert "Atlas does the work" in session["instructions"]
    assert [(m["role"], m["content"][0]["text"]) for m in session["input"]] == [
        ("user", "What is on today?"),
        ("assistant", "Two meetings."),
    ]
    assert body["transport"] == {"type": "webrtc", "sdp": offer}
    assert "fake-key" not in json.dumps(opened), "the key never goes to the screen"
    with pytest.raises(ValueError, match="no WebRTC offer"):
        _call(plugins, "live_session", {"sdp": "nothing"})


def test_without_the_key_the_live_conversation_says_so(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake, key=None)
    plugins.set_setting("voice", "voice_turns_model", "conversation")
    status = _call(plugins, "live_status", None)
    assert (status["ready"], status["turns"]) == (False, None)
    assert "OPENAI_API_KEY" in status["why"]


def test_a_spoken_turn_carries_its_note_and_a_typed_one_does_not(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _voice_on(plugins, fake)
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.reply(FakeReply(text="Two meetings.", when=ATLAS), FakeReply(text="Done.", when=ATLAS))

    async def scenario() -> None:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, Hooks((), parts.hooks), readers=parts.readers)
        conversation = assistant.conversation(assistant.conversations.create().id)
        [mark] = [c for c in parts.calls if c.name == "live_turn"]
        spoken = "User: what is on today\nVoice: let me check"
        assert await mark.run({"conversation": conversation.id, "spoken": spoken}) is True
        conversation.send("what is on today")
        await conversation.wait()
        conversation.send("Thanks, typed this time.")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()

    asyncio.run(scenario())

    spoken_turn, typed_turn = fake.bodies(CHAT)
    said = json.dumps(spoken_turn["messages"])
    assert "transcript of speech" in said
    assert "Voice: let me check" in said
    assert "transcript of speech" not in json.dumps(typed_turn["messages"][-1]), "only once"


# -- a setting's choices (SDK version 25) ----------------------------------------------------------


def _with_settings(settings: list[Any], api: int = 25, calls: tuple[str, ...] = ()) -> Any:
    atlas = {"api": api, "settings": settings, "calls": list(calls)}
    return parse_manifest(
        json.dumps({"name": "tuned", "atlas": atlas}), {"__init__.py"}, folder_name="tuned"
    )


def test_a_setting_declares_its_choices() -> None:
    manifest = _with_settings(
        [
            "plain",
            {"name": "color", "default": "red", "choices": ["red", "green"]},
            {"name": "model", "choices": "models"},
            {"name": "voice", "description": "Who speaks.", "choices": "plugin"},
        ],
        calls=("setting_choices",),
    )
    assert [(s.name, s.choices, s.choices_from) for s in manifest.settings] == [
        ("plain", (), ""),
        ("color", ("red", "green"), ""),
        ("model", (), "models"),
        ("voice", (), "plugin"),
    ]
    assert manifest.settings[3].description == "Who speaks."


@pytest.mark.parametrize(
    ("setting", "api", "calls", "problem"),
    [
        ({"name": "x", "choices": ["a"]}, 24, (), "need atlas.api 25"),
        ({"name": "x", "choices": "somewhere"}, 25, (), "come from models or plugin"),
        ({"name": "x", "choices": "plugin"}, 25, (), "the call setting_choices"),
        ({"name": "x", "choices": []}, 25, (), "1 to 64 values"),
        ({"name": "x", "choices": ["a", 2]}, 25, (), "1 to 64 values"),
        ({"name": "x", "choices": {"from": "models"}}, 25, (), "come from models or plugin"),
        ({"name": "x", "default": 3}, 25, (), "an object of text"),
    ],
)
def test_choices_declared_badly_are_refused(
    setting: dict[str, Any], api: int, calls: tuple[str, ...], problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        _with_settings([setting], api, calls)


def test_the_voice_lists_the_models_the_provider_has_for_each_setting(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake)
    fake.model_ids = [
        "gpt-6-luna",
        "gpt-transcribe",
        "gpt-4o-mini-transcribe-2025-03-20",
        "gpt-4o-mini-transcribe",
        "gpt-live-transcribe",
        "whisper-1",
        "gpt-4o-mini-tts",
        "gpt-live-1",
    ]

    def values(setting: str, **more: Any) -> list[str]:
        found = _call(plugins, "setting_choices", {"setting": setting, **more})
        assert found["why"] == ""
        return [choice["value"] for choice in found["choices"]]

    # The provider's own first, as the empty value; a dated snapshot after the name it pins.
    assert values("transcription_model") == [
        "",
        "gpt-4o-mini-transcribe",
        "gpt-transcribe",
        "whisper-1",
        "gpt-4o-mini-transcribe-2025-03-20",
    ]
    assert values("speech_model") == ["", "gpt-4o-mini-tts"]
    assert values("live_model") == ["", "gpt-live-1"]
    assert values("voice") == [""], "OpenAI lists no voices: its voice is typed"
    models = [{"value": "gemini/gemini-3.8-flash", "label": "Google Gemini · Gemini 3.8 Flash"}]
    assert values("voice_turns_model", models=[*models, {"value": "no-provider"}, 3]) == [
        "",
        "conversation",
        "gemini/gemini-3.8-flash",
    ]
    found = _call(plugins, "setting_choices", {"setting": "transcription_model"})
    assert found["choices"][0]["label"] == "OpenAI's choice: gpt-transcribe"
    assert _call(plugins, "setting_choices", {"setting": "quiet_close_seconds"}) == {
        "choices": [],
        "why": "",
    }


def test_gemini_lists_what_each_model_does_and_its_voices_by_language(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake, provider="gemini")

    def model(name: str, *methods: str) -> dict[str, Any]:
        return {
            "name": f"models/{name}",
            "displayName": name.replace("-", " ").title(),
            "supportedGenerationMethods": list(methods),
        }

    fake.gemini_models = [
        model("gemini-3.8-flash", "generateContent"),
        model("gemini-3.5-transcribe", "generateContent"),
        model("gemini-3.8-flash-tts", "generateContent"),
        model("gemini-3.1-flash-image", "generateContent"),
        model("gemini-embedding-2", "embedContent"),
        model("gemini-3.8-live", "bidiGenerateContent"),
        model("gemini-3.5-transcribe-live", "bidiGenerateContent"),
    ]
    fake.gemini_voices = {
        "pt-BR": [
            {"id": "pt-br-advisor-1", "display_name": "Advisor 1", "gender": "female"},
        ],
        "en-US": [
            {"id": "kore", "display_name": "Kore", "gender": "female"},
            {"id": "en-us-tutor-1", "display_name": "Tutor 1", "gender": "male"},
        ],
    }

    def values(setting: str, language: str = "") -> list[str]:
        params = {"setting": setting, "language": language}
        return [c["value"] for c in _call(plugins, "setting_choices", params)["choices"]]

    # A transcription model first, then the models that talk; nothing that draws or embeds.
    assert values("transcription_model") == ["", "gemini-3.5-transcribe", "gemini-3.8-flash"]
    assert values("speech_model") == ["", "gemini-3.8-flash-tts"]
    assert values("live_model") == ["", "gemini-3.8-live"]
    assert values("voice", "pt-BR") == ["", "pt-br-advisor-1", "kore"]
    assert values("voice", "en-US") == ["", "kore"]
    labels = [
        c["label"] for c in _call(plugins, "setting_choices", {"setting": "voice"})["choices"]
    ]
    assert labels == ["Gemini's choice: Kore", "Kore (female, every language)"]
    keys = {r.headers.get("x-goog-api-key") for r in fake.requests if r.path.startswith("/v1beta")}
    assert keys == {"fake-key"}


def test_a_list_kept_short_says_why(plugins: Plugins, fake: FakeModelServer, vault: Vault) -> None:
    _voice_on(plugins, fake, provider="gemini", key="wrong-key")
    found = _call(plugins, "setting_choices", {"setting": "speech_model"})
    assert [c["value"] for c in found["choices"]] == [""]
    assert "Gemini refused the list of models (HTTP 400): API key not valid" in found["why"]
    vault.delete("GEMINI_API_KEY")
    found = _call(plugins, "setting_choices", {"setting": "speech_model"})
    assert "no GEMINI_API_KEY" in found["why"]


# -- the voice through Gemini --------------------------------------------------------------------


def test_gemini_writes_down_audio_with_its_transcription_model(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake, provider="gemini")
    fake.transcribe("Buy bread.", "Call the bank.")
    recorded = base64.b64encode(b"webm recording").decode()
    said = _call(plugins, "dictate", {"audio": recorded, "media_type": "audio/webm;codecs=opus"})
    assert said["text"] == "Buy bread."
    (body,) = fake.bodies("/v1beta/interactions")
    assert body == {
        "model": "gemini-3.5-transcribe",
        "input": [{"type": "audio", "mime_type": "audio/webm", "data": recorded}],
    }

    # Another model is asked for the words only, beside the audio.
    plugins.set_setting("voice", "transcription_model", "gemini-3.8-flash")
    _call(plugins, "dictate", {"audio": recorded, "media_type": "audio/ogg"})
    asked = fake.bodies("/v1beta/interactions")[-1]
    assert asked["model"] == "gemini-3.8-flash"
    assert asked["input"][0]["type"] == "text"
    assert "words only" in asked["input"][0]["text"]
    assert not any(r.headers.get("authorization") for r in fake.requests), "its key, its header"


def test_gemini_takes_a_long_recording_through_its_files_api(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake, provider="gemini")
    fake.transcribe("A long meeting.")
    long = b"\x01" * (INLINE_BYTES + 1)
    said = _call(plugins, "dictation_status", None)
    assert said["ready"] is True

    async def read() -> str:
        parts = await plugins.parts()
        [reader] = parts.readers
        path = plugins.data_folder("voice") / "meeting.ogg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(long)
        try:
            return await reader.run(path, "audio/ogg")
        finally:
            await plugins.stop()

    assert asyncio.run(read()) == "A long meeting."
    [(uri, data)] = fake.uploaded.items()
    assert data == long
    (body,) = fake.bodies("/v1beta/interactions")
    assert body["input"] == [{"type": "audio", "mime_type": "audio/ogg", "uri": uri}]


def test_without_the_gemini_key_the_voice_names_it(plugins: Plugins, fake: FakeModelServer) -> None:
    _voice_on(plugins, fake, provider="gemini", key=None)
    status = _call(plugins, "dictation_status", None)
    assert status["ready"] is False
    assert "no GEMINI_API_KEY" in status["why"]
    with pytest.raises(ValueError, match="no GEMINI_API_KEY"):
        _call(plugins, "dictate", {"audio": base64.b64encode(b"x").decode()})


def _spoken_by_gemini(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> Path:
    """Atlas asked to speak, through Gemini: the file the conversation got."""
    _voice_on(plugins, fake, provider="gemini")
    heard: list[tuple[str, ConversationEvent]] = []
    assistant = _assistant(home, vault, fake, connect, tmp_path, heard)
    speak = '{"text": "It is sunny.", "tone": "calm"}'
    fake.reply(
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "speak audio"}', id="s1")], when=ATLAS),
        FakeReply(calls=[FakeCall("voice_speak", speak, id="c1")], when=ATLAS),
        FakeReply(text="Sent.", when=ATLAS),
    )

    async def scenario() -> str:
        parts = await plugins.parts()
        await assistant.set_plugins(parts.tools, {}, {}, readers=parts.readers)
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Tell me the weather, in audio.")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()
        return conversation.id

    conversation_id = asyncio.run(scenario())
    (asked,) = fake.bodies("/v1beta/interactions")
    assert asked == {
        "model": "gemini-3.8-flash-tts",
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": "It is sunny.",
                        "annotations": [{"type": "speech_metadata", "style": "calm"}],
                    }
                ],
            }
        ],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": "Kore"}]},
    }
    [sent] = [e for c, e in heard if c == conversation_id and isinstance(e, FileSent)]
    assert sent.attachment.kind is AttachmentKind.AUDIO
    kept = assistant.attachments.path(sent.attachment.id)
    assert kept is not None
    return kept


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed here")
def test_gemini_speech_becomes_a_voice_note_with_ffmpeg(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    kept = _spoken_by_gemini(plugins, home, vault, fake, connect, tmp_path)
    assert kept.suffix == ".ogg"
    assert kept.read_bytes().startswith(b"OggS"), "Opus in Ogg, which Telegram plays as a voice"


def test_gemini_speech_goes_as_wav_without_ffmpeg(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A PATH with no ffmpeg on it, as on a computer that never installed it.
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    kept = _spoken_by_gemini(plugins, home, vault, fake, connect, tmp_path)
    assert kept.suffix == ".wav"
    assert kept.read_bytes() == SPOKEN_WAV


def test_the_gemini_live_session_gets_a_token_and_its_setup_here(
    plugins: Plugins, fake: FakeModelServer
) -> None:
    _voice_on(plugins, fake, provider="gemini")
    plugins.set_setting("voice", "voice", "pt-br-advisor-1")
    status = _call(plugins, "live_status", None)
    assert (status["ready"], status["provider"]) == (True, "gemini")
    assert status["turns"] == {"provider": "gemini", "model": "gemini-3.8-flash", "effort": "none"}
    history = [
        {"role": "user", "text": "What is on today?"},
        {"role": "assistant", "text": "Two meetings."},
    ]
    opened = _call(plugins, "live_session", {"history": history})
    assert opened["url"] == (
        f"ws://127.0.0.1:{fake.port}/ws/google.ai.generativelanguage.v1beta.GenerativeService"
        f".BidiGenerateContentConstrained?access_token={LIVE_TOKEN}"
    )
    setup_message, history_message = opened["messages"]
    setup = setup_message["setup"]
    assert setup["model"] == "models/gemini-3.8-live"
    voice = setup["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
    assert voice == {"voiceName": "pt-br-advisor-1"}
    assert "Atlas does the work" in setup["systemInstruction"]["parts"][0]["text"]
    [declared] = setup["tools"][0]["functionDeclarations"]
    assert declared["name"] == "ask_atlas"
    assert setup["inputAudioTranscription"] == setup["outputAudioTranscription"] == {}
    assert history_message == {
        "clientContent": {
            "turns": [
                {"role": "user", "parts": [{"text": "What is on today?"}]},
                {"role": "model", "parts": [{"text": "Two meetings."}]},
            ],
            "turnComplete": False,
        }
    }
    assert "fake-key" not in json.dumps(opened), "the key never goes to the screen"
    (token,) = fake.bodies("/v1beta/auth_tokens")
    assert token["uses"] == 1

    # Gemini let the session go on: a new token, the same session, no history again.
    resumed = _call(plugins, "live_session", {"resume": "h-1"})
    assert [list(m) for m in resumed["messages"]] == [["setup"]]
    assert resumed["messages"][0]["setup"]["sessionResumption"] == {"handle": "h-1"}
