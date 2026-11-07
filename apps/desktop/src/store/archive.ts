/**
 * The executors' folders the user archived, out of the sidebar's list so it stays short (asked for
 * by the user on 18/10/2026), never touching the folder, kept in the window's storage and apart per
 * home and executor so one home's hiding does not bleed into another's. An archived folder stays
 * listed under the executor's "Archived", where it comes back. A session is archived the same way
 * (asked for by the user on 19/10/2026), hidden and kept whole, under its folder's "Archived".
 *
 * Archived means still since it was archived (decided with the user on 01/11/2026): each one keeps
 * when it was archived, and one that changed after that is back in the list by itself, so a session
 * resumed in a terminal is never left hidden. The first run's "Bring your work" archives this way
 * what did not change within the window it was given.
 */
import { atom } from 'nanostores'

const KEY = 'atlas.archivedFolders'
const SESSIONS_KEY = 'atlas.archivedSessions'

/** By folder or session id, when it was archived, in UTC text. */
export type ArchivedAt = Record<string, string>

function read<T>(key: string): Record<string, T> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(key) ?? '{}')

    return stored !== null && typeof stored === 'object' ? (stored as Record<string, T>) : {}
  } catch {
    return {}
  }
}

/** The stored archive, where a list of ids written before 01/11/2026 counts as archived now. */
function readArchived(key: string): Record<string, ArchivedAt> {
  const now = new Date().toISOString()

  return Object.fromEntries(
    Object.entries(read<unknown>(key)).map(([place, found]): [string, ArchivedAt] => [
      place,
      Array.isArray(found)
        ? Object.fromEntries(found.filter((id): id is string => typeof id === 'string').map(id => [id, now]))
        : found !== null && typeof found === 'object'
          ? (found as ArchivedAt)
          : {}
    ])
  )
}

/** Whether a folder or a session is archived: archived, and unchanged since (`updatedAt`, its
 * last change as its harness says). */
export function isArchived(archived: ArchivedAt | undefined, id: string, updatedAt: string): boolean {
  const at = id ? archived?.[id] : undefined

  if (at === undefined) {
    return false
  }

  const changed = Date.parse(updatedAt)

  return Number.isNaN(changed) || changed <= Date.parse(at)
}

/** By `home\u0000executor`, the folders archived. */
export const $archived = atom<Record<string, ArchivedAt>>(readArchived(KEY))

export function archiveKey(home: null | string, executor: string): string {
  return `${home ?? ''}\u0000${executor}`
}

function write(store: typeof $archived, key: string, place: string, ids: string[], archived: boolean): void {
  const all = store.get()
  const now = new Date().toISOString()
  const kept = Object.fromEntries(Object.entries(all[place] ?? {}).filter(([id]) => !ids.includes(id)))
  const next = { ...all, [place]: archived ? { ...kept, ...Object.fromEntries(ids.map(id => [id, now])) } : kept }
  store.set(next)
  localStorage.setItem(key, JSON.stringify(next))
}

export function setArchived(home: null | string, executor: string, folder: string, archived: boolean): void {
  write($archived, KEY, archiveKey(home, executor), [folder], archived)
}

/** Several folders at once, as "Bring your work" archives them. */
export function archiveFolders(home: null | string, executor: string, folders: string[]): void {
  write($archived, KEY, archiveKey(home, executor), folders, true)
}

/** By `home\u0000executor`, the sessions archived, by id. */
export const $archivedSessions = atom<Record<string, ArchivedAt>>(readArchived(SESSIONS_KEY))

export function setSessionArchived(home: null | string, executor: string, session: string, archived: boolean): void {
  write($archivedSessions, SESSIONS_KEY, archiveKey(home, executor), [session], archived)
}

/** Several sessions at once, as "Bring your work" archives them. */
export function archiveSessions(home: null | string, executor: string, sessions: string[]): void {
  write($archivedSessions, SESSIONS_KEY, archiveKey(home, executor), sessions, true)
}

const SHOWN_KEY = 'atlas.showArchived'

/** By `home\u0000executor`, whether its archived folders and sessions are listed at all (asked for by
 * the user on 21/10/2026, to keep the sidebar clean): off until turned on in its row's menu. */
export const $showArchived = atom<Record<string, boolean>>(read<boolean>(SHOWN_KEY))

export function setShowArchived(home: null | string, executor: string, shown: boolean): void {
  const next = { ...$showArchived.get(), [archiveKey(home, executor)]: shown }
  $showArchived.set(next)
  localStorage.setItem(SHOWN_KEY, JSON.stringify(next))
}
