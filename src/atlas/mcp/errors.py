"""What can go wrong with MCP servers. Every message says what went wrong and how to fix it."""


class McpError(Exception):
    """Base for every error of the `mcp` subject."""


class McpConfigError(McpError):
    """`mcp.json` is malformed, or a server needs a secret the vault does not have."""


class McpServerError(McpError):
    """A server could not start, or failed or went away while it was used."""


class McpSignInError(McpError):
    """A server that signs in with OAuth has no sign-in, or its authorization server refused one."""
