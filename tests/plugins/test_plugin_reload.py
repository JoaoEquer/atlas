"""For whoever writes and installs plugins (step 16n): a plugin loaded again when its files are
saved, installed by its address, and plugins that need others.

The plugins are real folders in a temporary home; the installs clone real git repositories made
here; the watch runs in a real server, whose plugin is saved while it serves.
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS, main
from atlas.home import Database, Home, migrate
from atlas.plugins import (
    Dependency,
    ManifestError,
    PluginError,
    Plugins,
    install_plugin,
    installed,
    remove_plugin,
    update_plugin,
)
from atlas.plugins.install import parse_address
from atlas.plugins.model import VersionError, parse_manifest, satisfies
from atlas.plugins.service import load_order
from atlas.secrets import Vault

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="installing by address needs git")


def _plugin(
    root: Path,
    name: str,
    *,
    version: str = "1.0.0",
    dependencies: list[Any] | None = None,
    code: str | None = None,
    atlas: dict[str, Any] | None = None,
) -> Path:
    folder = root / name
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest: dict[str, Any] = {"name": name, "version": version, "atlas": atlas or {}}
    if dependencies is not None:
        manifest["dependencies"] = dependencies
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (folder / "skills" / "hello").mkdir(parents=True)
    (folder / "skills" / "hello" / "SKILL.md").write_bytes(
        b"---\nname: hello\ndescription: Say hello.\n---\nSay hello.\n"
    )
    if code is not None:
        (folder / "__init__.py").write_bytes(code.encode())
    return folder


@pytest.fixture
def plugins(home: Home, vault: Vault, tmp_path: Path) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, official_folder=tmp_path / "bundled")


def _on(plugins: Plugins, name: str) -> None:
    manifest = plugins.view(name).manifest
    assert manifest is not None
    plugins.enable(name, manifest.fingerprint())


# -- versions ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "wanted", "held"),
    [
        ("2.1.5", "~2.1.0", True),
        ("2.2.0", "~2.1.0", False),
        ("2.9.0", "^2.0", True),
        ("3.0.0", "^2.0", False),
        ("3.0.0-beta", "^2.0", False),
        ("0.2.9", "^0.2.3", True),
        ("0.3.0", "^0.2.3", False),
        ("0.0.4", "^0.0.3", False),
        ("1.4.0", ">=1.4", True),
        ("1.3.9", ">=1.4", False),
        ("2.1.0", "=2.1.0", True),
        ("2.1.0", "2.1.0", True),
        ("2.1.1", "2.1.0", False),
        ("2.7.3", "2.x", True),
        ("3.0.0", "2.x", False),
        ("9.9.9", "*", True),
        ("9.9.9", "", True),
        ("1.5.0", ">=1.2 <2", True),
        ("2.0.0", ">=1.2 <2", False),
        ("3.1.0", "^1.0 || ^3.0", True),
        ("1.3.0", "1.2 - 1.4", True),
        ("1.4.9", "1.2 - 1.4", True),
        ("1.5.0", "1.2 - 1.4", False),
        ("1.0.0-rc.1", ">=1.0.0-beta", True),
        ("1.0.0", ">= 1.0.0", True),
        ("2.0.0", "<=2", True),
        ("3.0.0", "<=2", False),
        ("2.1.0", ">2", False),
        ("3.0.0", ">2", True),
        ("v1.2.3+build.5", "~1.2", True),
    ],
)
def test_a_version_range_is_read_as_npm_reads_it(version: str, wanted: str, held: bool) -> None:
    assert satisfies(version, wanted) is held


@pytest.mark.parametrize(("version", "wanted"), [("one", "^1.0"), ("1.0.0", "^banana")])
def test_a_version_or_range_that_cannot_be_read_says_so(version: str, wanted: str) -> None:
    with pytest.raises(VersionError):
        satisfies(version, wanted)


# -- dependencies --------------------------------------------------------------------------------


def test_dependencies_are_read_in_claude_code_s_shapes_and_shown() -> None:
    text = json.dumps(
        {
            "name": "video",
            "dependencies": [
                "render-kit",
                "audio@official",
                {"name": "fonts", "version": "~2.1.0", "marketplace": "official"},
            ],
        }
    )
    manifest = parse_manifest(text, set(), folder_name="video")
    assert manifest.dependencies == (
        Dependency("render-kit"),
        Dependency("audio", "", "official"),
        Dependency("fonts", "~2.1.0", "official"),
    )
    assert "Needs the plugins render-kit, audio, fonts ~2.1.0, turned on first." in (
        manifest.declaration()
    )


@pytest.mark.parametrize(
    ("dependencies", "problem"),
    [
        ("fonts", "dependencies is a list"),
        ([3], "a name or an object of text"),
        ([{"name": "fonts", "version": 2}], "a name or an object of text"),
        (["Fonts!"], "bad name"),
        (["video"], "cannot depend on itself"),
        ([{"name": "fonts", "version": "^banana"}], "the dependency fonts"),
    ],
)
def test_dependencies_written_wrong_refuse_the_plugin(dependencies: object, problem: str) -> None:
    text = json.dumps({"name": "video", "dependencies": dependencies})
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(text, set(), folder_name="video")


def test_a_plugin_runs_only_while_what_it_needs_runs(plugins: Plugins, home: Home) -> None:
    _plugin(home.plugins_folder, "fonts", version="2.1.4")
    _plugin(home.plugins_folder, "video", dependencies=[{"name": "fonts", "version": "~2.1.0"}])
    _plugin(home.plugins_folder, "editor", dependencies=["video"])
    with pytest.raises(PluginError, match="needs the plugin fonts, which is off or failed"):
        _on(plugins, "video")
    _on(plugins, "fonts")
    _on(plugins, "video")
    _on(plugins, "editor")
    assert all(view.running for view in plugins.all())
    assert set(plugins.skill_folders()) == {"fonts", "video", "editor"}

    with pytest.raises(PluginError, match="video needs fonts: turn it off first"):
        plugins.disable("fonts")

    # One that fails stops those that need it, and those that need them.
    plugins.failed("fonts", "It broke.")
    views = {view.name: view for view in plugins.all()}
    assert views["video"].problem == (
        "It needs the plugin fonts, which is off or failed: turn it on."
    )
    assert views["editor"].problem == (
        "It needs the plugin video, which is off or failed: turn it on."
    )
    assert set(plugins.skill_folders()) == set()
    plugins.clear_failure("fonts")
    assert set(plugins.skill_folders()) == {"fonts", "video", "editor"}

    # An update of what it needs past its range stops it, and says which it has.
    manifest_file = home.plugins_folder / "fonts" / ".claude-plugin" / "plugin.json"
    manifest_file.write_text(json.dumps({"name": "fonts", "version": "3.0.0"}), "utf-8")
    assert plugins.view("video").problem == ("It needs fonts ~2.1.0; the one installed is 3.0.0.")


def test_what_a_plugin_needs_that_is_not_there_is_said(plugins: Plugins, home: Home) -> None:
    _plugin(home.plugins_folder, "video", dependencies=["fonts"])
    with pytest.raises(PluginError, match="needs the plugin fonts, which is not installed"):
        _on(plugins, "video")
    folder = _plugin(home.plugins_folder, "fonts", version="")
    _plugin(home.plugins_folder, "subtitles", dependencies=[{"name": "fonts", "version": "^1"}])
    _on(plugins, "fonts")
    with pytest.raises(PluginError, match="the one installed is without a version"):
        _on(plugins, "subtitles")
    (folder / ".claude-plugin" / "plugin.json").write_text("{", "utf-8")
    with pytest.raises(PluginError, match="fonts, which is not installed"):
        _on(plugins, "video")


def test_what_a_plugin_needs_loads_before_it(plugins: Plugins, home: Home) -> None:
    log = home.root / "loaded.txt"
    code = (
        f"def register(ctx):\n    with open({str(log)!r}, 'a') as f:\n"
        "        f.write(__name__ + ' ')\n"
    )
    tools = {"api": 14, "tools": ["noop"]}
    _plugin(home.plugins_folder, "zeta", code=code, atlas=tools)
    _plugin(home.plugins_folder, "alpha", dependencies=["zeta"], code=code, atlas=tools)
    _plugin(home.plugins_folder, "beta", code=code, atlas=tools)
    for name in ("zeta", "alpha", "beta"):
        _on(plugins, name)
    asyncio.run(plugins.parts())
    loaded = log.read_text("utf-8").split()
    # The order: each after what it needs, ties by name.
    assert loaded == ["atlas_plugin_beta", "atlas_plugin_zeta", "atlas_plugin_alpha"]
    # Plugins that need each other in a circle keep their order.
    _plugin(home.plugins_folder, "one", dependencies=["two"])
    _plugin(home.plugins_folder, "two", dependencies=["one"])
    order = [view.name for view in load_order(plugins.all())]
    assert order.index("one") < order.index("two")


# -- the stamps the watch compares ---------------------------------------------------------------


def test_a_saved_file_changes_its_plugin_s_stamp_and_only_its(plugins: Plugins, home: Home) -> None:
    video = _plugin(home.plugins_folder, "video")
    _plugin(home.plugins_folder, "fonts")
    before = plugins.stamps()
    assert set(before) == {"video", "fonts"}
    assert plugins.stamps() == before, "nothing saved, nothing changed"

    (video / "node_modules").mkdir()
    (video / "node_modules" / "left-pad.js").write_text("x", "utf-8")
    (video / "__pycache__").mkdir()
    (video / "__pycache__" / "x.pyc").write_text("x", "utf-8")
    assert plugins.stamps() == before, "what programs write is not watched"

    skill = video / "skills" / "hello" / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"Say it twice.\n")
    after = plugins.stamps()
    assert after["video"] != before["video"]
    assert after["fonts"] == before["fonts"]

    _on(plugins, "fonts")
    assert plugins.stamps()["fonts"] != after["fonts"], "turned on: changed too"


# -- installing by address -----------------------------------------------------------------------


def _git(folder: Path, *arguments: str) -> str:
    assert GIT is not None
    done = subprocess.run(  # noqa: S603 - git, in a folder of the test's
        [GIT, "-c", "user.name=Test", "-c", "user.email=test@example.com", *arguments],
        cwd=folder,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def _repository(root: Path, *, inside: str = "") -> Path:
    """A git repository holding the plugin `video`, at its root or in its folder `inside`."""
    if inside:
        repository = root / "video-repo"
        _plugin(repository / inside, "video")
    else:
        # At the root of a repository named otherwise: its manifest names it.
        repository = _plugin(root, "video-repo")
        manifest = repository / ".claude-plugin" / "plugin.json"
        manifest.write_text(json.dumps({"name": "video", "version": "1.0.0"}), "utf-8")
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "First")
    return repository


@pytest.mark.parametrize(
    ("typed", "url", "subdir", "ref"),
    [
        ("owner/repo", "https://github.com/owner/repo.git", "", ""),
        ("owner/repo.git", "https://github.com/owner/repo.git", "", ""),
        ("owner/repo#plugins/video", "https://github.com/owner/repo.git", "plugins/video", ""),
        ("owner/repo/plugins/video", "https://github.com/owner/repo.git", "plugins/video", ""),
        ("https://gitlab.com/a/b.git#video", "https://gitlab.com/a/b.git", "video", ""),
        ("git@github.com:a/b.git", "git@github.com:a/b.git", "", ""),
        ("ssh://git@host/a/b.git", "ssh://git@host/a/b.git", "", ""),
        (
            "https://github.com/a/b/tree/v2/plugins/video",
            "https://github.com/a/b.git",
            "plugins/video",
            "v2",
        ),
        ("https://github.com/a/b/tree/main", "https://github.com/a/b.git", "", "main"),
    ],
)
def test_an_address_is_read_into_its_repository_folder_and_ref(
    typed: str, url: str, subdir: str, ref: str
) -> None:
    address = parse_address(typed)
    assert (address.url, address.subdir, address.ref, address.folder) == (url, subdir, ref, False)


@pytest.mark.parametrize("typed", ["", "   ", "just-a-name", "-/ x"])
def test_what_is_no_address_is_refused(typed: str) -> None:
    with pytest.raises(PluginError):
        parse_address(typed)


@needs_git
def test_a_plugin_is_installed_off_from_a_repository_and_updated_and_removed(
    plugins: Plugins, home: Home, tmp_path: Path
) -> None:
    repository = _repository(tmp_path)
    first = _git(repository, "rev-parse", "HEAD")
    found = install_plugin(plugins, str(repository))
    assert (found.name, found.folder) == ("video", home.plugins_folder / "video")
    assert not (found.folder / ".git").exists(), "the plugin, not the repository"
    view = plugins.view("video")
    assert view.manifest is not None
    assert not view.enabled, "it arrives off"
    record = installed(home.plugins_folder)["video"]
    assert (record.url, record.revision) == (str(repository.resolve()), first)

    found, changed = update_plugin(plugins, "video")
    assert not changed, "the same commit"

    _on(plugins, "video")
    (repository / "skills" / "hello" / "SKILL.md").write_bytes(
        b"---\nname: hello\ndescription: Say hello, louder.\n---\nHELLO.\n"
    )
    _git(repository, "commit", "-q", "-am", "Louder")
    found, changed = update_plugin(plugins, "video")
    assert changed
    assert b"HELLO" in (found.folder / "skills" / "hello" / "SKILL.md").read_bytes()
    assert plugins.view("video").running, "the same declaration keeps its consent"

    (repository / "bin").mkdir()
    (repository / "bin" / "render").write_text("echo render\n", "utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-q", "-m", "A program")
    update_plugin(plugins, "video")
    view = plugins.view("video")
    assert view.enabled
    assert not view.consented, "it declares more: consent again"

    with pytest.raises(PluginError, match="Update it with"):
        install_plugin(plugins, str(repository))
    install_plugin(plugins, str(repository), force=True)

    (plugins.data_folder("video")).mkdir(parents=True)
    data = remove_plugin(plugins, "video")
    assert data == home.plugin_data_folder / "video"
    assert data.is_dir(), "its data stays"
    assert not (home.plugins_folder / "video").exists()
    assert "video" not in installed(home.plugins_folder)
    assert not (home.plugins_folder / ".installing").exists() or not any(
        (home.plugins_folder / ".installing").iterdir()
    ), "nothing left half made"


@needs_git
def test_a_plugin_is_installed_from_a_folder_of_a_repository_at_a_commit(
    plugins: Plugins, home: Home, tmp_path: Path
) -> None:
    repository = _repository(tmp_path, inside="plugins")
    first = _git(repository, "rev-parse", "HEAD")
    manifest = repository / "plugins" / "video" / ".claude-plugin" / "plugin.json"
    manifest.write_text(json.dumps({"name": "video", "version": "2.0.0"}), "utf-8")
    _git(repository, "commit", "-q", "-am", "Two")
    found = install_plugin(plugins, f"{repository}#plugins/video", ref=first)
    assert found.manifest is not None
    assert found.manifest.version == "1.0.0", "the commit asked"
    record = installed(home.plugins_folder)["video"]
    assert (record.subdir, record.ref, record.revision) == ("plugins/video", first, first)
    found, changed = update_plugin(plugins, "video")
    assert not changed, "a reference given stays on update"
    assert found.manifest is not None
    assert found.manifest.version == "1.0.0"


@needs_git
@pytest.mark.parametrize(
    ("typed", "problem"),
    [
        ("{repo}#nowhere", "There is no folder nowhere"),
        ("{repo}#../..", "leaves the repository"),
        ("{repo}#skills", "holds no plugin Atlas loads"),
        ("{missing}", "is not an address|git failed"),
    ],
)
def test_an_install_that_cannot_be_made_leaves_nothing(
    plugins: Plugins, home: Home, tmp_path: Path, typed: str, problem: str
) -> None:
    repository = _repository(tmp_path)
    text = typed.format(repo=repository, missing="file:///" + str(tmp_path / "missing"))
    with pytest.raises(PluginError, match=problem):
        install_plugin(plugins, text)
    assert [p.name for p in home.plugins_folder.iterdir() if not p.name.startswith(".")] == []
    assert installed(home.plugins_folder) == {}


def test_a_folder_is_copied_and_a_folder_of_the_user_s_is_never_replaced_or_removed(
    plugins: Plugins, home: Home, tmp_path: Path
) -> None:
    source = _plugin(tmp_path / "elsewhere", "fonts")
    found = install_plugin(plugins, str(source))
    assert (found.folder / "skills" / "hello" / "SKILL.md").is_file()
    (source / "skills" / "hello" / "SKILL.md").write_bytes(
        b"---\nname: hello\ndescription: Changed.\n---\nChanged.\n"
    )
    assert update_plugin(plugins, "fonts")[1], "a folder is copied again"

    _plugin(home.plugins_folder, "mine")
    with pytest.raises(PluginError, match="It is yours: move it away first"):
        install_plugin(plugins, str(_plugin(tmp_path / "other", "mine")), force=True)
    with pytest.raises(PluginError, match="its folder is yours, delete it yourself"):
        remove_plugin(plugins, "mine")
    with pytest.raises(PluginError, match="nowhere to update it from"):
        update_plugin(plugins, "mine")
    assert (home.plugins_folder / "mine" / "skills").is_dir()


def test_the_terminal_installs_updates_and_removes(
    home: Home, tmp_path: Path, capsys: pytest.CaptureFixture[str], vault: Vault
) -> None:
    source = _plugin(tmp_path / "elsewhere", "fonts")
    environ = {"ATLAS_HOME": str(home.root)}
    assert main(["plugins", "install", str(source)], dict(environ)) == 0
    said = capsys.readouterr().out
    assert "fonts is installed in" in said
    assert "atlas plugins enable fonts" in said
    assert main(["plugins", "update", "fonts"], dict(environ)) == 0
    assert "fonts is up to date" not in capsys.readouterr().out, "a folder is copied again"
    assert main(["plugins", "remove", "fonts"], dict(environ)) == 0
    assert "fonts was removed. Its data stays in" in capsys.readouterr().out
    assert main(["plugins", "remove", "fonts"], dict(environ)) == 1
    assert "not installed by address" in capsys.readouterr().err
