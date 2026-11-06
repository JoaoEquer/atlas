"""The plugins Atlas makes (`plugins/`, step 26), their modules imported as Atlas imports a plugin:
from its folder, under a name of its own (`atlas_plugin_<name>`)."""

import importlib
import sys
from types import ModuleType

from atlas.plugins import OFFICIAL_FOLDER
from atlas.plugins.store import import_plugin


def official_module(plugin: str, name: str = "") -> ModuleType:
    """The plugin's package, or one of its modules (`bot`, `gemini`), imported once."""
    package = f"atlas_plugin_{plugin.replace('-', '_')}"
    if package not in sys.modules:
        import_plugin(OFFICIAL_FOLDER / plugin, plugin)
    return importlib.import_module(f"{package}.{name}" if name else package)
