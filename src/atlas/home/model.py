"""The home's layout and boundary, the typed settings, and migrations. Pure data, no I/O.

**The boundary.** Every entry the code writes at the top of the home has a declared class, and
a test fails when the code writes one that is not declared. Atlas V3 learned this late: its backup
copied a folder that mixed the user's data with the vault and the push key, and a restore would
have put an old vault over the current one. Here the classes exist before any backup does:

- `user`: the user's data. A backup carries it, and a restore brings it back.
- `machine`: bound to this machine (the vault and its key). It never travels, and a restore never
  writes it.
- `diagnostic`: logs. They stay on this machine and never travel.
- `homes`: the default home's `homes/`, the other homes of this machine. Each is a home with its
  own boundary, backed up on its own.
"""

import hashlib
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

#: The server listens only on this machine unless told otherwise (docs/ARCHITECTURE.md, Server).
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18790

CONFIG_FILE = "config.toml"
MODELS_FILE = "models.toml"
DATABASE_FILE = "atlas.db"
SECRETS_FOLDER = "secrets"
SPILL_FOLDER = "spill"
COMMANDS_FOLDER = "commands"
#: Atlas's own Chrome profile and the browser engine's files (step 14).
BROWSER_FOLDER = "browser"
SKILLS_FOLDER = "skills"
#: The home's bots, one folder each (step 13b).
BOTS_FOLDER = "bots"
MCP_FILE = "mcp.json"
MEMORY_FOLDER = "memory"
#: The main agent's identity.
SOUL_FILE = "SOUL.md"
#: The home's picture, beside its name in the app (home/picture.py).
PICTURE_FILE = "picture.png"
DOCUMENTS_FOLDER = "documents"
#: The workspaces' worktrees, one folder per project (step 21).
WORKTREES_FOLDER = "worktrees"
#: Each routine's probe's own folder, where it keeps what it saw (step 23).
PROBES_FOLDER = "probes"
#: The user's dictations, each recording with what it said (step 26).
DICTATIONS_FOLDER = "dictations"
#: The speech model, downloaded once for every home of the machine, at the root (step 26).
SPEECH_FOLDER = "speech"
#: The files the user attached to messages, one folder each (step 16g).
ATTACHMENTS_FOLDER = "attachments"
LOGS_FOLDER = "logs"
#: Plugins the user added, one folder each (docs/ARCHITECTURE.md, Plugins).
PLUGINS_FOLDER = "plugins"
#: Each plugin's own data, kept across its updates (Claude Code's `${CLAUDE_PLUGIN_DATA}`).
PLUGIN_DATA_FOLDER = "plugin-data"
#: What was installed from the marketplace, and the marketplace as last read (step 16s).
MARKETPLACE_FOLDER = "marketplace"
#: The system home folder of the user Atlas runs as, when it lives in the home: the container's
#: `HOME=/data/home`, so what the agent makes outlives a new image (ARCHITECTURE.md, Container).
USER_HOME_FOLDER = "home"
#: Inside a container's user home folder, the logins of the programs Atlas ships skills for (step
#: 14b): `gh`, `gws` and `gcloud` keep their tokens in files where there is no keyring, so these
#: folders stay on this machine although the folder around them travels.
MACHINE_INSIDE_USER_HOME: tuple[str, ...] = (".config/gh", ".config/gws", ".config/gcloud")
#: The other homes of this machine, inside the default one.
HOMES_FOLDER = "homes"
#: Which home opens when none is named.
ACTIVE_HOME_FILE = "active_home"
#: Backups taken before a migration.
BACKUPS_FOLDER = "backups"
#: Held by the server serving the home, so a second one is refused.
SERVER_LOCK_FILE = "server.lock"
#: Whether the home is always on and open to the user's machines, and its port (step 15).
ALWAYS_ON_FILE = "always_on.json"
#: The name of the home at the root, the one there was before any other.
DEFAULT_HOME = "default"
#: A home's name: lowercase letters, digits, `-` and `_`, up to 64.
HOME_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class EntryClass(StrEnum):
    """Whether an entry of the home travels with the user or stays on this machine."""

    USER = "user"
    MACHINE = "machine"
    DIAGNOSTIC = "diagnostic"
    HOMES = "homes"


@dataclass(frozen=True, slots=True)
class Entry:
    """One name at the top of the home, and what it is."""

    name: str
    is_folder: bool
    entry_class: EntryClass
    why: str


