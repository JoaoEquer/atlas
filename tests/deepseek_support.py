"""The DeepSeek plugin Atlas ships, imported from its folder as Atlas imports it, and the fake
`dsh` (tests/fake_dsh.py) it runs in the tests: the real executor, over real pipes."""

import importlib
import logging
import sys
from pathlib import Path
from types import ModuleType

import httpx2

from atlas.plugins import OFFICIAL_FOLDER, Executor
from atlas.plugins.store import import_plugin

#: The fake `dsh`, as the plugin's `program` setting names a program and its arguments.
FAKE = [sys.executable, str(Path(__file__).parent / "fake_dsh.py")]
#: Where the plugin sits.
FOLDER = OFFICIAL_FOLDER / "deepseek"


def plugin_module(name: str) -> ModuleType:
    """One of the plugin's modules (`executor`, `protocol`, `home`), imported once."""
    package = "atlas_plugin_deepseek"
    if package not in sys.modules:
        import_plugin(FOLDER, "deepseek")
    return importlib.import_module(f"{package}.{name}")


def deepseek(
    data: Path,
    cli: list[str] | None = FAKE,
    *,
    home: Path | None = None,
    api_key: str | None = None,
    session_logs: bool = False,
    transport: httpx2.AsyncBaseTransport | None = None,
) -> Executor:
    """The plugin's executor on `cli`, the fake `dsh` by default."""
    made: Executor = plugin_module("executor").DeepSeek(
        cli,
        logger=logging.getLogger("atlas.plugins.deepseek"),
        data=data,
        home=home,
        api_key=api_key,
        session_logs=session_logs,
        transport=transport,
    )
    return made
