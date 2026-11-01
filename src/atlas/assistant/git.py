"""A project folder's git, as an executor's session shows it (step 18g): the branch it is on, the
repository it belongs to, and whether it is a worktree of another; and a new worktree, where a
session works apart from the folder it was opened on.

A worktree goes beside the repository, `<repository>-<name>` on a new branch `atlas/<name>` from
the one checked out, as `git worktree add` makes it: Atlas never checks out, resets or deletes
anything in the user's folder.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from atlas.plugins import ExecutorError

#: Longest a git call may take: they all read local files.
GIT_SECONDS = 15.0
#: A worktree's name, as the branch and the folder take it.
_NAME = re.compile(r"[^a-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class GitState:
    """A folder's git; `repository` false, and the rest empty, outside any."""

    repository: bool
    branch: str = ""
    #: The repository's top folder, the worktree's own for a worktree.
    root: str = ""
    #: The folder is a worktree of another repository's folder.
    worktree: bool = False


def git_state(folder: Path) -> GitState:
    """What git says of `folder`; outside a repository, or with no git, nothing."""
    try:
        top, branch, own, common = _git(
            folder,
            "rev-parse",
            "--show-toplevel",
            "--abbrev-ref",
            "HEAD",
            "--absolute-git-dir",
            "--git-common-dir",
        ).splitlines()[:4]
    except (ExecutorError, ValueError):
        return GitState(repository=False)
    if branch == "HEAD":
        # A detached head is named by its commit.
        branch = _git(folder, "rev-parse", "--short", "HEAD").strip()
    shared = Path(common) if Path(common).is_absolute() else folder / common
    return GitState(
        repository=True,
        branch=branch,
        root=str(Path(top)),
        worktree=Path(own).resolve() != shared.resolve(),
    )


def add_worktree(folder: Path, name: str) -> Path:
    """A new worktree of `folder`'s repository, beside it, on a new branch from the one checked
    out. Raises `ExecutorError` outside a repository, or when the folder or branch exists."""
    state = git_state(folder)
    if not state.repository:
        raise ExecutorError(f"{folder} is not in a git repository.")
    slug = _NAME.sub("-", name.strip().lower()).strip("-.")
    if not slug:
        raise ExecutorError("A worktree needs a name.")
    root = Path(state.root)
    target = root.parent / f"{root.name}-{slug}"
    if target.exists():
        raise ExecutorError(f"{target} already exists.")
    _git(root, "worktree", "add", "-b", f"atlas/{slug}", str(target))
    return target


def _git(folder: Path, *arguments: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise ExecutorError("git is not installed here.")
    try:
        done = subprocess.run(  # noqa: S603 - git, on the user's own folder
            [git, "-C", str(folder), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_SECONDS,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ExecutorError(f"git could not run: {error}") from error
    if done.returncode != 0:
        said = done.stderr.strip().splitlines()
        raise ExecutorError(f"git failed: {said[-1] if said else f'exit {done.returncode}'}")
    return done.stdout
