"""The marketplace's items as its repository writes them, and the rules each must pass. Pure: text
in, data out.

**The repository** (step 16s) holds `marketplace.yaml`, saying its format; one file per item,
`items/<name>.yaml`; and `removed.yaml`, the items retired for safety. An item enters by a pull
request the maintainer reviews, and what Atlas installs is exactly what was reviewed:

- **Four kinds:** a `skill`, one folder with a `SKILL.md`; an `mcp` server, an `mcp.json` entry; a
  `plugin`, a folder Atlas loads as a plugin; and an `executor`, a plugin that registers one.
- **The code is pinned.** A skill, a plugin or an executor names a GitHub repository, the folder in
  it, and a whole commit: a new version is a new pull request moving the commit, and the reviewer
  reads what changed between the two. An MCP server run from a package names its exact version,
  which the registries never let change (`npx` and `uvx`), or its image's digest (`docker`); a
  remote one, its `https` address.
- **Nothing runs to describe an item:** its card and its page come from its file, its README and its
  pictures, and a picture is a file of the item's own repository at its commit, so the page shows
  what was reviewed too.
- **A retired item** is refused, and stops where it was installed.

One file per item, rather than one list, so two pull requests open at once never change the same
line.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast

import yaml

from atlas.marketplace.errors import MarketplaceError

#: The format of the repository this Atlas reads; a marketplace of a newer one is refused whole.
FORMAT = 1
META_FILE = "marketplace.yaml"
ITEMS_FOLDER = "items"
REMOVED_FILE = "removed.yaml"
#: What the marketplace's nightly check found of the outside items that do not work in Atlas
#: (step 24): a list of tables with a name and a reason.
OUTSIDE_FILE = "outside.yaml"

#: An item's name: what the user types and the name it takes in the home, unique in the
#: marketplace: lowercase letters, digits and inner dashes, up to 64.
NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
#: Who wrote it: a GitHub account, never a person's name.
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
_FOLDER = re.compile(r"^[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
_LICENSE = re.compile(r"^[A-Za-z0-9.+()-]+(?: (?:AND|OR|WITH) [A-Za-z0-9.+()-]+)*$")
_SECRET = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.-]*)(?::-[^}]*)?\}")
#: A package at one exact version: npm's `name@1.2.3`, PyPI's `name==1.2.3` or `name@1.2.3`, and
#: an image by its digest.
_NPM_PINNED = re.compile(
    r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*@\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$"
)
_PYPI_PINNED = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9,._-]+\])?(?:==|@)\d[0-9A-Za-z.+!-]*$"
)
_IMAGE_PINNED = re.compile(r"@sha256:[0-9a-f]{64}$")
_REMOTE = frozenset({"http", "sse", "streamable-http", "streamable_http"})
#: The fields of an `mcp.json` entry an item may hold. `oauth` waits for Atlas to sign in to
#: servers; `headersHelper` and `cwd` would run or reach outside what was reviewed.
_SERVER_FIELDS = frozenset({"type", "command", "args", "env", "url", "headers", "timeout"})
#: Where an item's pictures may come from: its own repository, at its commit.
PICTURES = "https://raw.githubusercontent.com/{repo}/{sha}/"

TITLE_CHARS = 60
SUMMARY_CHARS = 160
SCREENSHOTS = 6


class Kind(StrEnum):
    SKILL = "skill"
    MCP = "mcp"
    PLUGIN = "plugin"
    EXECUTOR = "executor"

    @property
    def has_code(self) -> bool:
        """Its files come from a repository at a commit; an MCP server's entry is its whole."""
        return self is not Kind.MCP


class Tier(StrEnum):
    #: Made by the Atlas team; only the maintainer marks an item so.
    OFFICIAL = "official"
    COMMUNITY = "community"


class Source(StrEnum):
    """Where an item comes from (step 24): Atlas's own repository, or a public catalog Atlas reads
    directly, with no review and no copy."""

    ATLAS = "atlas"
    MCP_REGISTRY = "mcp-registry"
    CLAUDE_PLUGINS = "claude-plugins"
    SKILLS_SH = "skills.sh"


class State(StrEnum):
    """Where an item stands in this home."""

    AVAILABLE = "available"
    #: A skill or an MCP server in the home.
    INSTALLED = "installed"
    #: A plugin or an executor in the home, turned off, or on.
    OFF = "off"
    ON = "on"
    #: Retired for safety after it was installed: it was stopped.
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class Secret:
    """A secret an MCP server reads, as the marketplace describes it to the user."""

    name: str
    description: str = ""
    url: str = ""


