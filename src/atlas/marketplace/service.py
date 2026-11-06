"""The marketplace as this home sees it (step 16s): what it offers, what came from it, and
installing, updating, removing and retiring.

- **What is listed:** the items the marketplace offers; beside them what Atlas ships, its plugins,
  its executors and its skills, marked official; and what came from the marketplace that it no
  longer lists, so it can still be removed.
- **The last read stands in** when GitHub cannot be reached, and the listing says so. A read older
  than an hour is read again when the listing is asked for.
- **Installing:**
  - a skill goes into the home's library through `SkillLibrary.put`, which keeps a version before
    every write. The MCP servers it names that the home lacks come along, each as the item it is.
    A skill for other systems, or naming a server the marketplace does not have, is refused; a
    program it names that is missing is said, and the skill installs anyway;
  - an MCP server's entry goes into the home's `mcp.json`, where a server of the same name refuses
    it;
  - a plugin or an executor is checked as Atlas reads it and put in place off: turning it on is the
    consent the user always gives.
- **Updating** takes the item's new revision. A skill or an MCP entry changed in the home since it
  came is updated only when asked again with `force`, the skill's versions keeping what it was.
- **Removing:** a skill is archived (`atlas skills restore` brings it back), an MCP entry leaves
  `mcp.json`, a plugin is taken away with its data kept.
- **Retiring:** an installed item the marketplace retired stops as removing stops it, and stays
  listed as retired, with the reason.

Nothing of an item runs here: its page reads files, and a plugin's code runs only once the user
turns it on. `check`, for the marketplace's own CI, is the exception: there a plugin's Python is
loaded, when asked, in a home thrown away after.
"""

import json
import logging
import shutil
import sys
import threading
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Literal, cast

import yaml

from atlas.home import Home, format_utc, utc_now
from atlas.marketplace.errors import MarketplaceError
from atlas.marketplace.model import (
    ITEMS_FOLDER,
    META_FILE,
    OUTSIDE_FILE,
    PLUGIN_MARKS,
    REMOVED_FILE,
    Index,
    Item,
    Kind,
    Source,
    State,
    Tier,
    compiled,
    parse_item,
    parse_meta,
    parse_removed,
)
from atlas.marketplace.sources import Sources, publisher_picture
from atlas.marketplace.store import (
    MarketplaceStore,
    McpFile,
    Record,
    entry_digest,
    subfolder,
)
from atlas.plugins import (
    API_VERSION,
    MANIFEST,
    MARKETPLACE_SOURCE,
    OFFICIAL_FOLDER,
    Found,
    Installed,
    PluginError,
    Plugins,
    PluginView,
    install_official,
    is_official,
    official_installed,
    place_plugin,
    read_plugin,
    remove_plugin,
    validate,
)
from atlas.skills import (
    SKILL_FILE,
    Needs,
    Origin,
    Skill,
    SkillChangeError,
    SkillError,
    SkillLibrary,
    bundled_skills,
    files_digest,
    parse_skill,
    read_manifest,
    reset_bundled,
    skill_files,
)
from atlas.trust import Scope, scan_for_threats

#: A read older than this is read again when the listing is asked for.
STALE = timedelta(hours=1)
#: The most a skill from the marketplace may hold.
SKILL_FILES = 2_000
SKILL_BYTES = 20 * 1024 * 1024
#: Who the items Atlas ships are by.
TEAM = "Atlas"
_SYSTEM = {"win32": "windows", "darwin": "macos"}

#: The files of an outside skill read for what the memory refuses: what the model may read.
_TEXT_SUFFIXES = frozenset({".md", ".txt", ".markdown", ".json", ".yaml", ".yml"})

_logger = logging.getLogger("atlas.marketplace")


@dataclass(frozen=True, slots=True)
class Row:
    """An item as the marketplace's screens list it."""

    name: str
    kind: Kind
    title: str
    summary: str
    author: str
    license: str
    tier: Tier
    version: str
    state: State
    #: Atlas ships it: there is nothing to install, only to turn on.
    shipped: bool = False
    #: The marketplace has another revision than the one installed.
    update: bool = False
    image: str = ""
    #: Where its code is read, at its commit; an MCP server's home page.
    source_url: str = ""
    #: What keeps it from installing or running, or why it was retired.
    problem: str | None = None
    #: Where it comes from (step 24), when its source last published it, the installs its source
    #: counts, and the category it files it under.
    source: Source = Source.ATLAS
    updated: str = ""
    installs: int | None = None
    category: str = ""


@dataclass(frozen=True, slots=True)
class Listing:
    name: str
    description: str
    rows: list[Row]
    #: When the marketplace was last read well; `None` when it never was.
    read_at: datetime | None
    #: Why it could not be read now: the last read stands in, or nothing does.
    problem: str | None
    #: The outside sources that could not be read for this listing, a line each (step 24).
    source_problems: list[str] = field(default_factory=lambda: list[str]())


