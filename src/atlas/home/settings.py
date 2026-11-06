"""Read the settings once, from `ATLAS_*` variables and `ATLAS_HOME/config.toml`.

This is the only module that reads the environment, and it reads the mapping it is given (the
process entry point passes `os.environ`). Atlas V3 had 156 scattered environment reads in 51 files.

**Precedence**, lowest first: built-in default, `config.toml`, `ATLAS_*` variable, command-line
flag. A variable is named after its place in the file: `[server] port` is `ATLAS_SERVER_PORT`.
A blank variable counts as unset.

**Strict file, tolerant environment.** An unknown key in `config.toml` is an error, because a typo
there would otherwise be ignored in silence. An unknown `ATLAS_*` variable is only reported: the
environment is shared with other programs.

**A section that moved elsewhere is read past, not refused**, and the server moves it where it
lives now when it starts (`drop_section`), the file kept first. Refusing it stopped every command,
the update's own build among them: on 15/10/2026 a tester's `atlas update` failed on the
`[executors]` section step 16c moved, and went back to the version before, so the command the
message named never arrived.

**Secrets stay out of the file.** The vault key comes only from the environment or from a key file.
After reading, the entry point removes the secret variables from the process environment, so no
child process (a command the agent runs, an MCP server) inherits them.
"""

import json
import re
import shutil
import tomllib
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from atlas.home.errors import SettingsError
from atlas.home.files import replace_file
from atlas.home.model import (
    ARCHIVE_URL,
    CLAUDE_PLUGINS_REPOSITORY,
    CONFIG_FILE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    GITHUB_API_URL,
    LOG_LEVELS,
    MARKETPLACE_REPOSITORY,
    RAW_URL,
    REGISTRY_URL,
    SKILLS_URL,
    TRANSCRIPTIONS,
    BackupSettings,
    BrowserSettings,
    Home,
    LogLevel,
    LogSettings,
    MarketplaceSettings,
    MemorySettings,
    ModelSettings,
    RelaySettings,
    SecretText,
    ServerSettings,
    Settings,
    TimeSettings,
    TrustSettings,
    VaultSettings,
    VoiceSettings,
    WorkspaceSettings,
)

HOME_VARIABLE = "ATLAS_HOME"
VARIABLE_PREFIX = "ATLAS_"
DEFAULT_HOME_NAME = ".atlas"
#: Variables that carry a secret. The entry point removes them once the settings are read.
SECRET_VARIABLES = ("ATLAS_VAULT_KEY", "ATLAS_SERVER_TOKEN")

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True, slots=True)
class LoadedSettings:
    settings: Settings
    #: `ATLAS_*` variables that no setting reads: likely a typo, or another program's.
    unknown_variables: tuple[str, ...]
    #: Sections of `config.toml` that moved elsewhere (`MOVED_SECTIONS`), read past.
    moved: tuple[str, ...] = ()
    #: The `ATLAS_*` variables the settings were read with, the secret ones left out: what the
    #: settings screen writes is read again with them, since they win over the file (step 15b).
    variables: Mapping[str, str] = field(default_factory=dict[str, str])


def resolve_home(environ: Mapping[str, str]) -> Path:
    """Where the home is: `ATLAS_HOME` when declared, otherwise `~/.atlas`. Touches no disk.

    A relative path is refused rather than resolved against the current folder: a process started
    by a scheduler or a service has no promised working folder, and the same variable would point
    at a different home depending on where Atlas was started.
    """
    declared = environ.get(HOME_VARIABLE, "").strip()
    if not declared:
        return Path.home() / DEFAULT_HOME_NAME
    home = Path(declared).expanduser()
    if not home.is_absolute():
        raise SettingsError(
            f"{HOME_VARIABLE}={declared!r} is a relative path. Give the full path, "
            "such as /data or ~/.atlas."
        )
    return home


#: What a memory limit may be, in characters: below, a store holds a couple of entries; above,
#: every model call carries some 5,000 tokens of it.
MEMORY_LIMIT_RANGE = {"minimum": 500, "maximum": 20_000}


