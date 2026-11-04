/**
 * The home's workspaces (step 21): a project's tasks, each a worktree in the home on a branch of
 * its own, made, archived, brought back and deleted by the server (`workspace.*`), read again
 * whenever it says one changed (`workspaces.changed`). How a project prepares a new one, its setup
 * script and its ports, is the project's (`workspace.project`, `workspace.set_project`).
 */
import type { ProjectRow, WorkspaceRow } from '@/protocol/protocol.generated'

import { current } from './app'

export async function listWorkspaces(): Promise<WorkspaceRow[]> {
  return (await current().client.call('workspace.list', { folder: '' })).workspaces
}

export function createWorkspace(folder: string, name: string): Promise<WorkspaceRow> {
  return current().client.call('workspace.create', { base: '', folder, name })
}

export function archiveWorkspace(id: string): Promise<WorkspaceRow> {
  return current().client.call('workspace.archive', { id })
}

export function unarchiveWorkspace(id: string): Promise<WorkspaceRow> {
  return current().client.call('workspace.unarchive', { id })
}

/** Delete a workspace, its folder and its branch; one holding work not delivered is refused
 *  unless `force`, the user's yes after the warning. */
export async function deleteWorkspace(id: string, force = false): Promise<void> {
  await current().client.call('workspace.delete', { force, id })
}

export function readProject(folder: string): Promise<ProjectRow> {
  return current().client.call('workspace.project', { folder })
}

export function saveProject(
  folder: string,
  setupScript: string,
  portsStart: number,
  portsSize: number
): Promise<ProjectRow> {
  return current().client.call('workspace.set_project', {
    folder,
    ports_size: portsSize,
    ports_start: portsStart,
    setup_script: setupScript
  })
}

/** The workspaces grouped under their project, the projects and their workspaces by name. */
export function byProject(rows: WorkspaceRow[]): [string, WorkspaceRow[]][] {
  const groups = new Map<string, WorkspaceRow[]>()

  for (const row of [...rows].sort((a, b) => a.name.localeCompare(b.name))) {
    groups.set(row.project_root, [...(groups.get(row.project_root) ?? []), row])
  }

  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
}
