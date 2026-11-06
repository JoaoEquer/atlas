"""What a card of Claude Code's plugin directory says: only what the directory gives."""

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from atlas.marketplace import MarketplaceError
from atlas.marketplace.model import Item
from atlas.marketplace.sources import (
    FACTS_FILE,
    Sources,
    manifest_facts,
    organization_facts,
    plugins_installs,
    plugins_item,
    publisher_picture,
    registry_item,
    skills_item,
)

DIRECTORY = "example/plugins-directory"
CATALOG = "https://example.com/catalog.json"
INSTALLS = "https://example.com/installs.json"
RAW = "https://example.com/raw/{repo}/{ref}/{path}"
GITHUB = "https://example.com/github"
COMMIT = "a" * 40
NOW = datetime(2026, 11, 6, tzinfo=UTC)

Get = Callable[[str, int], bytes | None]


def _plugin(name: str, **more: object) -> dict[str, object]:
    return {"name": name, "description": "A plugin.", "source": f"./plugins/{name}", **more}


def _catalog(*plugins: dict[str, object]) -> bytes:
    return json.dumps({"name": "plugins-directory", "plugins": list(plugins)}).encode()


def _installs(**counts: object) -> bytes:
    rows = [
        {"plugin": f"{name.replace('_', '-')}@plugins-directory", "unique_installs": count}
        for name, count in counts.items()
    ]
    return json.dumps({"plugins": rows}).encode()


def _sources(get: Get) -> Sources:
    return Sources(
        get,
        registry_url="https://example.com/registry",
        skills_url="https://example.com/skills",
        github_api_url="https://example.com/github",
        plugins_url=CATALOG,
        plugins_repository=DIRECTORY,
        plugins_installs_url=INSTALLS,
    )


def _serving(catalog: bytes, installs: bytes | None) -> Get:
    def get(url: str, _limit: int) -> bytes | None:
        return catalog if url == CATALOG else installs

    return get


def test_a_plugin_keeps_its_plain_name_when_the_directory_gives_no_display_name() -> None:
    plain = plugins_item(_plugin("api-security-testing"), DIRECTORY)
    named = plugins_item(_plugin("gc-ai", displayName="GC AI"), DIRECTORY)

    assert plain is not None
    assert plain.title == "api-security-testing"
    assert named is not None
    assert (named.title, named.target) == ("GC AI", "gc-ai")


def test_the_category_is_kept_as_the_directory_writes_it() -> None:
    filed = plugins_item(_plugin("notes", category="development"), DIRECTORY)
    unfiled = plugins_item(_plugin("notes"), DIRECTORY)

    assert filed is not None
    assert filed.category == "development"
    assert unfiled is not None
    assert unfiled.category == ""


def test_a_plugin_in_the_directory_s_own_folders_is_not_said_to_be_the_directory_s() -> None:
    own_folder = plugins_item(_plugin("notes"), DIRECTORY)
    own_repository = plugins_item(
        {
            "name": "tool",
            "description": "A tool.",
            "source": {"source": "url", "url": "https://github.com/someone/tool.git"},
        },
        DIRECTORY,
    )

    assert own_folder is not None
    assert own_folder.author == ""
    assert own_repository is not None
    assert own_repository.author == "someone"


def test_the_installs_are_read_only_from_a_count_of_this_directory() -> None:
    data = json.dumps(
        {
            "plugins": [
                {"plugin": "notes@plugins-directory", "unique_installs": 12},
                {"plugin": "other@another-directory", "unique_installs": 99},
                {"plugin": "words@plugins-directory", "unique_installs": "many"},
                {"plugin": "flag@plugins-directory", "unique_installs": True},
                "not a row",
            ]
        }
    ).encode()

    assert plugins_installs(data, "plugins-directory") == {"notes": 12}
    assert plugins_installs(b"not json", "plugins-directory") == {}
    assert plugins_installs(b"[]", "plugins-directory") == {}
    assert plugins_installs(data, "") == {}


