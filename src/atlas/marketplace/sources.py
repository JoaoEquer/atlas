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
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
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
_REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")

_REGISTRY_META = "io.modelcontextprotocol.registry/official"
#: A namespace verified by a GitHub account rather than a domain.
_GITHUB_NAMESPACE = "io.github."
#: Names that say nothing of the server, for which its namespace names it in the home.
_PLAIN = frozenset({"mcp", "server", "mcp-server", "mcpserver", "remote", "api"})
_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SECRET_NAME = re.compile(r"[^A-Z0-9_]")

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
        folder: Path | None = None,
    ) -> None:
        self._get = get
        self._registry = registry_url
        self._skills = skills_url
        self._github = github_api_url.rstrip("/")
        self._folder = folder
        #: Each repository's newest commit as last read, and when.
        self._heads: dict[str, tuple[str, datetime]] = {}

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

    def revision(self, item: Item, now: datetime) -> str | None:
        """The commit an outside skill would install from now, read at most once an hour per
        repository; `None` when GitHub cannot say."""
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
