"""A harness's newest version, read where its makers publish it, and its update at the user's click
(asked for by the user on 01/11/2026): the version reads, the comparison, and the server's row and
`executor.update` on a fake registry and a command that only writes a file."""

import asyncio
import dataclasses
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from atlas.plugins import Capabilities, Executor, UpdateSource
from atlas.server.updates import FRESH, Newest, UpdateError, command, newer, newest, shown
from claude_code_support import claude_code
from server_support import WAIT, Serve

TAGS = {"latest": "2.1.316", "stable": "2.1.309"}


def _registry(request: httpx2.Request) -> httpx2.Response:
    """npm's dist-tags, WinGet's catalog and Claude Code's releases, as each answers."""
    url = str(request.url)
    if url.startswith("https://registry.npmjs.org/-/package/"):
        return httpx2.Response(200, json=TAGS)
    if url.startswith("https://api.github.com/repos/microsoft/winget-pkgs/"):
        folders = ["2.1.296", "2.1.302", "2.1.4", ".validation"]
        return httpx2.Response(200, json=[{"name": n, "type": "dir"} for n in folders])
    if url.endswith("/claude-code-releases/latest"):
        return httpx2.Response(200, text="2.1.316\n")
    return httpx2.Response(404)


def _read(source: UpdateSource) -> str:
    async def read() -> str:
        transport = httpx2.MockTransport(_registry)
        async with httpx2.AsyncClient(transport=transport) as client:
            return await newest(source, client)

    return asyncio.run(read())


def test_the_newest_version_is_read_where_each_install_updates_from() -> None:
    assert _read(UpdateSource(npm="@google/gemini-cli")) == "2.1.316"
    assert _read(UpdateSource(npm="@google/gemini-cli", tag="stable")) == "2.1.309"
    # WinGet's catalog, which lags: its newest folder by version, not by name.
    assert _read(UpdateSource(("winget",), winget="Anthropic.ClaudeCode")) == "2.1.302"
    releases = "https://downloads.claude.ai/claude-code-releases/latest"
    assert _read(UpdateSource(("claude", "update"), url=releases)) == "2.1.316"
    with pytest.raises(UpdateError, match="could not be read"):
        _read(UpdateSource(url="https://example.com/nothing"))


def test_a_prerelease_comes_before_its_release_and_nothing_unreadable_is_newer() -> None:
    assert newer("2.1.316", "2.1.140")
    assert not newer("2.1.140", "2.1.140")
    assert not newer("2.1.139", "2.1.140")
    assert newer("0.2.1-rc.5", "0.2.1-rc.4")
    assert newer("0.2.1", "0.2.1-rc.5")
    assert not newer("0.2.1-rc.5", "0.2.1")
    assert newer("0.10.0", "0.9.9"), "by number, not by text"
    assert not newer("", "2.1.140")
    assert not newer("2.1.316", "not a version")


def test_an_npm_package_updates_with_the_users_npm_and_a_command_reads_as_typed() -> None:
    argv = command(UpdateSource(npm="@openai/codex"), "0.166.0")
    if argv is not None:  # npm is on this machine
        assert argv[-3:] == ("install", "-g", "@openai/codex@0.166.0")
        assert "npm install -g @openai/codex@0.166.0" in shown(argv)
    assert command(UpdateSource(("claude", "update"), url="x"), "2.1.316") == ("claude", "update")
    assert shown(["cmd", "/c", "npm", "install", "-g", "x@1.0.0"]) == "npm install -g x@1.0.0"


def test_a_version_read_stays_good_for_a_day() -> None:
    source = UpdateSource(npm="@openai/codex")
    newest_read = Newest()
    now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    assert newest_read.due(source, now)
    newest_read.keep(source, "0.166.0", now)
    assert newest_read.get(UpdateSource(npm="@openai/codex")) == "0.166.0"
    assert not newest_read.due(source, now + FRESH / 2)
    assert newest_read.due(source, now + FRESH)


class _Updating:
    """The fake `claude`, saying where its newest version is and how it updates: a command that
    only writes a file, so the test sees it ran."""

    def __init__(self, inner: Executor, marker: Path) -> None:
        self._inner = inner
        self._marker = marker
        self.title = inner.title

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def capabilities(self) -> Capabilities:
        return dataclasses.replace(self._inner.capabilities(), updates=True)

    def update_source(self) -> UpdateSource:
        write = f"open({str(self._marker)!r}, 'w').write('updated')"
        return UpdateSource((sys.executable, "-c", write), npm="@anthropic-ai/claude-code")


def test_the_row_offers_the_newer_version_and_the_owners_click_updates_it(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "updated.txt"
    executor: Any = _Updating(claude_code(), marker)
    server = serve(
        executors={"claude-code": executor}, update_transport=httpx2.MockTransport(_registry)
    )
    with server.client() as client:
        deadline = time.monotonic() + WAIT
        row: dict[str, Any] = {}
        while time.monotonic() < deadline:
            [row] = client.result("workbench.list")["executors"]
            if row["latest"]:
                break
            time.sleep(0.05)
        assert (row["version"], row["latest"], row["updating"]) == ("2.1.140", "2.1.316", False)
        assert Path(sys.executable).name in row["update"]

        # The command ran; the harness is looked for again, and its version then is the one shown.
        monkeypatch.setenv("FAKE_CLAUDE_VERSION", "2.1.316")
        assert client.result("executor.update", {"executor": "claude-code"}) == {
            "updated": True,
            "output": "",
        }
        assert marker.read_text(encoding="utf-8") == "updated"
        [after] = client.result("workbench.list")["executors"]
        assert (after["version"], after["latest"], after["update"]) == ("2.1.316", "", "")

        # Up to date, there is nothing to install.
        refused = client.call("executor.update", {"executor": "claude-code"})
        assert "no update to install" in json.dumps(refused["error"])