@dataclass(frozen=True, slots=True)
class Item:
    """One item of the marketplace, as its file says."""

    name: str
    kind: Kind
    title: str
    summary: str
    author: str
    license: str
    tier: Tier = Tier.COMMUNITY
    #: What the author calls this version; the commit is what installs.
    version: str = ""
    repo: str = ""
    sha: str = ""
    #: The item's folder in the repository; empty for its root.
    path: str = ""
    image: str = ""
    screenshots: tuple[str, ...] = ()
    homepage: str = ""
    #: An MCP server's `mcp.json` entry.
    server: Mapping[str, Any] = field(default_factory=dict[str, Any])
    secrets: tuple[Secret, ...] = ()
    #: Where it comes from (step 24); an outside item's name is `<source>:<its id>`.
    source: Source = Source.ATLAS
    #: The name it takes in the home, when not its own (an outside item's short name).
    target: str = ""
    #: When its source last published it, `YYYY-MM-DD`; empty when the source does not say.
    updated: str = ""
    #: The source counts its installs (skills.sh, Claude Code's plugin directory); `None` when it
    #: does not.
    installs: int | None = None
    #: The category its source files it under, as the source writes it; empty when it says none.
    category: str = ""
    #: An MCP Registry server whose name a domain verifies: the vendor's own.
    vendor: bool = False
    #: Why its source's entry cannot install here, said on its card.
    problem: str | None = None

    @property
    def home_name(self) -> str:
        """The name it takes in the home: a skill's folder, a server's `mcp.json` entry."""
        return self.target or self.name

    @property
    def source_url(self) -> str:
        """Where its code is read on GitHub, at its commit; an MCP server's home page."""
        if not self.kind.has_code:
            return self.homepage
        tree = f"https://github.com/{self.repo}/tree/{self.sha}"
        return f"{tree}/{self.path}" if self.path else tree

    @property
    def revision(self) -> str:
        """What changes when a new version is merged: the commit, or an MCP server's entry."""
        if self.kind.has_code:
            return self.sha
        text = json.dumps(
            {"server": self.server, "secrets": [s.name for s in self.secrets]}, sort_keys=True
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


@dataclass(frozen=True, slots=True)
class Retired:
    """An item pulled from the marketplace for safety: refused, and stopped where installed."""

    name: str
    reason: str
    repo: str = ""
    date: str = ""


@dataclass(frozen=True, slots=True)
class Index:
    """The marketplace as one read of its repository."""

    name: str
    description: str
    items: Mapping[str, Item]
    retired: Mapping[str, Retired]
    #: Items that could not be read, a line each; the others are listed anyway.
    problems: tuple[str, ...] = ()
    #: The outside items that do not work in Atlas, by name, with why (`outside.yaml`).
    incompatible: Mapping[str, str] = field(default_factory=dict[str, str])

    def retired_for(self, item: Item) -> Retired | None:
        """Whether `item` is retired, by its name or its repository."""
        found = self.retired.get(item.name)
        if found is not None:
            return found
        if item.repo:
            for entry in self.retired.values():
                if entry.repo and entry.repo.lower() == item.repo.lower():
                    return entry
        return None


def load_yaml(text: str, where: str) -> object:
    """A YAML document; raises `MarketplaceError` naming `where`."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise MarketplaceError(f"{where} is not valid YAML: {error}") from error


def parse_meta(text: str) -> tuple[str, str]:
    """The marketplace's name and description, from `marketplace.yaml`. Raises
    `MarketplaceError` for a file that is none, or of a format newer than this Atlas reads."""
    data = load_yaml(text, META_FILE)
    if not isinstance(data, Mapping):
        raise MarketplaceError(f"{META_FILE} is not a table.")
    fields = cast("Mapping[str, object]", data)
    found = fields.get("format")
    if not isinstance(found, int) or isinstance(found, bool):
        raise MarketplaceError(f"{META_FILE} says no format.")
    if found > FORMAT:
        raise MarketplaceError(
            f"The marketplace is written in format {found}, newer than this Atlas reads "
            f"({FORMAT}). Update Atlas with `atlas update`."
        )
    return _text(fields.get("name")), _text(fields.get("description"))


def parse_item(text: str, file_name: str) -> Item:
    """An item from its file, `items/<name>.yaml`. Raises `MarketplaceError` with every problem
    found, a line each."""
    data = load_yaml(text, file_name)
    if not isinstance(data, Mapping):
        raise MarketplaceError(f"{file_name} is not a table.")
    fields = cast("Mapping[str, object]", data)
    problems: list[str] = []
    name = _text(fields.get("name"))
    if not NAME.fullmatch(name):
        problems.append(
            "name must be lowercase letters, digits and inner dashes, up to 64 characters."
        )
    elif file_name.removesuffix(".yaml").rsplit("/", 1)[-1] != name:
        problems.append(f"the file must be named after the item: {ITEMS_FOLDER}/{name}.yaml.")
    try:
        kind = Kind(_text(fields.get("kind")))
    except ValueError:
        kind = Kind.SKILL
        problems.append(f"kind must be one of {', '.join(k.value for k in Kind)}.")
    try:
        tier = Tier(_text(fields.get("tier")) or Tier.COMMUNITY)
    except ValueError:
        tier = Tier.COMMUNITY
        problems.append(f"tier must be one of {', '.join(t.value for t in Tier)}.")
    title = _text(fields.get("title"))
    summary = " ".join(_text(fields.get("summary")).split())
    author = _text(fields.get("author"))
    license_ = _text(fields.get("license"))
    if not title or len(title) > TITLE_CHARS:
        problems.append(f"title must be given, in at most {TITLE_CHARS} characters.")
    if not summary or len(summary) > SUMMARY_CHARS:
        problems.append(f"summary must be one sentence of at most {SUMMARY_CHARS} characters.")
    if not _ACCOUNT.fullmatch(author):
        problems.append("author must be the GitHub account that made it.")
    if not _LICENSE.fullmatch(license_):
        problems.append("license must name its license, as an SPDX identifier such as MIT.")
    repo, sha, path = (_text(fields.get(key)) for key in ("repo", "sha", "path"))
    path = path.strip("/")
    image = _text(fields.get("image"))
    screenshots = tuple(_texts(fields.get("screenshots")))
    homepage = _text(fields.get("homepage"))
    server = fields.get("server")
    secrets = _secrets(fields.get("secrets"), problems)
    if kind.has_code:
        if not REPOSITORY.fullmatch(repo):
            problems.append("repo must be a GitHub repository, owner/name.")
        if not COMMIT.fullmatch(sha):
            problems.append("sha must be a whole commit, 40 lowercase hexadecimal characters.")
        if path and (not _FOLDER.fullmatch(path) or ".." in path.split("/")):
            problems.append("path must be a folder inside the repository.")
        if server is not None or secrets:
            problems.append(f"a {kind.value} brings no server nor secrets; an mcp item does.")
        server_entry: Mapping[str, Any] = {}
    else:
        if repo or sha or path:
            problems.append("an mcp item names no repo, sha or path: its server entry is its own.")
        if not isinstance(server, Mapping):
            problems.append("server must be the server's entry, as mcp.json writes it.")
            server_entry = {}
        else:
            server_entry = cast("Mapping[str, Any]", server)
            problems.extend(server_problems(server_entry, secrets))
    if homepage and not homepage.startswith("https://"):
        problems.append("homepage must be an https address.")
    pictures = PICTURES.format(repo=repo, sha=sha)
    for picture in (image, *screenshots) if image else screenshots:
        if not kind.has_code or not picture.startswith(pictures):
            problems.append(f"{picture} must be a file of the item's own repository: {pictures}...")
    if len(screenshots) > SCREENSHOTS:
        problems.append(f"at most {SCREENSHOTS} screenshots.")
    if problems:
        raise MarketplaceError("\n".join(f"{file_name}: {problem}" for problem in problems))
    return Item(
        name=name,
        kind=kind,
        title=title,
        summary=summary,
        author=author,
        license=license_,
        tier=tier,
        version=_text(fields.get("version")),
        repo=repo,
        sha=sha,
        path=path,
        image=image,
        screenshots=screenshots,
        homepage=homepage,
        server=server_entry,
        secrets=secrets,
    )


def server_problems(server: Mapping[str, Any], secrets: Iterable[Secret]) -> list[str]:
    """What keeps an MCP server's entry out of the marketplace: what it would run is not pinned,
    or it reads a secret it does not declare."""
    problems: list[str] = []
    unknown = sorted(set(server) - _SERVER_FIELDS)
    if unknown:
        problems.append(f"the server entry may not hold {', '.join(unknown)}.")
    kind = str(server.get("type") or "stdio")
    if kind in _REMOTE:
        url = server.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            problems.append("a remote server's url must be an https address.")
    elif kind == "stdio":
        command = server.get("command")
        raw_args = server.get("args", [])
        args = (
            [str(arg) for arg in cast("list[object]", raw_args)]
            if isinstance(raw_args, list)
            else []
        )
        if not isinstance(raw_args, list):
            problems.append("args must be a list.")
        problem = _pin_problem(command, args)
        if problem is not None:
            problems.append(problem)
    else:
        problems.append(f"the server's type must be stdio, http or sse, not {kind}.")
    used = set(placeholders(server))
    declared = {secret.name for secret in secrets}
    for name in sorted(used - declared):
        problems.append(f"the server reads ${{{name}}}, which secrets does not declare.")
    for name in sorted(declared - used):
        problems.append(f"secrets declares {name}, which the server never reads.")
    return problems


def _pin_problem(command: object, args: list[str]) -> str | None:
    values = [arg for arg in args if not arg.startswith("-")]
    if command == "npx":
        if not values or not _NPM_PINNED.fullmatch(values[0]):
            return "npx must run a package at an exact version, such as name@1.2.3."
        return None
    if command == "uvx":
        chosen = args[args.index("--from") + 1] if "--from" in args[:-1] else None
        package = chosen or (values[0] if values else "")
        if not _PYPI_PINNED.fullmatch(package):
            return "uvx must run a package at an exact version, such as name==1.2.3."
        return None
    if command == "docker":
        if not args or args[0] != "run" or not any(_IMAGE_PINNED.search(a) for a in args):
            return "docker must run an image by its digest: docker run ... name@sha256:..."
        return None
    return "the server must run through npx, uvx or docker, or be a remote https address."


def placeholders(value: object) -> list[str]:
    """The secrets a value names, `${NAME}`, in order, through lists and tables."""
    found: list[str] = []
    if isinstance(value, str):
        found.extend(match.group(1) for match in _PLACEHOLDER.finditer(value))
    elif isinstance(value, Mapping):
        for inner in cast("Mapping[object, object]", value).values():
            found.extend(placeholders(inner))
    elif isinstance(value, list):
        for inner in cast("list[object]", value):
            found.extend(placeholders(inner))
    return list(dict.fromkeys(found))


#: What a compiled file begins with, which no reviewer can read: ELF, PE, Mach-O (both byte
#: orders, and the universal one, also a Java class's), and WebAssembly.
_COMPILED_HEADS = (
    b"\x7fELF",
    b"MZ",
    b"\xfe\xed\xfa\xce",
    b"\xfe\xed\xfa\xcf",
    b"\xce\xfa\xed\xfe",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\x00asm",
)
_COMPILED_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".class"})
#: What makes a folder a plugin rather than a skill.
PLUGIN_MARKS = (".claude-plugin", ".mcp.json", "hooks", "plugin.json")


def compiled(name: str, head: bytes) -> bool:
    """Whether a file is compiled code, by its name or its first bytes."""
    suffix = name[name.rfind(".") :].lower() if "." in name else ""
    return suffix in _COMPILED_SUFFIXES or head.startswith(_COMPILED_HEADS)


def parse_removed(text: str) -> dict[str, Retired]:
    """The retired items, by name, from `removed.yaml`: a list of tables with a name and a
    reason. Raises `MarketplaceError`."""
    data = load_yaml(text, REMOVED_FILE)
    if data is None:
        return {}
    if not isinstance(data, list):
        raise MarketplaceError(f"{REMOVED_FILE} is not a list.")
    retired: dict[str, Retired] = {}
    for entry in cast("list[object]", data):
        if not isinstance(entry, Mapping):
            raise MarketplaceError(f"{REMOVED_FILE} holds an entry that is not a table.")
        fields = cast("Mapping[str, object]", entry)
        name, reason = _text(fields.get("name")), _text(fields.get("reason"))
        if not name or not reason:
            raise MarketplaceError(f"{REMOVED_FILE}: each entry needs a name and a reason.")
        retired[name] = Retired(name, reason, _text(fields.get("repo")), _text(fields.get("date")))
    return retired


def parse_outside(text: str) -> dict[str, str]:
    """The outside items that do not work in Atlas, by name, with why, from `outside.yaml`.
    Raises `MarketplaceError`."""
    data = load_yaml(text, OUTSIDE_FILE)
    if data is None:
        return {}
    if not isinstance(data, list):
        raise MarketplaceError(f"{OUTSIDE_FILE} is not a list.")
    found: dict[str, str] = {}
    for entry in cast("list[object]", data):
        empty: Mapping[str, object] = {}
        fields = cast("Mapping[str, object]", entry) if isinstance(entry, Mapping) else empty
        name, reason = _text(fields.get("name")), _text(fields.get("reason"))
        if not name or not reason:
            raise MarketplaceError(f"{OUTSIDE_FILE}: each entry needs a name and a reason.")
        found[name] = reason
    return found


def _secrets(value: object, problems: list[str]) -> tuple[Secret, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        problems.append("secrets must be a list of tables with a name.")
        return ()
    found: list[Secret] = []
    for entry in cast("list[object]", value):
        empty: Mapping[str, object] = {}
        fields = cast("Mapping[str, object]", entry) if isinstance(entry, Mapping) else empty
        name = _text(fields.get("name"))
        if not _SECRET.fullmatch(name):
            problems.append(f"secret {name or '?'} must be named in capitals, digits and _.")
            continue
        url = _text(fields.get("url"))
        if url and not url.startswith("https://"):
            problems.append(f"secret {name}'s url must be an https address.")
        found.append(Secret(name, _text(fields.get("description")), url))
    return tuple(found)


def _text(value: object) -> str:
    if value is None or isinstance(value, bool | Mapping | list):
        return ""
    return str(value).strip()


def _texts(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (_text(item) for item in cast("list[object]", value)) if text]