def load_settings(environ: Mapping[str, str]) -> LoadedSettings:
    """Every setting, from the defaults, the home's `config.toml` and the environment."""
    home = resolve_home(environ)
    path = home / CONFIG_FILE
    return _settings(home, _Source(environ, path, _read_config(path)))


def _settings(home: Path, source: "_Source") -> LoadedSettings:
    settings = Settings(
        home=home,
        server=ServerSettings(
            host=source.text("server", "host", DEFAULT_HOST),
            port=source.integer("server", "port", DEFAULT_PORT, minimum=1, maximum=65535),
            token=source.secret("server", "token", key_file=False),
        ),
        log=LogSettings(
            level=source.log_level("log", "level", "INFO"),
            file=source.boolean("log", "file", default=True),
        ),
        vault=VaultSettings(
            key=source.secret("vault", "key", key_file=True),
            key_file=source.path("vault", "key_file"),
        ),
        model=ModelSettings(
            provider=source.optional_text("model", "provider"),
            name=source.optional_text("model", "name"),
            effort=source.optional_text("model", "effort"),
        ),
        trust=TrustSettings(
            deny=source.text_list("trust", "deny"),
            ask=source.text_list("trust", "ask"),
            ask_skip=source.text_list("trust", "ask_skip"),
            approval_timeout=source.integer(
                "trust", "approval_timeout", 300, minimum=10, maximum=86_400
            ),
        ),
        memory=MemorySettings(
            user_limit=source.integer(
                "memory", "user_limit", MemorySettings().user_limit, **MEMORY_LIMIT_RANGE
            ),
            memory_limit=source.integer(
                "memory", "memory_limit", MemorySettings().memory_limit, **MEMORY_LIMIT_RANGE
            ),
        ),
        workspace=WorkspaceSettings(folder=source.path("workspace", "folder")),
        time=TimeSettings(zone=source.zone("time", "zone")),
        browser=BrowserSettings(chrome=source.text_list("browser", "chrome")),
        relay=RelaySettings(url=source.optional_text("relay", "url")),
        backup=BackupSettings(
            folder=source.path("backup", "folder"),
            every_hours=source.integer("backup", "every_hours", 24, minimum=1, maximum=24 * 30),
            keep=source.integer("backup", "keep", 3, minimum=0, maximum=1000),
        ),
        marketplace=MarketplaceSettings(
            repository=source.text("marketplace", "repository", MARKETPLACE_REPOSITORY),
            branch=source.text("marketplace", "branch", "main"),
            archive_url=source.text("marketplace", "archive_url", ARCHIVE_URL),
            raw_url=source.text("marketplace", "raw_url", RAW_URL),
            outside=source.boolean("marketplace", "outside", default=True),
            registry_url=source.text("marketplace", "registry_url", REGISTRY_URL),
            skills_url=source.text("marketplace", "skills_url", SKILLS_URL),
            github_api_url=source.text("marketplace", "github_api_url", GITHUB_API_URL),
            claude_plugins=source.text("marketplace", "claude_plugins", CLAUDE_PLUGINS_REPOSITORY),
        ),
        voice=VoiceSettings(
            transcription=source.one_of(
                "voice", "transcription", VoiceSettings().transcription, TRANSCRIPTIONS
            ),
        ),
    )
    moved = source.refuse_unknown_keys()
    return LoadedSettings(settings, source.unknown_variables(), moved, source.variables())


def forget_secret_variables(environ: MutableMapping[str, str]) -> None:
    """Remove the secret variables, so that no child process inherits them."""
    for name in SECRET_VARIABLES:
        environ.pop(name, None)


def _read_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"{path} is not valid TOML: {error}. Fix it or remove it.") from error
    except OSError as error:
        raise SettingsError(f"{path} cannot be read: {error}.") from error


#: Sections that moved elsewhere, and where they live now: read past, and moved by the server.
MOVED_SECTIONS = {
    "executors": "Claude Code's program, now the claude-code plugin's `program` setting (step 16c)",
}
#: A table's header line: `[name]`, `[name.sub]` or `[[name]]`.
_HEADER = re.compile(r"^\s*\[")


