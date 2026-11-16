"""A stand-in for browser-harness, for the tests: run as Atlas runs the engine (`python
tests/fake_engine.py`, the code on its standard input), it checks that the browser at `BU_CDP_WS`
accepts a WebSocket handshake on its path, then runs the code with helpers that pretend: each
session's tab is a JSON file in `BH_HOME`, so what one call opened the next one finds. `--reload`
stops nothing and is logged.

Every run is appended to `FAKE_ENGINE_LOG`: its arguments, the engine's variables Atlas set, and
the code."""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

_HOME = Path(os.environ.get("BH_HOME", "."))
_NAME = os.environ.get("BU_NAME", "default")
_TAB = _HOME / f"tab-{_NAME}.json"
#: A 1x1 PNG.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def _log(entry: dict[str, Any]) -> None:
    path = os.environ.get("FAKE_ENGINE_LOG")
    if path:
        with Path(path).open("a", encoding="utf-8") as file:
            file.write(json.dumps(entry) + "\n")


def _tab() -> dict[str, Any]:
    return json.loads(_TAB.read_text(encoding="utf-8")) if _TAB.exists() else {"url": "about:blank"}


def new_tab(url: str = "about:blank") -> str:
    _HOME.mkdir(parents=True, exist_ok=True)
    _TAB.write_text(json.dumps({"url": url, "title": f"Page at {url}"}), encoding="utf-8")
    return "tab-1"


goto_url = new_tab


def wait_for_load(timeout: float = 15.0) -> bool:
    return True


def page_info() -> dict[str, Any]:
    return _tab()


def js(expression: str) -> object:
    return _tab().get("title") if expression == "document.title" else None


def capture_screenshot(path: str | None = None) -> str:
    target = Path(path) if path else _HOME / "shot.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_PNG)
    return str(target)


def cdp(method: str, **params: object) -> dict[str, Any]:
    return {"targetId": "tab-1"} if method == "Target.createTarget" else {}


def switch_tab(target: object) -> str:
    return "session-1"


def current_tab() -> dict[str, Any]:
    return {"targetId": "tab-1", **_tab()}


def activate_tab(target: object) -> str:
    _log({"activated": _NAME})
    return "tab-1"


def main() -> None:
    arguments = sys.argv[1:]
    variables = {
        name: value
        for name, value in os.environ.items()
        if name.startswith(("BU_", "BH_", "BROWSER_", "ANONYMIZED_"))
    }
    if arguments == ["--reload"]:
        _log({"reload": _NAME, "variables": variables})
        return
    code = sys.stdin.read()
    _log({"argv": arguments, "variables": variables, "code": code})
    endpoint = os.environ.get("BU_CDP_WS", "")
    try:
        # A real handshake on the very path: the browser endpoint refuses any other.
        connect(endpoint, open_timeout=5).close()
    except (OSError, ValueError, WebSocketException):
        sys.stderr.write(f"browser-harness: fatal: CDP WS handshake failed: {endpoint}\n")
        sys.exit(1)
    helpers = {name: value for name, value in globals().items() if not name.startswith("_")}
    helpers.update({"time": time, "__name__": "__main__"})
    exec(compile(code, "<code>", "exec"), helpers)  # noqa: S102 - it is the engine's job


if __name__ == "__main__":
    main()
