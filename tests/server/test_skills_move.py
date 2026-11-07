"""Step 26 on a home that had the skills Atlas shipped for GitHub and Google Workspace: their
plugins come installed and on, the untouched copies leave the library for its archive, and a copy
the user changed stays theirs."""

import shutil

from atlas.home import Home
from atlas.plugins import OFFICIAL_FOLDER
from atlas.skills import files_digest, read_manifest, skill_files
from server_support import Serve


def _shipped(home: Home, *names: str, changed: str = "") -> None:
    """The home as Atlas left it before step 26: each skill copied, and its hash kept."""
    skills = home.skills_folder
    lines: list[str] = []
    for name in names:
        plugin = "github" if name == "github" else "google"
        source = OFFICIAL_FOLDER / plugin / "skills" / name
        shutil.copytree(source, skills / name)
        lines.append(f"{name}:{files_digest(skill_files(source))}\n")
        if name == changed:
            (skills / name / "SKILL.md").write_text(
                (source / "SKILL.md").read_text("utf-8") + "\nMy own note.\n", "utf-8"
            )
    (skills / ".bundled_manifest").write_text("".join(lines), "utf-8")


def test_a_home_that_had_the_shipped_skills_gets_their_plugins_on(serve: Serve, home: Home) -> None:
    _shipped(home, "github", "gws-gmail", "gws-shared", changed="gws-gmail")

    with serve().client() as client:
        rows = {row["name"]: row for row in client.result("plugin.list")["plugins"]}

    assert (rows["github"]["enabled"], rows["github"]["consented"]) == (True, True)
    assert (rows["google"]["enabled"], rows["google"]["consented"]) == (True, True)
    archive = home.skills_folder / ".archive"
    assert sorted(path.name for path in archive.iterdir()) == ["github", "gws-shared"]
    assert "My own note." in (home.skills_folder / "gws-gmail" / "SKILL.md").read_text("utf-8")
    assert read_manifest(home.skills_folder) == {}, "none is Atlas's to update any more"


def test_a_home_without_them_gets_no_plugin(serve: Serve, home: Home) -> None:
    with serve().client() as client:
        rows = {row["name"]: row for row in client.result("plugin.list")["plugins"]}

    assert not rows["github"]["enabled"]
    assert not rows["google"]["enabled"]


def test_the_skills_come_from_the_plugins_now() -> None:
    inside = OFFICIAL_FOLDER.parent / "src" / "atlas" / "skills" / "bundled"
    shipped = {path.name for path in inside.iterdir() if not path.name.startswith("__")}
    assert shipped == {"atlas", "write-plugin"}, "Atlas's own skills stay inside it"
    assert (OFFICIAL_FOLDER / "github" / "skills" / "github" / "SKILL.md").exists()
    assert (OFFICIAL_FOLDER / "google" / "skills" / "gws-shared" / "SKILL.md").exists()
