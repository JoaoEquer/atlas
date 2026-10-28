"""Signing in to a remote MCP server with OAuth 2.1, and keeping that sign-in fresh.

**What a server asks**, as the MCP authorization spec says:

- **Where to sign in**: the server's protected resource metadata (RFC 9728), at the address its
  401 names in `WWW-Authenticate`, else at the well-known address for its path, then for its root;
  then its authorization server's metadata (RFC 8414, then OpenID Connect discovery). A server
  that publishes neither gets the older spec's defaults at its origin (`/authorize`, `/token`,
  `/register`).
- **Who Atlas is**: a public client (`none`) registered for each sign-in (RFC 7591), so the
  redirect address it registers is the one it listens on.
- **The code grant with PKCE S256**, a `state` checked on the way back, the authorization
  server's `iss` checked when it sends one (RFC 9207), and `resource` naming the server
  (RFC 8707). An authorization server that lists its PKCE methods without S256 is refused.
- **Renewal**: the access token is renewed with the refresh token a minute before it ends, and
  once more when the server answers 401, the request then sent again once.

**A server that asks** without its entry saying `oauth` (Claude Code reads only the address) is
found by its 401 when it fails to connect: it is left out saying the same command.

**Who signs in** is the user, from a terminal: `atlas mcp login <server>`. The server's page says
who is signing in and asks to authorize Atlas, which nothing but a person can do, so a server
process never starts a sign-in: a server without one is left out, saying the command. The browser
comes back to a loopback address Atlas listens on for this sign-in only (RFC 8252). A browser on
another machine (Atlas in a container, or over SSH) cannot reach it: the page it lands on fails,
and its address, which holds the code, is pasted into the terminal instead.

**What is kept**, by server name, in the vault's second file (`secrets/mcp-sign-ins.json`,
`atlas.secrets`): the server's address, the client and token endpoint, and the tokens. A sign-in
made for another address is not used.
"""

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2

from atlas.mcp.errors import McpSignInError
from atlas.secrets import SecretsError

#: Seconds before a token's end at which it is renewed.
EARLY_SECONDS = 60.0
#: Seconds one request to the authorization server may take.
HTTP_SECONDS = 30.0
#: Seconds a sign-in waits for the user to authorize in the browser.
SIGN_IN_SECONDS = 600.0
CALLBACK_PATH = "/callback"
_CLIENT_NAME = "Atlas"
_logger = logging.getLogger("atlas.mcp")


class SignIns(Protocol):
    """Where sign-ins are kept by server name: the vault's second file (`atlas.secrets.Vault`)."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> bool: ...

    def register(self, value: str) -> None:
        """Keep `value` out of every later log line."""


@dataclass(frozen=True, slots=True)
class Endpoints:
    """Where a server's authorization server takes each step."""

    authorization: str
    token: str
    registration: str | None
    #: The server as the tokens name it (RFC 8707).
    resource: str
    #: The authorization server's `issuer`, when its metadata was found.
    issuer: str | None = None
    #: The scopes the server says it supports, asked for all at once.
    scope: str | None = None


@dataclass(frozen=True, slots=True)
class SignIn:
    """One server's sign-in, as kept."""

    url: str
    client_id: str
    token_endpoint: str
    resource: str
    access_token: str
    refresh_token: str | None = None
    #: When the access token ends (seconds since the epoch); `None` when the server did not say.
    expires_at: float | None = None

    def fresh(self, now: float) -> bool:
        return self.expires_at is None or now < self.expires_at - EARLY_SECONDS


# -- what is kept ---------------------------------------------------------------------------------


def read_sign_in(store: SignIns, server: str, url: str) -> SignIn | None:
    """The sign-in kept for `server` at `url`, or `None`. Raises `McpSignInError` when the file
    cannot be opened."""
    try:
        text = store.get(server)
    except SecretsError as error:
        raise McpSignInError(
            f"The sign-in of the MCP server {server} cannot be read: {error}"
        ) from error
    if not text:
        return None
    try:
        fields = cast("dict[str, Any]", json.loads(text))
        found = SignIn(**fields)
    except (ValueError, TypeError):
        _logger.warning(
            "The sign-in kept for the MCP server %s is damaged and is not used.", server
        )
        return None
    _register_tokens(store, found)
    return found if found.url == url else None


