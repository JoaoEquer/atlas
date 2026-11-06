"""Dictation through the server (step 26, the voice plugin's until then): a recording kept in the
home before it is written down, by whom `[voice] transcription` says; one that could not be is kept
with why and written down again; each read, played and deleted, every client told; and the
dictations the voice plugin kept moved to the home's."""

import base64
from dataclasses import replace

from atlas.home import Home
from atlas.speech import OPENAI, ModelStore, Speech
from fake_model import FakeError, FakeModelServer
from server_support import Serve


def _speech(home: Home, fake: FakeModelServer) -> Speech:
    return Speech(
        ModelStore(home.speech_folder),
        lambda: "openai",
        lambda _name: "fake-key",
        openai=replace(OPENAI, api_url=fake.base_url),
    )


def _audio(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_every_dictation_is_kept_with_its_recording_and_written_down(
    serve: Serve, home: Home, fake: FakeModelServer
) -> None:
    fake.transcribe("Buy bread.", FakeError(500, "The transcription service is down."))
    with serve(speech=_speech(home, fake)).client() as client:
        first = client.result(
            "dictation.write", {"audio": _audio(b"first"), "media_type": "audio/webm;codecs=opus"}
        )
        told = client.event("dictations.changed")
        failed = client.result(
            "dictation.write",
            {"audio": _audio(b"second"), "media_type": "audio/webm", "where": "anywhere"},
        )
        listed = client.result("dictation.list")["dictations"]
        audio = client.result("dictation.audio", {"id": first["id"]})
        fake.transcribe("Call the bank.")
        again = client.result("dictation.again", {"id": failed["id"]})
        client.result("dictation.delete", {"id": first["id"]})
        gone = [client.call("dictation.audio", {"id": wrong}) for wrong in ("", "../../vault")]
        gone.append(client.call("dictation.audio", {"id": first["id"]}))
        refused = client.call("dictation.write", {"audio": "not base64!"})

    assert (first["text"], first["where"], first["error"]) == ("Buy bread.", "box", "")
    assert told["payload"] == {}
    assert (failed["text"], failed["where"]) == ("", "anywhere")
    assert "service is down" in failed["error"], "kept with why, its speech not lost"
    # Both made within the same second: their order is not told apart.
    assert sorted(d["id"] for d in listed) == sorted([first["id"], failed["id"]])
    assert base64.b64decode(audio["audio"]) == b"first"
    assert audio["media_type"] == "audio/webm;codecs=opus"
    assert (again["id"], again["text"], again["error"]) == (failed["id"], "Call the bank.", "")
    assert fake.heard[0]["file_type"] == "audio/webm;codecs=opus"
    assert fake.heard[-1]["file_bytes"] == b"second", "its own recording, written down again"
    assert sorted(path.suffix for path in home.dictations_folder.iterdir()) == [".json", ".webm"]
    assert all("no such dictation" in frame["error"]["message"] for frame in gone)
    assert "not base64" in refused["error"]["message"]


def test_a_dictation_before_the_model_is_kept_and_the_app_asked(serve: Serve, home: Home) -> None:
    with serve().client() as client:
        written = client.result("dictation.write", {"audio": _audio(b"webm recording")})
        asked = client.event("speech.changed")["payload"]

    assert "not on this computer yet" in written["error"]
    assert asked["wanted"] is True
    assert len(list(home.dictations_folder.glob("*.webm"))) == 1


def test_the_voice_plugins_dictations_move_to_the_home(serve: Serve, home: Home) -> None:
    kept = home.plugin_data_folder / "voice" / "dictations"
    kept.mkdir(parents=True)
    (kept / "20261025-101500-ab12.json").write_text(
        '{"id": "20261025-101500-ab12", "at": "2026-10-25T10:15:00-0300", "text": "Buy bread.",'
        ' "media_type": "audio/webm", "where": "anywhere", "error": ""}',
        encoding="utf-8",
    )
    (kept / "20261025-101500-ab12.webm").write_bytes(b"recording")

    with serve().client() as client:
        listed = client.result("dictation.list")["dictations"]

    assert [(d["id"], d["text"]) for d in listed] == [("20261025-101500-ab12", "Buy bread.")]
    assert not kept.exists()


def test_a_provider_chosen_on_the_settings_screen_is_reached_where_atlass_is(
    serve: Serve, fake: FakeModelServer
) -> None:
    """With no Speech given, the server's own: OpenAI at the address and with the key Atlas's
    OpenAI provider has, the fake model server's here."""
    fake.transcribe("Water the plants.")
    with serve(providers=("openai",)).client() as client:
        client.result(
            "settings.write",
            {"changes": [{"section": "voice", "key": "transcription", "value": "openai"}]},
        )
        written = client.result("dictation.write", {"audio": _audio(b"webm recording")})

    assert (written["text"], written["error"]) == ("Water the plants.", "")
    [heard] = fake.heard
    assert heard["model"] == "gpt-transcribe"
