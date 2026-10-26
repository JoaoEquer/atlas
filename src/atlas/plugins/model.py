"""Plugins: the manifest, what a plugin declares and the consent it needs, and pairing. Pure.

**The format is Claude Code's** (docs/ARCHITECTURE.md, Plugins), which Codex reads too: a folder
with `.claude-plugin/plugin.json`, whose `name` is the only field required. What only Atlas reads
goes under `atlas` in the same file; Claude Code ignores fields it does not know.
Version 1 loads a plugin's Python (`__init__.py`, `register(ctx)`) and its channels, version 2 its
executors (step 16c). Step 16j loads, as Claude Code does, the skills in its `skills/` folder, each
named after the plugin (`video:render`), and the MCP servers of its `.mcp.json` (or of the
manifest's `mcpServers`), each named `plugin_<plugin>_<server>` (Claude Code's tool names,
`mcp__plugin_<plugin>_<server>__<tool>`); and the tools its Python registers, which the manifest
names under `atlas.tools` (version 14). Step 16k loads its hooks: Claude Code's `hooks/hooks.json`
(and the manifest's `hooks`), read by `atlas.hooks` and given here as one line each, and the Python
hooks it names under `atlas.hooks` (version 15), its commands, agents, programs in `bin/`,
`userConfig` and monitors. A plugin that brings LSP servers, output styles, themes or workflows is
listed, and refused.

**A plugin's MCP servers read only what the user was shown:** `${CLAUDE_PLUGIN_ROOT}` and
`${CLAUDE_PLUGIN_DATA}` (Claude Code's names for its folder and its data folder), its options
(`${user_config.KEY}`), and secrets. The MCP client fills a `${NAME}` from the whole vault, so a
secret a server names is declared by being named, as one of `atlas.secrets` is: shown at consent
and counted in its fingerprint (decided with the user on 20/10/2026, so Claude Code's own plugins
run unchanged: GitHub's reads `${GITHUB_PERSONAL_ACCESS_TOKEN}`). Before, such a plugin was
refused; either way it never reads a key the user was not shown.

**Consent:** a plugin arrives turned off, and turning it on shows what it
declares: its Python, its channels and executors, the secrets it reads, the hosts it reaches. The
user's consent is kept as the fingerprint of that declaration, and an update that declares
anything else turns the plugin off until the user consents again, before the new code runs.

**Pairing:** someone a channel does not know gets a code and their
message is dropped; the user approves the code in the app or the terminal, and they send again.
Codes of 8 characters from an alphabet without look-alikes, valid for an hour, at most 3 waiting
per channel; a person who writes again while their code holds is told the same code.
"""

import hashlib
import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import total_ordering
from typing import Any, cast

#: The manifest, where Claude Code keeps it.
MANIFEST = ".claude-plugin/plugin.json"
#: A plugin's Python: the module whose `register(ctx)` Atlas calls.
PYTHON = "__init__.py"
#: A plugin's name, Claude Code's rule: kebab-case.
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")
#: A secret's name, as the vault names them.
_SECRET = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SETTING = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: A `userConfig` option's key, Claude Code's rule: letters, digits and `_`, not a digit first.
_OPTION = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
#: What a `userConfig` option holds; anything else refuses the plugin, as in Claude Code.
_OPTION_FIELDS = frozenset(
    {"type", "title", "description", "required", "default", "options", "multiple", "sensitive",
     "min", "max"}
)  # fmt: skip
_OPTION_TYPES = frozenset({"string", "number", "boolean", "directory", "file"})
#: How an MCP server's fields name an option, Claude Code's `${user_config.KEY}`.
USER_CONFIG = "user_config."
#: A tool's name: what providers take, lowercase, as Atlas's own are named.
_TOOL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: An MCP server's name, as the user's MCP file takes it.
_SERVER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
#: A skill's name, as `atlas.skills` takes it.
_SKILL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: A `${NAME}` or `${NAME:-default}` in an MCP server's fields, as `atlas.mcp` reads them.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)(?::-[^}]*)?\}")
#: Claude Code's names for the plugin's folder and its data folder.
PLUGIN_ROOT = "CLAUDE_PLUGIN_ROOT"
PLUGIN_DATA = "CLAUDE_PLUGIN_DATA"
#: A plugin's MCP servers and its skills, where Claude Code keeps them.
MCP_FILE = ".mcp.json"
SKILLS_FOLDER = "skills"
#: A plugin's commands and its programs, where Claude Code keeps them (step 16k).
COMMANDS_FOLDER = "commands"
BIN_FOLDER = "bin"
AGENTS_FOLDER = "agents"
#: The SDK versions that register tools, and Python hooks.
TOOLS_API = 14
HOOKS_API = 15
#: The SDK version of commands, terminal commands, `llm` and `notify`.
PYTHON_API = 16
#: The SDK version of a screen's calls to the plugin's Python (step 16m).
CALLS_API = 17
#: The SDK version of attachment readers (step 17a), and the kinds a reader may read.
READERS_API = 21
READER_KINDS = ("audio",)
#: The SDK version of a screen's desktop powers (step 17b), and the powers a screen may declare.
DESKTOP_API = 23
DESKTOP_POWERS = ("paste", "shortcut")
#: The SDK version of themes (`atlas.themes`, version 24): the colors a plugin gives the app, one
#: `themes/<name>.json` each. A theme is data: no Python, no screen, nothing runs.
THEMES_API = 24
THEMES_FOLDER = "themes"
#: The largest theme file Atlas reads.
THEME_BYTES = 64 * 1024
THEME_BASES = ("light", "dark")
#: What a theme paints: the app's named colors (`apps/desktop/src/styles.css`, step 9d), the only
#: names a screen paints with. A test keeps this list the same as the app's.
THEME_TOKENS = (
    "background", "foreground", "soft", "muted-foreground", "faint", "subtle", "raised", "muted",
    "hover", "selected", "border", "border-strong", "primary", "primary-foreground", "ring",
    "scrim", "working", "waiting", "waiting-text", "waiting-foreground", "done", "done-text",
    "failed", "failed-text", "danger", "added", "removed",
)  # fmt: skip
#: What it paints with: a hex color, and nothing else, so a theme can never carry a URL or a rule.
_THEME_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_THEME_FIELDS = frozenset({"label", "labels", "base", "colors"})
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
#: A plugin's screen, its desktop half (step 16m).
SCREEN_FILE = "desktop/plugin.js"
#: A hook's event, Claude Code's way of naming them.
_EVENT = re.compile(r"^[A-Z][A-Za-z]{1,63}$")
#: What Claude Code's format holds that Atlas does not load yet, by where it sits.
UNSUPPORTED = {
    ".lsp.json": "LSP servers",
}
#: The same, declared in the manifest instead.
UNSUPPORTED_KEYS = {
    "skills": "skills outside skills/",
    "commands": "commands",
    "agents": "agents",
    "lspServers": "LSP servers",
    "outputStyles": "output styles",
    "workflows": "workflows",
    "themes": "themes",
}
#: Claude Code's `experimental` holds monitors, which Atlas runs, and these, which it does not.
UNSUPPORTED_EXPERIMENTS = {"themes": "themes", "evals": "evals"}
#: A plugin's monitors, where Claude Code keeps them (step 16k).
MONITORS_FILE = "monitors/monitors.json"
#: What a monitor holds; anything else refuses the plugin, as in Claude Code.
_MONITOR_FIELDS = frozenset({"name", "command", "description", "when"})
#: A monitor's `when` that waits for a skill, Claude Code's.
ON_SKILL = "on-skill-invoke:"