LAYOUT: tuple[Entry, ...] = (
    Entry(CONFIG_FILE, False, EntryClass.USER, "the settings the user chose"),
    Entry(MODELS_FILE, False, EntryClass.USER, "providers and models the user added or corrected"),
    Entry(DATABASE_FILE, False, EntryClass.USER, "every subject's tables"),
    Entry(f"{DATABASE_FILE}-wal", False, EntryClass.USER, "the database's write-ahead log"),
    Entry(f"{DATABASE_FILE}-shm", False, EntryClass.USER, "the database's shared-memory index"),
    Entry(
        SECRETS_FOLDER,
        True,
        EntryClass.MACHINE,
        "the vault and its key; credentials stay on the machine that holds the key",
    ),
    Entry(LOGS_FOLDER, True, EntryClass.DIAGNOSTIC, "JSON log lines"),
    Entry(
        MCP_FILE,
        False,
        EntryClass.USER,
        "the MCP servers; a backup carries them",
    ),
    Entry(SKILLS_FOLDER, True, EntryClass.USER, "the user's skills, one folder each"),
    Entry(MEMORY_FOLDER, True, EntryClass.USER, "the profile and the memory the agent keeps"),
    Entry(SOUL_FILE, False, EntryClass.USER, "the main agent's identity, the user's to write"),
    Entry(PICTURE_FILE, False, EntryClass.USER, "the home's picture, beside its name in the app"),
    Entry(PLUGINS_FOLDER, True, EntryClass.USER, "the plugins the user added, one folder each"),
    Entry(
        PLUGIN_DATA_FOLDER,
        True,
        EntryClass.USER,
        "each plugin's own data, one folder each, kept across its updates",
    ),
    Entry(
        MARKETPLACE_FOLDER,
        True,
        EntryClass.USER,
        "what was installed from the marketplace, so it can be updated, and the marketplace as "
        "last read",
    ),
    Entry(
        BOTS_FOLDER,
        True,
        EntryClass.USER,
        "the home's bots, one folder each: its definition, its memory and its skills",
    ),
    Entry(
        ATTACHMENTS_FOLDER,
        True,
        EntryClass.USER,
        "the files the user attached to messages, one folder each: a restored conversation still "
        "opens them",
    ),
    Entry(
        DOCUMENTS_FOLDER,
        True,
        EntryClass.MACHINE,
        "the text of the user's documents, converted for search; rebuilt from the files, and it "
        "holds what they say",
    ),
    Entry(
        SPILL_FOLDER,
        True,
        EntryClass.MACHINE,
        "tool results too large for the context, kept a day; they may hold secrets",
    ),
    Entry(
        COMMANDS_FOLDER,
        True,
        EntryClass.MACHINE,
        "the output of commands run in the background, kept a day; it may hold secrets",
    ),
    Entry(
        BROWSER_FOLDER,
        True,
        EntryClass.MACHINE,
        "Atlas's own Chrome profile, with the sites the user signed into, and the browser's "
        "workspaces; logins stay on this machine",
    ),
    Entry(
        USER_HOME_FOLDER,
        True,
        EntryClass.USER,
        "in a container, the user's home folder, where the workspace starts: what the agent made",
    ),
    Entry(
        WORKTREES_FOLDER,
        True,
        EntryClass.MACHINE,
        "the workspaces' worktrees, copies of the user's repositories each on a branch of its own; "
        "their work goes back through git, not a backup",
    ),
    Entry(
        PROBES_FOLDER,
        True,
        EntryClass.USER,
        "each routine's probe's own folder, where it keeps what it already saw; lost, a probe "
        "hears everything again as new",
    ),
    Entry(
        DICTATIONS_FOLDER,
        True,
        EntryClass.USER,
        "what the user dictated, each recording with its words, kept until deleted",
    ),
    Entry(
        SPEECH_FOLDER,
        True,
        EntryClass.MACHINE,
        "the model that writes down recordings on this computer, at the root for every home; "
        "downloaded again where it is missing",
    ),
    Entry(HOMES_FOLDER, True, EntryClass.HOMES, "the other homes of this machine"),
    Entry(ACTIVE_HOME_FILE, False, EntryClass.MACHINE, "which home opens here when none is named"),
    Entry(
        BACKUPS_FOLDER,
        True,
        EntryClass.MACHINE,
        "backups taken before a migration, kept to undo it here; each already holds the home",
    ),
    Entry(SERVER_LOCK_FILE, False, EntryClass.MACHINE, "held while a server serves the home"),
    Entry(
        ALWAYS_ON_FILE,
        False,
        EntryClass.MACHINE,
        "whether the home's server runs as this machine's service, open to the user's machines, "
        "and on which port; a service of another machine is no service here",
    ),
)


