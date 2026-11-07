"""The Gemini CLI plugin Atlas ships, imported from its folder as Atlas imports it, and the fake
`gemini` (tests/fake_gemini.py) it runs in the tests: the real executor, over real pipes."""

import importlib
import logging
import sys
from pathlib import Path
from types import ModuleType

import httpx2

from atlas.plugins import OFFICIAL_FOLDER, Executor
from atlas.plugins.store import import_plugin

#: The fake `gemini`, as the plugin's `program` setting names a program and its arguments.
FAKE = [sys.executable, str(Path(__file__).parent / "fake_gemini.py")]
#: Where the plugin sits.
FOLDER = OFFICIAL_FOLDER / "gemini-cli"


def plugin_module(name: str) -> ModuleType:
    """One of the plugin's modules (`executor`, `protocol`, `home`), imported once."""
    package = "atlas_plugin_gemini_cli"
    if package not in sys.modules:
        import_plugin(FOLDER, "gemini-cli")
    return importlib.import_module(f"{package}.{name}")


def gemini(
    cli: list[str] | None = FAKE,
    *,
    home: Path | None = None,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> Executor:
    """The plugin's executor on `cli`, the fake `gemini` by default."""
    made: Executor = plugin_module("executor").GeminiCli(
        cli, logger=logging.getLogger("atlas.plugins.gemini-cli"), home=home, transport=transport
    )
    return made
