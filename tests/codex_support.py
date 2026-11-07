"""The Codex plugin Atlas ships, imported from its folder as Atlas imports it, and the fake `codex`
(tests/fake_codex.py) it runs in the tests: the real executor, over real pipes."""

import importlib
import logging
import sys
from pathlib import Path
from types import ModuleType

from atlas.plugins import OFFICIAL_FOLDER, Executor
from atlas.plugins.store import import_plugin

#: The fake `codex`, as the plugin's `program` setting names a program and its arguments.
FAKE = [sys.executable, str(Path(__file__).parent / "fake_codex.py")]
#: Where the plugin sits.
FOLDER = OFFICIAL_FOLDER / "codex"


def plugin_module(name: str) -> ModuleType:
    """One of the plugin's modules (`executor`, `protocol`, `server`), imported once."""
    package = "atlas_plugin_codex"
    if package not in sys.modules:
        import_plugin(FOLDER, "codex")
    return importlib.import_module(f"{package}.{name}")


def codex(cli: list[str] | None = FAKE, *, home: Path | None = None) -> Executor:
    """The plugin's executor on `cli`, the fake `codex` by default."""
    made: Executor = plugin_module("executor").Codex(
        cli, logger=logging.getLogger("atlas.plugins.codex"), home=home
    )
    return made
