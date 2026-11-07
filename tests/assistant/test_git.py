"""A project folder's git and a new worktree of it (step 18g), on a real repository."""

import shutil
import subprocess
from pathlib import Path

import pytest

from atlas.assistant.git import git_state

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