def _register_tokens(store: SignIns, sign_in: SignIn) -> None:
    for token in (sign_in.access_token, sign_in.refresh_token):
        if token:
            store.register(token)


def keep_sign_in(store: SignIns, server: str, sign_in: SignIn) -> None:
    _register_tokens(store, sign_in)
    try:
        store.set(server, json.dumps(asdict(sign_in)))
    except SecretsError as error:
        raise McpSignInError(
            f"The sign-in of the MCP server {server} cannot be kept: {error}"
        ) from error


# -- the steps ------------------------------------------------------------------------------------


def pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _well_known(url: str, name: str) -> list[str]:
    """The well-known addresses of `name` for `url`: inserted before its path, then at its root."""
    origin, path = _origin(url), urlsplit(url).path.rstrip("/")
    found = [f"{origin}/.well-known/{name}{path}"] if path else []
    return [*found, f"{origin}/.well-known/{name}"]


async def _json(http: httpx2.AsyncClient, url: str) -> dict[str, Any] | None:
    """The JSON object at `url`, or `None` when there is none there."""
    try:
        answer = await http.get(url, headers={"Accept": "application/json"})
    except httpx2.HTTPError:
        return None
    if answer.status_code != 200:
        return None
    try:
        found: object = answer.json()
    except ValueError:
        return None
    return cast("dict[str, Any]", found) if isinstance(found, dict) else None


def _hinted_metadata(answer: httpx2.Response) -> str | None:
    """The `resource_metadata` a 401's `WWW-Authenticate` names."""
    header = answer.headers.get("WWW-Authenticate", "")
    marker = "resource_metadata="
    at = header.find(marker)
    if at < 0:
        return None
    rest = header[at + len(marker) :].strip()
    if rest.startswith('"'):
        end = rest.find('"', 1)
        return rest[1:end] if end > 0 else None
    return rest.split(",")[0].strip() or None


async def discover(http: httpx2.AsyncClient, url: str) -> Endpoints:
    """Where the server at `url` signs in. Raises `McpSignInError`."""
    places = _well_known(url, "oauth-protected-resource")
    try:
        probe = await http.get(url, headers={"Accept": "text/event-stream"})
        hinted = _hinted_metadata(probe) if probe.status_code == 401 else None
    except httpx2.HTTPError as error:
        raise McpSignInError(f"The MCP server at {url} cannot be reached: {error}") from error
    if hinted:
        places.insert(0, hinted)
    resource_metadata: dict[str, Any] | None = None
    for place in places:
        resource_metadata = await _json(http, place)
        if resource_metadata is not None:
            break
    resource = url
    issuer = _origin(url)
    scope: str | None = None
    if resource_metadata is not None:
        resource = str(resource_metadata.get("resource") or url)
        if not url.rstrip("/").startswith(resource.rstrip("/")):
            raise McpSignInError(
                f"The MCP server at {url} says its tokens are for {resource}; Atlas refuses a "
                "token for another server."
            )
        servers = cast("list[Any]", resource_metadata.get("authorization_servers") or [])
        issuer = str(servers[0]) if servers else issuer
        scopes = cast("list[Any]", resource_metadata.get("scopes_supported") or [])
        scope = " ".join(str(item) for item in scopes) or None
    metadata: dict[str, Any] | None = None
    for place in [
        *_well_known(issuer, "oauth-authorization-server"),
        *_well_known(issuer, "openid-configuration"),
    ]:
        metadata = await _json(http, place)
        if metadata is not None:
            break
    if metadata is None:
        if resource_metadata is not None:
            raise McpSignInError(
                f"The authorization server {issuer} of the MCP server at {url} publishes no "
                "metadata (RFC 8414)."
            )
        origin = _origin(url)
        return Endpoints(f"{origin}/authorize", f"{origin}/token", f"{origin}/register", resource)
    named = str(metadata.get("issuer") or "")
    if named.rstrip("/") != issuer.rstrip("/"):
        raise McpSignInError(
            f"The authorization server at {issuer} names itself {named or 'nothing'}; Atlas "
            "refuses metadata of another server."
        )
    methods = metadata.get("code_challenge_methods_supported")
    if isinstance(methods, list) and "S256" not in cast("list[Any]", methods):
        raise McpSignInError(
            f"The authorization server {issuer} does not take PKCE with S256, which OAuth 2.1 asks."
        )
    try:
        authorization = str(metadata["authorization_endpoint"])
        token = str(metadata["token_endpoint"])
    except KeyError as error:
        raise McpSignInError(
            f"The authorization server {issuer} names no {error.args[0]}."
        ) from error
    registration = metadata.get("registration_endpoint")
    return Endpoints(
        authorization,
        token,
        str(registration) if registration else None,
        resource,
        issuer=named,
        scope=scope,
    )


