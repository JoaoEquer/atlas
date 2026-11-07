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


def run(
    folder: Path,
    *arguments: str,
    timeout: float = GIT_SECONDS,
    environment: dict[str, str] | None = None,
) -> str:
    """What `git -C folder <arguments>` printed, `environment` added to the process's; raises
    `WorkspaceError` with git's last words."""
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
                **(environment or {}),
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


def summary(folder: Path, base: str) -> str:
    """What `folder` changed since it left `base`, in a line: its commits, its files changed,
    committed or not, and its new files git does not track yet."""
    start = run(folder, "merge-base", base, "HEAD").strip()
    parts: list[str] = []
    commits = ahead(folder, base, "HEAD")
    if commits:
        parts.append(f"{commits} commit{'s' if commits != 1 else ''} of its own")
    shortstat = run(folder, "diff", "--shortstat", start).strip()
    if shortstat:
        parts.append(shortstat)
    new = [line for line in run(folder, "ls-files", "--others", "--exclude-standard").splitlines()]
    if new:
        parts.append(f"{len(new)} new file{'s' if len(new) != 1 else ''} not yet committed")
    return "; ".join(parts) or "nothing changed"


#: Where a workspace's checkpoints are kept: refs of Atlas's own, which a push leaves out.
CHECKPOINTS = "refs/atlas/checkpoints"
_ATLAS = ("-c", "user.name=Atlas", "-c", "user.email=atlas@localhost")


def snapshot(folder: Path, message: str) -> tuple[str, str]:
    """Everything in `folder` now, untracked files too but not the ignored ones, as a commit on top
    of its HEAD that no branch holds: its id and its tree's. The folder, its index and its branch
    are left as they were (a throwaway index does the adding)."""
    marker = run(folder, "rev-parse", "--git-path", "atlas-checkpoint-index").strip()
    scratch = Path(marker) if Path(marker).is_absolute() else folder / marker
    environment = {"GIT_INDEX_FILE": str(scratch)}
    try:
        run(folder, "read-tree", "HEAD", environment=environment)
        run(folder, "add", "--all", environment=environment)
        tree = run(folder, "write-tree", environment=environment).strip()
    finally:
        scratch.unlink(missing_ok=True)
    commit = run(folder, *_ATLAS, "commit-tree", tree, "-p", "HEAD", "-m", message).strip()
    return commit, tree


def keep_checkpoint(folder: Path, name: str, commit: str) -> None:
    run(folder, "update-ref", f"{CHECKPOINTS}/{name}", commit)


def checkpoints(folder: Path, prefix: str) -> list[tuple[str, str, str, str]]:
    """The checkpoints under `prefix`, the newest first: each its ref, its commit, its tree and
    its message's first line."""
    listed = run(
        folder,
        "for-each-ref",
        "--sort=-refname",
        "--format=%(refname)%00%(objectname)%00%(tree)%00%(contents:subject)",
        f"{CHECKPOINTS}/{prefix}/",
    )
    found: list[tuple[str, str, str, str]] = []
    for line in listed.splitlines():
        parts = line.split("\0")
        if len(parts) == 4:
            found.append((parts[0], parts[1], parts[2], parts[3]))
    return found


def drop_checkpoints(folder: Path, prefix: str) -> None:
    for ref, *_ in checkpoints(folder, prefix):
        run(folder, "update-ref", "-d", ref)


def restore(folder: Path, commit: str) -> None:
    """`folder` as it was at the checkpoint `commit`: its files, its branch back on the commit
    the checkpoint was made on, its changes since then unstaged. Files made since are removed,
    the ignored ones kept; whoever calls has kept a checkpoint of now first."""
    run(folder, "read-tree", "-u", "--reset", f"{commit}^{{tree}}")
    run(folder, "clean", "-d", "--force", "--quiet")
    run(folder, "reset", "--quiet", f"{commit}^")


#: How long a push may take, over the network.
PUSH_SECONDS = 300.0


def commit(folder: Path, message: str) -> None:
    """Every change in `folder` committed as the user's own: their name from their git, the
    project's hooks run, as a commit they typed would."""
    run(folder, "add", "--all")
    run(folder, "commit", "--quiet", "--message", message)


def checked_out(root: Path, branch: str) -> Path | None:
    """The worktree `branch` is checked out in, if any."""
    folder: Path | None = None
    for line in run(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            folder = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}" and folder is not None:
            return folder
    return None


def merge(root: Path, base: str, branch: str) -> None:
    """`branch` merged into `base`, a merge commit of its own. Where `base` is checked out, the
    merge happens there, refused when that folder holds changes of its own; elsewhere git merges
    without a folder. Conflicts leave everything as it was, said."""
    message = f"Merge branch '{branch}' into {base}"
    where = checked_out(root, base)
    if where is not None:
        if run(where, "status", "--porcelain", "--untracked-files=no").strip():
            raise WorkspaceError(
                f"{where} has changes of its own on {base}: commit or put them away first."
            )
        try:
            run(where, "merge", "--no-ff", "--no-edit", "--message", message, branch)
        except WorkspaceError as error:
            run(where, "merge", "--abort")
            raise WorkspaceError(
                f"{branch} does not merge into {base} cleanly: ask the agent to merge {base} into "
                "its branch and settle the conflicts, then deliver again."
            ) from error
        return
    try:
        tree = run(root, "merge-tree", "--write-tree", base, branch).splitlines()[0].strip()
    except WorkspaceError as error:
        raise WorkspaceError(
            f"{branch} does not merge into {base} cleanly: ask the agent to merge {base} into its "
            "branch and settle the conflicts, then deliver again."
        ) from error
    old = run(root, "rev-parse", f"refs/heads/{base}").strip()
    made = run(root, "commit-tree", tree, "-p", old, "-p", branch, "-m", message).strip()
    run(root, "update-ref", f"refs/heads/{base}", made, old)


def push(folder: Path, branch: str) -> str:
    """`branch` sent to the remote `origin`, set as where it goes from now on; what git said."""
    if "origin" not in run(folder, "remote").split():
        raise WorkspaceError("The project has no remote named origin to send the branch to.")
    done = run(folder, "push", "--set-upstream", "origin", branch, timeout=PUSH_SECONDS)
    return done.strip()


def delete_branch(root: Path, branch: str) -> None:
    run(root, "branch", "-D", branch)
