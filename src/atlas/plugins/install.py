"""Installing a plugin by its address (step 16n).

**The addresses**: a git URL
(`https://`, `git@`, `ssh://`, `file://`), GitHub's `owner/repo`, either with `#subdir` for a
plugin in a folder of the repository (or `owner/repo/subdir`), and GitHub's
`.../tree/<ref>/<subdir>` pages. A folder on this computer too: copied, or cloned when it is a
repository. A `--ref` takes a branch, a tag or a commit; a reference given stays on update, a
branch is followed.

**What lands:** the plugin's folder in the home's `plugins/`, named after it, without
the repository's `.git`, and where it came from in `plugins/.install-metadata.json`. It arrives
off, and is turned on with consent as any other; one that declares something else after an update
waits for consent again, its fingerprint changed. Removing takes away only a plugin installed by
address: one the user wrote in the folder is theirs to delete. Its data folder stays (Atlas's
own choice, marked).

**From the marketplace** (step 16s): the marketplace fetches a plugin at its pinned commit itself
and hands the folder to `place`, which records it under `marketplace:<name>`; such a plugin is
updated through the marketplace, never from its address. A dependency missing is still said, not
installed.
"""

import json
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from atlas.home import utc_now
from atlas.plugins.model import MANIFEST
from atlas.plugins.service import PluginError, Plugins
from atlas.plugins.store import INSTALLED_FILE, OFFICIAL_SOURCE, Found, read_plugin

#: Where an install is put together before it takes its place, skipped by the plugin search.
_STAGING = ".installing"
#: How long a clone may take.
GIT_SECONDS = 300.0
#: What the record of a plugin installed from the marketplace says it came from, before the
#: item's name.
MARKETPLACE_SOURCE = "marketplace:"

_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_GITHUB_TREE = re.compile(
    r"^https://github\.com/(?P<repo>[^/]+/[^/#]+?)(?:\.git)?/tree/(?P<ref>[^/#]+)(?:/(?P<sub>[^#]+))?/?$"
)
_SHORT = re.compile(r"^(?P<repo>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?:/(?P<sub>[^#]+))?$")

_logger = logging.getLogger("atlas.plugins")


@dataclass(frozen=True, slots=True)
class Address:
    """Where a plugin is fetched from."""

    #: A git URL, or a folder on this computer.
    url: str
    #: The plugin's folder inside the repository; empty for its root.
    subdir: str = ""
    #: The branch, tag or commit; empty for the default branch.
    ref: str = ""
    #: `url` is a folder that is not a repository: copied as it is.
    folder: bool = False


@dataclass(frozen=True, slots=True)
class Installed:
    """A plugin installed by address, as recorded."""

    #: The address as the user gave it.
    source: str
    url: str
    subdir: str
    ref: str
    #: The commit installed; empty for a copied folder.
    revision: str
    installed_at: str


def parse_address(text: str, ref: str = "") -> Address:
    """The address the user typed. Raises `PluginError` for one that is none."""
    typed = text.strip()
    if not typed:
        raise PluginError("Give the plugin's address: a git URL, owner/repo or a folder.")
    address, _, subdir = typed.partition("#")
    local = Path(address).expanduser()
    if local.is_dir():
        folder = not (local / ".git").exists()
        return Address(str(local.resolve()), subdir.strip("/"), ref, folder=folder)
    tree = _GITHUB_TREE.match(address)
    if tree is not None:
        return Address(
            f"https://github.com/{tree['repo']}.git",
            (tree["sub"] or subdir).strip("/"),
            ref or tree["ref"],
        )
    if address.startswith(("https://", "http://", "git@", "ssh://", "file://")):
        if address.startswith(("http://", "file://")):
            _logger.warning("A plugin is fetched over an address that is not secure: %s", address)
        return Address(address, subdir.strip("/"), ref)
    short = _SHORT.match(address)
    if short is None:
        raise PluginError(
            f"{typed!r} is not an address Atlas reads: a git URL (https://, git@, ssh://), "
            "GitHub's owner/repo (with #folder for a plugin inside it), or a folder."
        )
    return Address(
        f"https://github.com/{short['repo'].removesuffix('.git')}.git",
        (short["sub"] or subdir).strip("/"),
        ref,
    )


