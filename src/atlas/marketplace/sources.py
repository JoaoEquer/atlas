"""The marketplace's outside sources (step 24): public catalogs Atlas reads directly, with no review
and no copy, beside its own repository.

- **The MCP Registry** (`registry.modelcontextprotocol.io`), the official one, made to be read by
  other catalogs. Each server's name is verified by its author: by a domain (`com.stripe/mcp`,
  the vendor's own) or by a GitHub account (`io.github.<user>/...`). A server becomes the same
  `mcp.json` entry an item of Atlas's own repository would be, and passes the same rules: a package
  at an exact version (`npx`, `uvx`), or a remote `https` address. One that does not is listed
  with why, and does not install. What a server asks for (an environment variable, a header, a
  part of its address) becomes a secret the vault holds; a remote one that signs in with OAuth
  gets the Sign in button as any other.

An outside item is named `<source>:<its id>` (`mcp-registry:com.stripe/mcp`), never like an item
of Atlas's own repository; in the home it takes a short name (`stripe`). Pure but for `Sources`,
which reads through the store's `Fetch`.
"""

import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import quote, urlencode

from atlas.home import MarketplaceSettings, format_utc
from atlas.marketplace.errors import MarketplaceError
from atlas.marketplace.model import (
    NAME,
    SUMMARY_CHARS,
    TITLE_CHARS,
    Item,
    Kind,
    Secret,
    Source,
    server_problems,
)
from atlas.marketplace.store import Fetch

#: The most servers one read of the registry lists, and how far back the listing with no search
#: looks for servers updated.
REGISTRY_LIMIT = 100
REGISTRY_RECENT = timedelta(days=30)
#: The most one read of a source may weigh.
SOURCE_BYTES = 8 * 1024 * 1024

_REGISTRY_META = "io.modelcontextprotocol.registry/official"
#: A namespace verified by a GitHub account rather than a domain.
_GITHUB_NAMESPACE = "io.github."
#: Names that say nothing of the server, for which its namespace names it in the home.
_PLAIN = frozenset({"mcp", "server", "mcp-server", "mcpserver", "remote", "api"})
_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SECRET_NAME = re.compile(r"[^A-Z0-9_]")

_logger = logging.getLogger("atlas.marketplace")

# -- the MCP Registry -----------------------------------------------------------------------------


def outside_sources(settings: MarketplaceSettings, get: Fetch) -> "Sources | None":
    """The outside sources the settings ask for; `None` when they are off."""
    return Sources(get, registry_url=settings.registry_url) if settings.outside else None


def registry_items(data: bytes) -> list[Item]:
    """The servers of one read of the registry, active and at their latest version. Raises
    `MarketplaceError` when it is not the registry's answer."""
    try:
        document: object = json.loads(data)
    except ValueError as error:
        raise MarketplaceError(f"The MCP Registry answered something else: {error}") from error
    if not isinstance(document, Mapping):
        raise MarketplaceError("The MCP Registry answered something else.")
    servers = cast("Mapping[str, Any]", document).get("servers")
    if not isinstance(servers, list):
        raise MarketplaceError("The MCP Registry answered with no servers.")
    found: list[Item] = []
    for entry in cast("list[object]", servers):
        item = registry_item(entry)
        if item is not None:
            found.append(item)
    return found


