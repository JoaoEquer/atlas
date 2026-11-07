import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { contribute } from '@/plugins/registry'
import type {
  ExecutorRow,
  KeyProvider,
  ModelOptions,
  PluginRow,
  SessionFolderRow,
  SessionRow
} from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import { $archived, $archivedSessions, archiveKey, isArchived } from '@/store/archive'
import { $firstRun, firstRunOf, openFirstRun, type Step } from '@/store/setup'

import { FirstRun, FirstRunBand } from './first-run'

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
  app.$plugins.set([])
  app.$pairings.set([])
  app.$pluginError.set(null)
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
  modes: [],
  account_id: '',
  accounts: true,
  base: '',
  consent: 'fingerprint',
  consent_again: false,
  declaration: ['Adds the executor claude-code'],
  enabled: true,
  files: false,
  installed: true,
  latest: '',
  logged_in: true,
  minimum: '2.1.140',
  name: 'claude-code',
  nickname: '',
  plan: '',
  plugin: 'claude-code',
  problem: '',
  supported: true,
  title: 'Claude Code',
  update: '',
  updating: false,
  version: '2.1.296'
}

const run = () => firstRunOf($firstRun.get(), null)

describe('the first run (step 18d)', () => {
  it('opens on the Model step, numbered on the left, and stays open once a key is stored', () => {
    app.$modelOptions.set(NONE)
    app.$keyProviders.set([OPENROUTER])
    render(<FirstRun />)

    expect(screen.getByTestId('first-run-count').textContent).toBe('Step 1 of 5')
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

  it('offers the executors found, and the accounts plugin once one is on, its part once it runs', () => {
    openFirstRun(null)
    $firstRun.set({ '': { ...run(), step: 'executors' } })
    app.$workbench.set({ executors: [CLAUDE], instances: [] })
    render(<FirstRun />)

    expect(screen.getByTestId('executor-offer').textContent).toContain('Claude Code')
    expect(screen.getByTestId('first-run-accounts').textContent).toContain('More than one account?')
    // Off, the accounts plugin is offered (step 22); on, its part shows for each harness.
    expect(screen.getByTestId('first-run-accounts-plugin').textContent).toBe('Turn on the Accounts plugin')
    let undo: () => void = () => undefined
    act(() => {
      undo = contribute('accounts', {
        area: 'executor',
        id: 'accounts',
        render: place => <p data-testid="plugin-part">{(place as { base: string }).base}</p>,
        title: 'Accounts'
      })
    })
    expect(screen.getByTestId('plugin-part').textContent).toBe('claude-code')
    expect(screen.queryByTestId('first-run-accounts-plugin')).toBeNull()
    act(undo)
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

  const at = (step: Step) => {
    openFirstRun(null)
    $firstRun.set({ '': { ...run(), step } })
  }

  const TOKEN = { description: "The bot's token", name: 'TELEGRAM_BOT_TOKEN', stored: false, url: '' }
  const TELEGRAM: PluginRow = {
    author: 'Atlas',
    bundled: true,
    channels: ['telegram'],
    consent: 'fingerprint',
    consented: false,
    declaration: ['Reach api.telegram.org', 'Read the secret TELEGRAM_BOT_TOKEN'],
    description: 'Talk to Atlas from Telegram',
    enabled: false,
    name: 'telegram',
    problem: null,
    running: false,
    secrets: [TOKEN],
    settings: [],
    version: '1.0.0'
  }

  it('connects the bot with the consent shown, and lets in who wrote to it', async () => {
    vi.spyOn(app, 'refreshPlugins').mockReturnValue()
    const enable = vi.spyOn(app, 'enablePlugin').mockImplementation(() => {
      app.$plugins.set([{ ...TELEGRAM, consented: true, enabled: true }])

      return Promise.resolve(true)
    })
    const store = vi.spyOn(app, 'storePluginSecret').mockImplementation(() => {
      app.$plugins.set([
        { ...TELEGRAM, consented: true, enabled: true, running: true, secrets: [{ ...TOKEN, stored: true }] }
      ])

      return Promise.resolve(true)
    })
    const approve = vi.spyOn(app, 'approvePairing').mockResolvedValue(true)
    app.$plugins.set([TELEGRAM])
    at('telegram')
    render(<FirstRun />)

    expect(screen.getByTestId('first-run-title').textContent).toBe('Atlas in your pocket')
    expect(screen.getByTestId('first-run-telegram-consent').textContent).toContain('Reach api.telegram.org')
    expect(screen.getByTestId('first-run-telegram-connect')).toHaveProperty('disabled', true)
    fireEvent.change(screen.getByTestId('first-run-telegram-token'), { target: { value: ' 123:abc ' } })
    fireEvent.click(screen.getByTestId('first-run-telegram-connect'))

    await screen.findByTestId('first-run-telegram-ready')
    expect(enable).toHaveBeenCalledWith(TELEGRAM)
    expect(store).toHaveBeenCalledWith('telegram', 'TELEGRAM_BOT_TOKEN', '123:abc')
    expect(screen.getByTestId('first-run-continue')).toHaveProperty('disabled', true)

    // Someone wrote to the bot: let in from the step, and the step is done.
    const waiting = { approved: false, asked_at: '', channel: 'telegram', code: 'K7QM2XPA', name: 'Ana', user: '42' }
    act(() => {
      app.$pairings.set([waiting])
    })
    expect(screen.getByTestId('first-run-telegram-person').textContent).toContain('Ana waits with K7QM2XPA')
    fireEvent.click(screen.getByTestId('first-run-telegram-let-in'))
    expect(approve).toHaveBeenCalledWith('K7QM2XPA')
    act(() => {
      app.$pairings.set([{ ...waiting, approved: true }])
    })
    fireEvent.click(screen.getByTestId('first-run-continue'))
    expect(run()).toMatchObject({ marks: { telegram: 'done' }, step: 'connections' })
  })

  it('says why Telegram refused a stored token, and takes another', () => {
    vi.spyOn(app, 'refreshPlugins').mockReturnValue()
    const store = vi.spyOn(app, 'storePluginSecret').mockResolvedValue(true)
    const refused = "Telegram refused the bot's token: get the token again."
    app.$plugins.set([
      { ...TELEGRAM, consented: true, enabled: true, problem: refused, secrets: [{ ...TOKEN, stored: true }] }
    ])
    at('telegram')
    render(<FirstRun />)

    expect(screen.getByTestId('first-run-telegram-problem').textContent).toBe(refused)
    expect(screen.queryByTestId('first-run-telegram-consent')).toBeNull()
    fireEvent.change(screen.getByTestId('first-run-telegram-token'), { target: { value: '456:def' } })
    fireEvent.click(screen.getByTestId('first-run-telegram-connect'))
    expect(store).toHaveBeenCalledWith('telegram', 'TELEGRAM_BOT_TOKEN', '456:def')
  })

  const GITHUB: PluginRow = {
    ...TELEGRAM,
    channels: [],
    declaration: ['Runs its own Python code inside Atlas', 'Says it reaches: github.com.'],
    description: 'Work with GitHub through its own program, gh',
    name: 'github',
    secrets: []
  }

  it('offers GitHub and Google as plugins of Atlas, turned on in place, and shows their parts once on', () => {
    vi.spyOn(app, 'refreshPlugins').mockReturnValue()
    const enable = vi.spyOn(app, 'enablePlugin').mockResolvedValue(true)
    const opened = vi.spyOn(app, 'newConversation').mockReturnValue()
    app.$plugins.set([GITHUB, { ...GITHUB, description: 'Gmail and Calendar through gws', name: 'google' }])
    at('connections')
    render(<FirstRun />)

    const offer = screen.getByTestId('first-run-offer-github')
    expect(offer.textContent).toContain('Says it reaches: github.com.')
    expect(screen.getByTestId('first-run-offer-google')).toBeTruthy()
    expect(screen.getByTestId('first-run-continue')).toHaveProperty('disabled', true)
    fireEvent.click(within(offer).getByTestId('first-run-turn-on'))
    expect(enable).toHaveBeenCalledWith(GITHUB)

    // On, the plugin's own part shows; its setup in a conversation leaves the first run a band away.
    let undo: () => void = () => undefined
    act(() => {
      app.$plugins.set([{ ...GITHUB, consented: true, enabled: true }])
      undo = contribute('github', {
        area: 'connection',
        id: 'github',
        render: place => (
          <button
            data-testid="plugin-part"
            onClick={() => {
              ;(place as { away: () => void }).away()
            }}
            type="button"
          >
            part
          </button>
        ),
        title: 'GitHub'
      })
    })
    expect(screen.queryByTestId('first-run-offer-github')).toBeNull()
    expect(screen.getByTestId('first-run-continue')).toHaveProperty('disabled', false)
    fireEvent.click(screen.getByTestId('plugin-part'))
    expect(opened).toHaveBeenCalled()
    expect(run()).toMatchObject({ away: true, open: true, step: 'connections' })
    act(undo)
  })

  it('the band over the app brings back the step stepped away from', () => {
    at('connections')
    $firstRun.set({ '': { ...run(), away: true } })
    render(<FirstRunBand step="connections" />)

    expect(screen.getByTestId('first-run-band').textContent).toContain('The first run waits on Connections.')
    fireEvent.click(screen.getByTestId('first-run-back'))
    expect(run()).toMatchObject({ away: false, open: true, step: 'connections' })
  })
})
