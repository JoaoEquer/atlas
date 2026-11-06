"""The ASGI application and its routes: `/health`, and the clients' WebSocket at `/ws`."""

import asyncio
import hmac
import logging
from collections.abc import AsyncGenerator, Set
from contextlib import asynccontextmanager, suppress

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from atlas import __version__
from atlas.home import DEFAULT_HOST
from atlas.instances import RelayGoneError, RelayLink, RelayRoom, RelaySession
from atlas.server.access import refusal
from atlas.server.connection import PeerGoneError
from atlas.server.protocol import PROTOCOL, dump
from atlas.server.service import Gateway

_logger = logging.getLogger("atlas.server")

#: What `atlas serve` writes on standard output, then its port, once it listens: whoever started
#: it with port 0 learns the port from it.
READY_LINE = "ATLAS_SERVER_READY port="


async def health(_request: Request) -> JSONResponse:
    """Answer whether the server is up, which version is running, and which protocol it speaks (a
    client of another one says which side to update, step 15)."""
    return JSONResponse({"status": "ok", "version": __version__, "protocol": PROTOCOL})


def _local_address(websocket: WebSocket) -> str | None:
    """The address the connection arrived on (ASGI's `server`)."""
    server: object = websocket.scope.get("server")
    if isinstance(server, (tuple, list)) and server:
        first: object = server[0]  # pyright: ignore[reportUnknownVariableType]
        return first if isinstance(first, str) else None
    return None


class _Socket:
    """A Starlette WebSocket as the gateway's `Peer`."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket

    async def send(self, text: str) -> None:
        try:
            await self._websocket.send_text(text)
        except (WebSocketDisconnect, RuntimeError, OSError) as error:
            raise PeerGoneError(str(error)) from error

    async def receive(self) -> str | None:
        try:
            message = await self._websocket.receive()
        except (WebSocketDisconnect, RuntimeError, OSError):
            return None
        if message["type"] == "websocket.disconnect":
            return None
        text = message.get("text")
        if text is None:
            data: bytes = message.get("bytes") or b""
            text = data.decode("utf-8", "replace")
        return text

    async def close(self, code: int) -> None:
        with suppress(RuntimeError, OSError):
            await self._websocket.close(code)


class _RelayPeer:
    """A connection through the relay (step 20) as the gateway's `Peer`: one more client."""

    def __init__(self, session: RelaySession) -> None:
        self._session = session

    async def send(self, text: str) -> None:
        try:
            await self._session.send(text)
        except RelayGoneError as error:
            raise PeerGoneError(str(error)) from error

    async def receive(self) -> str | None:
        return await self._session.receive()

    async def close(self, code: int) -> None:
        with suppress(RuntimeError, OSError):
            await self._session.close(code)


def create_app(
    gateway: Gateway | None = None,
    *,
    token: str = "",
    bound_host: str = DEFAULT_HOST,
    tailnet_names: Set[str] = frozenset(),
    relay: RelayRoom | None = None,
) -> Starlette:
    """The application. Without a gateway it only answers `/health`. `token` is what clients
    present; `bound_host` is where the server listens, which decides who may come in;
    `tailnet_names`, the machine's tailnet address and name while it also listens there, read at
    each connection (the server fills it once the tailnet is up)."""
    routes: list[BaseRoute] = [Route("/health", health, methods=["GET"])]
    if gateway is None:
        return Starlette(routes=routes)
    served = gateway

    async def clients(websocket: WebSocket) -> None:
        offered = websocket.headers.get("sec-websocket-protocol", "")
        refused = refusal(
            token=token,
            bound_host=bound_host,
            protocols=[p.strip() for p in offered.split(",") if p.strip()],
            host=websocket.headers.get("host", ""),
            origin=websocket.headers.get("origin"),
            peer=websocket.client.host if websocket.client else None,
            local=_local_address(websocket),
            tailnet_names=tailnet_names,
        )
        if refused is not None:
            code, why = refused
            _logger.warning("A client was refused: %s.", why, extra={"code": code})
            await websocket.close(code)
            return
        await websocket.accept(subprotocol=PROTOCOL)
        await served.serve(_Socket(websocket))

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncGenerator[None]:
        await served.start()
        # The relay's apps come in as clients too, having proved they hold its key (step 20).
        waiting = (
            None
            if relay is None
            else asyncio.create_task(
                RelayLink(relay, lambda session: served.serve(_RelayPeer(session))).run()
            )
        )
        try:
            yield
        finally:
            if waiting is not None:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            await served.close()

    async def activity(request: Request) -> JSONResponse:
        """The home's activity (step 20), for the host that sleeps it: with the server token as a
        bearer, since it says when the user works."""
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return JSONResponse(dump(await served.activity()))

    routes.append(Route("/activity", activity, methods=["GET"]))
    routes.append(WebSocketRoute("/ws", clients))
    return Starlette(routes=routes, lifespan=lifespan)