#: The pairing alphabet: no 0, O, 1 or I.
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 8
PAIRING_TTL = timedelta(hours=1)
#: Codes waiting per channel; past them, a newcomer gets none until one is approved or expires.
PAIRING_MOST_PENDING = 3


class ManifestError(ValueError):
    """A manifest Atlas cannot read, with what to fix."""


@dataclass(frozen=True, slots=True)
class SecretDeclaration:
    name: str
    description: str = ""
    #: Where the user gets it (BotFather's address, for a Telegram token).
    url: str = ""


@dataclass(frozen=True, slots=True)
class SettingDeclaration:
    """A setting the user may change (Claude Code's `userConfig`), with its default."""

    name: str
    default: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class MonitorDeclaration:
    """A monitor (step 16k), Claude Code's: a command that runs in the background of each of the
    user's conversations, each line it prints reaching the model as a notice."""

    name: str
    command: str
    description: str
    #: `always`: from the conversation's first turn; or the skill whose first use starts it.
    when: str = "always"


@dataclass(frozen=True, slots=True)
class Dependency:
    """Another plugin a plugin needs (step 16n), as Claude Code's `dependencies` names one."""

    name: str
    #: The versions it takes, an npm semver range (`~2.1.0`); empty for any.
    version: str = ""
    #: The marketplace Claude Code installs it from, kept as written: Atlas has none yet.
    marketplace: str = ""

    def __str__(self) -> str:
        return f"{self.name} {self.version}" if self.version else self.name


