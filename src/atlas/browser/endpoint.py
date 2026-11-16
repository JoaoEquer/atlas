"""The browser endpoint: the DevTools WebSocket Atlas opens on the loopback for the engine when the
app lends its browser (docs/ARCHITECTURE.md, Browser).

The engine believes it talks to a Chrome. This is Atlas speaking for one: `Target.*` is answered
here, from the tabs the app lists, and a command of a page goes to the app's browser host, which
runs it in the tab's own debugger; the page's events come back the same way. Every path holds one
conversation's token, and what it shows and lets the engine touch is that conversation's tabs and
no one else's. The app's own window is not a target here, because the app never lists it.
"""

import asyncio
import contextlib
import json
import logging
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Protocol, cast

import websockets
from websockets.asyncio.server import Server, ServerConnection
from websockets.http11 import Request, Response

from atlas.browser.errors import HostError
from atlas.browser.model import (
    CDP_NO_SESSION,
    CDP_NOT_FOUND,
    CDP_SERVER_ERROR,
    NO_SUCH_SESSION,
    SESSION_NOT_FOUND,
    HostTab,
    HostTabs,
    browser_version,
    cdp_refused,
    conversation_tabs,
    navigation_url,
    target_info,
    url_refused,
    wrong_method,
)

#: The largest frame the engine may send: a script or a pasted value, never a screenshot.
MAX_FRAME = 64 * 1024 * 1024
#: The error code for a request whose parameters name nothing that exists.
INVALID_PARAMS = -32602

_logger = logging.getLogger("atlas.browser")

#: Checks an address the engine would load beyond what the rules know (the vault, the names that
#: resolve to a cloud metadata address): the reason, or `None`.
type RefuseUrl = Callable[[str], Awaitable[str | None]]


class AppHost(Protocol):
    """The app browser, as the endpoint needs it. Each method raises `HostError` when the browser
    says no or is gone."""

    async def tabs(self) -> HostTabs: ...

    async def open(self, owner: str, url: str, *, background: bool) -> str: ...

    async def close(self, tab: str) -> None: ...

    async def show(self, tab: str) -> None: ...

    async def attach(self, tab: str) -> None: ...

    async def detach(self, tab: str) -> None: ...

    async def cdp(self, tab: str, method: str, params: Mapping[str, Any]) -> object: ...


@dataclass
class _Session:
    connection: ServerConnection
    tab: str


@dataclass
class _Link:
    """One engine's connection: its conversation, and the commands it has running."""

    conversation_id: str
    tasks: set[asyncio.Task[None]] = field(default_factory=set[asyncio.Task[None]])