def drop_section(
    home: Home, section: str, now: datetime, move: Callable[[dict[str, Any]], None]
) -> Path:
    """Take a section out of `config.toml` once `move` took its values where they live now, and
    return where the file as it was is kept, in the home's `backups/`.

    The section is cut as text, so the user's comments and the order of the rest stay. The cut is
    checked first: the text left must read as the same settings without the section, or nothing is
    moved and nothing written. `move` raising leaves the file as it was too.
    """
    path = home.config_file
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise SettingsError(f"{path} cannot be read: {error}.") from error
    before = _parse(path, text)
    table = before.get(section)
    if not isinstance(table, dict):
        raise SettingsError(f"{path} has no [{section}] table to move.")
    named = re.compile(rf"^\s*\[\[?\s*{re.escape(section)}\s*[\].]")
    kept: list[str] = []
    dropping = False
    for line in text.splitlines(keepends=True):
        if _HEADER.match(line):
            dropping = bool(named.match(line))
        if not dropping:
            kept.append(line)
    left = "".join(kept)
    expected = {key: value for key, value in before.items() if key != section}
    try:
        after: dict[str, Any] | None = tomllib.loads(left)
    except tomllib.TOMLDecodeError:
        after = None
    if after != expected:
        raise SettingsError(
            f"[{section}] in {path} could not be cut out on its own; remove it by hand."
        )
    move(cast("dict[str, Any]", table))
    home.backups_folder.mkdir(exist_ok=True)
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    copy = home.backups_folder / f"config-{stamp}.toml"
    number = 2
    while copy.exists():
        copy, number = home.backups_folder / f"config-{stamp}-{number}.toml", number + 1
    shutil.copy2(path, copy)
    replace_file(path, left.encode("utf-8"))
    return copy


def _parse(path: Path, text: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"{path} is not valid TOML: {error}. Fix it or remove it.") from error


# -- the settings screen (step 15b) ---------------------------------------------------------------

#: How the settings screen edits a setting: one line of text, one entry per line (a TOML list), a
#: whole number, a full path, or an IANA time zone.
EditKind = Literal["text", "lines", "number", "path", "zone"]


@dataclass(frozen=True, slots=True)
class Editable:
    """A setting the settings screen writes."""

    section: str
    key: str
    kind: EditKind


#: What the settings screen writes, in its order. The log, the server's address, the vault and the
#: browser's program stay in the file.
EDITABLE = (
    Editable("model", "provider", "text"),
    Editable("model", "name", "text"),
    Editable("model", "effort", "text"),
    Editable("trust", "deny", "lines"),
    Editable("trust", "ask", "lines"),
    Editable("trust", "ask_skip", "lines"),
    Editable("trust", "approval_timeout", "number"),
    Editable("memory", "user_limit", "number"),
    Editable("memory", "memory_limit", "number"),
    Editable("workspace", "folder", "path"),
    Editable("time", "zone", "zone"),
    Editable("voice", "transcription", "text"),
)


@dataclass(frozen=True, slots=True)
class EditableValue:
    setting: Editable
    #: What `config.toml` holds, as the screen edits it (a list one entry per line); `None` when
    #: the file leaves it unset.
    written: str | None
    #: The `ATLAS_*` variable that sets it, which wins over the file; `None` when none does.
    variable: str | None


def variable_name(section: str, key: str) -> str:
    """The variable named after a setting's place in the file: `[server] port` is
    `ATLAS_SERVER_PORT`."""
    return f"{VARIABLE_PREFIX}{section}_{key}".upper()


def editable_values(home: Path, environ: Mapping[str, str]) -> list[EditableValue]:
    """The editable settings as the home's file writes them, each with the variable of `environ`
    that wins over it."""
    path = home / CONFIG_FILE
    config = _read_config(path)
    values: list[EditableValue] = []
    for setting in EDITABLE:
        table = config.get(setting.section)
        value = cast("dict[str, Any]", table).get(setting.key) if isinstance(table, dict) else None
        name = variable_name(setting.section, setting.key)
        values.append(
            EditableValue(
                setting,
                None if value is None else _as_text(value),
                name if environ.get(name, "").strip() else None,
            )
        )
    return values


