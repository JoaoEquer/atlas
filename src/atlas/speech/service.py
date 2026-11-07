"""Writing down recordings for the whole server: an audio file a message carries, on any channel,
and what the user dictates. Who does it is the user's choice (`[voice] transcription`), read on
each recording, so a change on the settings screen applies to the next.

On this computer, the model is downloaded once, in the background, with its progress told; a
recording that comes before it is there keeps its note, and marks that one was wanted, so the app
asks once whether to download it (the size said first). Saying no is kept, at the root, for every
home.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

import httpx2

from atlas.speech.cloud import (
    GEMINI,
    OPENAI,
    CloudProvider,
    transcribe_gemini,
    transcribe_openai,
)
from atlas.speech.errors import SpeechError
from atlas.speech.local import LocalEngine
from atlas.speech.model import CREDIT, Transcription
from atlas.speech.store import ModelStore

#: How often a download's progress is told, at most.
PROGRESS_SECONDS = 0.5
#: The file at the root that keeps the user's no to the download.
DECLINED_FILE = "declined"

_logger = logging.getLogger("atlas.speech")


class Engine(Protocol):
    """What writes down a recording on this computer. Blocking."""

    def transcribe(self, path: Path) -> str: ...


@dataclass(frozen=True, slots=True)
class SpeechStatus:
    #: The user's choice, one of `Transcription`.
    transcription: str
    #: The model is on this computer, whole.
    present: bool
    #: Its download's size, in bytes.
    size: int
    #: The bytes downloaded so far while a download runs; `None` when none does.
    downloading: int | None
    #: A recording came for the model before it was there.
    wanted: bool
    #: The user said no to the download.
    declined: bool
    #: Why the last download failed; empty when it did not.
    error: str
    #: Whose the model is, and its license.
    credit: str


class Speech:
    """The server's writing down of recordings."""

    def __init__(
        self,
        store: ModelStore,
        choice: Callable[[], str],
        secret: Callable[[str], str | None],
        *,
        engine: Engine | None = None,
        on_changed: Callable[[], None] = lambda: None,
        transport: httpx2.BaseTransport | None = None,
        address: Callable[[str], str | None] = lambda _provider: None,
        openai: CloudProvider = OPENAI,
        gemini: CloudProvider = GEMINI,
    ) -> None:
        self._store = store
        self._choice = choice
        self._secret = secret
        self._engine: Engine = engine if engine is not None else LocalEngine(store)
        #: Told when the status changes, on the event loop: the server tells its clients.
        self.on_changed = on_changed
        self._transport = transport
        #: The address of Atlas's provider of a name (`openai`, `gemini`), as its model catalog
        #: has it, so a recording goes where the provider's other calls go; `None` for its own.
        self._address = address
        self._openai = openai
        self._gemini = gemini
        self._lock = threading.Lock()
        self._downloading: int | None = None
        self._download: asyncio.Task[None] | None = None
        self._wanted = False
        self._error = ""

    @property
    def transcription(self) -> Transcription:
        try:
            return Transcription(self._choice())
        except ValueError:
            return Transcription.LOCAL

    def status(self) -> SpeechStatus:
        with self._lock:
            downloading = self._downloading
        return SpeechStatus(
            transcription=self.transcription.value,
            present=self._store.present(),
            size=self._store.size(),
            downloading=downloading,
            wanted=self._wanted,
            declined=(self._store.folder / DECLINED_FILE).exists(),
            error=self._error,
            credit=CREDIT,
        )

    def download(self) -> bool:
        """Start downloading the model in the background, unless it is there or on its way;
        whether one started. A download takes back an earlier no."""
        if self._download is not None and not self._download.done():
            return False
        if self._store.present():
            return False
        (self._store.folder / DECLINED_FILE).unlink(missing_ok=True)
        self._error = ""
        with self._lock:
            self._downloading = 0
        self._download = asyncio.create_task(self._fetch(asyncio.get_running_loop()))
        self.on_changed()
        return True

    def decline(self) -> None:
        """The user's no to the download, kept: the app does not ask again."""
        self._store.folder.mkdir(parents=True, exist_ok=True)
        (self._store.folder / DECLINED_FILE).write_text("", encoding="utf-8")
        self.on_changed()

    async def wait(self) -> None:
        """Until the download in course ends (for the server's stop, and the tests)."""
        if self._download is not None:
            await asyncio.wait({self._download})

    async def transcribe(self, path: Path, media_type: str) -> str:
        """What the recording at `path` says, by whom the user chose. Raises `SpeechError`, also
        when the choice is nobody or the model is not here yet."""
        chosen = self.transcription
        if chosen is Transcription.OFF:
            raise SpeechError("Writing down recordings is off in Settings, Voice.")
        if chosen is Transcription.LOCAL:
            if not self._store.present():
                if not self._wanted:
                    self._wanted = True
                    self.on_changed()
                raise SpeechError(
                    "The speech model is not on this computer yet: download it in Settings, "
                    f"Voice ({self._store.size() // 1_000_000} MB)."
                )
            return await asyncio.to_thread(self._engine.transcribe, path)
        data = await asyncio.to_thread(path.read_bytes)
        if chosen is Transcription.OPENAI:
            openai = self._at(self._openai, "openai")
            return await transcribe_openai(
                openai, self._secret(openai.key), path.name, data, media_type
            )
        gemini = self._at(self._gemini, "gemini")
        return await transcribe_gemini(
            gemini, self._secret(gemini.key), path.name, data, media_type
        )

    def _at(self, provider: CloudProvider, name: str) -> CloudProvider:
        """The provider at the address Atlas's provider of that name has. Gemini's is its
        OpenAI-compatible one, `/openai` under the API this speaks."""
        found = (self._address(name) or "").rstrip("/").removesuffix("/openai")
        return replace(provider, api_url=found) if found else provider

    async def _fetch(self, loop: asyncio.AbstractEventLoop) -> None:
        told = 0.0

        def progress(done: int, _total: int) -> None:
            nonlocal told
            with self._lock:
                self._downloading = done
            if time.monotonic() - told >= PROGRESS_SECONDS:
                told = time.monotonic()
                loop.call_soon_threadsafe(self.on_changed)

        try:
            await asyncio.to_thread(self._store.download, progress, transport=self._transport)
        except SpeechError as error:
            self._error = str(error)
            _logger.warning("The speech model could not be downloaded: %s", error)
        finally:
            with self._lock:
                self._downloading = None
            self.on_changed()
