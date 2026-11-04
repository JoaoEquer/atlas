"""Writing a plugin: a new one's folder, and checking one before it is turned on (step 16j).

**`atlas plugins new`** is `claude plugin init`: a folder in Claude Code's format, with Atlas's part
in the manifest, a tool its Python registers and a skill, each the smallest that works, to be
changed into the plugin's own.

**`atlas plugins validate`** is `claude plugin validate`: the folder
read by the real loader, and its Python loaded the way Atlas loads it, in a home of its own that is
thrown away, so nothing of the user's home is touched or read (no secret is in its vault). What it
registers is said, and what is wrong with the fix. It is no sandbox: the plugin's Python runs,
with the user's permissions.
"""

import asyncio
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from atlas.home import Database, Redactor, VaultSettings, migrate, open_home
from atlas.plugins.model import MANIFEST, PYTHON, SKILLS_FOLDER, TOOLS_API, Manifest
from atlas.plugins.service import Plugins
from atlas.plugins.store import SCHEMA, Found, read_plugin
from atlas.secrets import Vault

_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$|^[a-z0-9]$")

_PYTHON = '''"""{title}: the plugin's Python. From Atlas it imports only the SDK."""

from typing import Any

from atlas.plugins.sdk import PluginContext, ToolHost


def register(ctx: PluginContext) -> None:
    """Runs once when the plugin is turned on: it only registers. The work starts when Atlas calls
    what it registered."""

    async def hello(arguments: dict[str, Any], host: ToolHost) -> str:
        host.progress("Saying hello")
        return f"Hello, {{arguments.get('name') or 'world'}}!"

    ctx.register_tool(
        "{tool}",
        "Say hello to someone by name.",
        {{
            "type": "object",
            "properties": {{"name": {{"type": "string", "description": "Who to greet."}}}},
            "required": ["name"],
        }},
        hello,
        read_only=True,
    )
'''

_SKILL = """---
name: {name}
description: How to use the {name} plugin.
---
Say what this skill teaches: when to use the plugin's tools, and in what order.

To greet someone, call the `{tool}` tool with their name.
"""


@dataclass(frozen=True, slots=True)
class Validation:
    """What `validate` found."""

    folder: Path
    manifest: Manifest | None
    #: What is wrong, with what to do; empty when it can be turned on.
    problems: tuple[str, ...]
    #: What its Python registered: `tool echo_upper`, `channel telegram`.
    registered: tuple[str, ...] = field(default_factory=tuple[str, ...])

    @property
    def ok(self) -> bool:
        return not self.problems


def new_plugin(parent: Path, name: str) -> Path:
    """A new plugin's folder in `parent`, named `name`. Raises `ValueError` for a name Claude Code
    would refuse, or a folder already there."""
    if not _NAME.match(name):
        raise ValueError(
            f"{name!r} cannot name a plugin: use kebab-case, lowercase letters, digits and hyphens."
        )
    folder = parent / name
    if folder.exists():
        raise ValueError(f"{folder} is already there.")
    tool = re.sub(r"[^a-z0-9]", "_", name) + "_hello"
    title = name.replace("-", " ").capitalize()
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": f"{title}: say what it adds to Atlas.",
        "author": {"name": "you"},
        "atlas": {"api": TOOLS_API, "tools": [tool]},
    }
    (folder / ".claude-plugin").mkdir(parents=True)
    (folder / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (folder / PYTHON).write_text(_PYTHON.format(title=title, tool=tool), encoding="utf-8")
    skill = folder / SKILLS_FOLDER / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(_SKILL.format(name=name, tool=tool), encoding="utf-8")
    return folder


def validate(folder: Path) -> Validation:
    """Check the plugin in `folder` as Atlas would load it, in a home thrown away after."""
    folder = folder.resolve()
    if not folder.is_dir():
        return Validation(folder, None, (f"There is no folder {folder}.",))
    found: Found = read_plugin(folder, bundled=False)
    if found.manifest is None:
        return Validation(folder, None, (str(found.problem),))
    manifest = found.manifest
    with tempfile.TemporaryDirectory(prefix="atlas-plugin-check-") as scratch:
        home = open_home(Path(scratch) / "home")
        shutil.copytree(
            folder,
            home.plugins_folder / folder.name,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules", ".venv"),
        )
        database = Database(home.database_file)
        migrate(database, (SCHEMA,))
        vault = Vault(home.secrets_folder, VaultSettings(), Redactor())
        plugins = Plugins(home, database, vault, official_folder=Path(scratch) / "none")
        view = plugins.view(manifest.name)
        if view.problem is not None:
            return Validation(folder, manifest, (view.problem,))
        plugins.enable(manifest.name, manifest.fingerprint())
        registered = asyncio.run(_load(plugins)) if manifest.python else ()
        problem = plugins.view(manifest.name).problem
    problems = [] if problem is None else [problem]
    if manifest.python:
        missing = [
            f"{what} {name}"
            for what, names in (
                ("channel", manifest.channels),
                ("executor", manifest.executors),
                ("tool", manifest.tools),
            )
            for name in names
            if f"{what} {name}" not in registered
        ]
        if missing and problem is None:
            problems.append(
                f"It declares {', '.join(missing)} but its register(ctx) does not register them."
            )
    return Validation(folder, manifest, tuple(problems), registered)


async def _load(plugins: Plugins) -> tuple[str, ...]:
    try:
        parts = await plugins.parts()
        cli = await plugins.cli_commands()
        channels = await plugins.channels()
        executors = await plugins.executors()
    finally:
        await plugins.stop()
    return (
        *(f"tool {tool.name}" for tool in parts.tools),
        *(f"hook {hook.event}" for hook in parts.hooks),
        *(f"command /{command.typed}" for command in parts.commands),
        *(f"terminal command atlas {command.name}" for command in cli),
        *(f"channel {channel.name}" for channel in channels),
        *(f"executor {executor.name}" for executor in executors),
    )
