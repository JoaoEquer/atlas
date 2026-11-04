"""The marketplace's I/O: GitHub over HTTPS, and the home's `marketplace/` folder.

- **Reading the marketplace:** its repository's archive at its branch, one request, unpacked in a
  folder thrown away after. The index read from it is kept in `marketplace/index.json`, the last
  one read well, which stands in when GitHub cannot be reached.
- **An item's code:** its repository's archive at its commit, the item's folder taken out of it.
  GitHub serves an archive of any commit, so no git and no clone are needed, and the container,
  which has no git, installs too.
- **One file** of a repository at a commit: an item's README or `SKILL.md`, for its page.
- **What was installed**, `marketplace/installed.json`: what each item was when it came, so it can
  be updated, and retired.

An archive is unpacked with the standard library's data filter: no file lands outside the folder,
and no link outside it, device or permission bit comes along. The limits hold whatever a repository
holds.
"""

import dataclasses
import hashlib
import io
import json
import logging
import shutil
import tarfile
import tempfile
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

import httpx2

from atlas.home import MarketplaceSettings, format_utc, parse_utc, replace_file
from atlas.marketplace.errors import MarketplaceError
from atlas.marketplace.model import (
    ITEMS_FOLDER,
    META_FILE,
    REMOVED_FILE,
    Index,
    Item,
    Kind,
    Retired,
    Secret,
    Source,
    Tier,
    parse_item,
    parse_meta,
    parse_removed,
)

INDEX_FILE = "index.json"
INSTALLED_FILE = "installed.json"
#: The most an archive may weigh, the files it may hold, and what they may weigh unpacked.
ARCHIVE_BYTES = 64 * 1024 * 1024
ARCHIVE_FILES = 20_000
UNPACKED_BYTES = 256 * 1024 * 1024
#: The most one file read for a page may weigh.
FILE_BYTES = 2 * 1024 * 1024
HTTP_SECONDS = 60.0
USER_AGENT = "Atlas marketplace"

_logger = logging.getLogger("atlas.marketplace")

#: Fetches an address: its body, or `None` when there is nothing there (HTTP 404). Raises
#: `MarketplaceError` when it cannot be reached or weighs more than the limit.
type Fetch = Callable[[str, int], bytes | None]


