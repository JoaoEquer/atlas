"""One connection to one MCP server, through the official SDK. The only module that imports it.

**One long-lived session per server**. Atlas V3 opened a
session per call (its LangChain adapter's default), and a stateful server lost its state between
calls: Chrome DevTools' `click` never found the snapshot `take_snapshot` had taken a call before.

From V3's session pool, kept because the SDK still needs them:

- **The session is entered and left in the same task**, a task of its own that holds it open:
  anyio's cancel scopes refuse to be left from another task.
- **A session that died is opened again on the next call**: the tools are found by name at call
  time, never held to a dead session.
- **A `python` command means Atlas's own interpreter**: with two Pythons on the PATH, V3's servers
  died with `ModuleNotFoundError` in the other one.

What V3 did by hand the SDK now does: it stops a server's whole process tree (a process group on
POSIX, a Job Object on Windows), and runs `npx` on Windows without going through cmd.exe.

**Waiting**, as Claude Code waits: a server has 30 seconds to start; a tool call waits as long as
the server's `timeout` says, or else until it has gone silent for 5 minutes (a network server) or
30 (a stdio one). Claude Code also moves a call past 2 minutes to the background; that is a job's
work here (step 12 of the roadmap).
"""

import asyncio
import contextlib
import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import httpx2
import mcp_types
from mcp import Client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError

from atlas.mcp.errors import McpServerError
from atlas.mcp.model import ServerConfig, Transport

#: Seconds a server has to start and answer its first listing.
CONNECT_SECONDS = 30.0
#: Seconds a tool call may wait without an answer, unless the server's `timeout` says otherwise
#: (Claude Code's CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT defaults).
NETWORK_IDLE_SECONDS = 300.0
STDIO_IDLE_SECONDS = 1_800.0
_PYTHON = frozenset({"python", "python3", "python.exe", "python3.exe"})
_logger = logging.getLogger("atlas.mcp")


