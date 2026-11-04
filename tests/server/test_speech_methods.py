"""Speech through the server (step 26): where the model is, its download told to every client,
the user's no kept, the choice of who writes recordings down made on the settings screen, and a
home whose voice plugin wrote them down keeping its provider."""

import hashlib
from pathlib import Path

import httpx2

from atlas.home import Database, Home, migrate
from atlas.plugins import OFFICIAL_FOLDER, Plugins, install_official
from atlas.secrets import Vault
from atlas.speech import ModelFile, ModelStore, Speech
from server_support import Serve

WEIGHTS = b"the model's weights"
PARTS = (
    (
        "model",
        (
            ModelFile(
                "weights.onnx",
                "https://models.example.com/weights.onnx",
                len(WEIGHTS),
                hashlib.sha256(WEIGHTS).hexdigest(),
            ),
        ),
    ),
)


class _Engine:
    def transcribe(self, path: Path) -> str:
        return "said here"


def _speech(home: Home) -> Speech:
    transport = httpx2.MockTransport(lambda _request: httpx2.Response(200, content=WEIGHTS))
    return Speech(
        ModelStore(home.speech_folder, PARTS),
        lambda: "local",
        lambda _name: None,
        engine=_Engine(),
        transport=transport,
    )


def test_the_model_is_offered_downloaded_and_told_to_every_client(serve: Serve, home: Home) -> None:
    server = serve(speech=_speech(home))
    with server.client() as client, server.client() as other:
        before = client.result("speech.status")
        declined = client.result("speech.decline")
        started = client.result("speech.download")
        told = other.event("speech.changed")["payload"]
        while not told["present"] and not told["error"]:
            told = other.event("speech.changed")["payload"]
        after = client.result("speech.status")

    assert (before["transcription"], before["present"], before["size"]) == (
        "local",
        False,
        len(WEIGHTS),
    )
    assert "NVIDIA" in before["credit"]
    assert declined["declined"] is True
    assert started["declined"] is False, "a download takes the no back"
    assert (told["present"], told["error"]) == (True, "")
    assert after["present"] is True
    assert (home.speech_folder / "model" / "weights.onnx").read_bytes() == WEIGHTS


def test_who_writes_recordings_down_is_chosen_on_the_settings_screen(
    serve: Serve, home: Home
) -> None:
    with serve(speech=None).client() as client:
        changed = client.result(
            "settings.write",
            {"changes": [{"section": "voice", "key": "transcription", "value": "gemini"}]},
        )
        now = client.result("speech.status")
        refused = client.call(
            "settings.write",
            {"changes": [{"section": "voice", "key": "transcription", "value": "whisper"}]},
        )

    [row] = [
        r for r in changed["settings"] if (r["section"], r["key"]) == ("voice", "transcription")
    ]
    assert row["value"] == "gemini"
    assert now["transcription"] == "gemini", "applied without a restart"
    assert "is not one of: local, openai, gemini, off" in refused["error"]["message"]
    assert 'transcription = "gemini"' in home.config_file.read_text(encoding="utf-8")


def _voice(home: Home, vault: Vault, provider: str, *, on: bool) -> None:
    database = Database(home.database_file)
    plugins = Plugins(home, database, vault, official_folder=OFFICIAL_FOLDER)
    install_official(plugins, "voice")
    plugins.set_setting("voice", "provider", provider)
    manifest = plugins.view("voice").manifest
    assert manifest is not None
    if on:
        plugins.enable("voice", manifest.fingerprint())


def test_a_home_whose_voice_wrote_recordings_down_keeps_its_provider(
    serve: Serve, home: Home, vault: Vault
) -> None:
    from atlas.__main__ import SCHEMAS  # noqa: PLC0415 - the schemas the server migrates

    migrate(Database(home.database_file), SCHEMAS)
    _voice(home, vault, "gemini", on=True)

    with serve().client() as client:
        first = client.result("speech.status")

    assert first["transcription"] == "gemini"
    moved = home.plugin_data_folder / "voice" / "transcription-moved"
    assert moved.exists(), "once: a user who takes it out later gets this computer's model"


def test_a_home_whose_voice_was_off_writes_them_down_here(
    serve: Serve, home: Home, vault: Vault
) -> None:
    from atlas.__main__ import SCHEMAS  # noqa: PLC0415 - the schemas the server migrates

    migrate(Database(home.database_file), SCHEMAS)
    _voice(home, vault, "openai", on=False)

    with serve().client() as client:
        assert client.result("speech.status")["transcription"] == "local"
    assert not home.config_file.exists() or "voice" not in home.config_file.read_text("utf-8")