def fetch(url: str, limit: int) -> bytes | None:
    """GET `url` over HTTP, following redirects, the body held to `limit` bytes."""
    try:
        with (
            httpx2.Client(
                timeout=HTTP_SECONDS, follow_redirects=True, headers={"User-Agent": USER_AGENT}
            ) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code == 404:
                return None
            if response.status_code >= 400:
                raise MarketplaceError(f"{url} answered HTTP {response.status_code}.")
            data = bytearray()
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > limit:
                    raise MarketplaceError(f"{url} weighs more than {limit // 1024 // 1024} MiB.")
            return bytes(data)
    except httpx2.HTTPError as error:
        raise MarketplaceError(f"{url} could not be reached: {error}") from error


@dataclass(frozen=True, slots=True)
class Record:
    """An item installed from the marketplace, as it was when it came."""

    name: str
    kind: Kind
    #: The item's revision then: its commit, or its server entry's digest.
    revision: str
    #: What the home held right after: a skill's files, or the server's entry in `mcp.json`, as a
    #: digest; empty for a plugin, whose own consent sees its changes.
    digest: str
    installed_at: str
    title: str = ""
    summary: str = ""
    author: str = ""
    tier: Tier = Tier.COMMUNITY
    repo: str = ""
    #: The skills that brought this MCP server along, by name.
    needed_by: tuple[str, ...] = ()
    #: Why it was retired, once it was.
    retired: str = ""
    #: Where it came from (step 24), and the name it took in the home when not its own.
    source: Source = Source.ATLAS
    target: str = ""

    @property
    def home_name(self) -> str:
        return self.target or self.name


class MarketplaceStore:
    """The marketplace's repository on GitHub, and what this home keeps of it."""

    def __init__(self, folder: Path, settings: MarketplaceSettings, *, get: Fetch = fetch) -> None:
        self.folder = folder
        self.settings = settings
        self._get = get

    # -- GitHub -----------------------------------------------------------------------------------

    def _address(self, template: str, **parts: str) -> str:
        try:
            return template.format(**parts)
        except (KeyError, IndexError, ValueError) as error:
            raise MarketplaceError(
                f"The marketplace's address {template!r} is wrong: {error}."
            ) from error

    @contextmanager
    def archive(self, repo: str, ref: str) -> Generator[Path]:
        """A repository's files at `ref`, unpacked in a folder that is gone after."""
        url = self._address(self.settings.archive_url, repo=repo, ref=ref)
        data = self._get(url, ARCHIVE_BYTES)
        if data is None:
            raise MarketplaceError(f"GitHub has no {repo} at {ref}.")
        work = Path(tempfile.mkdtemp(prefix="atlas-marketplace-"))
        try:
            yield _unpack(data, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def file(self, repo: str, ref: str, path: str) -> str | None:
        """One file of a repository at `ref`, as text; `None` when there is none."""
        url = self._address(self.settings.raw_url, repo=repo, ref=ref, path=path)
        data = self._get(url, FILE_BYTES)
        return None if data is None else data.decode("utf-8", "replace")

    def read(self, now: datetime) -> Index:
        """Read the marketplace from GitHub, and keep it as the last read well."""
        settings = self.settings
        with self.archive(settings.repository, settings.branch) as root:
            index = read_index(root)
        self._keep(index, now)
        return index

    # -- the last read ----------------------------------------------------------------------------

    def cached(self) -> tuple[Index, datetime] | None:
        """The marketplace as last read well, and when; `None` when it never was, or another
        marketplace was."""
        try:
            data: object = json.loads((self.folder / INDEX_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            _logger.warning("The marketplace's last read cannot be read: %s", error)
            return None
        if not isinstance(data, dict):
            return None
        fields = cast("dict[str, Any]", data)
        if fields.get("repository") != self.settings.repository:
            return None
        try:
            items = {
                name: _item(entry)
                for name, entry in cast("dict[str, Any]", fields["items"]).items()
            }
            retired = {
                name: Retired(**entry)
                for name, entry in cast("dict[str, Any]", fields["retired"]).items()
            }
            index = Index(fields["name"], fields["description"], items, retired)
            return index, parse_utc(fields["read_at"])
        except (KeyError, TypeError, ValueError) as error:
            _logger.warning("The marketplace's last read is damaged: %s", error)
            return None

    def _keep(self, index: Index, now: datetime) -> None:
        data = {
            "repository": self.settings.repository,
            "read_at": format_utc(now),
            "name": index.name,
            "description": index.description,
            "items": {name: dataclasses.asdict(item) for name, item in index.items.items()},
            "retired": {name: dataclasses.asdict(r) for name, r in index.retired.items()},
        }
        self.folder.mkdir(parents=True, exist_ok=True)
        replace_file(self.folder / INDEX_FILE, (json.dumps(data, indent=2) + "\n").encode())

    # -- what was installed -----------------------------------------------------------------------

    def records(self) -> dict[str, Record]:
        try:
            data: object = json.loads((self.folder / INSTALLED_FILE).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise MarketplaceError(
                f"{self.folder / INSTALLED_FILE} cannot be read ({error}); fix or move it."
            ) from error
        records: dict[str, Record] = {}
        if isinstance(data, dict):
            for name, entry in cast("dict[str, Any]", data).items():
                try:
                    fields = dict(cast("Mapping[str, Any]", entry))
                    fields["kind"] = Kind(fields["kind"])
                    fields["tier"] = Tier(fields.get("tier", Tier.COMMUNITY))
                    fields["needed_by"] = tuple(fields.get("needed_by", ()))
                    fields["source"] = Source(fields.get("source", Source.ATLAS))
                    records[name] = Record(**fields)
                except (KeyError, TypeError, ValueError):
                    _logger.warning("An entry of %s is damaged: %s", INSTALLED_FILE, name)
        return records

    def save(self, record: Record) -> None:
        records = self.records()
        records[record.name] = record
        self._write(records)

    def forget(self, name: str) -> None:
        records = self.records()
        if records.pop(name, None) is not None:
            self._write(records)

    def _write(self, records: Mapping[str, Record]) -> None:
        data = {name: dataclasses.asdict(r) for name, r in sorted(records.items())}
        self.folder.mkdir(parents=True, exist_ok=True)
        replace_file(self.folder / INSTALLED_FILE, (json.dumps(data, indent=2) + "\n").encode())


class McpFile:
    """The home's `mcp.json`, as the marketplace adds and takes away its servers' entries: the
    rest of the file stays as it is, and a file that cannot be read is never written over."""

    def __init__(self, file: Path) -> None:
        self.file = file

    def _read(self) -> dict[str, Any]:
        try:
            text = self.file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"mcpServers": {}}
        except OSError as error:
            raise MarketplaceError(f"{self.file} cannot be read: {error}") from error
        try:
            data: object = json.loads(text) if text.strip() else {}
        except ValueError as error:
            raise MarketplaceError(
                f"{self.file} is not valid JSON ({error}); fix it before installing a server."
            ) from error
        if not isinstance(data, dict):
            raise MarketplaceError(f"{self.file} is not a JSON object; fix it first.")
        document = cast("dict[str, Any]", data)
        servers = document.setdefault("mcpServers", {})
        if not isinstance(servers, dict):
            raise MarketplaceError(f"{self.file}'s mcpServers is not an object; fix it first.")
        return document

    def servers(self) -> dict[str, Mapping[str, Any]]:
        """The entries by server name; none when the file cannot be read."""
        try:
            return dict(cast("dict[str, Mapping[str, Any]]", self._read()["mcpServers"]))
        except MarketplaceError:
            return {}

    def put(self, name: str, entry: Mapping[str, Any]) -> None:
        document = self._read()
        document["mcpServers"][name] = dict(entry)
        self._write(document)

    def drop(self, name: str) -> None:
        document = self._read()
        if document["mcpServers"].pop(name, None) is not None:
            self._write(document)

    def _write(self, document: Mapping[str, Any]) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        replace_file(self.file, (json.dumps(document, indent=2) + "\n").encode("utf-8"))


def entry_digest(entry: Mapping[str, Any]) -> str:
    """A server entry's digest, the same for the same entry however its keys are ordered."""
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode("utf-8")).hexdigest()


def read_index(root: Path) -> Index:
    """The marketplace in a repository's files: its format, its items and its retired ones. An
    item that cannot be read is left out and said; a broken `marketplace.yaml` or
    `removed.yaml` refuses the whole, since retiring is what keeps the user safe."""
    meta = root / META_FILE
    if not meta.is_file():
        raise MarketplaceError(f"There is no marketplace here: {META_FILE} is missing.")
    name, description = parse_meta(meta.read_text(encoding="utf-8"))
    removed = root / REMOVED_FILE
    retired = parse_removed(removed.read_text(encoding="utf-8")) if removed.is_file() else {}
    items: dict[str, Item] = {}
    problems: list[str] = []
    folder = root / ITEMS_FOLDER
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else ():
        try:
            item = parse_item(path.read_text(encoding="utf-8"), f"{ITEMS_FOLDER}/{path.name}")
        except (MarketplaceError, UnicodeDecodeError) as error:
            problems.append(str(error))
            continue
        items[item.name] = item
    for problem in problems:
        _logger.warning("An item of the marketplace was left out: %s", problem)
    return Index(name, description, items, retired, tuple(problems))


def subfolder(root: Path, path: str) -> Path:
    """The item's folder in its repository's files. Raises `MarketplaceError`."""
    folder = root.joinpath(*PurePosixPath(path).parts).resolve() if path else root.resolve()
    if not folder.is_relative_to(root.resolve()):
        raise MarketplaceError(f"{path} leaves the repository.")
    if not folder.is_dir():
        raise MarketplaceError(f"The repository has no folder {path}.")
    return folder


def _unpack(data: bytes, into: Path) -> Path:
    """Unpack a `.tar.gz` archive; its one top folder, as GitHub's archives have."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            if len(members) > ARCHIVE_FILES:
                raise MarketplaceError(f"The archive holds more than {ARCHIVE_FILES:,} files.")
            if sum(member.size for member in members) > UNPACKED_BYTES:
                raise MarketplaceError("The archive unpacks to more than 256 MiB.")
            archive.extractall(into, filter="data")
    except (tarfile.TarError, OSError, EOFError) as error:
        raise MarketplaceError(f"The archive cannot be unpacked: {error}") from error
    tops = [path for path in into.iterdir()]
    if len(tops) != 1 or not tops[0].is_dir():
        raise MarketplaceError("The archive does not hold one folder, as GitHub's do.")
    return tops[0]


def _item(entry: Mapping[str, Any]) -> Item:
    fields = dict(entry)
    fields["kind"] = Kind(fields["kind"])
    fields["tier"] = Tier(fields["tier"])
    fields["screenshots"] = tuple(fields.get("screenshots", ()))
    fields["secrets"] = tuple(Secret(**secret) for secret in fields.get("secrets", ()))
    fields["source"] = Source(fields.get("source", Source.ATLAS))
    return Item(**fields)
