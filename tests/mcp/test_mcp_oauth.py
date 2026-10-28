"""OAuth 2.1 sign-in to a remote MCP server, against a real server on this machine.

The fake authorization server checks what a real one checks: a registered client and its
redirect address, the PKCE S256 verifier against the challenge, the `resource`, and the refresh
token. In front of it, the real MCP server of `test_mcp.py`, which answers 401 without a valid
bearer token. The browser is an HTTP client that follows the authorization page's redirect to
Atlas's loopback address, as a browser whose user authorized Atlas would.
"""

import asyncio
import base64
import hashlib
import io
import json
import secrets
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from atlas.__main__ import main
from atlas.agent import Toolset
from atlas.home import Redactor, SecretText, VaultSettings, open_home
from atlas.mcp import (
    McpClient,
    McpSignInError,
    ServerState,
    ServerStatus,
    read_sign_in,
    sign_in,
)
from atlas.mcp.oauth import begin_sign_in
from atlas.secrets import MCP_SIGN_IN_FILE, Vault

SERVER = Path(__file__).parent.parent / "mcp_server.py"


class Store:
    """Sign-ins in memory, as the vault's second file keeps them."""

    def __init__(self) -> None:
        self.kept: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.kept.get(name)

    def set(self, name: str, value: str) -> None:
        self.kept[name] = value

    def delete(self, name: str) -> bool:
        return self.kept.pop(name, None) is not None

    def register(self, value: str) -> None:
        pass


class FakeErp:
    """An MCP server behind its own OAuth 2.1 authorization server."""

    def __init__(self, *, expires_in: int = 3600, s256: bool = True) -> None:
        self.expires_in = expires_in
        self.s256 = s256
        self.refuse_renewal = False
        self.base = ""
        self.clients: dict[str, list[str]] = {}
        self.registrations: list[dict[str, Any]] = []
        self.authorizations: list[dict[str, str]] = []
        self.codes: dict[str, dict[str, str]] = {}
        self.access: set[str] = set()
        self.refresh: dict[str, str] = {}
        self.renewals = 0
        self.mcp_headers: list[str] = []
        sys.path.insert(0, str(SERVER.parent))
        from mcp_server import build  # noqa: PLC0415 - the test server module, by path

        self._mcp = build().streamable_http_app()
        self._oauth = Starlette(
            routes=[
                Route("/.well-known/oauth-protected-resource/mcp", self._resource),
                Route("/.well-known/oauth-authorization-server", self._metadata),
                Route("/oauth/register", self._register, methods=["POST"]),
                Route("/oauth/authorize", self._authorize),
                Route("/oauth/token", self._token, methods=["POST"]),
            ]
        )

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._mcp(scope, receive, send)
            return
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            header = dict(scope["headers"]).get(b"authorization", b"").decode()
            self.mcp_headers.append(header)
            if header.removeprefix("Bearer ") not in self.access:
                metadata = f"{self.base}/.well-known/oauth-protected-resource/mcp"
                refused = JSONResponse(
                    {"error": "invalid_token"},
                    401,
                    headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
                )
                await refused(scope, receive, send)
                return
            await self._mcp(scope, receive, send)
            return
        await self._oauth(scope, receive, send)

    def revoke(self) -> None:
        self.access.clear()

    async def _resource(self, request: Request) -> Response:
        return JSONResponse({"resource": f"{self.base}/mcp", "authorization_servers": [self.base]})

    async def _metadata(self, request: Request) -> Response:
        return JSONResponse(
            {
                "issuer": self.base,
                "authorization_endpoint": f"{self.base}/oauth/authorize",
                "token_endpoint": f"{self.base}/oauth/token",
                "registration_endpoint": f"{self.base}/oauth/register",
                "code_challenge_methods_supported": ["S256"] if self.s256 else ["plain"],
                "token_endpoint_auth_methods_supported": ["none"],
            }
        )

    async def _register(self, request: Request) -> Response:
        body = await request.json()
        self.registrations.append(body)
        client_id = secrets.token_hex(8)
        self.clients[client_id] = body["redirect_uris"]
        return JSONResponse({**body, "client_id": client_id}, 201)

    async def _authorize(self, request: Request) -> Response:
        query = dict(request.query_params)
        self.authorizations.append(query)
        assert query["redirect_uri"] in self.clients[query["client_id"]]
        assert query["code_challenge_method"] == "S256"
        code = secrets.token_hex(8)
        self.codes[code] = query
        back = urlencode({"code": code, "state": query["state"], "iss": self.base})
        return RedirectResponse(f"{query['redirect_uri']}?{back}", 302)

    async def _token(self, request: Request) -> Response:
        form = dict(await request.form())
        if form["grant_type"] == "authorization_code":
            asked = self.codes.pop(str(form["code"]), None)
            digest = hashlib.sha256(str(form["code_verifier"]).encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
            if (
                asked is None
                or asked["code_challenge"] != challenge
                or asked["redirect_uri"] != form["redirect_uri"]
                or asked["resource"] != form["resource"]
            ):
                return JSONResponse({"error": "invalid_grant"}, 400)
            return self._tokens(str(form["client_id"]))
        if self.refuse_renewal or self.refresh.pop(str(form["refresh_token"]), None) is None:
            return JSONResponse({"error": "invalid_grant", "error_description": "expired"}, 400)
        self.renewals += 1
        return self._tokens(str(form["client_id"]))

    def _tokens(self, client_id: str) -> Response:
        access, refresh = secrets.token_hex(16), secrets.token_hex(16)
        self.access.add(access)
        self.refresh[refresh] = client_id
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": self.expires_in,
                "refresh_token": refresh,
            }
        )