type NeedKind = Literal["program", "server", "system"]
#: Met; to be brought along from the marketplace (a server); or missing.
type NeedState = Literal["met", "brought", "missing"]


@dataclass(frozen=True, slots=True)
class Need:
    """Something a skill says it needs, and whether this home has it."""

    kind: NeedKind
    name: str
    state: NeedState


@dataclass(frozen=True, slots=True)
class SecretState:
    name: str
    description: str
    url: str
    #: The vault holds it. Its value is never read here.
    stored: bool


@dataclass(frozen=True, slots=True)
class Page:
    """An item's page: its row, its README, and what it needs."""

    row: Row
    #: Markdown: the README at the item's commit; a skill's own `SKILL.md`, which is what the model
    #: reads.
    readme: str
    #: Where the README's relative pictures are read, and its relative links lead, at the commit.
    picture_base: str
    link_base: str
    needs: list[Need] = field(default_factory=lambda: list[Need]())
    secrets: list[SecretState] = field(default_factory=lambda: list[SecretState]())
    screenshots: list[str] = field(default_factory=lambda: list[str]())
    homepage: str = ""
    #: An MCP server's entry, as JSON, exactly as it goes into `mcp.json`: what was reviewed.
    entry: str = ""


@dataclass(frozen=True, slots=True)
class Done:
    row: Row
    #: The items installed or updated, those brought along first.
    changed: list[str] = field(default_factory=lambda: list[str]())
    #: Not updated: the home's copy was changed since it came, so it waits for `force`.
    local_changes: bool = False


