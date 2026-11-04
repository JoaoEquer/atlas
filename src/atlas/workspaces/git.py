"""The git a workspace needs, run by the server, never the agent: its project found from any of its
folders, its worktree made, archived, brought back and removed, and what is left undelivered.

Every call is `git -C <folder>`, with no prompt and no editor, so nothing waits for a person. The
project's hooks never run for Atlas's own commit (the archive's): our choice, marked, as running
the project's code on Atlas's behalf is no part of keeping its work.
"""

import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from atlas.workspaces.errors import WorkspaceError

#: How long a git call may take; making a worktree checks out every file.
GIT_SECONDS = 120.0
#: The file of a project that names the files copied into a new workspace.
INCLUDE_FILE = ".worktreeinclude"


def run(folder: Path, *arguments: str, timeout: float = GIT_SECONDS) -> str:
    """What `git -C folder <arguments>` printed; raises `WorkspaceError` with git's last words."""
    git = shutil.which("git")
    if git is None:
        raise WorkspaceError("git is not installed here.")
    try:
        done = subprocess.run(  # noqa: S603 - git, on the user's own repository
            [git, "-C", str(folder), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_EDITOR": "true",
                "GIT_OPTIONAL_LOCKS": "0",
            },
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise WorkspaceError(f"git could not run: {error}") from error
    if done.returncode != 0:
        said = done.stderr.strip().splitlines()
        raise WorkspaceError(f"git failed: {said[-1] if said else f'exit {done.returncode}'}")
    return done.stdout


def project_of(folder: Path) -> tuple[str, str] | None:
    """The common git folder of `folder`'s repository and its main worktree's folder; `None`
    outside a repository; a file's is its folder's. Kept per folder: a folder does not change
    repository."""
    found = folder.resolve()
    return _project_of(str(found if found.is_dir() else found.parent))


@lru_cache(maxsize=512)
def _project_of(folder: str) -> tuple[str, str] | None:
    try:
        common = run(Path(folder), "rev-parse", "--path-format=absolute", "--git-common-dir")
    except WorkspaceError:
        return None
    common_dir = Path(common.strip()).resolve()
    # The main worktree is the folder that holds the common git folder, for a repository that is
    # not bare.
    root = common_dir.parent if common_dir.name == ".git" else common_dir
    return str(common_dir), str(root)


def current_branch(folder: Path) -> str:
    """The branch checked out in `folder`; raises `WorkspaceError` on a detached head."""
    branch = run(folder, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch == "HEAD":
        raise WorkspaceError(f"{folder} has no branch checked out: check one out first.")
    return branch


def branch_exists(folder: Path, branch: str) -> bool:
    try:
        run(folder, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    except WorkspaceError:
        return False
    return True


def add_worktree(root: Path, target: Path, branch: str, base: str | None) -> None:
    """A worktree at `target`: on a new `branch` from `base`, or on `branch` as it is."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if base is None:
        run(root, "worktree", "add", str(target), branch)
    else:
        run(root, "worktree", "add", "-b", branch, str(target), base)


def remove_worktree(root: Path, target: Path) -> None:
    """The worktree at `target` gone, its folder too, whatever it held; the branch stays."""
    run(root, "worktree", "remove", "--force", str(target))
    run(root, "worktree", "prune")


def copy_included(root: Path, target: Path) -> list[str]:
    """The files the project's `.worktreeinclude` lists, copied from `root` into `target`, once;
    the ones copied. A line is a path under the project, `#` a comment; a path that leaves the
    project, or is missing, is skipped."""
    listed = root / INCLUDE_FILE
    try:
        lines = listed.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    copied: list[str] = []
    top = root.resolve()
    for line in lines:
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        source = (root / name).resolve()
        if not source.is_relative_to(top) or not source.is_file():
            continue
        relative = source.relative_to(top)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative.as_posix())
    return copied


def changed(folder: Path) -> bool:
    """Whether `folder` holds changes not committed, untracked files included."""
    return bool(run(folder, "status", "--porcelain").strip())


def commit_all(folder: Path, message: str) -> bool:
    """Every change in `folder` committed with `message`, the project's hooks left out; whether
    there was any."""
    if not changed(folder):
        return False
    run(folder, "add", "--all")
    run(
        folder,
        "-c",
        "user.name=Atlas",
        "-c",
        "user.email=atlas@localhost",
        "commit",
        "--no-verify",
        "--quiet",
        "--message",
        message,
    )
    return True


def last_message(folder: Path) -> str:
    return run(folder, "log", "-1", "--format=%B").strip()


def undo_last_commit(folder: Path) -> None:
    """The last commit undone, its changes back in the folder as they were before it, unstaged."""
    run(folder, "reset", "--quiet", "HEAD~1")


def ahead(folder: Path, base: str, branch: str) -> int:
    """How many commits `branch` has that `base` has not."""
    try:
        return int(run(folder, "rev-list", "--count", f"{base}..{branch}").strip() or 0)
    except (WorkspaceError, ValueError):
        return 0


def delete_branch(root: Path, branch: str) -> None:
    run(root, "branch", "-D", branch)
