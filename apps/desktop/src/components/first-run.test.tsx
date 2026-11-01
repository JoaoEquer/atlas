import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type {
  ExecutorRow,
  KeyProvider,
  ModelOptions,
  SessionFolderRow,
  SessionRow
} from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import { $archived, $archivedSessions, archiveKey, isArchived } from '@/store/archive'
import { $firstRun, firstRunOf, openFirstRun } from '@/store/setup'

import { FirstRun } from './first-run'

// Leaving the first run leaves the key band, which is the started app's.
beforeEach(() => {
  vi.spyOn(app, 'chooseKeyLater').mockImplementation(later => {
    app.$keyLater.set(later)
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
  localStorage.clear()
  $firstRun.set({})
  $archived.set({})
  $archivedSessions.set({})
  app.$keyProviders.set(null)
  app.$keyNotice.set(null)
  app.$keyLater.set(false)
  app.$modelOptions.set(null)
  app.$workbench.set({ executors: [], instances: [] })
  app.$sessionFolders.set({})
  app.$sessions.set({})
})

const NONE: ModelOptions = { default: { effort: null, model: null, picked: false, provider: null }, providers: [] }
const KEYED: ModelOptions = {
  default: { effort: null, model: 'openai/gpt-6-luna', picked: false, provider: 'openrouter' },
  providers: [{ id: 'openrouter', models: [], name: 'OpenRouter' }]
}
const OPENROUTER: KeyProvider = {
  id: 'openrouter',
  key_page: 'https://openrouter.example/keys',
  name: 'OpenRouter',
  stored: false
}

const CLAUDE: ExecutorRow = {
  account: 'someone@example.com',
  account_folders: true,
  account_id: '',
  accounts: true,
  base: '',
  consent: 'fingerprint',
  declaration: ['Adds the executor claude-code'],
  enabled: true,
  files: false,
  installed: true,
  logged_in: true,
  minimum: '2.1.140',
  name: 'claude-code',
  nickname: '',
  plan: '',
  plugin: 'claude-code',
  problem: '',
  supported: true,
  title: 'Claude Code',
  version: '2.1.296'
}

const run = () => firstRunOf($firstRun.get(), null)

describe('the first run (step 18d)', () => {
  it('opens on the Model step, numbered on the left, and stays open once a key is stored', () => {
    app.$modelOptions.set(NONE)
    app.$keyProviders.set([OPENROUTER])
    render(<FirstRun />)

    expect(screen.getByTestId('first-run-count').textContent).toBe('Step 1 of 3')
    expect(screen.getByTestId('first-run-title').textContent).toBe('Choose the model')
    expect(screen.getByTestId('first-run-step-model').getAttribute('aria-current')).toBe('step')
    expect(screen.getByTestId('key-provider-openrouter')).toBeTruthy()
    // With no key yet, Continue waits and Skip goes on.
    expect(screen.getByTestId('first-run-continue')).toHaveProperty('disabled', true)
    // Opened for want of a key, it is kept open: the steps after the key follow.
    expect(run().open).toBe(true)

    act(() => {
      app.$modelOptions.set(KEYED)
    })
    fireEvent.click(screen.getByTestId('first-run-continue'))

    expect(run()).toMatchObject({ marks: { model: 'done' }, open: true, step: 'executors' })
    expect(screen.getByTestId('first-run-title').textContent).toBe('Connect your agents')
    expect(screen.getByTestId('first-run-step-model').dataset.mark).toBe('done')
  })

  it('skips a step, and "Go to Atlas" leaves at any point, the key band waiting when no key is stored', () => {
    app.$modelOptions.set(NONE)
    render(<FirstRun />)

    fireEvent.click(screen.getByTestId('first-run-skip'))
    expect(run()).toMatchObject({ marks: { model: 'skipped' }, step: 'executors' })
    expect(within(screen.getByTestId('first-run-step-model')).getByText('Skipped')).toBeTruthy()

    fireEvent.click(screen.getByTestId('first-run-leave'))
    expect(app.$keyLater.get()).toBe(true)
    expect(run().open).toBe(false)
  })

  it("shows what a stored key's check said, and goes on once it is read", () => {
    const dismissed = vi.spyOn(app, 'dismissKeyNotice').mockReturnValue()
    openFirstRun(null)
    app.$modelOptions.set(KEYED)
    app.$keyNotice.set({
      detail: 'The key is valid; its credit or quota: none left.',
      provider: 'openrouter',
      status: 'no_credit',
      stored: true
    })
    render(<FirstRun />)

    expect(screen.getByTestId('key-notice').textContent).toBe('The key is valid; its credit or quota: none left.')
    fireEvent.click(screen.getByTestId('key-continue'))
    expect(dismissed).toHaveBeenCalled()
    expect(run().step).toBe('executors')
  })

  it('offers the executors found, and accounts that take over from one another once one is on', () => {
    openFirstRun(null)
    $firstRun.set({ '': { ...run(), step: 'executors' } })
    app.$workbench.set({ executors: [CLAUDE], instances: [] })
    render(<FirstRun />)

    expect(screen.getByTestId('executor-offer').textContent).toContain('Claude Code')
    expect(screen.getByTestId('first-run-accounts').textContent).toContain('More than one account?')
    expect(screen.getByTestId('add-account')).toBeTruthy()
    expect(screen.getByTestId('first-run-continue')).toHaveProperty('disabled', false)
  })
})

describe('"Bring your work" (step 18d)', () => {
  const NOW = Date.parse('2026-11-01T12:00:00.000Z')
  const folders: SessionFolderRow[] = [
    { account_id: '', executor: 'claude-code', folder: '/work/app', updated_at: '2026-10-30T10:00:00.000Z' },
    { account_id: '', executor: 'claude-code', folder: '/work/old', updated_at: '2026-06-01T10:00:00.000Z' }
  ]
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

  it('counts the sessions of the window, and archives the rest once brought', async () => {
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true })
    const readFolders = vi.spyOn(app, 'refreshFolders').mockImplementation(executor => {
      app.$sessionFolders.setKey(executor, { error: null, folders, reading: false })

      return Promise.resolve()
    })
    const readSessions = vi.spyOn(app, 'refreshSessions').mockImplementation((executor, path) => {
      app.$sessions.setKey(app.folderKey(executor, path), {
        error: null,
        reading: false,
        sessions: [session('new', '2026-10-30T10:00:00.000Z'), session('stale', '2026-09-01T10:00:00.000Z')],
        stale: false
      })

      return Promise.resolve()
    })
    openFirstRun(null)
    $firstRun.set({ '': { ...run(), step: 'work' } })
    app.$workbench.set({ executors: [CLAUDE], instances: [] })
    render(<FirstRun />)

    const bring = await screen.findByText('Bring 1 session')
    expect(readFolders).toHaveBeenCalledWith('claude-code')
    // Only the folder the window keeps has its sessions read.
    expect(readSessions.mock.calls).toEqual([['claude-code', '/work/app']])
    expect(screen.getByTestId('first-run-counts').textContent).toBe('1 Claude Code')

    // A longer window counts more.
    fireEvent.change(screen.getByTestId('first-run-window'), { target: { value: '90' } })
    await screen.findByText('Bring 2 sessions')
    fireEvent.change(screen.getByTestId('first-run-window'), { target: { value: '30' } })
    await waitFor(() => {
      expect(bring.textContent).toBe('Bring 1 session')
    })

    fireEvent.click(bring)

    const key = archiveKey(null, 'claude-code')
    expect(isArchived($archived.get()[key], '/work/old', folders[1]?.updated_at ?? '')).toBe(true)
    expect(isArchived($archived.get()[key], '/work/app', folders[0]?.updated_at ?? '')).toBe(false)
    expect(isArchived($archivedSessions.get()[key], 'stale', '2026-09-01T10:00:00.000Z')).toBe(true)
    expect(isArchived($archivedSessions.get()[key], 'new', '2026-10-30T10:00:00.000Z')).toBe(false)
    expect(run()).toMatchObject({ marks: { work: 'done' }, open: false })
  })

  it('with no executor on, says so and offers a conversation', () => {
    openFirstRun(null)
    $firstRun.set({ '': { ...run(), step: 'work' } })
    render(<FirstRun />)

    expect(screen.getByTestId('first-run-no-executors')).toBeTruthy()
    fireEvent.click(screen.getByTestId('first-run-start'))
    expect(run()).toMatchObject({ marks: { work: 'skipped' }, open: false })
  })
})
