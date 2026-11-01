"""A project folder's git and a new worktree of it (step 18g), on a real repository."""

import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.assistant.git import add_worktree, git_state
from atlas.plugins import ExecutorError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _repository(folder: Path) -> Path:
    folder.mkdir()

    def git(*arguments: str) -> None:
        subprocess.run(["git", "-C", str(folder), *arguments], check=True, capture_output=True)  # noqa: S603, S607

    git("init", "-b", "main")
    git("config", "user.email", "someone@example.com")
    git("config", "user.name", "Someone")
    (folder / "README.md").write_text("Hello.\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "First")
    return folder


def test_a_folder_says_its_branch_and_that_it_is_no_worktree(tmp_path: Path) -> None:
    site = _repository(tmp_path / "site")
    (site / "src").mkdir()

    state = git_state(site / "src")

    assert state.repository
    assert state.branch == "main"
    assert Path(state.root) == site.resolve()
    assert not state.worktree


def test_a_folder_outside_any_repository_says_nothing(tmp_path: Path) -> None:
    assert not git_state(tmp_path).repository


def test_a_worktree_goes_beside_the_repository_on_a_branch_of_its_own(tmp_path: Path) -> None:
    site = _repository(tmp_path / "site")

    made = add_worktree(site, "1101-1832")

    assert made == site.resolve().parent / "site-1101-1832"
    state = git_state(made)
    assert state.worktree
    assert state.branch == "atlas/1101-1832"
    assert (made / "README.md").read_text(encoding="utf-8") == "Hello.\n"
    # The folder it came from is as it was.
    assert git_state(site).branch == "main"
    with pytest.raises(ExecutorError, match="already exists"):
        add_worktree(site, "1101-1832")


def test_a_worktree_needs_a_repository_and_a_name(tmp_path: Path) -> None:
    with pytest.raises(ExecutorError, match="not in a git repository"):
        add_worktree(tmp_path, "one")
    with pytest.raises(ExecutorError, match="needs a name"):
        add_worktree(_repository(tmp_path / "site"), "  ..  ")