def registry_item(entry: object) -> Item | None:
    """One server of the registry as an item, or `None` when it is not active or not one."""
    if not isinstance(entry, Mapping):
        return None
    fields = cast("Mapping[str, Any]", entry)
    server = fields.get("server")
    meta: object = cast("Mapping[str, Any]", fields.get("_meta") or {}).get(_REGISTRY_META) or {}
    if not isinstance(server, Mapping) or not isinstance(meta, Mapping):
        return None
    server = cast("Mapping[str, Any]", server)
    meta = cast("Mapping[str, Any]", meta)
    if meta.get("status", "active") != "active":
        return None
    name = _text(server.get("name"))
    if "/" not in name:
        return None
    namespace = name.split("/", 1)[0]
    repository = cast("Mapping[str, Any]", server.get("repository") or {})
    homepage = _text(server.get("websiteUrl")) or _text(repository.get("url"))
    entry_, secrets, problem = _registry_entry(server)
    if problem is None:
        problems = server_problems(entry_, secrets)
        problem = problems[0] if problems else None
    title = _text(server.get("title")) or name.split("/", 1)[1]
    return Item(
        name=f"{Source.MCP_REGISTRY.value}:{name}",
        kind=Kind.MCP,
        title=title[:TITLE_CHARS],
        summary=_cut(" ".join(_text(server.get("description")).split()), SUMMARY_CHARS),
        author=_author(namespace),
        license="",
        version=_text(server.get("version")),
        homepage=homepage if homepage.startswith("https://") else "",
        server=entry_,
        secrets=secrets,
        source=Source.MCP_REGISTRY,
        target=home_name(name),
        updated=_text(meta.get("updatedAt") or meta.get("publishedAt"))[:10],
        vendor=not namespace.startswith(_GITHUB_NAMESPACE),
        problem=problem,
    )


def home_name(registry_name: str) -> str:
    """The short name a registry server takes in `mcp.json`: its own name after the namespace,
    or the namespace's last part when that says nothing (`com.stripe/mcp`: `stripe`)."""
    namespace, _, own = registry_name.lower().partition("/")
    slug = re.sub(r"[^a-z0-9]+", "-", own).strip("-")
    if not slug or slug in _PLAIN:
        slug = re.sub(r"[^a-z0-9]+", "-", namespace.rsplit(".", 1)[-1]).strip("-")
    slug = slug[:64].strip("-")
    return slug if NAME.fullmatch(slug) else "server"


def registry_order(items: list[Item]) -> list[Item]:
    """The vendors' own first, then the newest: the registry counts no installs."""
    newest = sorted(items, key=lambda item: item.updated, reverse=True)
    return sorted(newest, key=lambda item: not item.vendor)


def _registry_entry(
    server: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[Secret, ...], str | None]:
    """The `mcp.json` entry a server becomes, the secrets it reads, and why it cannot be one.
    A remote is preferred: nothing runs on the computer."""
    secrets: dict[str, Secret] = {}
    for remote in cast("list[object]", server.get("remotes") or []):
        if not isinstance(remote, Mapping):
            continue
        remote = cast("Mapping[str, Any]", remote)
        kind = _text(remote.get("type"))
        if kind not in ("streamable-http", "sse"):
            continue
        variables = cast("Mapping[str, Any]", remote.get("variables") or {})
        url = _fill(_text(remote.get("url")), variables, None, secrets)
        entry: dict[str, Any] = {"type": "http" if kind == "streamable-http" else "sse", "url": url}
        headers: dict[str, str] = {}
        for header in cast("list[object]", remote.get("headers") or []):
            if not isinstance(header, Mapping):
                continue
            header = cast("Mapping[str, Any]", header)
            value = _text(header.get("value"))
            if not header.get("isSecret") and not _VARIABLE.search(value) and value:
                headers[_text(header.get("name"))] = value
                continue
            if not header.get("isRequired") and not header.get("isSecret"):
                continue
            filled = _fill(value, None, header, secrets) or _secret(
                _text(header.get("name")), header, secrets
            )
            headers[_text(header.get("name"))] = filled
        if headers:
            entry["headers"] = headers
        return entry, tuple(secrets.values()), None
    for package in cast("list[object]", server.get("packages") or []):
        if not isinstance(package, Mapping):
            continue
        package = cast("Mapping[str, Any]", package)
        transport = cast("Mapping[str, Any]", package.get("transport") or {})
        if _text(transport.get("type")) not in ("", "stdio"):
            continue
        registry = _text(package.get("registryType"))
        identifier = _text(package.get("identifier"))
        version = _text(package.get("version"))
        if registry == "npm":
            entry = {"command": "npx", "args": ["-y", f"{identifier}@{version}"]}
        elif registry == "pypi":
            entry = {"command": "uvx", "args": [f"{identifier}=={version}"]}
        else:
            continue
        for argument in cast("list[object]", package.get("packageArguments") or []):
            if not isinstance(argument, Mapping):
                continue
            argument = cast("Mapping[str, Any]", argument)
            value = _text(argument.get("value") or argument.get("default"))
            if not value:
                if argument.get("isRequired"):
                    return {}, (), "It needs an argument Atlas cannot fill yet."
                continue
            if argument.get("type") == "named":
                entry["args"].append(_text(argument.get("name")))
            entry["args"].append(value)
        env: dict[str, str] = {}
        for variable in cast("list[object]", package.get("environmentVariables") or []):
            if not isinstance(variable, Mapping):
                continue
            variable = cast("Mapping[str, Any]", variable)
            name = _text(variable.get("name"))
            default = _text(variable.get("value") or variable.get("default"))
            if variable.get("isSecret") or (variable.get("isRequired") and not default):
                env[name] = _secret(name, variable, secrets)
            elif default:
                env[name] = default
        if env:
            entry["env"] = env
        return entry, tuple(secrets.values()), None
    return {}, (), "It runs in a way Atlas does not run from here (only npm, PyPI and https)."


