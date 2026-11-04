"""The plugins Atlas makes (step 26, decided with the user on 03/11/2026): the four executors, the
voice, Telegram and the accounts, kept in the repository's own folder `plugins/`, beside the core
and tested with it, but no longer part of it.

- **A plugin of Atlas's is installed like any other**, off until the user turns it on, and only
  then is it the home's: its record in the home's plugins (`official:<name>`) says it is installed.
  Its files stay where the installation keeps them, the checkout the user runs or the folder the
  image copies: Atlas runs it from there, so it is always the version of the Atlas that runs it,
  installing it needs no network, and it updates with Atlas with nothing to copy again (A revisão
  da sequência, item 5). Our choice, marked: a copy in the home would add a second version to keep
  in step and nothing the record does not give.
- **Whoever used one before step 26 keeps it**: a plugin of Atlas's the user ever turned on or off
  is installed by the first start, in the state the user left it.
- **The executors are looked at before they are installed**, so the first run and the executors
  tab offer the harnesses found on the computer: their code is Atlas's own, at the version that
  runs, as the shipped plugins' was.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from atlas.home import utc_now
from atlas.plugins.install import Installed, forget, installed, keep_record
from atlas.plugins.service import PluginError, Plugins
from atlas.plugins.store import OFFICIAL_FOLDER, OFFICIAL_SOURCE, Found, find_plugins

_logger = logging.getLogger("atlas.plugins")


def official_plugins(folder: Path = OFFICIAL_FOLDER) -> dict[str, Found]:
    """The plugins Atlas makes, as the installation holds them, by name; none when the
    installation has no such folder."""
    return {found.name: found for found in find_plugins(folder) if found.manifest is not None}


def official_installed(plugins: Plugins) -> set[str]:
    """The plugins of Atlas's installed in the home, by name."""
    return {
        name
        for name, kept in installed(plugins.folder).items()
        if kept.source.startswith(OFFICIAL_SOURCE)
    }


def install_official(
    plugins: Plugins,
    name: str,
    *,
    folder: Path = OFFICIAL_FOLDER,
    clock: Callable[[], datetime] = utc_now,
) -> Found:
    """Install the plugin of Atlas's named `name`: the home records it, off unless the user had
    it on. Raises `PluginError` for a name Atlas makes no plugin of, or one a plugin of the user's
    has taken."""
    source = official_plugins(folder).get(name)
    if source is None:
        raise PluginError(f"Atlas makes no plugin named {name!r}.")
    if (plugins.folder / name).exists():
        raise PluginError(
            f"There is a plugin {name} in {plugins.folder / name} already: Atlas's own takes its "
            "name. Move yours away first."
        )
    keep_record(
        plugins.folder,
        name,
        Installed(f"{OFFICIAL_SOURCE}{name}", str(source.folder), "", "", "", clock().isoformat()),
    )
    _logger.info("A plugin of Atlas's was installed.", extra={"plugin": name})
    return source


def uninstall_official(plugins: Plugins, name: str) -> None:
    """Take a plugin of Atlas's out of the home: turned off, and no longer installed; its data
    stays, and the installation keeps its files."""
    if name not in official_installed(plugins):
        raise PluginError(f"{name} is not one of Atlas's plugins installed here.")
    if plugins.view(name).enabled:
        plugins.disable(name)
    forget(plugins.folder, name)


def refresh_official(
    plugins: Plugins,
    *,
    folder: Path = OFFICIAL_FOLDER,
    clock: Callable[[], datetime] = utc_now,
) -> list[str]:
    """Install each plugin of Atlas's the user decided on before step 26, when Atlas shipped it
    inside itself and nothing recorded it; the names it installed. One a plugin of the user's has
    taken is logged and left out."""
    decided = plugins.store.states().keys()
    present = installed(plugins.folder)
    done: list[str] = []
    for name in official_plugins(folder):
        if name not in decided or name in present:
            continue
        try:
            install_official(plugins, name, folder=folder, clock=clock)
        except PluginError as error:
            _logger.warning("A plugin of Atlas's was not installed: %s", error)
            continue
        done.append(name)
    return done


def is_official(name: str, folder: Path = OFFICIAL_FOLDER) -> bool:
    return name in official_plugins(folder)
