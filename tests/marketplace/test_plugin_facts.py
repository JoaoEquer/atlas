"""What a card of Claude Code's plugin directory says: only what the directory gives."""

import json
from collections.abc import Callable
from datetime import UTC, datetime

from atlas.marketplace import MarketplaceError
from atlas.marketplace.sources import Sources, plugins_installs, plugins_item

DIRECTORY = "example/plugins-directory"
CATALOG = "https://example.com/catalog.json"
INSTALLS = "https://example.com/installs.json"
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

    assert plugins_installs(data, DIRECTORY) == {"notes": 12}
    assert plugins_installs(b"not json", DIRECTORY) == {}
    assert plugins_installs(b"[]", DIRECTORY) == {}


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
