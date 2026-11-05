"""GitHub's program, gh, fetched by the plugin when the computer has none (step 26b, revisited on
05/11/2026): the installer no longer brings it, so someone who never opened a terminal still
gets it, with one click on Connect.

The version and each build's SHA-256 are pinned here, as the installer pinned them; a download
whose digest differs is refused and nothing is kept. The archive is unpacked in a folder thrown
away after, and only the program comes out of it, into the plugin's data folder.
"""

import hashlib
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

VERSION = "2.101.0"
_RELEASE = f"https://github.com/cli/cli/releases/download/v{VERSION}/gh_{VERSION}"
#: How long the download may take, and the most it may weigh.
DOWNLOAD_SECONDS = 300
DOWNLOAD_BYTES = 80 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Build:
    """One build of gh: where it is, its digest, and the program's place inside its archive."""

    url: str
    sha256: str
    inside: str


#: gh's builds, by system and machine, as `sys.platform` and `platform.machine()` say them.
BUILDS: dict[tuple[str, str], Build] = {
    ("win32", "amd64"): Build(
        f"{_RELEASE}_windows_amd64.zip",
        "bc6c814367b193cd8e713611d61e36013c0ef843b8f516458fe3eda039192794",
        "bin/gh.exe",
    ),
    ("win32", "arm64"): Build(
        f"{_RELEASE}_windows_arm64.zip",
        "e6cbb2d4afdad3e70f3d38b8d1ebaa3a0870a897cfc0e4cf569826710b96b4fd",
        "bin/gh.exe",
    ),
    ("linux", "x86_64"): Build(
        f"{_RELEASE}_linux_amd64.tar.gz",
        "9bca2d1c16825f109907a23307628a2f0698fbf99662b73a5cf0b020293072b8",
        f"gh_{VERSION}_linux_amd64/bin/gh",
    ),
    ("linux", "aarch64"): Build(
        f"{_RELEASE}_linux_arm64.tar.gz",
        "b57e8063f18862647c9d22727c32e9da1b963f8bf9db648fe123a6975695640f",
        f"gh_{VERSION}_linux_arm64/bin/gh",
    ),
    ("darwin", "x86_64"): Build(
        f"{_RELEASE}_macOS_amd64.zip",
        "a6fd66c88e2f07d6e4e058173db341d07dd74d58cf8f19ae668293d2bb614ca3",
        f"gh_{VERSION}_macOS_amd64/bin/gh",
    ),
    ("darwin", "arm64"): Build(
        f"{_RELEASE}_macOS_arm64.zip",
        "e4303e39d8f07141c4bad4b99b01079f05029c59b27076e8fbc825c985ecdd8b",
        f"gh_{VERSION}_macOS_arm64/bin/gh",
    ),
}


class FetchError(Exception):
    """gh could not be fetched; the message says why, and what to do instead."""


def build() -> Build | None:
    """This computer's build of gh, or `None` when there is none for it."""
    return BUILDS.get((sys.platform, platform.machine().lower()))


def fetched(folder: Path) -> Path | None:
    """The gh this plugin fetched into `folder` before, at its pinned version, or `None`."""
    found = build()
    if found is None:
        return None
    program = folder / f"gh-{VERSION}" / Path(found.inside).name
    return program if program.is_file() else None


def fetch(folder: Path, *, get: "type[urllib.request.OpenerDirector] | None" = None) -> Path:
    """Fetch gh into `folder`, checked against its pinned digest; the program's path. Raises
    `FetchError`."""
    found = build()
    if found is None:
        raise FetchError(
            f"There is no build of gh for this computer ({sys.platform}, {platform.machine()}): "
            "install it from cli.github.com."
        )
    kept = fetched(folder)
    if kept is not None:
        return kept
    with tempfile.TemporaryDirectory(prefix="atlas-gh-") as scratch:
        archive = Path(scratch) / Path(found.url).name
        _download(found.url, archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != found.sha256:
            raise FetchError(
                "The gh downloaded is not the one Atlas expects (its digest differs), so it was "
                "not kept. Try again later, or install gh from cli.github.com."
            )
        source = _unpack(archive, Path(scratch) / "unpacked", found.inside)
        target = folder / f"gh-{VERSION}"
        target.mkdir(parents=True, exist_ok=True)
        program = target / source.name
        shutil.copyfile(source, program)
    program.chmod(program.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return program


def _download(url: str, into: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "Atlas GitHub plugin"})  # noqa: S310 - a pinned https address
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_SECONDS) as answer:  # noqa: S310
            data = answer.read(DOWNLOAD_BYTES + 1)
    except OSError as error:
        raise FetchError(f"gh could not be downloaded from github.com: {error}") from error
    if len(data) > DOWNLOAD_BYTES:
        raise FetchError("The gh download weighs more than expected, so it was not kept.")
    into.write_bytes(data)


def _unpack(archive: Path, into: Path, inside: str) -> Path:
    """Only the program out of the archive; raises `FetchError` when it is not there."""
    into.mkdir()
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as opened:
                opened.extract(inside, into)
        else:
            with tarfile.open(archive) as opened:
                opened.extract(inside, into, filter="data")
    except (KeyError, OSError, zipfile.BadZipFile, tarfile.TarError) as error:
        raise FetchError(f"The gh download could not be unpacked: {error}") from error
    return into / inside
