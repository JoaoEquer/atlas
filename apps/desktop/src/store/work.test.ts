import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SessionFolderRow, SessionRow } from '@/protocol/protocol.generated'

import * as app from './app'
import { $sessionFolders } from './app'
import { $firstRun, firstRunOf } from './setup'
import { cutoff, offerWork, planWork } from './work'

const NOW = Date.parse('2026-11-01T12:00:00.000Z')

const folder = (path: string, updatedAt: string): SessionFolderRow => ({
  account_id: '',
  executor: 'claude-code',
  folder: path,
  updated_at: updatedAt
})

const session = (id: string, updatedAt: string): SessionRow => ({
  by_atlas: false,
  elsewhere: '',
  executor: 'claude-code',
  folder: '/work/app',
  instance_id: '',
  session_id: id,
  started_at: '',
  state: '',
  title: id,
  updated_at: updatedAt,
  waiting: 0
})

describe('what "Bring your work" keeps and archives (step 18d)', () => {
  const folders = [folder('/work/app', '2026-10-30T10:00:00.000Z'), folder('/work/old', '2026-08-15T10:00:00.000Z')]
  const sessions = (path: string) =>
    path === '/work/app'
      ? [session('new', '2026-10-30T10:00:00.000Z'), session('stale', '2026-09-01T10:00:00.000Z'), session('', '')]
      : null

  it('keeps what changed within the window, and archives the rest: a folder whole, a session alone', () => {
    expect(planWork(folders, sessions, cutoff(30, NOW))).toEqual({
      folders: ['/work/app'],
      // The session with no id yet, and no time, is kept: nothing says it is old.
      kept: 2,
      oldFolders: ['/work/old'],
      oldSessions: ['stale']
    })
  })

  it('a longer window keeps more, and no window keeps everything', () => {
    expect(planWork(folders, sessions, cutoff(90, NOW)).oldFolders).toEqual([])
    expect(planWork(folders, sessions, cutoff(90, NOW)).oldSessions).toEqual([])
    expect(cutoff(null, NOW)).toBeNull()
    expect(planWork(folders, () => null, null)).toMatchObject({ oldFolders: [], oldSessions: [] })
  })
})

describe('"Bring your work" offered to a home that had its key before (step 18d)', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
    localStorage.clear()
    $firstRun.set({})
    $sessionFolders.set({})
  })

  const read = (rows: SessionFolderRow[]) =>
    vi.spyOn(app, 'refreshFolders').mockImplementation(executor => {
      $sessionFolders.setKey(executor, { error: null, folders: rows, reading: false })

      return Promise.resolve()
    })

  it('opens it alone, once, when a folder has been untouched for a month', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    const reads = read([
      folder('/work/app', '2026-10-30T10:00:00.000Z'),
      folder('/work/old', '2026-08-15T10:00:00.000Z')
    ])

    await offerWork('home', ['claude-code'])

    expect(firstRunOf($firstRun.get(), 'home')).toEqual({
      marks: { executors: 'done', model: 'done' },
      open: true,
      step: 'work'
    })
    // Never again, whatever it finds then.
    await offerWork('home', ['claude-code'])
    expect(reads).toHaveBeenCalledTimes(1)
  })

  it('only marks a home whose lists are short, or that has no executor on', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    read([folder('/work/app', '2026-10-30T10:00:00.000Z')])

    await offerWork('home', ['claude-code'])
    await offerWork('other', [])

    expect(firstRunOf($firstRun.get(), 'home').open).toBe(false)
    expect(firstRunOf($firstRun.get(), 'other').open).toBe(false)
    expect(Object.keys($firstRun.get())).toEqual(['home', 'other'])
  })
})
