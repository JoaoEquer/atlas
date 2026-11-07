"""Speech (step 26): a recording of any format read at the model's rate; the model's download,
each file checked against its hash; and who writes a recording down, as the user chose: this
computer (a fake engine in the model's place), OpenAI or Gemini (the fake model server in theirs),
or nobody.
"""

import asyncio
import hashlib
import math
import struct
import wave
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest

from atlas.speech import GEMINI, OPENAI, ModelFile, ModelStore, Speech, SpeechError
from atlas.speech.local import read_samples
from fake_model import FakeError, FakeModelServer

SAID = "Remind me to call the dentist on Thursday."
GEMINI_INLINE = 14 * 1024 * 1024

FIRST = b"the model's weights"
SECOND = b"its vocabulary"


def _file(name: str, data: bytes) -> ModelFile:
    return ModelFile(name, f"https://models.example.com/{name}", len(data), _sha(data))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


#: A model of two small files, in two folders, as the real one has the model's and the VAD's.
PARTS = (("model", (_file("weights.onnx", FIRST),)), ("vad", (_file("vocab.txt", SECOND),)))


def _serving(files: dict[str, bytes], asked: list[str] | None = None) -> httpx2.MockTransport:
    def handle(request: httpx2.Request) -> httpx2.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if asked is not None:
            asked.append(name)
        if name not in files:
            return httpx2.Response(404)
        return httpx2.Response(200, content=files[name])

    return httpx2.MockTransport(handle)


class FakeEngine:
    """This computer's model, as the tests have it: what it heard, and what it says."""

    def __init__(self) -> None:
        self.heard: list[Path] = []

    def transcribe(self, path: Path) -> str:
        self.heard.append(path)
        return SAID


def _wav(path: Path, rate: int, channels: int, seconds: float) -> None:
    """A tone, as a WAV file, at `rate` and with `channels`."""
    frames = int(rate * seconds)
    samples = (int(8000 * math.sin(2 * math.pi * 440 * n / rate)) for n in range(frames))
    data = b"".join(struct.pack("<h", sample) * channels for sample in samples)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data)


# -- reading a recording ---------------------------------------------------------------------------


def test_a_recording_of_any_format_is_read_mono_at_the_models_rate(tmp_path: Path) -> None:
    import av  # noqa: PLC0415 - the transcribe extra, as atlas.speech imports it

    wav = tmp_path / "tone.wav"
    _wav(wav, 8000, 2, 1.5)
    webm = tmp_path / "tone.webm"
    with av.open(str(wav)) as source, av.open(str(webm), "w") as out:
        stream = out.add_stream("libopus", rate=48000)
        for frame in source.decode(audio=0):
            frame.pts = None
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)

    for path in (wav, webm):
        samples = read_samples(path)
        assert samples.ndim == 1, "mono"
        assert str(samples.dtype) == "float32"
        assert abs(len(samples) - 24000) < 1600, f"{path.name}: 1.5 s at 16 kHz"


def test_a_file_that_is_no_recording_is_refused(tmp_path: Path) -> None:
    text = tmp_path / "notes.ogg"
    text.write_text("not audio at all", encoding="utf-8")

    with pytest.raises(SpeechError, match="could not be read as audio"):
        read_samples(text)


# -- the download ----------------------------------------------------------------------------------


def test_the_model_is_downloaded_once_each_file_checked(tmp_path: Path) -> None:
    store = ModelStore(tmp_path / "speech", PARTS)
    told: list[tuple[int, int]] = []
    asked: list[str] = []
    assert not store.present()

    store.download(
        lambda done, total: told.append((done, total)),
        transport=_serving({"weights.onnx": FIRST, "vocab.txt": SECOND}, asked),
    )

    assert store.present()
    assert (tmp_path / "speech" / "model" / "weights.onnx").read_bytes() == FIRST
    assert (tmp_path / "speech" / "vad" / "vocab.txt").read_bytes() == SECOND
    total = len(FIRST) + len(SECOND)
    assert told[0] == (0, total)
    assert told[-1] == (total, total)
    assert [done for done, _ in told] == sorted(done for done, _ in told)
    store.download(transport=_serving({}, asked))
    assert asked == ["weights.onnx", "vocab.txt"], "what is there is not fetched again"


def test_a_file_that_is_not_the_one_expected_is_thrown_away(tmp_path: Path) -> None:
    store = ModelStore(tmp_path / "speech", PARTS)
    changed = b"the model's w8ights"  # its size, another content

    with pytest.raises(SpeechError, match="not the file Atlas expects"):
        store.download(transport=_serving({"weights.onnx": changed}))

    assert list((tmp_path / "speech" / "model").iterdir()) == [], "nothing kept, no .part"
    with pytest.raises(SpeechError, match=r"vocab.txt could not be downloaded \(HTTP 404\)"):
        store.download(transport=_serving({"weights.onnx": FIRST}))
    assert (tmp_path / "speech" / "model" / "weights.onnx").exists(), "the good one stays"
    assert not store.present()


# -- who writes it down ----------------------------------------------------------------------------


