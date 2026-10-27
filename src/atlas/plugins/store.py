"""Plugins on disk and in the database: their folders, their Python, and what the user decided.

**Where plugins come from**: the ones Atlas ships, in `atlas/plugins/bundled/`, then the user's,
one folder each in the home's `plugins/`. A user's plugin with the name of a shipped one is
refused, not loaded in its place.

**A plugin's Python is imported from its folder** under a name of its own (`atlas_plugin_<name>`),
so two plugins never share a module, and Atlas never imports a plugin that is turned off.

**The tables:** whether each plugin is on and the declaration the user consented to; the settings
the user changed; the people each channel knows (pairing); and which conversation each chat
continues.
"""

import importlib.util
import json
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from atlas.home import Database, Migration, Schema, format_utc, parse_utc, transaction
from atlas.hooks import CommandHook, HookConfigError, parse_hooks
from atlas.plugins.model import (
    AGENTS_FOLDER,
    BIN_FOLDER,
    COMMANDS_FOLDER,
    MANIFEST,
    MCP_FILE,
    MONITORS_FILE,
    PYTHON,
    SCREEN_FILE,
    SKILLS_FOLDER,
    THEMES_FOLDER,
    Manifest,
    ManifestError,
    Pairing,
    parse_manifest,
)
from atlas.skills import command_files, skill_file

#: A plugin's command hooks, where Claude Code keeps them.
HOOKS_FILE = "hooks/hooks.json"

SCHEMA = Schema(
    "plugins",
    (
        Migration(
            1,
            "plugins, their settings, the people channels know and the chats they continue",
            """
CREATE TABLE plugins_state (
    name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    consent TEXT,
    decided_at TEXT NOT NULL
) STRICT;
CREATE TABLE plugins_settings (
    plugin TEXT NOT NULL,
    name TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (plugin, name)
) STRICT;
CREATE TABLE plugins_pairing (
    channel TEXT NOT NULL,
    user TEXT NOT NULL,
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    asked_at TEXT NOT NULL,
    approved INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (channel, user)
) STRICT;
CREATE TABLE plugins_chats (
    channel TEXT NOT NULL,
    chat TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    PRIMARY KEY (channel, chat)
) STRICT;
""",
        ),
        Migration(
            2,
            "the chat each person a channel knows writes from, for what belongs to no chat",
            # Someone let in before is known by a chat named as they are, which a platform gives
            # their own private chat (Telegram's, whose chats are the only ones Atlas reads); the
            # rest are known from the next message they send.
            """
ALTER TABLE plugins_pairing ADD COLUMN chat TEXT NOT NULL DEFAULT '';
UPDATE plugins_pairing SET chat = user
WHERE approved = 1 AND EXISTS (
    SELECT 1 FROM plugins_chats
    WHERE plugins_chats.channel = plugins_pairing.channel
    AND plugins_chats.chat = plugins_pairing.user
);
""",
        ),
    ),
)

#: The plugins Atlas ships.
BUNDLED_FOLDER = Path(__file__).parent / "bundled"


@dataclass(frozen=True, slots=True)
class Found:
    """A plugin's folder, and its manifest or why it cannot be read."""

    folder: Path
    name: str
    manifest: Manifest | None
    problem: str | None
    bundled: bool


def find_plugins(user_folder: Path, bundled_folder: Path = BUNDLED_FOLDER) -> list[Found]:
    """Every plugin folder, the shipped ones first, by name."""
    found: dict[str, Found] = {}
    for root, bundled in ((bundled_folder, True), (user_folder, False)):
        for folder in _folders(root):
            entry = read_plugin(folder, bundled)
            if entry.name in found:
                if found[entry.name].bundled and not bundled:
                    found[f"{entry.name} ({folder})"] = Found(
                        folder,
                        entry.name,
                        None,
                        f"Atlas ships a plugin named {entry.name}; rename this one.",
                        bundled=False,
                    )
                continue
            found[entry.name] = entry
    return list(found.values())


