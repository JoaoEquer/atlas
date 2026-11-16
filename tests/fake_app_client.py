"""A stand-in for the app's main process, for the server tests: a client of the real gateway, on a
loop of its own, that writes the host proof as a file the way the app does, offers its browser
(`browser.host`) and answers the server's `browser.*` requests from tests/fake_app_host.py. What it
was asked is in `requests`; a method in `held` is not answered until the test says so."""

import asyncio
import json
import secrets
import threading
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import WebSocketException
from websockets.typing import Subprotocol

from atlas.browser.errors import HostError
from atlas.server import PROTOCOL, TOKEN_PREFIX
from fake_app_host import FakeAppHost
from server_support import TOKEN, AtlasServer


class FakeAppClient:
    def __init__(self, server: AtlasServer, host: FakeAppHost, folder: Path) -> None:
        self.host = host
        #: The app's folder: the proofs go in its `browser-proof/`.
        self.folder = folder
        self.requests: list[dict[str, Any]] = []
        #: Frames that are neither an answer nor a `browser.*` request.
        self.other: list[dict[str, Any]] = []
        self.held: set[str] = set()
        self._server = server
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._socket: ClientConnection | None = None
        self._reader: asyncio.Future[None] | None = None
        self._next = 0
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._arrived = threading.Condition()
        self._tasks: set[asyncio.Future[None]] = set()

    # -- the test's side ---------------------------------------------------------------------------

    def __enter__(self) -> "FakeAppClient":
        self._thread.start()
        self._run(self._connect())
        return self

    def __exit__(self, *_: object) -> None:
        self.hang_up()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """A method's whole response frame."""
        return self._run(self._call(method, params or {}))

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._run(self._send({"jsonrpc": "2.0", "method": method, "params": params}))

    def offer(
        self,
        *,
        nonce: str | None = None,
        written: str | None = None,
        name: str | None = None,
        size: int | None = None,
    ) -> dict[str, Any]:
        """`browser.host` with a proof written as the app writes it: a file with the nonce in
        `browser-proof/`, removed once the server has answered. `written` is what the file holds
        when that is not the nonce, `name` its name, `size` pads it."""
        nonce = nonce or secrets.token_hex(32)
        folder = self.folder / "browser-proof"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name or secrets.token_hex(16))
        path.write_text((written if written is not None else nonce).ljust(size or 0), "utf-8")
        try:
            return self.call("browser.host", {"proof_file": str(path), "nonce": nonce})
        finally:
            path.unlink(missing_ok=True)

    def hang_up(self) -> None:
        if self._socket is not None:
            self._run(self._socket.close())

    def wait_for(self, method: str, count: int = 1, seconds: float = 10) -> list[dict[str, Any]]:
        """The server's requests of a method, once there are `count` of them."""
        with self._arrived:
            found = self._arrived.wait_for(
                lambda: sum(r["method"] == method for r in self.requests) >= count, seconds
            )
            assert found, f"{method} never came: {self.requests}"
        return [r for r in self.requests if r["method"] == method]

    def answer(self, request_id: str, result: dict[str, Any]) -> None:
        self._run(self._send({"jsonrpc": "2.0", "id": request_id, "result": result}))

    def refuse(self, request_id: str, message: str) -> None:
        error = {"code": -32000, "message": message}
        self._run(self._send({"jsonrpc": "2.0", "id": request_id, "error": error}))

    # -- the loop's side ---------------------------------------------------------------------------

    def _run[T](self, work: Awaitable[T]) -> T:
        return asyncio.run_coroutine_threadsafe(_wrap(work), self._loop).result(30)

    async def _connect(self) -> None:
        self._socket = await connect(
            self._server.url,
            subprotocols=[Subprotocol(PROTOCOL), Subprotocol(TOKEN_PREFIX + TOKEN)],
            max_size=None,
        )
        ready = json.loads(await self._socket.recv())
        assert ready["params"]["type"] == "server.ready"
        self._reader = asyncio.ensure_future(self._read())

    async def _send(self, frame: dict[str, Any]) -> None:
        assert self._socket is not None
        await self._socket.send(json.dumps(frame))

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next += 1
        future = asyncio.get_running_loop().create_future()
        self._waiting[self._next] = future
        await self._send({"jsonrpc": "2.0", "id": self._next, "method": method, "params": params})
        return await asyncio.wait_for(future, 30)

    async def _read(self) -> None:
        assert self._socket is not None
        try:
            async for message in self._socket:
                frame = json.loads(message)
                if isinstance(frame.get("id"), int) and "method" not in frame:
                    self._waiting.pop(frame["id"]).set_result(frame)
                elif str(frame.get("method", "")).startswith("browser."):
                    with self._arrived:
                        self.requests.append(frame)
                        self._arrived.notify_all()
                    if frame["method"] not in self.held:
                        self._tasks.add(asyncio.ensure_future(self._serve(frame)))
                else:
                    self.other.append(frame)
        except (OSError, ValueError, WebSocketException):  # the socket ended
            return

    async def _serve(self, frame: dict[str, Any]) -> None:
        method, params = frame["method"], frame["params"]
        try:
            result = await self._answer(method, params)
        except HostError as error:
            reply: dict[str, Any] = {"error": {"code": -32000, "message": str(error)}}
        else:
            reply = {"result": result}
        await self._send({"jsonrpc": "2.0", "id": frame["id"], **reply})

    async def _answer(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        host = self.host
        match method:
            case "browser.tabs":
                found = await host.tabs()
                rows = [
                    {
                        "id": t.id,
                        "url": t.url,
                        "title": t.title,
                        "owner": t.owner,
                        "loading": t.loading,
                        "selected": t.selected,
                    }
                    for t in found.tabs
                ]
                return {"tabs": rows, "user_agent": found.user_agent, "product": found.product}
            case "browser.open":
                tab = await host.open(
                    params["owner"], params["url"], background=params["background"]
                )
                return {"id": tab}
            case "browser.close":
                await host.close(params["id"])
            case "browser.show":
                await host.show(params["id"])
            case "browser.attach":
                await host.attach(params["id"])
            case "browser.detach":
                await host.detach(params["id"])
            case "browser.cdp":
                return {"result": await host.cdp(params["id"], params["method"], params["params"])}
        return {}


async def _wrap[T](work: Awaitable[T]) -> T:
    return await work
