"""A stand-in for Chrome, for the tests: opened as Atlas opens Chrome (`python tests/fake_chrome.py
<Chrome's own flags>`), it listens for WebSocket connections on a port of the system's choosing,
writes the profile's `DevToolsActivePort` as Chrome does, and closes on `Browser.close`. Its
command line is appended to `FAKE_CHROME_LOG`, one JSON line per start. With `FAKE_CHROME_HANDOFF`
set it starts as Edge's launcher does on Windows: it hands the browser to another process and ends
with code 0 before the endpoint is written. With `FAKE_CHROME_STUBBORN` set it answers
`Browser.close` and keeps running."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import websockets


async def main() -> None:
    arguments = sys.argv[1:]
    log = os.environ.get("FAKE_CHROME_LOG")
    if log:
        with Path(log).open("a", encoding="utf-8") as file:
            file.write(json.dumps({"argv": arguments, "pid": os.getpid()}) + "\n")
    profile = Path(next(a for a in arguments if a.startswith("--user-data-dir=")).split("=", 1)[1])
    closed = asyncio.Event()

    async def handle(socket: websockets.ServerConnection) -> None:
        async for message in socket:
            request = json.loads(message)
            await socket.send(json.dumps({"id": request.get("id"), "result": {}}))
            if request.get("method") == "Browser.close" and not os.environ.get(
                "FAKE_CHROME_STUBBORN"
            ):
                closed.set()

    async with websockets.serve(handle, "127.0.0.1", 0) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "DevToolsActivePort").write_text(f"{port}\n/devtools/browser/fake\n")
        await closed.wait()


if __name__ == "__main__":
    if os.environ.get("FAKE_CHROME_HANDOFF"):
        environ = {k: v for k, v in os.environ.items() if k != "FAKE_CHROME_HANDOFF"}
        # This same script, with the arguments it was given.
        subprocess.Popen([sys.executable, __file__, *sys.argv[1:]], env=environ)  # noqa: S603
    else:
        asyncio.run(main())
