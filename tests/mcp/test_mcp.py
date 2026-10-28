"""The MCP client against real MCP servers built with the official SDK: one over stdio, run as a
subprocess, and one over streamable HTTP. The configuration is a real mcp.json in a temporary home,
and secrets come from a vault-like lookup, as the composition root wires the vault."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import mcp_types
import pytest
import uvicorn

from atlas.agent import AgentLoop, Tool, Toolset
from atlas.mcp import (
    DESCRIPTION_CHARS,
    TOOL_NAME_CHARS,
    McpClient,
    McpConfigError,
    McpConnection,
    ServerConfig,
    Transport,
    filled,
    parse_config,
    secrets_needed,
    tool_name,
)
from atlas.mcp.model import cut_description, model_schema
from atlas.models import TOOL_SEARCH, SystemMessage
from atlas.tools import install_tool_search
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run

SERVER = Path(__file__).parent.parent / "mcp_server.py"
CHAT = "/v1/chat/completions"


def _write(home: Path, servers: dict[str, Any]) -> Path:
    file = home / "mcp.json"
    file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return file


def _notes(**extra: Any) -> dict[str, Any]:
    return {"command": "python", "args": [str(SERVER)], **extra}


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    async def main() -> T:
        return await scenario()

    return asyncio.run(main())


async def _call(toolset: Toolset, name: str, **arguments: Any) -> str:
    tool = toolset.get(name)
    assert tool is not None, name
    output = await tool.run(arguments)
    assert isinstance(output, str)
    return output


# -- the configuration ---------------------------------------------------------------------------


def test_the_file_is_claude_codes_mcp_json() -> None:
    servers = parse_config(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.example/mcp",
                        "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
                    },
                    "files": {"command": "npx", "args": ["-y", "server"], "env": {"A": "b"}},
                    "remote": {
                        "type": "streamable-http",
                        "url": "http://localhost:1/mcp",
                        "timeout": 600000,
                        "alwaysLoad": True,
                        "headersHelper": "print-headers",
                    },
                    "events": {"type": "sse", "url": "http://localhost:2/sse", "timeout": 500},
                    "newer": {"command": "x", "someFieldOfANewerClaudeCode": 1},
                }
            }
        ),
        "mcp.json",
    )

    github, files, remote, events, newer = servers
    assert (github.transport, github.source) == (Transport.HTTP, "mcp:github")
    assert files == ServerConfig(
        "files", Transport.STDIO, command="npx", args=("-y", "server"), env={"A": "b"}
    )
    assert (remote.transport, remote.timeout, remote.always_load) == (Transport.HTTP, 600, True)
    assert remote.headers_helper == "print-headers"
    # Claude Code ignores a timeout below a second.
    assert (events.transport, events.timeout) == (Transport.SSE, None)
    assert newer.ignored == ("someFieldOfANewerClaudeCode",)
    assert secrets_needed(github) == ["GITHUB_TOKEN"]
    assert parse_config("", "mcp.json") == []


def test_a_server_is_trusted_unless_it_says_so_and_a_typo_is_untrusted() -> None:
    plain, full, untrusted, typo = parse_config(
        json.dumps(
            {
                "mcpServers": {
                    "plain": {"command": "x"},
                    "full": {"command": "x", "trust": "full"},
                    "crm": {"command": "x", "trust": "untrusted"},
                    "typo": {"command": "x", "trust": "ful"},
                }
            }
        ),
        "mcp.json",
    )

    assert (plain.trusted, full.trusted, untrusted.trusted, typo.trusted) == (
        True,
        True,
        False,
        False,
    )
    assert untrusted.ignored == ()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{", "is not valid JSON"),
        ("[]", "must be a JSON object"),
        ('{"mcpServers": []}', "must be an object of servers"),
        ('{"mcpServers": {"a": {}}}', "needs a command"),
        ('{"mcpServers": {"a": {"url": "http://x/mcp"}}}', "has a url but no type"),
        ('{"mcpServers": {"a": {"type": "ws", "url": "ws://x"}}}', "takes stdio, http and sse"),
        ('{"mcpServers": {"a": {"command": "x", "timeout": "1m"}}}', "number of milliseconds"),
        ('{"mcpServers": {"a": {"command": "x", "alwaysLoad": "yes"}}}', "must be true or false"),
        ('{"mcpServers": {"a": {"type": "http", "url": "ftp://x"}}}', "needs an http"),
        ('{"mcpServers": {"a b": {"command": "x"}}}', "needs a name"),
        ('{"mcpServers": {"a": {"command": "x", "args": "y"}}}', "args must be a list"),
        ('{"mcpServers": {"a": {"command": "x", "env": {"K": 1}}}}', "must be text"),
    ],
)
def test_a_malformed_file_says_what_is_wrong(text: str, message: str) -> None:
    with pytest.raises(McpConfigError, match=message):
        parse_config(text, "mcp.json")


def test_secrets_come_from_the_vault_and_a_missing_one_says_how_to_set_it() -> None:
    server = ServerConfig(
        "s", Transport.HTTP, url="https://x/${PATH_PART}", headers={"K": "Bearer ${TOKEN}"}
    )
    vault = {"TOKEN": "t0k", "PATH_PART": "mcp"}

    done = filled(server, vault.get)

    assert (done.url, done.headers["K"]) == ("https://x/mcp", "Bearer t0k")
    with pytest.raises(McpConfigError, match=r"needs TOKEN in the vault.*atlas secrets set TOKEN"):
        filled(server, {"PATH_PART": "mcp"}.get)


def test_a_secret_may_have_a_default_as_in_claude_code() -> None:
    server = ServerConfig("s", Transport.HTTP, url="https://${HOST:-api.example}/mcp")

    assert secrets_needed(server) == []
    assert filled(server, {}.get).url == "https://api.example/mcp"
    assert filled(server, {"HOST": "mine.example"}.get).url == "https://mine.example/mcp"


def test_schemas_are_held_to_what_providers_take() -> None:
    flat, note = model_schema(
        {
            "anyOf": [
                {"properties": {"id": {"type": "string"}}, "required": ["id"]},
                {"properties": {"name": {"type": "string"}}, "required": ["name"]},
            ]
        }
    )

    assert flat == {
        "type": "object",
        "properties": {"id": {"type": "string"}, "name": {"type": "string"}},
    }
    assert note == "Pass at least one of these groups of parameters: (id); (name). "
    with pytest.raises(McpConfigError, match="are not 1 to 64 letters"):
        model_schema({"type": "object", "properties": {"bad name!": {"type": "string"}}})
    with pytest.raises(McpConfigError, match="not an object"):
        model_schema({"type": "string"})
    assert cut_description("x" * 3_000).endswith("…")
    assert len(cut_description("x" * 3_000)) == DESCRIPTION_CHARS


def test_tool_names_are_claude_codes_and_fit_providers() -> None:
    long = tool_name("a-very-long-server-name", "x" * 80)

    assert tool_name("github", "create issue") == "mcp__github__create_issue"
    assert len(long) == TOOL_NAME_CHARS
    assert long == tool_name("a-very-long-server-name", "x" * 80)


# -- a server over stdio -------------------------------------------------------------------------


def test_a_stdio_server_keeps_its_session_and_its_tools_are_deferred(tmp_path: Path) -> None:
    file = _write(tmp_path, {"notes": _notes(env={"NOTES_TOKEN": "${NOTES_TOKEN}"})})
    toolset = Toolset()
    installs: list[int] = []

    async def scenario() -> tuple[list[str], str, str, str, str, Tool | None]:
        def changed() -> None:
            installs.append(1)
            install_tool_search(toolset, client.sources)

        client = McpClient(file, {"NOTES_TOKEN": "s3cret"}.get, toolset, changed)
        await client.start()
        names = sorted(t.name for t in toolset.deferred)
        await _call(toolset, "mcp__notes__add_note", text="first")
        await _call(toolset, "mcp__notes__add_note", text="second")
        listed = await _call(toolset, "mcp__notes__list_notes")
        failed = await _call(toolset, "mcp__notes__broken")
        token = await _call(toolset, "mcp__notes__token")
        search = toolset.get(TOOL_SEARCH)
        await client.close()
        return (
            names,
            listed,
            failed,
            token,
            search.description if search else "",
            toolset.get("mcp__notes__add_note"),
        )

    names, listed, failed, token, search, after = _run(scenario)

    assert names == [
        "mcp__notes__add_note",
        "mcp__notes__broken",
        "mcp__notes__list_notes",
        "mcp__notes__token",
    ]
    assert listed == "first\nsecond"  # the same session served both calls
    assert failed.startswith('<untrusted_tool_result source="mcp:notes">')  # outside text
    assert "[The tool reported an error]" in failed
    assert "broken" in failed  # the SDK's server keeps the exception's own text to itself
    assert token == "s3cret"
    assert "notes (4 tools): Notes kept by a test server.\n- mcp__notes__add_note: Add a" in search
    assert after is None
    assert installs


def test_read_only_tools_say_so(tmp_path: Path) -> None:
    file = _write(tmp_path, {"notes": _notes()})
    toolset = Toolset()

    async def scenario() -> dict[str, bool]:
        client = McpClient(file, {}.get, toolset)
        await client.start()
        flags = {t.name: t.read_only for t in toolset.deferred}
        await client.close()
        return flags

    flags = _run(scenario)

    assert flags["mcp__notes__list_notes"] is True
    assert flags["mcp__notes__add_note"] is False


def test_a_server_that_cannot_start_is_left_out_and_the_others_connect(tmp_path: Path) -> None:
    file = _write(
        tmp_path,
        {
            "notes": _notes(),
            "missing": {"command": str(tmp_path / "no-such-program")},
            "keyless": _notes(env={"T": "${MISSING_TOKEN}"}),
        },
    )
    toolset = Toolset()

    async def scenario() -> McpClient:
        client = McpClient(file, {}.get, toolset)
        await client.start()
        await client.close()
        return client

    client = _run(scenario)

    assert set(client.problems) == {"missing", "keyless"}
    assert "atlas secrets set MISSING_TOKEN" in client.problems["keyless"]
    assert "could not start" in client.problems["missing"]


def test_a_broken_file_is_reported_and_atlas_goes_on(tmp_path: Path) -> None:
    (tmp_path / "mcp.json").write_text("{", encoding="utf-8")

    async def scenario() -> McpClient:
        client = McpClient(tmp_path / "mcp.json", {}.get, Toolset())
        await client.start()
        return client

    assert "is not valid JSON" in _run(scenario).problems["mcp.json"]


def test_no_file_no_servers(tmp_path: Path) -> None:
    async def scenario() -> Toolset:
        toolset = Toolset()
        await McpClient(tmp_path / "mcp.json", {}.get, toolset).start()
        return toolset

    assert _run(scenario).deferred == ()


def test_a_session_that_died_opens_again_on_the_next_call(tmp_path: Path) -> None:
    file = _write(tmp_path, {"notes": _notes()})
    toolset = Toolset()

    async def scenario() -> str:
        client = McpClient(file, {}.get, toolset)
        await client.start()
        connection = client._connections["notes"]  # pyright: ignore[reportPrivateUsage]
        await connection.close()
        said = await _call(toolset, "mcp__notes__add_note", text="again")
        await client.close()
        return said

    assert _run(scenario) == "added note 1"


# -- a server over HTTP --------------------------------------------------------------------------


def test_an_http_server_gets_its_secret_header(tmp_path: Path) -> None:
    sys.path.insert(0, str(SERVER.parent))
    from mcp_server import build  # noqa: PLC0415 - the test server module, by path

    app = build().streamable_http_app()
    seen: list[str] = []

    async def recording(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            seen.append(dict(scope["headers"]).get(b"authorization", b"").decode())
        await app(scope, receive, send)

    async def scenario() -> str:
        config = uvicorn.Config(recording, host="127.0.0.1", port=0, log_config=None)
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        file = _write(
            tmp_path,
            {
                "web": {
                    "type": "http",
                    "url": f"http://127.0.0.1:{port}/mcp",
                    "headers": {"Authorization": "Bearer ${WEB_TOKEN}"},
                }
            },
        )
        toolset = Toolset()
        client = McpClient(file, {"WEB_TOKEN": "h3ader"}.get, toolset)
        await client.start()
        said = await _call(toolset, "mcp__web__add_note", text="over http")
        await client.close()
        server.should_exit = True
        await task
        return said

    assert _run(scenario) == "added note 1"
    assert seen
    assert all(header == "Bearer h3ader" for header in seen)


# -- in a turn -----------------------------------------------------------------------------------


def test_the_model_finds_an_mcp_tool_and_calls_it_through_the_gate(
    tmp_path: Path, fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    file = _write(tmp_path, {"notes": _notes()})
    fake.reply(
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "add a note"}', id="s1")]),
        FakeReply(calls=[FakeCall("mcp__notes__add_note", '{"text": "milk"}', id="n1")]),
        FakeReply(text="Noted."),
    )
    models = connect(fake, "openrouter")
    gated: list[str] = []

    async def gate(tool: Tool, arguments: dict[str, Any]) -> str | None:
        gated.append(f"{tool.source} {tool.name}")
        return None

    async def scenario() -> Any:
        toolset = Toolset()
        client = McpClient(
            file, {}.get, toolset, lambda: install_tool_search(toolset, client.sources)
        )
        await client.start()
        ended = await AgentLoop(models.client("openrouter"), toolset, gate=gate).reply(
            [SystemMessage("Atlas.")], "Note milk"
        )
        await client.close()
        return ended

    ended = run(models, scenario)

    assert ended.text == "Noted."
    assert gated == ["atlas tool_search", "mcp:notes mcp__notes__add_note"]
    first_tools = [t["function"]["name"] for t in fake.bodies(CHAT)[0]["tools"]]
    assert first_tools == ["mcp__notes__today", TOOL_SEARCH]  # the server marks today always on
    assert fake.bodies(CHAT)[2]["messages"][-1]["content"] == "added note 1"


# -- Claude Code's per-server fields -------------------------------------------------------------


def _connected(tmp_path: Path, servers: dict[str, Any], vault: dict[str, str] | None = None) -> Any:
    """Start a client on these servers, run `check(client, toolset)`, and close it."""
    file = _write(tmp_path, servers)

    def run(check: Callable[[McpClient, Toolset], Awaitable[Any]]) -> Any:
        async def scenario() -> Any:
            toolset = Toolset()
            client = McpClient(file, (vault or {}).get, toolset)
            await client.start()
            try:
                return await check(client, toolset)
            finally:
                await client.close()

        return _run(scenario)

    return run


def test_always_load_keeps_tools_on_as_the_entry_or_the_server_says(tmp_path: Path) -> None:
    async def flags(client: McpClient, toolset: Toolset) -> dict[str, bool]:
        return {t.name: t.deferred for t in [*toolset.always, *toolset.deferred]}

    marked = _connected(tmp_path, {"notes": _notes()})(flags)
    every = _connected(tmp_path, {"notes": _notes(alwaysLoad=True)})(flags)
    none = _connected(tmp_path, {"notes": _notes(alwaysLoad=False)})(flags)

    assert marked["mcp__notes__today"] is False  # the server marks it anthropic/alwaysLoad
    assert marked["mcp__notes__add_note"] is True
    assert not any(every.values())
    assert all(none.values())


def test_a_server_that_signs_in_with_oauth_is_left_out_with_the_reason(tmp_path: Path) -> None:
    """Without a sign-in, or anywhere to keep one; `test_mcp_oauth.py` signs in."""

    async def problems(client: McpClient, toolset: Toolset) -> dict[str, str]:
        return client.problems

    found = _connected(
        tmp_path,
        {"slack": {"type": "http", "url": "https://mcp.slack.example/mcp", "oauth": {}}},
    )(problems)

    assert "signs in with OAuth, and this Atlas keeps no sign-ins" in found["slack"]


def test_a_tool_whose_schema_no_provider_takes_is_left_out_and_said(tmp_path: Path) -> None:
    connection = McpConnection(ServerConfig("odd", Transport.STDIO, command="x"))
    good = mcp_types.Tool(name="fine", input_schema={"type": "object", "properties": {}})
    bad_name = mcp_types.Tool(
        name="spaced", input_schema={"type": "object", "properties": {"a b": {"type": "string"}}}
    )
    bad_schema = mcp_types.Tool(
        name="broken", input_schema={"type": "object", "properties": {"a": {"type": 5}}}
    )
    toolset = Toolset()
    client = McpClient(tmp_path / "mcp.json", {}.get, toolset)

    client._install(connection, [good, bad_name, bad_schema])  # pyright: ignore[reportPrivateUsage]

    assert [t.name for t in toolset.deferred] == ["mcp__odd__fine"]
    assert "spaced: its parameter names 'a b'" in client.problems["odd"]
    assert "broken: its input schema is not valid JSON Schema" in client.problems["odd"]


def _http_server(app: Any) -> Any:
    """Serve an ASGI app on a free port for the length of an async block."""

    class Running:
        async def __aenter__(self) -> int:
            config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None)
            self.server = uvicorn.Server(config)
            self.task = asyncio.create_task(self.server.serve())
            while not self.server.started:
                await asyncio.sleep(0.01)
            return self.server.servers[0].sockets[0].getsockname()[1]

        async def __aexit__(self, *_: object) -> None:
            self.server.should_exit = True
            await self.task

    return Running()


def test_headers_helper_and_sse_as_in_claude_code(tmp_path: Path) -> None:
    sys.path.insert(0, str(SERVER.parent))
    from mcp_server import build  # noqa: PLC0415 - the test server module, by path

    seen: list[str] = []
    streamable = build().streamable_http_app()
    events = build().sse_app()

    async def recording(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            seen.append(dict(scope["headers"]).get(b"authorization", b"").decode())
        await streamable(scope, receive, send)

    helper = (
        f'"{sys.executable}" -c "import json; print(json.dumps({{\'Authorization\': '
        f"'Bearer from-helper'}}))\""
    )

    async def scenario() -> tuple[str, str]:
        async with _http_server(recording) as http_port, _http_server(events) as sse_port:
            file = _write(
                tmp_path,
                {
                    "web": {
                        "type": "http",
                        "url": f"http://127.0.0.1:{http_port}/mcp",
                        "headersHelper": helper,
                    },
                    "stream": {"type": "sse", "url": f"http://127.0.0.1:{sse_port}/sse"},
                },
            )
            toolset = Toolset()
            client = McpClient(file, {}.get, toolset)
            await client.start()
            over_http = await _call(toolset, "mcp__web__add_note", text="a")
            over_sse = await _call(toolset, "mcp__stream__add_note", text="b")
            await client.close()
            return over_http, over_sse

    assert _run(scenario) == ("added note 1", "added note 1")
    assert seen
    assert all(header == "Bearer from-helper" for header in seen)


def test_the_file_read_again_connects_new_servers_and_stops_gone_ones_only(tmp_path: Path) -> None:
    """Step 16s: the marketplace writes mcp.json while Atlas serves, and the client follows it."""
    file = _write(tmp_path, {"notes": _notes()})
    toolset = Toolset()

    async def scenario() -> tuple[set[str], set[str], set[str], dict[str, str]]:
        client = McpClient(file, {}.get, toolset)
        await client.start()
        first = {tool.name for tool in toolset.deferred}
        _write(tmp_path, {"notes": _notes(), "more": _notes()})
        await client.reload_file()
        both = {tool.name for tool in toolset.deferred}
        file.write_text("{ broken", encoding="utf-8")
        await client.reload_file()
        kept = {tool.name for tool in toolset.deferred}
        problems = dict(client.problems)
        _write(tmp_path, {"more": _notes()})
        await client.reload_file()
        after = {tool.name for tool in toolset.deferred}
        await client.close()
        assert kept == both, "a file that cannot be read changes nothing"
        return first, both, after, problems

    first, both, after, problems = _run(scenario)

    assert "mcp__notes__add_note" in first
    assert "mcp__more__add_note" not in first
    assert {"mcp__notes__add_note", "mcp__more__add_note"} <= both
    assert "mcp.json" in problems
    assert "mcp__notes__add_note" not in after
    assert "mcp__more__add_note" in after
