/**
 * The home's workspaces (step 21): a project's tasks, each a worktree in the home on a branch of
 * its own, made, archived, brought back and deleted by the server (`workspace.*`), read again
 * whenever it says one changed (`workspaces.changed`). How a project prepares a new one, its setup
 * script and its ports, is the project's (`workspace.project`, `workspace.set_project`).
 *
 * What a workspace changed, line by line (`workspace.diff`, A2), and the user's comments on its
 * lines, kept per workspace until they are sent: they go to the session as one message, each with
 * its file, its line and the line's text, so the agent finds the place.
 */
import { atom, map } from 'nanostores'

import type { CheckpointRow, ProjectRow, WorkspaceDiff, WorkspaceRow } from '@/protocol/protocol.generated'

import { $home, current, goOnInCopy, sendToSession } from './app'

/** The home's workspaces, as last read. */
export const $workspaceRows = atom<WorkspaceRow[]>([])

export async function refreshWorkspaces(): Promise<void> {
  $workspaceRows.set(await listWorkspaces())
}

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

/** A path as compared: one separator, and no case on Windows, where `C:\Work` is `c:/work`. */
function samePath(path: string): string {
  const slashed = path.replace(/\\/g, '/').replace(/\/+$/, '')

  return /^[a-zA-Z]:\//.test(slashed) ? slashed.toLowerCase() : slashed
}

/** The workspace whose folder `folder` is, not archived. */
export function workspaceAt(rows: WorkspaceRow[], folder: string): undefined | WorkspaceRow {
  const wanted = samePath(folder)

  return rows.find(row => row.state !== 'archived' && samePath(row.folder) === wanted)
}

export async function readCheckpoints(id: string): Promise<CheckpointRow[]> {
  return (await current().client.call('workspace.checkpoints', { id })).checkpoints
}

/** The workspace's files back as they were at a checkpoint, what they are now kept first as
 *  another (step 21, A3); then the session on screen goes on in a copy, so what it said after the
 *  checkpoint stays in the original. */
export async function goBack(id: string, commit: string): Promise<void> {
  await current().client.call('workspace.restore', { commit, id })
  await goOnInCopy()
}

/** Every change of the workspace committed on its branch, as the user's own commit (A4). */
export function commitWorkspace(id: string, message: string): Promise<WorkspaceRow> {
  return current().client.call('workspace.commit', { id, message })
}

/** Its branch merged into its branch of origin, after the user's yes. */
export function mergeWorkspace(id: string): Promise<WorkspaceRow> {
  return current().client.call('workspace.merge', { id })
}

/** Its branch sent to the project's remote, after the user's yes; what git said. */
export async function pushWorkspace(id: string): Promise<string> {
  return (await current().client.call('workspace.push', { id })).said
}

export function readDiff(id: string): Promise<WorkspaceDiff> {
  return current().client.call('workspace.diff', { id })
}

/** A comment on a line of what a workspace changed. */
export interface ReviewComment {
  /** The line's number in the file as it is now, or as it was for a line removed. */
  line: number
  path: string
  /** Whether the line was removed: its number is then the file's before. */
  removed: boolean
  /** The line commented on, so the agent finds it even after the file moved on. */
  quoted: string
  text: string
}

/** The comments not sent yet, per home and workspace. */
export const $reviewComments = map<Record<string, ReviewComment[] | undefined>>({})

function commentsKey(workspaceId: string): string {
  return `${$home.get() ?? ''}\u0000${workspaceId}`
}

export function commentsOf(all: Record<string, ReviewComment[] | undefined>, workspaceId: string): ReviewComment[] {
  return all[commentsKey(workspaceId)] ?? []
}

export function addComment(workspaceId: string, comment: ReviewComment): void {
  const key = commentsKey(workspaceId)
  $reviewComments.setKey(key, [...($reviewComments.get()[key] ?? []), comment])
}

export function removeComment(workspaceId: string, index: number): void {
  const key = commentsKey(workspaceId)
  $reviewComments.setKey(
    key,
    ($reviewComments.get()[key] ?? []).filter((_, at) => at !== index)
  )
}

/** The comments as the agent reads them: a line per place, the line quoted, then the comment. */
export function reviewMessage(comments: ReviewComment[]): string {
  const parts = comments.map(comment => {
    const where = `${comment.path}:${String(comment.line)}${comment.removed ? ' (a line removed)' : ''}`
    const quoted = comment.quoted.trim() ? `\n> ${comment.quoted.trim()}` : ''

    return `${where}${quoted}\n${comment.text.trim()}`
  })

  return `Review comments on your changes:\n\n${parts.join('\n\n')}`
}

/** Send a workspace's comments to the session on screen, as one message; kept when it fails. */
export async function sendReview(workspaceId: string): Promise<boolean> {
  const comments = commentsOf($reviewComments.get(), workspaceId)

  if (comments.length === 0) {
    return false
  }

  const sent = await sendToSession(reviewMessage(comments))

  if (sent) {
    $reviewComments.setKey(commentsKey(workspaceId), [])
  }

  return sent
}
