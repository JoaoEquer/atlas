"""Workspaces: a project's tasks, each a worktree and a branch of its own (step 21)."""

from atlas.workspaces.errors import WorkspaceError
from atlas.workspaces.model import Project, Workspace, WorkspaceState
from atlas.workspaces.service import Workspaces
from atlas.workspaces.store import SCHEMA

__all__ = ["SCHEMA", "Project", "Workspace", "WorkspaceError", "WorkspaceState", "Workspaces"]