def test_the_count_is_matched_by_the_name_the_catalog_gives_itself() -> None:
    catalog = json.dumps({"name": "renamed-directory", "plugins": [_plugin("notes")]}).encode()
    count = json.dumps(
        {"plugins": [{"plugin": "notes@renamed-directory", "unique_installs": 3}]}
    ).encode()
    found = _sources(_serving(catalog, count)).search("", NOW)

    assert [item.installs for item in found.items if item.target] == [3]


def test_the_most_installed_come_first_and_one_with_no_count_shows_none() -> None:
    catalog = _catalog(_plugin("alpha"), _plugin("beta"), _plugin("gamma"))
    found = _sources(_serving(catalog, _installs(beta=5, gamma=40))).search("", NOW)

    plugins = [(item.target, item.installs) for item in found.items if item.target]

    assert plugins == [("gamma", 40), ("beta", 5), ("alpha", None)]


def test_a_count_under_another_spelling_is_not_taken_for_the_plugin_s() -> None:
    catalog = _catalog(_plugin("notion"))
    found = _sources(_serving(catalog, _installs(Notion=7))).search("", NOW)

    assert [item.installs for item in found.items if item.target] == [None]


def test_with_no_count_to_read_the_plugins_stay_listed_by_title() -> None:
    def refused(url: str, _limit: int) -> bytes | None:
        if url == CATALOG:
            return _catalog(_plugin("beta"), _plugin("alpha"))
        raise MarketplaceError("GitHub answered HTTP 503.")

    for get in (refused, _serving(_catalog(_plugin("beta"), _plugin("alpha")), None)):
        found = _sources(get).search("", NOW)
        plugins = [(item.target, item.installs) for item in found.items if item.target]

        assert plugins == [("alpha", None), ("beta", None)]


def test_a_plugin_s_picture_is_its_publisher_s_only_when_the_catalog_says_who_that_is() -> None:
    own_repository = plugins_item(
        {
            "name": "tool",
            "source": {"source": "url", "url": "https://github.com/someone/tool.git"},
        },
        DIRECTORY,
    )
    by_the_maker = plugins_item(_plugin("notes", author={"name": "Example"}), DIRECTORY, "Example")
    by_another = plugins_item(_plugin("notes", author={"name": "Other"}), DIRECTORY, "Example")
    by_nobody = plugins_item(_plugin("notes"), DIRECTORY, "Example")

    assert own_repository is not None
    assert own_repository.image == "https://github.com/someone.png?size=96"
    assert by_the_maker is not None
    assert by_the_maker.image == "https://github.com/example.png?size=96"
    assert by_another is not None
    assert by_another.image == ""
    assert by_nobody is not None
    assert by_nobody.image == ""


def _manifest_of(plugin: str) -> str:
    return f"https://example.com/raw/{DIRECTORY}/HEAD/plugins/{plugin}/.claude-plugin/plugin.json"


def _reading(get: Get, folder: Path | None = None) -> Sources:
    return Sources(
        get,
        registry_url="https://example.com/registry",
        skills_url="https://example.com/skills",
        github_api_url=GITHUB,
        plugins_raw_url=RAW,
        plugins_url=CATALOG,
        plugins_repository=DIRECTORY,
        folder=folder,
    )


def _manifested(answers: dict[str, bytes | None], folder: Path | None = None) -> Sources:
    return _reading(lambda url, _limit: answers.get(url), folder)


def _one(sources: Sources) -> Item:
    return sources.plugins()[0]


def test_a_manifest_says_what_the_catalog_does_not() -> None:
    answers: dict[str, bytes | None] = {
        CATALOG: _catalog(_plugin("notes")),
        _manifest_of("notes"): json.dumps(
            {"displayName": "Notes", "author": {"name": "Someone"}, "icon": "./assets/icon.png"}
        ).encode(),
    }
    item = _one(_manifested(answers))

    assert (item.title, item.author) == ("Notes", "Someone")
    assert item.image == f"https://example.com/raw/{DIRECTORY}/HEAD/plugins/notes/assets/icon.png"