def installed(plugins_folder: Path) -> dict[str, Installed]:
    """The plugins installed by address, by name."""
    file = plugins_folder / INSTALLED_FILE
    try:
        data: object = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        _logger.warning("%s cannot be read: %s", file, error)
        return {}
    found: dict[str, Installed] = {}
    if isinstance(data, dict):
        for name, entry in data.items():  # pyright: ignore[reportUnknownVariableType]
            try:
                found[str(name)] = Installed(**entry)  # pyright: ignore[reportUnknownArgumentType]
            except TypeError:
                continue
    return found


def install(
    plugins: Plugins,
    text: str,
    *,
    ref: str = "",
    force: bool = False,
    git: str = "git",
    clock: Callable[[], datetime] = utc_now,
) -> Found:
    """Fetch the plugin at `text` into the home's plugins folder, off. `force` replaces one of
    the same name the user installed. Raises `PluginError`."""
    address = parse_address(text, ref)
    found, revision = _fetch(plugins, address, git, replacing=None, force=force)
    keep_record(plugins.folder, found.name, Installed(text.strip(), address.url, address.subdir,
            address.ref, revision, clock().isoformat()))  # fmt: skip
    _logger.info("A plugin was installed.", extra={"plugin": found.name})
    return found


def update(plugins: Plugins, name: str, *, git: str = "git") -> tuple[Found, bool]:
    """Fetch an installed plugin again from where it came, on the reference it was installed on;
    with whether its commit or files changed. Raises `PluginError`."""
    record = installed(plugins.folder).get(name)
    if record is None:
        raise PluginError(
            f"{name} was not installed by address, so there is nowhere to update it from."
        )
    if record.source.startswith(MARKETPLACE_SOURCE):
        raise PluginError(
            f"{name} came from the marketplace: update it there, or with "
            f"`atlas marketplace update {name}`."
        )
    address = Address(record.url, record.subdir, record.ref, folder=not record.revision)
    found, revision = _fetch(plugins, address, git, replacing=name, force=True)
    changed = address.folder or revision != record.revision
    kept = Installed(
        record.source, record.url, record.subdir, record.ref, revision, record.installed_at
    )
    keep_record(plugins.folder, name, kept)
    return found, changed


def place(
    plugins: Plugins, folder: Path, record: Installed, *, replacing: str | None = None
) -> Found:
    """Put a plugin fetched elsewhere (the marketplace's, at its commit) in the home's plugins
    folder, checked as Atlas loads it, recorded as `record` says it came; it arrives off.
    `replacing` names the installed plugin it updates. Raises `PluginError`."""
    address = Address(str(folder), folder=True)
    found, _ = _fetch(plugins, address, "git", replacing=replacing, force=bool(replacing))
    keep_record(plugins.folder, found.name, record)
    _logger.info("A plugin was put in place.", extra={"plugin": found.name})
    return found


def remove(plugins: Plugins, name: str) -> Path:
    """Turn off and take away a plugin installed by address; its data folder stays, and is
    returned. Raises `PluginError`."""
    if name not in installed(plugins.folder):
        raise PluginError(
            f"{name} was not installed by address: its folder is yours, delete it yourself."
        )
    view = plugins.view(name)
    if view.enabled:
        plugins.disable(name)
    if not installed(plugins.folder)[name].source.startswith(OFFICIAL_SOURCE):
        # One of Atlas's own (step 26) keeps its files in the installation: only its record goes.
        _remove_tree(view.folder)
    forget(plugins.folder, name)
    _logger.info("A plugin was removed.", extra={"plugin": name})
    return plugins.data_folder(name)


