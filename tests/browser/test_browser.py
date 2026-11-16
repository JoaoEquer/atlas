"""The browser as Atlas drives it: real processes and pipes, against tests/fake_chrome.py, which
opens a DevTools port as Chrome does, and tests/fake_engine.py, which runs the code as
browser-harness does with helpers that pretend."""

import asyncio
import contextlib
import json
import os
import re
import signal
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import ToolOutput
from atlas.browser import Browser, BrowserMode
from atlas.browser.model import session_name
from fake_app_host import FakeAppHost

TESTS = Path(__file__).parent.parent
FAKE_CHROME = [sys.executable, str(TESTS / "fake_chrome.py")]
FAKE_ENGINE = (sys.executable, str(TESTS / "fake_engine.py"))
SECRET = "sk-test-0123456789abcdef"


class Setup:
    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.folder = folder / "browser"
        self.chrome_log = folder / "chrome.jsonl"
        self.engine_log = folder / "engine.jsonl"
        monkeypatch.setenv("FAKE_CHROME_LOG", str(self.chrome_log))
        monkeypatch.setenv("FAKE_ENGINE_LOG", str(self.engine_log))
        self.resolved: dict[str, list[str]] = {}

    def browser(self) -> Browser:
        async def resolve(host: str) -> list[str]:
            return self.resolved.get(host, [])

        return Browser(
            self.folder,
            FAKE_CHROME,
            redact=lambda text: text.replace(SECRET, "[REDACTED]"),
            engine=FAKE_ENGINE,
            resolve=resolve,
            environ=dict(os.environ),
        )

    def runs(self) -> list[dict[str, Any]]:
        if not self.engine_log.exists():
            return []
        lines = self.engine_log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def chromes(self) -> list[dict[str, Any]]:
        lines = self.chrome_log.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Setup:
    return Setup(tmp_path, monkeypatch)


def _with(setup: Setup, body: Callable[[Browser], Awaitable[None]]) -> None:
    async def main() -> None:
        browser = setup.browser()
        try:
            await body(browser)
        finally:
            await browser.close()

    asyncio.run(main())


def _result(text: str) -> dict[str, Any]:
    inner = text.split("\n\n", 2)[-1].rsplit("\n</untrusted_tool_result>", 1)[0]
    return json.loads(inner)


def test_a_call_runs_in_the_conversations_session_and_the_browser_lasts(setup: Setup) -> None:
    async def body(browser: Browser) -> None:
        first = await browser.run(
            "c-1", "# Opening the page\nnew_tab('https://example.com')", BrowserMode.PROFILE, 30
        )
        assert '<untrusted_tool_result source="browser_exec">' in first.text
        assert _result(first.text)["success"] is True
        second = await browser.run("c-1", "print(page_info()['url'])", BrowserMode.PROFILE, 30)
        assert _result(second.text)["output"].strip() == "https://example.com"

    _with(setup, body)
    runs = setup.runs()
    code_runs = [run for run in runs if "code" in run]
    assert len(code_runs) == 2
    variables = code_runs[0]["variables"]
    assert variables["BU_CDP_WS"].startswith("ws://127.0.0.1:")
    assert variables["BU_NAME"].startswith("atlas-profile-")
    assert variables["BH_TELEMETRY"] == "0"
    assert variables["BH_UPDATE_CHECK"] == "0"
    assert Path(variables["BH_AGENT_WORKSPACE"]).is_dir()
    # The engine's Unix socket lives in a short folder: Linux caps its address at 108 bytes.
    if sys.platform == "win32":
        assert "BH_RUNTIME_DIR" not in variables
    else:
        socket = Path(variables["BH_RUNTIME_DIR"]) / f"bu-{variables['BU_NAME']}.sock"
        assert len(str(socket).encode()) < 104, socket
    # Each conversation pinned to its own tab, the code after.
    assert "_atlas_ensure_own_tab()" in code_runs[0]["code"]
    assert code_runs[0]["code"].endswith("new_tab('https://example.com')")
    # One Chrome for both calls, on Atlas's profile, with a window; closed at the end.
    chromes = setup.chromes()
    assert len(chromes) == 1
    assert f"--user-data-dir={setup.folder / 'profile'}" in chromes[0]["argv"]
    assert "--headless=new" not in chromes[0]["argv"]
    assert not (setup.folder / "profile" / "DevToolsActivePort").exists()