def test_what_the_catalog_says_stands_but_the_plugin_s_own_picture_comes_first() -> None:
    plugin = {
        "name": "notes",
        "displayName": "Catalog Notes",
        "author": {"name": "Catalog"},
        "source": {"source": "url", "url": "https://github.com/someone/notes.git", "sha": COMMIT},
    }
    manifest = f"https://example.com/raw/someone/notes/{COMMIT}/.claude-plugin/plugin.json"
    answers: dict[str, bytes | None] = {
        CATALOG: _catalog(plugin),
        manifest: json.dumps(
            {"displayName": "Other", "author": {"name": "Other"}, "logo": "logo.svg"}
        ).encode(),
    }
    item = _one(_manifested(answers))

    assert (item.title, item.author) == ("Catalog Notes", "Catalog")
    assert item.image == f"https://example.com/raw/someone/notes/{COMMIT}/logo.svg"


def test_a_plugin_a_search_did_not_find_keeps_the_picture_it_was_listed_with() -> None:
    answers: dict[str, bytes | None] = {
        CATALOG: _catalog(_plugin("notes")),
        _manifest_of("notes"): b'{"icon": "icon.png"}',
    }
    sources = _manifested(answers)

    assert sources.picture("claude-plugins:notes") == ""

    sources.search("something else", NOW)

    assert sources.picture("claude-plugins:notes").endswith("/plugins/notes/icon.png")
    assert sources.picture("claude-plugins:other") == ""


def test_a_picture_outside_the_plugin_s_folder_or_not_a_picture_is_not_taken() -> None:
    for icon in ("../secret.png", "a/../../b.png", "https://example.com/a.png", "run.sh", 7):
        assert manifest_facts(json.dumps({"icon": icon}).encode())["picture"] == ""


def test_what_is_not_a_manifest_says_nothing() -> None:
    for data in (b"not json", b"[]"):
        assert set(manifest_facts(data).values()) == {""}


def test_an_author_s_avatar_needs_github_to_say_it_is_an_organization_of_that_name() -> None:
    def account(kind: str, name: str) -> bytes:
        return json.dumps({"login": "acme", "type": kind, "name": name}).encode()

    avatar = "https://github.com/acme.png?size=96"

    assert organization_facts(account("Organization", "Acme"), "Acme")["picture"] == avatar
    assert organization_facts(account("Organization", "ACME AI"), "Acme")["picture"] == avatar
    assert organization_facts(account("Organization", "Acme, Inc."), "Acme")["picture"] == avatar
    assert organization_facts(account("Organization", "Acme"), "Acme Inc.")["picture"] == avatar
    assert organization_facts(account("User", "Acme"), "Acme")["picture"] == ""
    assert organization_facts(account("Organization", "Acmeware"), "Acme")["picture"] == ""
    assert organization_facts(account("Organization", "Other"), "Acme")["picture"] == ""
    assert organization_facts(b"not json", "Acme")["picture"] == ""


def test_a_plugin_is_given_its_author_s_organization_only_as_github_confirms_it() -> None:
    answers: dict[str, bytes | None] = {
        CATALOG: _catalog(_plugin("notes")),
        _manifest_of("notes"): json.dumps({"author": {"name": "Acme Labs"}}).encode(),
        f"{GITHUB}/users/AcmeLabs": json.dumps(
            {"login": "AcmeLabs", "type": "Organization", "name": "Acme Labs"}
        ).encode(),
    }

    assert _one(_manifested(answers)).image == "https://github.com/AcmeLabs.png?size=96"

    answers[f"{GITHUB}/users/AcmeLabs"] = None

    assert _one(_manifested(answers)).image == ""


def test_the_directory_s_own_maker_named_by_a_manifest_shows_the_directory_s_account() -> None:
    catalog = json.dumps(
        {"name": "plugins-directory", "owner": {"name": "Example"}, "plugins": [_plugin("notes")]}
    ).encode()
    answers: dict[str, bytes | None] = {
        CATALOG: catalog,
        _manifest_of("notes"): json.dumps({"author": {"name": "Example"}}).encode(),
    }

    assert _one(_manifested(answers)).image == "https://github.com/example.png?size=96"


