import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { AtlasClient } from '@/protocol/client'
import type { ExecutorRow } from '@/protocol/protocol.generated'
import { $extensionsTab, $pluginFocus, $view, current, openHome, startApp } from '@/store/app'

import { ExecutorsList } from './executors-list'
import { SessionView } from './session-view'

const RUNNING: ExecutorRow = {
  account: 'someone@example.com',
  account_folders: false,
  account_id: '',
  accounts: true,
  base: '',
  consent: '',
  consent_again: false,
  declaration: [],
  enabled: true,
  files: false,
  installed: true,
  latest: '',
  logged_in: true,
  minimum: '',
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
/** Its plugin was on, and an update changed what it declares: offered again, off, waiting. */
const WAITING: ExecutorRow = {
  ...RUNNING,
  consent: 'new fingerprint',
  consent_again: true,
  declaration: ['Adds the executor claude-code'],
  enabled: false
}
const CODEX: ExecutorRow = { ...RUNNING, name: 'codex', plugin: 'codex', title: 'Codex' }

let stop: () => void = () => undefined

beforeAll(() => {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => undefined
})

afterEach(() => {
  cleanup()
  stop()
  $extensionsTab.set('plugin')
  $pluginFocus.set(null)
})

/** The app on a client that answers `executor.session` with nothing and leaves the rest
 * unanswered, the workbench read with `executors`. */
async function app(executors: ExecutorRow[]): Promise<string[]> {
  const calls: string[] = []
  const none = () => () => undefined
  const client = {
    call: (method: string) => {
      calls.push(method)

      return method === 'executor.session'
        ? Promise.resolve({ entries: [], expired: false })
        : new Promise(() => undefined)
    },
    onError: none,
    onEvent: none,
    onRequest: none,
    onResync: none,
    onState: none,
    start: vi.fn(),
    stop: vi.fn(),
    watch: vi.fn()
  } as unknown as AtlasClient
  stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
  await openHome('work')
  const session = current()
  session.$workbench.set({ executors, instances: [] })
  session.$read.setKey('workbench', true)

  return calls
}

function openSession(executor = 'claude-code'): void {
  current().$openSession.set({
    choice: null,
    elsewhere: '',
    executor,
    expired: false,
    folder: 'C:\\work\\site',
    instanceId: '',
    sessionId: 'session-1',
    title: 'The site'
  })
}

describe('an executor whose plugin waits for the consent again (found on 01/11/2026)', () => {
  it('stays in the sidebar saying why, and opens Extensions at its consent', async () => {
    await app([WAITING, CODEX])
    render(<ExecutorsList />)

    const notice = screen.getByTestId('executor-consent-again')
    expect(notice.textContent).toContain('Claude Code waits for your consent again after an update')
    // The harnesses on still show as before, and "none on" is not said for it.
    expect(screen.getAllByTestId('executor-node')).toHaveLength(1)
    expect(screen.queryByTestId('executors-open-extensions')).toBeNull()

    act(() => {
      fireEvent.click(screen.getByTestId('executor-review-consent'))
    })
    expect($view.get()).toBe('extensions')
    expect($extensionsTab.get()).toBe('plugin')
    expect($pluginFocus.get()).toBe('claude-code')
  })

  it('says nothing of a harness never turned on', async () => {
    await app([{ ...WAITING, consent_again: false }])
    render(<ExecutorsList />)

    expect(screen.queryByTestId('executor-consent-again')).toBeNull()
    expect(screen.getByTestId('executors-open-extensions')).toBeTruthy()
  })

  it('a tab of one of its sessions says so instead of reading forever, and reads it once it is on', async () => {
    const calls = await app([WAITING])
    openSession()
    render(<SessionView />)

    expect(screen.getByTestId('session-not-running').textContent).toContain(
      'Claude Code waits for your consent again after an update'
    )
    expect(screen.queryByText('Reading…')).toBeNull()

    await act(async () => {
      current().$workbench.set({ executors: [RUNNING], instances: [] })
      await Promise.resolve()
    })
    expect(screen.queryByTestId('session-not-running')).toBeNull()
    expect(calls).toContain('executor.session')
  })

  it('a session on another account waits with its harness', async () => {
    await app([WAITING])
    openSession('claude-code@ab12cd34')
    render(<SessionView />)

    expect(screen.getByTestId('executor-consent-again').textContent).toContain('Claude Code')
  })

  it('a tab whose executor is off or gone says it is not running, with the way to Extensions', async () => {
    await app([CODEX])
    openSession()
    render(<SessionView />)

    expect(screen.getByTestId('session-not-running').textContent).toContain('claude-code is not running')
    act(() => {
      fireEvent.click(screen.getByTestId('session-open-extensions'))
    })
    expect($view.get()).toBe('extensions')
    expect($extensionsTab.get()).toBe('executor')
  })
})

describe("an executor's update (asked for by the user on 01/11/2026)", () => {
  /** The app on a client whose `executor.update` answers `ended`, the rest unanswered. */
  async function updating(executors: ExecutorRow[], ended: { output: string; updated: boolean }): Promise<string[]> {
    const calls: string[] = []
    const none = () => () => undefined
    const client = {
      call: (method: string) => {
        calls.push(method)

        return method === 'executor.update' ? Promise.resolve(ended) : new Promise(() => undefined)
      },
      onError: none,
      onEvent: none,
      onRequest: none,
      onResync: none,
      onState: none,
      start: vi.fn(),
      stop: vi.fn(),
      watch: vi.fn()
    } as unknown as AtlasClient
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    current().$workbench.set({ executors, instances: [] })
    current().$read.setKey('workbench', true)

    return calls
  }

  const NEWER: ExecutorRow = { ...RUNNING, latest: '2.1.316', update: 'claude update', version: '2.1.140' }

  it('offers a newer version on its row, and runs its update only on the click', async () => {
    const calls = await updating([NEWER], { output: '', updated: true })
    render(<ExecutorsList />)

    expect(screen.getByTestId('executor-update-version').textContent).toBe('Update available: 2.1.140 → 2.1.316')
    expect(screen.getByTestId('executor-update-run').getAttribute('title')).toBe('Runs: claude update')
    expect(calls).not.toContain('executor.update')

    await act(async () => {
      fireEvent.click(screen.getByTestId('executor-update-run'))
      await Promise.resolve()
    })
    expect(calls).toContain('executor.update')
  })

  it('shows the end of the output of an update that failed', async () => {
    await updating([NEWER], { output: 'npm ERR! EBUSY: resource busy', updated: false })
    render(<ExecutorsList />)

    await act(async () => {
      fireEvent.click(screen.getByTestId('executor-update-run'))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    expect(screen.getByRole('alert').textContent).toContain('EBUSY')
  })

  it('says one too old for Atlas apart, in the sidebar and in its session, with its update', async () => {
    const old: ExecutorRow = {
      ...NEWER,
      problem: 'Claude Code 2.0.1 is older than 2.1.140. Update it: claude update',
      supported: false,
      version: '2.0.1'
    }
    await updating([old], { output: '', updated: true })
    render(<ExecutorsList />)

    const notice = screen.getByTestId('executor-too-old')
    expect(notice.textContent).toContain('Claude Code 2.0.1 is too old for Atlas')
    expect(notice.textContent).toContain('Update available: 2.0.1 → 2.1.316')
    expect(screen.queryByTestId('executor-node')).toBeNull()
    cleanup()

    openSession()
    render(<SessionView />)
    expect(screen.getByTestId('session-not-running').textContent).toContain('too old for Atlas')
  })

  it('says nothing of an update when there is none', async () => {
    await updating([RUNNING], { output: '', updated: true })
    render(<ExecutorsList />)

    expect(screen.queryByTestId('executor-update')).toBeNull()
  })
})
