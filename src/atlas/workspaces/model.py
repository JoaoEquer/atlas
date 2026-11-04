"""Workspaces (step 21, the base A of docs/ARCHITECTURE.md, Workspaces): what one is, what its
names may be, and the ports each gets. Pure.

A workspace is a task of a project: a worktree Atlas made in the home's `worktrees/`, on a branch
of its own, `atlas/<name>`, from the branch the project had checked out. Its git is the server's,
never the agent's.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

#: The prefix of a workspace's branch.
BRANCH_PREFIX = "atlas/"
#: The message of the commit that keeps an archived workspace's work on its branch; unarchiving
#: undoes a last commit with this message only.
ARCHIVE_MESSAGE = "Atlas: the work of an archived workspace, kept until it is unarchived"
#: How many ports a workspace gets when its project names none.
PORTS_SIZE = 10
#: The longest a name may be, as a folder and a branch take it.
NAME_LENGTH = 48

_NOT_NAME = re.compile(r"[^a-z0-9._-]+")
_BESIDE_DASH = re.compile(r"[-.]*-[-.]*")


class WorkspaceState(StrEnum):
    #: Its folder is there and its sessions run in it.
    READY = "ready"
    #: Its setup script runs.
    PREPARING = "preparing"
    #: Its setup script failed; the folder is there all the same.
    SETUP_FAILED = "setup_failed"
    #: Its folder is gone and its work kept on its branch.
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class Project:
    """A git repository, as Atlas groups its folders."""

    #: Its common git folder, which every worktree of it shares: the project's identity.
    common_dir: str
    #: The folder of its main worktree.
    root: str
    #: The command that prepares a new workspace's folder, empty for none.
    setup_script: str = ""
    #: The first port of the project's workspaces, 0 for none, and how many each gets.
    ports_start: int = 0
    ports_size: int = PORTS_SIZE


@dataclass(frozen=True, slots=True)
class Workspace:
    id: str
    #: The project's common git folder.
    project: str
    name: str
    branch: str
    #: The branch it was made from, where its work goes back.
    base: str
    folder: str
    state: WorkspaceState
    created_at: str
    #: Its block of ports' place in the project, from 0.
    slot: int
    #: The setup script's last words, when it ran.
    setup_output: str = ""
    #: Its work was kept in a commit when it was archived.
    archived_commit: bool = False


def slug(name: str) -> str:
    """A workspace's name as a folder and a branch take it; empty when nothing is left. A dot
    stays inside a word (`v1.2`), not beside a dash (`page.-2` is `page-2`)."""
    dashed = _BESIDE_DASH.sub("-", _NOT_NAME.sub("-", name.strip().lower()))
    return dashed.strip("-.")[:NAME_LENGTH].strip("-.")


def ports(project: Project, workspace_slot: int) -> tuple[int, int] | None:
    """The first and last port of a workspace's block, when its project names a range."""
    if project.ports_start <= 0:
        return None
    first = project.ports_start + workspace_slot * project.ports_size
    return first, first + project.ports_size - 1


def free_slot(taken: set[int]) -> int:
    """The lowest block of ports no workspace of the project holds."""
    slot = 0
    while slot in taken:
        slot += 1
    return slot