@dataclass(frozen=True, slots=True)
class Manifest:
    name: str
    #: Its name for people (`displayName`), where the marketplace and the screens show it.
    display_name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    license: str = ""
    #: The SDK version it was written for (`atlas.plugins.sdk.API_VERSION`).
    api: int = 1
    #: It brings Python: an `__init__.py` with `register(ctx)`.
    python: bool = False
    channels: tuple[str, ...] = ()
    #: The outside harnesses it drives (version 2): `claude-code`.
    executors: tuple[str, ...] = ()
    secrets: tuple[SecretDeclaration, ...] = ()
    settings: tuple[SettingDeclaration, ...] = ()
    #: The hosts it says it reaches, shown at consent; declared, not enforced.
    network: tuple[str, ...] = ()
    #: What it brings that Atlas does not load yet.
    unsupported: tuple[str, ...] = ()
    #: The tools its Python registers (`atlas.tools`, version 14).
    tools: tuple[str, ...] = ()
    #: Its skills, by their own names, without the plugin's in front.
    skills: tuple[str, ...] = ()
    #: Its commands (`commands/<name>.md`, step 16k), read as skills, by their own names.
    commands: tuple[str, ...] = ()
    #: The programs of its `bin/` (step 16k), on the `PATH` of the commands the model runs.
    programs: tuple[str, ...] = ()
    #: Its agents (`agents/<name>.md`, step 16k), predefined subagents, by their files' names.
    agents: tuple[str, ...] = ()
    #: Its monitors (step 16k).
    monitors: tuple[MonitorDeclaration, ...] = ()
    #: Its `userConfig` options (step 16k), by key: the vault's name for a sensitive one (which
    #: is among `secrets`), or empty for one kept as a setting (among `settings`).
    options: Mapping[str, str] = field(default_factory=dict[str, str])
    #: Its MCP servers by their own names, as its `.mcp.json` declares them.
    mcp_servers: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict[str, Mapping[str, Any]]
    )
    #: Its command hooks, one line each (`atlas.hooks.CommandHook.summary`).
    hooks: tuple[str, ...] = ()
    #: The events its Python hooks into (`atlas.hooks`, version 15).
    python_hooks: tuple[str, ...] = ()
    #: The slash commands its Python runs (`atlas.commands`, version 16).
    slash_commands: tuple[str, ...] = ()
    #: The terminal commands its Python runs, `atlas <name>` (`atlas.cli`, version 16).
    cli_commands: tuple[str, ...] = ()
    #: It calls the user's model on its own (`atlas.llm`, version 16).
    llm: bool = False
    #: It starts turns in conversations on its own (`atlas.notify`, version 16).
    notify: bool = False
    #: The model providers it adds (`atlas.providers`, step 16l), by id, each as `models.toml`'s
    #: `[providers.<id>]` writes one.
    providers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict[str, Mapping[str, Any]])
    #: It brings a screen of its own for the app (`desktop/plugin.js`, step 16m).
    screen: bool = False
    #: What its screen may call in its Python (`atlas.calls`, version 17).
    calls: tuple[str, ...] = ()
    #: The kinds of attachment its Python reads into text (`atlas.readers`, version 21): `audio`.
    readers: tuple[str, ...] = ()
    #: What its screen does outside the app's window (`atlas.desktop`, version 23): `shortcut`, a
    #: key heard in every program, and `paste`, text pasted into the program in focus.
    desktop: tuple[str, ...] = ()
    #: The themes it brings (`atlas.themes`, version 24), by name: `themes/<name>.json` each.
    themes: tuple[str, ...] = ()
    #: The other plugins it needs on (Claude Code's `dependencies`, step 16n).
    dependencies: tuple[Dependency, ...] = ()

    def declaration(self) -> tuple[str, ...]:
        """What the user consents to, one line each."""
        lines: list[str] = []
        if self.python:
            lines.append(
                "Runs its own Python code inside Atlas, with your user's permissions on this "
                "computer: there is no sandbox."
            )
        lines += [
            f"Adds the channel {channel}: the people you allow talk to Atlas through it, and "
            "Atlas answers, asks and asks for approvals there."
            for channel in self.channels
        ]
        lines += [
            f"Adds the executor {executor}: it runs that program as installed on this computer, "
            "with your own login, in the folders you or Atlas give it."
            for executor in self.executors
        ]
        if self.tools:
            lines.append(
                f"Adds the tools {', '.join(self.tools)}, which the model may call and its Python "
                "runs."
            )
        lines += [_server_line(name, entry) for name, entry in self.mcp_servers.items()]
        lines += [
            f"Runs {hook}: it sees what happens there, and may stop it, change it or add to it."
            for hook in self.hooks
        ]
        if self.python_hooks:
            lines.append(
                f"Its Python hooks into {', '.join(self.python_hooks)}: it sees what happens "
                "there, and may stop it, change it or add to it."
            )
        if self.skills:
            named = ", ".join(f"{self.name}:{skill}" for skill in self.skills)
            lines.append(f"Adds the skills {named}: instructions the model may load and follow.")
        if self.commands:
            named = ", ".join(f"/{self.name}:{command}" for command in self.commands)
            lines.append(f"Adds the commands {named}: instructions you or the model may run.")
        if self.agents:
            named = ", ".join(f"{self.name}:{agent}" for agent in self.agents)
            lines.append(
                f"Adds the subagents {named}: helpers with their own instructions, which the "
                "model may hand a task to."
            )
        lines += [
            f"Runs `{monitor.command}` in the background of your conversations "
            f"({_when(monitor.when)}): {monitor.description}. Each line it prints reaches the "
            "model, which answers it."
            for monitor in self.monitors
        ]
        if self.programs:
            lines.append(
                f"Puts the programs {', '.join(self.programs)} on the PATH of the commands the "
                "model runs: running one runs the plugin's own code."
            )
        if self.slash_commands:
            named = ", ".join(f"/{self.name}:{command}" for command in self.slash_commands)
            lines.append(f"Adds the commands {named}, which its Python runs when you type them.")
        if self.cli_commands:
            named = ", ".join(f"`atlas {command}`" for command in self.cli_commands)
            lines.append(f"Adds the terminal commands {named}, which run its Python.")
        if self.llm:
            lines.append(
                "Calls your model on its own, on your key: each call costs credit, and is in "
                "`atlas usage` as the plugin's."
            )
        if self.notify:
            lines.append(
                "Writes to your conversations on its own: each message starts a turn, which "
                "costs credit."
            )
        lines += [
            f"Adds the model provider {provider} at {entry.get('base_url', '')}: what you choose "
            "there is sent to that address."
            for provider, entry in self.providers.items()
        ]
        if self.screen:
            lines.append(
                "Shows screens of its own in the app (desktop/plugin.js). Their code runs inside "
                "the app with all the app's access: your conversations, your homes, and what "
                "Atlas does for the app. There is no sandbox."
            )
        if self.calls:
            lines.append(
                f"Its screen calls its Python ({', '.join(self.calls)}), which answers with your "
                "user's permissions on the computer Atlas runs on."
            )
        lines += [
            f"Reads the {kind} files you send, in the app and through your channels, before the "
            "model does: its Python gets each file and writes down what it holds, which the model "
            "reads."
            for kind in self.readers
        ]
        if "shortcut" in self.desktop:
            lines.append(
                "Its screen listens for a key you choose in every program, while the app is open."
            )
        if "paste" in self.desktop:
            lines.append(
                "Its screen pastes text into the program you are in: it puts the text on the "
                "clipboard, presses Ctrl+V for you, and puts back what the clipboard held."
            )
        if self.themes:
            lines.append(
                f"Adds the themes {', '.join(self.themes)}: colors for the app, and nothing "
                "that runs."
            )
        if self.dependencies:
            lines.append(
                f"Needs the plugins {', '.join(map(str, self.dependencies))}, turned on first."
            )
        lines += [f"Reads the secret {secret.name} from the vault." for secret in self.secrets]
        if self.network:
            lines.append(f"Says it reaches: {', '.join(self.network)}.")
        return tuple(lines)

    def fingerprint(self) -> str:
        """What the consent covers, so a manifest that declares anything else asks again."""
        declared: dict[str, object] = {
            "api": self.api,
            "python": self.python,
            "channels": sorted(self.channels),
            "secrets": sorted(secret.name for secret in self.secrets),
            "network": sorted(self.network),
        }
        # Each only where declared, so a plugin of an earlier version keeps the consent it was
        # given.
        if self.executors:
            declared["executors"] = sorted(self.executors)
        if self.tools:
            declared["tools"] = sorted(self.tools)
        if self.mcp_servers:
            # The whole entry: a server that runs another command asks again.
            declared["mcp"] = {name: dict(entry) for name, entry in self.mcp_servers.items()}
        if self.skills:
            declared["skills"] = sorted(self.skills)
        if self.commands:
            declared["commands"] = sorted(self.commands)
        if self.programs:
            declared["programs"] = sorted(self.programs)
        if self.agents:
            declared["agents"] = sorted(self.agents)
        if self.monitors:
            declared["monitors"] = sorted(
                f"{monitor.name}\n{monitor.command}\n{monitor.when}" for monitor in self.monitors
            )
        if self.hooks:
            declared["hooks"] = sorted(self.hooks)
        if self.python_hooks:
            declared["python_hooks"] = sorted(self.python_hooks)
        if self.slash_commands:
            declared["slash_commands"] = sorted(self.slash_commands)
        if self.cli_commands:
            declared["cli"] = sorted(self.cli_commands)
        if self.llm:
            declared["llm"] = True
        if self.notify:
            declared["notify"] = True
        if self.providers:
            declared["providers"] = {name: dict(entry) for name, entry in self.providers.items()}
        if self.screen:
            declared["screen"] = True
        if self.calls:
            declared["calls"] = sorted(self.calls)
        if self.readers:
            declared["readers"] = sorted(self.readers)
        if self.desktop:
            declared["desktop"] = sorted(self.desktop)
        if self.themes:
            declared["themes"] = sorted(self.themes)
        text = json.dumps(declared, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def setting(self, name: str) -> SettingDeclaration | None:
        return next((s for s in self.settings if s.name == name), None)


@dataclass(frozen=True, slots=True)
class Theme:
    """A theme a plugin brings (version 24): its colors for the app, as `themes/<name>.json` holds
    them. `base` is the look it sits on (the app's light or dark), `colors` the named colors of
    the app it repaints (`background`, `soft`), each a hex color."""

    name: str
    label: str
    base: str
    colors: Mapping[str, str]
    #: The label in a language, by locale (`pt-BR`), for the ones that have it.
    labels: Mapping[str, str] = field(default_factory=dict[str, str])


def parse_theme(name: str, text: str) -> Theme:
    """A theme from the text of its file; raises `ManifestError` with what to fix."""
    where = f"themes/{name}.json"
    if len(text.encode("utf-8")) > THEME_BYTES:
        raise ManifestError(f"{where} is over {THEME_BYTES // 1024} KB.")
    try:
        raw: object = json.loads(text)
    except ValueError as error:
        raise ManifestError(f"{where} is not JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestError(f"{where} is not a JSON object.")
    data: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    extra = sorted(set(data) - _THEME_FIELDS)
    if extra:
        raise ManifestError(
            f"{where} holds {', '.join(extra)}; a theme has {', '.join(sorted(_THEME_FIELDS))}."
        )
    label = data.get("label", name)
    if not isinstance(label, str) or not 1 <= len(label.strip()) <= 40:
        raise ManifestError(f"{where}: label is a text of 1 to 40 characters.")
    labels_raw: object = data.get("labels", {})
    if not isinstance(labels_raw, dict):
        raise ManifestError(f"{where}: labels is an object of text by locale.")
    labels: dict[str, str] = {}
    for locale, value in cast("dict[object, object]", labels_raw).items():
        if not isinstance(locale, str) or not _LOCALE.match(locale):
            raise ManifestError(f"{where}: labels has a bad locale {locale!r}.")
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 40:
            raise ManifestError(f"{where}: the label for {locale} is a text of 1 to 40 characters.")
        labels[locale] = value.strip()
    base = data.get("base")
    if base not in THEME_BASES:
        raise ManifestError(f"{where}: base is {' or '.join(THEME_BASES)}.")
    colors_raw: object = data.get("colors")
    if not isinstance(colors_raw, dict) or not colors_raw:
        raise ManifestError(f"{where}: colors is an object of the app's colors and hex colors.")
    pairs = cast("dict[object, object]", colors_raw)
    colors: dict[str, str] = {}
    for key, value in pairs.items():
        if not isinstance(key, str) or key not in THEME_TOKENS:
            raise ManifestError(
                f"{where}: {key!r} is not a color of the app; it has {', '.join(THEME_TOKENS)}."
            )
        if not isinstance(value, str) or not _THEME_HEX.match(value):
            raise ManifestError(f"{where}: {key} is not a hex color (such as #e2dbc9).")
        colors[key] = value.lower()
    return Theme(name, label.strip(), str(base), colors, labels)


def parse_manifest(
    text: str,
    present: set[str],
    *,
    folder_name: str,
    skills: Sequence[str] = (),
    mcp_text: str | None = None,
    hooks: Sequence[str] = (),
    commands: Sequence[str] = (),
    programs: Sequence[str] = (),
    agents: Sequence[str] = (),
    monitors_text: str | None = None,
    screen: bool = False,
    themes: Mapping[str, str] | None = None,
) -> Manifest:
    """A plugin's manifest from `plugin.json`'s text; `present` names what sits at the plugin's
    root (`__init__.py`, `skills`), `folder_name` the folder it is in, `skills` the folders of its
    `skills/` that hold a `SKILL.md`, `mcp_text` its `.mcp.json`, when it has one, `hooks` its
    command hooks, one line each, as `atlas.hooks` read them, and `commands` the names of the
    Markdown files of its `commands/`, `programs` the files of its `bin/`, `agents` the
    Markdown files of its `agents/`, `monitors_text` its `monitors/monitors.json`, and
    `screen` whether it has a `desktop/plugin.js`, and `themes` the text of the files of its
    `themes/`, by name."""
    try:
        raw: object = json.loads(text)
    except ValueError as error:
        raise ManifestError(f"{MANIFEST} is not JSON: {error}") from error
    if not isinstance(raw, dict):
        raise ManifestError(f"{MANIFEST} is not a JSON object.")
    data: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    name = data.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        raise ManifestError(
            f"{MANIFEST} needs a name in kebab-case (lowercase letters, digits and hyphens), "
            f"such as {folder_name.lower()!r}."
        )
    if name != folder_name:
        raise ManifestError(f"The plugin {name!r} must sit in a folder named {name!r}.")
    atlas = data.get("atlas", {})
    if not isinstance(atlas, dict):
        raise ManifestError(f"{MANIFEST}: atlas is not an object.")
    own: dict[str, Any] = atlas  # pyright: ignore[reportUnknownVariableType]
    api = own.get("api", 1)
    if not isinstance(api, int) or isinstance(api, bool) or api < 1:
        raise ManifestError(f"{MANIFEST}: atlas.api is a whole number from 1.")
    channels = _names(own, "channels", _NAME)
    executors = _names(own, "executors", _NAME)
    python = PYTHON in present
    if channels and not python:
        raise ManifestError(f"{name} declares channels but brings no {PYTHON}.")
    if executors and not python:
        raise ManifestError(f"{name} declares executors but brings no {PYTHON}.")
    if executors and api < 2:
        raise ManifestError(f"{name} declares executors, which need atlas.api 2.")
    tools = _names(own, "tools", _TOOL)
    if tools and not python:
        raise ManifestError(f"{name} declares tools but brings no {PYTHON}.")
    if tools and api < TOOLS_API:
        raise ManifestError(f"{name} declares tools, which need atlas.api {TOOLS_API}.")
    python_hooks = _names(own, "hooks", _EVENT)
    if python_hooks and not python:
        raise ManifestError(f"{name} declares Python hooks but brings no {PYTHON}.")
    if python_hooks and api < HOOKS_API:
        raise ManifestError(f"{name} declares Python hooks, which need atlas.api {HOOKS_API}.")
    slash_commands = _names(own, "commands", _NAME)
    cli_commands = _names(own, "cli", _NAME)
    llm, notify = _flag(own, "llm"), _flag(own, "notify")
    for what, used in (
        ("commands", slash_commands),
        ("terminal commands", cli_commands),
        ("the user's model", llm),
        ("messages to conversations", notify),
    ):
        if used and not python:
            raise ManifestError(f"{name} declares {what} but brings no {PYTHON}.")
        if used and api < PYTHON_API:
            raise ManifestError(f"{name} declares {what}, which need atlas.api {PYTHON_API}.")
    calls = _names(own, "calls", _TOOL)
    if calls and not python:
        raise ManifestError(f"{name} declares calls but brings no {PYTHON}.")
    if calls and api < CALLS_API:
        raise ManifestError(f"{name} declares calls, which need atlas.api {CALLS_API}.")
    readers = _names(own, "readers", _NAME)
    if readers and not python:
        raise ManifestError(f"{name} declares readers but brings no {PYTHON}.")
    if readers and api < READERS_API:
        raise ManifestError(f"{name} declares readers, which need atlas.api {READERS_API}.")
    unknown = sorted(set(readers) - set(READER_KINDS))
    if unknown:
        raise ManifestError(
            f"{name} declares readers for {', '.join(unknown)}; a reader reads "
            f"{', '.join(READER_KINDS)}."
        )
    desktop = _names(own, "desktop", _NAME)
    if desktop and not screen:
        raise ManifestError(f"{name} declares desktop powers but brings no {SCREEN_FILE}.")
    if desktop and api < DESKTOP_API:
        raise ManifestError(f"{name} declares desktop powers, which need atlas.api {DESKTOP_API}.")
    unknown = sorted(set(desktop) - set(DESKTOP_POWERS))
    if unknown:
        raise ManifestError(
            f"{name} declares the desktop powers {', '.join(unknown)}; a screen may declare "
            f"{', '.join(DESKTOP_POWERS)}."
        )
    theme_names = _names(own, "themes", _NAME)
    if theme_names and api < THEMES_API:
        raise ManifestError(f"{name} declares themes, which need atlas.api {THEMES_API}.")
    for theme in theme_names:
        found = (themes or {}).get(theme)
        if found is None:
            raise ManifestError(
                f"{name} declares the theme {theme} but brings no {THEMES_FOLDER}/{theme}.json."
            )
        parse_theme(theme, found)
    secrets = tuple(
        SecretDeclaration(entry["name"], entry.get("description", ""), entry.get("url", ""))
        for entry in _entries(own, "secrets", _SECRET)
    )
    settings = tuple(
        SettingDeclaration(entry["name"], entry.get("default", ""), entry.get("description", ""))
        for entry in _entries(own, "settings", _SETTING)
    )
    options, option_settings, option_secrets = _user_config(name, data.get("userConfig"))
    taken = {s.name for s in settings} | {s.name for s in secrets}
    clash = sorted(taken & ({s.name for s in option_settings} | {s.name for s in option_secrets}))
    if clash:
        raise ManifestError(f"userConfig and atlas both declare {', '.join(clash)}.")
    secrets += option_secrets
    settings += option_settings
    servers, implied = _servers(
        data, mcp_text, {secret.name for secret in secrets} | {USER_CONFIG + key for key in options}
    )
    secrets += implied
    providers = _providers(own.get("providers"))
    named = {secret.name for secret in secrets}
    secrets += tuple(
        SecretDeclaration(key, f"The key of its model provider {provider}.")
        for provider, entry in providers.items()
        if isinstance(key := entry.get("key"), str) and key and key not in named
    )
    author = data.get("author")
    return Manifest(
        name=name,
        display_name=_text(data, "displayName"),
        version=_text(data, "version"),
        description=_text(data, "description"),
        author=_text(author, "name") if isinstance(author, dict) else "",  # pyright: ignore[reportUnknownArgumentType]
        license=_text(data, "license"),
        api=api,
        python=python,
        channels=channels,
        executors=executors,
        secrets=secrets,
        settings=settings,
        network=_names(own, "network", re.compile(r"^[A-Za-z0-9.:*-]{1,253}$")),
        unsupported=(
            *(what for where, what in UNSUPPORTED.items() if where in present),
            *(what for key, what in UNSUPPORTED_KEYS.items() if key in data),
            *_experiments(data),
        ),
        tools=tools,
        skills=tuple(sorted(skill for skill in skills if _SKILL.match(skill))),
        commands=tuple(sorted(command for command in commands if _SKILL.match(command))),
        programs=tuple(sorted(programs)),
        agents=tuple(sorted(agent for agent in agents if _SKILL.match(agent))),
        monitors=_monitors(data, monitors_text),
        options=options,
        mcp_servers=servers,
        hooks=tuple(hooks),
        python_hooks=python_hooks,
        slash_commands=slash_commands,
        cli_commands=cli_commands,
        llm=llm,
        notify=notify,
        providers=providers,
        screen=screen,
        calls=calls,
        readers=readers,
        desktop=desktop,
        themes=theme_names,
        dependencies=_dependencies(name, data.get("dependencies")),
    )


def _dependencies(plugin: str, value: object) -> tuple[Dependency, ...]:
    """Claude Code's `dependencies`: each `"name"`, `"name@marketplace"` or `{name, version,
    marketplace}`, the version an npm semver range."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ManifestError(f"{MANIFEST}: dependencies is a list.")
    found: dict[str, Dependency] = {}
    bad = f"{MANIFEST}: each of dependencies is a name or an object of text."
    for item in cast("list[object]", value):
        if isinstance(item, str):
            name, _, marketplace = item.partition("@")
            entry = Dependency(name, "", marketplace)
        elif isinstance(item, dict):
            fields = cast("dict[str, object]", item)
            if not all(isinstance(fields.get(k, ""), str) for k in Dependency.__slots__):
                raise ManifestError(bad)
            entry = Dependency(
                str(fields.get("name", "")),
                str(fields.get("version", "")).strip(),
                str(fields.get("marketplace", "")),
            )
        else:
            raise ManifestError(bad)
        if not _NAME.match(entry.name):
            raise ManifestError(f"{MANIFEST}: dependencies has a bad name {entry.name!r}.")
        if entry.name == plugin:
            raise ManifestError(f"{plugin} cannot depend on itself.")
        if entry.version:
            try:
                check_range(entry.version)
            except VersionError as error:
                raise ManifestError(f"{MANIFEST}: the dependency {entry.name}: {error}") from error
        found[entry.name] = entry
    return tuple(found.values())


def _flag(data: dict[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise ManifestError(f"{MANIFEST}: atlas.{key} is true or false.")
    return value


def _providers(value: object) -> dict[str, Mapping[str, Any]]:
    """`atlas.providers`: model providers, each as `models.toml`'s `[providers.<id>]` writes one,
    an OpenAI-compatible address at least; `atlas.models` reads the rest when it is turned on. Its
    `key` is a secret's name, declared by being named, as an MCP server's."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ManifestError(f"{MANIFEST}: atlas.providers is an object of providers by id.")
    providers: dict[str, Mapping[str, Any]] = {}
    for provider, raw in cast("dict[object, object]", value).items():
        where = f"{MANIFEST}: atlas.providers.{provider}"
        if not isinstance(provider, str) or not _NAME.match(provider):
            raise ManifestError(f"{where}: an id is lowercase letters, digits and hyphens.")
        if not isinstance(raw, dict):
            raise ManifestError(f"{where} is not an object.")
        entry = cast("dict[str, Any]", raw)
        url = entry.get("base_url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ManifestError(f"{where} needs base_url, its OpenAI-compatible address.")
        key = entry.get("key")
        if key is not None and (not isinstance(key, str) or not _SECRET.match(key)):
            raise ManifestError(f"{where}: key is a secret's name, such as MY_PROVIDER_KEY.")
        providers[provider] = entry
    return providers


def _experiments(data: dict[str, Any]) -> list[str]:
    """What of Claude Code's `experimental` Atlas does not run: all but its monitors."""
    experimental = data.get("experimental")
    if experimental is None:
        return []
    if not isinstance(experimental, dict):
        return ["experimental"]
    keys = cast("dict[str, object]", experimental)
    return [UNSUPPORTED_EXPERIMENTS.get(key, key) for key in keys if key != "monitors"]


def _monitors(data: dict[str, Any], text: str | None) -> tuple[MonitorDeclaration, ...]:
    """The monitors of the manifest (`experimental.monitors`, or the `monitors` Claude Code still
    reads at the top), in place of `monitors/monitors.json`, as in Claude Code."""
    experimental = data.get("experimental")
    declared: object = None
    where = MONITORS_FILE
    if isinstance(experimental, dict) and "monitors" in experimental:
        declared, where = cast("dict[str, object]", experimental)["monitors"], "experimental"
    elif "monitors" in data:
        declared, where = data["monitors"], "monitors"
    elif text is not None:
        try:
            declared = json.loads(text) if text.strip() else []
        except ValueError as error:
            raise ManifestError(f"{MONITORS_FILE} is not JSON: {error}") from error
    if declared is None:
        return ()
    if isinstance(declared, str):
        raise ManifestError(
            f"{MANIFEST}: {where}.monitors names a file; Atlas reads {MONITORS_FILE} or the array."
        )
    if not isinstance(declared, list):
        raise ManifestError(f"{where}: the monitors are an array.")
    monitors: list[MonitorDeclaration] = []
    for raw in cast("list[object]", declared):
        if not isinstance(raw, dict):
            raise ManifestError(f"{where}: each monitor is an object.")
        entry = cast("dict[str, object]", raw)
        unknown = sorted(set(entry) - _MONITOR_FIELDS)
        if unknown:
            raise ManifestError(f"{where}: a monitor has fields Claude Code refuses: {unknown}.")
        name, command, about = entry.get("name"), entry.get("command"), entry.get("description")
        if not all(isinstance(v, str) and v.strip() for v in (name, command, about)):
            raise ManifestError(f"{where}: a monitor needs a name, a command and a description.")
        when = entry.get("when", "always")
        if when != "always" and not (isinstance(when, str) and when.startswith(ON_SKILL)):
            raise ManifestError(f"{where}: a monitor's when is always or {ON_SKILL}<skill>.")
        if "${user_config." in str(command):
            raise ManifestError(
                f"{where}: the monitor {name} names an option; a monitor's command takes none, "
                "as in Claude Code."
            )
        monitors.append(
            MonitorDeclaration(
                str(name).strip(), str(command), " ".join(str(about).split()), str(when)
            )
        )
    if len({monitor.name for monitor in monitors}) != len(monitors):
        raise ManifestError(f"{where}: two monitors have the same name.")
    return tuple(monitors)


def _when(when: str) -> str:
    if when.startswith(ON_SKILL):
        return f"once the skill {when.removeprefix(ON_SKILL)} is used"
    return "from their first turn"


def option_secret(plugin: str, key: str) -> str:
    """The vault's name for a plugin's sensitive option: the plugin's name and the key, in
    capitals (`erp` and `apiToken`: `ERP_API_TOKEN`), so two plugins never share one."""
    snake = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", key)
    name = re.sub(r"[^A-Z0-9_]", "_", f"{plugin}_{snake}".upper())
    return name if name[0].isalpha() else f"PLUGIN_{name}"


def _user_config(
    plugin: str, value: object
) -> tuple[dict[str, str], tuple[SettingDeclaration, ...], tuple[SecretDeclaration, ...]]:
    """Claude Code's `userConfig`, as Atlas keeps it: a sensitive option in the vault, the others
    as the plugin's settings, each value as text (`true`, `3`, `a,b`)."""
    if value is None:
        return {}, (), ()
    if not isinstance(value, dict):
        raise ManifestError(f"{MANIFEST}: userConfig is not an object of options.")
    options: dict[str, str] = {}
    settings: list[SettingDeclaration] = []
    secrets: list[SecretDeclaration] = []
    for key, raw in cast("dict[object, object]", value).items():
        where = f"{MANIFEST}: userConfig.{key}"
        if not isinstance(key, str) or not _OPTION.match(key):
            raise ManifestError(f"{where}: a key is letters, digits and _, not a digit first.")
        if not isinstance(raw, dict):
            raise ManifestError(f"{where} is not an object.")
        entry = cast("dict[str, object]", raw)
        unknown = sorted(set(entry) - _OPTION_FIELDS)
        if unknown:
            raise ManifestError(f"{where} has fields Claude Code refuses: {', '.join(unknown)}.")
        if entry.get("type") not in _OPTION_TYPES:
            raise ManifestError(f"{where}: type is one of {', '.join(sorted(_OPTION_TYPES))}.")
        about = ": ".join(
            text for text in (_text(entry, "title"), _text(entry, "description")) if text
        )
        if entry.get("sensitive") is True:
            secret = option_secret(plugin, key)
            options[key] = secret
            secrets.append(SecretDeclaration(secret, about))
            continue
        default = _option_text(entry.get("default"))
        if "${" in default:
            raise ManifestError(f"{where}: a default may not name another value with ${{...}}.")
        options[key] = ""
        settings.append(SettingDeclaration(key, default, about))
    return options, tuple(settings), tuple(secrets)


def _option_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(item) for item in cast("list[object]", value))
    return str(value)


def _servers(
    data: dict[str, Any], mcp_text: str | None, secrets: set[str]
) -> tuple[dict[str, Mapping[str, Any]], tuple[SecretDeclaration, ...]]:
    """The MCP servers of `.mcp.json` and of the manifest's `mcpServers`, the manifest's winning a
    name both declare (Claude Code loads `.mcp.json` first), and the secrets they read that the
    plugin does not declare under `atlas.secrets`: named, they are declared."""
    found: dict[str, Mapping[str, Any]] = {}
    if mcp_text is not None:
        try:
            document: object = json.loads(mcp_text) if mcp_text.strip() else {}
        except ValueError as error:
            raise ManifestError(f"{MCP_FILE} is not JSON: {error}") from error
        if not isinstance(document, dict):
            raise ManifestError(f"{MCP_FILE} is not a JSON object.")
        # With `mcpServers`, or the servers alone, as Claude Code reads it (GitHub's plugin).
        servers = cast("dict[str, Any]", document)
        listed = servers.get("mcpServers", servers)
        found.update(_server_map(listed, MCP_FILE))
    if "mcpServers" in data:
        found.update(_server_map(data["mcpServers"], f"{MANIFEST}: mcpServers"))
    allowed = {PLUGIN_ROOT, PLUGIN_DATA, *secrets}
    implied: dict[str, SecretDeclaration] = {}
    for server, entry in found.items():
        for name in sorted(set(_placeholders(entry)) - allowed):
            if not _SECRET.match(name):
                raise ManifestError(
                    f"The MCP server {server} reads {name}, which is neither a secret's name "
                    "(capitals, digits and _) nor an option the manifest declares."
                )
            implied.setdefault(name, SecretDeclaration(name, f"Read by its MCP server {server}."))
    return found, tuple(implied.values())


def _server_map(value: object, where: str) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, dict):
        raise ManifestError(f"{where} is not an object of servers by name.")
    servers: dict[str, Mapping[str, Any]] = {}
    for name, entry in cast("dict[object, object]", value).items():
        if not isinstance(name, str) or not _SERVER.match(name):
            raise ManifestError(
                f"{where} has a server named {name!r}; use letters, digits, dots, dashes and "
                "underscores."
            )
        if not isinstance(entry, dict):
            raise ManifestError(f"{where}: the server {name} is not an object.")
        servers[name] = cast("dict[str, Any]", entry)
    return servers


def _placeholders(value: object) -> list[str]:
    """Every `${NAME}` in a server's texts, wherever they sit in it."""
    if isinstance(value, str):
        return [match.group(1) for match in _PLACEHOLDER.finditer(value)]
    if isinstance(value, dict):
        items = cast("dict[object, object]", value).values()
        return [name for item in items for name in _placeholders(item)]
    if isinstance(value, list):
        return [name for item in cast("list[object]", value) for name in _placeholders(item)]
    return []


def _server_line(name: str, entry: Mapping[str, Any]) -> str:
    url = entry.get("url")
    if isinstance(url, str) and url:
        return f"Connects to the MCP server {name} at {url}; the model may call its tools."
    parts = [entry.get("command", ""), *cast("list[object]", entry.get("args") or [])]
    command = " ".join(str(part) for part in parts)
    return f"Starts the MCP server {name} ({command}); the model may call its tools."


def server_name(plugin: str, server: str) -> str:
    """A plugin's MCP server as Atlas names it beside the user's: Claude Code's
    `plugin:<plugin>:<server>`, with `_` for the `:` a server's name cannot hold."""
    return f"plugin_{plugin}_{server}"


def plugin_servers(
    manifest: Manifest, root: str, data: str, settings: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The plugin's MCP servers as an MCP file declares them (`{"mcpServers": ...}`), named for
    the plugin, with its folder and its data folder in place of `${CLAUDE_PLUGIN_ROOT}` and
    `${CLAUDE_PLUGIN_DATA}` (written with `/`, as Claude Code writes them on Windows). A secret
    stays `${NAME}`, for the MCP client to fill from the vault; so does a sensitive option
    (Claude Code's `${user_config.KEY}`), under its vault name. Another option is its value in
    `settings`, with any `${` taken out, so a value never names a secret for the client to
    fill."""
    places = {PLUGIN_ROOT: root, PLUGIN_DATA: data}
    for key, secret in manifest.options.items():
        value = (settings or {}).get(key, "")
        places[USER_CONFIG + key] = "${" + secret + "}" if secret else value.replace("${", "$")

    def fill(value: object) -> object:
        if isinstance(value, str):
            return _PLACEHOLDER.sub(lambda m: places.get(m.group(1), m.group(0)), value)
        if isinstance(value, dict):
            return {k: fill(v) for k, v in cast("dict[str, object]", value).items()}
        if isinstance(value, list):
            return [fill(item) for item in cast("list[object]", value)]
        return value

    return {
        "mcpServers": {
            server_name(manifest.name, name): fill(dict(entry))
            for name, entry in manifest.mcp_servers.items()
        }
    }


def _text(data: object, key: str) -> str:
    if not isinstance(data, dict):
        return ""
    value = data.get(key, "")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return value.strip() if isinstance(value, str) else ""


def _names(data: dict[str, Any], key: str, rule: re.Pattern[str]) -> tuple[str, ...]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) and rule.match(item)
        for item in value  # pyright: ignore[reportUnknownVariableType]
    ):
        raise ManifestError(f"{MANIFEST}: atlas.{key} is a list of names.")
    items: list[str] = value  # pyright: ignore[reportUnknownVariableType]
    return tuple(dict.fromkeys(items))


def _entries(data: dict[str, Any], key: str, rule: re.Pattern[str]) -> list[dict[str, str]]:
    """A list of `{name, ...}` objects whose values are text, a plain name standing for
    `{name}`."""
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ManifestError(f"{MANIFEST}: atlas.{key} is a list.")
    entries: list[dict[str, str]] = []
    for item in value:  # pyright: ignore[reportUnknownVariableType]
        entry: object = {"name": item} if isinstance(item, str) else cast(object, item)
        if not isinstance(entry, dict):
            raise ManifestError(f"{MANIFEST}: each of atlas.{key} is a name or an object of text.")
        pairs = cast("dict[object, object]", entry)
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in pairs.items()):
            raise ManifestError(f"{MANIFEST}: each of atlas.{key} is a name or an object of text.")
        fields = {str(k): str(v) for k, v in pairs.items()}
        if not rule.match(fields.get("name", "")):
            raise ManifestError(f"{MANIFEST}: atlas.{key} has a bad name {fields.get('name')!r}.")
        entries.append(fields)
    return entries


# -- pairing ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pairing:
    """Someone a channel does not know, waiting with a code, or allowed."""

    channel: str
    user: str
    name: str
    code: str
    #: When the code was given; an approved pairing keeps it.
    asked_at: datetime
    approved: bool = False
    #: The chat they last wrote from, as the channel names it: where what belongs to no chat
    #: reaches them (an executor session's requests, Claude Code's own relay). Empty until they
    #: write once after it began to be kept.
    chat: str = ""

    def expired(self, now: datetime) -> bool:
        return not self.approved and now - self.asked_at >= PAIRING_TTL


@dataclass(frozen=True, slots=True)
class Admission:
    """Whether someone may talk through a channel, and if not, the code to tell them."""

    allowed: bool
    #: The code they pair with; `None` when they may talk, or no code can be given now.
    code: str | None = None
    #: The code is new: the user's screen hears someone waits.
    new: bool = False


def new_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def admission(known: Pairing | None, others_waiting: int, now: datetime) -> Admission:
    """What to do with a message from someone, given their pairing (if any) and how many other
    codes wait on the channel: let it in, tell them the code they have again, give a new one
    (`new`, with an empty code the caller makes), or give none (too many wait)."""
    if known is not None and known.approved:
        return Admission(True)
    if known is not None and not known.expired(now):
        return Admission(False, known.code)
    if others_waiting >= PAIRING_MOST_PENDING:
        return Admission(False)
    return Admission(False, "", new=True)


def normal_code(code: str) -> str:
    """A code as typed: spaces and case do not matter."""
    return "".join(code.split()).upper()


# -- versions (step 16n) ---------------------------------------------------------------------------
# Versions and the ranges a plugin's `dependencies` asks for (step 16n). Pure.
#
# **The ranges are npm's semver, as Claude Code's `dependencies` reads them:** `~2.1.0`, `^2.0`,
# `>=1.4`, `=2.1.0`, `2.1.0`, `2.x`, `*`, comparators joined by spaces (all must hold) and `||`
# (either may), and `1.2 - 1.4`. A version is `MAJOR.MINOR.PATCH`, with a `-prerelease` that sorts
# before its release and a `+build` that is ignored; a missing part is 0, as npm reads `1.2` in a
# version. Prereleases are compared as npm does when a range names one: here simply in order, so
# `>=1.0.0-beta` takes `1.0.0-rc`.

_VERSION = re.compile(
    r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?)?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
#: A range's version, whose parts may be missing or wild (`x`, `X`, `*`).
_PARTIAL = re.compile(
    r"^v?(?P<major>\d+|[xX*])(?:\.(?P<minor>\d+|[xX*])(?:\.(?P<patch>\d+|[xX*]))?)?"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)
_OPERATOR = re.compile(r"^(<=|>=|<|>|=|\^|~>?)?\s*(.+)$")


class VersionError(ValueError):
    """A version or a range that cannot be read."""


@total_ordering
@dataclass(frozen=True, slots=True)
class Version:
    major: int
    minor: int = 0
    patch: int = 0
    #: The prerelease's dot-separated parts; empty for a release.
    pre: tuple[str, ...] = ()

    def _key(self) -> tuple[int, int, int, int, tuple[tuple[int, int, str], ...]]:
        # A release sorts after its prereleases; numeric parts before text, as semver says.
        parts = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in self.pre)
        return (self.major, self.minor, self.patch, 0 if self.pre else 1, parts)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __str__(self) -> str:
        text = f"{self.major}.{self.minor}.{self.patch}"
        return f"{text}-{'.'.join(self.pre)}" if self.pre else text


def parse_version(text: str) -> Version:
    found = _VERSION.match(text.strip())
    if found is None:
        raise VersionError(f"{text!r} is not a version such as 1.2.0.")
    pre = found["pre"]
    return Version(
        int(found["major"]),
        int(found["minor"] or 0),
        int(found["patch"] or 0),
        tuple(pre.split(".")) if pre else (),
    )


#: One comparator: an operator (`<`, `<=`, `>`, `>=`, `=`) and a version.
type _Comparator = tuple[str, Version]


def satisfies(version: str | Version, wanted: str) -> bool:
    """Whether `version` is in the range `wanted`. Raises `VersionError` for either unreadable."""
    found = parse_version(version) if isinstance(version, str) else version
    return any(all(_holds(found, comparator) for comparator in group) for group in _groups(wanted))


def check_range(wanted: str) -> None:
    """Raises `VersionError` when `wanted` is not a range."""
    _groups(wanted)


def _holds(version: Version, comparator: _Comparator) -> bool:
    operator, bound = comparator
    match operator:
        case "<":
            return version < bound
        case "<=":
            return version <= bound
        case ">":
            return version > bound
        case ">=":
            return version >= bound
        case _:
            return version == bound


def _groups(wanted: str) -> list[list[_Comparator]]:
    groups: list[list[_Comparator]] = []
    for alternative in wanted.split("||"):
        text = alternative.strip()
        hyphen = re.fullmatch(r"(\S+)\s+-\s+(\S+)", text)
        if hyphen is not None:
            groups.append([*_comparators(">=", hyphen[1]), *_upper(hyphen[2])])
            continue
        # npm allows a space between an operator and its version: `>= 1.2`.
        text = re.sub(r"(<=|>=|<|>|=|\^|~>?)\s+", r"\1", text)
        group: list[_Comparator] = []
        for word in text.split() or ["*"]:
            found = _OPERATOR.match(word)
            if found is None:  # pragma: no cover - the pattern takes any word
                raise VersionError(f"{wanted!r} is not a version range.")
            group += _comparators(found[1] or "", found[2], wanted)
        groups.append(group)
    return groups


def _partial(text: str, wanted: str) -> tuple[list[int], tuple[str, ...]]:
    """A range's version: its parts given (a wild one ends them), and its prerelease."""
    found = _PARTIAL.match(text)
    if found is None:
        raise VersionError(f"{wanted!r} is not a version range such as ^1.2.0.")
    parts: list[int] = []
    for name in ("major", "minor", "patch"):
        part = found[name]
        if part is None or not part.isdigit():
            break
        parts.append(int(part))
    pre = found["pre"]
    return parts, tuple(pre.split(".")) if pre else ()


def _comparators(operator: str, text: str, wanted: str = "") -> list[_Comparator]:
    parts, pre = _partial(text, wanted or text)
    full = len(parts) == 3
    low = _at(parts, pre if full else ())
    if not parts:
        return [] if operator in ("", "=", ">=", "<=", "^", "~", "~>") else [("<", Version(0))]
    match operator:
        case "^":
            # The leftmost part that is not 0 stays: ^1.2.3 is <2, ^0.2.3 <0.3, ^0.0.3 <0.0.4.
            if parts[0] > 0 or len(parts) == 1:
                high = Version(parts[0] + 1)
            elif len(parts) == 2 or parts[1] > 0:
                high = Version(0, parts[1] + 1)
            else:
                high = Version(0, 0, parts[2] + 1)
            return [(">=", low), ("<", _floor(high))]
        case "~" | "~>":
            high = Version(parts[0] + 1) if len(parts) == 1 else Version(parts[0], parts[1] + 1)
            return [(">=", low), ("<", _floor(high))]
        case "" | "=":
            if full:
                return [("=", low)]
            return [(">=", low), ("<", _floor(_next(parts)))]
        case ">":
            return [(">", low)] if full else [(">=", _floor(_next(parts)))]
        case ">=":
            return [(">=", low)]
        case "<":
            return [("<", _floor(low))]
        case _:  # "<="
            return [("<=", low)] if full else [("<", _floor(_next(parts)))]


def _upper(text: str) -> list[_Comparator]:
    """The top of `a - b`: `b` itself when whole, below its next part when not."""
    parts, pre = _partial(text, text)
    if not parts:
        return []
    if len(parts) == 3:
        return [("<=", _at(parts, pre))]
    return [("<", _floor(_next(parts)))]


def _next(parts: list[int]) -> Version:
    """The first version past a partial one: past `1.2` is `1.3.0`."""
    bumped = [*parts[:-1], parts[-1] + 1]
    return _at(bumped)


def _at(parts: list[int], pre: tuple[str, ...] = ()) -> Version:
    """The version of `parts`, those missing 0."""
    whole = [*parts, 0, 0, 0]
    return Version(whole[0], whole[1], whole[2], pre)


def _floor(version: Version) -> Version:
    """The lowest version at `version`'s numbers, its first prerelease, so `<2.0.0` leaves out
    `2.0.0-beta` as npm does."""
    return Version(version.major, version.minor, version.patch, ("0",))