@contextmanager
def serving(erp: FakeErp) -> Iterator[str]:
    """Serve the fake on a free port, in a thread of its own, for the length of the block."""
    server = uvicorn.Server(uvicorn.Config(erp, host="127.0.0.1", port=0, log_config=None))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    erp.base = f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    try:
        yield erp.base
    finally:
        server.should_exit = True
        thread.join(10)


def browse(page: str) -> None:
    """The user authorizes Atlas: the browser opens the page and follows it back to Atlas."""

    def run() -> None:
        with httpx2.Client(follow_redirects=True, timeout=10) as browser:
            browser.get(page)

    threading.Thread(target=run, daemon=True).start()


def landed(page: str, **changed: str) -> Callable[[], Awaitable[str]]:
    """What a user pastes when the browser could not reach Atlas: the address it landed on."""

    async def paste() -> str:
        async with httpx2.AsyncClient() as browser:
            location = (await browser.get(page)).headers["location"]
        parts = urlsplit(location)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        return f"{parts.scheme}://{parts.netloc}{parts.path}?{urlencode(query | changed)}"

    return paste


def _write(home: Path, servers: dict[str, Any]) -> Path:
    file = home / "mcp.json"
    file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return file


async def _call(toolset: Toolset, name: str, **arguments: Any) -> str:
    tool = toolset.get(name)
    assert tool is not None, f"{name} is not in the toolset"
    output = await tool.run(arguments)
    assert isinstance(output, str)
    return output


# -- the sign-in ----------------------------------------------------------------------------------


def test_sign_in_registers_atlas_and_keeps_tokens_got_with_pkce() -> None:
    erp = FakeErp()
    store = Store()
    with serving(erp) as base:
        url = f"{base}/mcp"
        signed = asyncio.run(sign_in("erp", url, store, browse))

    registered = erp.registrations[0]
    assert registered["token_endpoint_auth_method"] == "none"
    assert registered["grant_types"] == ["authorization_code", "refresh_token"]
    assert registered["redirect_uris"][0].startswith("http://127.0.0.1:")
    asked = erp.authorizations[0]
    assert asked["code_challenge_method"] == "S256"
    assert asked["resource"] == url
    assert signed.access_token in erp.access
    kept = read_sign_in(store, "erp", url)
    assert kept is not None
    assert kept == signed
    assert kept.refresh_token is not None
    assert kept.token_endpoint == f"{base}/oauth/token"
    assert read_sign_in(store, "erp", f"{base}/other") is None  # made for another address


def test_the_address_the_browser_landed_on_may_be_pasted() -> None:
    erp = FakeErp()
    store = Store()
    pages: list[str] = []
    with serving(erp) as base:

        async def scenario() -> None:
            async def paste() -> str:
                while not pages:
                    await asyncio.sleep(0.01)
                return await landed(pages[0])()

            await sign_in("erp", f"{base}/mcp", store, pages.append, paste)

        asyncio.run(scenario())

    assert "erp" in store.kept


def test_an_answer_to_another_sign_in_is_refused_and_nothing_is_kept() -> None:
    erp = FakeErp()
    store = Store()
    pages: list[str] = []
    with serving(erp) as base:

        async def scenario() -> None:
            async def paste() -> str:
                while not pages:
                    await asyncio.sleep(0.01)
                return await landed(pages[0], state="someone-else")()

            await sign_in("erp", f"{base}/mcp", store, pages.append, paste)

        with pytest.raises(McpSignInError, match="state differs"):
            asyncio.run(scenario())

    assert store.kept == {}


