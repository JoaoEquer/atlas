"""Delivering a workspace (step 21, A4, the core's part), against real git: its work committed as
the user's own, merged into its branch of origin where that branch is checked out or where none
is, refused with its reason when it cannot be clean, and its branch sent to the project's remote."""

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


@pytest.fixture(autouse=True)
def identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """The user's name in git, which their commits take."""
    for name, value in IDENTITY.items():
        monkeypatch.setenv(name, value)


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
    _git(folder, "init", "--quiet", "--initial-branch", "main")
    _git(folder, "add", "--all")
    _git(folder, "commit", "--quiet", "--message", "The shop")
    return folder


def test_a_workspace_is_committed_and_merged_where_its_branch_of_origin_is_checked_out(
    workspaces: Workspaces, project: Path
) -> None:
    made = workspaces.create(project, "cart")
    folder = Path(made.folder)
    (folder / "cart.py").write_text("TOTAL = 0\n", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="not committed: commit them first"):
        workspaces.merge(made.id)
    assert workspaces.status(made.id) == (True, 0)
    workspaces.commit(made.id, "Add the cart")
    assert workspaces.status(made.id) == (False, 1)
    with pytest.raises(WorkspaceError, match="nothing to commit"):
        workspaces.commit(made.id, "Again")
    (project / "app.py").write_text("print('mine')\n", encoding="utf-8")
    with pytest.raises(WorkspaceError, match="changes of its own on main"):
        workspaces.merge(made.id)
    (project / "app.py").write_text("print('shop')\n", encoding="utf-8")

    workspaces.merge(made.id)

    assert (project / "cart.py").read_text(encoding="utf-8") == "TOTAL = 0\n"
    merged = _git(project, "log", "-1", "--format=%s").strip()
    assert merged == "Merge branch 'atlas/cart' into main"
    assert _git(project, "log", "-1", "--format=%an", "HEAD^2").strip() == "Ana", "the user's own"
    assert not workspaces.undelivered(made.id)
    with pytest.raises(WorkspaceError, match="has all of atlas/cart already"):
        workspaces.merge(made.id)


def test_a_branch_of_origin_checked_out_nowhere_is_merged_without_a_folder(
    workspaces: Workspaces, project: Path
) -> None:
    made = workspaces.create(project, "cart")
    _git(project, "switch", "--quiet", "--create", "elsewhere")
    (Path(made.folder) / "cart.py").write_text("TOTAL = 0\n", encoding="utf-8")
    workspaces.commit(made.id, "Add the cart")

    workspaces.merge(made.id)

    assert "cart.py" in _git(project, "ls-tree", "--name-only", "main")
    assert not (project / "cart.py").exists(), "the folder, on another branch, untouched"
    assert _git(project, "rev-parse", "--abbrev-ref", "HEAD").strip() == "elsewhere"


def test_a_conflict_leaves_everything_as_it_was(workspaces: Workspaces, project: Path) -> None:
    made = workspaces.create(project, "cart")
    (Path(made.folder) / "app.py").write_text("print('cart')\n", encoding="utf-8")
    workspaces.commit(made.id, "Cart")
    (project / "app.py").write_text("print('main')\n", encoding="utf-8")
    _git(project, "commit", "--quiet", "--all", "--message", "Main")
    head = _git(project, "rev-parse", "HEAD")

    with pytest.raises(WorkspaceError, match="does not merge into main cleanly"):
        workspaces.merge(made.id)

    assert _git(project, "rev-parse", "HEAD") == head
    assert _git(project, "status", "--porcelain").strip() == ""


def test_its_branch_is_sent_to_the_projects_remote(
    workspaces: Workspaces, project: Path, tmp_path: Path
) -> None:
    made = workspaces.create(project, "cart")
    with pytest.raises(WorkspaceError, match="no remote named origin"):
        workspaces.push(made.id)
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(remote))
    _git(project, "remote", "add", "origin", str(remote))
    (Path(made.folder) / "cart.py").write_text("TOTAL = 0\n", encoding="utf-8")
    workspaces.commit(made.id, "Add the cart")

    workspaces.push(made.id)

    assert _git(remote, "log", "-1", "--format=%s", "atlas/cart").strip() == "Add the cart"
    assert _git(Path(made.folder), "rev-parse", "--abbrev-ref", "@{upstream}").strip() == (
        "origin/atlas/cart"
    )
