"""Workspaces: a project's tasks, each a worktree and a branch of its own (step 21)."""

from atlas.workspaces.diff import DiffFile, DiffLine
from atlas.workspaces.errors import WorkspaceError
from atlas.workspaces.model import Checkpoint, Project, Workspace, WorkspaceState
from atlas.workspaces.service import Workspaces
from atlas.workspaces.store import SCHEMA

__all__ = [
    "SCHEMA",
    "Checkpoint",
    "DiffFile",
    "DiffLine",
    "Project",
    "Workspace",
    "WorkspaceError",
    "WorkspaceState",
    "Workspaces",
]