def edit_settings(
    home: Path, environ: Mapping[str, str], changes: Mapping[tuple[str, str], str | None]
) -> Settings:
    """Write editable settings to the home's `config.toml`, `None` or blank taking one out, and
    return the settings the file gives now, read with `environ`'s variables.

    Only the settings' own lines change, so the user's comments and the order of the rest stay: a
    field nobody touched is never sent back. The new text is read again through the same rules as a
    start, and must give the same values with only these changes: if not, or if a value is refused,
    nothing is written. A setting an `ATLAS_*` variable sets is
    refused, since the variable wins and writing the file would change nothing.
    """
    path = home / CONFIG_FILE
    kinds: dict[tuple[str, str], EditKind] = {
        (setting.section, setting.key): setting.kind for setting in EDITABLE
    }
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        text = ""
    except (OSError, UnicodeDecodeError) as error:
        raise SettingsError(f"{path} cannot be read: {error}.") from error
    expected = _parse(path, text)
    for (section, key), raw in changes.items():
        kind = kinds.get((section, key))
        if kind is None:
            raise SettingsError(f"[{section}] {key} is not a setting the settings screen writes.")
        name = variable_name(section, key)
        if environ.get(name, "").strip():
            raise SettingsError(
                f"[{section}] {key} is set by {name}, which wins over {path.name}. Change or "
                "unset the variable instead."
            )
        value = _from_text(section, key, kind, raw)
        text = _set_line(text, section, key, value)
        table = expected.get(section)
        if value is not None:
            if not isinstance(table, dict):
                expected[section] = table = {}
            cast("dict[str, Any]", table)[key] = value
        elif isinstance(table, dict):
            cast("dict[str, Any]", table).pop(key, None)
    try:
        after: dict[str, Any] | None = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        after = None
    if after is None or after != expected:
        raise SettingsError(
            f"{path} could not be changed one setting at a time; change it by hand."
        )
    settings = _settings(home, _Source(environ, path, after)).settings
    replace_file(path, text.encode("utf-8"))
    return settings


def _as_text(value: object) -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in cast("list[object]", value))
    return str(value)


def _from_text(section: str, key: str, kind: EditKind, raw: str | None) -> object:
    """The TOML value of what the screen sent; `None` takes the setting out."""
    if raw is None or not raw.strip():
        return None
    if kind == "lines":
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return lines or None
    if kind == "number":
        try:
            return int(raw.strip())
        except ValueError:
            raise SettingsError(f"[{section}] {key} must be a whole number, got {raw!r}.") from None
    return raw.strip()


#: A setting's line, by its bare key.
_KEY = r"^\s*{key}\s*="


def _set_line(text: str, section: str, key: str, value: object) -> str:
    """`text` with `[section] key` set to `value`, or taken out for `None`, touching no other line.
    A value written on several lines (a long list) is replaced whole."""
    lines = text.splitlines(keepends=True)
    header = re.compile(rf"^\s*\[\s*{re.escape(section)}\s*\]\s*(#.*)?$")
    start = next((index for index, line in enumerate(lines) if header.match(line)), None)
    rendered = None if value is None else f"{key} = {_toml(value)}\n"
    if start is None:
        if rendered is None:
            return text
        lead = "" if not text else ("\n" if text.endswith("\n") else "\n\n")
        return f"{text}{lead}[{section}]\n{rendered}"
    end = next(
        (index for index in range(start + 1, len(lines)) if _HEADER.match(lines[index])),
        len(lines),
    )
    pattern = re.compile(_KEY.format(key=re.escape(key)))
    found = next((index for index in range(start + 1, end) if pattern.match(lines[index])), None)
    if found is None:
        if rendered is None:
            return text
        at = end
        while at > start + 1 and not lines[at - 1].strip():
            at -= 1
        if not lines[at - 1].endswith("\n"):
            lines[at - 1] += "\n"
        lines.insert(at, rendered)
        return "".join(lines)
    stop = found + 1
    while stop < end and not _whole(lines[found:stop]):
        stop += 1
    lines[found:stop] = [] if rendered is None else [rendered]
    return "".join(lines)


