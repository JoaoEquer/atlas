"""A plugin's probe (step 23, SDK version 30): a routine's source of events in the plugin's Python,
which a routine names as `plugin:<plugin>:<name>`.

The plugin is a real folder in a temporary home, loaded as Atlas loads it, and its probe runs
through the routine runner on a real assistant; what a probe's output then means is the jobs
subject's, proven in tests/jobs/test_probes.py.
"""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.assistant import Assistant, RoutineRunner
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.jobs import Creator, Routine, Schedule, ScheduleKind
from atlas.models import CallLedger
from atlas.plugins import ManifestError, Plugins
from atlas.plugins.model import parse_manifest
from atlas.secrets import Vault
from fake_model import FakeModelServer
from model_support import Connect

CODE = """
def register(ctx):
    async def issues(state):
        seen = state / "seen"
        last = seen.read_text() if seen.exists() else "0"
        seen.write_text("7")
        return f"issues after {last}: #7 Crash on save"

    async def broken(state):
        raise ConnectionError("github.example.com is down")

    ctx.register_probe("issues", issues)
    ctx.register_probe("broken", broken)
"""


def _plugin(root: Path, atlas: dict[str, Any], code: str = CODE) -> None:
    folder = root / "watch"
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "watch", "version": "1.0.0", "atlas": atlas}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (folder / "__init__.py").write_text(code, encoding="utf-8")


@pytest.fixture
def plugins(home: Home, vault: Vault, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, official_folder=tmp_path / "bundled")


def _on(plugins: Plugins) -> None:
    manifest = plugins.view("watch").manifest
    assert manifest is not None
    plugins.enable("watch", manifest.fingerprint())


def test_a_probe_is_declared_and_shown_with_how_a_routine_names_it() -> None:
    manifest = parse_manifest(
        json.dumps({"name": "watch", "atlas": {"api": 30, "probes": ["issues"]}}),
        {"__init__.py"},
        folder_name="watch",
    )
    assert manifest.probes == ("issues",)
    assert any(
        line.startswith(
            "Adds the probe issues: a routine you make with it as its probe (plugin:watch:issues)"
        )
        for line in manifest.declaration()
    )
    bare = parse_manifest(
        json.dumps({"name": "watch", "atlas": {"api": 30}}), {"__init__.py"}, folder_name="watch"
    )
    assert bare.fingerprint() != manifest.fingerprint(), "a new probe asks consent again"


@pytest.mark.parametrize(
    ("atlas", "present", "problem"),
    [
        ({"api": 29, "probes": ["issues"]}, {"__init__.py"}, "need atlas.api 30"),
        ({"api": 30, "probes": ["issues"]}, set(), "brings no __init__.py"),
    ],
)
def test_a_probe_is_refused_when_not_declared_well(
    atlas: dict[str, Any], present: set[str], problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(json.dumps({"name": "watch", "atlas": atlas}), present, folder_name="watch")


def test_a_probe_it_does_not_declare_marks_the_plugin(plugins: Plugins, home: Home) -> None:
    _plugin(home.plugins_folder, {"api": 30, "probes": ["issues"]})
    _on(plugins)
    assert asyncio.run(plugins.parts()).probes == []
    assert "the probe broken, which it does not declare" in str(plugins.view("watch").problem)


def _assistant(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> Assistant:
    chosen = ModelSettings(provider="openrouter")
    models = connect(
        fake, "openrouter", settings=chosen, ledger=CallLedger(Database(home.database_file))
    )
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    return Assistant(home, settings, models, vault, listener=lambda c, e: None)


def _routine(probe: str) -> Routine:
    return Routine(
        id="r1",
        name="issues",
        schedule=Schedule(ScheduleKind.INTERVAL, "every 5m", minutes=5),
        prompt="Triage the new issues.",
        command=None,
        enabled=True,
        created_at=datetime(2026, 11, 4, 13, 0, tzinfo=UTC),
        created_by=Creator.USER,
        probe=probe,
    )


def test_a_routine_runs_a_plugins_probe_with_its_folder_and_a_failure_is_the_probes(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _plugin(home.plugins_folder, {"api": 30, "probes": ["issues", "broken"]})
    _on(plugins)
    assistant = _assistant(home, vault, fake, connect, tmp_path)

    async def scenario() -> list[tuple[bool, str]]:
        try:
            parts = await plugins.parts()
            assert [probe.typed for probe in parts.probes] == [
                "plugin:watch:issues",
                "plugin:watch:broken",
            ]
            await assistant.set_plugins([], {}, {}, probes=parts.probes)
            runner = RoutineRunner(assistant)
            return [
                await runner.probe(_routine(probe))
                for probe in (
                    "plugin:watch:issues",
                    "plugin:watch:issues",
                    "plugin:watch:broken",
                    "plugin:other:issues",
                )
            ]
        finally:
            await plugins.stop()
            await assistant.close()

    first, again, broken, missing = asyncio.run(scenario())

    assert first == (True, "issues after 0: #7 Crash on save")
    assert again == (True, "issues after 7: #7 Crash on save"), "it kept where it stopped"
    assert (home.probes_folder / "r1" / "seen").read_text() == "7"
    assert broken == (False, "ConnectionError: github.example.com is down")
    assert missing == (False, "No plugin that is on brings the probe plugin:other:issues.")
    assert fake.bodies("/v1/chat/completions") == [], "a probe calls no model"
