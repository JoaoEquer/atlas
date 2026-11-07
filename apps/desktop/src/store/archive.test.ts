import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(() => {
  localStorage.clear()
  vi.resetModules()
  vi.useRealTimers()
})

describe('an archived folder or session (decided with the user on 01/11/2026)', () => {
  it('is archived while it has not changed since, and back in the list once it has', async () => {
    const { $archivedSessions, archiveKey, isArchived, setSessionArchived } = await import('./archive')
    vi.useFakeTimers({ now: Date.parse('2026-11-01T12:00:00.000Z') })

    setSessionArchived('home', 'claude-code', 's1', true)
    const archived = $archivedSessions.get()[archiveKey('home', 'claude-code')]

    expect(isArchived(archived, 's1', '2026-10-20T09:00:00.000Z')).toBe(true)
    // Resumed in a terminal after it was archived: back by itself.
    expect(isArchived(archived, 's1', '2026-11-01T12:30:00.000Z')).toBe(false)
    expect(isArchived(archived, 's2', '2026-10-20T09:00:00.000Z')).toBe(false)
    // A session not kept yet has no id, and is never archived.
    expect(isArchived(archived, '', '2026-10-20T09:00:00.000Z')).toBe(false)

    setSessionArchived('home', 'claude-code', 's1', false)
    expect(
      isArchived($archivedSessions.get()[archiveKey('home', 'claude-code')], 's1', '2026-10-20T09:00:00.000Z')
    ).toBe(false)
  })

  it('kept as a list of ids before 01/11/2026, counts as archived when the window opens', async () => {
    vi.useFakeTimers({ now: Date.parse('2026-11-01T12:00:00.000Z') })
    localStorage.setItem('atlas.archivedFolders', JSON.stringify({ '\u0000codex': ['/work/old'] }))
    const { $archived, archiveKey, isArchived } = await import('./archive')
    const archived = $archived.get()[archiveKey(null, 'codex')]

    expect(isArchived(archived, '/work/old', '2026-10-01T00:00:00.000Z')).toBe(true)
    expect(isArchived(archived, '/work/old', '2026-11-02T00:00:00.000Z')).toBe(false)
  })

  it('archives several at once, apart per home and executor', async () => {
    const { $archived, archiveFolders, archiveKey } = await import('./archive')

    archiveFolders('home', 'codex', ['/a', '/b'])
    archiveFolders('other', 'codex', ['/c'])

    expect(Object.keys($archived.get()[archiveKey('home', 'codex')] ?? {})).toEqual(['/a', '/b'])
    expect(Object.keys($archived.get()[archiveKey('other', 'codex')] ?? {})).toEqual(['/c'])
    expect(JSON.parse(localStorage.getItem('atlas.archivedFolders') ?? '{}')).toEqual($archived.get())
  })
})