async def register(http: httpx2.AsyncClient, endpoints: Endpoints, redirect: str) -> str:
    """Register Atlas as a public client that comes back to `redirect` (RFC 7591); its id."""
    if endpoints.registration is None:
        raise McpSignInError(
            f"The authorization server {endpoints.issuer} does not register clients (RFC 7591), "
            "which Atlas needs to sign in."
        )
    metadata: dict[str, Any] = {
        "client_name": _CLIENT_NAME,
        "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if endpoints.scope:
        metadata["scope"] = endpoints.scope
    try:
        answer = await http.post(endpoints.registration, json=metadata)
    except httpx2.HTTPError as error:
        raise McpSignInError(f"The client registration failed: {error}") from error
    found = _answer(answer, "The client registration")
    method = found.get("token_endpoint_auth_method", "none")
    if not found.get("client_id") or method != "none":
        raise McpSignInError(
            f"The authorization server registered Atlas as a client that signs with {method!r}; "
            "Atlas is a public client."
        )
    return str(found["client_id"])


def authorization_url(
    endpoints: Endpoints, client_id: str, redirect: str, challenge: str, state: str
) -> str:
    """The page where the user authorizes Atlas."""
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": endpoints.resource,
    }
    if endpoints.scope:
        query["scope"] = endpoints.scope
    joiner = "&" if "?" in endpoints.authorization else "?"
    return f"{endpoints.authorization}{joiner}{urlencode(query)}"


def _answer(answer: httpx2.Response, step: str) -> dict[str, Any]:
    """The JSON object of a successful answer. Raises `McpSignInError` with the server's error
    code and description only, never the rest of its body."""
    try:
        found: object = answer.json()
    except ValueError:
        found = None
    body = cast("dict[str, Any]", found) if isinstance(found, dict) else {}
    if answer.status_code in (200, 201) and body:
        return body
    error = " ".join(str(body.get(key, "")) for key in ("error", "error_description")).strip()
    raise McpSignInError(f"{step} was refused: HTTP {answer.status_code} {error}".rstrip())


def _signed(previous: SignIn, answer: dict[str, Any], now: float) -> SignIn:
    kind = str(answer.get("token_type", "bearer")).lower()
    access = answer.get("access_token")
    if kind != "bearer" or not isinstance(access, str) or not access:
        raise McpSignInError("The authorization server gave no bearer token.")
    lasts = answer.get("expires_in")
    seconds = lasts if isinstance(lasts, int | float) and not isinstance(lasts, bool) else None
    refresh = answer.get("refresh_token")
    return replace(
        previous,
        access_token=access,
        # A server that does not rotate its refresh token keeps the one it gave before.
        refresh_token=refresh if isinstance(refresh, str) and refresh else previous.refresh_token,
        expires_at=now + seconds if seconds is not None else None,
    )


async def _token(
    http: httpx2.AsyncClient, endpoint: str, form: dict[str, str], step: str
) -> dict[str, Any]:
    try:
        answer = await http.post(endpoint, data=form, headers={"Accept": "application/json"})
    except httpx2.HTTPError as error:
        raise McpSignInError(f"{step} failed: {error}") from error
    return _answer(answer, step)


async def renew(http: httpx2.AsyncClient, sign_in: SignIn, now: float) -> SignIn:
    """The sign-in with a new access token, from its refresh token. Raises `McpSignInError`."""
    if not sign_in.refresh_token:
        raise McpSignInError("The sign-in has no refresh token.")
    form = {
        "grant_type": "refresh_token",
        "refresh_token": sign_in.refresh_token,
        "client_id": sign_in.client_id,
        "resource": sign_in.resource,
    }
    return _signed(sign_in, await _token(http, sign_in.token_endpoint, form, "The renewal"), now)