class Marketplace:
    """The marketplace and what this home installed from it."""

    def __init__(
        self,
        home: Home,
        store: MarketplaceStore,
        plugins: Plugins,
        library: SkillLibrary,
        *,
        stored: Callable[[], Collection[str]] = frozenset,
        clock: Callable[[], datetime] = utc_now,
        which: Callable[[str], str | None] = shutil.which,
        system: str = sys.platform,
        sources: Sources | None = None,
    ) -> None:
        """`stored` gives the vault's names, to say which secrets are there; `sources`, the
        outside catalogs (step 24), none in a test of Atlas's own repository alone."""
        self.store = store
        self.sources = sources
        self.plugins = plugins
        self.library = library
        self.mcp = McpFile(home.mcp_file)
        self._stored = stored
        self._clock = clock
        self._which = which
        self.system = _SYSTEM.get(system, "linux")
        #: Reentrant: installing reads the marketplace, and a read retires what it must.
        self._lock = threading.RLock()

    # -- reading ----------------------------------------------------------------------------------

    def index(self, *, refresh: bool = False) -> tuple[Index | None, datetime | None, str | None]:
        """The marketplace: read again when asked, never read, or stale; with when it was read
        and, when reading failed, why. What it retired is stopped as soon as it is read."""
        cached = self.store.cached()
        now = self._clock()
        if not refresh and cached is not None and now - cached[1] < STALE:
            return cached[0], cached[1], None
        try:
            index = self.store.read(now)
        except MarketplaceError as error:
            _logger.warning("The marketplace could not be read: %s", error)
            if cached is None:
                return None, None, str(error)
            return cached[0], cached[1], str(error)
        self.retire(index)
        return index, now, None

    def listing(self, *, refresh: bool = False, query: str = "") -> Listing:
        """What the marketplace lists, and with `query` what the outside sources find for it;
        with none, what they list first (the registry's servers updated lately)."""
        index, read_at, problem = self.index(refresh=refresh)
        records = self.store.records()
        views = {view.name: view for view in self.plugins.all()}
        servers = self.mcp.servers()
        rows = self._shipped(views)
        shipped = {row.name for row in rows}
        listed: set[str] = set()
        for item in index.items.values() if index is not None else ():
            if item.name in shipped:
                _logger.warning("The marketplace lists %s, which Atlas ships.", item.name)
                continue
            retired = index.retired_for(item) if index is not None else None
            record = records.get(item.name)
            if retired is not None and record is None:
                continue
            listed.add(item.name)
            rows.append(self._row(item, record, views, servers))
        outside: list[Row] = []
        source_problems: list[str] = []
        if self.sources is not None:
            found = self.sources.search(query, self._clock())
            source_problems = found.problems
            for item in found.items:
                retired = index.retired.get(item.name) if index is not None else None
                if item.name in listed or (retired is not None and item.name not in records):
                    continue
                listed.add(item.name)
                item = _marked(item, index)
                outside.append(self._row(item, records.get(item.name), views, servers))
        for record in records.values():
            if record.name not in listed and record.name not in shipped:
                rows.append(self._unlisted(record, views, servers))
        rows.sort(
            key=lambda row: (row.kind.value, row.tier is not Tier.OFFICIAL, row.title.lower())
        )
        name, description = (index.name, index.description) if index is not None else ("", "")
        return Listing(name, description, rows + outside, read_at, problem, source_problems)

    def row(self, name: str) -> Row:
        if _outside(name):
            return self._outside_row(name)
        found = next((row for row in self.listing().rows if row.name == name), None)
        if found is None:
            raise MarketplaceError(f"The marketplace has no item {name}.")
        return found

    def page(self, name: str) -> Page:
        """An item's page. Raises `MarketplaceError` when its README cannot be read."""
        row = self.row(name)
        if row.shipped:
            return self._shipped_page(row)
        index = self.index()[0]
        item = self._item(index, name)
        if item is None:
            return Page(row, row.summary, "", "")
        pictures = self.store.settings.raw_url.format(
            repo=item.repo, ref=item.sha, path=_inside(item.path, "")
        )
        links = f"https://github.com/{item.repo}/blob/{item.sha}/{_inside(item.path, '')}"
        stored = set(self._stored())
        secrets = [
            SecretState(s.name, s.description, s.url, s.name in stored) for s in item.secrets
        ]
        if item.kind is Kind.MCP:
            entry = json.dumps(dict(item.server), indent=2)
            return Page(row, "", "", "", secrets=secrets, homepage=item.homepage, entry=entry)
        needs: list[Need] = []
        if item.kind is Kind.SKILL:
            text = self.store.file(item.repo, item.sha, _inside(item.path, SKILL_FILE))
            readme = text or item.summary
            if text is not None:
                try:
                    skill = parse_skill(text, Path(item.name))
                except SkillError:
                    pass
                else:
                    readme = skill.body
                    needs = self._needs(skill.needs, index)
        else:
            readme = (
                self.store.file(item.repo, item.sha, _inside(item.path, "README.md"))
                or (self.store.file(item.repo, item.sha, "README.md") if item.path else None)
                or item.summary
            )
        return Page(
            row,
            readme,
            pictures,
            links,
            needs,
            secrets,
            list(item.screenshots),
            item.homepage,
        )

    # -- installing -------------------------------------------------------------------------------

    def install(self, name: str) -> Done:
        """Install an item, and what it needs from the marketplace. Raises `MarketplaceError`."""
        with self._lock:
            if name in bundled_skills() and not (self.library.folder / name).exists():
                reset_bundled(self.library, name)
                return Done(self.row(name), [name])
            if is_official(name) and name not in official_installed(self.plugins):
                # One of Atlas's own (step 26): from the installation's folder, off.
                try:
                    install_official(self.plugins, name)
                except PluginError as error:
                    raise MarketplaceError(str(error)) from error
                return Done(self.row(name), [name])
            index = self._index_for(name)
            item = self._offered(index, name)
            record = self.store.records().get(name)
            if record is not None and not record.retired and self._present(record):
                raise MarketplaceError(f"{name} is installed already; update it instead.")
            if item.problem is not None:
                raise MarketplaceError(f"{name} cannot install here: {item.problem}")
            changed = self._put(index, item, needed_by=None, replacing=False)
        return Done(self.row(name), changed)

    def update(self, name: str, *, force: bool = False) -> Done:
        """Bring an installed item to the marketplace's revision. A skill or an MCP entry changed
        in the home since it came is left as it is unless `force`; the answer says so."""
        with self._lock:
            record = self.store.records().get(name)
            if record is None or not self._present(record):
                raise MarketplaceError(f"{name} was not installed from the marketplace.")
            if record.retired:
                raise MarketplaceError(f"{name} was retired: {record.retired}")
            index = self._index_for(name)
            item = self._item(index, name)
            if item is None:
                raise MarketplaceError(f"The marketplace no longer lists {name}.")
            if item.revision == record.revision:
                return Done(self.row(name))
            if not force and self._changed_here(record):
                return Done(self.row(name), local_changes=True)
            changed = self._put(index, item, needed_by=None, replacing=True)
        return Done(self.row(name), changed)

    def remove(self, name: str) -> Row:
        """Take away an item installed from the marketplace. Raises `MarketplaceError`."""
        with self._lock:
            records = self.store.records()
            record = records.get(name)
            if record is None:
                raise MarketplaceError(f"{name} was not installed from the marketplace.")
            needing = [n for n in record.needed_by if n in records and self._present(records[n])]
            if needing and not record.retired:
                raise MarketplaceError(
                    f"{', '.join(needing)} {'needs' if len(needing) == 1 else 'need'} {name}: "
                    "remove it first."
                )
            self._stop(record)
            self.store.forget(name)
            if record.kind is Kind.SKILL:
                self._release(record.name, records)
        return self.row(name) if self._listed(name) else _gone(record)

    def retire(self, index: Index) -> list[str]:
        """Stop what this home installed that the marketplace retired; the names stopped."""
        stopped: list[str] = []
        with self._lock:
            for record in self.store.records().values():
                if record.retired:
                    continue
                found = index.retired.get(record.name) or next(
                    (
                        r
                        for r in index.retired.values()
                        if r.repo and r.repo.lower() == record.repo.lower()
                    ),
                    None,
                )
                if found is None:
                    continue
                try:
                    self._stop(record)
                except MarketplaceError as error:
                    _logger.warning("A retired item could not be stopped: %s", error)
                self.store.save(replace(record, retired=found.reason))
                _logger.warning("An item the marketplace retired was stopped: %s", record.name)
                stopped.append(record.name)
        return stopped

    # -- the work ---------------------------------------------------------------------------------

    def _current(self) -> Index:
        index, _, problem = self.index()
        if index is None:
            raise MarketplaceError(f"The marketplace could not be read: {problem}")
        return index

    def _item(self, index: Index | None, name: str) -> Item | None:
        """An item by name: of Atlas's own repository, or of an outside source read again now."""
        if _outside(name):
            return self.sources.item(name) if self.sources is not None else None
        return index.items.get(name) if index is not None else None

    def _outside_row(self, name: str) -> Row:
        records = self.store.records()
        views = {view.name: view for view in self.plugins.all()}
        servers = self.mcp.servers()
        try:
            item = self._item(None, name)
        except MarketplaceError:
            item = None
        if item is not None:
            item = _marked(item, self.index()[0])
            return self._row(item, records.get(name), views, servers)
        record = records.get(name)
        if record is None:
            raise MarketplaceError(f"The marketplace has no item {name}.")
        return self._unlisted(record, views, servers)

    def _index_for(self, name: str) -> Index | None:
        """The marketplace's index, which an outside item needs only for what was retired: it
        installs while Atlas's own repository cannot be read."""
        return self.index()[0] if _outside(name) else self._current()

    def _offered(self, index: Index | None, name: str) -> Item:
        item = self._item(index, name)
        if item is None:
            raise MarketplaceError(f"The marketplace has no item {name}.")
        item = _marked(item, index)
        retired = index.retired_for(item) if index is not None else None
        if retired is not None:
            raise MarketplaceError(f"{name} was retired from the marketplace: {retired.reason}")
        return item

    def _put(
        self, index: Index | None, item: Item, *, needed_by: str | None, replacing: bool
    ) -> list[str]:
        if item.kind is Kind.MCP:
            return self._put_server(item, needed_by=needed_by, replacing=replacing)
        if item.kind is Kind.SKILL:
            return self._put_skill(index, item, replacing=replacing)
        return self._put_plugin(item, replacing=replacing)

    def _put_skill(self, index: Index | None, item: Item, *, replacing: bool) -> list[str]:
        home = item.home_name
        with self.store.archive(item.repo, item.sha) as root:
            folder = subfolder(root, item.path)
            needs = read_skill(folder, home).needs
            files = skill_files(folder)
        if item.source is not Source.ATLAS:
            _refuse_threats(item.name, files)
        if needs.systems and self.system not in needs.systems:
            raise MarketplaceError(
                f"{item.name} works on {', '.join(needs.systems)}, not on {self.system}."
            )
        if not replacing and (self.library.folder / home).exists():
            raise MarketplaceError(f"You have a skill named {home} already.")
        servers = self.mcp.servers()
        brought: list[Item] = []
        for server in needs.servers:
            if server in servers:
                continue
            dependency = index.items.get(server) if index is not None else None
            if dependency is None or dependency.kind is not Kind.MCP:
                raise MarketplaceError(
                    f"{item.name} needs the MCP server {server}, which the marketplace does not "
                    "have. Add it to your mcp.json first."
                )
            brought.append(self._offered(index, server))
        changed: list[str] = []
        for dependency in brought:
            changed += self._put_server(dependency, needed_by=item.name, replacing=False)
        try:
            self.library.put(home, files)
        except SkillChangeError as error:
            raise MarketplaceError(str(error)) from error
        if not replacing and self.library.usage is not None:
            # The user's, as a skill they wrote: a row the review left under this name would
            # otherwise let the curator archive it.
            self.library.usage.created(home, Origin.USER, self._clock())
        for server in needs.servers:
            self._needed(server, item.name)
        digest = files_digest(skill_files(self.library.folder / home))
        self._record(item, digest)
        return [*changed, item.name]

    def _put_server(self, item: Item, *, needed_by: str | None, replacing: bool) -> list[str]:
        servers = self.mcp.servers()
        record = self.store.records().get(item.name)
        if item.home_name in servers and not replacing and record is None:
            raise MarketplaceError(f"Your mcp.json has a server named {item.home_name} already.")
        self.mcp.put(item.home_name, item.server)
        needing: set[str] = set(record.needed_by) if record is not None else set()
        if needed_by:
            needing.add(needed_by)
        self._record(item, entry_digest(item.server), needed_by=tuple(sorted(needing)))
        return [item.name]

    def _put_plugin(self, item: Item, *, replacing: bool) -> list[str]:
        with self.store.archive(item.repo, item.sha) as root:
            outside = item.source is not Source.ATLAS
            folder = _named(subfolder(root, item.path), item.home_name, optional=outside)
            found = read_plugin(folder, bundled=False)
            problem = plugin_problem(found, item)
            if problem is not None:
                raise MarketplaceError(problem)
            _refuse_compiled(folder)
            record = Installed(
                source=MARKETPLACE_SOURCE + item.name,
                url=f"https://github.com/{item.repo}.git",
                subdir=item.path,
                ref=item.sha,
                revision=item.sha,
                installed_at=format_utc(self._clock()),
            )
            try:
                place_plugin(
                    self.plugins, folder, record, replacing=item.home_name if replacing else None
                )
            except PluginError as error:
                raise MarketplaceError(str(error)) from error
        self._record(item, "")
        return [item.name]

    def _record(self, item: Item, digest: str, *, needed_by: tuple[str, ...] = ()) -> None:
        self.store.save(
            Record(
                name=item.name,
                kind=item.kind,
                revision=item.revision,
                digest=digest,
                installed_at=format_utc(self._clock()),
                title=item.title,
                summary=item.summary,
                author=item.author,
                tier=item.tier,
                repo=item.repo,
                needed_by=needed_by,
                source=item.source,
                target=item.target,
            )
        )

    def _needed(self, server: str, skill: str) -> None:
        record = self.store.records().get(server)
        if record is not None and skill not in record.needed_by:
            self.store.save(replace(record, needed_by=tuple(sorted({*record.needed_by, skill}))))

    def _release(self, skill: str, records: dict[str, Record]) -> None:
        for record in records.values():
            if skill in record.needed_by and record.name in self.store.records():
                needing = tuple(n for n in record.needed_by if n != skill)
                self.store.save(replace(record, needed_by=needing))

    def _stop(self, record: Record) -> None:
        if record.kind is Kind.SKILL:
            if (self.library.folder / record.home_name).is_dir():
                try:
                    self.library.archive(record.home_name)
                except SkillChangeError as error:
                    raise MarketplaceError(str(error)) from error
        elif record.kind is Kind.MCP:
            self.mcp.drop(record.home_name)
        elif (self.plugins.folder / record.home_name).is_dir():
            try:
                remove_plugin(self.plugins, record.home_name)
            except PluginError as error:
                raise MarketplaceError(str(error)) from error

    def _present(self, record: Record) -> bool:
        if record.kind is Kind.SKILL:
            return (self.library.folder / record.home_name).is_dir()
        if record.kind is Kind.MCP:
            return record.home_name in self.mcp.servers()
        return (self.plugins.folder / record.home_name).is_dir()

    def _changed_here(self, record: Record) -> bool:
        if record.kind is Kind.SKILL:
            folder = self.library.folder / record.home_name
            return files_digest(skill_files(folder)) != record.digest
        if record.kind is Kind.MCP:
            entry = self.mcp.servers().get(record.home_name)
            return entry is not None and entry_digest(entry) != record.digest
        return False

    def _listed(self, name: str) -> bool:
        return any(row.name == name for row in self.listing().rows)

    # -- rows -------------------------------------------------------------------------------------

    def _row(
        self,
        item: Item,
        record: Record | None,
        views: dict[str, PluginView],
        servers: Collection[str],
    ) -> Row:
        state, problem = self._state(item.home_name, item.kind, record, views, servers)
        if state is State.AVAILABLE and problem is None:
            problem = self._taken(item, views, servers) or item.problem
        if state in (State.INSTALLED, State.ON) and item.kind is Kind.MCP:
            missing = [s.name for s in item.secrets if s.name not in set(self._stored())]
            if missing:
                problem = f"Store {', '.join(missing)} to start it."
        return Row(
            name=item.name,
            kind=item.kind,
            title=item.title,
            summary=item.summary,
            author=item.author,
            license=item.license,
            tier=item.tier,
            version=item.version,
            state=state,
            update=record is not None
            and state not in (State.AVAILABLE, State.RETIRED)
            and self._revision(item) not in (None, record.revision),
            image=item.image
            or (publisher_picture(item.repo) if item.source is Source.ATLAS else ""),
            source_url=item.source_url,
            problem=problem,
            source=item.source,
            updated=item.updated,
            installs=item.installs,
            category=item.category,
        )

    def _revision(self, item: Item) -> str | None:
        """What installing `item` would take now: its revision, or for a skill of an outside
        source listed with no commit, its repository's newest, when known."""
        if item.revision or self.sources is None:
            return item.revision
        return self.sources.revision(item, self._clock())

    def _unlisted(
        self, record: Record, views: dict[str, PluginView], servers: Collection[str]
    ) -> Row:
        state, problem = self._state(record.home_name, record.kind, record, views, servers)
        return Row(
            name=record.name,
            kind=record.kind,
            title=record.title or record.name,
            summary=record.summary,
            author=record.author,
            license="",
            tier=record.tier,
            version="",
            state=state,
            problem=problem
            or (
                None
                if state is State.AVAILABLE or record.source is not Source.ATLAS
                else "The marketplace no longer lists it."
            ),
            source=record.source,
            image=self.sources.picture(record.name) if self.sources is not None else "",
        )

    def _state(
        self,
        name: str,
        kind: Kind,
        record: Record | None,
        views: dict[str, PluginView],
        servers: Collection[str],
    ) -> tuple[State, str | None]:
        if record is not None and record.retired:
            return State.RETIRED, f"Retired from the marketplace: {record.retired}"
        if record is None or not self._present(record):
            return State.AVAILABLE, None
        if kind in (Kind.SKILL, Kind.MCP):
            return State.INSTALLED, None
        view = views.get(name)
        if view is None:
            return State.AVAILABLE, None
        return (State.ON if view.enabled and view.consented else State.OFF), view.problem

    def _taken(
        self, item: Item, views: dict[str, PluginView], servers: Collection[str]
    ) -> str | None:
        if item.kind is Kind.SKILL and (self.library.folder / item.home_name).exists():
            return f"You have a skill named {item.home_name} already."
        if item.kind is Kind.MCP and item.home_name in servers:
            return f"Your mcp.json has a server named {item.home_name} already."
        if item.kind.has_code and item.kind is not Kind.SKILL and item.home_name in views:
            return f"You have a plugin named {item.home_name} already."
        return None

    def _shipped(self, views: dict[str, PluginView]) -> list[Row]:
        rows: list[Row] = []
        for view in views.values():
            manifest = view.manifest
            if not view.bundled or manifest is None:
                continue
            rows.append(
                Row(
                    name=view.name,
                    kind=Kind.EXECUTOR if manifest.executors else Kind.PLUGIN,
                    title=manifest.display_name or view.name,
                    summary=_sentence(manifest.description),
                    author=TEAM,
                    license=manifest.license or "MIT",
                    tier=Tier.OFFICIAL,
                    version=manifest.version,
                    state=State.AVAILABLE
                    if not view.installed
                    else State.ON
                    if view.enabled and view.consented
                    else State.OFF,
                    shipped=True,
                    problem=view.problem,
                )
            )
        manifest = read_manifest(self.library.folder)
        for name, folder in bundled_skills().items():
            home = self.library.folder / name
            try:
                skill = parse_skill((folder / SKILL_FILE).read_text(encoding="utf-8"), folder)
            except (OSError, SkillError):
                continue
            rows.append(
                Row(
                    name=name,
                    kind=Kind.SKILL,
                    title=name,
                    summary=_sentence(skill.description),
                    author=TEAM,
                    license="MIT",
                    tier=Tier.OFFICIAL,
                    version="",
                    state=State.INSTALLED
                    if home.is_dir() and name in manifest
                    else State.AVAILABLE,
                    shipped=True,
                )
            )
        return rows

    def _shipped_page(self, row: Row) -> Page:
        if row.kind is Kind.SKILL:
            folder = bundled_skills()[row.name]
            skill = parse_skill((folder / SKILL_FILE).read_text(encoding="utf-8"), folder)
            index = self.index()[0]
            return Page(row, skill.body, "", "", self._needs(skill.needs, index))
        view = self.plugins.view(row.name)
        manifest = view.manifest
        about = manifest.description if manifest is not None else row.summary
        lines = list(manifest.declaration()) if manifest is not None else []
        readme = about + ("\n\n" + "\n".join(f"- {line}" for line in lines) if lines else "")
        return Page(row, readme, "", "")

    def _needs(self, needs: Needs, index: Index | None) -> list[Need]:
        servers = self.mcp.servers()
        found: list[Need] = []
        for program in needs.programs:
            found.append(Need("program", program, "met" if self._which(program) else "missing"))
        for server in needs.servers:
            item = index.items.get(server) if index is not None else None
            offered = (
                item is not None
                and item.kind is Kind.MCP
                and index is not None
                and index.retired_for(item) is None
            )
            state: NeedState = "met" if server in servers else "brought" if offered else "missing"
            found.append(Need("server", server, state))
        if needs.systems:
            met = self.system in needs.systems
            found.append(Need("system", ", ".join(needs.systems), "met" if met else "missing"))
        return found


