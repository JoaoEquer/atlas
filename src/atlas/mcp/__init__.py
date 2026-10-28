"""MCP: the client for the user's MCP servers, whose tools are found by tool search."""

from atlas.mcp.errors import McpConfigError, McpError, McpServerError, McpSignInError
from atlas.mcp.model import (
    DESCRIPTION_CHARS,
    TOOL_NAME_CHARS,
    ServerConfig,
    ServerState,
    ServerStatus,
    Transport,
    filled,
    parse_config,
    secrets_needed,
    tool_name,
)
from atlas.mcp.oauth import SignedIn, SignIns, login_hint, read_sign_in, sign_in
from atlas.mcp.service import McpClient
from atlas.mcp.store import (
    CONNECT_SECONDS,
    NETWORK_IDLE_SECONDS,
    STDIO_IDLE_SECONDS,
    McpConnection,
    result_text,
)

__all__ = [
    "CONNECT_SECONDS",
    "DESCRIPTION_CHARS",
    "NETWORK_IDLE_SECONDS",
    "STDIO_IDLE_SECONDS",
    "TOOL_NAME_CHARS",
    "McpClient",
    "McpConfigError",
    "McpConnection",
    "McpError",
    "McpServerError",
    "McpSignInError",
    "ServerConfig",
    "ServerState",
    "ServerStatus",
    "SignIns",
    "SignedIn",
    "Transport",
    "filled",
    "login_hint",
    "parse_config",
    "read_sign_in",
    "result_text",
    "secrets_needed",
    "sign_in",
    "tool_name",
]
