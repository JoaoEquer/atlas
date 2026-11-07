"""A relay over real WebSockets, for the tests of reaching a home through one (step 20).

It does what Atlas Cloud's relay does and no more: a home waits at `/{room}/home`, an app comes in
at `/{room}/app`, the relay joins the app to a waiting home, tells the home `paired`, and carries
every frame of one to the other, closing both when one closes. It keeps every binary frame it
carried (`carried`), so a test can show it never saw what the app and the home said.
"""

import asyncio
import contextlib
import threading
from collections import defaultdict, deque

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed


class FakeRelay:
    def __init__(self) -> None:
        self.carried: list[bytes] = []
        self._waiting: dict[str, deque[tuple[ServerConnection, asyncio.Future[ServerConnection]]]]
        self._waiting = defaultdict(deque)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: Server | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def waiting(self, room: str) -> int:
        return len(self._waiting[room])

    async def _handle(self, socket: ServerConnection) -> None:
        parts = (socket.request.path if socket.request else "").strip("/").split("/")
        if len(parts) != 2 or parts[1] not in ("home", "app"):
            await socket.close(1008)
            return
        room, side = parts
        if side == "home":
            joined: asyncio.Future[ServerConnection] = asyncio.get_running_loop().create_future()
            self._waiting[room].append((socket, joined))
            try:
                app = await joined
            except asyncio.CancelledError:
                return
            await socket.send("paired")
            await self._carry(socket, app)
            return
        if not self._waiting[room]:
            await socket.close(1013, "no home is waiting")
            return
        home, joined = self._waiting[room].popleft()
        joined.set_result(socket)
        await self._carry(socket, home)

    async def _carry(self, source: ServerConnection, target: ServerConnection) -> None:
        try:
            async for frame in source:
                if isinstance(frame, bytes):
                    self.carried.append(frame)
                await target.send(frame)
        except ConnectionClosed:
            pass
        finally:
            await target.close()

    def __enter__(self) -> "FakeRelay":
        started = threading.Event()

        async def main() -> None:
            self._loop = asyncio.get_running_loop()
            self._server = await serve(self._handle, "127.0.0.1", 0, max_size=None)
            self.port = next(iter(self._server.sockets)).getsockname()[1]
            started.set()
            with contextlib.suppress(asyncio.CancelledError):
                await self._server.serve_forever()

        self._thread = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
        self._thread.start()
        if not started.wait(10):
            raise RuntimeError("The fake relay did not start.")
        return self

    def __exit__(self, *_: object) -> None:
        if self._loop is not None and self._server is not None:
            self._loop.call_soon_threadsafe(self._server.close)
        if self._thread is not None:
            self._thread.join(timeout=10)