def classify(name: str, *, is_folder: bool) -> Entry | None:
    """The declared entry for a name at the top of the home, or `None` if nobody declared it.

    `None` means "not the user's data": whatever copies or restores the home must skip it.
    """
    for entry in LAYOUT:
        if entry.name == name and entry.is_folder == is_folder:
            return entry
    return None


def unclassified(entries: dict[str, bool]) -> list[str]:
    """The names (mapped to "is a folder") that the layout does not declare. Folders end in `/`."""
    return sorted(
        f"{name}/" if is_folder else name
        for name, is_folder in entries.items()
        if classify(name, is_folder=is_folder) is None
    )


@dataclass(frozen=True, slots=True)
class Home:
    """Where one user's state lives, and the place of each entry inside it."""

    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / CONFIG_FILE

    @property
    def models_file(self) -> Path:
        return self.root / MODELS_FILE

    @property
    def database_file(self) -> Path:
        return self.root / DATABASE_FILE

    @property
    def secrets_folder(self) -> Path:
        return self.root / SECRETS_FOLDER

    @property
    def logs_folder(self) -> Path:
        return self.root / LOGS_FOLDER

    @property
    def spill_folder(self) -> Path:
        return self.root / SPILL_FOLDER

    @property
    def commands_folder(self) -> Path:
        return self.root / COMMANDS_FOLDER

    @property
    def browser_folder(self) -> Path:
        return self.root / BROWSER_FOLDER

    @property
    def skills_folder(self) -> Path:
        return self.root / SKILLS_FOLDER

    @property
    def bots_folder(self) -> Path:
        return self.root / BOTS_FOLDER

    @property
    def mcp_file(self) -> Path:
        return self.root / MCP_FILE

    @property
    def memory_folder(self) -> Path:
        return self.root / MEMORY_FOLDER

    @property
    def soul_file(self) -> Path:
        return self.root / SOUL_FILE

    @property
    def picture_file(self) -> Path:
        return self.root / PICTURE_FILE

    @property
    def always_on_file(self) -> Path:
        return self.root / ALWAYS_ON_FILE

    @property
    def attachments_folder(self) -> Path:
        return self.root / ATTACHMENTS_FOLDER

    @property
    def plugins_folder(self) -> Path:
        return self.root / PLUGINS_FOLDER

    @property
    def plugin_data_folder(self) -> Path:
        return self.root / PLUGIN_DATA_FOLDER

    @property
    def marketplace_folder(self) -> Path:
        return self.root / MARKETPLACE_FOLDER

    @property
    def documents_folder(self) -> Path:
        return self.root / DOCUMENTS_FOLDER

    @property
    def worktrees_folder(self) -> Path:
        return self.root / WORKTREES_FOLDER

    @property
    def probes_folder(self) -> Path:
        return self.root / PROBES_FOLDER

    @property
    def dictations_folder(self) -> Path:
        return self.root / DICTATIONS_FOLDER

    @property
    def speech_folder(self) -> Path:
        """The speech model's folder: the root's, as only the root's is used."""
        return self.root / SPEECH_FOLDER

    @property
    def homes_folder(self) -> Path:
        return self.root / HOMES_FOLDER

    @property
    def active_home_file(self) -> Path:
        return self.root / ACTIVE_HOME_FILE

    @property
    def backups_folder(self) -> Path:
        return self.root / BACKUPS_FOLDER

    @property
    def server_lock_file(self) -> Path:
        return self.root / SERVER_LOCK_FILE


def home_name_problem(name: str) -> str | None:
    """Why `name` cannot name a home, or `None`."""
    if name == DEFAULT_HOME:
        return None
    if not HOME_NAME.fullmatch(name):
        return (
            f"{name!r} is not a home name. Use lowercase letters, digits, - and _ (up to 64, "
            "starting with a letter or digit), such as work."
        )
    return None


