"""The workspaces of one home (step 21, A1): made from any folder of a project, prepared, archived,
brought back and deleted, each a worktree in the home's `worktrees/` on a branch of its own.

Blocking: the server runs each call in a thread. Nothing here touches the project's own folder
but to read its `.worktreeinclude` and to make, list and remove worktrees through git; the
branch a workspace came from is never checked out, reset or deleted.
"""

import hashlib
import logging
import os
import secrets
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from atlas.home import Database, format_utc, utc_now
from atlas.workspaces import git
from atlas.workspaces.errors import WorkspaceError
from atlas.workspaces.model import (
    ARCHIVE_MESSAGE,
    BRANCH_PREFIX,
    PORTS_SIZE,
    Project,
    Workspace,
    WorkspaceState,
    free_slot,
    ports,
    slug,
)
from atlas.workspaces.store import WorkspaceStore

#: How long a project's setup script may run.
SETUP_SECONDS = 600.0
#: How much of the setup script's output is kept, from its end.
OUTPUT_CHARACTERS = 4000

_logger = logging.getLogger("atlas.workspaces")


class Workspaces:
    def __init__(
        self,
        folder: Path,
        database: Database,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        #: The home's `worktrees/`: one folder per project, one per workspace in it.
        self.folder = folder
        self._store = WorkspaceStore(database)
        self._clock = clock

    # -- projects --------------------------------------------------------------------------------

    def project_of(self, folder: Path) -> Project | None:
        """The project `folder` belongs to, with its settings; `None` outside a repository."""
        found = git.project_of(folder)
        if found is None:
            return None
        return self._store.project(*found)

    def set_project(
        self, folder: Path, *, setup_script: str, ports_start: int, ports_size: int
    ) -> Project:
        """How the project of `folder` prepares a new workspace."""
        project = self._project(folder)
        if ports_start < 0 or not 0 < (ports_size or PORTS_SIZE) <= 1000:
            raise WorkspaceError("Ports start at 1 or more, and a workspace gets 1 to 1000.")
        if ports_start and ports_start + (ports_size or PORTS_SIZE) > 65536:
            raise WorkspaceError("The ports run past 65535: start lower.")
        changed = replace(
            project,
            setup_script=setup_script.strip(),
            ports_start=ports_start,
            ports_size=ports_size or PORTS_SIZE,
        )
        self._store.set_project(changed)
        return changed

    # -- workspaces ------------------------------------------------------------------------------

    def listed(self, folder: Path | None = None) -> list[Workspace]:
        """Every workspace of the home, or of the project of `folder`."""
        if folder is None:
            return self._store.listed()
        project = git.project_of(folder)
        return [] if project is None else self._store.listed(project[0])

    def get(self, workspace_id: str) -> Workspace:
        found = self._store.get(workspace_id)
        if found is None:
            raise WorkspaceError(f"There is no workspace {workspace_id!r}.")
        return found

    def create(self, folder: Path, name: str, base: str | None = None) -> Workspace:
        """A new workspace of the project of `folder`, named `name`, on a new branch from `base`
        (the branch the project has checked out when none is named): its worktree made and the
        listed files copied. Its setup script, when the project has one, waits for `prepare`."""
        project = self._project(folder)
        named = slug(name)
        if not named:
            raise WorkspaceError("A workspace needs a name of letters or digits.")
        root = Path(project.root)
        branch = f"{BRANCH_PREFIX}{named}"
        if git.branch_exists(root, branch):
            raise WorkspaceError(
                f"The branch {branch} exists already: name the workspace otherwise."
            )
        target = self._project_folder(project) / named
        if target.exists():
            raise WorkspaceError(f"{target} exists already: name the workspace otherwise.")
        origin = base or git.current_branch(root)
        git.add_worktree(root, target, branch, origin)
        copied = git.copy_included(root, target)
        taken = {other.slot for other in self._store.listed(project.common_dir)}
        made = Workspace(
            id=f"w-{secrets.token_hex(6)}",
            project=project.common_dir,
            name=named,
            branch=branch,
            base=origin,
            folder=str(target),
            state=WorkspaceState.PREPARING if project.setup_script else WorkspaceState.READY,
            created_at=format_utc(self._clock()),
            slot=free_slot(taken),
        )
        self._store.put(made)
        _logger.info("A workspace was made.", extra={"workspace": made.id, "copied": len(copied)})
        return made

    def prepare(self, workspace_id: str) -> Workspace:
        """Run the project's setup script in a new workspace's folder, with its ports in the
        environment; ready whatever it says, marked when it failed."""
        workspace = self.get(workspace_id)
        project = self._store.project(workspace.project, self._root(workspace))
        if not project.setup_script:
            done = replace(workspace, state=WorkspaceState.READY)
            self._store.put(done)
            return done
        try:
            ran = subprocess.run(  # noqa: S602 - the project's own script, set by the user
                project.setup_script,
                shell=True,
                cwd=workspace.folder,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=SETUP_SECONDS,
                env={**os.environ, **self.environment(workspace)},
                stdin=subprocess.DEVNULL,
                check=False,
            )
            output = (ran.stdout + ran.stderr)[-OUTPUT_CHARACTERS:]
            ok = ran.returncode == 0
        except subprocess.TimeoutExpired:
            output, ok = f"The setup script ran past {SETUP_SECONDS:.0f} seconds.", False
        except OSError as error:
            output, ok = f"The setup script could not run: {error}", False
        done = replace(
            workspace,
            state=WorkspaceState.READY if ok else WorkspaceState.SETUP_FAILED,
            setup_output=output,
        )
        self._store.put(done)
        return done

    def environment(self, workspace: Workspace) -> dict[str, str]:
        """What a workspace's setup script and sessions find in their environment."""
        project = self._store.project(workspace.project, self._root(workspace))
        found = {
            "ATLAS_WORKSPACE": workspace.name,
            "ATLAS_WORKSPACE_BRANCH": workspace.branch,
            "ATLAS_PROJECT_ROOT": project.root,
        }
        block = ports(project, workspace.slot)
        if block is not None:
            found["ATLAS_WORKSPACE_PORT"] = str(block[0])
            found["ATLAS_WORKSPACE_PORTS"] = f"{block[0]}-{block[1]}"
        return found

    def undelivered(self, workspace_id: str) -> bool:
        """Whether a workspace holds work its branch of origin has not: changes not committed,
        or commits of its own."""
        workspace = self.get(workspace_id)
        root = Path(self._root(workspace))
        if workspace.state is not WorkspaceState.ARCHIVED and git.changed(Path(workspace.folder)):
            return True
        return git.ahead(root, workspace.base, workspace.branch) > 0

    def archive(self, workspace_id: str) -> Workspace:
        """Its work kept in a commit on its branch, and its folder gone; nothing is lost."""
        workspace = self.get(workspace_id)
        if workspace.state is WorkspaceState.ARCHIVED:
            return workspace
        folder = Path(workspace.folder)
        kept = git.commit_all(folder, ARCHIVE_MESSAGE)
        git.remove_worktree(Path(self._root(workspace)), folder)
        done = replace(workspace, state=WorkspaceState.ARCHIVED, archived_commit=kept)
        self._store.put(done)
        return done

    def unarchive(self, workspace_id: str) -> Workspace:
        """Its folder back on its branch, and the archive's commit undone, its work as it was."""
        workspace = self.get(workspace_id)
        if workspace.state is not WorkspaceState.ARCHIVED:
            return workspace
        folder = Path(workspace.folder)
        if folder.exists():
            raise WorkspaceError(f"{folder} exists: move it away to bring the workspace back.")
        git.add_worktree(Path(self._root(workspace)), folder, workspace.branch, None)
        if workspace.archived_commit and git.last_message(folder) == ARCHIVE_MESSAGE:
            git.undo_last_commit(folder)
        done = replace(workspace, state=WorkspaceState.READY, archived_commit=False)
        self._store.put(done)
        return done

    def delete(self, workspace_id: str, *, force: bool = False) -> None:
        """A workspace gone, its folder and its branch; one holding undelivered work only with
        `force`, the user's word after a warning."""
        workspace = self.get(workspace_id)
        if not force and self.undelivered(workspace_id):
            raise WorkspaceError(
                f"The workspace {workspace.name} holds work not delivered: delete it anyway, or "
                "deliver or archive it first."
            )
        root = Path(self._root(workspace))
        if workspace.state is not WorkspaceState.ARCHIVED and Path(workspace.folder).exists():
            git.remove_worktree(root, Path(workspace.folder))
        if git.branch_exists(root, workspace.branch):
            git.delete_branch(root, workspace.branch)
        self._store.delete(workspace_id)

    # -- helpers ---------------------------------------------------------------------------------

    def _project(self, folder: Path) -> Project:
        project = self.project_of(folder)
        if project is None:
            raise WorkspaceError(f"{folder} is not in a git repository: it has no workspaces.")
        return project

    def _project_folder(self, project: Project) -> Path:
        """The project's folder in `worktrees/`: its name and a few letters of its git folder,
        so two projects of one name stay apart."""
        mark = hashlib.sha256(project.common_dir.encode("utf-8")).hexdigest()[:6]
        return self.folder / f"{slug(Path(project.root).name) or 'project'}-{mark}"

    @staticmethod
    def _root(workspace: Workspace) -> str:
        common = Path(workspace.project)
        return str(common.parent if common.name == ".git" else common)