def test_the_app_s_sign_in_gives_the_page_first_and_keeps_the_sign_in_once_authorized() -> None:
    erp = FakeErp()
    store = Store()
    with serving(erp) as base:
        url = f"{base}/mcp"

        async def scenario() -> tuple[str, bool]:
            page, signing = await begin_sign_in("erp", url, store)
            kept_before = read_sign_in(store, "erp", url) is not None
            browse(page)  # the app opens it; the user authorizes
            await signing
            return page, kept_before

        page, kept_before = asyncio.run(scenario())

    assert "/oauth/authorize?" in page
    assert kept_before is False
    assert read_sign_in(store, "erp", f"{base}/mcp") is not None


def test_the_app_s_sign_in_fails_before_a_page_when_the_server_cannot_be_signed_in_to() -> None:
    erp = FakeErp(s256=False)
    with serving(erp) as base, pytest.raises(McpSignInError, match="S256"):
        asyncio.run(begin_sign_in("erp", f"{base}/mcp", Store()))


def test_an_authorization_server_without_pkce_s256_is_refused() -> None:
    erp = FakeErp(s256=False)
    with serving(erp) as base, pytest.raises(McpSignInError, match="S256"):
        asyncio.run(sign_in("erp", f"{base}/mcp", Store(), browse))

    assert erp.registrations == []


# -- the connection -------------------------------------------------------------------------------


def test_a_signed_in_server_gets_its_token_and_a_401_renews_it_once(tmp_path: Path) -> None:
    erp = FakeErp()
    store = Store()
    with serving(erp) as base:
        url = f"{base}/mcp"
        file = _write(tmp_path, {"erp": {"type": "http", "url": url, "oauth": {}}})

        async def scenario() -> tuple[str, str]:
            await sign_in("erp", url, store, browse)
            erp.mcp_headers.clear()  # the sign-in's probe, before any token
            toolset = Toolset()
            client = McpClient(file, {}.get, toolset, sign_ins=store)
            await client.start()
            first = await _call(toolset, "mcp__erp__add_note", text="a")
            erp.revoke()  # the server forgets the token: the next request gets a 401
            second = await _call(toolset, "mcp__erp__add_note", text="b")
            await client.close()
            return first, second

        said = asyncio.run(scenario())

    assert said == ("added note 1", "added note 2")
    assert erp.renewals == 1
    assert all(header.startswith("Bearer ") for header in erp.mcp_headers)
    assert json.loads(store.kept["erp"])["access_token"] in erp.access  # the renewed one is kept


def test_a_token_about_to_end_is_renewed_before_connecting(tmp_path: Path) -> None:
    erp = FakeErp(expires_in=5)  # within the minute before its end
    store = Store()
    with serving(erp) as base:
        url = f"{base}/mcp"
        file = _write(tmp_path, {"erp": {"type": "http", "url": url, "oauth": {}}})

        async def scenario() -> str:
            await sign_in("erp", url, store, browse)
            erp.mcp_headers.clear()  # the sign-in's probe, before any token
            toolset = Toolset()
            client = McpClient(file, {}.get, toolset, sign_ins=store)
            await client.start()
            said = await _call(toolset, "mcp__erp__add_note", text="a")
            await client.close()
            return said

        assert asyncio.run(scenario()) == "added note 1"

    assert erp.renewals >= 1
    assert erp.mcp_headers
    assert all(h.startswith("Bearer ") for h in erp.mcp_headers)


