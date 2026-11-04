"""A workspace's checkpoints (step 21, A3), against real git: its folder kept before each prompt
without touching its branch, its index or its files, and put back as it was, what it held kept
first, so going back loses nothing and can itself be undone."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Home, migrate
from atlas.workspaces import WorkspaceError, Workspaces

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")

IDENTITY = {
    "GIT_AUTHOR_NAME": "Ana",
    "GIT_AUTHOR_EMAIL": "ana@example.com",
    "GIT_COMMITTER_NAME": "Ana",
    "GIT_COMMITTER_EMAIL": "ana@example.com",
}


def _git(folder: Path, *arguments: str) -> str:
    return subprocess.run(  # noqa: S603 - the system's git, on a test's own repository
        ["git", "-C", str(folder), *arguments],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **IDENTITY},
    ).stdout


@pytest.fixture
def workspaces(home: Home) -> Workspaces:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Workspaces(home.worktrees_folder, database)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    folder = tmp_path / "shop"
    folder.mkdir()
    (folder / "app.py").write_text("print('shop')\n", encoding="utf-8")
    (folder / ".gitignore").write_text(".env\n", encoding="utf-8")
    _git(folder, "init", "--quiet", "--initial-branch", "main")
    _git(folder, "add", "--all")
    _git(folder, "commit", "--quiet", "--message", "The shop")
    return folder


def test_a_checkpoint_keeps_the_folder_and_touches_nothing(
    workspaces: Workspaces, project: Path
) -> None:
    made = workspaces.create(project, "cart")
    folder = Path(made.folder)
    (folder / "app.py").write_text("print('cart')\n", encoding="utf-8")
    (folder / "cart.py").write_text("TOTAL = 0\n", encoding="utf-8")
    status = _git(folder, "status", "--porcelain")
    head = _git(folder, "rev-parse", "HEAD")

    first = workspaces.checkpoint(made.id, "Add the  cart.\n")
    again = workspaces.checkpoint(made.id, "Nothing changed since.")

    assert first is not None
    assert first.label == "Add the  cart.\n"
    assert again is None, "the same folder twice is one checkpoint"
    assert _git(folder, "status", "--porcelain") == status, "its index and files as they were"
    assert _git(folder, "rev-parse", "HEAD") == head, "its branch as it was"
    [kept] = workspaces.checkpoints(made.id)
    assert (kept.commit, kept.label) == (first.commit, "Add the cart.")
    assert "cart.py" in _git(folder, "show", "--name-only", "--format=", kept.commit)
    assert "refs/atlas/checkpoints/" in _git(project, "for-each-ref", "refs/atlas/")
    assert not _git(project, "branch", "--list", "--all", "*checkpoint*").strip()


def test_going_back_puts_the_files_back_and_keeps_what_was_there(
    workspaces: Workspaces, project: Path
) -> None:
    made = workspaces.create(project, "cart")
    folder = Path(made.folder)
    (folder / ".env").write_text("TOKEN=example\n", encoding="utf-8")
    (folder / "app.py").write_text("print('cart, first')\n", encoding="utf-8")
    before = workspaces.checkpoint(made.id, "Make it faster.")
    assert before is not None
    # What the agent did after: an edit, a file of its own, a commit, a file removed.
    (folder / "app.py").write_text("print('cart, faster')\n", encoding="utf-8")
    (folder / "cache.py").write_text("CACHE = {}\n", encoding="utf-8")
    _git(folder, "add", "--all")
    _git(folder, "commit", "--quiet", "--message", "Faster")
    (folder / ".gitignore").unlink()

    workspaces.restore(made.id, before.commit)

    assert (folder / "app.py").read_text(encoding="utf-8") == "print('cart, first')\n"
    assert not (folder / "cache.py").exists()
    assert (folder / ".gitignore").exists()
    assert (folder / ".env").read_text(encoding="utf-8") == "TOKEN=example\n", "ignored, kept"
    assert _git(folder, "log", "-1", "--format=%s").strip() == "The shop", "the branch went back"
    assert _git(folder, "status", "--porcelain").strip() == "M app.py", "unstaged, as it was"
    newest, older = workspaces.checkpoints(made.id)
    assert newest.label == "Before going back to an earlier checkpoint"
    assert older.commit == before.commit

    workspaces.restore(made.id, newest.commit)

    assert (folder / "app.py").read_text(encoding="utf-8") == "print('cart, faster')\n"
    assert (folder / "cache.py").exists(), "going back is undone, nothing lost"
    assert _git(folder, "log", "-1", "--format=%s").strip() == "Faster"


def test_only_its_own_checkpoints_and_none_left_once_deleted(
    workspaces: Workspaces, project: Path
) -> None:
    one = workspaces.create(project, "one")
    two = workspaces.create(project, "two")
    (Path(two.folder) / "app.py").write_text("print('two')\n", encoding="utf-8")
    kept = workspaces.checkpoint(two.id, "Two.")
    assert kept is not None

    with pytest.raises(WorkspaceError, match="has no checkpoint"):
        workspaces.restore(one.id, kept.commit)
    workspaces.delete(two.id, force=True)

    assert workspaces.checkpoints(one.id) == []
    assert not _git(project, "for-each-ref", "refs/atlas/").strip()
