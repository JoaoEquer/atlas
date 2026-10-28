"""The MCP client: the user's servers, connected, their tools in the toolset.

MCP tools are deferred: found by tool search and passing the tool gate like any other (ROADMAP,
Ferramentas), unless the server's entry says `alwaysLoad: true`, or the server marks a tool with
`anthropic/alwaysLoad` and the entry leaves it be, as in Claude Code. A server's tools are its
source (`mcp:<server>`): they come and go together when it connects, reloads its list or leaves,
and the tool search is installed again after, through `on_change`.

What the model receives is held to what providers take, as Claude Code holds it: each tool
description and each server's instructions are cut at 2,048 characters; a schema with a combinator
at its root is flattened; and a tool whose schema no provider would take (an invalid JSON Schema,
a parameter name outside `[A-Za-z0-9_.-]{1,64}`) is left out and said, since one such schema makes
every request that carries it fail.

A server that cannot start (a missing secret, a command not found, no sign-in) is logged with the
fix and left out; the others still connect, and Atlas runs without it.

**A server that signs in with OAuth** (`"oauth"` in its entry, or one the user signed in to) gets
its bearer token on every request, renewed before it ends and once on a 401 (`atlas.mcp.oauth`).
One whose entry does not say so is found by its 401 when it fails to connect, as Claude Code finds
it, and is left out the same way.
The sign-in itself is the user's, in a terminal (`atlas mcp login`): without one the server is left
out saying so, and `signed_in` tries it again once the user signed in.

**The plugins' servers** (step 16j) come beside the file's, as one more MCP file whose servers are
named for their plugin (`atlas.plugins`): they connect when their plugin is turned on and leave
when it is turned off (`reload_plugins`), the file's untouched.

**What the app shows** (`status`): every declared server, connecting, connected with its tools or
left out with why, and whether it signs in, which the app sends to the terminal, since the sign-in
opens a browser on the machine of the home.
"""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import mcp_types
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from atlas.agent import Tool, Toolset
from atlas.mcp.errors import McpConfigError, McpError, McpSignInError
from atlas.mcp.model import (
    ServerConfig,
    ServerState,
    ServerStatus,
    Transport,
    cut_description,
    filled,
    model_schema,
    parse_config,
    secrets_needed,
    tool_name,
)
from atlas.mcp.oauth import SignedIn, SignIns, asks_sign_in, login_hint, read_sign_in
from atlas.mcp.store import McpConnection
from atlas.trust import untrusted

_logger = logging.getLogger("atlas.mcp")
_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