def _fetch(
    plugins: Plugins, address: Address, git: str, *, replacing: str | None, force: bool
) -> tuple[Found, str]:
    """The plugin at `address`, checked as Atlas loads it, in its place; with its commit."""
    folder = plugins.folder
    folder.mkdir(parents=True, exist_ok=True)
    staging = folder / _STAGING
    staging.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=staging))
    try:
        revision = ""
        if address.folder:
            root = Path(address.url)
        else:
            root = work / "clone"
            revision = _clone(git, address, root)
        source = (root / address.subdir).resolve()
        if not source.is_relative_to(root.resolve()):
            raise PluginError(f"{address.subdir} leaves the repository.")
        if not source.is_dir():
            raise PluginError(f"There is no folder {address.subdir} in {address.url}.")
        name = _name(source)
        if replacing is not None and name != replacing:
            raise PluginError(f"{address.url} now holds the plugin {name}, not {replacing}.")
        staged = work / "plugin" / name
        shutil.copytree(
            source, staged, ignore=shutil.ignore_patterns(".git", "__pycache__"), symlinks=True
        )
        found = read_plugin(staged, bundled=False)
        if found.manifest is None:
            raise PluginError(f"{address.url} holds no plugin Atlas loads: {found.problem}")
        if replacing is None and any(
            view.name == name and view.bundled and view.installed for view in plugins.all()
        ):
            raise PluginError(
                f"Atlas's own plugin {name} is installed; this one cannot replace it."
            )
        target = folder / name
        if target.exists():
            ours = name in installed(folder)
            if not (force and ours):
                raise PluginError(
                    f"There is a plugin {name} in {target} already. "
                    + ("Update it with `atlas plugins update`." if ours else "It is yours: "
                       "move it away first.")
                )  # fmt: skip
            old = work / "old"
            target.rename(old)
        staged.rename(target)
        return read_plugin(target, bundled=False), revision
    finally:
        _remove_tree(work)


def _clone(git: str, address: Address, into: Path) -> str:
    """Clone `address` into `into`, on its reference; the commit checked out."""
    if address.ref and _COMMIT.match(address.ref):
        _git(git, "clone", "--no-checkout", "--", address.url, str(into))
        _git(git, "-C", str(into), "checkout", "--detach", address.ref)
    else:
        branch = ["--branch", address.ref] if address.ref else []
        _git(git, "clone", "--depth", "1", *branch, "--", address.url, str(into))
    return _git(git, "-C", str(into), "rev-parse", "HEAD").strip()


def _git(git: str, *arguments: str) -> str:
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    try:
        done = subprocess.run(  # noqa: S603 - git, with the user's address as an argument
            [git, *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_SECONDS,
            env=environment,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError as error:
        raise PluginError(
            "Installing by address needs git, which is not installed here."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise PluginError(f"git took more than {GIT_SECONDS:.0f} s.") from error
    if done.returncode != 0:
        said = done.stderr.strip().splitlines()
        raise PluginError(f"git failed: {said[-1] if said else f'exit {done.returncode}'}")
    return done.stdout


def _name(folder: Path) -> str:
    """The plugin's name: its manifest's, or its folder's when it has none, as Claude Code."""
    try:
        data: object = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return folder.name
    except (OSError, ValueError) as error:
        raise PluginError(f"{MANIFEST} cannot be read: {error}") from error
    name = data.get("name") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name.startswith("."):
        raise PluginError(f"{MANIFEST} has no name Atlas can use.")
    return name


def keep_record(plugins_folder: Path, name: str, kept: Installed) -> None:
    """Record where the plugin `name` came from."""
    records = installed(plugins_folder)
    records[name] = kept
    _write(plugins_folder, records)


def forget(plugins_folder: Path, name: str) -> None:
    """Forget where the plugin `name` came from."""
    records = installed(plugins_folder)
    records.pop(name, None)
    _write(plugins_folder, records)


def _write(plugins_folder: Path, records: dict[str, Installed]) -> None:
    plugins_folder.mkdir(parents=True, exist_ok=True)
    file = plugins_folder / INSTALLED_FILE
    text = json.dumps({n: asdict(r) for n, r in sorted(records.items())}, indent=2) + "\n"
    file.write_text(text, encoding="utf-8", newline="\n")


def _remove_tree(path: Path) -> None:
    """Remove a folder; a read-only file (git's objects on Windows) is made writable first."""

    def writable(function: Callable[..., object], failed: str, _error: BaseException) -> None:
        os.chmod(failed, stat.S_IWRITE)  # noqa: PTH101 - the path rmtree hands over
        function(failed)

    if path.exists():
        shutil.rmtree(path, onexc=writable)
