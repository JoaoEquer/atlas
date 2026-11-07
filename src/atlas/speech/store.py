"""The model on disk: whether it is there whole, and its download, each file checked against its
hash before it takes its place, so a broken or changed file is never loaded."""

import hashlib
import logging
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx2

from atlas.speech.errors import SpeechError
from atlas.speech.model import MODEL_FILES, MODEL_FOLDER, VAD_FILES, VAD_FOLDER, ModelFile

#: How long a read may stall before the download gives up.
READ_SECONDS = 60.0
_CHUNK = 1 << 20

_logger = logging.getLogger("atlas.speech")


class ModelStore:
    """The root's `speech/` folder: the model's files and the VAD's, each in a folder of its own."""

    def __init__(
        self, folder: Path, parts: Sequence[tuple[str, Sequence[ModelFile]]] | None = None
    ) -> None:
        self.folder = folder
        #: Each folder's name and its files: the model's and the VAD's, or a test's own.
        self._files = parts or ((MODEL_FOLDER, MODEL_FILES), (VAD_FOLDER, VAD_FILES))

    @property
    def model_folder(self) -> Path:
        return self.folder / MODEL_FOLDER

    @property
    def vad_folder(self) -> Path:
        return self.folder / VAD_FOLDER

    def present(self) -> bool:
        """Every file is there, at its size (its hash was checked when it was written)."""
        return all(
            _whole(folder / file.name, file) for folder, files in self._parts() for file in files
        )

    def download(
        self,
        progress: Callable[[int, int], None] = lambda _done, _total: None,
        *,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        """Download what is missing, telling `progress` the bytes done of the whole. Raises
        `SpeechError`: a file that does not match its hash is thrown away."""
        total = self.size()
        done = sum(
            file.size
            for folder, files in self._parts()
            for file in files
            if _whole(folder / file.name, file)
        )
        progress(done, total)
        with httpx2.Client(
            follow_redirects=True,
            timeout=httpx2.Timeout(30.0, read=READ_SECONDS),
            transport=transport,
        ) as client:
            for folder, files in self._parts():
                folder.mkdir(parents=True, exist_ok=True)
                for file in files:
                    target = folder / file.name
                    if _whole(target, file):
                        continue
                    done = _fetch(client, file, target, done, total, progress)
        _logger.info("The speech model was downloaded.")

    def _parts(self) -> Sequence[tuple[Path, Sequence[ModelFile]]]:
        return [(self.folder / name, files) for name, files in self._files]

    def size(self) -> int:
        """How many bytes the whole download takes."""
        return sum(file.size for _folder, files in self._parts() for file in files)


def _whole(path: Path, file: ModelFile) -> bool:
    try:
        return path.stat().st_size == file.size
    except OSError:
        return False


def _fetch(
    client: httpx2.Client,
    file: ModelFile,
    target: Path,
    done: int,
    total: int,
    progress: Callable[[int, int], None],
) -> int:
    """One file, into a `.part` beside its place, checked, then in place; the bytes done after."""
    part = target.with_name(target.name + ".part")
    hashed = hashlib.sha256()
    try:
        with client.stream("GET", file.url) as response:
            if response.status_code != 200:
                raise SpeechError(
                    f"The speech model's {file.name} could not be downloaded "
                    f"(HTTP {response.status_code})."
                )
            with part.open("wb") as out:
                for chunk in response.iter_bytes(_CHUNK):
                    out.write(chunk)
                    hashed.update(chunk)
                    done += len(chunk)
                    progress(done, total)
    except httpx2.HTTPError as error:
        part.unlink(missing_ok=True)
        raise SpeechError(f"The speech model could not be downloaded: {error}") from error
    if hashed.hexdigest() != file.sha256:
        part.unlink(missing_ok=True)
        raise SpeechError(
            f"The speech model's {file.name} is not the file Atlas expects; nothing was kept."
        )
    part.replace(target)
    return done
