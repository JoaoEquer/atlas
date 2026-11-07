"""The workspaces and their projects' settings, in the home's database."""

from collections.abc import Sequence
from typing import Any

from atlas.home import Database, Migration, Schema, transaction
from atlas.workspaces.model import PORTS_SIZE, Project, Workspace, WorkspaceState

SCHEMA = Schema(
    "workspaces",
    (
        Migration(
            1,
            "workspaces, and how each project prepares a new one",
            """
CREATE TABLE workspaces_projects (
    common_dir TEXT PRIMARY KEY,
    root TEXT NOT NULL,
    setup_script TEXT NOT NULL DEFAULT '',
    ports_start INTEGER NOT NULL DEFAULT 0,
    ports_size INTEGER NOT NULL DEFAULT 10
) STRICT;
CREATE TABLE workspaces_workspaces (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    name TEXT NOT NULL,
    branch TEXT NOT NULL,
    base TEXT NOT NULL,
    folder TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    slot INTEGER NOT NULL,
    setup_output TEXT NOT NULL DEFAULT '',
    archived_commit INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project, name)
) STRICT;
""",
        ),
    ),
)


class WorkspaceStore:
    """Each query reads and writes a workspace's columns in the order the table declares them."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def project(self, common_dir: str, root: str) -> Project:
        """A project's settings; one never set has none."""
        rows = self._read(
            "SELECT setup_script, ports_start, ports_size FROM workspaces_projects "
            "WHERE common_dir = ?",
            (common_dir,),
        )
        if not rows:
            return Project(common_dir, root)
        script, start, size = rows[0]
        return Project(common_dir, root, str(script), int(start), int(size))

    def set_project(self, project: Project) -> None:
        self._write(
            "INSERT INTO workspaces_projects (common_dir, root, setup_script, ports_start, "
            "ports_size) VALUES (?, ?, ?, ?, ?) ON CONFLICT (common_dir) DO UPDATE SET "
            "root = excluded.root, setup_script = excluded.setup_script, "
            "ports_start = excluded.ports_start, ports_size = excluded.ports_size",
            (
                project.common_dir,
                project.root,
                project.setup_script,
                project.ports_start,
                project.ports_size or PORTS_SIZE,
            ),
        )

    def listed(self, project: str | None = None) -> list[Workspace]:
        if project is None:
            rows = self._read("SELECT * FROM workspaces_workspaces ORDER BY created_at", ())
        else:
            rows = self._read(
                "SELECT * FROM workspaces_workspaces WHERE project = ? ORDER BY created_at",
                (project,),
            )
        return [_workspace(row) for row in rows]

    def get(self, workspace_id: str) -> Workspace | None:
        rows = self._read("SELECT * FROM workspaces_workspaces WHERE id = ?", (workspace_id,))
        return _workspace(rows[0]) if rows else None

    def put(self, workspace: Workspace) -> None:
        self._write(
            "INSERT OR REPLACE INTO workspaces_workspaces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workspace.id,
                workspace.project,
                workspace.name,
                workspace.branch,
                workspace.base,
                workspace.folder,
                workspace.state.value,
                workspace.created_at,
                workspace.slot,
                workspace.setup_output,
                int(workspace.archived_commit),
            ),
        )

    def delete(self, workspace_id: str) -> None:
        self._write("DELETE FROM workspaces_workspaces WHERE id = ?", (workspace_id,))

    def _read(self, sql: str, parameters: Sequence[object]) -> list[tuple[Any, ...]]:
        connection = self._database.connect()
        try:
            return list(connection.execute(sql, tuple(parameters)).fetchall())
        finally:
            connection.close()

    def _write(self, sql: str, parameters: Sequence[object]) -> None:
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(sql, tuple(parameters))
        finally:
            connection.close()


def _workspace(row: tuple[Any, ...]) -> Workspace:
    (
        workspace_id,
        project,
        name,
        branch,
        base,
        folder,
        state,
        created_at,
        slot,
        output,
        archived,
    ) = row
    return Workspace(
        id=str(workspace_id),
        project=str(project),
        name=str(name),
        branch=str(branch),
        base=str(base),
        folder=str(folder),
        state=WorkspaceState(str(state)),
        created_at=str(created_at),
        slot=int(slot),
        setup_output=str(output),
        archived_commit=bool(archived),
    )
