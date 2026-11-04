"""The marketplace (step 16s): its items read by its rules, the marketplace read from GitHub and
kept, each kind installed in its place, updated, removed and retired.

GitHub is the fake one over real HTTP, serving real git repositories made here; the home, its
plugins, its skill library and its `mcp.json` are real.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from atlas.__main__ import SCHEMAS, main
from atlas.home import Database, Home, MarketplaceSettings, migrate
from atlas.marketplace import (
    Kind,
    Marketplace,
    MarketplaceError,
    MarketplaceStore,
    State,
    Tier,
    check,
    parse_item,
    parse_meta,
    parse_removed,
)
from atlas.plugins import PluginError, Plugins, installed, update_plugin
from atlas.secrets import Vault
from atlas.skills import Origin, SkillLibrary, SkillUsage
from fake_github import GIT, FakeGitHub
from marketplace_support import (
    MARKETPLACE,
    Repository,
    entry,
    marketplace,
    plugin_files,
    skill_text,
    write_marketplace,
)

pytestmark = pytest.mark.skipif(GIT is None, reason="the fake GitHub serves git repositories")

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def github() -> Iterator[FakeGitHub]:
    with FakeGitHub() as server:
        yield server


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def plugins(home: Home, vault: Vault) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault)


def _marketplace(
    home: Home,
    github: FakeGitHub,
    plugins: Plugins,
    clock: Clock,
    *,
    found: set[str] | None = None,
    stored: set[str] | None = None,
    system: str = "linux",
) -> Marketplace:
    settings = MarketplaceSettings(
        repository=MARKETPLACE, archive_url=github.archive_url, raw_url=github.raw_url
    )
    programs = found or set()
    return Marketplace(
        home,
        MarketplaceStore(home.marketplace_folder, settings),
        plugins,
        SkillLibrary(home.skills_folder),
        stored=lambda: stored or set(),
        clock=clock,
        which=lambda name: f"/usr/bin/{name}" if name in programs else None,
        system=system,
    )


def _row(market: Marketplace, name: str):
    return next(row for row in market.listing().rows if row.name == name)


def _servers(home: Home) -> dict[str, Any]:
    return json.loads(home.mcp_file.read_text(encoding="utf-8"))["mcpServers"]


DOCS_SERVER = {"command": "npx", "args": ["-y", "docs-mcp@1.2.3"], "env": {"KEY": "${DOCS_KEY}"}}
DOCS_SECRET = [{"name": "DOCS_KEY", "description": "The docs key.", "url": "https://example.com"}]


# -- the rules ------------------------------------------------------------------------------------


def _item(fields: dict[str, Any], name: str = "") -> str:
    return yaml.safe_dump(fields, sort_keys=False)


def test_an_item_of_each_kind_is_read_with_what_it_says() -> None:
    sha = "a" * 40
    skill = parse_item(
        _item(
            {
                **entry("pdf-tools", "skill"),
                "path": "skills/pdf-tools",
                "repo": "someone/tools",
                "sha": sha,
                "image": f"https://raw.githubusercontent.com/someone/tools/{sha}/pdf.png",
                "version": "1.2.0",
            }
        ),
        "items/pdf-tools.yaml",
    )
    assert (skill.kind, skill.repo, skill.sha, skill.path) == (
        Kind.SKILL,
        "someone/tools",
        sha,
        "skills/pdf-tools",
    )
    assert skill.tier is Tier.COMMUNITY
    assert skill.revision == sha
    assert skill.source_url == f"https://github.com/someone/tools/tree/{sha}/skills/pdf-tools"
    server = parse_item(
        _item({**entry("docs", "mcp"), "server": DOCS_SERVER, "secrets": DOCS_SECRET}),
        "items/docs.yaml",
    )
    assert server.kind is Kind.MCP
    assert server.secrets[0].name == "DOCS_KEY"
    assert len(server.revision) == 40
    remote = parse_item(
        _item(
            {**entry("remote", "mcp"), "server": {"type": "http", "url": "https://x.example.com"}}
        ),
        "items/remote.yaml",
    )
    assert remote.server["url"] == "https://x.example.com"


@pytest.mark.parametrize(
    ("fields", "said"),
    [
        ({"name": "Bad_Name"}, "name must be lowercase"),
        ({"name": "other"}, "the file must be named after the item"),
        ({"kind": "theme"}, "kind must be one of"),
        ({"sha": "abc123"}, "sha must be a whole commit"),
        ({"repo": "not a repo"}, "repo must be a GitHub repository"),
        ({"path": "../outside"}, "path must be a folder inside the repository"),
        ({"author": "Some Person"}, "author must be the GitHub account"),
        ({"summary": "x" * 200}, "summary must be one sentence"),
        ({"tier": "verified"}, "tier must be one of"),
        ({"image": "https://example.com/a.png"}, "must be a file of the item's own repository"),
    ],
)
def test_an_item_that_breaks_a_rule_is_refused_saying_which(
    fields: dict[str, Any], said: str
) -> None:
    item = {**entry("tools", "skill"), "repo": "someone/tools", "sha": "b" * 40, **fields}
    with pytest.raises(MarketplaceError, match=said):
        parse_item(_item(item), "items/tools.yaml")


@pytest.mark.parametrize(
    ("server", "said"),
    [
        ({"command": "npx", "args": ["-y", "docs-mcp"]}, "npx must run a package at an exact"),
        ({"command": "npx", "args": ["-y", "docs-mcp@latest"]}, "npx must run a package"),
        ({"command": "npx", "args": ["-y", "docs-mcp@^1.2.0"]}, "npx must run a package"),
        ({"command": "uvx", "args": ["docs-mcp>=1.0"]}, "uvx must run a package at an exact"),
        ({"command": "docker", "args": ["run", "-i", "docs:latest"]}, "docker must run an image"),
        ({"command": "node", "args": ["server.js"]}, "must run through npx, uvx or docker"),
        ({"type": "http", "url": "http://x.example.com"}, "must be an https address"),
        ({"type": "http", "url": "https://x.example.com", "oauth": {}}, "may not hold oauth"),
        ({"command": "npx", "args": ["a@1.0.0"], "headersHelper": "x"}, "may not hold"),
        ({"command": "npx", "args": ["a@1.0.0", "${TOKEN}"]}, "secrets does not declare"),
    ],
)
def test_an_mcp_server_must_pin_what_it_runs_and_declare_its_secrets(
    server: dict[str, Any], said: str
) -> None:
    with pytest.raises(MarketplaceError, match=said):
        parse_item(_item({**entry("docs", "mcp"), "server": server}), "items/docs.yaml")


def test_pinned_servers_of_each_runner_pass() -> None:
    digest = "c" * 64
    for server in (
        {"command": "npx", "args": ["-y", "@scope/docs-mcp@2.0.1"]},
        {"command": "uvx", "args": ["docs-mcp==2025.1.17"]},
        {"command": "uvx", "args": ["--from", "docs-mcp[cli]==1.0.0", "docs"]},
        {"command": "docker", "args": ["run", "-i", "--rm", f"ghcr.io/a/docs@sha256:{digest}"]},
        {"type": "sse", "url": "https://x.example.com/sse"},
    ):
        parse_item(_item({**entry("docs", "mcp"), "server": server}), "items/docs.yaml")


def test_a_declared_secret_the_server_never_reads_is_refused() -> None:
    with pytest.raises(MarketplaceError, match="never reads"):
        parse_item(
            _item(
                {
                    **entry("docs", "mcp"),
                    "server": {"command": "npx", "args": ["docs@1.0.0"]},
                    "secrets": DOCS_SECRET,
                }
            ),
            "items/docs.yaml",
        )


def test_the_marketplace_s_own_files_are_read_and_a_newer_format_is_refused() -> None:
    assert parse_meta("format: 1\nname: M\ndescription: D\n") == ("M", "D")
    with pytest.raises(MarketplaceError, match="Update Atlas"):
        parse_meta("format: 2\nname: M\n")
    retired = parse_removed("- name: bad\n  reason: It sent files away.\n  repo: x/bad\n")
    assert retired["bad"].reason == "It sent files away."
    with pytest.raises(MarketplaceError, match="name and a reason"):
        parse_removed("- name: bad\n")


# -- reading --------------------------------------------------------------------------------------


def test_the_marketplace_is_read_kept_and_the_last_read_stands_in_when_github_fails(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    sha = tools.write({"pdf/SKILL.md": skill_text("pdf")}).commit()
    market = _marketplace(home, github, plugins, clock)
    marketplace(
        tmp_path,
        github,
        {
            "pdf": entry("pdf", "skill", repo=tools, sha=sha, path="pdf"),
            "broken": {"name": "broken", "kind": "skill"},
        },
    )
    listing = market.listing()
    assert listing.name == "Test marketplace"
    assert listing.problem is None
    assert _row(market, "pdf").state is State.AVAILABLE
    assert "broken" not in {row.name for row in listing.rows}
    asked = len(github.asked)
    market.listing()
    assert len(github.asked) == asked, "a read younger than an hour is not read again"
    github.fail()
    clock.now += timedelta(hours=2)
    stale = market.listing()
    assert "pdf" in {row.name for row in stale.rows}
    assert stale.problem is not None
    assert "503" in stale.problem
    assert stale.read_at == NOW


def test_with_nothing_read_and_github_down_the_listing_says_why(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock
) -> None:
    github.fail()
    listing = _marketplace(home, github, plugins, clock).listing()
    assert listing.read_at is None
    assert listing.problem is not None
    # What Atlas ships is listed anyway, marked official.
    shipped = {row.name: row for row in listing.rows if row.shipped}
    assert shipped["claude-code"].kind is Kind.EXECUTOR
    assert shipped["claude-code"].title == "Claude Code"
    assert shipped["telegram"].kind is Kind.PLUGIN
    assert shipped["write-plugin"].kind is Kind.SKILL
    assert shipped["github"].kind is Kind.PLUGIN, "a plugin of Atlas's since step 26"
    assert {row.tier for row in shipped.values()} == {Tier.OFFICIAL}


# -- skills ---------------------------------------------------------------------------------------


def test_a_skill_installs_into_the_library_bringing_the_server_it_needs(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    requires = {"bins": ["ffmpeg", "magick"], "mcp": ["docs"], "os": ["linux", "darwin"]}
    sha = tools.write(
        {
            "video/SKILL.md": skill_text("video", requires=requires, body="Cut videos."),
            "video/scripts/cut.py": "# /// script\n# dependencies = ['av==14.0.0']\n# ///\n",
        }
    ).commit()
    home.mcp_file.write_text(json.dumps({"mcpServers": {"mine": {"command": "x"}}}), "utf-8")
    marketplace(
        tmp_path,
        github,
        {
            "video": entry("video", "skill", repo=tools, sha=sha, path="video"),
            "docs": {**entry("docs", "mcp"), "server": DOCS_SERVER, "secrets": DOCS_SECRET},
        },
    )
    market = _marketplace(home, github, plugins, clock, found={"ffmpeg"})
    page = market.page("video")
    assert page.readme == "Cut videos."
    assert [(need.kind, need.name, need.state) for need in page.needs] == [
        ("program", "ffmpeg", "met"),
        ("program", "magick", "missing"),
        ("server", "docs", "brought"),
        ("system", "linux, macos", "met"),
    ]
    done = market.install("video")
    assert done.changed == ["docs", "video"]
    assert (home.skills_folder / "video" / "scripts" / "cut.py").is_file()
    servers = _servers(home)
    assert servers["mine"] == {"command": "x"}, "the user's servers stay as they were"
    assert servers["docs"] == DOCS_SERVER
    assert _row(market, "video").state is State.INSTALLED
    docs = _row(market, "docs")
    assert docs.state is State.INSTALLED
    assert docs.problem == "Store DOCS_KEY to start it."
    assert market.store.records()["docs"].needed_by == ("video",)
    with pytest.raises(MarketplaceError, match="video needs docs: remove it first"):
        market.remove("docs")
    with pytest.raises(MarketplaceError, match="installed already"):
        market.install("video")


@pytest.mark.parametrize(
    ("files", "said"),
    [
        ({"video/SKILL.md": skill_text("other")}, "named other, not video"),
        ({"video/notes.md": "no skill here"}, "has no SKILL.md"),
        ({"video/SKILL.md": skill_text("video"), "video/hooks/hooks.json": "{}"}, "plugin"),
        ({"video/SKILL.md": skill_text("video"), "video/.mcp.json": "{}"}, "plugin"),
        ({"video/SKILL.md": skill_text("video"), "video/bin/tool": b"\x7fELF\x02\x01"}, "compiled"),
        ({"video/SKILL.md": skill_text("video"), "video/a.pyc": b"\x00\x00"}, "compiled"),
        ({"video/SKILL.md": skill_text("video", requires={"os": ["windows"]})}, "works on windows"),
        ({"video/SKILL.md": skill_text("video", requires={"mcp": ["nowhere"]})}, "nowhere"),
    ],
)
def test_a_skill_that_is_not_what_it_says_is_refused_and_nothing_lands(
    home: Home,
    github: FakeGitHub,
    plugins: Plugins,
    clock: Clock,
    tmp_path: Path,
    files: dict[str, str | bytes],
    said: str,
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    sha = tools.write(files).commit()
    marketplace(
        tmp_path, github, {"video": entry("video", "skill", repo=tools, sha=sha, path="video")}
    )
    market = _marketplace(home, github, plugins, clock)
    with pytest.raises(MarketplaceError, match=said):
        market.install("video")
    assert not (home.skills_folder / "video").exists()
    assert market.store.records() == {}


def test_a_skill_is_updated_kept_when_changed_here_and_forced_with_its_version_kept(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    first = tools.write({"pdf/SKILL.md": skill_text("pdf", body="First.")}).commit()
    store = marketplace(
        tmp_path, github, {"pdf": entry("pdf", "skill", repo=tools, sha=first, path="pdf")}
    )
    market = _marketplace(home, github, plugins, clock)
    market.install("pdf")
    second = tools.write({"pdf/SKILL.md": skill_text("pdf", body="Second.")}).commit()
    write_marketplace(store, {"pdf": entry("pdf", "skill", repo=tools, sha=second, path="pdf")})
    market.listing(refresh=True)
    assert _row(market, "pdf").update
    done = market.update("pdf")
    assert done.changed == ["pdf"]
    assert "Second." in (home.skills_folder / "pdf" / "SKILL.md").read_text("utf-8")
    assert not _row(market, "pdf").update
    # Changed here (by the user, or by the review), the next update waits for force.
    (home.skills_folder / "pdf" / "SKILL.md").write_text(skill_text("pdf", body="Mine."), "utf-8")
    third = tools.write({"pdf/SKILL.md": skill_text("pdf", body="Third.")}).commit()
    write_marketplace(store, {"pdf": entry("pdf", "skill", repo=tools, sha=third, path="pdf")})
    market.listing(refresh=True)
    waiting = market.update("pdf")
    assert waiting.local_changes
    assert "Mine." in (home.skills_folder / "pdf" / "SKILL.md").read_text("utf-8")
    market.update("pdf", force=True)
    assert "Third." in (home.skills_folder / "pdf" / "SKILL.md").read_text("utf-8")
    assert market.library.versions("pdf"), "what the home held is kept as a version"


def test_removing_a_skill_archives_it(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    sha = tools.write({"pdf/SKILL.md": skill_text("pdf")}).commit()
    marketplace(tmp_path, github, {"pdf": entry("pdf", "skill", repo=tools, sha=sha, path="pdf")})
    market = _marketplace(home, github, plugins, clock)
    market.install("pdf")
    row = market.remove("pdf")
    assert row.state is State.AVAILABLE
    assert not (home.skills_folder / "pdf").exists()
    assert market.library.archived() == ["pdf"]


def test_a_skill_atlas_ships_deleted_by_the_user_is_put_back_by_installing_it(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    marketplace(tmp_path, github)
    market = _marketplace(home, github, plugins, clock)
    assert _row(market, "write-plugin").state is State.AVAILABLE
    market.install("write-plugin")
    assert (home.skills_folder / "write-plugin" / "SKILL.md").is_file()
    assert _row(market, "write-plugin").state is State.INSTALLED


# -- MCP servers ----------------------------------------------------------------------------------


def test_a_server_is_written_into_mcp_json_and_one_of_the_same_name_refuses_it(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    store = marketplace(
        tmp_path,
        github,
        {"docs": {**entry("docs", "mcp"), "server": DOCS_SERVER, "secrets": DOCS_SECRET}},
    )
    market = _marketplace(home, github, plugins, clock, stored={"DOCS_KEY"})
    page = market.page("docs")
    assert [(s.name, s.stored) for s in page.secrets] == [("DOCS_KEY", True)]
    market.install("docs")
    assert _servers(home)["docs"] == DOCS_SERVER
    assert _row(market, "docs").problem is None
    # A new entry is an update; the entry changed here waits for force.
    changed = {**DOCS_SERVER, "args": ["-y", "docs-mcp@1.3.0"]}
    write_marketplace(
        store, {"docs": {**entry("docs", "mcp"), "server": changed, "secrets": DOCS_SECRET}}
    )
    market.listing(refresh=True)
    assert _row(market, "docs").update
    document = json.loads(home.mcp_file.read_text("utf-8"))
    document["mcpServers"]["docs"]["env"]["EXTRA"] = "1"
    home.mcp_file.write_text(json.dumps(document), "utf-8")
    assert market.update("docs").local_changes
    market.update("docs", force=True)
    assert _servers(home)["docs"] == changed
    market.remove("docs")
    assert "docs" not in _servers(home)
    home.mcp_file.write_text(json.dumps({"mcpServers": {"docs": {"command": "mine"}}}), "utf-8")
    assert _row(market, "docs").problem == "Your mcp.json has a server named docs already."
    with pytest.raises(MarketplaceError, match="already"):
        market.install("docs")
    home.mcp_file.write_text("{ not json", "utf-8")
    with pytest.raises(MarketplaceError, match="not valid JSON"):
        market.install("docs")
    assert home.mcp_file.read_text("utf-8") == "{ not json", "a broken file is never written over"


# -- plugins and executors ------------------------------------------------------------------------


def test_a_plugin_arrives_off_from_its_commit_and_updates_only_through_the_marketplace(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    code = Repository(tmp_path, "someone/video", github)
    first = code.write({f"plugins/video/{k}": v for k, v in plugin_files("video").items()}).commit()
    store = marketplace(
        tmp_path,
        github,
        {"video": entry("video", "plugin", repo=code, sha=first, path="plugins/video")},
    )
    market = _marketplace(home, github, plugins, clock)
    page = market.page("video")
    assert page.readme.startswith("# video")
    assert page.picture_base.endswith(f"/raw/someone/video/{first}/plugins/video/")
    market.install("video")
    view = plugins.view("video")
    assert not view.enabled
    assert _row(market, "video").state is State.OFF
    record = installed(plugins.folder)["video"]
    assert (record.source, record.revision) == ("marketplace:video", first)
    with pytest.raises(PluginError, match="came from the marketplace"):
        update_plugin(plugins, "video")
    manifest = view.manifest
    assert manifest is not None
    plugins.enable("video", manifest.fingerprint())
    assert _row(market, "video").state is State.ON
    changed = plugin_files("video") | {"README.md": "# video\n\nNow better.\n"}
    second = code.write({f"plugins/video/{k}": v for k, v in changed.items()}).commit()
    write_marketplace(
        store, {"video": entry("video", "plugin", repo=code, sha=second, path="plugins/video")}
    )
    market.listing(refresh=True)
    market.update("video")
    assert installed(plugins.folder)["video"].revision == second
    assert "Now better." in (plugins.folder / "video" / "README.md").read_text("utf-8")
    market.remove("video")
    assert not (plugins.folder / "video").exists()


@pytest.mark.parametrize(
    ("kind", "files", "said"),
    [
        ("plugin", plugin_files("other"), "named other, not video"),
        ("executor", plugin_files("video"), "registers no executor"),
        ("plugin", plugin_files("video", executors=["video"]), "list it as an executor"),
        ("plugin", plugin_files("video", api=999), "newer Atlas"),
        ("plugin", {**plugin_files("video"), "bin/tool.exe": b"MZ\x90\x00"}, "compiled"),
    ],
)
def test_a_plugin_that_is_not_what_its_item_says_is_refused(
    home: Home,
    github: FakeGitHub,
    plugins: Plugins,
    clock: Clock,
    tmp_path: Path,
    kind: str,
    files: dict[str, str | bytes],
    said: str,
) -> None:
    code = Repository(tmp_path, "someone/video", github)
    sha = code.write(files).commit()
    marketplace(tmp_path, github, {"video": entry("video", kind, repo=code, sha=sha)})
    market = _marketplace(home, github, plugins, clock)
    with pytest.raises(MarketplaceError, match=said):
        market.install("video")
    assert not (plugins.folder / "video").exists()


def test_an_executor_is_listed_and_installed_as_one(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    code = Repository(tmp_path, "someone/harness", github)
    sha = code.write(plugin_files("harness", executors=["harness"])).commit()
    marketplace(tmp_path, github, {"harness": entry("harness", "executor", repo=code, sha=sha)})
    market = _marketplace(home, github, plugins, clock)
    assert _row(market, "harness").kind is Kind.EXECUTOR
    market.install("harness")
    assert _row(market, "harness").state is State.OFF


# -- retiring -------------------------------------------------------------------------------------


def test_what_the_marketplace_retires_stops_where_it_was_installed_and_is_refused(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    code = Repository(tmp_path, "evil/video", github)
    sha = tools.write({"pdf/SKILL.md": skill_text("pdf")}).commit()
    video = code.write(plugin_files("video")).commit()
    items = {
        "pdf": entry("pdf", "skill", repo=tools, sha=sha, path="pdf"),
        "video": entry("video", "plugin", repo=code, sha=video),
        "docs": {**entry("docs", "mcp"), "server": DOCS_SERVER, "secrets": DOCS_SECRET},
        "other": entry("other", "skill", repo=code, sha=video),
    }
    store = marketplace(tmp_path, github, items)
    market = _marketplace(home, github, plugins, clock)
    for name in ("pdf", "video", "docs"):
        market.install(name)
    removed = [
        {"name": "pdf", "reason": "It sent files away."},
        {"name": "docs", "reason": "It leaked its key."},
        # Retired by repository: everything from it stops.
        {"name": "evil-repo", "repo": "evil/video", "reason": "Its author's account was taken."},
    ]
    write_marketplace(store, items, removed=removed)
    listing = market.listing(refresh=True)
    rows = {row.name: row for row in listing.rows}
    assert rows["pdf"].state is State.RETIRED
    assert rows["pdf"].problem == "Retired from the marketplace: It sent files away."
    assert rows["video"].state is State.RETIRED
    assert rows["docs"].state is State.RETIRED
    assert "other" not in rows, "a retired item nobody installed is not listed"
    assert not (home.skills_folder / "pdf").exists()
    assert market.library.archived() == ["pdf"]
    assert not (plugins.folder / "video").exists()
    assert "docs" not in _servers(home)
    for name in ("pdf", "video", "docs"):
        with pytest.raises(MarketplaceError, match="retired"):
            market.install(name)


# -- the marketplace's own CI ---------------------------------------------------------------------


def _write_item(folder: Path, fields: dict[str, Any]) -> Path:
    path = folder / "items" / f"{fields['name']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(fields), "utf-8")
    return path


def test_the_check_reads_each_item_and_its_code_at_its_commit(
    home: Home, github: FakeGitHub, tmp_path: Path
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    good = tools.write(
        {
            "pdf/SKILL.md": skill_text("pdf"),
            **{f"video/{k}": v for k, v in plugin_files("video").items()},
        }
    ).commit()
    hooked = tools.write({"pdf/hooks/hooks.json": "{}"}).commit()
    root = tmp_path / "submission"
    (root).mkdir()
    (root / "marketplace.yaml").write_text("format: 1\nname: M\n", "utf-8")
    store = MarketplaceStore(
        home.marketplace_folder,
        MarketplaceSettings(archive_url=github.archive_url, raw_url=github.raw_url),
    )
    files = [
        _write_item(root, entry("pdf", "skill", repo=tools, sha=good, path="pdf")),
        _write_item(root, entry("video", "plugin", repo=tools, sha=good, path="video")),
    ]
    assert check(files, store, root=root) == []
    files = [
        _write_item(root, entry("pdf", "skill", repo=tools, sha=hooked, path="pdf")),
        _write_item(root, entry("video", "executor", repo=tools, sha=good, path="video")),
        _write_item(root, {**entry("docs", "mcp"), "server": {"command": "npx", "args": ["docs"]}}),
        _write_item(root, entry("github", "skill", repo=tools, sha=good, path="pdf")),
    ]
    problems = check(files, store, root=root, shipped={"github"})
    assert any("pdf.yaml" in p and "plugin" in p for p in problems)
    assert any("video.yaml" in p and "registers no executor" in p for p in problems)
    assert any("docs.yaml" in p and "exact version" in p for p in problems)
    assert any("github.yaml" in p and "Atlas ships" in p for p in problems)


def test_the_check_loads_a_plugin_s_python_only_when_asked(
    home: Home, github: FakeGitHub, tmp_path: Path
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    broken = plugin_files("video", tools=["cut"], code="raise RuntimeError('broken on import')\n")
    sha = tools.write(broken).commit()
    root = tmp_path / "submission"
    root.mkdir()
    store = MarketplaceStore(
        home.marketplace_folder,
        MarketplaceSettings(archive_url=github.archive_url, raw_url=github.raw_url),
    )
    files = [_write_item(root, entry("video", "plugin", repo=tools, sha=sha))]
    assert check(files, store) == []
    problems = check(files, store, load=True)
    assert any("broken on import" in problem for problem in problems)


def test_a_skill_from_the_marketplace_is_the_user_s_so_the_curator_leaves_it(
    home: Home, github: FakeGitHub, plugins: Plugins, clock: Clock, tmp_path: Path
) -> None:
    usage = SkillUsage(Database(home.database_file))
    # A skill the review once made under this name, archived and gone, left its row behind.
    usage.created("pdf", Origin.REVIEW, NOW)
    tools = Repository(tmp_path, "someone/tools", github)
    sha = tools.write({"pdf/SKILL.md": skill_text("pdf")}).commit()
    marketplace(tmp_path, github, {"pdf": entry("pdf", "skill", repo=tools, sha=sha, path="pdf")})
    market = _marketplace(home, github, plugins, clock)
    market.library = SkillLibrary(home.skills_folder, usage)
    market.install("pdf")
    record = usage.record("pdf")
    assert record is not None
    assert record.origin is Origin.USER


def test_the_terminal_lists_shows_installs_updates_and_removes(
    home: Home, github: FakeGitHub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tools = Repository(tmp_path, "someone/tools", github)
    sha = tools.write(
        {"pdf/SKILL.md": skill_text("pdf", requires={"bins": ["nowhere-tool"]})}
    ).commit()
    marketplace(
        tmp_path,
        github,
        {
            "pdf": entry("pdf", "skill", repo=tools, sha=sha, path="pdf"),
            "docs": {**entry("docs", "mcp"), "server": DOCS_SERVER, "secrets": DOCS_SECRET},
        },
    )
    environ = {
        "ATLAS_HOME": str(home.root),
        "ATLAS_MARKETPLACE_REPOSITORY": MARKETPLACE,
        "ATLAS_MARKETPLACE_ARCHIVE_URL": github.archive_url,
        "ATLAS_MARKETPLACE_RAW_URL": github.raw_url,
        "ATLAS_MARKETPLACE_OUTSIDE": "false",
    }
    assert main(["marketplace", "list", "--kind", "skill"], dict(environ)) == 0
    said = capsys.readouterr().out
    assert "Skills" in said
    assert "pdf 1.0.0" not in said
    assert "  pdf  Pdf by someone  (available)" in said
    assert "Write an Atlas plugin with the user" in said
    assert main(["marketplace", "show", "pdf"], dict(environ)) == 0
    assert "needs the program nowhere-tool: missing" in capsys.readouterr().out
    assert main(["marketplace", "install", "pdf"], dict(environ)) == 0
    assert "pdf is installed." in capsys.readouterr().out
    assert main(["marketplace", "update", "pdf"], dict(environ)) == 0
    assert "pdf is up to date." in capsys.readouterr().out
    assert main(["marketplace", "install", "docs"], dict(environ)) == 0
    assert "Store DOCS_KEY to start it." in capsys.readouterr().out
    assert main(["marketplace", "remove", "pdf"], dict(environ)) == 0
    assert "pdf was removed." in capsys.readouterr().out
    assert main(["marketplace", "install", "nowhere"], dict(environ)) == 1
    assert "has no item nowhere" in capsys.readouterr().err


def test_the_terminal_check_says_each_problem_and_fails(
    home: Home, github: FakeGitHub, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "submission"
    (root / "items").mkdir(parents=True)
    (root / "marketplace.yaml").write_text("format: 1\nname: M\n", "utf-8")
    environ = {"ATLAS_HOME": str(home.root)}
    assert main(["marketplace", "check", "--root", str(root)], dict(environ)) == 0
    assert "No items yet: the marketplace's own files read well." in capsys.readouterr().out
    _write_item(root, {**entry("docs", "mcp"), "server": DOCS_SERVER, "secrets": DOCS_SECRET})
    assert main(["marketplace", "check", "--root", str(root)], dict(environ)) == 0
    assert "1 item checked: each may enter." in capsys.readouterr().out
    (root / "items" / "bad.yaml").write_text("name: Bad\nkind: skill\n", "utf-8")
    assert main(["marketplace", "check", "--root", str(root)], dict(environ)) == 1
    said = capsys.readouterr().out
    assert "items/bad.yaml: name must be lowercase" in said
    assert "2 items checked: 7 problems." in said