def test_a_server_without_a_sign_in_is_left_out_saying_how_then_connects(tmp_path: Path) -> None:
    erp = FakeErp()
    store = Store()
    with serving(erp) as base:
        url = f"{base}/mcp"
        file = _write(
            tmp_path,
            {
                "erp": {"type": "http", "url": url, "oauth": {}},
                "notes": {"command": "python", "args": [str(SERVER)]},
            },
        )

        async def scenario() -> tuple[str, bool, bool, str | None, list[ServerStatus]]:
            toolset = Toolset()
            client = McpClient(file, {}.get, toolset, sign_ins=store)
            await client.start()
            before = client.problems.get("erp", "")
            notes = toolset.get("mcp__notes__add_note") is not None
            statuses = client.status()
            await sign_in("erp", url, store, browse)  # `atlas mcp login erp`, elsewhere
            erp.mcp_headers.clear()
            await client.signed_in()
            erp_now = toolset.get("mcp__erp__add_note") is not None
            statuses += client.status()
            await client.close()
            return before, notes, erp_now, client.problems.get("erp"), statuses

        before, notes, erp_now, after, statuses = asyncio.run(scenario())

    assert "atlas mcp login erp" in before
    assert notes is True  # the other server connected
    # What the app shows: the server that signs in, left out until the user signed in, then
    # connected; the stdio one never signs in.
    by_name = [(row.name, row.state, row.signs_in) for row in statuses]
    assert by_name == [
        ("erp", ServerState.LEFT_OUT, True),
        ("notes", ServerState.CONNECTED, False),
        ("erp", ServerState.CONNECTED, True),
        ("notes", ServerState.CONNECTED, False),
    ]
    assert erp_now is True
    assert after is None
    assert erp.mcp_headers == [] or all(h.startswith("Bearer ") for h in erp.mcp_headers)


def test_a_server_that_asks_without_its_entry_saying_oauth_is_found_by_its_401(
    tmp_path: Path,
) -> None:
    # As Claude Code reads a plugin's server (Cloudflare's): the address only.
    erp = FakeErp()
    store = Store()
    with serving(erp) as base:
        url = f"{base}/mcp"
        file = _write(tmp_path, {"erp": {"type": "http", "url": url}})

        async def scenario() -> tuple[str, bool, str | None, bool]:
            toolset = Toolset()
            client = McpClient(file, {}.get, toolset, sign_ins=store)
            await client.start()
            before = client.problems.get("erp", "")
            # The app sends it to the terminal too, though its entry never said oauth.
            [status] = client.status()
            await sign_in("erp", url, store, browse)
            await client.signed_in()
            connected = toolset.get("mcp__erp__add_note") is not None
            await client.close()
            return before, connected, client.problems.get("erp"), status.signs_in

        before, connected, after, signs_in = asyncio.run(scenario())

    assert "signs in with OAuth" in before
    assert signs_in is True
    assert "atlas mcp login erp" in before
    assert connected is True
    assert after is None


def test_a_server_that_fails_without_a_401_says_its_own_error(tmp_path: Path) -> None:
    file = _write(tmp_path, {"gone": {"type": "http", "url": "http://127.0.0.1:9/mcp"}})

    async def scenario() -> tuple[str, bool]:
        client = McpClient(file, {}.get, Toolset(), sign_ins=Store())
        await client.start()
        await client.close()
        return client.problems.get("gone", ""), client.status()[0].signs_in

    problem, signs_in = asyncio.run(scenario())
    assert problem
    assert "atlas mcp login" not in problem
    assert signs_in is False


def test_the_app_s_sign_in_button_connects_the_server_once_the_user_authorized(
    tmp_path: Path,
) -> None:
    erp = FakeErp()
    store = Store()
    with serving(erp) as base:
        file = _write(tmp_path, {"erp": {"type": "http", "url": f"{base}/mcp", "oauth": {}}})

        async def scenario() -> tuple[str, str, bool, str | None]:
            toolset = Toolset()
            client = McpClient(file, {}.get, toolset, sign_ins=store)
            await client.start()
            before = client.problems.get("erp", "")
            page = await client.sign_in("erp")  # the button; the app opens the page
            browse(page)  # the user authorizes Atlas
            for _ in range(200):
                if toolset.get("mcp__erp__add_note") is not None:
                    break
                await asyncio.sleep(0.05)
            connected = toolset.get("mcp__erp__add_note") is not None
            await client.close()
            return before, page, connected, client.problems.get("erp")

        before, page, connected, after = asyncio.run(scenario())

    assert "atlas mcp login erp" in before
    assert "/oauth/authorize?" in page
    assert connected is True
    assert after is None


def test_the_sign_in_button_says_why_a_server_cannot_sign_in(tmp_path: Path) -> None:
    file = _write(tmp_path, {"notes": {"command": "python", "args": [str(SERVER)]}})

    async def scenario() -> list[str]:
        client = McpClient(file, {}.get, Toolset(), sign_ins=Store())
        await client.start()
        why: list[str] = []
        for name in ("notes", "crm"):
            try:
                await client.sign_in(name)
            except McpSignInError as error:
                why.append(str(error))
        await client.close()
        return why

    assert asyncio.run(scenario()) == [
        "The MCP server notes runs on this machine and does not sign in.",
        "No MCP server is named crm.",
    ]