def _folders(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    for folder in sorted(root.iterdir()):
        if folder.is_dir() and not folder.name.startswith((".", "_")):
            yield folder


#: What a plugin brings, where Claude Code looks for it without a manifest.
COMPONENTS = frozenset(
    {SKILLS_FOLDER, COMMANDS_FOLDER, AGENTS_FOLDER, BIN_FOLDER, MCP_FILE, "hooks", "monitors"}
)


def read_plugin(folder: Path, bundled: bool) -> Found:
    """One plugin folder: its manifest, or why it cannot be read. The manifest is optional, as in
    Claude Code: without one, a folder that brings something is the plugin named after it."""
    manifest_file = folder / MANIFEST
    try:
        text = manifest_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        if not {item.name for item in folder.iterdir()} & COMPONENTS:
            why = f"No {MANIFEST} in {folder}, and nothing a plugin brings."
            return Found(folder, folder.name, None, why, bundled)
        text = json.dumps({"name": folder.name})
    except OSError as error:
        return Found(folder, folder.name, None, f"{manifest_file} cannot be read: {error}", bundled)
    present = {item.name for item in folder.iterdir()}
    mcp_file = folder / MCP_FILE
    monitors_file = folder / MONITORS_FILE
    try:
        mcp_text = mcp_file.read_text(encoding="utf-8") if mcp_file.is_file() else None
        monitors_text = (
            monitors_file.read_text(encoding="utf-8") if monitors_file.is_file() else None
        )
        hooks = plugin_hooks(folder, text)
        manifest = parse_manifest(
            text,
            present,
            folder_name=folder.name,
            skills=_skills(folder / SKILLS_FOLDER),
            mcp_text=mcp_text,
            hooks=[hook.summary() for hook in hooks],
            commands=[file.stem for file in command_files(folder / COMMANDS_FOLDER)],
            programs=_programs(folder / BIN_FOLDER),
            agents=[file.stem for file in command_files(folder / AGENTS_FOLDER)],
            monitors_text=monitors_text,
            screen=(folder / SCREEN_FILE).is_file(),
            themes=_themes(folder / THEMES_FOLDER),
        )
    except OSError as error:
        return Found(
            folder, folder.name, None, f"A file of {folder} cannot be read: {error}", bundled
        )
    except (ManifestError, HookConfigError) as error:
        return Found(folder, folder.name, None, str(error), bundled)
    return Found(folder, manifest.name, manifest, None, bundled)


def plugin_hooks(folder: Path, manifest_text: str | None = None) -> list[CommandHook]:
    """A plugin's command hooks (step 16k): its `hooks/hooks.json`, then what its manifest's
    `hooks` names (a `./` file, an inline event map, or a list of both), as Claude Code merges
    them. Raises `HookConfigError`, and `OSError` for a file that cannot be read."""
    hooks: list[CommandHook] = []
    default = folder / HOOKS_FILE
    if default.is_file():
        hooks += _hooks_file(folder, default)
    if manifest_text is None:
        manifest_text = (folder / MANIFEST).read_text(encoding="utf-8")
    try:
        data: object = json.loads(manifest_text)
    except ValueError:
        return hooks  # the manifest's own reading says what is wrong with it
    declared: object = cast("dict[str, Any]", data).get("hooks") if isinstance(data, dict) else None
    entries: list[object] = (
        [] if declared is None
        else cast("list[object]", declared) if isinstance(declared, list)
        else [declared]
    )  # fmt: skip
    for entry in entries:
        if isinstance(entry, str):
            hooks += _hooks_file(folder, _inside(folder, entry))
        else:
            hooks += parse_hooks(entry, f"{MANIFEST}: hooks")
    return hooks


def _hooks_file(folder: Path, file: Path) -> list[CommandHook]:
    where = str(file.relative_to(folder)).replace("\\", "/")
    try:
        document: object = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as error:
        raise HookConfigError(f"{where} is not JSON: {error}") from error
    if not isinstance(document, dict) or "hooks" not in document:
        raise HookConfigError(
            f'{where} must hold its events under "hooks", as Claude Code reads it.'
        )
    return parse_hooks(cast("dict[str, Any]", document)["hooks"], where)


def _inside(folder: Path, path: str) -> Path:
    """A path a manifest names: `./`-relative, inside the plugin, and there (Claude Code's)."""
    if not path.startswith("./"):
        raise HookConfigError(f"{MANIFEST}: the path {path!r} must start with ./")
    found = (folder / path).resolve()
    if not found.is_relative_to(folder.resolve()):
        raise HookConfigError(f"{MANIFEST}: the path {path!r} leads outside the plugin.")
    if not found.is_file():
        raise HookConfigError(f"{MANIFEST}: the path {path!r} is not there.")
    return found


def _themes(folder: Path) -> dict[str, str]:
    """The text of the files of a plugin's `themes/`, by name (version 24)."""
    if not folder.is_dir():
        return {}
    return {
        child.stem: child.read_text(encoding="utf-8", errors="replace")
        for child in sorted(folder.iterdir())
        if child.is_file() and child.suffix == ".json" and not child.name.startswith(".")
    }


def _programs(folder: Path) -> list[str]:
    """The files of a plugin's `bin/`, each a program the model's commands may run."""
    if not folder.is_dir():
        return []
    return sorted(
        child.name
        for child in folder.iterdir()
        if child.is_file() and not child.name.startswith(".")
    )


def _skills(folder: Path) -> list[str]:
    """The folders of a plugin's `skills/` that hold a `SKILL.md`, as Claude Code finds them."""
    if not folder.is_dir():
        return []
    return [
        child.name
        for child in sorted(folder.iterdir())
        if child.is_dir() and not child.name.startswith(".") and skill_file(child) is not None
    ]


def import_plugin(folder: Path, name: str) -> ModuleType:
    """The plugin's Python, imported under a name of its own; again from its files each time, so
    an updated plugin runs its new code."""
    module_name = f"atlas_plugin_{name.replace('-', '_')}"
    for loaded in [m for m in sys.modules if m == module_name or m.startswith(f"{module_name}.")]:
        del sys.modules[loaded]
    spec = importlib.util.spec_from_file_location(
        module_name, folder / PYTHON, submodule_search_locations=[str(folder)]
    )
    if spec is None or spec.loader is None:  # pragma: no cover - a file location always has one
        raise ImportError(f"{folder / PYTHON} cannot be imported.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


@dataclass(frozen=True, slots=True)
class State:
    enabled: bool
    #: The fingerprint of the declaration the user consented to.
    consent: str | None


class PluginStore:
    """What the user decided about plugins, and what their channels know."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def states(self) -> dict[str, State]:
        rows: list[tuple[str, int, str | None]] = self._read(
            "SELECT name, enabled, consent FROM plugins_state", ()
        )
        return {name: State(bool(enabled), consent) for name, enabled, consent in rows}

    def decide(self, name: str, *, enabled: bool, consent: str | None, now: datetime) -> None:
        self._write(
            "INSERT INTO plugins_state (name, enabled, consent, decided_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET enabled = excluded.enabled, "
            "consent = excluded.consent, decided_at = excluded.decided_at",
            (name, int(enabled), consent, format_utc(now)),
        )

    def settings(self, plugin: str) -> dict[str, str]:
        rows: list[tuple[str, str]] = self._read(
            "SELECT name, value FROM plugins_settings WHERE plugin = ?", (plugin,)
        )
        return dict(rows)

    def set_setting(self, plugin: str, name: str, value: str | None) -> None:
        """Keep a setting's value; `None` goes back to the default."""
        if value is None:
            self._write(
                "DELETE FROM plugins_settings WHERE plugin = ? AND name = ?", (plugin, name)
            )
            return
        self._write(
            "INSERT INTO plugins_settings (plugin, name, value) VALUES (?, ?, ?) "
            "ON CONFLICT (plugin, name) DO UPDATE SET value = excluded.value",
            (plugin, name, value),
        )

    # -- pairing ----------------------------------------------------------------------------------

    def pairings(self, channel: str | None = None) -> list[Pairing]:
        sql = "SELECT channel, user, name, code, asked_at, approved, chat FROM plugins_pairing"
        rows: list[tuple[str, str, str, str, str, int, str]] = (
            self._read(sql + " ORDER BY asked_at", ())
            if channel is None
            else self._read(sql + " WHERE channel = ? ORDER BY asked_at", (channel,))
        )
        return [
            Pairing(channel, user, name, code, parse_utc(asked), bool(approved), chat)
            for channel, user, name, code, asked, approved, chat in rows
        ]

    def ask_pairing(self, pairing: Pairing) -> None:
        """Keep a code given to someone, replacing an expired one."""
        self._write(
            "INSERT INTO plugins_pairing (channel, user, name, code, asked_at, approved, chat) "
            "VALUES (?, ?, ?, ?, ?, 0, ?) ON CONFLICT (channel, user) DO UPDATE SET "
            "name = excluded.name, code = excluded.code, asked_at = excluded.asked_at, "
            "approved = 0, chat = excluded.chat",
            (
                pairing.channel,
                pairing.user,
                pairing.name,
                pairing.code,
                format_utc(pairing.asked_at),
                pairing.chat,
            ),
        )

    def wrote_from(self, channel: str, user: str, chat: str) -> None:
        """Someone the channel knows wrote from `chat`: where to reach them from now on."""
        self._write(
            "UPDATE plugins_pairing SET chat = ? WHERE channel = ? AND user = ?",
            (chat, channel, user),
        )

    def approve(self, channel: str, user: str) -> None:
        self._write(
            "UPDATE plugins_pairing SET approved = 1 WHERE channel = ? AND user = ?",
            (channel, user),
        )

    def forget(self, channel: str, user: str) -> bool:
        """Remove someone, waiting or allowed; `False` when the channel did not know them."""
        return (
            self._write(
                "DELETE FROM plugins_pairing WHERE channel = ? AND user = ?", (channel, user)
            )
            > 0
        )

    # -- chats ------------------------------------------------------------------------------------

    def conversation(self, channel: str, chat: str) -> str | None:
        rows: list[tuple[str]] = self._read(
            "SELECT conversation_id FROM plugins_chats WHERE channel = ? AND chat = ?",
            (channel, chat),
        )
        return rows[0][0] if rows else None

    def bind(self, channel: str, chat: str, conversation_id: str) -> None:
        """The chat continues `conversation_id` from now on."""
        self._write(
            "INSERT INTO plugins_chats (channel, chat, conversation_id) VALUES (?, ?, ?) "
            "ON CONFLICT (channel, chat) DO UPDATE SET conversation_id = excluded.conversation_id",
            (channel, chat, conversation_id),
        )

    def chats(self, channel: str) -> dict[str, str]:
        """Each chat of the channel, by the conversation it continues."""
        rows: list[tuple[str, str]] = self._read(
            "SELECT conversation_id, chat FROM plugins_chats WHERE channel = ?", (channel,)
        )
        return dict(rows)

    # -- the database -----------------------------------------------------------------------------

    def _read(self, sql: str, values: tuple[object, ...]) -> list[Any]:
        connection = self._database.connect()
        try:
            return connection.execute(sql, values).fetchall()
        finally:
            connection.close()

    def _write(self, sql: str, values: tuple[object, ...]) -> int:
        connection = self._database.connect()
        try:
            with transaction(connection):
                cursor: sqlite3.Cursor = connection.execute(sql, values)
                return cursor.rowcount
        finally:
            connection.close()
