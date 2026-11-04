"""Workspaces (step 21, A1) against real git repositories: made from any folder of a project, the
listed files copied, prepared by the project's script with its ports, archived with nothing lost
and brought back as they were, and deleted only with the user's word when work is undelivered."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Home, migrate
from atlas.workspaces import WorkspaceError, Workspaces, WorkspaceState

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="git is not installed",
)

ARCHIVED = "Atlas: the work of an archived workspace, kept until it is unarchived"


def _git(folder: Path, *arguments: str) -> str:
    done = subprocess.run(  # noqa: S603 - the system's git, on a test's own repository
        ["git", "-C", str(folder), *arguments],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Ana",
            "GIT_AUTHOR_EMAIL": "ana@example.com",
            "GIT_COMMITTER_NAME": "Ana",
            "GIT_COMMITTER_EMAIL": "ana@example.com",
        },
    )
    return done.stdout


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """A project on `main`, with an ignored `.env` its `.worktreeinclude` lists."""
    folder = tmp_path / "shop"
    folder.mkdir()
    _git(folder, "init", "--quiet", "--initial-branch", "main")
    (folder / "app.py").write_text("print('shop')\n", encoding="utf-8")
    (folder / ".gitignore").write_text(".env\n", encoding="utf-8")
    (folder / ".worktreeinclude").write_text(
        "# the secrets a copy needs\n.env\n../outside.txt\nmissing.txt\n", encoding="utf-8"
    )
    (folder / ".env").write_text("TOKEN=example\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("not the project's\n", encoding="utf-8")
    _git(folder, "add", "--all")
    _git(folder, "commit", "--quiet", "--message", "The shop")
    return folder


@pytest.fixture
def workspaces(home: Home) -> Workspaces:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Workspaces(home.worktrees_folder, database)


def test_a_workspace_is_a_worktree_in_the_home_on_a_branch_of_its_own(
    workspaces: Workspaces, repository: Path, home: Home
) -> None:
    made = workspaces.create(repository / "app.py", "Fix the login!")

    folder = Path(made.folder)
    assert folder.parent.parent == home.worktrees_folder
    assert (made.name, made.branch, made.base, made.state) == (
        "fix-the-login",
        "atlas/fix-the-login",
        "main",
        WorkspaceState.READY,
    )
    assert _git(folder, "rev-parse", "--abbrev-ref", "HEAD").strip() == "atlas/fix-the-login"
    assert (folder / ".env").read_text(encoding="utf-8") == "TOKEN=example\n", "copied, once"
    assert not (folder.parent / "outside.txt").exists(), "nothing from outside the project"
    assert _git(repository, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main", "untouched"
    # Its own folder belongs to the same project, as any worktree of it does.
    assert [w.id for w in workspaces.listed(folder)] == [made.id]
    assert workspaces.listed(repository) == workspaces.listed()


def test_a_name_a_branch_or_a_folder_that_cannot_be_is_refused(
    workspaces: Workspaces, repository: Path, tmp_path: Path
) -> None:
    workspaces.create(repository, "checkout")

    with pytest.raises(WorkspaceError, match="needs a name"):
        workspaces.create(repository, " !! ")
    with pytest.raises(WorkspaceError, match="branch atlas/checkout exists"):
        workspaces.create(repository, "Checkout")
    plain = tmp_path / "notes"
    plain.mkdir()
    with pytest.raises(WorkspaceError, match="not in a git repository"):
        workspaces.create(plain, "anything")


def test_the_projects_script_prepares_each_workspace_with_ports_of_its_own(
    workspaces: Workspaces, repository: Path
) -> None:
    script = f'"{sys.executable}" -c "import os; print(os.environ[\'ATLAS_WORKSPACE_PORTS\'])"'
    workspaces.set_project(repository, setup_script=script, ports_start=4000, ports_size=5)

    first = workspaces.create(repository, "one")
    second = workspaces.create(repository, "two")
    assert (first.state, second.slot) == (WorkspaceState.PREPARING, 1)
    ready = workspaces.prepare(second.id)

    assert ready.state is WorkspaceState.READY
    assert ready.setup_output.strip() == "4005-4009"
    assert workspaces.environment(first)["ATLAS_WORKSPACE_PORT"] == "4000"
    workspaces.set_project(
        repository, setup_script=f'"{sys.executable}" -c "exit(3)"', ports_start=0, ports_size=0
    )
    failed = workspaces.prepare(first.id)
    assert failed.state is WorkspaceState.SETUP_FAILED
    assert "ATLAS_WORKSPACE_PORT" not in workspaces.environment(first)
    with pytest.raises(WorkspaceError, match="past 65535"):
        workspaces.set_project(repository, setup_script="", ports_start=65530, ports_size=10)


def test_an_archived_workspace_keeps_its_work_and_comes_back_as_it_was(
    workspaces: Workspaces, repository: Path
) -> None:
    made = workspaces.create(repository, "new-page")
    folder = Path(made.folder)
    (folder / "app.py").write_text("print('changed')\n", encoding="utf-8")
    (folder / "page.py").write_text("print('new')\n", encoding="utf-8")

    archived = workspaces.archive(made.id)

    assert (archived.state, archived.archived_commit) == (WorkspaceState.ARCHIVED, True)
    assert not folder.exists()
    assert _git(repository, "log", "-1", "--format=%B", "atlas/new-page").strip() == ARCHIVED
    assert _git(repository, "status", "--porcelain").strip() == "", "the project untouched"

    back = workspaces.unarchive(made.id)

    assert back.state is WorkspaceState.READY
    assert (folder / "app.py").read_text(encoding="utf-8") == "print('changed')\n"
    status = _git(folder, "status", "--porcelain")
    assert " M app.py" in status
    assert "?? page.py" in status, "a new file is new again, not committed"
    assert _git(folder, "log", "-1", "--format=%B").strip() == "The shop"


def test_undelivered_work_is_deleted_only_with_the_users_word(
    workspaces: Workspaces, repository: Path
) -> None:
    clean = workspaces.create(repository, "clean")
    busy = workspaces.create(repository, "busy")
    (Path(busy.folder) / "app.py").write_text("print('half done')\n", encoding="utf-8")

    workspaces.delete(clean.id)
    with pytest.raises(WorkspaceError, match="holds work not delivered"):
        workspaces.delete(busy.id)
    workspaces.delete(busy.id, force=True)

    assert workspaces.listed() == []
    assert not Path(busy.folder).exists()
    assert "atlas/busy" not in _git(repository, "branch", "--list")
    with pytest.raises(WorkspaceError, match="no workspace"):
        workspaces.get(busy.id)