class _RefusedError(Exception):
    """A command answered with an error."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BrowserEndpoint:
    """A server on `127.0.0.1` for the engines of the conversations that use the app browser."""

    def __init__(self, host: AppHost, refuse: RefuseUrl) -> None:
        self._host = host
        self._refuse = refuse
        self._server: Server | None = None
        self._port = 0
        self._tokens: dict[str, str] = {}
        self._by_conversation: dict[str, str] = {}
        self._links: dict[ServerConnection, _Link] = {}
        self._sessions: dict[str, _Session] = {}

    async def start(self) -> None:
        self._server = await websockets.serve(
            self._handle,
            "127.0.0.1",
            0,
            process_request=self._admit,
            max_size=MAX_FRAME,
            ping_interval=None,
        )
        self._port = next(iter(self._server.sockets)).getsockname()[1]

    def url_for(self, conversation_id: str) -> str:
        """The endpoint a conversation's engine connects to: a path only it knows."""
        token = self._by_conversation.get(conversation_id)
        if token is None:
            token = secrets.token_urlsafe(32)
            self._by_conversation[conversation_id] = token
            self._tokens[token] = conversation_id
        return f"ws://127.0.0.1:{self._port}/devtools/browser/{token}"

    async def close(self) -> None:
        """Stop listening and let go of every engine."""
        if self._server is not None:
            self._server.close()
        await self.lost()
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None

    async def lost(self) -> None:
        """The app browser is gone: every engine is told so by its connection ending."""
        self._sessions.clear()
        for connection in list(self._links):
            with contextlib.suppress(websockets.WebSocketException):
                await connection.close(1001, "The app browser closed.")

    # -- what the app tells -----------------------------------------------------------------------

    async def host_event(self, tab: str, method: str, params: object) -> None:
        """A page's event, handed to each session attached to it; a debugger that let go ends
        them, as a closed tab does."""
        for session_id, session in list(self._sessions.items()):
            if session.tab == tab:
                await self._send(
                    session.connection,
                    {"method": method, "params": params, "sessionId": session_id},
                )
        if method == "Inspector.detached":
            await self._gone(tab)

    async def _gone(self, tab: str) -> None:
        """The tab is no more: its sessions end, and the engines that drove it hear it."""
        engines: dict[ServerConnection, None] = {}
        for session_id, session in list(self._sessions.items()):
            if session.tab != tab:
                continue
            del self._sessions[session_id]
            engines[session.connection] = None
            await self._send(
                session.connection,
                {
                    "method": "Target.detachedFromTarget",
                    "params": {"sessionId": session_id, "targetId": tab},
                },
            )
        for connection in engines:
            await self._send(
                connection, {"method": "Target.targetDestroyed", "params": {"targetId": tab}}
            )

    # -- the connection ---------------------------------------------------------------------------

    def _admit(self, connection: ServerConnection, request: Request) -> Response | None:
        """Only an engine that knows a conversation's path comes in: a page of the user's own
        browser may open a WebSocket to the loopback, but it sends an `Origin`, and a name that
        resolves to this address (rebinding) sends another `Host`."""
        headers = request.headers
        if "Origin" in headers:
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden.\n")
        if headers.get_all("Host") not in (
            [f"127.0.0.1:{self._port}"],
            [f"localhost:{self._port}"],
        ):
            return connection.respond(HTTPStatus.FORBIDDEN, "Forbidden.\n")
        if self._conversation(request.path) is None:
            return connection.respond(HTTPStatus.NOT_FOUND, "Not found.\n")
        return None

    def _conversation(self, path: str) -> str | None:
        prefix = "/devtools/browser/"
        if not path.startswith(prefix):
            return None
        return self._tokens.get(path[len(prefix) :])

    async def _handle(self, connection: ServerConnection) -> None:
        request = connection.request
        conversation_id = None if request is None else self._conversation(request.path)
        if conversation_id is None:  # pragma: no cover - refused before the handshake
            return
        link = self._links[connection] = _Link(conversation_id)
        try:
            async for message in connection:
                task = asyncio.create_task(self._command(connection, link, message))
                link.tasks.add(task)
                task.add_done_callback(link.tasks.discard)
        except websockets.ConnectionClosed:  # pragma: no cover - `async for` ends quietly
            pass
        finally:
            del self._links[connection]
            for task in list(link.tasks):
                task.cancel()
            await self._release(connection)

    async def _release(self, connection: ServerConnection) -> None:
        """The engine went away: its sessions end, and a page nobody drives stops sending."""
        mine = {id_: s for id_, s in self._sessions.items() if s.connection is connection}
        for session_id in mine:
            del self._sessions[session_id]
        for tab in {session.tab for session in mine.values()}:
            if not any(session.tab == tab for session in self._sessions.values()):
                with contextlib.suppress(HostError):
                    await self._host.detach(tab)

    # -- a command --------------------------------------------------------------------------------

    async def _command(
        self, connection: ServerConnection, link: _Link, message: str | bytes
    ) -> None:
        try:
            request: object = json.loads(message)
        except ValueError:
            return
        if not isinstance(request, dict):
            return
        fields: dict[str, Any] = request  # pyright: ignore[reportUnknownVariableType]
        reply_id = fields.get("id")
        method = fields.get("method")
        params: object = fields.get("params") or {}
        session_id = fields.get("sessionId")
        if not isinstance(method, str) or not isinstance(params, dict):
            await self._send(connection, _error(reply_id, CDP_NOT_FOUND, "Invalid request."))
            return
        arguments = cast("dict[str, Any]", params)
        try:
            if isinstance(session_id, str):
                result = await self._in_session(connection, session_id, method, arguments)
            else:
                result = await self._browser(connection, link, method, arguments)
        except _RefusedError as refused:
            reply = _error(reply_id, refused.code, refused.message)
        except HostError as error:
            reply = _error(reply_id, CDP_SERVER_ERROR, str(error))
        except Exception as error:  # the engine's command must not end the endpoint
            _logger.exception("A browser endpoint command failed.", extra={"method": method})
            reply = _error(reply_id, CDP_SERVER_ERROR, f"{type(error).__name__}: {error}")
        else:
            reply = {"id": reply_id, "result": result}
        if isinstance(session_id, str):
            reply["sessionId"] = session_id
        await self._send(connection, reply)
        if method == "Target.closeTarget" and "result" in reply:
            await self._gone(str(arguments["targetId"]))

    async def _in_session(
        self, connection: ServerConnection, session_id: str, method: str, params: dict[str, Any]
    ) -> object:
        session = self._sessions.get(session_id)
        if session is None or session.connection is not connection:
            raise _RefusedError(CDP_NO_SESSION, SESSION_NOT_FOUND)
        if method == "Browser.getVersion":
            # The engine asks it on a page's session too (to pick Ctrl or Cmd to select all).
            return browser_version(await self._host.tabs())
        refused = cdp_refused(method)
        if refused is not None:
            raise _RefusedError(CDP_SERVER_ERROR, refused)
        await self._check_url(method, params)
        return await self._host.cdp(session.tab, method, params)

    async def _check_url(self, method: str, params: Mapping[str, object]) -> None:
        url = navigation_url(method, params)
        if url is None:
            return
        refused = url_refused(url) or await self._refuse(url)
        if refused is not None:
            raise _RefusedError(CDP_SERVER_ERROR, refused)

    async def _browser(
        self, connection: ServerConnection, link: _Link, method: str, params: dict[str, Any]
    ) -> object:
        match method:
            case "Browser.getVersion":
                return browser_version(await self._host.tabs())
            case "Target.getTargets":
                tabs = conversation_tabs(await self._host.tabs(), link.conversation_id)
                driven = {session.tab for session in self._sessions.values()}
                return {"targetInfos": [target_info(t, attached=t.id in driven) for t in tabs]}
            case "Target.createTarget":
                url = params.get("url", "about:blank")
                if not isinstance(url, str):
                    raise _RefusedError(INVALID_PARAMS, "url must be a string.")
                await self._check_url(method, {"url": url})
                background = params.get("background", True) is not False
                return {
                    "targetId": await self._host.open(
                        link.conversation_id, url, background=background
                    )
                }
            case "Target.attachToTarget":
                tab = await self._own_tab(link, params)
                if params.get("flatten") is not True:
                    raise _RefusedError(INVALID_PARAMS, "Not supported: attach with flatten=true.")
                if not any(s.tab == tab.id for s in self._sessions.values()):
                    await self._host.attach(tab.id)
                session_id = secrets.token_hex(16).upper()
                self._sessions[session_id] = _Session(connection, tab.id)
                return {"sessionId": session_id}
            case "Target.detachFromTarget":
                return await self._detach(connection, params.get("sessionId"))
            case "Target.closeTarget":
                tab = await self._own_tab(link, params)
                await self._host.close(tab.id)
                return {"success": True}
            case "Target.activateTarget":
                tab = await self._own_tab(link, params)
                await self._host.show(tab.id)
                return {}
            case "Target.getTargetInfo":
                if params.get("targetId") is None:
                    return {
                        "targetInfo": {
                            "targetId": "browser",
                            "type": "browser",
                            "title": "",
                            "url": "",
                            "attached": True,
                            "canAccessOpener": False,
                        }
                    }
                tab = await self._own_tab(link, params)
                driven = any(s.tab == tab.id for s in self._sessions.values())
                return {"targetInfo": target_info(tab, attached=driven)}
            case "Target.setDiscoverTargets" | "Target.setAutoAttach":
                return {}
            case _:
                pass
        raise _RefusedError(CDP_NOT_FOUND, wrong_method(method))

    async def _detach(self, connection: ServerConnection, session_id: object) -> object:
        session = self._sessions.get(session_id) if isinstance(session_id, str) else None
        if (
            not isinstance(session_id, str)
            or session is None
            or session.connection is not connection
        ):
            raise _RefusedError(INVALID_PARAMS, NO_SUCH_SESSION)
        del self._sessions[session_id]
        if not any(s.tab == session.tab for s in self._sessions.values()):
            await self._host.detach(session.tab)
        return {}

    async def _own_tab(self, link: _Link, params: Mapping[str, object]) -> HostTab:
        target = params.get("targetId")
        for tab in conversation_tabs(await self._host.tabs(), link.conversation_id):
            if tab.id == target:
                return tab
        raise _RefusedError(INVALID_PARAMS, "No target with given id found")

    async def _send(self, connection: ServerConnection, frame: dict[str, Any]) -> None:
        with contextlib.suppress(websockets.WebSocketException):
            await connection.send(json.dumps(frame, ensure_ascii=False))


def _error(reply_id: object, code: int, message: str) -> dict[str, Any]:
    return {"id": reply_id, "error": {"code": code, "message": message}}
