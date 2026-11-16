"""The real engine (browser-harness) through the browser endpoint, with the app browser as a real
headless Chrome's tabs (tests/chrome_app_host.py): the commands the engine sends, the tabs it sees,
and what it must not touch. Skipped where no Chrome, Edge or Chromium is installed. This is the
test that catches a new engine using a `Target.*` command the endpoint does not answer."""

import asyncio
import json
import struct
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import HTMLResponse

from atlas.browser import Browser, BrowserMode, find_chrome
from chrome_app_host import ChromeAppHost
from web_server import LocalServer

CHROME = find_chrome()

pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome, Edge or Chromium installed")

PAGE = """<!doctype html><html><head><title>Atlas test page</title></head>
<body><h1>Olá, navegador</h1><input id="name"><p id="echo"></p>
<script>document.getElementById('name').addEventListener('input', e =>
  document.getElementById('echo').textContent = 'typed: ' + e.target.value)</script>
</body></html>"""
USER_PAGE = "<!doctype html><title>The user's page</title><h1>Signed in</h1>"
OTHER_PAGE = "<!doctype html><title>Another conversation</title><h1>Not for you</h1>"


async def _page(_request: Request) -> HTMLResponse:
    return HTMLResponse(PAGE)


async def _user(_request: Request) -> HTMLResponse:
    return HTMLResponse(USER_PAGE)


async def _other(_request: Request) -> HTMLResponse:
    return HTMLResponse(OTHER_PAGE)


type Scenario = Callable[[Browser, ChromeAppHost, LocalServer], Awaitable[None]]


def _run(tmp_path: Path, scenario: Scenario) -> None:
    assert CHROME is not None

    async def main(server: LocalServer) -> None:
        host = ChromeAppHost(str(CHROME), tmp_path / "chrome-profile")
        browser = Browser(tmp_path / "browser", None, redact=str)
        await host.start()
        host_up = False
        try:
            await browser.host(host)
            host_up = True
            host.sink = browser.host_event
            await scenario(browser, host, server)
        finally:
            if host_up:
                await browser.close()
            await host.stop()

    routes = {"/page": _page, "/user": _user, "/other": _other}
    with LocalServer(routes) as server:
        asyncio.run(main(server))


def _result(text: str) -> dict[str, Any]:
    inner = text.split("\n\n", 2)[-1].rsplit("\n</untrusted_tool_result>", 1)[0]
    return json.loads(inner)


async def _call(browser: Browser, code: str, conversation: str = "c-1") -> dict[str, Any]:
    result = await browser.run(conversation, code, BrowserMode.PROFILE, 90)
    found = _result(result.text)
    assert found["success"], found
    return found


def test_the_engine_reads_types_and_sees_through_the_endpoint(tmp_path: Path) -> None:
    async def scenario(browser: Browser, host: ChromeAppHost, server: LocalServer) -> None:
        url = server.url("/page")
        opened = await browser.run(
            "c-1",
            f"# Opening the test page\nnew_tab({url!r})\nwait_for_load()\n"
            "print(json.dumps(page_info()))\n"
            "print(js('document.querySelector(\"h1\").textContent'))\n"
            "print(capture_screenshot())\n",
            BrowserMode.PROFILE,
            90,
        )
        found = _result(opened.text)
        assert found["success"], found
        info = json.loads(found["output"].splitlines()[0])
        assert info["title"].endswith("Atlas test page")
        assert "Olá, navegador" in found["output"]
        assert opened.images[0].data.startswith(b"\x89PNG")
        # The page is the conversation's tab, in the app, made on the engine's behalf.
        listed = (await host.tabs()).tabs
        assert [tab.owner for tab in listed] == ["c-1"]
        assert listed[0].url == url
        # The session lasts across calls: the next one types into the same page.
        typed = await _call(
            browser,
            "fill_input('#name', 'Ol\\u00e1')\n"
            "print(js(\"document.getElementById('echo').textContent\"))\n"
            "wait_for_network_idle()\n",
        )
        assert "typed: Olá" in typed["output"]
        assert "note" not in typed
        assert len((await host.tabs()).tabs) == 1

    _run(tmp_path, scenario)


