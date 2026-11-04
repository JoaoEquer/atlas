/**
 * The accounts plugin's screen (step 22), the file Atlas ships, as the app loads it: its part by a
 * session's box and atop an executor's tree, with the plugin's calls and Atlas's switch of the
 * session faked, so what it asks for is checked without a server.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExecutorAccounts, ExecutorRow, InstanceRow } from '@/protocol/protocol.generated'
import { $connection, $executorAccounts, $workbench } from '@/store/app'

// @ts-expect-error -- the plugin's screen is plain JavaScript, loaded uncompiled by the app.
import screenModule from '../../../../plugins/accounts/desktop/plugin.js'

import type { Place } from './registry'
import { host } from './sdk'

interface Registered {
  area: string
  render: (place: Place) => ReactNode
}

const MAIN: ExecutorRow = {
  account: 'someone@example.com',
  account_folders: true,
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
  modes: [],
  name: 'claude-code',
  nickname: '',
  plan: '',
  plugin: 'claude-code',
  problem: '',
  supported: true,
  title: 'Claude Code',
  update: '',
  updating: false,
  version: '2.1.140'
}
const WORK: ExecutorRow = {
  ...MAIN,
  account: 'w@example.com',
  account_id: 'ab12cd34',
  base: 'claude-code',
  name: 'claude-code@ab12cd34',
  nickname: 'work',
  title: 'Claude Code · work'
}
const ACCOUNTS: ExecutorAccounts = {
  accounts: [
    {
      account_id: '',
      executor: 'claude-code',
      folder: '/home/me/.claude',
      logged_in: true,
      login: 'someone@example.com',
      name: 'claude-code',
      nickname: ''
    },
    {
      account_id: 'ab12cd34',
      executor: 'claude-code',
      folder: '/home/me/.claude-work',
      logged_in: true,
      login: 'w@example.com',
      name: 'claude-code@ab12cd34',
      nickname: 'work'
    }
  ],
  can_add: true,
  compact: true,
  executor: 'claude-code',
  failover: true
}

function instance(state = 'idle'): InstanceRow {
  return {
    account: 'someone@example.com',
    account_id: '',
    executor: 'claude-code',
    instance_id: 'in-1',
    state,
    transcript_id: 'c-1'
  } as InstanceRow
}

/** The screen's two parts, registered with a `ctx` whose calls answer as `answer` says. */
function registered(answer: (name: string, params: Record<string, unknown>) => unknown) {
  const calls: [string, Record<string, unknown>][] = []
  const parts: Registered[] = []
  const ctx = {
    call: (name: string, params: Record<string, unknown>) => {
      calls.push([name, params])

      return Promise.resolve(answer(name, params))
    },
    register: (part: Registered) => {
      parts.push(part)
    }
  }
  ;(screenModule as { register: (ctx: unknown) => void }).register(ctx)
  const part = (area: string) => parts.find(found => found.area === area) as Registered

  return { calls, part }
}

const EMPTY_ROUTE = { compact: true, enabled: true, stops: [] }