def _fill(
    text: str,
    described: Mapping[str, Any] | None,
    fallback: object,
    secrets: dict[str, Secret],
) -> str:
    """`text` with each `{name}` it holds made a secret, described by `described[name]` or by
    `fallback`."""
    return _VARIABLE.sub(
        lambda match: _secret(
            match.group(1),
            described.get(match.group(1)) if described is not None else fallback,
            secrets,
        ),
        text,
    )


def _secret(name: str, described: object, secrets: dict[str, Secret]) -> str:
    """`${NAME}` for a value the server asks for, kept by the vault under that name."""
    key = _SECRET_NAME.sub("_", name.upper()).strip("_") or "SECRET"
    if not key[0].isalpha():
        key = f"S_{key}"
    fields: Mapping[str, Any] = (
        cast("Mapping[str, Any]", described) if isinstance(described, Mapping) else {}
    )
    secrets.setdefault(key, Secret(key[:128], _text(fields.get("description"))[:300]))
    return f"${{{key[:128]}}}"


def _author(namespace: str) -> str:
    """Who verified the name: the GitHub account, or the domain read the right way round."""
    if namespace.startswith(_GITHUB_NAMESPACE):
        return namespace.removeprefix(_GITHUB_NAMESPACE)
    return ".".join(reversed(namespace.split(".")))


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[: chars - 1].rstrip() + "…"


# -- reading them ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Found:
    """What the outside sources list for one search, and why one could not be read."""

    items: list[Item]
    problems: list[str]


class Sources:
    """The outside sources, read through `get` (the store's `Fetch`)."""

    def __init__(self, get: Fetch, *, registry_url: str) -> None:
        self._get = get
        self._registry = registry_url

    def search(self, query: str, now: datetime) -> Found:
        """What the sources list for `query`; with none, the registry's servers updated lately."""
        problems: list[str] = []
        items: list[Item] = []
        try:
            items += registry_order(self._registry_read(query, now))
        except MarketplaceError as error:
            _logger.warning("The MCP Registry could not be read: %s", error)
            problems.append(f"The MCP Registry could not be read: {error}")
        return Found(items, problems)

    def item(self, name: str) -> Item | None:
        """One outside item, read again now. Raises `MarketplaceError` when its source cannot be
        read."""
        source, _, own = name.partition(":")
        if source == Source.MCP_REGISTRY.value and own:
            data = self._get(
                f"{self._registry}/{quote(own, safe='')}/versions/latest", SOURCE_BYTES
            )
            if data is None:
                return None
            try:
                found = registry_item(json.loads(data))
            except ValueError as error:
                raise MarketplaceError(
                    f"The MCP Registry answered something else: {error}"
                ) from error
            return found if found is not None and found.name == name else None
        return None

    def _registry_read(self, query: str, now: datetime) -> list[Item]:
        params = {"version": "latest", "limit": str(REGISTRY_LIMIT)}
        if query.strip():
            params["search"] = query.strip()
        else:
            params["updated_since"] = format_utc(now - REGISTRY_RECENT)
        data = self._get(f"{self._registry}?{urlencode(params)}", SOURCE_BYTES)
        if data is None:
            raise MarketplaceError("The MCP Registry is not there.")
        return registry_items(data)