def test_the_users_tab_is_listed_first_and_never_taken_over(tmp_path: Path) -> None:
    async def scenario(browser: Browser, host: ChromeAppHost, server: LocalServer) -> None:
        mine = await host.add("c-1", server.url("/user"), selected=True)
        await host.add("c-2", server.url("/other"))
        await asyncio.sleep(0.5)
        done = await _call(
            browser,
            f"new_tab({server.url('/page')!r})\nwait_for_load()\n"
            "print(json.dumps([t['url'] for t in list_tabs()]))\n",
        )
        urls = json.loads(done["output"].splitlines()[-1])
        # The user's page first, then the engine's own; the other conversation's never.
        assert urls == [server.url("/user"), server.url("/page")]
        by_id = {tab.id: tab for tab in (await host.tabs()).tabs}
        assert by_id[mine].url == server.url("/user")
        # To go on where the user is, the engine switches to their tab.
        read = await _call(
            browser,
            "switch_tab(list_tabs()[0])\nprint(js('document.querySelector(\"h1\").textContent'))\n"
            "print(page_info()['url'])\n",
        )
        assert read["output"].split() == ["Signed", "in", server.url("/user")]
        # A conversation that has not opened a page yet lands on the user's by ensure_real_tab().
        await host.add("c-3", server.url("/user"), selected=True)
        await asyncio.sleep(0.5)
        landed = await _call(
            browser,
            "ensure_real_tab()\nprint(js('document.querySelector(\"h1\").textContent'))",
            conversation="c-3",
        )
        assert landed["output"].strip() == "Signed in"
        # The other conversation drives its own tab and sees only that one.
        other = await _call(
            browser, "print(json.dumps([t['url'] for t in list_tabs()]))", conversation="c-2"
        )
        # Its own page, and the blank one the engine made for it; none of c-1's.
        assert sorted(json.loads(other["output"].splitlines()[-1])) == sorted(
            ["about:blank", server.url("/other")]
        )

    _run(tmp_path, scenario)


def test_the_engine_may_ask_for_a_desktop_layout_and_give_it_back(tmp_path: Path) -> None:
    async def scenario(browser: Browser, host: ChromeAppHost, server: LocalServer) -> None:
        await _call(browser, f"new_tab({server.url('/page')!r})\nwait_for_load()")
        before = await _call(browser, "print(js('innerWidth'))")
        widened = await _call(
            browser,
            "cdp('Emulation.setDeviceMetricsOverride', width=1280, height=800, "
            "deviceScaleFactor=1, mobile=False)\nprint(js('innerWidth'))\n"
            "print(capture_screenshot())\n",
        )
        lines = widened["output"].split("\n")
        assert lines[0].strip() == "1280"
        shot = Path(lines[1].strip())
        width = struct.unpack(">I", shot.read_bytes()[16:20])[0]
        assert width == 1280
        cleared = await _call(
            browser, "cdp('Emulation.clearDeviceMetricsOverride')\nprint(js('innerWidth'))"
        )
        assert cleared["output"].strip() == before["output"].strip()

    _run(tmp_path, scenario)


def test_a_tab_closed_from_outside_is_replaced_on_the_next_call(tmp_path: Path) -> None:
    async def scenario(browser: Browser, host: ChromeAppHost, server: LocalServer) -> None:
        await _call(browser, f"new_tab({server.url('/page')!r})\nwait_for_load()")
        (tab,) = (await host.tabs()).tabs
        await host.destroy_outside(tab.id)
        await asyncio.sleep(0.5)
        after = await _call(
            browser, f"new_tab({server.url('/user')!r})\nwait_for_load()\nprint(page_info()['url'])"
        )
        assert after["output"].strip().endswith("/user")
        urls = [t.url for t in (await host.tabs()).tabs]
        assert server.url("/user") in urls
        assert server.url("/page") not in urls

    _run(tmp_path, scenario)


def test_what_a_page_session_may_not_reach_is_refused_to_the_engine(tmp_path: Path) -> None:
    async def scenario(browser: Browser, host: ChromeAppHost, server: LocalServer) -> None:
        await _call(browser, "print(page_info())")
        refused = await browser.run(
            "c-1",
            "try:\n    cdp('Browser.close')\n"
            "except Exception as error:\n    print('refused', error)\n"
            "try:\n    cdp('Page.navigate', url='http://169.254.' + '169.254/latest')\n"
            "except Exception as error:\n    print('refused', error)\n",
            BrowserMode.PROFILE,
            90,
        )
        found = _result(refused.text)
        assert found["success"], found
        assert found["output"].count("refused") == 2
        assert "not available in the app's browser" in found["output"]
        assert "cloud metadata" in found["output"]
        # The window is still there: the page's tab is, and so is the Chrome behind the host.
        assert len((await host.tabs()).tabs) == 1

    _run(tmp_path, scenario)
