"""Where each harness Atlas ships says its newest version is published, and how its install updates
(SDK version 27), by where the program was found: never a program the user named by hand."""

import json
import logging
from pathlib import Path

import pytest

import claude_code_support
import codex_support
import deepseek_support
import gemini_support
from atlas.plugins import UpdateSource

RELEASES = "https://downloads.claude.ai/claude-code-releases/{}"


def _file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_claude_code_updates_as_it_was_installed(tmp_path: Path) -> None:
    source = claude_code_support.plugin_module("executor").update_source
    folder = tmp_path / ".claude"
    native = _file(tmp_path / ".local" / "bin" / "claude.exe")

    assert source(str(native), folder) == UpdateSource(
        (str(native), "update"), url=RELEASES.format("latest")
    )
    # The channel the user chose in its settings.
    folder.mkdir()
    (folder / "settings.json").write_text(json.dumps({"autoUpdatesChannel": "stable"}), "utf-8")
    assert source(str(native), folder).url == RELEASES.format("stable")

    winget = _file(tmp_path / "Microsoft" / "WinGet" / "Links" / "claude.exe")
    found = source(str(winget), folder)
    assert (found.winget, found.command[:4]) == (
        "Anthropic.ClaudeCode",
        ("winget", "upgrade", "--id", "Anthropic.ClaudeCode"),
    )
    brew = _file(tmp_path / "Caskroom" / "claude-code" / "claude")
    assert source(str(brew), folder) == UpdateSource(
        ("brew", "upgrade", "--cask", "claude-code"), url=RELEASES.format("stable")
    )


@pytest.mark.parametrize(
    ("support", "package", "script"),
    [
        (codex_support, "@openai/codex", "bin/codex.exe"),
        (gemini_support, "@google/gemini-cli", "bundle/gemini.js"),
        (deepseek_support, "@deepseek-ai/dsh", "lib/bin.js"),
    ],
)
def test_an_npm_harness_updates_through_npm_only_when_npm_installed_it(
    support: object, package: str, script: str, tmp_path: Path
) -> None:
    source = support.plugin_module("executor").update_source  # type: ignore[attr-defined]
    installed = _file(tmp_path / "npm" / "node_modules" / package / script)
    elsewhere = _file(tmp_path / "bin" / "harness.exe")

    assert source(str(installed)) == UpdateSource(npm=package)
    assert source(str(elsewhere)) is None


def test_a_program_named_by_hand_says_nothing_of_how_it_updates(tmp_path: Path) -> None:
    executor = claude_code_support.plugin_module("executor")
    logger = logging.getLogger("atlas.plugins.claude-code")
    native = str(_file(tmp_path / ".local" / "bin" / "claude.exe"))

    assert executor.ClaudeCode([native], logger=logger).update_source() is None
    found = executor.ClaudeCode([native], logger=logger, found=True)
    assert found.capabilities().updates
    assert found.update_source() is not None
