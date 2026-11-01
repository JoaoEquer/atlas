import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { AtlasClient } from '@/protocol/client'
import type { ExecutorRow, InstanceRow, SessionRouteRow } from '@/protocol/protocol.generated'
import { current, openHome, startApp } from '@/store/app'
import { applyActivity } from '@/store/session'
import { EMPTY_THREAD } from '@/store/thread'

import { SessionView } from './session-view'

const ROW: ExecutorRow = {
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
  ...ROW,
  account_id: 'ab12cd34',
  base: 'claude-code',
  name: 'claude-code@ab12cd34',
  nickname: 'work',
  title: 'Claude Code · work'
}
const CODEX: ExecutorRow = { ...ROW, name: 'codex', plugin: 'codex', title: 'Codex' }
const OFF: ExecutorRow = { ...ROW, enabled: false, name: 'pi', plugin: 'pi', title: 'Pi' }

const INSTANCE: InstanceRow = {
  account: 'someone@example.com',
  account_id: '',
  accounts: true,
  atlas_answers: false,
  auto: true,
  can_auto: true,
  can_model: false,
  chosen_model: '',
  commands: [],
  effort: '',
  executor: 'claude-code',
  folder: 'C:\\work\\site',
  instance_id: 'in-1',
  last_tool: null,
  model: '',
  owner: 'user',
  parent_id: '',
  problem: '',
  session_id: '',
  state: 'idle',
  steer: true,
  subagent_id: '',
  title: 'Claude Code',
  tools: 0,
  transcript_id: 'c-1',
  waiting: 0
}

const route = (stops: SessionRouteRow['stops'], enabled = true, compact = true): SessionRouteRow => ({
  compact,
  enabled,
  instance_id: 'in-1',
  stops
})
type Stop = SessionRouteRow['stops'][number]
const MAIN_STOP: Stop = {
  account_id: '',
  effort: null,
  executor: 'claude-code',
  installed: true,
  limited: false,
  model: null,
  name: 'claude-code',
  nickname: '',
  title: 'Claude Code'
}
const WORK_STOP: Stop = { ...MAIN_STOP, account_id: 'ab12cd34', name: 'claude-code@ab12cd34', nickname: 'work' }
const CODEX_STOP: Stop = { ...MAIN_STOP, executor: 'codex', name: 'codex', title: 'Codex' }
/** What a stop is called on the path, as its row says it. */
const names = () => screen.getAllByTestId('session-route-name').map(name => name.textContent)

let stop: () => void = () => undefined

afterEach(() => {
  cleanup()
  stop()
})
beforeAll(() => {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => undefined
})