def _refuse_threats(name: str, files: Mapping[PurePosixPath, bytes]) -> None:
    """Refuse an outside skill whose text carries what the memory refuses (step 7): it would
    reach the model's context, and nobody reviewed it for Atlas."""
    for path, data in files.items():
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        hits = scan_for_threats(data.decode("utf-8", "replace"), Scope.CONTEXT)
        if hits:
            raise MarketplaceError(f"{name} was refused: {path} holds {hits[0]}.")


def _marked(item: Item, index: Index | None) -> Item:
    """An outside item as the marketplace's nightly check found it: one that does not work in
    Atlas says why, and does not install."""
    reason = index.incompatible.get(item.name) if index is not None else None
    if reason is None:
        return item
    return replace(item, problem=f"It does not work in Atlas: {reason}")


def _outside(name: str) -> bool:
    """Whether a name is an outside source's item, `<source>:<its id>`."""
    return ":" in name


def read_skill(folder: Path, name: str) -> Skill:
    """The skill in `folder`, checked as the marketplace takes one: named as its item, with
    nothing of a plugin, nothing compiled, and within the limits. Raises `MarketplaceError`."""
    file = folder / SKILL_FILE
    if not file.is_file():
        raise MarketplaceError(f"{name} has no {SKILL_FILE} in its folder.")
    try:
        skill = parse_skill(file.read_text(encoding="utf-8"), folder)
    except (SkillError, UnicodeDecodeError) as error:
        raise MarketplaceError(f"{name}'s {SKILL_FILE} cannot be read: {error}") from error
    if skill.name != name:
        raise MarketplaceError(f"The skill is named {skill.name}, not {name} as its item says.")
    marks = [mark for mark in PLUGIN_MARKS if (folder / mark).exists()]
    if marks:
        raise MarketplaceError(
            f"{name} holds {', '.join(marks)}, which make it a plugin: list it as a plugin."
        )
    files = [path for path in folder.rglob("*") if path.is_file()]
    if len(files) > SKILL_FILES or sum(path.stat().st_size for path in files) > SKILL_BYTES:
        raise MarketplaceError(f"{name} holds more than {SKILL_FILES:,} files or 20 MiB.")
    _refuse_compiled(folder)
    return skill


