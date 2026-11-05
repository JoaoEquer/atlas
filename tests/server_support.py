"""A real Atlas server for the server tests, and a client that talks to it over a real WebSocket.

The server is `create_app` with a `Gateway` on uvicorn, in a thread of its own, as `atlas serve`
runs it; the model behind it is the fake model server. The client is the `websockets` package's,
the one scripts use, so frames cross a real socket both ways.
"""

import json
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import uvicorn
from websockets.sync.client import ClientConnection, connect

from atlas.__main__ import SCHEMAS
from atlas.home import Home, Settings
from atlas.instances import RelayRoom
from atlas.models import Models
from atlas.secrets import Vault
from atlas.server import PROTOCOL, TOKEN_PREFIX, Gateway, create_app

TOKEN = "test-token-0123456789"
# How long a test waits for a frame, the server or a client. Only a failing test waits it out, and
# the tests share the machine's cores with each other.
WAIT = 30.0


class AtlasServer:
    def __init__(
        self,
        home: Home,
        settings: Settings,
        models: Models,
        vault: Vault,
        relay: RelayRoom | None = None,
        **gateway: Any,
    ) -> None:
        self.gateway = Gateway(home, settings, models, vault, schemas=SCHEMAS, **gateway)
        app = create_app(self.gateway, token=TOKEN, bound_host="127.0.0.1", relay=relay)
        config = uvicorn.Config(
            app, host="127.0.0.1", port=0, log_config=None, log_level=None, access_log=False
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self.port = 0

    def __enter__(self) -> "AtlasServer":
        self._thread.start()
        deadline = time.monotonic() + WAIT
        while not self._server.started:
            if time.monotonic() > deadline or not self._thread.is_alive():
                raise RuntimeError("The Atlas server did not start.")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_: object) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=20)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    @contextmanager
    def client(self, token: str = TOKEN, **options: Any) -> Iterator["Client"]:
        socket = connect(self.url, subprotocols=[PROTOCOL, TOKEN_PREFIX + token], **options)  # pyright: ignore[reportArgumentType]
        try:
            yield Client(socket)
        finally:
            socket.close()


class Client:
    """Calls methods and reads events and server requests, keeping what it read past."""

    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket
        self._next_id = 0
        self._kept: deque[dict[str, Any]] = deque()
        self.ready = self._read(WAIT)
        assert self.ready["params"]["type"] == "server.ready"

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """The method's whole response frame: `result` or `error`."""
        self._next_id += 1
        request_id = self._next_id
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        return self._take(lambda f: f.get("id") == request_id and "method" not in f)

    def result(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.call(method, params)
        assert "result" in response, response
        return response["result"]

    def send(self, frame: dict[str, Any]) -> None:
        self.socket.send(json.dumps(frame))

    def event(self, kind: str, timeout: float = WAIT) -> dict[str, Any]:
        """The next event of a kind, its `params`; earlier frames of other kinds stay kept."""
        frame = self._take(
            lambda f: f.get("method") == "event" and f["params"]["type"] == kind, timeout
        )
        return frame["params"]

    def request(self, method: str, timeout: float = WAIT) -> dict[str, Any]:
        """The next server request of a method: its whole frame."""
        return self._take(lambda f: f.get("method") == method and "id" in f, timeout)

    def next_frame(
        self, wanted: Callable[[dict[str, Any]], bool], timeout: float = WAIT
    ) -> dict[str, Any]:
        """The next frame `wanted` accepts, whatever it is; the others stay kept."""
        return self._take(wanted, timeout)

    def answer(self, request_id: str, result: dict[str, Any]) -> None:
        self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def events_until(self, kind: str, timeout: float = WAIT) -> list[dict[str, Any]]:
        """Every event of a conversation up to and including the next of a kind, in order; the
        lists' `conversations.changed`, which every client hears, stays kept for `event`."""
        seen: list[dict[str, Any]] = []
        deadline = time.monotonic() + timeout
        while True:
            frame = self._pop(kind) or self._read(deadline - time.monotonic())
            if frame.get("method") == "event" and _ours(frame, kind):
                seen.append(frame["params"])
                if frame["params"]["type"] == kind:
                    return seen
            else:
                self._kept.append(frame)
                if len(self._kept) > 1000:  # pragma: no cover
                    raise AssertionError("too many frames kept")

    def _pop(self, kind: str) -> dict[str, Any] | None:
        for frame in list(self._kept):
            if frame.get("method") == "event" and _ours(frame, kind):
                self._kept.remove(frame)
                return frame
        return None

    def _take(self, wanted: Any, timeout: float = WAIT) -> dict[str, Any]:
        for frame in list(self._kept):
            if wanted(frame):
                self._kept.remove(frame)
                return frame
        deadline = time.monotonic() + timeout
        while True:
            frame = self._read(deadline - time.monotonic())
            if wanted(frame):
                return frame
            self._kept.append(frame)

    def _read(self, timeout: float) -> dict[str, Any]:
        if timeout <= 0:
            raise TimeoutError("No frame came in time.")
        return json.loads(self.socket.recv(timeout=timeout))


#: The `serve` fixture: start a server with these gateway options.
type Serve = Callable[..., AtlasServer]


def _ours(frame: dict[str, Any], kind: str) -> bool:
    """Whether `events_until` takes an event: all but the lists' notice, unless it is the one
    waited for."""
    told = frame["params"]["type"] == "conversations.changed"
    return not told or kind == "conversations.changed"
