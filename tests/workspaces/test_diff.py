"""What a workspace changed, line by line (step 21, A2), from real git: its edits committed or not,
its new files whole, each line numbered as the file has it, so a comment names the right line."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Home, migrate
from atlas.workspaces import Workspaces

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

IDENTITY = {
    "GIT_AUTHOR_NAME": "Ana",
    "GIT_AUTHOR_EMAIL": "ana@example.com",
    "GIT_COMMITTER_NAME": "Ana",
    "GIT_COMMITTER_EMAIL": "ana@example.com",
}


def _git(folder: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - the system's git, on a test's own repository
        ["git", "-C", str(folder), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        env={**os.environ, **IDENTITY},
    )


def test_a_workspaces_changes_are_its_files_with_each_line_numbered(
    home: Home, tmp_path: Path
) -> None:
    project = tmp_path / "shop"
    project.mkdir()
    lines = [f"line {n}" for n in range(1, 21)]
    (project / "app.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (project / "old.txt").write_text("going\n", encoding="utf-8")
    (project / "logo.png").write_bytes(b"\x89PNG\0\0")
    _git(project, "init", "--quiet", "--initial-branch", "main")
    _git(project, "add", "--all")
    _git(project, "commit", "--quiet", "--message", "The shop")
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    workspaces = Workspaces(home.worktrees_folder, database)
    made = workspaces.create(project, "edit")
    folder = Path(made.folder)

    changed = [*lines]
    changed[1] = "line two, committed"
    changed[16] = "line seventeen, not committed"
    (folder / "app.py").write_text("\n".join(changed[:2] + lines[2:]) + "\n", encoding="utf-8")
    (folder / "old.txt").unlink()
    _git(folder, "add", "--all")
    _git(folder, "commit", "--quiet", "--message", "Two")
    (folder / "app.py").write_text("\n".join(changed) + "\n", encoding="utf-8")
    (folder / "notes.md").write_text("# Notes\nFirst.\n", encoding="utf-8")
    (folder / "logo.png").write_bytes(b"\x89PNG\0\1")

    files, more = workspaces.diff(made.id)

    assert not more
    assert [(f.path, f.status) for f in files] == [
        ("app.py", "modified"),
        ("logo.png", "modified"),
        ("notes.md", "added"),
        ("old.txt", "deleted"),
    ]
    app, logo, notes, old = files
    assert (app.added, app.removed) == (2, 2)
    plus = [(line.new, line.text) for line in app.lines if line.kind == "plus"]
    assert plus == [(2, "line two, committed"), (17, "line seventeen, not committed")]
    minus = [(line.old, line.text) for line in app.lines if line.kind == "minus"]
    assert minus == [(2, "line 2"), (17, "line 17")]
    assert [line.kind for line in app.lines].count("gap") == 1, "two hunks, apart"
    same = next(line for line in app.lines if line.text == "line 18")
    assert (same.old, same.new) == (18, 18)
    assert logo.binary
    assert [(line.new, line.text) for line in notes.lines] == [(1, "# Notes"), (2, "First.")]
    assert (old.removed, old.lines[0].text) == (1, "going")
