/**
 * An executor's session's environment (step 18g): its folder's git, read by the server
 * (`workbench.git`), and how full its session's context is (`workbench.context`); and a new
 * worktree of the folder's repository, where a new session works apart from it. Kept per home, a
 * folder's path naming another machine's folder in another home.
 */
import { map } from 'nanostores'

import type { FolderGit, SessionContext } from '@/protocol/protocol.generated'

import { $home, current, newSession } from './app'

export const $folderGit = map<Record<string, FolderGit | undefined>>({})
export const $sessionContext = map<Record<string, SessionContext | undefined>>({})

function key(what: string): string {
  return `${$home.get() ?? ''}\u0000${what}`
}

export function folderGitOf(all: Record<string, FolderGit | undefined>, folder: string): FolderGit | undefined {
  return all[key(folder)]
}

export function contextOf(
  all: Record<string, SessionContext | undefined>,
  instanceId: string
): SessionContext | undefined {
  return all[key(instanceId)]
}

/** Read the folder's git; a folder git cannot read stays as it was. */
export async function readFolderGit(folder: string): Promise<void> {
  try {
    $folderGit.setKey(key(folder), await current().client.call('workbench.git', { folder }))
  } catch {
    // Outside a home's reach (another machine's folder gone): nothing to show.
  }
}

export async function readSessionContext(instanceId: string): Promise<void> {
  try {
    const read = await current().client.call('workbench.context', { instance_id: instanceId })
    $sessionContext.setKey(key(instanceId), read)
  } catch {
    // An instance gone: its context is no longer shown.
  }
}

/** A new workspace of the folder's project (step 21: a worktree in the home), and a new session of
 *  `executor` in it;
 * what went wrong is the session's error. Its name is when it was made: `1101-1832`. */
export async function startWorktree(executor: string, folder: string, now = new Date()): Promise<void> {
  const two = (value: number) => String(value).padStart(2, '0')
  const name = `${two(now.getMonth() + 1)}${two(now.getDate())}-${two(now.getHours())}${two(now.getMinutes())}`

  try {
    const made = await current().client.call('workbench.worktree', { folder, name })
    $folderGit.setKey(key(made.folder), made)
    newSession(executor, made.folder)
  } catch (error) {
    current().$sessionError.set(error instanceof Error ? error.message : String(error))
  }
}
