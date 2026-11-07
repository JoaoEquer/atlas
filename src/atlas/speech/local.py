"""Writing down a recording on this computer: the file read into samples, brought to the model's
rate, and the model run on it, whole or, past `WHOLE_SECONDS`, cut at its silences first.

onnx-asr, onnxruntime and PyAV are the `transcribe` extra, imported only here and only once a
recording comes: a home that never gets one never loads them. The model is loaded once, then kept.
"""

import importlib
import threading
from pathlib import Path
from typing import Any

from atlas.speech.errors import SpeechError
from atlas.speech.model import MODEL_NAME, QUANTIZATION, SAMPLE_RATE, WHOLE_SECONDS
from atlas.speech.store import ModelStore


class LocalEngine:
    """Parakeet v3 on this computer, from the store's folders."""

    def __init__(self, store: ModelStore) -> None:
        self._store = store
        self._lock = threading.Lock()
        self._model: Any = None
        self._vad: Any = None

    def transcribe(self, path: Path) -> str:
        """What the recording at `path` says. Blocking: run it in a thread. Raises
        `SpeechError`."""
        samples = read_samples(path)
        with self._lock:
            model = self._loaded()
            if len(samples) <= WHOLE_SECONDS * SAMPLE_RATE:
                said: Any = model.recognize(samples, sample_rate=SAMPLE_RATE)
                return str(said).strip()
            pieces = model.with_vad(self._vad_loaded()).recognize(samples, sample_rate=SAMPLE_RATE)
            return " ".join(str(piece.text).strip() for piece in pieces).strip()

    def _loaded(self) -> Any:
        if self._model is None:
            onnx_asr = _module("onnx_asr")
            try:
                self._model = onnx_asr.load_model(
                    MODEL_NAME, self._store.model_folder, quantization=QUANTIZATION
                )
            except Exception as error:  # onnx-asr's own errors and onnxruntime's
                raise SpeechError(f"The speech model could not be loaded: {error}") from error
        return self._model

    def _vad_loaded(self) -> Any:
        if self._vad is None:
            onnx_asr = _module("onnx_asr")
            try:
                self._vad = onnx_asr.load_vad("silero", self._store.vad_folder)
            except Exception as error:  # onnx-asr's own errors and onnxruntime's
                raise SpeechError(f"The speech model's VAD could not be loaded: {error}") from error
        return self._vad


def read_samples(path: Path) -> Any:
    """The recording as mono samples at the model's rate (a numpy array of float32), whatever its
    format: PyAV decodes it and FFmpeg's resampler brings it to the rate. Raises `SpeechError`
    for a file with no audio in it, or one that is not a recording."""
    av = _module("av")
    numpy = _module("numpy")
    pieces: list[Any] = []
    try:
        with av.open(str(path)) as container:
            streams = [stream for stream in container.streams if stream.type == "audio"]
            if not streams:
                raise SpeechError(f"{path.name} has no audio in it.")
            resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
            for frame in container.decode(streams[0]):
                pieces.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
            pieces.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))
    except av.FFmpegError as error:
        raise SpeechError(f"{path.name} could not be read as audio: {error}") from error
    if not pieces:
        return numpy.zeros(0, dtype="float32")
    return numpy.concatenate(pieces).astype("float32")


def _module(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as error:
        raise SpeechError(
            "Writing down audio here needs Atlas's transcribe extra, which is not installed: "
            "run the installer again, or `uv sync --all-extras`."
        ) from error
