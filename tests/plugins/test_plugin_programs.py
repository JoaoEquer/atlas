"""A folder of a plugin's own data on the model's PATH (SDK version 31): where a program it fetched,
the GitHub plugin's `gh`, is found by the model's commands. The plugin is a real folder in a
temporary home, loaded as Atlas loads it."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Home, migrate
from atlas.plugins import Plugins
from atlas.secrets import Vault

CODE = """
def register(ctx):
    async def status(params):
        return {}

    ctx.register_call("status", status)
    ctx.add_programs(ctx.data_folder / "bin")
"""


def _plugin(root: Path, atlas: dict[str, Any], code: str = CODE) -> None:
    folder = root / "tools"
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "tools", "version": "1.0.0", "atlas": atlas}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (folder / "__init__.py").write_text(code, encoding="utf-8")


@pytest.fixture
def plugins(home: Home, vault: Vault, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, official_folder=tmp_path / "bundled")


def _on(plugins: Plugins) -> None:
    manifest = plugins.view("tools").manifest
    assert manifest is not None
    plugins.enable("tools", manifest.fingerprint())


def test_a_folder_of_its_data_goes_on_the_models_path(plugins: Plugins, home: Home) -> None:
    _plugin(home.plugins_folder, {"api": 31, "calls": ["status"]})
    _on(plugins)

    parts = asyncio.run(plugins.parts())

    assert parts.programs == [(home.plugin_data_folder / "tools" / "bin").resolve()]


@pytest.mark.parametrize(
    ("api", "folder", "problem"),
    [
        (30, 'ctx.data_folder / "bin"', "needs atlas.api 31"),
        (31, 'ctx.data_folder / ".." / "other"', "only a folder of its own data"),
    ],
)
def test_a_folder_not_its_own_or_too_early_marks_the_plugin(
    plugins: Plugins, home: Home, api: int, folder: str, problem: str
) -> None:
    _plugin(
        home.plugins_folder,
        {"api": api, "calls": ["status"]},
        CODE.replace('ctx.data_folder / "bin"', folder),
    )
    _on(plugins)

    assert asyncio.run(plugins.parts()).programs == []
    assert problem in str(plugins.view("tools").problem)