def plugin_problem(found: Found, item: Item) -> str | None:
    """Why a plugin's folder is not the item it says it is, or `None`."""
    manifest = found.manifest
    if manifest is None:
        return f"{item.name} cannot be read as a plugin: {found.problem}"
    if found.name != item.home_name:
        return f"The plugin is named {found.name}, not {item.home_name} as its item says."
    if manifest.api > API_VERSION:
        return (
            f"{item.name} was written for a newer Atlas (SDK {manifest.api}; this one is "
            f"{API_VERSION}). Update Atlas with `atlas update`."
        )
    if item.kind is Kind.EXECUTOR and not manifest.executors:
        return f"{item.name} registers no executor, so it is a plugin, not an executor."
    if item.kind is Kind.PLUGIN and manifest.executors:
        return f"{item.name} registers an executor: list it as an executor."
    return None


def check(
    paths: Sequence[Path],
    store: MarketplaceStore,
    *,
    root: Path | None = None,
    shipped: Collection[str] = (),
    load: bool = False,
    base: Path | None = None,
) -> list[str]:
    """What keeps the item files at `paths` out of the marketplace, a line each; empty when every
    one may enter (`atlas marketplace check`, its CI's gate). Each is read by the marketplace's
    rules, then its code is fetched at its commit and checked for its kind; with `load`, a
    plugin's Python is loaded as Atlas loads it, in a home thrown away after. `root` is the
    marketplace's own folder, whose `marketplace.yaml` and `removed.yaml` are read too.

    `base` is the marketplace as it is on its branch, for a pull request of someone other than its
    owner (step 24): such a one may not mark an item official, nor change what was retired or what
    the nightly check found (`removed.yaml`, `outside.yaml`), which the owner alone changes."""
    problems: list[str] = []
    if base is not None and root is not None:
        problems += _owner_only(root, base, paths)
    if root is not None:
        for file, parse in ((META_FILE, parse_meta), (REMOVED_FILE, parse_removed)):
            path = root / file
            if path.is_file():
                try:
                    parse(path.read_text(encoding="utf-8"))
                except MarketplaceError as error:
                    problems.append(str(error))
            elif file == META_FILE:
                problems.append(f"{META_FILE} is missing.")
    for path in paths:
        where = f"{ITEMS_FOLDER}/{path.name}"
        try:
            item = parse_item(path.read_text(encoding="utf-8"), where)
        except (MarketplaceError, OSError, UnicodeDecodeError) as error:
            problems.append(str(error))
            continue
        if item.name in shipped:
            problems.append(f"{where}: Atlas ships an item named {item.name}; choose another name.")
        if not item.kind.has_code:
            continue
        try:
            with store.archive(item.repo, item.sha) as code:
                folder = subfolder(code, item.path)
                if item.kind is Kind.SKILL:
                    read_skill(folder, item.name)
                    continue
                folder = _named(folder, item.name)
                found = read_plugin(folder, bundled=False)
                problem = plugin_problem(found, item)
                if problem is not None:
                    raise MarketplaceError(problem)
                _refuse_compiled(folder)
                if load:
                    validation = validate(folder)
                    problems.extend(f"{where}: {line}" for line in validation.problems)
        except MarketplaceError as error:
            problems.append(f"{where}: {error}")
    return problems