def test_the_output_is_redacted_and_the_errors_cut(setup: Setup) -> None:
    async def body(browser: Browser) -> None:
        result = await browser.run(
            "c-1",
            f"import sys\nprint('key {SECRET}')\nsys.stderr.write('x' * 5000 + '{SECRET}')\n"
            "raise SystemExit(3)",
            BrowserMode.HEADLESS,
            30,
        )
        found = _result(result.text)
        assert found["success"] is False
        assert found["exit_code"] == 3
        assert SECRET not in result.text
        assert "[REDACTED]" in found["output"]
        assert found["stderr"].startswith("… ")
        assert len(found["stderr"]) == 4_002

    _with(setup, body)


def test_cloud_metadata_and_vault_secrets_are_never_reached(setup: Setup) -> None:
    setup.resolved["sneaky.example"] = ["169.254.169.254"]

    async def body(browser: Browser) -> None:
        for code in (
            "goto_url('http://169.254.169.254/latest/meta-data')",
            "goto_url('http://metadata.google.internal/computeMetadata')",
            "goto_url('https://sneaky.example/')",
        ):
            refused = await browser.run("c-1", code, BrowserMode.HEADLESS, 30)
            assert "cloud metadata" in json.loads(refused.text)["error"]
        refused = await browser.run(
            "c-1", f"goto_url('https://a.example/?key={SECRET}')", BrowserMode.HEADLESS, 30
        )
        assert "secret from Atlas's vault" in json.loads(refused.text)["error"]
        # A private address is a local browser's to reach.
        allowed = await browser.run(
            "c-1", "goto_url('http://192.168.0.10/router')", BrowserMode.HEADLESS, 30
        )
        assert _result(allowed.text)["success"] is True

    _with(setup, body)
    assert len([run for run in setup.runs() if "code" in run]) == 1


def test_a_call_that_runs_out_of_time_is_stopped(setup: Setup) -> None:
    async def body(browser: Browser) -> None:
        started = asyncio.get_running_loop().time()
        result = await browser.run("c-1", "time.sleep(30)", BrowserMode.HEADLESS, 1)
        assert asyncio.get_running_loop().time() - started < 15
        assert "timed out after 1s" in json.loads(result.text)["error"]

    _with(setup, body)


def test_a_closed_browser_opens_again_and_its_sessions_restart(setup: Setup) -> None:
    async def body(browser: Browser) -> None:
        await browser.run("c-1", "new_tab('https://a.example')", BrowserMode.CLEAN, 30)
        temporary = setup.folder / "temporary" / "clean"
        assert temporary.is_dir()
        # The user closes the window.
        pid = setup.chromes()[0]["pid"]
        await _kill(pid)
        result = await browser.run("c-1", "print('again')", BrowserMode.CLEAN, 30)
        assert _result(result.text)["output"].strip() == "again"

    _with(setup, body)
    assert len(setup.chromes()) == 2
    assert next(run["reload"] for run in setup.runs() if "reload" in run).startswith("atlas-clean-")
    # A temporary profile goes with its browser.
    assert not (setup.folder / "temporary" / "clean").exists()


def test_a_chrome_already_open_on_the_profile_is_used(setup: Setup) -> None:
    async def body() -> None:
        first = setup.browser()
        await first.run("c-1", "print(1)", BrowserMode.PROFILE, 30)
        # Another Atlas finds it running, as after a restart that left the window open.
        second = setup.browser()
        result = await second.run("c-1", "print(2)", BrowserMode.PROFILE, 30)
        assert _result(result.text)["success"] is True
        await second.close()
        await first.close()

    asyncio.run(body())
    assert len(setup.chromes()) == 1


