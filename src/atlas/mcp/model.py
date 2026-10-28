"""MCP servers as the user declares them, and the names their tools take. Pure: text in, data out.

**The file** is `ATLAS_HOME/mcp.json`, in Claude Code's `.mcp.json` format so a plugin's file
reads the same way (decided 2026-10-07, ROADMAP, Plugins): `{"mcpServers": {name: server}}`. A
server is stdio (`command`, `args`, `env`, `cwd`; an entry without `type`), `http` (or its alias
`streamable-http`) or `sse` (`url`, `headers`, `headersHelper`). Every kind takes Claude Code's
`timeout` (milliseconds) and `alwaysLoad`. As in Claude Code, an entry with a `url` but no `type`
is an error, since an entry without `type` is stdio. Fields Atlas does not know are ignored and
reported, so a plugin written for a newer Claude Code still loads. `oauth` is understood: such a
server signs in with OAuth (`atlas.mcp.oauth`), and its fields are not read. No server ships
connected (ROADMAP, decided 2026-10-07). Atlas adds `trust`: `"untrusted"` makes every call that
may write wait for the user's approval; a server is trusted unless it says so, and a value Atlas
does not know is untrusted, so a typo never turns the gate off (decided 2026-10-10). The file is
`user` in the home's boundary and travels in a backup (decided 09/10/2026).

**Secrets** are written `${NAME}` or `${NAME:-default}`, Claude Code's syntax, in `command`, `args`,
`env`, `url` and `headers`, and filled from the vault, never from the environment. A server whose
secret is missing and has no default is not started, and says which secret to set. Atlas V3 had its
own `secret://NAME`; one syntax, the one plugins already use, replaces it.

**Tool names** are Claude Code's, `mcp__<server>__<tool>`, so two servers can offer a tool by the
same name, kept within the 64 characters providers allow.
"""

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, cast

from atlas.mcp.errors import McpConfigError

#: The longest tool name providers take (OpenAI: ^[a-zA-Z0-9_-]{1,64}$).
TOOL_NAME_CHARS = 64
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)(?::-([^}]*))?\}")
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
_HTTP = frozenset({"http", "streamable-http", "streamable_http"})
_KNOWN = frozenset(
    {"type", "command", "args", "env", "cwd", "url", "headers", "headersHelper", "timeout",
     "alwaysLoad", "oauth", "trust"}
)  # fmt: skip
#: Claude Code ignores a `timeout` below a second.
_MIN_TIMEOUT_MS = 1_000


class Transport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


@dataclass(frozen=True, slots=True)
class ServerConfig:
    name: str
    transport: Transport
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    cwd: str | None = None
    url: str = ""
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    #: A command that prints a JSON object of headers, run when the server connects.
    headers_helper: str | None = None
    #: Seconds a tool call may wait for an answer; `None` for the transport's default.
    timeout: float | None = None
    #: `True` keeps the server's tools always on; `False` keeps them all deferred; `None` leaves
    #: each tool as its server marks it (`anthropic/alwaysLoad`), deferred otherwise.
    always_load: bool | None = None
    #: The server signs in with OAuth (`atlas.mcp.oauth`).
    oauth: bool = False
    #: `False` when the entry says `"trust": "untrusted"` (or anything but `"full"`): its tools that
    #: may write ask the user first.
    trusted: bool = True
    #: Fields this Atlas does not know, ignored.
    ignored: tuple[str, ...] = ()

    @property
    def source(self) -> str:
        return f"mcp:{self.name}"


def parse_config(text: str, where: str) -> list[ServerConfig]:
    """The servers in an `mcp.json`. Raises `McpConfigError` naming what is wrong."""
    try:
        document: object = json.loads(text) if text.strip() else {}
    except ValueError as error:
        raise McpConfigError(f"{where} is not valid JSON: {error}") from error
    if not isinstance(document, dict):
        raise McpConfigError(f"{where} must be a JSON object with mcpServers.")
    servers = cast("dict[str, Any]", document).get("mcpServers", {})
    if not isinstance(servers, dict):
        raise McpConfigError(f"{where}: mcpServers must be an object of servers by name.")
    return [
        _server(str(name), entry, where) for name, entry in cast("dict[str, Any]", servers).items()
    ]


