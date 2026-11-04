"""Speech: writing down what a recording says, on this computer or at a provider (step 26)."""

from atlas.speech.cloud import GEMINI, OPENAI, CloudProvider
from atlas.speech.errors import SpeechError
from atlas.speech.model import CREDIT, ModelFile, Transcription
from atlas.speech.service import Engine, Speech, SpeechStatus
from atlas.speech.store import ModelStore

__all__ = [
    "CREDIT",
    "GEMINI",
    "OPENAI",
    "CloudProvider",
    "Engine",
    "ModelFile",
    "ModelStore",
    "Speech",
    "SpeechError",
    "SpeechStatus",
    "Transcription",
]