class McpConnection:
    """A server's session, held open by a task of its own."""

    def __init__(
        self,
        server: ServerConfig,
        on_tools_changed: Callable[[], None] | None = None,
        auth: httpx2.Auth | None = None,
    ):
        """`auth` signs each request to a network server: its OAuth sign-in (`atlas.mcp.oauth`),
        which sends the bearer token and, on a 401, renews it and sends the request again once."""
        self.server = server
        self._auth = auth
        self._on_tools_changed = on_tools_changed
        self._client: Client | None = None
        self._holder: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._lock = asyncio.Lock()

    @property
    def call_seconds(self) -> float:
        if self.server.timeout is not None:
            return self.server.timeout
        stdio = self.server.transport is Transport.STDIO
        return STDIO_IDLE_SECONDS if stdio else NETWORK_IDLE_SECONDS

    @property
    def instructions(self) -> str:
        client = self._client
        return (client.instructions or "") if client is not None else ""

    async def open(self) -> None:
        """Start the server and wait until it answers. Raises `McpServerError`."""
        async with self._lock:
            if self._holder is not None and not self._holder.done():
                return
            ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._stop = asyncio.Event()
            self._holder = asyncio.create_task(self._hold(ready))
            try:
                await asyncio.wait_for(asyncio.shield(ready), CONNECT_SECONDS)
            except TimeoutError as error:
                await self.close()
                raise McpServerError(
                    f"The MCP server {self.server.name} did not answer in {CONNECT_SECONDS:.0f} "
                    "seconds."
                ) from error

    async def _hold(self, ready: asyncio.Future[None]) -> None:
        try:
            target = await self._target()
            async with Client(
                target, message_handler=self._message, read_timeout_seconds=self.call_seconds
            ) as client:
                self._client = client
                if not ready.done():
                    ready.set_result(None)
                await self._stop.wait()
        except Exception as error:  # the MCP edge: a server is foreign code, logged
            _logger.warning("The MCP server %s stopped: %s", self.server.name, error, exc_info=True)
            if not ready.done():
                ready.set_exception(
                    McpServerError(
                        f"The MCP server {self.server.name} could not start: "
                        f"{type(error).__name__}: {error}"
                    )
                )
        finally:
            self._client = None

    async def _target(self) -> Any:
        server = self.server
        if server.transport is not Transport.STDIO:
            headers = dict(server.headers)
            if server.headers_helper:
                headers |= await _helper_headers(server.name, server.headers_helper)
            if server.transport is Transport.SSE:
                return sse_client(
                    server.url, headers=headers, sse_read_timeout=self.call_seconds, auth=self._auth
                )
            http = httpx2.AsyncClient(headers=headers, timeout=CONNECT_SECONDS, auth=self._auth)
            return streamable_http_client(server.url, http_client=http)
        command = sys.executable if Path(server.command).name.lower() in _PYTHON else server.command
        return StdioServerParameters(
            command=command, args=list(server.args), env=dict(server.env) or None, cwd=server.cwd
        )

    async def _message(self, message: object) -> None:
        if isinstance(message, mcp_types.ToolListChangedNotification) and self._on_tools_changed:
            self._on_tools_changed()

    async def tools(self) -> list[mcp_types.Tool]:
        client = await self._connected()
        found: list[mcp_types.Tool] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor, cache_mode="refresh")
            found.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                return found

    async def call(self, tool: str, arguments: dict[str, Any]) -> str:
        client = await self._connected()
        try:
            result = await client.call_tool(tool, arguments, read_timeout_seconds=self.call_seconds)
        except MCPError as error:
            raise McpServerError(f"{self.server.name} refused {tool}: {error}") from error
        except TimeoutError as error:
            raise McpServerError(
                f"{self.server.name} did not answer {tool} in {self.call_seconds:.0f} seconds."
            ) from error
        return result_text(result)

    async def _connected(self) -> Client:
        if self._client is None or self._holder is None or self._holder.done():
            await self.open()
        if self._client is None:
            raise McpServerError(f"The MCP server {self.server.name} is not connected.")
        return self._client

    async def close(self) -> None:
        self._stop.set()
        holder = self._holder
        if holder is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(holder, CONNECT_SECONDS)


async def _helper_headers(server: str, command: str) -> dict[str, str]:
    """Claude Code's `headersHelper`: a command that prints a JSON object of headers."""
    process = await asyncio.create_subprocess_shell(
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), CONNECT_SECONDS)
    except TimeoutError as error:
        process.kill()
        raise McpServerError(f"The headersHelper of {server} did not finish.") from error
    try:
        found: object = json.loads(out.decode("utf-8", "replace") or "{}")
    except ValueError as error:
        raise McpServerError(
            f"The headersHelper of {server} did not print a JSON object: "
            f"{err.decode('utf-8', 'replace')[:300]}"
        ) from error
    if not isinstance(found, dict):
        raise McpServerError(f"The headersHelper of {server} did not print a JSON object.")
    return {str(key): str(value) for key, value in cast("dict[Any, Any]", found).items()}


def result_text(result: mcp_types.CallToolResult) -> str:
    """What the model reads of a tool's result: its text, and a note for what is not text."""
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, mcp_types.TextContent):
            parts.append(block.text)
        elif isinstance(block, mcp_types.EmbeddedResource):
            resource = block.resource
            if isinstance(resource, mcp_types.TextResourceContents):
                parts.append(resource.text)
            else:
                parts.append(f"[a binary resource: {resource.uri}]")
        elif isinstance(block, mcp_types.ResourceLink):
            parts.append(f"[a resource: {block.name} at {block.uri}]")
        elif isinstance(block, mcp_types.ImageContent):
            parts.append(f"[an image, {block.mime_type}]")
        else:  # audio, the last kind of block
            parts.append(f"[audio, {block.mime_type}]")
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, ensure_ascii=False))
    text = "\n".join(parts) or "(no output)"
    return f"[The tool reported an error]\n{text}" if result.is_error else text
