"""A plugin's themes (SDK version 24): colors for the app, in `themes/<name>.json`, with
no Python and no screen. Declared in the manifest, shown at consent, offered only while the plugin
is on, and read as data: a hex color for a step of the palette and nothing else."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Home, migrate
from atlas.plugins import ManifestError, Plugins
from atlas.plugins.model import THEME_TOKENS, parse_manifest, parse_theme
from atlas.secrets import Vault

SAND: dict[str, Any] = {
    "label": "Sand",
    "labels": {"pt-BR": "Areia"},
    "base": "light",
    "colors": {"background": "#E9E3D3", "subtle": "#e2dbc9", "working": "#2d63a8"},
}


def _theme_plugin(root: Path, theme: dict[str, Any] | str = SAND, api: int = 24) -> Path:
    folder = root / "sand"
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": "sand", "version": "1.0.0", "atlas": {"api": api, "themes": ["sand"]}}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (folder / "themes").mkdir()
    text = theme if isinstance(theme, str) else json.dumps(theme)
    (folder / "themes" / "sand.json").write_text(text, "utf-8")
    return folder


@pytest.fixture
def plugins(home: Home, vault: Vault, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, bundled_folder=tmp_path / "bundled")


def test_a_theme_is_read_as_data() -> None:
    theme = parse_theme("sand", json.dumps(SAND))
    assert (theme.name, theme.label, theme.base) == ("sand", "Sand", "light")
    assert theme.labels == {"pt-BR": "Areia"}
    assert theme.colors["background"] == "#e9e3d3", "colors are kept in lowercase"


@pytest.mark.parametrize(
    ("theme", "problem"),
    [
        ("not json", "is not JSON"),
        ("[]", "is not a JSON object"),
        ({**SAND, "script": "x"}, "holds script"),
        ({**SAND, "base": "sepia"}, "base is light or dark"),
        ({**SAND, "label": ""}, "label is a text"),
        ({**SAND, "labels": {"portuguese": "Areia"}}, "bad locale"),
        ({**SAND, "labels": {"pt\n": "Areia"}}, "bad locale"),
        ({**SAND, "colors": {}}, "colors is an object"),
        ({**SAND, "colors": {"subtle": "red"}}, "not a hex color"),
        ({**SAND, "colors": {"subtle": "url(https://x.test/a.png)"}}, "not a hex color"),
        ({**SAND, "colors": {"subtle": "#fff\n"}}, "not a hex color"),
        ({**SAND, "colors": {"subtle": "oklch(98% 0 0)"}}, "not a hex color"),
        ({**SAND, "colors": {"subtle;}body{display:none": "#fff"}}, "not a color of the app"),
        ({**SAND, "colors": {"--background": "#fff"}}, "not a color of the app"),
        ({**SAND, "colors": {"neutral-50": "#fff"}}, "not a color of the app"),
        ({**SAND, "colors": {"motion-fast": "#fff"}}, "not a color of the app"),
    ],
)
def test_a_theme_that_is_more_than_colors_is_refused(theme: Any, problem: str) -> None:
    text = theme if isinstance(theme, str) else json.dumps(theme)
    with pytest.raises(ManifestError, match=problem):
        parse_theme("sand", text)


def test_a_theme_past_its_size_is_refused() -> None:
    with pytest.raises(ManifestError, match="over 64 KB"):
        parse_theme("sand", json.dumps({**SAND, "label": "x" * (70 * 1024)}))


def _parsed(atlas: dict[str, Any], themes: dict[str, str] | None = None) -> Any:
    return parse_manifest(
        json.dumps({"name": "sand", "atlas": atlas}),
        set(),
        folder_name="sand",
        themes={"sand": json.dumps(SAND)} if themes is None else themes,
    )


def test_themes_are_declared_and_shown_at_consent() -> None:
    manifest = _parsed({"api": 24, "themes": ["sand"]})
    assert manifest.themes == ("sand",)
    assert any("Adds the themes sand" in line for line in manifest.declaration())
    assert manifest.fingerprint() != _parsed({"api": 24}).fingerprint(), "asks consent again"
    assert not manifest.python, "a theme brings no code"
    assert not manifest.screen


@pytest.mark.parametrize(
    ("atlas", "themes", "problem"),
    [
        ({"api": 23, "themes": ["sand"]}, None, "need atlas.api 24"),
        ({"api": 24, "themes": ["dusk"]}, None, "brings no themes/dusk.json"),
        ({"api": 24, "themes": ["Sand!"]}, None, "atlas.themes is a list of names"),
        ({"api": 24, "themes": ["sand"]}, {"sand": "{}"}, "base is light or dark|holds|colors"),
    ],
)
def test_themes_declared_badly_are_refused(
    atlas: dict[str, Any], themes: dict[str, str] | None, problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        _parsed(atlas, themes)


def test_a_theme_is_offered_only_while_its_plugin_is_on(plugins: Plugins, home: Home) -> None:
    _theme_plugin(home.plugins_folder)
    manifest = plugins.view("sand").manifest
    assert manifest is not None
    assert manifest.themes == ("sand",)
    assert plugins.themes() == [], "off: the app offers nothing of it"
    plugins.enable("sand", manifest.fingerprint())
    [offered] = plugins.themes()
    assert (offered.plugin, offered.theme.name, offered.theme.base) == ("sand", "sand", "light")
    assert offered.theme.colors["working"] == "#2d63a8"


def test_a_theme_file_that_went_bad_after_consent_refuses_the_plugin(
    plugins: Plugins, home: Home
) -> None:
    folder = _theme_plugin(home.plugins_folder)
    manifest = plugins.view("sand").manifest
    assert manifest is not None
    plugins.enable("sand", manifest.fingerprint())
    (folder / "themes" / "sand.json").write_text('{"base": "light", "colors": {"x": "y"}}', "utf-8")
    assert plugins.themes() == []
    assert "themes/sand.json" in str(plugins.view("sand").problem)


def test_a_plugin_with_a_theme_file_but_no_theme_declared_offers_nothing(
    plugins: Plugins, home: Home
) -> None:
    folder = _theme_plugin(home.plugins_folder)
    manifest_file = folder / ".claude-plugin" / "plugin.json"
    manifest_file.write_text(json.dumps({"name": "sand", "atlas": {"api": 24}}), "utf-8")
    view = plugins.view("sand")
    assert view.manifest is not None
    assert view.manifest.themes == ()
    plugins.enable("sand", view.manifest.fingerprint())
    assert plugins.themes() == []


def test_the_colors_a_theme_may_name_are_the_ones_the_app_defines() -> None:
    css = (Path(__file__).parents[2] / "apps" / "desktop" / "src" / "styles.css").read_text("utf-8")
    light = css.split(":root,\n.light {", 1)[1].split("\n}", 1)[0]
    defined = re.findall(r"^\s*--([a-z-]+):", light, re.MULTILINE)

    assert sorted(THEME_TOKENS) == sorted(defined)