class McpClient:
    """Connects the servers of `config_file` and keeps their tools in `toolset`."""

    def __init__(
        self,
        config_file: Path,
        secret: Callable[[str], str | None],
        toolset: Toolset,
        on_change: Callable[[], None] | None = None,
        *,
        plugins: Callable[[], Mapping[str, Any]] = lambda: {},
        sign_ins: SignIns | None = None,
    ) -> None:
        """`plugins` gives the plugins' servers, as an MCP file's document, each time it is
        asked; `sign_ins` holds the OAuth sign-ins, by server name."""
        self._sign_ins = sign_ins
        self._file = config_file
        self._secret = secret
        self._toolset = toolset
        self._on_change = on_change
        self._plugins = plugins
        self._connections: dict[str, McpConnection] = {}
        #: The plugins' servers connected or tried, as they were declared then.
        self._plugin_servers: dict[str, ServerConfig] = {}
        #: The file's servers connected or tried, as they were declared then.
        self._file_declared: dict[str, ServerConfig] = {}
        #: Why a server, or some of its tools, were left out, by server name.
        self.problems: dict[str, str] = {}
        #: The tools each connected server gives, by server name.
        self._tool_counts: dict[str, int] = {}
        #: The servers that sign in with OAuth or that the user signed in to, as last tried.
        self._signs_in: set[str] = set()
        self._refreshes: set[asyncio.Task[None]] = set()

    @property
    def sources(self) -> dict[str, str]:
        """One line per connected server, for tool search's list of sources."""
        lines: dict[str, str] = {}
        for connection in self._connections.values():
            about = " ".join(cut_description(connection.instructions).split())
            left_out = self.problems.get(connection.server.name)
            lines[connection.server.source] = f"{about} ({left_out})" if left_out else about
        return lines

    @property
    def file_problem(self) -> str | None:
        """Why `mcp.json`, or the plugins' servers, could not be read; `None` when they were."""
        found = [self.problems[key] for key in ("mcp.json", "plugins") if key in self.problems]
        return " ".join(found) or None

    def status(self) -> list[ServerStatus]:
        """Every declared server, the file's then the plugins', as it stands now."""
        declared = [
            *((server, False) for server in self._file_declared.values()),
            *((server, True) for server in self._plugin_servers.values()),
        ]
        rows: list[ServerStatus] = []
        for server, plugin in declared:
            problem = self.problems.get(server.name)
            if server.name in self._connections:
                state = ServerState.CONNECTED
            elif problem is not None:
                state = ServerState.LEFT_OUT
            else:
                state = ServerState.CONNECTING
            rows.append(
                ServerStatus(
                    name=server.name,
                    transport=server.transport,
                    plugin=plugin,
                    state=state,
                    tools=self._tool_counts.get(server.name, 0),
                    problem=problem,
                    signs_in=server.transport is not Transport.STDIO
                    and (server.oauth or server.name in self._signs_in),
                )
            )
        return rows

    async def start(self) -> None:
        """Connect every declared server, the file's and the plugins', together. A server that
        fails is left out."""
        servers = self._file_servers(report=True)
        self._file_declared = {server.name: server for server in servers}
        plugins = self._declared_plugins()
        self._plugin_servers = {server.name: server for server in plugins}
        await asyncio.gather(*(self._connect(server) for server in (*servers, *plugins)))
        self._changed()

    async def reload_file(self) -> None:
        """The file changed (the marketplace wrote it, or the user): connect its new servers and
        stop those gone or declared otherwise now; the others stay connected. A file that cannot
        be read changes nothing, and is said."""
        try:
            text = self._file.read_text(encoding="utf-8") if self._file.exists() else ""
            declared = parse_config(text, str(self._file)) if text.strip() else []
        except (OSError, McpError) as error:
            self.problems["mcp.json"] = str(error)
            _logger.warning("%s", error)
            return
        self.problems.pop("mcp.json", None)
        wanted = {server.name: server for server in declared}
        gone = [name for name, server in self._file_declared.items() if wanted.get(name) != server]
        for name in gone:
            self._file_declared.pop(name, None)
            await self._disconnect(name)
        new = [server for name, server in wanted.items() if name not in self._file_declared]
        self._file_declared.update((server.name, server) for server in new)
        if gone or new:
            # A plugin's server of a name the file now holds stops first, and one of a name it
            # no longer holds connects.
            await self.reload_plugins()
        await asyncio.gather(*(self._connect(server) for server in new))
        if gone or new:
            self._changed()

    async def _disconnect(self, name: str) -> None:
        self.problems.pop(name, None)
        self._tool_counts.pop(name, None)
        self._signs_in.discard(name)
        connection = self._connections.pop(name, None)
        if connection is not None:
            self._toolset.replace(connection.server.source, [])
            await connection.close()

    def _file_servers(self, *, report: bool = False) -> list[ServerConfig]:
        if not self._file.exists():
            return []
        try:
            return parse_config(self._file.read_text(encoding="utf-8"), str(self._file))
        except McpError as error:
            if report:
                self.problems["mcp.json"] = str(error)
                _logger.warning("%s", error)
            return []

    def _declared_plugins(self) -> list[ServerConfig]:
        """The plugins' servers now; a name the user's file has too is the user's."""
        mine = {server.name for server in self._file_servers()}
        try:
            servers = parse_config(json.dumps(self._plugins()), "a plugin's MCP servers")
        except McpError as error:
            self.problems["plugins"] = str(error)
            _logger.warning("%s", error)
            return []
        self.problems.pop("plugins", None)
        return [server for server in servers if server.name not in mine]

    async def reload_plugins(self) -> None:
        """The plugins changed: connect the servers of those turned on, and stop those of the
        plugins turned off, or whose server is declared otherwise now."""
        wanted = {server.name: server for server in self._declared_plugins()}
        gone = [name for name, server in self._plugin_servers.items() if wanted.get(name) != server]
        for name in gone:
            self._plugin_servers.pop(name, None)
            await self._disconnect(name)
        new = [server for name, server in wanted.items() if name not in self._plugin_servers]
        self._plugin_servers.update((server.name, server) for server in new)
        await asyncio.gather(*(self._connect(server) for server in new))
        if gone or new:
            self._changed()

    async def _connect(self, declared: ServerConfig) -> None:
        if declared.ignored:
            _logger.warning(
                "The MCP server %s has fields this Atlas does not know, ignored: %s",
                declared.name,
                ", ".join(declared.ignored),
            )
        try:
            server = filled(declared, self._secret)
            auth = await self._signed_in(server)
        except McpSignInError as error:
            self._signs_in.add(declared.name)
            self._left_out(declared.name, str(error))
            return
        except McpError as error:
            self._left_out(declared.name, str(error))
            return
        if auth is not None:
            self._signs_in.add(server.name)
        connection = McpConnection(server, lambda: self._refresh_later(server.name), auth)
        try:
            await connection.open()
            tools = await connection.tools()
        except Exception as error:  # the MCP edge: a server is foreign code, logged
            await connection.close()
            if (
                auth is None
                and server.transport is not Transport.STDIO
                and await asks_sign_in(server.url, server.headers)
            ):
                self._signs_in.add(server.name)
                self._left_out(
                    server.name,
                    f"The MCP server {server.name} signs in with OAuth and Atlas is not signed in "
                    f"to it. {login_hint(server.name)}",
                )
                return
            self.problems[server.name] = str(error)
            _logger.warning("The MCP server %s was left out: %s", server.name, error, exc_info=True)
            return
        self._connections[server.name] = connection
        self._install(connection, tools)
        _logger.info("The MCP server %s connected with %d tools.", server.name, len(tools))

    async def _signed_in(self, server: ServerConfig) -> SignedIn | None:
        """The server's sign-in, renewed if it is about to end, when it signs in with OAuth or the
        user signed in to it. Raises `McpSignInError` when it needs one and has none."""
        if server.transport is Transport.STDIO:
            return None
        if self._sign_ins is None:
            if server.oauth:
                raise McpSignInError(
                    f"The MCP server {server.name} signs in with OAuth, and this Atlas keeps no "
                    "sign-ins."
                )
            return None
        if not server.oauth and read_sign_in(self._sign_ins, server.name, server.url) is None:
            return None
        auth = SignedIn(server.name, server.url, self._sign_ins)
        await auth.current()
        return auth

    async def signed_in(self) -> None:
        """The sign-ins changed (`atlas mcp login`): try again every server left out that signs in
        with OAuth or now has a sign-in. A connected server keeps its session; its next renewal
        reads the new sign-in."""
        if self._sign_ins is None:
            return
        servers = [*self._file_servers(), *self._plugin_servers.values()]
        to_retry: list[ServerConfig] = []
        for server in servers:
            if server.name in self._connections or server.transport is Transport.STDIO:
                continue
            try:
                kept = read_sign_in(self._sign_ins, server.name, filled(server, self._secret).url)
            except McpError:
                kept = None
            if server.oauth or kept is not None:
                to_retry.append(server)
        if not to_retry:
            return
        for server in to_retry:
            self.problems.pop(server.name, None)
        await asyncio.gather(*(self._connect(server) for server in to_retry))
        self._changed()

    def _left_out(self, name: str, why: str) -> None:
        self.problems[name] = why
        _logger.warning("%s", why)

    def _install(self, connection: McpConnection, tools: list[mcp_types.Tool]) -> None:
        made: list[Tool] = []
        refused: list[str] = []
        for tool in tools:
            try:
                made.append(_tool(connection, tool))
            except McpConfigError as error:
                refused.append(f"{tool.name}: {error}")
                _logger.warning(
                    "The tool %s of %s was left out: %s", tool.name, connection.server.name, error
                )
        name = connection.server.name
        self._tool_counts[name] = len(made)
        if refused:
            self.problems[name] = "left out, schemas no provider takes: " + "; ".join(refused)
        else:
            self.problems.pop(name, None)
        self._toolset.replace(connection.server.source, made)

    def _refresh_later(self, name: str) -> None:
        task = asyncio.create_task(self.refresh(name))
        self._refreshes.add(task)
        task.add_done_callback(self._refreshes.discard)

    async def refresh(self, name: str) -> None:
        """List a server's tools again, as when it says its list changed."""
        connection = self._connections.get(name)
        if connection is None:
            return
        try:
            tools = await connection.tools()
        except Exception as error:  # the MCP edge, logged
            _logger.warning(
                "The tools of %s could not be listed again: %s", name, error, exc_info=True
            )
            return
        self._install(connection, tools)
        self._changed()

    async def secret_stored(self, name: str) -> None:
        """A secret was just written: try again any server left out because it needed `name`
        (step 16d). A server that is already connected is not reconnected; one that still fails
        keeps its own problem."""
        servers = [*self._file_servers(), *self._plugin_servers.values()]
        to_retry = [
            server
            for server in servers
            if server.name not in self._connections and name in secrets_needed(server)
        ]
        if not to_retry:
            return
        for server in to_retry:
            self.problems.pop(server.name, None)
        await asyncio.gather(*(self._connect(server) for server in to_retry))
        self._changed()

    async def close(self) -> None:
        """Stop every server and take its tools out."""
        for connection in self._connections.values():
            self._toolset.replace(connection.server.source, [])
            await connection.close()
        self._connections.clear()
        self._tool_counts.clear()
        self._changed()

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()


