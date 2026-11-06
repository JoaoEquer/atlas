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
- **Claude Code's plugin directory** (`anthropics/claude-plugins-official`), in the format
  Atlas's plugins already have, many by the service's own maker. Its catalog is read at its branch,
  at most once an hour, and searched here; a plugin installs from its repository at the commit the
  catalog pins, or, when it pins none, at the one its branch is on then, and arrives off, as every
  plugin. Atlas's marketplace marks the ones that do not work in Atlas (`outside.yaml`), which do
  not install. A card says what the directory says: its name, its display name when it gives one,
  its category, and the installs a branch of its repository counts, the most first. That count is
  not documented, so when it cannot be read a plugin shows no number; nothing is guessed. Where
  the catalog is silent, the plugin's own manifest (`.claude-plugin/plugin.json`, at the commit
  the catalog pins) says the rest: its display name, its author, its icon. Its picture is that
  icon, or else its publisher's GitHub avatar when the catalog says who that is, or else the
  avatar of the GitHub organization the author's name is, once GitHub says the organization goes
  by that name (a plugin by "Anthropic" must not show the person who holds that login). What a
  manifest says is kept, so a commit is read once; one that cannot be read only leaves its card
  as the catalog has it, and a plugin nothing identifies has no picture.
- **skills.sh**, whose search gives each skill's installs. Opening the tab is one search (the most
  installed for "skill"), and each search another; its API is not documented, so the last opening
  list read is kept, and stands in, said, when the search breaks. A skill installs from its GitHub
  repository at the commit its default branch is on then (GitHub's API says which, and where in the
  repository the skill's folder is), and Update shows once the branch moved.

An outside item is named `<source>:<its id>` (`mcp-registry:com.stripe/mcp`), never like an item
of Atlas's own repository; in the home it takes a short name (`stripe`). Pure but for `Sources`,
which reads through the store's `Fetch`.
"""

import json
import logging
import re
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlencode

from atlas.home import MarketplaceSettings, format_utc, replace_file
from atlas.marketplace.errors import MarketplaceError
from atlas.marketplace.model import (
    COMMIT,
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
#: What opening the tab searches skills.sh for, as it needs two characters at least, and the most
#: skills one search lists.
SKILLS_OPENING = "skill"
SKILLS_LIMIT = 60
#: How long a repository's newest commit, read from GitHub, is taken as still newest: GitHub
#: answers 60 questions an hour to a computer that does not sign in.
HEAD_STALE = timedelta(hours=1)
#: Where the last opening list of skills.sh is kept, in the marketplace's folder.
SKILLS_FILE = "skills-sh.json"
#: The catalog in Claude Code's plugin directory.
CLAUDE_PLUGINS_FILE = ".claude-plugin/marketplace.json"
#: Where the last catalog of Claude Code's plugin directory read is kept, and how long it stands.
PLUGINS_FILE = "claude-plugins.json"
PLUGINS_STALE = timedelta(hours=1)
#: Where the directory's repository counts its plugins' installs.
PLUGINS_INSTALLS_REF = "stats"
PLUGINS_INSTALLS_FILE = "stats/plugin-installs.json"
#: A plugin's own manifest, in its folder.
PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
#: Where what the manifests and GitHub said is kept, how long what is not pinned to a commit
#: stands, the most a manifest may weigh, how many are read at once, and how long a listing waits
#: for them, in seconds: the ones not read by then wait for the next read.
FACTS_FILE = "claude-plugins-facts.json"
FACTS_STALE = timedelta(days=1)
MANIFEST_BYTES = 256 * 1024
MANIFEST_READERS = 8
MANIFEST_WAIT = 10.0
#: Where in a plugin's folder a picture of its own may be.
_PICTURE = re.compile(r"(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+\.(?:png|svg|jpe?g|webp)")
#: What a manifest read at a commit is kept under: that commit never says otherwise.
_PINNED = re.compile(r"@[0-9a-f]{40}/")
_LOGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
#: How a company's legal form trails its name, which its GitHub account does not carry.
_LEGAL_FORM = re.compile(r",?\s+(?:inc|llc|ltd|gmbh)\.?$", re.IGNORECASE)
_GITHUB_URL = re.compile(
    r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"
)
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")

_REGISTRY_META = "io.modelcontextprotocol.registry/official"
#: A namespace verified by a GitHub account rather than a domain.
_GITHUB_NAMESPACE = "io.github."
#: Names that say nothing of the server, for which its namespace names it in the home.
_PLAIN = frozenset({"mcp", "server", "mcp-server", "mcpserver", "remote", "api"})
_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SECRET_NAME = re.compile(r"[^A-Z0-9_]")

#: A GitHub account's avatar: what says who published an outside item, not the item itself.
AVATAR = "https://github.com/{owner}.png?size=96"

_logger = logging.getLogger("atlas.marketplace")

# -- the MCP Registry -----------------------------------------------------------------------------


def outside_sources(
    settings: MarketplaceSettings, get: Fetch, folder: Path | None = None
) -> "Sources | None":
    """The outside sources the settings ask for, keeping what they must in `folder`; `None`
    when they are off."""
    if not settings.outside:
        return None
    return Sources(
        get,
        registry_url=settings.registry_url,
        skills_url=settings.skills_url,
        github_api_url=settings.github_api_url,
        plugins_raw_url=settings.raw_url,
        plugins_url=settings.raw_url.format(
            repo=settings.claude_plugins, ref="main", path=CLAUDE_PLUGINS_FILE
        ),
        plugins_repository=settings.claude_plugins,
        plugins_installs_url=settings.raw_url.format(
            repo=settings.claude_plugins, ref=PLUGINS_INSTALLS_REF, path=PLUGINS_INSTALLS_FILE
        ),
        folder=folder,
    )


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


def _avatar(owner: str) -> str:
    return AVATAR.format(owner=owner) if owner else ""


def publisher_picture(repo: str) -> str:
    """The avatar of who owns `repo`, for an item that brings no picture of its own."""
    return _avatar(repo.split("/", 1)[0])


def _registry_picture(server: Mapping[str, Any], namespace: str, repository: str) -> str:
    """The icon a server publishes, or the avatar of the GitHub account it names."""
    icons = server.get("icons")
    for icon in cast("list[object]", icons) if isinstance(icons, list) else []:
        source = (
            _text(cast("Mapping[str, Any]", icon).get("src")) if isinstance(icon, Mapping) else ""
        )
        if source.startswith("https://"):
            return source
    match = _GITHUB_URL.match(repository)
    if match is not None:
        return _avatar(match.group(1).split("/", 1)[0])
    return (
        _avatar(namespace.removeprefix(_GITHUB_NAMESPACE))
        if namespace.startswith(_GITHUB_NAMESPACE)
        else ""
    )


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
        image=_registry_picture(server, namespace, _text(repository.get("url"))),
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


def _matching(items: list[Item], query: str) -> list[Item]:
    """The items whose name, title, summary or author hold every word of `query`."""
    words = query.lower().split()
    return [
        item
        for item in items
        if all(
            word in f"{item.name} {item.title} {item.summary} {item.author}".lower()
            for word in words
        )
    ]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _cut(text: str, chars: int) -> str:
    return text if len(text) <= chars else text[: chars - 1].rstrip() + "…"


# -- skills.sh ------------------------------------------------------------------------------------


def skills_items(data: bytes) -> list[Item]:
    """The skills of one search of skills.sh, the most installed first. Raises
    `MarketplaceError` when it is not its answer."""
    try:
        document: object = json.loads(data)
    except ValueError as error:
        raise MarketplaceError(f"skills.sh answered something else: {error}") from error
    skills = (
        cast("Mapping[str, Any]", document).get("skills") if isinstance(document, Mapping) else None
    )
    if not isinstance(skills, list):
        raise MarketplaceError("skills.sh answered with no skills.")
    found: list[Item] = []
    for entry in cast("list[object]", skills):
        item = skills_item(entry)
        if item is not None:
            found.append(item)
    return sorted(found, key=lambda item: -(item.installs or 0))


def skills_item(entry: object) -> Item | None:
    """One skill of skills.sh's search as an item, its commit not read yet."""
    if not isinstance(entry, Mapping):
        return None
    fields = cast("Mapping[str, Any]", entry)
    repo, own = _text(fields.get("source")), _text(fields.get("skillId"))
    if not _REPOSITORY.fullmatch(repo) or not own:
        return None
    installs = fields.get("installs")
    target = own.lower()
    return Item(
        name=f"{Source.SKILLS_SH.value}:{repo}/{own}",
        kind=Kind.SKILL,
        title=_cut(_text(fields.get("name")) or own, TITLE_CHARS),
        summary=f"A skill of {repo}, on GitHub.",
        author=repo.split("/", 1)[0],
        license="",
        repo=repo,
        image=_avatar(repo.split("/", 1)[0]),
        source=Source.SKILLS_SH,
        target=target,
        installs=installs if isinstance(installs, int) else None,
        problem=None
        if NAME.fullmatch(target)
        else f"Its name, {own}, cannot be a skill's name here.",
    )


def skill_folder(tree: bytes, own: str) -> str | None:
    """Where in a repository's tree (GitHub's, read whole) the skill `own` is: the folder named
    after it that holds a `SKILL.md`, the shortest path first; the root when the repository is
    the skill. `None` when it holds none."""
    try:
        document: object = json.loads(tree)
    except ValueError:
        return None
    entries = (
        cast("Mapping[str, Any]", document).get("tree") if isinstance(document, Mapping) else None
    )
    paths = [
        _text(cast("Mapping[str, Any]", e).get("path"))
        for e in cast("list[object]", entries or [])
        if isinstance(e, Mapping)
    ]
    wanted = own.lower()
    found = sorted(
        (path.rsplit("/", 1)[0] for path in paths if path.endswith("/SKILL.md")),
        key=lambda folder: (folder.rsplit("/", 1)[-1].lower() != wanted, folder.count("/"), folder),
    )
    if found and found[0].rsplit("/", 1)[-1].lower() == wanted:
        return found[0]
    return "" if "SKILL.md" in paths else None


# -- Claude Code's plugin directory --------------------------------------------------------------


def plugins_items(data: bytes, repository: str) -> list[Item]:
    """The plugins of the directory's catalog, by name. Raises `MarketplaceError` when it is
    not one."""
    try:
        document: object = json.loads(data)
    except ValueError as error:
        raise MarketplaceError(f"The plugin directory answered something else: {error}") from error
    fields: Mapping[str, Any] = (
        cast("Mapping[str, Any]", document) if isinstance(document, Mapping) else {}
    )
    plugins = fields.get("plugins")
    if not isinstance(plugins, list):
        raise MarketplaceError("The plugin directory lists no plugins.")
    maker = _maker(fields)
    found = [plugins_item(entry, repository, maker) for entry in cast("list[object]", plugins)]
    return sorted((item for item in found if item is not None), key=lambda item: item.title.lower())


def _maker(catalog: Mapping[str, Any]) -> str:
    """Who the directory's catalog says owns it."""
    owner: object = catalog.get("owner")
    return _text(cast("Mapping[str, Any]", owner).get("name")) if isinstance(owner, Mapping) else ""


def plugins_installs(data: bytes, marketplace: str) -> dict[str, int]:
    """How many times the directory counts each plugin installed, by name, from its rows written
    `<plugin>@<marketplace>`; empty when what it answered is not that count."""
    try:
        document: object = json.loads(data)
    except ValueError:
        return {}
    fields: Mapping[str, Any] = (
        cast("Mapping[str, Any]", document) if isinstance(document, Mapping) else {}
    )
    rows = fields.get("plugins")
    found: dict[str, int] = {}
    own = f"@{marketplace}"
    for row in cast("list[object]", rows) if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        entry = cast("Mapping[str, Any]", row)
        plugin = _text(entry.get("plugin"))
        count = entry.get("unique_installs")
        if plugin.endswith(own) and type(count) is int and count >= 0:
            found[plugin.removesuffix(own)] = count
    return found


def _document(data: bytes) -> Mapping[str, Any]:
    """A JSON object as read; empty when `data` is not one."""
    try:
        document: object = json.loads(data)
    except ValueError:
        return {}
    return cast("Mapping[str, Any]", document) if isinstance(document, Mapping) else {}


def _marketplace_name(catalog: bytes) -> str:
    """The name the directory's catalog gives itself, which its install count is keyed by."""
    return _text(_document(catalog).get("name"))


def manifest_facts(data: bytes) -> dict[str, str]:
    """What a plugin's manifest says of it: `title`, `author` and `picture`, a path in its folder.
    All empty when it is not a manifest."""
    fields = _document(data)
    author: object = fields.get("author")
    named = (
        _text(cast("Mapping[str, Any]", author).get("name")) if isinstance(author, Mapping) else ""
    )
    picture = (_text(fields.get("icon")) or _text(fields.get("logo"))).removeprefix("./")
    safe = _PICTURE.fullmatch(picture) is not None and ".." not in picture.split("/")
    return {
        "title": _text(fields.get("displayName")),
        "author": named,
        "picture": picture if safe else "",
    }


def _company(author: str) -> str:
    return _LEGAL_FORM.sub("", author)


def organization_facts(data: bytes, author: str) -> dict[str, str]:
    """The `picture` of a GitHub account, when GitHub says it is an organization going by the
    author's name; a login alone says nothing of who holds it."""
    fields = _document(data)
    named, wanted = _text(fields.get("name")).lower(), _company(author).lower()
    same = named == wanted or named.startswith((f"{wanted} ", f"{wanted},"))
    organization = fields.get("type") == "Organization"
    return {"picture": _avatar(_text(fields.get("login"))) if organization and same else ""}


def _facts_key(item: Item) -> str:
    return f"{item.repo}@{item.sha or 'HEAD'}/{item.path}"


def _described(item: Item, facts: Mapping[str, str], raw_url: str) -> Item:
    """`item` with what its manifest adds: what the catalog says stands, but for the picture, where
    the plugin's own comes before its publisher's."""
    title = facts.get("title", "") if item.title == item.target else ""
    picture = facts.get("picture", "")
    path = f"{item.path}/{picture}".lstrip("/")
    return replace(
        item,
        title=title[:TITLE_CHARS] or item.title,
        author=item.author or facts.get("author", ""),
        image=raw_url.format(repo=item.repo, ref=item.sha or "HEAD", path=path)
        if picture
        else item.image,
    )


def _stale(kept: Mapping[str, str] | None, key: str, now: datetime) -> bool:
    """Whether what is kept under `key` should be read again: never at a pinned commit."""
    if kept is None:
        return True
    if _PINNED.search(key):
        return False
    try:
        return now - datetime.fromisoformat(kept.get("read_at", "")) >= FACTS_STALE
    except ValueError:
        return True


def plugins_item(entry: object, repository: str, maker: str = "") -> Item | None:
    """One plugin of the directory as an item: its repository, its folder in it, and the commit
    the catalog pins (none when it pins none)."""
    if not isinstance(entry, Mapping):
        return None
    fields = cast("Mapping[str, Any]", entry)
    name = _text(fields.get("name"))
    if not name:
        return None
    source = fields.get("source")
    repo, sha, path, problem = repository, "", "", None
    if isinstance(source, str):
        path = source.removeprefix("./").strip("/")
    elif isinstance(source, Mapping):
        source = cast("Mapping[str, Any]", source)
        match = _GITHUB_URL.match(_text(source.get("url")))
        if match is None:
            repo, problem = "", "Atlas installs plugins from GitHub only."
        else:
            repo = match.group(1)
        sha = _text(source.get("sha"))
        path = _text(source.get("path")).strip("/")
    else:
        return None
    if path and (".." in path.split("/") or not re.fullmatch(r"[A-Za-z0-9._/-]+", path)):
        problem = "Its folder is not one Atlas reads."
    if not NAME.fullmatch(name):
        problem = f"Its name, {name}, cannot be a plugin's name here."
    author: object = fields.get("author")
    homepage = _text(fields.get("homepage"))
    # A plugin in the directory's own folders is not by whoever owns the directory, so only a
    # repository of its own says whose it is when the catalog does not.
    owner = repo.split("/", 1)[0] if repo != repository else ""
    named = (
        _text(cast("Mapping[str, Any]", author).get("name")) if isinstance(author, Mapping) else ""
    )
    # There, the catalog naming the directory's own maker as the author is what says it is theirs.
    publisher = owner or (repository.split("/", 1)[0] if maker and named == maker else "")
    return Item(
        name=f"{Source.CLAUDE_PLUGINS.value}:{name}",
        kind=Kind.PLUGIN,
        title=(_text(fields.get("displayName")) or name)[:TITLE_CHARS],
        summary=_cut(" ".join(_text(fields.get("description")).split()), SUMMARY_CHARS),
        author=named or owner,
        license="",
        version=_text(fields.get("version")),
        image=_avatar(publisher),
        repo=repo,
        sha=sha if COMMIT.fullmatch(sha) else "",
        path=path,
        homepage=homepage if homepage.startswith("https://") else "",
        source=Source.CLAUDE_PLUGINS,
        target=name,
        problem=problem,
        category=_text(fields.get("category")),
    )


# -- reading them ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Found:
    """What the outside sources list for one search, and why one could not be read."""

    items: list[Item]
    problems: list[str]


class Sources:
    """The outside sources, read through `get` (the store's `Fetch`)."""

    def __init__(
        self,
        get: Fetch,
        *,
        registry_url: str,
        skills_url: str,
        github_api_url: str,
        plugins_raw_url: str = "",
        plugins_url: str = "",
        plugins_repository: str = "",
        plugins_installs_url: str = "",
        folder: Path | None = None,
    ) -> None:
        self._get = get
        self._registry = registry_url
        self._skills = skills_url
        self._github = github_api_url.rstrip("/")
        self._plugins_raw_url = plugins_raw_url
        self._plugins_url = plugins_url
        self._plugins_repository = plugins_repository
        self._plugins_installs_url = plugins_installs_url
        self._folder = folder
        #: The directory's catalog as last read, and when.
        self._plugins: tuple[list[Item], datetime] | None = None
        #: Each repository's newest commit as last read, and when.
        self._heads: dict[str, tuple[str, datetime]] = {}
        #: What each manifest and GitHub account said, read from the folder at first.
        self._facts: dict[str, dict[str, str]] | None = None

    def search(self, query: str, now: datetime) -> Found:
        """What the sources list for `query`; with none, what each lists first: the registry's
        servers updated lately, skills.sh's most installed."""
        problems: list[str] = []
        items: list[Item] = []
        try:
            items += registry_order(self._registry_read(query, now))
        except MarketplaceError as error:
            _logger.warning("The MCP Registry could not be read: %s", error)
            problems.append(f"The MCP Registry could not be read: {error}")
        if self._plugins_url:
            try:
                items += _matching(self._plugins_read(now), query)
            except MarketplaceError as error:
                _logger.warning("The plugin directory could not be read: %s", error)
                problems.append(f"Claude Code's plugin directory could not be read: {error}")
        try:
            items += self._skills_read(query, now)
        except MarketplaceError as error:
            _logger.warning("skills.sh could not be searched: %s", error)
            kept = None if query.strip() else self._kept_skills()
            if kept is None:
                problems.append(f"skills.sh could not be searched: {error}")
            else:
                items += kept[0]
                problems.append(
                    f"skills.sh could not be searched ({error}); its list read on {kept[1]} "
                    "stands in."
                )
        return Found(items, problems)

    def plugins(self) -> list[Item]:
        """Every plugin of Claude Code's directory, its catalog read now. Raises
        `MarketplaceError`."""
        self._plugins = None
        return self._plugins_read(None) if self._plugins_url else []

    def picture(self, name: str) -> str:
        """The picture of a plugin of the directory as last listed; empty for any other."""
        listed = self._plugins[0] if self._plugins is not None else []
        return next((item.image for item in listed if item.name == name), "")

    def revision(self, item: Item, now: datetime) -> str | None:
        """The commit an outside skill would install from now, read at most once an hour per
        repository; `None` when GitHub cannot say."""
        if item.source is Source.CLAUDE_PLUGINS and not item.sha and item.repo:
            try:
                return self._head(item.repo, now)
            except MarketplaceError:
                return None
        if item.source is not Source.SKILLS_SH:
            return None
        try:
            return self._head(item.repo, now)
        except MarketplaceError as error:
            _logger.info("The newest commit of %s could not be read: %s", item.repo, error)
            return None

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
        if source == Source.SKILLS_SH.value and own.count("/") >= 2:
            return self._skill(name, own)
        if source == Source.CLAUDE_PLUGINS.value and self._plugins_url:
            listed = next((i for i in self._plugins_read(None) if i.name == name), None)
            if listed is None or listed.sha or listed.problem is not None:
                return listed
            # Many plugins share a repository (the directory's own folders): its newest commit is
            # read once an hour, within GitHub's 60 questions an hour to a computer not signed in.
            return replace(listed, sha=self._head(listed.repo, datetime.now(UTC)))
        return None

    def _plugins_read(self, now: datetime | None) -> list[Item]:
        """The directory's catalog: as last read within the hour, read again, or kept from the
        last read well when it cannot be read."""
        if self._plugins is not None and (now is None or now - self._plugins[1] < PLUGINS_STALE):
            return self._plugins[0]
        try:
            data = self._get(self._plugins_url, SOURCE_BYTES)
            if data is None:
                raise MarketplaceError("The plugin directory's catalog is not there.")
            items = self._counted(plugins_items(data, self._plugins_repository), data)
        except MarketplaceError:
            kept = self._kept(PLUGINS_FILE)
            if kept is None:
                raise
            catalog = kept.encode("utf-8")
            items = self._counted(plugins_items(catalog, self._plugins_repository), catalog)
            return self._manifested(items, catalog, None)
        items = self._manifested(items, data, now or datetime.now(UTC))
        if self._folder is not None:
            self._folder.mkdir(parents=True, exist_ok=True)
            replace_file(self._folder / PLUGINS_FILE, data)
        if now is not None:
            self._plugins = (items, now)
        return items

    def _counted(self, items: list[Item], catalog: bytes) -> list[Item]:
        """The directory's plugins with the installs it counts, the most first, and as they came
        when that count cannot be read: it only ever adds to a card."""
        if not self._plugins_installs_url:
            return items
        try:
            data = self._get(self._plugins_installs_url, SOURCE_BYTES)
        except MarketplaceError as error:
            _logger.info("The plugin directory's installs could not be read: %s", error)
            return items
        counts = plugins_installs(data or b"", _marketplace_name(catalog))
        counted = [replace(item, installs=counts.get(item.target)) for item in items]
        return sorted(counted, key=lambda item: -(item.installs or 0))

    def _manifested(self, items: list[Item], catalog: bytes, now: datetime | None) -> list[Item]:
        """The directory's plugins with what their manifests and GitHub add, read when `now` is
        given and only as kept when not: it only ever adds to a card."""
        if not self._plugins_raw_url:
            return items
        facts = self._kept_facts()
        before = dict(facts)
        if now is not None:
            manifests = {
                _facts_key(item): self._plugins_raw_url.format(
                    repo=item.repo,
                    ref=item.sha or "HEAD",
                    path=f"{item.path}/{PLUGIN_MANIFEST}".lstrip("/"),
                )
                for item in items
                if item.repo
            }
            self._read_facts(facts, manifests, lambda _key, data: manifest_facts(data), now)
        described = [
            _described(item, facts.get(_facts_key(item), {}), self._plugins_raw_url)
            for item in items
        ]
        # As in the catalog, naming the directory's own maker is what says a plugin is theirs.
        maker = _maker(_document(catalog))
        directory = _avatar(self._plugins_repository.split("/", 1)[0])
        described = [
            replace(item, image=directory)
            if not item.image and maker and item.author == maker
            else item
            for item in described
        ]
        if now is not None:
            accounts = {
                f"@{item.author}": f"{self._github}/users/{login}"
                for item in described
                if not item.image
                and _LOGIN.fullmatch(login := re.sub(r"[^A-Za-z0-9-]", "", _company(item.author)))
            }
            self._read_facts(
                facts, accounts, lambda key, data: organization_facts(data, key[1:]), now
            )
        if facts != before and self._folder is not None:
            self._folder.mkdir(parents=True, exist_ok=True)
            replace_file(self._folder / FACTS_FILE, json.dumps(facts).encode("utf-8"))
        return [
            item
            if item.image
            else replace(item, image=facts.get(f"@{item.author}", {}).get("picture", ""))
            for item in described
        ]

    def _read_facts(
        self,
        facts: dict[str, dict[str, str]],
        wanted: Mapping[str, str],
        told: Callable[[str, bytes], dict[str, str]],
        now: datetime,
    ) -> None:
        """Reads into `facts` the `wanted` addresses it does not hold, a few at once. One that
        fails, or takes too long, is tried at the next read."""

        def read(key: str, url: str) -> dict[str, str]:
            data = self._get(url, MANIFEST_BYTES)
            return {} if data is None else told(key, data)

        missing = {key: url for key, url in wanted.items() if _stale(facts.get(key), key, now)}
        if not missing:
            return
        pool = ThreadPoolExecutor(MANIFEST_READERS)
        reads = {pool.submit(read, key, url): key for key, url in missing.items()}
        done = wait(reads, timeout=MANIFEST_WAIT).done
        pool.shutdown(wait=False, cancel_futures=True)
        for finished in done:
            try:
                facts[reads[finished]] = {**finished.result(), "read_at": format_utc(now)}
            except MarketplaceError as error:
                _logger.info("A plugin's facts could not be read: %s", error)

    def _kept_facts(self) -> dict[str, dict[str, str]]:
        if self._facts is None:
            kept = _document((self._kept(FACTS_FILE) or "").encode("utf-8"))
            self._facts = {
                key: {
                    name: _text(value) for name, value in cast("Mapping[str, object]", row).items()
                }
                for key, row in kept.items()
                if isinstance(row, Mapping)
            }
        return self._facts

    def _kept(self, file: str) -> str | None:
        if self._folder is None:
            return None
        try:
            return (self._folder / file).read_text(encoding="utf-8")
        except OSError:
            return None

    def _skill(self, name: str, own: str) -> Item | None:
        owner, repo_name, skill = own.split("/", 2)
        repo = f"{owner}/{repo_name}"
        listed = skills_item({"source": repo, "skillId": skill, "name": skill})
        if listed is None:
            return None
        sha = self._head(repo, None)
        tree = self._get(f"{self._github}/repos/{repo}/git/trees/{sha}?recursive=1", SOURCE_BYTES)
        if tree is None:
            raise MarketplaceError(f"GitHub has no {repo} at {sha}.")
        path = skill_folder(tree, skill)
        if path is None:
            raise MarketplaceError(f"{repo} holds no skill {skill} at its newest commit.")
        return replace(listed, name=name, sha=sha, path=path)

    def _head(self, repo: str, now: datetime | None) -> str:
        kept = self._heads.get(repo)
        if now is not None and kept is not None and now - kept[1] < HEAD_STALE:
            return kept[0]
        data = self._get(f"{self._github}/repos/{repo}/commits/HEAD", SOURCE_BYTES)
        if data is None:
            raise MarketplaceError(f"GitHub has no repository {repo}.")
        try:
            sha = _text(cast("Mapping[str, Any]", json.loads(data)).get("sha"))
        except (ValueError, AttributeError) as error:
            raise MarketplaceError(f"GitHub answered something else: {error}") from error
        if not COMMIT.fullmatch(sha):
            raise MarketplaceError(f"GitHub did not say the newest commit of {repo}.")
        if now is not None:
            self._heads[repo] = (sha, now)
        return sha

    def _skills_read(self, query: str, now: datetime) -> list[Item]:
        wanted = query.strip() if len(query.strip()) >= 2 else SKILLS_OPENING
        params = {"q": wanted, "limit": str(SKILLS_LIMIT)}
        data = self._get(f"{self._skills}?{urlencode(params)}", SOURCE_BYTES)
        if data is None:
            raise MarketplaceError("skills.sh's search is not there.")
        items = skills_items(data)
        if not query.strip() and self._folder is not None:
            self._folder.mkdir(parents=True, exist_ok=True)
            kept = {"read_at": format_utc(now), "data": data.decode("utf-8", "replace")}
            replace_file(self._folder / SKILLS_FILE, json.dumps(kept).encode("utf-8"))
        return items

    def _kept_skills(self) -> tuple[list[Item], str] | None:
        if self._folder is None:
            return None
        try:
            kept = json.loads((self._folder / SKILLS_FILE).read_text(encoding="utf-8"))
            return skills_items(str(kept["data"]).encode("utf-8")), str(kept["read_at"])[:10]
        except (OSError, ValueError, KeyError, TypeError, MarketplaceError):
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