/** The session on the instance `in-1` open, on a client answering the route's calls. */
async function openSessionOn(answer: (method: string, params: unknown) => unknown): Promise<[string, unknown][]> {
  const calls: [string, unknown][] = []
  const none = () => () => undefined
  const client = {
    call: (method: string, params: unknown) => {
      calls.push([method, params])
      const found = answer(method, params)

      return found === undefined ? new Promise(() => undefined) : Promise.resolve(found)
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
  session.$connection.set({ error: null, state: 'open' })
  session.$workbench.set({ executors: [ROW, WORK, CODEX, OFF], instances: [INSTANCE] })
  session.$openSession.set({
    choice: null,
    elsewhere: '',
    executor: 'claude-code',
    expired: false,
    folder: 'C:\\work\\site',
    instanceId: 'in-1',
    sessionId: '',
    title: 'The site'
  })
  render(<SessionView />)

  return calls
}

const listed = (calls: [string, unknown][], method: string) => calls.filter(([called]) => called === method)

describe("a session's route across executors (asked for by the user on 21/10/2026)", () => {
  it('reads the route as a path, adds a stop of another harness, orders it and turns it off', async () => {
    let kept = route([MAIN_STOP])
    const calls = await openSessionOn((method, params) => {
      if (method === 'workbench.route') {
        return kept
      }

      if (method === 'workbench.set_route') {
        const given = params as { enabled: boolean; targets: { account_id: string; executor: string }[] }
        const stops = given.targets.map(target => (target.executor === 'codex' ? CODEX_STOP : MAIN_STOP))
        kept = route(stops, given.enabled)

        return kept
      }

      return undefined
    })

    const pill = await screen.findByTestId('session-route')
    await vi.waitFor(() => {
      expect(pill.textContent).toBe('Route · 1')
    })
    fireEvent.click(pill)
    const editor = screen.getByTestId('session-route-editor')
    expect(names()).toEqual(['1. Claude Code · main account'])
    // The stop it runs on now is marked on the path.
    expect(within(editor).getByTestId('session-route-ready').textContent).toBe('here')
    const others = screen.getByTestId<HTMLSelectElement>('session-route-others')
    expect([...others.options].map(option => option.textContent)).toEqual([
      'Add a stop…',
      'Claude Code · work',
      'Codex · main account'
    ])

    fireEvent.change(others, { target: { value: 'codex' } })
    await vi.waitFor(() => {
      expect(screen.getAllByTestId('session-route-stop')).toHaveLength(2)
    })
    expect(listed(calls, 'workbench.set_route')[0]).toEqual([
      'workbench.set_route',
      {
        compact: true,
        enabled: true,
        instance_id: 'in-1',
        targets: [
          { account_id: '', effort: null, executor: 'claude-code', model: null },
          { account_id: '', effort: null, executor: 'codex', model: null }
        ]
      }
    ])

    fireEvent.click(screen.getByRole('button', { name: 'Codex · main account sooner' }))
    await vi.waitFor(() => {
      expect(names()).toEqual(['1. Codex · main account', '2. Claude Code · main account'])
    })
    expect(screen.getByTestId('session-route').textContent).toBe('Route · 2')

    fireEvent.click(screen.getByTestId('session-route-enabled'))
    await vi.waitFor(() => {
      expect(listed(calls, 'workbench.set_route')).toHaveLength(3)
    })
    expect(listed(calls, 'workbench.set_route')[2]?.[1]).toMatchObject({ enabled: false })
    await vi.waitFor(() => {
      expect(screen.getByTestId('session-route').textContent).toBe('Route')
    })
  })

  it('gives each stop the model and effort it goes on with, says whether it can take over and how much of its plan is used, and compacts first unless turned off', async () => {
    let kept = route([MAIN_STOP, { ...WORK_STOP, limited: true }, CODEX_STOP])
    const calls = await openSessionOn((method, params) => {
      if (method === 'workbench.route') {
        return kept
      }

      if (method === 'workbench.set_route') {
        const given = params as {
          compact: boolean
          enabled: boolean
          targets: { effort: null | string; model: null | string }[]
        }
        kept = route(
          kept.stops.map((stop, index) => ({
            ...stop,
            effort: given.targets[index]?.effort ?? null,
            model: given.targets[index]?.model ?? null
          })),
          given.enabled,
          given.compact
        )

        return kept
      }

      return undefined
    })
    const session = current()
    session.$workbench.set({
      executors: [ROW, WORK, { ...CODEX, logged_in: false }, OFF],
      instances: [INSTANCE]
    })
    const model = (id: string, efforts: string[]) => ({
      default_effort: '',
      description: '',
      efforts,
      id,
      name: id === 'default' ? 'Default' : 'Sonnet',
      resolved: ''
    })
    for (const name of ['claude-code', WORK.name, 'codex']) {
      session.$executorModels.setKey(name, [
        model('default', ['low', 'medium', 'high', 'max']),
        model('sonnet', ['low', 'high'])
      ])
    }
    session.$executorUsage.setKey(WORK.name, {
      error: null,
      reading: false,
      usage: {
        executor: WORK.name,
        extra_usage: null,
        listed: true,
        plan: 'max',
        read_at: new Date().toISOString(),
        unavailable: '',
        windows: [{ id: 'five', minutes: 300, model: '', resets_at: '', title: 'Current session', used: 81.6 }]
      }
    })

    fireEvent.click(await screen.findByTestId('session-route'))
    await vi.waitFor(() => {
      expect(screen.getAllByTestId('session-route-ready').map(state => state.textContent)).toEqual([
        'here',
        'at its limit · 82% used',
        'not logged in'
      ])
    })

    fireEvent.change(screen.getByRole('combobox', { name: 'Model on Claude Code · work' }), {
      target: { value: 'sonnet' }
    })
    await vi.waitFor(() => {
      expect(screen.getByRole<HTMLSelectElement>('combobox', { name: 'Model on Claude Code · work' }).value).toBe(
        'sonnet'
      )
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'Effort on Claude Code · work' }), {
      target: { value: 'high' }
    })
    await vi.waitFor(() => {
      expect(listed(calls, 'workbench.set_route')).toHaveLength(2)
    })
    expect(listed(calls, 'workbench.set_route')[1]?.[1]).toMatchObject({
      targets: [
        { effort: null, model: null },
        { effort: 'high', model: 'sonnet' },
        { effort: null, model: null }
      ]
    })
    // On another harness, "as now" is its own default model.
    expect(
      [...screen.getByRole<HTMLSelectElement>('combobox', { name: 'Model on Codex · main account' }).options].map(
        option => option.textContent
      )[0]
    ).toBe('its default')

    const compact = screen.getByTestId<HTMLInputElement>('session-route-compact')
    expect(compact.checked).toBe(true)
    fireEvent.click(compact)
    await vi.waitFor(() => {
      expect(listed(calls, 'workbench.set_route')).toHaveLength(3)
    })
    expect(listed(calls, 'workbench.set_route')[2]?.[1]).toMatchObject({ compact: false })
  })

  it('closes on a click outside it, on Escape and on its own button (asked for on 22/10/2026)', async () => {
    await openSessionOn(method => (method === 'workbench.route' ? route([MAIN_STOP]) : undefined))
    const pill = await screen.findByTestId('session-route')

    fireEvent.click(pill)
    fireEvent.pointerDown(screen.getByTestId('session-route-editor'))
    expect(screen.getByTestId('session-route-panel')).toBeTruthy()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByTestId('session-route-panel')).toBeNull()

    fireEvent.click(pill)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByTestId('session-route-panel')).toBeNull()

    fireEvent.click(pill)
    fireEvent.click(screen.getByTestId('session-route-close'))
    expect(screen.queryByTestId('session-route-panel')).toBeNull()

    fireEvent.pointerDown(pill)
    fireEvent.click(pill)
    expect(screen.getByTestId('session-route-panel')).toBeTruthy()
    fireEvent.pointerDown(pill)
    fireEvent.click(pill)
    expect(screen.queryByTestId('session-route-panel')).toBeNull()
  })

  it('says on the session where it went on, and why it could not', () => {
    const moved = applyActivity(
      EMPTY_THREAD,
      {
        attachments: [],
        call_id: '',
        failed: false,
        instance_id: 'in-1',
        kind: 'route',
        text: 'Codex · main account',
        tool: ''
      },
      1,
      0
    )
    const stuck = applyActivity(
      moved,
      {
        attachments: [],
        call_id: '',
        failed: true,
        instance_id: 'in-1',
        kind: 'route',
        text: "No destination in the session's route can go on now.",
        tool: ''
      },
      2,
      0
    )

    const noted = applyActivity(
      stuck,
      {
        attachments: [],
        call_id: '',
        failed: false,
        instance_id: 'in-1',
        kind: 'note',
        text: 'Codex · main account does not offer opus: the session goes on on its default model.',
        tool: ''
      },
      3,
      0
    )

    expect(noted.items).toEqual([
      { key: 'a1', kind: 'status', text: 'The plan limit was reached: it went on on Codex · main account.' },
      { key: 'a2', kind: 'status', text: "No destination in the session's route can go on now." },
      {
        key: 'a3',
        kind: 'status',
        text: 'Codex · main account does not offer opus: the session goes on on its default model.'
      }
    ])
  })
})
