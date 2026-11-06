"""The Claude Code plugin Atlas ships, imported from its folder as Atlas imports it, and the fake
`claude` (tests/fake_claude.py) it runs in the tests: the real executor, over real pipes."""

import importlib
import logging
import sys
from pathlib import Path
from types import ModuleType

from atlas.plugins import OFFICIAL_FOLDER, Executor
from atlas.plugins.store import import_plugin

#: The fake `claude`, as the plugin's `program` setting names a program and its arguments.
FAKE = [sys.executable, str(Path(__file__).parent / "fake_claude.py")]
#: Where the plugin sits.
FOLDER = OFFICIAL_FOLDER / "claude-code"


def plugin_module(name: str) -> ModuleType:
    """One of the plugin's modules (`executor`, `protocol`, `transcript`), imported once."""
    package = "atlas_plugin_claude_code"
    if package not in sys.modules:
        import_plugin(FOLDER, "claude-code")
    return importlib.import_module(f"{package}.{name}")


def claude_code(cli: list[str] | None = FAKE, *, sessions: Path | None = None) -> Executor:
    """The plugin's executor on `cli`, the fake `claude` by default."""
    made: Executor = plugin_module("executor").ClaudeCode(
        cli, logger=logging.getLogger("atlas.plugins.claude-code"), sessions=sessions
    )
    return made