def test_a_manifest_that_cannot_be_read_leaves_the_card_as_the_catalog_has_it() -> None:
    def refused(url: str, _limit: int) -> bytes | None:
        if url == CATALOG:
            return _catalog(_plugin("notes"))
        raise MarketplaceError("GitHub answered HTTP 503.")

    sources = _reading(refused)

    assert _one(sources).title == "notes"


def test_a_commit_s_manifest_is_read_once_and_kept_across_runs(tmp_path: Path) -> None:
    reads: list[str] = []
    plugin = {
        "name": "notes",
        "source": {"source": "url", "url": "https://github.com/someone/notes.git", "sha": COMMIT},
    }
    manifest = f"https://example.com/raw/someone/notes/{COMMIT}/.claude-plugin/plugin.json"

    def get(url: str, _limit: int) -> bytes | None:
        reads.append(url)
        if url == CATALOG:
            return _catalog(plugin)
        return b'{"displayName": "Notes"}' if url == manifest else None

    def sources() -> Sources:
        return _reading(get, tmp_path)

    first = sources()

    assert _one(first).title == "Notes"
    assert _one(first).title == "Notes"
    assert _one(sources()).title == "Notes"
    assert reads.count(manifest) == 1


def test_a_manifest_at_no_commit_is_read_again_after_a_day(tmp_path: Path) -> None:
    reads: list[str] = []

    def get(url: str, _limit: int) -> bytes | None:
        reads.append(url)
        return _catalog(_plugin("notes")) if url == CATALOG else b"{}"

    sources = _reading(get, tmp_path)
    sources.plugins()
    sources.plugins()

    assert reads.count(_manifest_of("notes")) == 1

    kept = json.loads((tmp_path / FACTS_FILE).read_text(encoding="utf-8"))
    for row in kept.values():
        row["read_at"] = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    (tmp_path / FACTS_FILE).write_text(json.dumps(kept), encoding="utf-8")
    sources = _reading(get, tmp_path)
    sources.plugins()

    assert reads.count(_manifest_of("notes")) == 2


def test_with_the_catalog_down_a_card_keeps_what_was_read_and_nothing_is_asked(
    tmp_path: Path,
) -> None:
    answers: dict[str, bytes | None] = {
        CATALOG: _catalog(_plugin("notes")),
        _manifest_of("notes"): b'{"displayName": "Notes"}',
    }
    _manifested(answers, tmp_path).plugins()
    asked: list[str] = []

    def down(url: str, _limit: int) -> bytes | None:
        asked.append(url)
        raise MarketplaceError("GitHub could not be reached.")

    sources = _reading(down, tmp_path)

    assert _one(sources).title == "Notes"
    assert asked == [CATALOG]


def _server(**more: object) -> dict[str, object]:
    return {
        "server": {
            "name": "io.github.someone/notes",
            "description": "Notes.",
            "version": "1.0.0",
            "remotes": [{"type": "streamable-http", "url": "https://example.com/mcp"}],
            **more,
        }
    }


def test_a_server_s_own_icon_comes_first_then_the_github_account_it_names() -> None:
    icon = "https://example.com/icon.png"
    published = registry_item(_server(icons=[{"src": "http://example.com/a.png"}, {"src": icon}]))
    plain = registry_item(_server())
    linked = registry_item(_server(repository={"url": "https://github.com/acme/notes"}))

    assert published is not None
    assert published.image == icon
    assert plain is not None
    assert plain.image == "https://github.com/someone.png?size=96"
    assert linked is not None
    assert linked.image == "https://github.com/acme.png?size=96"


def test_a_skill_s_picture_is_its_repository_s_owner() -> None:
    skill = skills_item({"source": "someone/tools", "skillId": "notes"})

    assert skill is not None
    assert skill.image == "https://github.com/someone.png?size=96"


def test_an_item_with_no_picture_shows_who_owns_its_repository() -> None:
    assert publisher_picture("cloudflare/skills") == "https://github.com/cloudflare.png?size=96"
    assert publisher_picture("") == ""