def _server(name: str, entry: object, where: str) -> ServerConfig:
    at = f"{where}: server {name!r}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise McpConfigError(f"{at} needs a name of letters, digits, dots, dashes and underscores.")
    if not isinstance(entry, dict):
        raise McpConfigError(f"{at} must be an object.")
    fields = cast("dict[str, Any]", entry)
    timeout = _timeout(fields.get("timeout"), at)
    always = _optional_flag(fields.get("alwaysLoad"), f"{at} alwaysLoad")
    oauth = fields.get("oauth") is not None
    trust = fields.get("trust")
    trusted = trust is None or trust == "full"
    ignored = tuple(sorted(set(fields) - _KNOWN))
    kind = fields.get("type")
    if kind is None:
        if fields.get("url"):
            raise McpConfigError(
                f'{at} has a url but no type; add "type": "http" (an entry without type is stdio).'
            )
        kind = "stdio"
    kind = _text(kind, f"{at} type").lower()
    if kind in _HTTP or kind == "sse":
        url = _text(fields.get("url"), f"{at} url")
        if not url.startswith(("http://", "https://", "${")):
            raise McpConfigError(f"{at} needs an http(s) url.")
        helper = fields.get("headersHelper")
        return ServerConfig(
            name,
            Transport.SSE if kind == "sse" else Transport.HTTP,
            url=url,
            headers=_table(fields.get("headers", {}), f"{at} headers"),
            headers_helper=_text(helper, f"{at} headersHelper") if helper is not None else None,
            timeout=timeout,
            always_load=always,
            oauth=oauth,
            trusted=trusted,
            ignored=ignored,
        )
    if kind != "stdio":
        raise McpConfigError(f"{at} has type {kind!r}; Atlas takes stdio, http and sse.")
    if not fields.get("command"):
        raise McpConfigError(f"{at} needs a command.")
    args = fields.get("args", [])
    if not isinstance(args, list):
        raise McpConfigError(f"{at} args must be a list of texts.")
    cwd = fields.get("cwd")
    return ServerConfig(
        name,
        Transport.STDIO,
        command=_text(fields["command"], f"{at} command"),
        args=tuple(_text(arg, f"{at} args") for arg in cast("list[Any]", args)),
        env=_table(fields.get("env", {}), f"{at} env"),
        cwd=_text(cwd, f"{at} cwd") if cwd is not None else None,
        timeout=timeout,
        always_load=always,
        oauth=oauth,
        trusted=trusted,
        ignored=ignored,
    )


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise McpConfigError(f"{where} must be text.")
    return value


