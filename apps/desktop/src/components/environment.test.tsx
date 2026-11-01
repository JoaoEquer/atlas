import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { InstanceRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import * as environment from '@/store/environment'

import { ContextSection, WherePill } from './environment'

beforeEach(() => {
  vi.spyOn(environment, 'readFolderGit').mockResolvedValue(undefined)
  vi.spyOn(environment, 'readSessionContext').mockResolvedValue(undefined)
  app.$connection.set({ error: null, state: 'open' })
})

afterEach(() => {
  cleanup()
  app.$connection.set({ error: null, state: 'idle' })
  vi.restoreAllMocks()
  environment.$folderGit.set({})
  environment.$sessionContext.set({})
})

const FOLDER = '/work/site'

describe('where a session works', () => {
  it('names the project and the branch, and offers a new worktree', () => {
    const start = vi.spyOn(environment, 'startWorktree').mockResolvedValue(undefined)
    environment.$folderGit.setKey(`\u0000${FOLDER}`, {
      branch: 'main',
      folder: FOLDER,
      repository: true,
      root: FOLDER,
      worktree: false
    })
    render(<WherePill executor="claude-code" folder={FOLDER} />)

    expect(screen.getByTestId('where-select').textContent).toBe('sitemain')
    fireEvent.keyDown(screen.getByTestId('where-select'), { key: 'Enter' })
    expect(screen.getByTestId('where-worktree').textContent).toContain('New worktree')
    fireEvent.click(screen.getByTestId('where-worktree'))

    expect(start).toHaveBeenCalledWith('claude-code', FOLDER)
  })

  it('offers no worktree outside a repository', () => {
    environment.$folderGit.setKey(`\u0000${FOLDER}`, {
      branch: '',
      folder: FOLDER,
      repository: false,
      root: '',
      worktree: false
    })
    render(<WherePill executor="claude-code" folder={FOLDER} />)
    fireEvent.keyDown(screen.getByTestId('where-select'), { key: 'Enter' })

    expect(screen.queryByTestId('where-worktree')).toBeNull()
    expect(screen.queryByTestId('where-branch')).toBeNull()
  })
})

describe("the session's context", () => {
  const INSTANCE = { instance_id: 'i1', state: 'idle' } as InstanceRow

  it('says how full it is against its model window', () => {
    environment.$sessionContext.setKey('\u0000i1', { tokens: 173_100, window: 1_000_000 })
    render(<ContextSection instance={INSTANCE} />)

    expect(screen.getByTestId('session-context').textContent).toContain('17% used')
    expect(screen.getByTestId('session-context-count').textContent).toBe('173.1K / 1M')
  })

  it('says only its tokens when the window is not known, and nothing when its harness says none', () => {
    environment.$sessionContext.setKey('\u0000i1', { tokens: 5_000, window: null })
    const { unmount } = render(<ContextSection instance={INSTANCE} />)
    expect(screen.getByTestId('session-context').textContent).toContain('5K tokens')
    unmount()

    environment.$sessionContext.setKey('\u0000i1', { tokens: null, window: null })
    render(<ContextSection instance={INSTANCE} />)
    expect(screen.queryByTestId('session-context')).toBeNull()
  })
})