type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LOG_LEVELS: tuple[LogLevel, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")


class SecretText:
    """A secret held in memory. Its `repr` and `str` are masked, so printing settings is safe."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """The secret itself. Call it only where the secret is used."""
        return self._value

    def __repr__(self) -> str:
        return "SecretText('**********')"

    def __str__(self) -> str:
        return "**********"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SecretText) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True, slots=True)
class ServerSettings:
    #: The address the server listens on: this machine only, unless told otherwise. `0.0.0.0`
    #: belongs only inside a container or behind a firewall.
    host: str = DEFAULT_HOST
    #: The port the server listens on. `0` lets the system choose one.
    port: int = DEFAULT_PORT
    #: What a client presents to be let in, from `ATLAS_SERVER_TOKEN`, set by whoever starts the
    #: server. Unset, the server makes a fresh one nobody knows.
    token: SecretText | None = None


@dataclass(frozen=True, slots=True)
class LogSettings:
    #: The least serious lines the log keeps: `DEBUG`, `INFO`, `WARNING` or `ERROR`.
    level: LogLevel = "INFO"
    #: Also write `logs/atlas.log`. A container usually turns it off and reads stderr instead.
    file: bool = True


@dataclass(frozen=True, slots=True)
class VaultSettings:
    #: The vault key itself (`AGE-SECRET-KEY-1...`). Only from the environment, never from a file.
    key: SecretText | None = None
    #: A file holding the vault key, such as a Docker secret mounted read-only.
    key_file: Path | None = None


@dataclass(frozen=True, slots=True)
class ModelSettings:
    #: The provider of the conversation's model, such as `openai` or `openrouter`.
    provider: str | None = None
    #: The model's id at that provider. Unset, the provider's default model answers.
    name: str | None = None
    #: How hard a reasoning model thinks (`low`, `medium`, `high`...). Unset, the model decides.
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class TrustSettings:
    #: Commands the agent must never run, as shell-style globs (`git push --force*`), matched
    #: case-insensitively; blocked even in auto mode.
    deny: tuple[str, ...] = ()
    #: More commands that always ask the user first, as shell-style globs (`git push*`), on top of
    #: the outward actions Atlas asks about on its own. They ask even in auto mode.
    ask: tuple[str, ...] = ()
    #: Globs of commands Atlas's own outward rules leave alone (`gh issue comment*`): the user's
    #: way to take an action off the default list. The user's `ask` rules still apply.
    ask_skip: tuple[str, ...] = ()
    #: Seconds an approval waits for the user before it counts as refused (60 was too tight for a
    #: phone notification).
    approval_timeout: int = 300


@dataclass(frozen=True, slots=True)
class MemorySettings:
    #: The characters `memory/USER.md` may hold, from 500 to 20,000: who the user is. Every model
    #: call carries it, and lowered under what is written it cuts nothing.
    user_limit: int = 1_375
    #: The characters `memory/MEMORY.md` may hold, from 500 to 20,000: the agent's notes. Every
    #: model call carries it, and lowered under what is written it cuts nothing.
    memory_limit: int = 2_200


@dataclass(frozen=True, slots=True)
class WorkspaceSettings:
    #: Where the file tools and commands start. Unset, the user's home folder.
    folder: Path | None = None


@dataclass(frozen=True, slots=True)
class TimeSettings:
    #: The IANA zone routines run in (`America/Sao_Paulo`). Unset, the system's.
    zone: str | None = None


@dataclass(frozen=True, slots=True)
class BackupSettings:
    #: Where the server writes the home's backup on its own (step 20): a home in a machine that may
    #: be lost (a cloud computer, a VPS) keeps its last few there. Unset, none is written but by
    #: hand (`atlas backup`, the app).
    folder: Path | None = None
    #: Hours between two backups.
    every_hours: int = 24
    #: The backups of this home kept in the folder, the newest; 0 keeps all.
    keep: int = 3


@dataclass(frozen=True, slots=True)
class RelaySettings:
    #: The relay the home waits at, so the app reaches it from anywhere with no open port (step
    #: 20): a `wss://` address. Unset, none: the tailnet and SSH reach it.
    url: str | None = None


@dataclass(frozen=True, slots=True)
class BrowserSettings:
    #: The program that runs Chrome, Edge or Chromium, and the arguments before its own (step 14).
    #: Unset, the first one found where they install.
    chrome: tuple[str, ...] = ()


#: Who writes down a recording (step 26): this computer, a provider with its key, or nobody.
TRANSCRIPTIONS = ("local", "openai", "gemini", "off")


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    #: Who writes down a recording sent or dictated: `local` (this computer, with its model
    #: downloaded once), `openai`, `gemini` (each with its key), or `off`.
    transcription: str = "local"


#: The marketplace Atlas reads (step 16s), and where GitHub serves a repository's archive and one
#: of its files at a commit or a branch.
MARKETPLACE_REPOSITORY = "marqueskaio/atlas-marketplace"
ARCHIVE_URL = "https://codeload.github.com/{repo}/tar.gz/{ref}"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
#: The official MCP Registry's list of servers (step 24), one of the marketplace's outside sources.
REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
#: skills.sh's search, and GitHub's API, which says a repository's newest commit and its files.
SKILLS_URL = "https://skills.sh/api/search"
GITHUB_API_URL = "https://api.github.com"
#: Claude Code's official plugin directory, whose catalog lists its plugins.
CLAUDE_PLUGINS_REPOSITORY = "anthropics/claude-plugins-official"


@dataclass(frozen=True, slots=True)
class MarketplaceSettings:
    #: The marketplace's repository on GitHub, `owner/name`.
    repository: str = MARKETPLACE_REPOSITORY
    #: The branch Atlas reads it from.
    branch: str = "main"
    #: Where a repository's archive is fetched, with `{repo}` and `{ref}`: a mirror's address, or
    #: a test's.
    archive_url: str = ARCHIVE_URL
    #: Where one file of a repository is read, with `{repo}`, `{ref}` and `{path}`.
    raw_url: str = RAW_URL
    #: List what the outside catalogs offer beside the marketplace (step 24): the MCP Registry,
    #: Claude Code's plugin directory and skills.sh.
    outside: bool = True
    #: Where the MCP Registry's list of servers is read.
    registry_url: str = REGISTRY_URL
    #: Where skills.sh is searched.
    skills_url: str = SKILLS_URL
    #: Where GitHub's API is reached, to pin an outside item to its repository's commit.
    github_api_url: str = GITHUB_API_URL
    #: Claude Code's plugin directory on GitHub, `owner/name`, whose catalog is read at its branch.
    claude_plugins: str = CLAUDE_PLUGINS_REPOSITORY


@dataclass(frozen=True, slots=True)
class Settings:
    """Every setting, read once at startup and passed to whoever needs it."""

    home: Path
    server: ServerSettings = field(default_factory=ServerSettings)
    log: LogSettings = field(default_factory=LogSettings)
    vault: VaultSettings = field(default_factory=VaultSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    trust: TrustSettings = field(default_factory=TrustSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    workspace: WorkspaceSettings = field(default_factory=WorkspaceSettings)
    time: TimeSettings = field(default_factory=TimeSettings)
    browser: BrowserSettings = field(default_factory=BrowserSettings)
    marketplace: MarketplaceSettings = field(default_factory=MarketplaceSettings)
    relay: RelaySettings = field(default_factory=RelaySettings)
    backup: BackupSettings = field(default_factory=BackupSettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)


# -- migrations ----------------------------------------------------------------------------------

#: A subject's name is the prefix of every table it owns: `memory` owns `memory_notes`.
_SUBJECT_NAME = re.compile(r"[a-z][a-z0-9]*")
#: The objects a migration creates, alters or drops, and their names.
_OBJECT_NAMES = re.compile(
    r"""\b(?:CREATE\s+(?:(?:TEMP|TEMPORARY|UNIQUE|VIRTUAL)\s+)*(?:TABLE|INDEX|VIEW|TRIGGER)
        |ALTER\s+TABLE|DROP\s+(?:TABLE|INDEX|VIEW|TRIGGER)|RENAME\s+TO)
        \s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?["`\[]?(\w+)""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered, forward-only change to a subject's tables."""

    number: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        """The fingerprint the database keeps, so that a migration edited after it ran is caught."""
        text = self.sql.replace("\r\n", "\n").strip()
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Schema:
    """A subject's migrations, numbered from 1 with no gap."""

    subject: str
    migrations: tuple[Migration, ...]


def schema_problems(schemas: tuple[Schema, ...]) -> list[str]:
    """What is wrong with the migrations declared in the code. Only an empty list is good."""
    problems: list[str] = []
    seen: set[str] = set()
    for schema in schemas:
        subject = schema.subject
        if not _SUBJECT_NAME.fullmatch(subject):
            problems.append(f"subject {subject!r} must be lowercase letters and digits")
        if subject in seen:
            problems.append(f"subject {subject!r} is declared twice")
        seen.add(subject)
        for position, migration in enumerate(schema.migrations, start=1):
            label = f"{subject} #{migration.number}"
            if migration.number != position:
                problems.append(f"{label} is out of place: {subject} counts 1, 2, 3 with no gap")
            if not migration.name.strip():
                problems.append(f"{label} has no name")
            for name in _OBJECT_NAMES.findall(migration.sql):
                if not name.lower().startswith(f"{subject}_"):
                    problems.append(f"{label} touches {name!r}, which is not {subject}_...")
    return problems
