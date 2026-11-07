"""What writing down audio on this computer needs: the model's files, each pinned to a commit and a
hash, and where a recording is sent instead when the user chose a provider. Pure.

**The model** (step 26, chosen on 03/11/2026): NVIDIA's Parakeet TDT 0.6B v3, multilingual, in
the int8 ONNX export onnx-asr reads, measured against Whisper small on 40 public Brazilian
Portuguese recordings (FLEURS pt_br, 9 minutes): 5.5% of the words wrong against 6.6%, at 31
times real time against 8. It is CC BY 4.0 (the screens say whose it is). Above ~25 seconds a
recording is cut at its silences first, by Silero's VAD (MIT), as the model hears at most about
that much at once.
"""

from dataclasses import dataclass
from enum import StrEnum


class Transcription(StrEnum):
    """Who writes down what a recording says, as the user chose (`[voice] transcription`)."""

    #: On this computer, with the model downloaded once (the default).
    LOCAL = "local"
    #: OpenAI's transcription, with the key the vault holds for its provider.
    OPENAI = "openai"
    #: Gemini's, likewise.
    GEMINI = "gemini"
    #: Nobody: a recording keeps its note.
    OFF = "off"


@dataclass(frozen=True, slots=True)
class ModelFile:
    """One file to download, where it comes from, and what it must be."""

    #: Its name in the model's folder.
    name: str
    url: str
    size: int
    sha256: str


_PARAKEET = (
    "https://huggingface.co/istupakov/parakeet-tdt-0.6b-v3-onnx/resolve/"
    "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce/"
)
_VAD = (
    "https://huggingface.co/istupakov/silero-vad-onnx/resolve/"
    "b3e3ee3cce4c11ceb63b1a0b229d916069c1ddf6/"
)

#: The folder of the model, inside the root's `speech/`, and its files.
MODEL_FOLDER = "parakeet-tdt-0.6b-v3-int8"
MODEL_FILES = (
    ModelFile(
        "config.json",
        _PARAKEET + "config.json",
        97,
        "666903c76b9798caf2c210afd4f6cd60b08a8dbf9800ec8d7a3bc0d2148ac466",
    ),
    ModelFile(
        "vocab.txt",
        _PARAKEET + "vocab.txt",
        93939,
        "d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d",
    ),
    ModelFile(
        "decoder_joint-model.int8.onnx",
        _PARAKEET + "decoder_joint-model.int8.onnx",
        18202004,
        "eea7483ee3d1a30375daedc8ed83e3960c91b098812127a0d99d1c8977667a70",
    ),
    ModelFile(
        "encoder-model.int8.onnx",
        _PARAKEET + "encoder-model.int8.onnx",
        652183999,
        "6139d2fa7e1b086097b277c7149725edbab89cc7c7ae64b23c741be4055aff09",
    ),
)
#: The VAD that cuts a long recording at its silences.
VAD_FOLDER = "silero-vad"
VAD_FILES = (
    ModelFile(
        "config.json",
        _VAD + "config.json",
        30,
        "1094039d370c82889582ba739a3d1caac5754c8b3a17a66a534200c9f72086e2",
    ),
    ModelFile(
        "silero_vad.onnx",
        _VAD + "silero_vad.onnx",
        2327524,
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3",
    ),
)
#: What onnx-asr calls the model, and the quantization of its files.
MODEL_NAME = "nemo-parakeet-tdt-0.6b-v3"
QUANTIZATION = "int8"
#: Who made it, and its license, for the screens.
CREDIT = "Parakeet TDT 0.6B v3 by NVIDIA, CC BY 4.0"

#: The rate the model hears, and the longest stretch it is given whole.
SAMPLE_RATE = 16000
WHOLE_SECONDS = 25.0