def _owner_only(root: Path, base: Path, paths: Sequence[Path]) -> list[str]:
    """What a pull request changed that only the marketplace's owner changes."""
    problems: list[str] = []
    for file in (REMOVED_FILE, OUTSIDE_FILE):
        now, before = root / file, base / file
        held = now.read_bytes() if now.is_file() else None
        if held != (before.read_bytes() if before.is_file() else None):
            problems.append(f"{file}: only the marketplace's owner changes it.")
    for path in paths:
        try:
            item = parse_item(path.read_text(encoding="utf-8"), f"{ITEMS_FOLDER}/{path.name}")
        except (MarketplaceError, OSError, UnicodeDecodeError):
            continue
        if item.tier is not Tier.OFFICIAL:
            continue
        was = base / ITEMS_FOLDER / path.name
        try:
            before = parse_item(was.read_text(encoding="utf-8"), f"{ITEMS_FOLDER}/{path.name}")
        except (MarketplaceError, OSError, UnicodeDecodeError):
            before = None
        if before is None or before.tier is not Tier.OFFICIAL:
            problems.append(
                f"{ITEMS_FOLDER}/{path.name}: only the marketplace's owner marks an item official."
            )
    return problems


def check_outside(sources: Sources, store: MarketplaceStore) -> dict[str, str]:
    """The plugins of Claude Code's directory that do not work in Atlas, by name, with why
    (`atlas marketplace outside`, the marketplace's nightly check, step 24): each fetched at the
    commit it would install from and read as Atlas reads a plugin, none of its code run. Raises
    `MarketplaceError` when the directory cannot be read."""
    found: dict[str, str] = {}
    for listed in sources.plugins():
        if listed.problem is not None:
            found[listed.name] = listed.problem
            continue
        try:
            item = sources.item(listed.name) or listed
            with store.archive(item.repo, item.sha) as code:
                folder = _named(subfolder(code, item.path), item.home_name, optional=True)
                problem = plugin_problem(read_plugin(folder, bundled=False), item)
                if problem is not None:
                    raise MarketplaceError(problem)
                _refuse_compiled(folder)
        except MarketplaceError as error:
            found[listed.name] = str(error).splitlines()[0]
    return found


