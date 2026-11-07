/**
 * "Bring your work", the first run's last step (step 18d): of the executors chosen, the folders and
 * sessions that changed within the window the user chose stay in the list, and the rest is
 * archived (store/archive.ts), never copied, moved or touched. A folder that changed within it has
 * its sessions read to count them and to archive its old ones; a folder that did not is archived
 * whole, unread. With no window, nothing is archived.
 */
import type { SessionFolderRow, SessionRow } from '@/protocol/protocol.generated'

import { $home, $sessionFolders, $sessions, folderKey, refreshFolders, refreshSessions } from './app'
import { archiveFolders, archiveSessions } from './archive'
import { begun, markOffered, type WorkWindow } from './setup'

const DAY = 24 * 60 * 60 * 1000

/** What a window keeps and archives of one executor's folders. */
export interface WorkPlan {
  /** The sessions that changed within the window, in the folders kept. */
  kept: number
  /** The folders kept: changed within the window. */
  folders: string[]
  /** The folders archived whole. */
  oldFolders: string[]
  /** The sessions archived, by id, in the folders kept. */
  oldSessions: string[]
}

/** The earliest change a window keeps, or `null` for everything. */
export function cutoff(window: WorkWindow, now = Date.now()): null | number {
  return window === null ? null : now - window * DAY
}

function within(updatedAt: string, since: null | number): boolean {
  if (since === null) {
    return true
  }

  const changed = Date.parse(updatedAt)

  // A time its harness did not keep is no reason to hide it.
  return Number.isNaN(changed) || changed >= since
}

/** The plan, from folders and the sessions read of the folders kept (`null` while unread). */
export function planWork(
  folders: SessionFolderRow[],
  sessions: (folder: string) => null | SessionRow[],
  since: null | number
): WorkPlan {
  const kept = folders.filter(row => within(row.updated_at, since))
  const read = kept.flatMap(row => sessions(row.folder) ?? [])

  return {
    folders: kept.map(row => row.folder),
    kept: read.filter(row => within(row.updated_at, since)).length,
    oldFolders: folders.filter(row => !within(row.updated_at, since)).map(row => row.folder),
    oldSessions: read.filter(row => row.session_id && !within(row.updated_at, since)).map(row => row.session_id)
  }
}

/** One executor's plan as last read. */
export function readPlan(executor: string, window: WorkWindow): null | WorkPlan {
  const folders = $sessionFolders.get()[executor]?.folders

  if (!folders) {
    return null
  }

  const all = $sessions.get()

  return planWork(folders, folder => all[folderKey(executor, folder)]?.sessions ?? null, cutoff(window))
}

/** Read what a window needs of an executor: its folders, read again since the list may have
 * changed since it was last read, then the sessions of the ones it keeps that were not read yet.
 * With no window, the folders alone: everything stays. */
export async function readWork(executor: string, window: WorkWindow): Promise<void> {
  await refreshFolders(executor)

  if (window === null) {
    return
  }

  const plan = readPlan(executor, window)
  const all = $sessions.get()
  const unread = plan?.folders.filter(folder => !all[folderKey(executor, folder)]?.sessions) ?? []
  await Promise.all(unread.map(folder => refreshSessions(executor, folder)))
}

/** Archive what the window leaves out, for each executor chosen. */
export function bringWork(executors: string[], window: WorkWindow): void {
  const home = $home.get()

  for (const executor of executors) {
    const plan = readPlan(executor, window)

    if (plan) {
      archiveFolders(home, executor, plan.oldFolders)
      archiveSessions(home, executor, plan.oldSessions)
    }
  }
}

/** How old a folder is, untouched, before "Bring your work" is offered for it. */
const OFFER_AFTER: WorkWindow = 30

const offering = new Set<string>()

/**
 * A home that had its key before the first run existed (decided with the user on 01/11/2026) is
 * offered "Bring your work" once, alone, when the executors on have folders untouched for a month:
 * the long lists it is for. With none, the home is only marked and never asked.
 */
export async function offerWork(home: null | string, executors: string[]): Promise<void> {
  const key = home ?? ''

  if (begun(home) || offering.has(key)) {
    return
  }

  offering.add(key)

  try {
    const unread = executors.filter(name => !$sessionFolders.get()[name]?.folders)
    await Promise.all(unread.map(name => refreshFolders(name)))

    if (!begun(home)) {
      markOffered(
        home,
        executors.some(name => (readPlan(name, OFFER_AFTER)?.oldFolders.length ?? 0) > 0)
      )
    }
  } finally {
    offering.delete(key)
  }
}