def _whole(lines: list[str]) -> bool:
    try:
        tomllib.loads("".join(lines))
    except tomllib.TOMLDecodeError:
        return False
    return True


def _toml(value: object) -> str:
    """A value as TOML writes it. JSON's string escapes are TOML's basic string's."""
    if isinstance(value, list):
        items = cast("list[str]", value)
        if len(items) == 1:
            return f"[{json.dumps(items[0], ensure_ascii=False)}]"
        body = "".join(f"    {json.dumps(item, ensure_ascii=False)},\n" for item in items)
        return f"[\n{body}]"
    if isinstance(value, int):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


class _Source:
    """One lookup per setting: the variable first, then the file, then the default.

    It remembers what was read, so that what nobody read can be reported afterwards.
    """

    def __init__(
        self, environ: Mapping[str, str], config_file: Path, config: dict[str, Any]
    ) -> None:
        self._environ = environ
        self._config_file = config_file
        self._config = config
        self._known: dict[str, set[str]] = {}
        # ATLAS_INSTALLATION is the installation's, read by `atlas.installation`: no setting.
        self._variables_read: set[str] = {HOME_VARIABLE, "ATLAS_INSTALLATION"}

    # -- lookups ---------------------------------------------------------------------------------

    def _variable(self, section: str, key: str) -> str:
        """The variable for a setting. Asking for it also declares the setting as known."""
        self._known.setdefault(section, set()).add(key)
        name = variable_name(section, key)
        self._variables_read.add(name)
        return name

    def _from_environment(self, section: str, key: str) -> tuple[str, str] | None:
        name = self._variable(section, key)
        value = self._environ.get(name, "").strip()
        return (value, name) if value else None

    def _from_file(self, section: str, key: str) -> tuple[Any, str] | None:
        table = self._config.get(section)
        if table is None:
            return None
        if not isinstance(table, dict):
            raise SettingsError(f"{self._config_file}: [{section}] must be a table of settings.")
        values = cast("dict[str, Any]", table)
        if key not in values:
            return None
        return values[key], f"{self._config_file} [{section}] {key}"

    def text(self, section: str, key: str, default: str) -> str:
        value = self.optional_text(section, key)
        return default if value is None else value

    def optional_text(self, section: str, key: str) -> str | None:
        found = self._from_environment(section, key) or self._from_file(section, key)
        if found is None:
            return None
        value, where = found
        if not isinstance(value, str) or not value.strip():
            raise SettingsError(f"{where} must be non-empty text, got {value!r}.")
        return value.strip()

    def text_list(self, section: str, key: str) -> tuple[str, ...]:
        """A list of texts: a TOML array in the file, one per line in the variable."""
        found = self._from_environment(section, key)
        if found is not None:
            return tuple(line.strip() for line in found[0].splitlines() if line.strip())
        in_file = self._from_file(section, key)
        if in_file is None:
            return ()
        value, where = in_file
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in cast("list[object]", value)
        ):
            raise SettingsError(f"{where} must be a list of texts, got {value!r}.")
        return tuple(item.strip() for item in cast("list[str]", value) if item.strip())

    def integer(self, section: str, key: str, default: int, *, minimum: int, maximum: int) -> int:
        found = self._from_environment(section, key)
        if found is not None:
            text, where = found
            try:
                number = int(text)
            except ValueError:
                raise SettingsError(f"{where}={text!r} must be a whole number.") from None
        else:
            in_file = self._from_file(section, key)
            if in_file is None:
                return default
            value, where = in_file
            if isinstance(value, bool) or not isinstance(value, int):
                raise SettingsError(f"{where} must be a whole number, got {value!r}.")
            number = value
        if not minimum <= number <= maximum:
            raise SettingsError(f"{where} must be between {minimum} and {maximum}, got {number}.")
        return number

    def boolean(self, section: str, key: str, *, default: bool) -> bool:
        found = self._from_environment(section, key)
        if found is not None:
            text, where = found
            if text.lower() in _TRUE:
                return True
            if text.lower() in _FALSE:
                return False
            raise SettingsError(
                f"{where}={text!r} must be true or false (also 1/0, yes/no, on/off)."
            )
        in_file = self._from_file(section, key)
        if in_file is None:
            return default
        value, where = in_file
        if not isinstance(value, bool):
            raise SettingsError(f"{where} must be true or false, got {value!r}.")
        return value

    def log_level(self, section: str, key: str, default: LogLevel) -> LogLevel:
        found = self._from_environment(section, key) or self._from_file(section, key)
        if found is None:
            return default
        value, where = found
        given = value.strip().upper() if isinstance(value, str) else value
        for level in LOG_LEVELS:
            if level == given:
                return level
        raise SettingsError(
            f"{where}={value!r} is not a log level. Use one of: {', '.join(LOG_LEVELS)}."
        )

    def one_of(self, section: str, key: str, default: str, choices: tuple[str, ...]) -> str:
        """One of a few names, given in any case."""
        value = self.optional_text(section, key)
        if value is None:
            return default
        if value.lower() not in choices:
            raise SettingsError(f"[{section}] {key}={value!r} is not one of: {', '.join(choices)}.")
        return value.lower()

    def zone(self, section: str, key: str) -> str | None:
        """An IANA time zone's name, refused when the zone database does not know it."""
        name = self.optional_text(section, key)
        if name is None:
            return None
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            raise SettingsError(
                f"[{section}] {key}={name!r} is not a time zone. Use an IANA name, such as "
                "America/Sao_Paulo or Europe/Lisbon."
            ) from None
        return name

    def path(self, section: str, key: str) -> Path | None:
        found = self._from_environment(section, key) or self._from_file(section, key)
        if found is None:
            return None
        value, where = found
        if not isinstance(value, str) or not value.strip():
            raise SettingsError(f"{where} must be a path, got {value!r}.")
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            raise SettingsError(f"{where}={value!r} is a relative path. Give the full path.")
        return path

    def secret(self, section: str, key: str, *, key_file: bool) -> SecretText | None:
        """A secret from the environment only. Written in the file, it is refused; `key_file`
        says whether a setting naming a file that holds it exists too."""
        if self._from_file(section, key) is not None:
            instead = self._variable(section, key)
            if key_file:
                instead += f" or {self._variable(section, key + '_file')}"
            raise SettingsError(
                f"{self._config_file} has [{section}] {key}, a secret in plain text next to "
                f"everything it protects. Remove it, and set {instead} instead."
            )
        found = self._from_environment(section, key)
        return SecretText(found[0]) if found else None

    # -- what nobody read ------------------------------------------------------------------------

    def refuse_unknown_keys(self) -> tuple[str, ...]:
        """Refuse what no setting reads; returns the sections that moved elsewhere, read past."""
        moved: list[str] = []
        for section, table in self._config.items():
            if section in MOVED_SECTIONS:
                moved.append(section)
                continue
            if section not in self._known:
                raise SettingsError(
                    f"{self._config_file} has an unknown section [{section}]. "
                    f"Known sections: {', '.join(sorted(self._known))}."
                )
            values: dict[str, Any] = table
            for key in values:
                if key not in self._known[section]:
                    raise SettingsError(
                        f"{self._config_file} has an unknown setting [{section}] {key}. "
                        f"Known in [{section}]: {', '.join(sorted(self._known[section]))}."
                    )
        return tuple(moved)

    def variables(self) -> dict[str, str]:
        return {
            name: value
            for name, value in self._environ.items()
            if name.upper().startswith(VARIABLE_PREFIX) and name.upper() not in SECRET_VARIABLES
        }

    def unknown_variables(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                name
                for name in self._environ
                if name.upper().startswith(VARIABLE_PREFIX)
                and name.upper() not in self._variables_read
            )
        )
