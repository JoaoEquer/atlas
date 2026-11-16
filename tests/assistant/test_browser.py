"""The browser in a conversation: browser_exec waits behind tool search, the model loads it and
drives tests/fake_chrome.py through tests/fake_engine.py. The model is the fake model server; the
database and the tools are real, in a temporary home."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from atlas.__main__ import SCHEMAS
from atlas.assistant import Assistant
from atlas.home import (
    BrowserSettings,
    Database,
    Home,
    ModelSettings,
    Settings,
    WorkspaceSettings,
    migrate,
)
from atlas.models import ToolResult
from atlas.secrets import Vault
from fake_app_host import FakeAppHost
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
TESTS = Path(__file__).parent.parent
FAKE_CHROME = [sys.executable, str(TESTS / "fake_chrome.py")]
FAKE_ENGINE = (sys.executable, str(TESTS / "fake_engine.py"))


def _assistant(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    **browser: object,
) -> Assistant:
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    models = connect(fake, "openrouter", settings=chosen)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(tmp_path))
    return Assistant(
        home,
        settings,
        models,
        vault,
        listener=lambda _c, _e: None,
        **browser,  # pyright: ignore[reportArgumentType]
    )


def test_the_model_finds_the_browser_and_drives_it(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_CHROME_LOG", str(tmp_path / "chrome.jsonl"))
    code = "# Reading the page\nnew_tab('https://example.com')\nprint(page_info()['title'])"
    fake.reply(
        FakeReply(calls=[FakeCall("tool_search", json.dumps({"query": "browser web page"}))]),
        FakeReply(calls=[FakeCall("browser_exec", json.dumps({"code": code, "mode": "headless"}))]),
        FakeReply(text="The page is Example."),
    )
    assistant = _assistant(
        home, vault, fake, connect, tmp_path, chrome=FAKE_CHROME, browser_engine=FAKE_ENGINE
    )

    async def scenario() -> list[str]:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("What is on example.com?")
        await conversation.wait()
        results = [m.text for m in conversation.history if isinstance(m, ToolResult)]
        await assistant.close()
        return results

    results = asyncio.run(scenario())

    first = fake.bodies(CHAT)[0]
    offered = {tool["function"]["name"] for tool in first["tools"]}
    assert "browser_exec" not in offered  # deferred, behind tool search
    search = next(t for t in first["tools"] if t["function"]["name"] == "tool_search")
    assert "browser_exec" in search["function"]["description"]
    assert "Page at https://example.com" in results[1]
    assert results[1].startswith('<untrusted_tool_result source="browser_exec">')
    assert assistant.browser is not None
    assert not (home.browser_folder / "temporary" / "headless").exists()  # closed with Atlas


def test_without_a_browser_installed_there_is_no_browser_exec(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("atlas.assistant.service._installed_browser", lambda: None)
    fake.reply(FakeReply(text="Hi."))
    assistant = _assistant(home, vault, fake, connect, tmp_path)

    async def scenario() -> None:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Hello")
        await conversation.wait()
        await assistant.close()

    asyncio.run(scenario())

    assert assistant.browser is not None
    assert not assistant.browser.available
    search = next(
        (t for t in fake.bodies(CHAT)[0]["tools"] if t["function"]["name"] == "tool_search"),
        None,
    )
    assert search is None or "browser_exec" not in search["function"]["description"]


def test_browser_exec_is_given_only_where_asked(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(
        home, vault, fake, connect, tmp_path, chrome=FAKE_CHROME, browser_engine=FAKE_ENGINE
    )
    shared = assistant._shared  # pyright: ignore[reportPrivateUsage]
    unasked, _, _ = shared.tools("c-1", [], None, None)
    asked, _, _ = shared.tools("c-1", [], None, None, browser=False)
    assert unasked.get("browser_exec") is None
    found = asked.get("browser_exec")
    assert found is not None
    assert "cannot view images" in found.description
    asyncio.run(assistant.close())


def test_the_chrome_setting_names_the_browser(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    migrate(Database(home.database_file), SCHEMAS)
    models = connect(fake, "openrouter")
    settings = Settings(home=home.root, workspace=WorkspaceSettings(tmp_path))
    named = Settings(
        home=settings.home,
        workspace=settings.workspace,
        browser=BrowserSettings(("my-chrome", "--flag")),
    )
    assistant = Assistant(home, named, models, vault, listener=lambda _c, _e: None)
    assert assistant.browser is not None
    asyncio.run(assistant.close())


def test_browser_exec_is_offered_while_the_app_lends_its_browser(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("atlas.assistant.service._installed_browser", lambda: None)
    assistant = _assistant(home, vault, fake, connect, tmp_path, browser_engine=FAKE_ENGINE)
    shared = assistant._shared  # pyright: ignore[reportPrivateUsage]

    async def scenario() -> None:
        assert not assistant.browser.available
        without, _, _ = shared.tools("c-1", [], None, None, browser=True)
        assert without.get("browser_exec") is None
        await assistant.browser.host(FakeAppHost())
        assert assistant.browser.available
        lent, _, _ = shared.tools("c-1", [], None, None, browser=True)
        assert lent.get("browser_exec") is not None
        await assistant.browser.unhost()
        gone, _, _ = shared.tools("c-1", [], None, None, browser=True)
        assert gone.get("browser_exec") is None
        await assistant.close()

    asyncio.run(scenario())