# -- the sign-in ----------------------------------------------------------------------------------


class _Callback:
    """The loopback address the browser comes back to, for one sign-in (RFC 8252)."""

    def __init__(self) -> None:
        self.answer: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._server: asyncio.Server | None = None
        self.address = ""

    async def __aenter__(self) -> "_Callback":
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.address = f"http://127.0.0.1:{port}{CALLBACK_PATH}"
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), HTTP_SECONDS)).decode("latin-1")
            while (await asyncio.wait_for(reader.readline(), HTTP_SECONDS)) not in (
                b"\r\n",
                b"\n",
                b"",
            ):
                pass
            target = line.split(" ")[1] if line.count(" ") >= 2 else ""
            ours = urlsplit(target).path == CALLBACK_PATH
            if ours and not self.answer.done():
                self.answer.set_result(target)
            page = (
                "Atlas has the answer. You can close this window." if ours else "Not found."
            ).encode()
            status = "200 OK" if ours else "404 Not Found"
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                f"Content-Length: {len(page)}\r\nConnection: close\r\n\r\n".encode()
                + page
            )
            await writer.drain()
        except (OSError, TimeoutError, IndexError):
            pass
        finally:
            writer.close()


def _query(answer: str) -> dict[str, str]:
    """The parameters of the address the browser came back to, or of what the user pasted: the
    whole address, or only its query."""
    text = answer.strip()
    query = urlsplit(text).query if "?" in text else text
    return {key: values[0] for key, values in parse_qs(query).items() if values}


async def sign_in(
    name: str,
    url: str,
    store: SignIns,
    show: Callable[[str], None],
    paste: Callable[[], Awaitable[str]] | None = None,
    *,
    seconds: float = SIGN_IN_SECONDS,
) -> SignIn:
    """Sign the user in to the MCP server `name` at `url` and keep the sign-in.

    `show` gets the page to open; the browser comes back to a loopback address, or `paste` gives
    the address it landed on, whichever comes first. Raises `McpSignInError`."""
    async with httpx2.AsyncClient(timeout=HTTP_SECONDS, follow_redirects=True) as http:
        endpoints = await discover(http, url)
        async with _Callback() as callback:
            client_id = await register(http, endpoints, callback.address)
            verifier, challenge = pkce()
            state = secrets.token_urlsafe(32)
            show(authorization_url(endpoints, client_id, callback.address, challenge, state))
            waiting: list[asyncio.Future[str]] = [callback.answer]
            if paste is not None:
                waiting.append(asyncio.ensure_future(paste()))
            done, pending = await asyncio.wait(
                waiting, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
            )
            for left in pending:
                left.cancel()
            if not done:
                raise McpSignInError(f"No authorization came back in {seconds / 60:.0f} minutes.")
            query = _query(done.pop().result())
        if not secrets.compare_digest(query.get("state", ""), state):
            raise McpSignInError(
                "The answer is not this sign-in's: its state differs. Sign in again."
            )
        if "error" in query:
            why = " ".join(query.get(key, "") for key in ("error", "error_description")).strip()
            raise McpSignInError(f"The authorization was refused: {why}")
        returned = query.get("iss")
        if (
            returned is not None
            and endpoints.issuer
            and returned.rstrip("/") != endpoints.issuer.rstrip("/")
        ):
            raise McpSignInError(f"The answer came from {returned}, not from {endpoints.issuer}.")
        code = query.get("code")
        if not code:
            raise McpSignInError("The answer holds no authorization code.")
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": callback.address,
            "client_id": client_id,
            "code_verifier": verifier,
            "resource": endpoints.resource,
        }
        empty = SignIn(url, client_id, endpoints.token, endpoints.resource, "")
        signed = _signed(
            empty, await _token(http, endpoints.token, form, "The sign-in"), time.time()
        )
    keep_sign_in(store, name, signed)
    _logger.info("Signed in to the MCP server %s.", name)
    return signed