def _speech(
    tmp_path: Path,
    choice: str,
    *,
    engine: FakeEngine | None = None,
    told: list[str] | None = None,
    files: dict[str, bytes] | None = None,
    key: str | None = "fake-key",
    fake: FakeModelServer | None = None,
) -> Speech:
    made = Speech(
        ModelStore(tmp_path / "speech", PARTS),
        lambda: choice,
        lambda _name: key,
        engine=engine or FakeEngine(),
        transport=_serving(files or {}),
        openai=replace(OPENAI, api_url=fake.base_url) if fake else OPENAI,
        gemini=replace(GEMINI, api_url=fake.gemini_url) if fake else GEMINI,
    )
    if told is not None:
        made.on_changed = lambda: told.append(str(made.status().downloading))
    return made


def _recording(tmp_path: Path, data: bytes = b"OggS" + b"\x00" * 64) -> Path:
    path = tmp_path / "voice.ogg"
    path.write_bytes(data)
    return path


def test_a_recording_before_the_model_waits_for_it_and_the_app_asks_once(tmp_path: Path) -> None:
    engine = FakeEngine()
    told: list[str] = []
    speech = _speech(
        tmp_path,
        "local",
        engine=engine,
        told=told,
        files={"weights.onnx": FIRST, "vocab.txt": SECOND},
    )
    recording = _recording(tmp_path)

    async def scenario() -> str:
        for _ in range(2):
            with pytest.raises(SpeechError, match=r"not on this computer yet.*Settings, Voice"):
                await speech.transcribe(recording, "audio/ogg")
        assert speech.status().wanted
        assert len(told) == 1, "told once, not on each recording"
        assert speech.download()
        assert not speech.download(), "one download at a time"
        assert speech.status().downloading is not None
        await speech.wait()
        return await speech.transcribe(recording, "audio/ogg")

    assert asyncio.run(scenario()) == SAID
    assert engine.heard == [recording]
    status = speech.status()
    assert (status.present, status.downloading, status.error) == (True, None, "")
    assert status.size == len(FIRST) + len(SECOND)
    assert "CC BY 4.0" in status.credit
    assert told[-1] == "None", "the end is told"


def test_a_no_to_the_download_is_kept_and_a_download_takes_it_back(tmp_path: Path) -> None:
    speech = _speech(tmp_path, "local")
    speech.decline()
    assert speech.status().declined
    assert _speech(tmp_path, "local").status().declined, "kept on disk, for every home"

    async def scenario() -> None:
        assert speech.download()
        await speech.wait()

    asyncio.run(scenario())
    status = speech.status()
    assert not status.declined
    assert "could not be downloaded (HTTP 404)" in status.error, "a failure is kept to show"


def test_nobody_writes_it_down_when_the_user_turned_it_off(tmp_path: Path) -> None:
    engine = FakeEngine()
    speech = _speech(tmp_path, "off", engine=engine)

    with pytest.raises(SpeechError, match="off in Settings, Voice"):
        asyncio.run(speech.transcribe(_recording(tmp_path), "audio/ogg"))
    assert engine.heard == []


def test_openai_writes_it_down_with_its_key(tmp_path: Path, fake: FakeModelServer) -> None:
    fake.transcribe(SAID)
    speech = _speech(tmp_path, "openai", fake=fake)

    assert asyncio.run(speech.transcribe(_recording(tmp_path), "audio/ogg")) == SAID
    [heard] = fake.heard
    assert (heard["model"], heard["file_name"], heard["file_type"]) == (
        "gpt-transcribe",
        "voice.ogg",
        "audio/ogg",
    )


@pytest.mark.parametrize(
    ("choice", "key", "answer", "said"),
    [
        ("openai", None, None, "no OPENAI_API_KEY in the vault"),
        ("gemini", None, None, "no GEMINI_API_KEY in the vault"),
        (
            "openai",
            "fake-key",
            FakeError(500, "Down."),
            r"OpenAI refused the transcription \(HTTP 500\)",
        ),
    ],
    ids=["no OpenAI key", "no Gemini key", "OpenAI fails"],
)
def test_a_provider_that_cannot_says_why(
    tmp_path: Path,
    fake: FakeModelServer,
    choice: str,
    key: str | None,
    answer: FakeError | None,
    said: str,
) -> None:
    if answer is not None:
        fake.transcribe(answer)
    speech = _speech(tmp_path, choice, key=key, fake=fake)

    with pytest.raises(SpeechError, match=said):
        asyncio.run(speech.transcribe(_recording(tmp_path), "audio/ogg"))


def test_gemini_takes_a_long_recording_through_its_files_api(
    tmp_path: Path, fake: FakeModelServer
) -> None:
    fake.transcribe("A long meeting.")
    long = b"\x01" * (GEMINI_INLINE + 1)
    speech = _speech(tmp_path, "gemini", fake=fake)

    said = asyncio.run(speech.transcribe(_recording(tmp_path, long), "audio/ogg; codecs=opus"))

    assert said == "A long meeting."
    [(uri, data)] = fake.uploaded.items()
    assert data == long
    (body,) = fake.bodies("/v1beta/interactions")
    assert body == {
        "model": "gemini-3.5-transcribe",
        "input": [{"type": "audio", "mime_type": "audio/ogg", "uri": uri}],
    }


def test_gemini_takes_a_short_one_inside_its_request(tmp_path: Path, fake: FakeModelServer) -> None:
    fake.transcribe(SAID)
    speech = _speech(tmp_path, "gemini", fake=fake)

    assert asyncio.run(speech.transcribe(_recording(tmp_path), "")) == SAID
    assert fake.uploaded == {}
    (body,) = fake.bodies("/v1beta/interactions")
    assert body["input"][0]["mime_type"] == "audio/ogg", "an unnamed type taken as a voice note"
