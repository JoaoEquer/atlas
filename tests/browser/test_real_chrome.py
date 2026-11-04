"""The real pieces: the Chrome installed on this computer, headless, driven by the real
browser-harness through Atlas, on a page served here (nothing leaves the machine). Skipped where no
Chrome, Edge or Chromium is installed."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request
from starlette.responses import HTMLResponse

from atlas.agent import ToolOutput
from atlas.browser import Browser, BrowserMode, find_chrome
from web_server import LocalServer

CHROME = find_chrome()

pytestmark = pytest.mark.skipif(CHROME is None, reason="no Chrome, Edge or Chromium installed")

PAGE = """<!doctype html><html><head><title>Atlas test page</title></head>
<body><h1>Olá, navegador</h1><input id="name"><p id="echo"></p>
<script>document.getElementById('name').addEventListener('input', e =>
  document.getElementById('echo').textContent = 'typed: ' + e.target.value)</script>
</body></html>"""


async def _page(_request: Request) -> HTMLResponse:
    return HTMLResponse(PAGE)


def test_the_real_chrome_and_engine_read_type_and_see(tmp_path: Path) -> None:
    assert CHROME is not None
    folder = tmp_path / "browser"

    async def main(url: str) -> None:
        browser = Browser(folder, [str(CHROME)], redact=str)
        try:
            tool = browser.tool("c-1", sees_images=True)
            opening = {
                "code": f"# Opening the test page\nnew_tab({url!r})\nwait_for_load()\n"
                'print(json.dumps(page_info()))\nprint(js(\'document.querySelector("h1")'
                ".textContent'))",
                "mode": "headless",
                "timeout_s": 60,
            }
            opened = await tool.run(opening)
            assert isinstance(opened, str)
            found = _result(opened)
            if "waiting for the daemon" in str(found.get("stderr", "")):
                # A Chrome still starting on a slow machine misses the engine's 5 seconds for a
                # tab; the model reads the error and calls again, as this does.
                opened = await tool.run(opening)
                assert isinstance(opened, str)
                found = _result(opened)
            assert found["success"], found
            info = json.loads(found["output"].splitlines()[0])
            assert info["title"].endswith("Atlas test page")  # the tab marked as the agent's
            assert "Olá, navegador" in found["output"]
            # The browser and the tab last: the next call types into the same page.
            typed = await tool.run(
                {
                    "code": "fill_input('#name', 'Ana')\nprint(js(\"document.getElementById("
                    "'echo').textContent\"))\nprint(capture_screenshot())",
                    "mode": "headless",
                    "timeout_s": 60,
                }
            )
            assert isinstance(typed, ToolOutput)
            assert "typed: Ana" in typed.text
            assert typed.images[0].data.startswith(b"\x89PNG")
        finally:
            await browser.close()

    with LocalServer({"/page": _page}) as server:
        asyncio.run(main(server.url("/page")))
    # The temporary profile went with its browser, and the engine's telemetry stayed off.
    assert not (folder / "temporary" / "headless").exists()
    assert not (folder / "engine" / "telemetry.json").exists()


def test_a_real_chrome_refused_at_cloud_metadata_never_starts(tmp_path: Path) -> None:
    assert CHROME is not None

    async def main() -> None:
        browser = Browser(tmp_path / "browser", [str(CHROME)], redact=str)
        result = await browser.run(
            "c-1", "new_tab('http://169.254.169.254/latest/meta-data/')", BrowserMode.HEADLESS, 30
        )
        assert "cloud metadata" in json.loads(result.text)["error"]
        await browser.close()

    asyncio.run(main())
    assert not (tmp_path / "browser" / "temporary").exists()


def _result(text: str) -> dict[str, Any]:
    inner = text.split("\n\n", 2)[-1].rsplit("\n</untrusted_tool_result>", 1)[0]
    return json.loads(inner)