async def begin_sign_in(
    name: str, url: str, store: SignIns, *, seconds: float = SIGN_IN_SECONDS
) -> tuple[str, "asyncio.Task[SignIn]"]:
    """A sign-in the app starts with a button: the page for the app to open in the browser, as
    soon as Atlas has it, and the sign-in still waiting for the browser to come back, which keeps
    it. Raises `McpSignInError` when it fails before there is a page (no discovery, a refused
    registration, no PKCE S256)."""
    page: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def show(address: str) -> None:
        if not page.done():
            page.set_result(address)

    signing = asyncio.create_task(sign_in(name, url, store, show, seconds=seconds))
    await asyncio.wait({page, signing}, return_when=asyncio.FIRST_COMPLETED)
    if page.done():
        return page.result(), signing
    signing.result()  # raises what stopped it
    raise McpSignInError(f"The sign-in to {name} ended before it had a page to open.")


# -- the requests ---------------------------------------------------------------------------------


def login_hint(name: str) -> str:
    return f"Run `atlas mcp login {name}` in a terminal to sign in."


#: What Atlas sends to learn whether a server asks for a sign-in: the first message of a session.
_PROBE = {
    "jsonrpc": "2.0",
    "id": 0,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": _CLIENT_NAME, "version": "0"},
    },
}


async def asks_sign_in(url: str, headers: Mapping[str, str]) -> bool:
    """Whether a network server whose entry does not say `oauth` asks for a sign-in: it answers
    401 to a request without one, as the MCP authorization spec has it. Claude Code finds out the
    same way, so a plugin written for it (Cloudflare's) declares nothing more than the address."""
    try:
        async with httpx2.AsyncClient(headers=dict(headers), timeout=HTTP_SECONDS) as http:
            answer = await http.post(
                url,
                json=_PROBE,
                headers={"Accept": "application/json, text/event-stream"},
            )
    except httpx2.HTTPError:
        return False
    return answer.status_code == 401


class SignedIn(httpx2.Auth):
    """A server's bearer token on every request: renewed before it ends, and once more on a 401,
    the request then sent again once."""

    def __init__(
        self, name: str, url: str, store: SignIns, clock: Callable[[], float] = time.time
    ) -> None:
        self.name = name
        self._url = url
        self._store = store
        self._clock = clock
        self._lock = asyncio.Lock()
        self._current: SignIn | None = None

    async def current(self) -> SignIn:
        """The sign-in, renewed when it is about to end. Raises `McpSignInError` saying how to
        sign in when there is none, or it can no longer be renewed."""
        async with self._lock:
            found = self._current or read_sign_in(self._store, self.name, self._url)
            if found is None:
                raise McpSignInError(
                    f"The MCP server {self.name} signs in with OAuth and Atlas is not signed in "
                    f"to it. {login_hint(self.name)}"
                )
            if not found.fresh(self._clock()):
                found = await self._renewed(found)
            self._current = found
            return found

    async def _renewed(self, stale: SignIn) -> SignIn:
        """The caller holds the lock. Another process may have renewed it first."""
        kept = read_sign_in(self._store, self.name, self._url)
        if (
            kept is not None
            and kept.access_token != stale.access_token
            and kept.fresh(self._clock())
        ):
            return kept
        try:
            async with httpx2.AsyncClient(timeout=HTTP_SECONDS) as http:
                renewed = await renew(http, stale, self._clock())
        except McpSignInError as error:
            self._current = None
            raise McpSignInError(
                f"The sign-in to the MCP server {self.name} has ended and could not be renewed "
                f"({error}). {login_hint(self.name)}"
            ) from error
        keep_sign_in(self._store, self.name, renewed)
        _logger.info("The sign-in to the MCP server %s was renewed.", self.name)
        return renewed

    async def _after_refusal(self, refused: SignIn) -> SignIn:
        async with self._lock:
            if self._current is not None and self._current.access_token != refused.access_token:
                return self._current
            self._current = await self._renewed(refused)
            return self._current

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        signed = await self.current()
        request.headers["Authorization"] = f"Bearer {signed.access_token}"
        response = yield request
        if response.status_code != 401:
            return
        renewed = await self._after_refusal(signed)
        request.headers["Authorization"] = f"Bearer {renewed.access_token}"
        yield request

    def sync_auth_flow(self, request: httpx2.Request) -> Any:
        raise RuntimeError("An MCP sign-in is used by the async client only.")