def outside_text(found: Mapping[str, str]) -> str:
    """`outside.yaml` for what `check_outside` found, by name."""
    entries = [{"name": name, "reason": found[name]} for name in sorted(found)]
    head = (
        "# Written by `atlas marketplace outside`: the outside items that do not work in Atlas.\n"
    )
    return head + (
        yaml.safe_dump(entries, sort_keys=False, allow_unicode=True) if entries else "[]\n"
    )


def shipped_names() -> set[str]:
    """The names of what Atlas ships, which no item of the marketplace may take."""
    names = set(bundled_skills())
    names.update(path.name for path in OFFICIAL_FOLDER.iterdir() if path.is_dir())
    return names


def _named(folder: Path, name: str, *, optional: bool = False) -> Path:
    """The plugin in `folder`, copied beside it into a folder named `name`: Atlas reads a plugin
    only in a folder of its own name, and an archive's folder is named otherwise. Raises
    `MarketplaceError` when its manifest names it otherwise, or there is none to name it; an
    outside catalog names its plugins itself, so for one of them (`optional`) the manifest may be
    missing, as Claude Code allows."""
    try:
        data: object = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        if optional:
            data = {"name": name}
        else:
            raise MarketplaceError(
                f"{name} has no {MANIFEST}: a plugin of the marketplace names itself there."
            ) from error
    except (OSError, ValueError) as error:
        raise MarketplaceError(f"{name}'s {MANIFEST} cannot be read: {error}") from error
    said = cast("dict[str, object]", data).get("name") if isinstance(data, dict) else None
    if said != name:
        raise MarketplaceError(f"The plugin is named {said}, not {name} as its item says.")
    target = folder.parent / f".{name}.named" / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(folder, target, symlinks=True)
    return target


def _refuse_compiled(folder: Path) -> None:
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        with path.open("rb") as file:
            head = file.read(8)
        if compiled(path.name, head):
            relative = PurePosixPath(path.relative_to(folder).as_posix())
            raise MarketplaceError(
                f"{relative} is compiled, which no reviewer can read: the marketplace takes source."
            )


def _inside(folder: str, file: str) -> str:
    """A file's path in its repository, from the item's folder; the folder itself, with a slash,
    when `file` is empty."""
    if not folder:
        return file
    return f"{folder}/{file}"


def _sentence(text: str) -> str:
    """A description's first sentence, for a card."""
    flat = " ".join(text.split())
    cut = flat.find(". ")
    if 0 < cut < 160:
        return flat[: cut + 1]
    return flat if len(flat) <= 160 else flat[:157].rstrip() + "..."


def _gone(record: Record) -> Row:
    return Row(
        name=record.name,
        kind=record.kind,
        title=record.title or record.name,
        summary=record.summary,
        author=record.author,
        license="",
        tier=record.tier,
        version="",
        state=State.AVAILABLE,
    )