def test_a_renewal_refused_leaves_the_server_out_saying_to_sign_in_again(tmp_path: Path) -> None:
    erp = FakeErp(expires_in=5)
    store = Store()
    with serving(erp) as base:
        url = f"{base}/mcp"
        file = _write(tmp_path, {"erp": {"type": "http", "url": url, "oauth": {}}})

        async def scenario() -> tuple[str, bool]:
            await sign_in("erp", url, store, browse)
            erp.refuse_renewal = True
            toolset = Toolset()
            client = McpClient(file, {}.get, toolset, sign_ins=store)
            await client.start()
            connected = toolset.get("mcp__erp__add_note") is not None
            await client.close()
            return client.problems.get("erp", ""), connected

        problem, connected = asyncio.run(scenario())

    assert connected is False
    assert "could not be renewed" in problem
    assert "atlas mcp login erp" in problem
    assert "invalid_grant" in problem


# -- where it is kept, and the command ------------------------------------------------------------


def test_sign_ins_are_kept_encrypted_beside_the_vault_never_among_its_secrets(
    tmp_path: Path,
) -> None:
    import pyrage  # noqa: PLC0415 - only here, to make a key

    home = open_home(tmp_path / "home")
    key = str(pyrage.x25519.Identity.generate())
    vault = Vault(home.secrets_folder, VaultSettings(key=SecretText(key)), Redactor())
    sign_ins = vault.beside(MCP_SIGN_IN_FILE)
    erp = FakeErp()
    with serving(erp) as base:
        signed = asyncio.run(sign_in("erp", f"{base}/mcp", sign_ins, browse))

    assert vault.names() == []
    assert sign_ins.names() == ["erp"]
    written = (home.secrets_folder / MCP_SIGN_IN_FILE).read_text(encoding="utf-8")
    assert signed.access_token not in written
    assert signed.refresh_token is not None
    assert signed.refresh_token not in written
    assert sign_ins.redact(f"token {signed.access_token}") != f"token {signed.access_token}"


def test_atlas_mcp_login_and_logout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import webbrowser  # noqa: PLC0415 - the browser the command opens, made the test's

    monkeypatch.setattr(webbrowser, "open", lambda page: browse(page) or True)  # pyright: ignore[reportUnknownLambdaType]
    home = tmp_path / "home"
    erp = FakeErp()
    with serving(erp) as base:
        home.mkdir()
        _write(home, {"erp": {"type": "http", "url": f"{base}/mcp", "oauth": {}}})
        environ = {"ATLAS_HOME": str(home)}

        unknown = main(["mcp", "login", "crm"], dict(environ), io.StringIO(""))
        logged = main(["mcp", "login", "erp"], dict(environ), io.StringIO(""))
    out = capsys.readouterr()
    gone = main(["mcp", "logout", "erp"], dict(environ), io.StringIO(""))
    again = main(["mcp", "logout", "erp"], dict(environ), io.StringIO(""))

    assert unknown == 1
    assert "has a server named crm (there are: erp)" in out.err
    assert logged == 0
    assert "Signed in to erp." in out.out
    assert "/oauth/authorize?" in out.out
    assert gone == 0
    assert again == 1


def test_atlas_mcp_login_finds_the_server_of_a_plugin_that_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import webbrowser  # noqa: PLC0415 - the browser the command opens, made the test's

    monkeypatch.setattr(webbrowser, "open", lambda page: browse(page) or True)  # pyright: ignore[reportUnknownLambdaType]
    home = tmp_path / "home"
    erp = FakeErp()
    with serving(erp) as base:
        folder = home / "plugins" / "cloud"
        (folder / ".claude-plugin").mkdir(parents=True)
        manifest = {"name": "cloud", "version": "1.0.0", "mcpServers": "./.mcp.json"}
        (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
        servers = {"mcpServers": {"api": {"type": "http", "url": f"{base}/mcp"}}}
        (folder / ".mcp.json").write_text(json.dumps(servers), encoding="utf-8")
        environ = {"ATLAS_HOME": str(home)}

        off = main(["mcp", "login", "plugin_cloud_api"], dict(environ), io.StringIO(""))
        assert main(["plugins", "enable", "cloud", "--yes"], dict(environ)) == 0
        logged = main(["mcp", "login", "plugin_cloud_api"], dict(environ), io.StringIO(""))
    out = capsys.readouterr()

    assert off == 1  # a plugin that is off declares nothing yet
    assert logged == 0
    assert "Signed in to plugin_cloud_api." in out.out
