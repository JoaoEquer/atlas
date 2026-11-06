"""Reaching a home through the relay (step 20): the home and the app each dial out to it, so neither
opens a port, and it joins the two ends, carrying bytes it cannot read (`tunnel`).

- **The relay's wire:** a home waits at `{relay}/{room}/home`, an app comes in at
  `{relay}/{room}/app`; the relay joins an app to a waiting home, tells the home with the text frame
  `paired`, and from then carries every binary frame of one to the other, closing both when one
  closes. Atlas Cloud's relay is a Cloudflare Worker; any relay that does this will do.
- **The home** (`RelayLink`, run by its server) keeps one connection waiting. Once an app is
  joined, it opens the next one for the next app, agrees the connection's keys, and hands the
  connection to its server as one more client of the same protocol: each frame opened as it comes,
  sealed as it goes. Holding the code's key is what lets an app in, as its token does elsewhere.
  A relay that cannot be reached is tried again, waiting longer each time, up to a minute.
- **The app's side** (`relay_client`) is the same tunnel from the other end, for the app and the
  tests.

The room is not a secret the tunnel leans on: someone who knows it may wait as the home, and the
app stops at once, since that end cannot prove it holds the key.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from atlas.instances.errors import RelayGoneError, TunnelError
from atlas.instances.tunnel import NONCE_BYTES, READY, Tunnel, hello, read_hello, read_key

#: The relay's word to a waiting home that an app was joined to it.
PAIRED = "paired"
#: Seconds the home waits before trying an unreachable relay again, at first and at most.
RETRY_SECONDS = 1.0
RETRY_MAX_SECONDS = 60.0
#: Seconds a joined connection may take to agree its keys.
HANDSHAKE_SECONDS = 15.0

_logger = logging.getLogger("atlas.instances")


@dataclass(frozen=True, slots=True)
class RelayRoom:
    """Where a home waits at a relay, and the key only the home and its apps hold."""

    url: str
    room: str
    key: str

    def side(self, which: str) -> str:
        return f"{self.url.rstrip('/')}/{self.room}/{which}"


def new_room() -> str:
    return secrets.token_urlsafe(16)


class RelaySession:
    """One app's connection through the relay, from the home's end, its keys agreed: the frames of
    the server's protocol, opened as they come and sealed as they go."""

    def __init__(self, socket: ClientConnection, tunnel: Tunnel) -> None:
        self._socket = socket
        self._tunnel = tunnel

    async def send(self, text: str) -> None:
        """Raises `RelayGoneError` once the connection closed."""
        try:
            await self._socket.send(self._tunnel.seal(text.encode("utf-8")))
        except ConnectionClosed as error:
            raise RelayGoneError(str(error)) from error

    async def receive(self) -> str | None:
        """The next frame, or `None` once the connection closed or carried a frame that does not
        open, which closes it."""
        try:
            return self._tunnel.open(_binary(await self._socket.recv())).decode("utf-8")
        except ConnectionClosed:
            return None
        except TunnelError as error:
            _logger.warning("A connection through the relay was closed: %s", error)
            await self._socket.close()
            return None

    async def close(self, code: int) -> None:
        await self._socket.close(code)


type Serve = Callable[[RelaySession], Awaitable[None]]


class RelayLink:
    """A home's end of the relay: one connection waiting, and each app joined served as one more
    client of the home's server (`serve`)."""

    def __init__(self, room: RelayRoom, serve: Serve) -> None:
        self._room = room
        self._key = read_key(room.key)
        self._serve = serve
        self._sessions: set[asyncio.Task[None]] = set()
        #: How many apps were joined, for the tests and the log.
        self.joined = 0

    async def run(self) -> None:
        """Wait at the relay until cancelled."""
        delay = RETRY_SECONDS
        try:
            while True:
                try:
                    waiting = await connect(self._room.side("home"), max_size=None)
                except (OSError, InvalidHandshake, InvalidURI, TimeoutError) as error:
                    _logger.warning("The relay cannot be reached: %s", error)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, RETRY_MAX_SECONDS)
                    continue
                delay = RETRY_SECONDS
                try:
                    said = await waiting.recv()
                except ConnectionClosed:
                    continue
                if said != PAIRED:
                    await waiting.close()
                    continue
                self.joined += 1
                session = asyncio.create_task(self._session(waiting))
                self._sessions.add(session)
                session.add_done_callback(self._sessions.discard)
        finally:
            for session in list(self._sessions):
                session.cancel()
            await asyncio.gather(*self._sessions, return_exceptions=True)

    async def _session(self, app: ClientConnection) -> None:
        try:
            async with asyncio.timeout(HANDSHAKE_SECONDS):
                app_nonce, _ = read_hello(_binary(await app.recv()))
                home_nonce = secrets.token_bytes(NONCE_BYTES)
                tunnel = Tunnel(self._key, app_nonce, home_nonce, "home")
                await app.send(hello(home_nonce) + tunnel.seal(READY))
                if tunnel.open(_binary(await app.recv())) != READY:
                    raise TunnelError("The app did not prove it holds the key.")
            await self._serve(RelaySession(app, tunnel))
        except (TunnelError, TimeoutError, ConnectionClosed, UnicodeDecodeError) as error:
            _logger.warning("A connection through the relay ended: %s", error)
        finally:
            await app.close()


@dataclass
class RelayClient:
    """The app's end of one connection through the relay: send and receive the protocol's text."""

    socket: ClientConnection
    tunnel: Tunnel

    async def send(self, text: str) -> None:
        await self.socket.send(self.tunnel.seal(text.encode("utf-8")))

    async def recv(self) -> str:
        return self.tunnel.open(_binary(await self.socket.recv())).decode("utf-8")


@contextlib.asynccontextmanager
async def relay_client(room: RelayRoom) -> AsyncGenerator[RelayClient]:
    """A connection to the home through the relay, its keys agreed, each end having proved it
    holds the key with a sealed `ready`. Raises `TunnelError` when the far end cannot."""
    key = read_key(room.key)
    async with connect(room.side("app"), max_size=None) as socket:
        app_nonce = secrets.token_bytes(NONCE_BYTES)
        await socket.send(hello(app_nonce))
        async with asyncio.timeout(HANDSHAKE_SECONDS):
            home_nonce, sealed = read_hello(_binary(await socket.recv()))
        tunnel = Tunnel(key, app_nonce, home_nonce, "app")
        if tunnel.open(sealed) != READY:
            raise TunnelError("The far end of the relay is not your home.")
        await socket.send(tunnel.seal(READY))
        yield RelayClient(socket, tunnel)


def _binary(frame: str | bytes) -> bytes:
    if isinstance(frame, str):
        raise TunnelError("The relay carried text where a sealed frame belongs.")
    return frame