def test_a_launcher_that_hands_the_browser_off_and_ends_cleanly_is_waited_for(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Edge's launcher on Windows ends with code 0 and another process writes the endpoint.
    monkeypatch.setenv("FAKE_CHROME_HANDOFF", "1")

    async def body(browser: Browser) -> None:
        result = await browser.run("c-1", "print('here')", BrowserMode.HEADLESS, 30)
        assert _result(result.text)["output"].strip() == "here"

    _with(setup, body)
    assert len(setup.chromes()) == 1
    # Closed, the browser that took over stops and its temporary profile goes with it.
    assert not (setup.folder / "temporary" / "headless").exists()


def test_a_browser_that_outlives_its_close_keeps_its_endpoint_for_the_next_start(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The browser that took over ignores Browser.close, and the launcher Atlas waited on is gone.
    monkeypatch.setenv("FAKE_CHROME_HANDOFF", "1")
    monkeypatch.setenv("FAKE_CHROME_STUBBORN", "1")
    monkeypatch.setattr("atlas.browser.store.CLOSE_SECONDS", 0.5)

    async def body(browser: Browser) -> None:
        result = await browser.run("c-1", "print('here')", BrowserMode.PROFILE, 30)
        assert _result(result.text)["output"].strip() == "here"

    try:
        _with(setup, body)
        assert (setup.folder / "profile" / "DevToolsActivePort").exists()
        # The next start finds it running and uses it, instead of a second launcher that would fail.
        _with(setup, body)
        assert len(setup.chromes()) == 1
    finally:
        for chrome in setup.chromes():
            with contextlib.suppress(OSError):
                os.kill(chrome["pid"], signal.SIGTERM)


def test_a_browser_that_cannot_start_says_why(
    setup: Setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("atlas.browser.store.HANDOFF_SECONDS", 0.2)

    async def body() -> None:
        browser = Browser(
            setup.folder,
            [str(tmp_path / "no-such-chrome")],
            redact=str,
            engine=FAKE_ENGINE,
        )
        result = await browser.run("c-1", "print(1)", BrowserMode.HEADLESS, 30)
        assert "could not start" in json.loads(result.text)["error"]
        ended = Browser(
            setup.folder,
            [sys.executable, "-c", "pass"],
            redact=str,
            engine=FAKE_ENGINE,
        )
        result = await ended.run("c-1", "print(1)", BrowserMode.HEADLESS, 30)
        assert "ended as it started" in json.loads(result.text)["error"]

    asyncio.run(body())


def test_the_tool(setup: Setup) -> None:
    async def body(browser: Browser) -> None:
        tool = browser.tool("c-1", sees_images=True)
        assert tool.name == "browser_exec"
        assert tool.deferred
        assert tool.failure_tolerant
        assert "attached to your context" in tool.description
        assert "cannot view images" in browser.tool("c-1", sees_images=False).description
        assert "No code provided" in str(await tool.run({"code": "  "}))
        assert "Unknown mode" in str(await tool.run({"code": "print(1)", "mode": "incognito"}))
        said = await tool.run({"code": "print('hi')", "mode": "headless", "timeout_s": 30})
        assert isinstance(said, str)
        assert _result(said)["output"].strip() == "hi"
        shot = await tool.run({"code": "print(capture_screenshot())", "mode": "headless"})
        assert isinstance(shot, ToolOutput)
        assert shot.images[0].media_type == "image/png"
        assert shot.images[0].data.startswith(b"\x89PNG")

    _with(setup, body)


def test_an_old_screenshot_is_not_sent_again(setup: Setup) -> None:
    async def body(browser: Browser) -> None:
        old = setup.folder / "old.png"
        old.parent.mkdir(parents=True, exist_ok=True)
        old.write_bytes(b"\x89PNG old")
        os.utime(old, (1_000_000, 1_000_000))
        result = await browser.run("c-1", f"print({str(old)!r})", BrowserMode.HEADLESS, 30)
        assert result.images == ()

    _with(setup, body)


async def _kill(pid: int) -> None:
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/T", "/F", "/PID", str(pid),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )  # fmt: skip
        await killer.wait()
    else:
        os.kill(pid, 9)
    await asyncio.sleep(0.5)


# -- the app's browser ---------------------------------------------------------------------------


def _app_setup(setup: Setup, *, chrome: bool = True) -> Browser:
    async def resolve(host: str) -> list[str]:
        return setup.resolved.get(host, [])

    return Browser(
        setup.folder,
        FAKE_CHROME if chrome else None,
        redact=lambda text: text.replace(SECRET, "[REDACTED]"),
        engine=FAKE_ENGINE,
        resolve=resolve,
        environ=dict(os.environ),
    )


def _code_runs(setup: Setup) -> list[dict[str, Any]]:
    return [run for run in setup.runs() if "code" in run]


def test_a_profile_call_runs_in_the_app_browser_when_it_is_lent(setup: Setup) -> None:
    async def main() -> None:
        browser = _app_setup(setup)
        assert browser.available
        await browser.host(FakeAppHost())
        try:
            first = await browser.run("c-1", "print('one')", BrowserMode.PROFILE, 30)
            assert _result(first.text)["output"].strip() == "one"
            assert "note" not in _result(first.text)
            await browser.run("c-1", "print('two')", BrowserMode.PROFILE, 30)
            await browser.run("c-2", "print('three')", BrowserMode.PROFILE, 30)
        finally:
            await browser.close()

    asyncio.run(main())
    runs = setup.runs()
    codes = _code_runs(setup)
    assert len(codes) == 3
    variables = codes[0]["variables"]
    # The engine connects to the endpoint, a path of this conversation's alone; no Chrome opened.
    assert re.fullmatch(r"ws://127\.0\.0\.1:\d+/devtools/browser/[\w-]{43}", variables["BU_CDP_WS"])
    assert variables["BU_CDP_WS"] != codes[2]["variables"]["BU_CDP_WS"]
    assert variables["BU_NAME"].startswith("atlas-app-")
    assert not setup.chrome_log.exists()
    # Without the tab preamble: the engine's daemon makes the conversation's tab itself.
    assert codes[0]["code"] == "print('one')"
    # The agent's workspace is the profile mode's, whatever the browser.
    profile_session = session_name("c-1", BrowserMode.PROFILE)
    assert variables["BH_AGENT_WORKSPACE"] == str(setup.folder / "workspaces" / profile_session)
    # The first call of each conversation stops the daemon of an earlier life, once.
    kinds = ["reload" if "reload" in run else "code" for run in runs]
    assert kinds[:2] == ["reload", "code"]
    assert runs[0]["reload"].startswith("atlas-app-")
    assert kinds[:3].count("reload") == 1
    assert kinds.count("reload") >= 2


def test_the_throwaway_modes_stay_in_chrome_even_with_the_app_lent(setup: Setup) -> None:
    async def main() -> None:
        browser = _app_setup(setup)
        await browser.host(FakeAppHost())
        try:
            for mode in (BrowserMode.CLEAN, BrowserMode.HEADLESS):
                result = await browser.run("c-1", "print(1)", mode, 30)
                assert _result(result.text)["success"] is True
        finally:
            await browser.close()

    asyncio.run(main())
    assert len(setup.chromes()) == 2
    names = [run["variables"]["BU_NAME"] for run in _code_runs(setup)]
    assert names[0].startswith("atlas-clean-")
    assert names[1].startswith("atlas-headless-")


def test_without_chrome_the_app_browser_alone_serves_the_profile_mode(setup: Setup) -> None:
    async def main() -> None:
        browser = _app_setup(setup, chrome=False)
        assert not browser.available
        nothing = await browser.run("c-1", "print(1)", BrowserMode.PROFILE, 30)
        assert "no browser to drive" in json.loads(nothing.text)["error"]
        await browser.host(FakeAppHost())
        assert browser.available
        try:
            served = await browser.run("c-1", "print(2)", BrowserMode.PROFILE, 30)
            assert _result(served.text)["output"].strip() == "2"
            for mode in (BrowserMode.CLEAN, BrowserMode.HEADLESS):
                refused = await browser.run("c-1", "print(3)", mode, 30)
                assert "need Chrome, Edge or Chromium" in json.loads(refused.text)["error"]
        finally:
            await browser.close()
        assert not browser.available

    asyncio.run(main())
    assert len(_code_runs(setup)) == 1


def test_the_first_call_in_another_browser_says_so(setup: Setup) -> None:
    async def call(browser: Browser, code: str) -> dict[str, Any]:
        return _result((await browser.run("c-1", code, BrowserMode.PROFILE, 30)).text)

    async def main() -> None:
        browser = _app_setup(setup)
        try:
            noted = [await call(browser, "print(1)")]
            await browser.host(FakeAppHost())
            noted.append(await call(browser, "print(2)"))
            noted.append(await call(browser, "print(3)"))
            # The app closes between calls: the next one runs in Chrome, and says so, once.
            await browser.unhost()
            noted.append(await call(browser, "print(4)"))
            noted.append(await call(browser, "print(5)"))
        finally:
            await browser.close()
        assert [found.get("note") is not None for found in noted] == [
            False,
            True,
            False,
            True,
            False,
        ]
        assert "app's browser" in noted[1]["note"]
        assert "Chrome window" in noted[3]["note"]

    asyncio.run(main())


def test_an_app_that_closes_during_a_call_fails_it_with_a_note(setup: Setup) -> None:
    async def main() -> None:
        browser = _app_setup(setup)
        await browser.host(FakeAppHost())
        try:
            running = asyncio.create_task(
                browser.run("c-1", "time.sleep(2)\nprint('late')", BrowserMode.PROFILE, 30)
            )
            for _ in range(200):
                if _code_runs(setup):
                    break
                await asyncio.sleep(0.05)
            await browser.unhost()
            result = _result((await running).text)
            assert result["success"] is False
            assert "app closed during this call" in result["note"]
            assert "Chrome window" in result["note"]
            # It is not repeated for the user: the next call runs in Chrome, in a new tab.
            again = await browser.run("c-1", "print('again')", BrowserMode.PROFILE, 30)
            after = _result(again.text)
            assert after["output"].strip() == "again"
            assert "Chrome window" in after["note"]
        finally:
            await browser.close()

    asyncio.run(main())
    assert len(setup.chromes()) == 1
    reloaded = [run["reload"] for run in setup.runs() if "reload" in run]
    assert sum(name.startswith("atlas-app-") for name in reloaded) >= 2  # at the start, and the end


def test_a_second_host_replaces_the_first(setup: Setup) -> None:
    async def main() -> None:
        browser = _app_setup(setup)
        try:
            await browser.host(FakeAppHost())
            await browser.run("c-1", "print(1)", BrowserMode.PROFILE, 30)
            await browser.host(FakeAppHost())
            await browser.run("c-1", "print(2)", BrowserMode.PROFILE, 30)
        finally:
            await browser.close()

    asyncio.run(main())
    first, second = (run["variables"]["BU_CDP_WS"] for run in _code_runs(setup))
    assert first != second
    # The daemon of the first life is stopped before the conversation's first call on the second.
    assert [run["reload"] for run in setup.runs() if "reload" in run].count(
        next(run["reload"] for run in setup.runs() if "reload" in run)
    ) >= 3


def test_an_address_is_refused_by_the_same_rules_wherever_it_is_asked(setup: Setup) -> None:
    setup.resolved["sneaky.example"] = ["169.254.169.254"]

    async def main() -> None:
        browser = _app_setup(setup)
        try:
            assert await browser.refused_url("https://example.com/") is None
            assert "cloud metadata" in str(await browser.refused_url("https://sneaky.example/"))
            assert "cloud metadata" in str(await browser.refused_url("http://169.254.1.1/"))
            assert "vault" in str(await browser.refused_url(f"https://a.example/?k={SECRET}"))
        finally:
            await browser.close()

    asyncio.run(main())