def _tool(connection: McpConnection, tool: mcp_types.Tool) -> Tool:
    """The tool as Atlas offers it. Raises `McpConfigError` when no provider would take it."""
    raw: dict[str, Any] = dict(tool.input_schema) if tool.input_schema else {"type": "object"}
    declared = raw.get("$schema")
    if declared is None or declared == _DRAFT_2020_12:
        try:
            Draft202012Validator.check_schema(raw)
        except SchemaError as error:
            why = f"its input schema is not valid JSON Schema: {error.message}"
            raise McpConfigError(why) from error
    schema, note = model_schema(raw)

    async def run(arguments: dict[str, Any]) -> str:
        # Text from a server is outside text: the model reads it as data.
        return untrusted(connection.server.source, await connection.call(tool.name, arguments))

    annotations = tool.annotations
    meta = tool.meta or {}
    always = connection.server.always_load
    if always is None:
        always = meta.get("anthropic/alwaysLoad") is True
    read_only = annotations is not None and annotations.read_only_hint is True
    return Tool(
        name=tool_name(connection.server.name, tool.name),
        description=cut_description(note + (tool.description or tool.title or tool.name)),
        parameters=schema,
        run=run,
        read_only=read_only,
        deferred=not always,
        source=connection.server.source,
        needs_approval=not connection.server.trusted and not read_only,
    )