def _table(value: object, where: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise McpConfigError(f"{where} must be an object of texts.")
    return {
        str(key): _text(item, f"{where}.{key}")
        for key, item in cast("dict[str, Any]", value).items()
    }


def _timeout(value: object, at: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise McpConfigError(f"{at} timeout must be a number of milliseconds.")
    return None if value < _MIN_TIMEOUT_MS else value / 1000


def _optional_flag(value: object, where: str) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    raise McpConfigError(f"{where} must be true or false.")


def secrets_needed(server: ServerConfig) -> list[str]:
    """The `${NAME}` secrets a server's fields name without a default, in order."""
    found: dict[str, None] = {}
    for text in _texts(server):
        for match in _PLACEHOLDER.finditer(text):
            if match.group(2) is None:
                found[match.group(1)] = None
    return list(found)


def _texts(server: ServerConfig) -> list[str]:
    return [
        server.command,
        server.url,
        *server.args,
        *server.env.values(),
        *server.headers.values(),
    ]


def filled(server: ServerConfig, secret: Callable[[str], str | None]) -> ServerConfig:
    """The server with its `${NAME}` secrets filled. Raises `McpConfigError` naming the missing
    ones and how to set them."""
    missing = [name for name in secrets_needed(server) if not secret(name)]
    if missing:
        names = ", ".join(missing)
        raise McpConfigError(
            f"The MCP server {server.name} needs {names} in the vault. Ask the user for it with "
            f"the secrets tool (action=request, name={missing[0]}), or run "
            f"`atlas secrets set {missing[0]}`."
        )

    def fill(text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: secret(m.group(1)) or m.group(2) or "", text)

    return replace(
        server,
        command=fill(server.command),
        args=tuple(fill(arg) for arg in server.args),
        env={key: fill(value) for key, value in server.env.items()},
        url=fill(server.url),
        headers={key: fill(value) for key, value in server.headers.items()},
    )


def tool_name(server: str, tool: str) -> str:
    """`mcp__<server>__<tool>`, with what providers refuse replaced, within 64 characters."""
    name = _UNSAFE.sub("_", f"mcp__{server}__{tool}")
    if len(name) <= TOOL_NAME_CHARS:
        return name
    # A stable digest: the history keeps tool names, so a name must survive a restart.
    digest = hashlib.sha256(name.encode()).hexdigest()[:6]
    return f"{name[: TOOL_NAME_CHARS - 7]}_{digest}"


# -- what the model receives of a server's tools -------------------------------------------------

#: Claude Code cuts each tool description and each server's instructions here.
DESCRIPTION_CHARS = 2_048
#: What Claude's API (and Claude Code before sending) takes as a top-level property name.
_PROPERTY = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_COMBINATORS = ("anyOf", "oneOf", "allOf")


def cut_description(text: str) -> str:
    """A tool description or a server's instructions, within Claude Code's 2,048 characters."""
    return text if len(text) <= DESCRIPTION_CHARS else text[: DESCRIPTION_CHARS - 1] + "…"


def model_schema(schema: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """A tool's input schema as providers take it, and a sentence for its description when the
    shape changed. Raises `McpConfigError` with the reason when no provider would take it.

    A schema with `anyOf`, `oneOf` or `allOf` at its root is flattened
    into one object, every branch's properties merged, and the description says which go together;
    a top-level property name outside `[A-Za-z0-9_.-]{1,64}` leaves the tool out, since one bad
    schema makes a provider refuse every request that carries it.
    """
    root: dict[str, Any] = dict(schema) if schema else {"type": "object"}
    note = ""
    combinator = next((key for key in _COMBINATORS if key in root), None)
    if combinator is not None:
        branches = [
            cast("dict[str, Any]", branch)
            for branch in cast("list[Any]", root.pop(combinator) or [])
            if isinstance(branch, dict)
        ]
        properties: dict[str, Any] = dict(cast("dict[str, Any]", root.get("properties") or {}))
        groups: list[str] = []
        required: list[str] = list(cast("list[str]", root.get("required") or []))
        for branch in branches:
            properties.update(cast("dict[str, Any]", branch.get("properties") or {}))
            needed = [str(name) for name in cast("list[Any]", branch.get("required") or [])]
            if combinator == "allOf":
                required.extend(needed)
            elif needed:
                groups.append(", ".join(needed))
        root = {**root, "type": "object", "properties": properties}
        if required:
            root["required"] = sorted(set(required))
        else:
            root.pop("required", None)
        if groups:
            kind = "exactly one" if combinator == "oneOf" else "at least one"
            note = (
                f"Pass {kind} of these groups of parameters: "
                + "; ".join(f"({group})" for group in groups)
                + ". "
            )
    if root.get("type") != "object":
        raise McpConfigError("its input schema is not an object.")
    declared: object = root.get("properties", {})
    if not isinstance(declared, dict):
        raise McpConfigError("its input schema's properties are not an object.")
    bad = [str(name) for name in cast("dict[Any, Any]", declared) if not _PROPERTY.match(str(name))]
    if bad:
        raise McpConfigError(
            f"its parameter names {', '.join(repr(b) for b in bad)} are not 1 to 64 letters, "
            "digits, '_', '.' or '-'."
        )
    return root, note