beforeEach(() => {
  $connection.set({ error: null, state: 'open' })
  $workbench.set({ executors: [MAIN, WORK], instances: [] })
  $executorAccounts.set({ 'claude-code': ACCOUNTS })
  vi.spyOn(host, 'readAccounts').mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("by a session's box", () => {
  it('names the account, and switches a running session only after the user read what changes', async () => {
    const check = vi.spyOn(host.sessionAccount, 'check').mockResolvedValue({
      account_id: 'ab12cd34',
      compared: true,
      executor: 'claude-code',
      login: 'w@example.com',
      missing: ['gmail'],
      tokens: 31234
    })
    const go = vi.spyOn(host.sessionAccount, 'switch').mockResolvedValue(undefined)
    const { part } = registered(name => (name === 'route' ? EMPTY_ROUTE : {}))
    const place = { conversation: 'c-1', executor: 'claude-code', folder: '/work/site', instance: instance() }
    render(<>{part('session').render(place)}</>)

    const pill = await screen.findByTestId('session-route')
    expect(pill.textContent).toBe('someone@example.com')
    fireEvent.click(pill)
    const choices = screen.getAllByTestId('session-account-choice')
    fireEvent.click(choices[1] as HTMLElement)

    const note = await screen.findByTestId('account-switch')
    expect(await within(note).findByTestId('account-switch-missing')).toBeTruthy()
    expect(note.textContent).toContain('about 31,234 tokens')
    expect(check).toHaveBeenCalledWith('ab12cd34')
    expect(go).not.toHaveBeenCalled()
    fireEvent.click(within(note).getByTestId('account-switch-confirm'))
    expect(go.mock.calls[0]?.[0]).toBe('ab12cd34')
  })

  it("draws the route, adds a stop and saves it for the session's card", async () => {
    const stop = {
      account: 'ab12cd34',
      effort: null,
      executor: 'claude-code',
      installed: true,
      limited: false,
      model: null,
      name: 'claude-code@ab12cd34',
      nickname: 'work',
      title: 'Claude Code'
    }
    vi.spyOn(host, 'request').mockRejectedValue(new Error('not read'))
    const { calls, part } = registered(name =>
      name === 'route' ? EMPTY_ROUTE : { compact: true, enabled: true, stops: [stop] }
    )
    const place = { conversation: 'c-1', executor: 'claude-code', folder: '/work/site', instance: instance() }
    render(<>{part('session').render(place)}</>)

    fireEvent.click(await screen.findByTestId('session-route'))
    const others = await screen.findByTestId<HTMLSelectElement>('session-route-others')
    fireEvent.change(others, { target: { value: 'claude-code@ab12cd34' } })

    expect(await screen.findByTestId('session-route-stop')).toBeTruthy()
    expect(calls.at(-1)).toEqual([
      'set_route',
      {
        compact: true,
        conversation: 'c-1',
        enabled: true,
        stops: [{ account: 'ab12cd34', effort: null, executor: 'claude-code', model: null }]
      }
    ])
    expect(screen.getByTestId('session-route-ready').textContent).toBe('ready')
    expect(screen.getByTestId('session-route').textContent).toBe('someone@example.com · route 1')
  })

  it('says nothing for a session not started on a harness with one account', () => {
    $executorAccounts.set({ 'claude-code': { ...ACCOUNTS, accounts: ACCOUNTS.accounts.slice(0, 1) } })
    const { part } = registered(() => EMPTY_ROUTE)
    const { container } = render(
      <>{part('session').render({ conversation: null, executor: 'claude-code', folder: '/w', instance: null })}</>
    )

    expect(container.textContent).toBe('')
  })
})

describe("atop an executor's tree", () => {
  it('adds an account by its nickname alone, saying why one is refused', async () => {
    const { calls, part } = registered((name, params) =>
      name === 'add' && params.nickname === 'a b'
        ? Promise.reject(new Error('BookError: Give the account a nickname of letters'))
        : { account_id: 'ef56ab78', name: 'claude-code@ef56ab78' }
    )
    render(
      <>
        {part('executor').render({ account: '', base: 'claude-code', executor: 'claude-code', title: 'Claude Code' })}
      </>
    )

    fireEvent.click(screen.getByTestId('add-account'))
    const form = screen.getByTestId('add-account-form')
    expect(form.textContent).toContain('a folder of its own beside yours')
    fireEvent.change(within(form).getByTestId('account-nickname'), { target: { value: 'a b' } })
    fireEvent.submit(form)
    expect((await screen.findByRole('alert')).textContent).toBe('Give the account a nickname of letters')

    fireEvent.change(within(form).getByTestId('account-nickname'), { target: { value: ' side ' } })
    await act(async () => {
      fireEvent.submit(form)
      await Promise.resolve()
    })
    expect(calls.at(-1)).toEqual(['add', { executor: 'claude-code', nickname: 'side' }])
    expect(await screen.findByTestId('add-account')).toBeTruthy()
  })

  it('starts a project on an account, and removes the account only once the user confirms', async () => {
    const folder = (path: string) => ({ account_id: '', executor: 'claude-code', folder: path, updated_at: '' })
    vi.spyOn(host, 'request').mockResolvedValue({ folders: [folder('/work/api'), folder('/work/site')] })
    const listed = {
      accounts: [{ account_id: 'ab12cd34', folder: '/home/me/.claude-work', folders: ['/work/api'] }]
    }
    const { calls, part } = registered(name => (name === 'list' ? listed : {}))
    render(
      <>
        {part('executor').render({
          account: 'ab12cd34',
          base: 'claude-code',
          executor: 'claude-code@ab12cd34',
          title: 'Claude Code · work'
        })}
      </>
    )

    expect((await screen.findByTestId('account-folders')).textContent).toBe('/work/api×')
    fireEvent.change(await screen.findByTestId('account-folder-add'), { target: { value: '/work/site' } })
    expect(calls).toContainEqual(['set_folder', { account: 'ab12cd34', executor: 'claude-code', folder: '/work/site' }])

    fireEvent.click(screen.getByTestId('remove-account'))
    const asked = screen.getByTestId('remove-account-confirm')
    expect(asked.textContent).toContain('.claude-work) stays as it is')
    expect(calls.some(([name]) => name === 'remove')).toBe(false)
    fireEvent.click(within(asked).getByRole('button', { name: 'Remove work' }))
    expect(calls.at(-1)).toEqual(['remove', { account: 'ab12cd34', executor: 'claude-code' }])
  })
})
