import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AtlasClient } from '@/protocol/client'
import type { ExecutorRow } from '@/protocol/protocol.generated'
import { current, openHome, showSidebarTab, startApp } from '@/store/app'
import { $extensionsTab, $settings, $settingsSection } from '@/store/settings'

import { ExecutorOffers } from './executor-offers'
import { ExecutorsList } from './executors-list'

const ROW: ExecutorRow = {
  account: '',
  account_folders: false,
  files: false,
  account_id: '',
  accounts: true,
  base: '',
  consent: 'fingerprint',
  declaration: ['Adds the executor claude-code'],
  enabled: true,
  installed: false,
  logged_in: false,
  minimum: '2.1.140',
  name: 'claude-code',
  nickname: '',
  plan: '',
  plugin: 'claude-code',
  problem: 'Claude Code is not installed. Install it, in PowerShell: irm https://claude.ai/install.ps1 | iex',
  supported: false,
  title: 'Claude Code',
  version: ''
}

const FOUND: ExecutorRow = { ...ROW, installed: true, problem: '', supported: true, version: '2.1.296' }

/** A client that never connects, whose calls `answer` settles, each kept in `calls`. */
function client(answer: (method: string) => Promise<unknown>, calls: string[]): AtlasClient {
  const none = () => () => undefined

  return {
    call: (method: string) => {
      calls.push(method)

      return answer(method)
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
}

let stop: () => void = () => undefined

afterEach(() => {
  cleanup()
  stop()
  vi.useRealTimers()
})

async function app(answer: (method: string) => Promise<unknown>, rows: ExecutorRow[]): Promise<string[]> {
  const calls: string[] = []
  stop = startApp({
    client: () => client(answer, calls),
    shell: { focus: () => undefined, notify: () => undefined }
  })
  await openHome('work')
  current().$workbench.set({ executors: rows, instances: [] })
  // As a read of the server's workbench leaves it.
  current().$read.setKey('workbench', true)

  return calls
}

describe('the harnesses on this computer', () => {
  it('keeps unavailable executors out of the sidebar once an executor is on', async () => {
    await app(() => new Promise(() => undefined), [FOUND, ROW])
    render(<ExecutorsList />)

    expect(screen.queryByTestId('executor-offers')).toBeNull()
    expect(screen.getByTestId('executor-card').textContent).toContain('Claude Code')
  })

  it('leaves turning one on to Settings when none is on, and leads there', async () => {
    await app(() => new Promise(() => undefined), [{ ...FOUND, enabled: false }, ROW])
    render(<ExecutorsList />)

    expect(screen.queryByTestId('executor-offers')).toBeNull()
    fireEvent.click(screen.getByTestId('executors-open-settings'))
    expect($settings.get()).toBe(true)
    expect($settingsSection.get()).toBe('extensions')
    expect($extensionsTab.get()).toBe('executor')
  })

  it('says why one is not usable, with the command that installs it', async () => {
    await app(() => new Promise(() => undefined), [ROW])
    render(<ExecutorOffers />)

    expect(screen.getByTestId('executor-problem').textContent).toBe(ROW.problem)
    expect(screen.getByTestId('executor-offer').getAttribute('data-usable')).toBe('false')
  })

  it('looks again on its button, and shows what the server found then', async () => {
    let found: (value: unknown) => void = () => undefined
    const calls = await app(
      method =>
        method === 'executor.detect' ? new Promise(resolve => (found = resolve)) : new Promise(() => undefined),
      [ROW]
    )
    render(<ExecutorOffers />)

    const button = screen.getByTestId('executors-look-again')
    fireEvent.click(button)
    expect(calls).toEqual(['executor.detect'])
    expect(button.hasAttribute('disabled')).toBe(true)
    await act(async () => {
      found({ executors: [FOUND] })
      await Promise.resolve()
    })

    expect(screen.queryByTestId('executor-problem')).toBeNull()
    expect(screen.getByTestId('executor-offer').textContent).toContain('Claude Code 2.1.296 · not logged in')
    expect(screen.getByTestId('executors-look-again').hasAttribute('disabled')).toBe(false)
  })

  it('looks when the executors tab shows, at most once a minute', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    const calls = await app(
      method =>
        Promise.resolve(
          method === 'workbench.list'
            ? { executors: [FOUND], instances: [], open_requests: [] }
            : { executors: [FOUND] }
        ),
      [FOUND]
    )

    const show = async (lists: number) => {
      await act(async () => {
        showSidebarTab('executors')
        await vi.waitFor(() => {
          expect(calls.filter(method => method === 'workbench.list')).toHaveLength(lists)
        })
      })
    }
    await show(1)
    await show(2)
    await vi.waitFor(() => {
      expect(calls.filter(method => method === 'executor.detect')).toHaveLength(1)
    })
    vi.setSystemTime(Date.now() + 61_000)
    await act(async () => {
      showSidebarTab('executors')
      await vi.waitFor(() => {
        expect(calls.filter(method => method === 'executor.detect')).toHaveLength(2)
      })
    })
  })

  it('says when nobody is logged in to an executor that is on, and its switch logs in', async () => {
    await app(() => new Promise(() => undefined), [FOUND])
    render(<ExecutorsList />)

    expect(screen.getByTestId('executor-card').textContent).toContain('2.1.296 · not logged in')
  })
})
