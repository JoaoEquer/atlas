import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import type { AtlasClient } from '@/protocol/client'
import type { ExecutorConnector, ExecutorRow, ExecutorUsageRow, SlashCommandRow } from '@/protocol/protocol.generated'
import { current, openHome, showExecutor, startApp } from '@/store/app'

import { ExecutorPanel } from './executor-panel'

const EXECUTOR: ExecutorRow = {
  account: 'someone@example.com',
  account_folders: false,
  modes: [],
  files: false,
  account_id: '',
  accounts: true,
  base: '',
  consent: '',
  consent_again: false,
  declaration: [],
  enabled: true,
  installed: true,
  latest: '',
  logged_in: true,
  minimum: '',
  name: 'claude-code',
  nickname: '',
  plan: 'max',
  plugin: 'claude-code',
  problem: '',
  supported: true,
  title: 'Claude Code',
  update: '',
  updating: false,
  version: '2.1.296'
}

const USAGE: ExecutorUsageRow = {
  executor: 'claude-code',
  extra_usage: false,
  listed: true,
  plan: 'max',
  read_at: new Date().toISOString(),
  unavailable: '',
  windows: [
    { id: 'five_hour', minutes: 300, model: '', resets_at: '', title: 'Current session', used: 45 },
    { id: 'seven_day', minutes: 10080, model: '', resets_at: '', title: 'Current week (all models)', used: 25 },
    { id: 'model:Fable', minutes: 10080, model: 'Fable', resets_at: '', title: 'Current week (Fable)', used: 96 }
  ]
}

let stop: () => void = () => undefined

afterEach(() => {
  cleanup()
  stop()
})

/** The app on a client whose `executor.usage` answers `usage`, the panel on Claude Code. */
async function showPanel(usage: () => Promise<ExecutorUsageRow>, answers: Record<string, unknown> = {}) {
  const calls: string[] = []
  let hear: (event: EventParams) => void = () => undefined
  const none = () => () => undefined
  const client = {
    call: (method: string) => {
      calls.push(method)

      if (method in answers) {
        return Promise.resolve(answers[method])
      }

      return method === 'executor.usage' ? usage() : new Promise(() => undefined)
    },
    onError: none,
    onEvent: (handler: (event: EventParams) => void) => {
      hear = handler

      return () => undefined
    },
    onRequest: none,
    onResync: none,
    onState: none,
    start: vi.fn(),
    stop: vi.fn(),
    watch: vi.fn()
  } as unknown as AtlasClient
  stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
  await openHome('work')
  current().$workbench.set({ executors: [EXECUTOR], instances: [] })
  render(<ExecutorPanel executor={EXECUTOR} />)
  await act(async () => {
    showExecutor('claude-code')
    await Promise.resolve()
  })

  return {
    calls,
    hear: (event: EventParams) => {
      act(() => {
        hear(event)
      })
    }
  }
}

const bars = () =>
  screen
    .getAllByTestId('usage-window')
    .map(row => [
      row.getAttribute('data-window'),
      row.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow'),
      row.textContent
    ])

describe("the side panel's plan usage", () => {
  it('is read when the executor shows, a bar per window, and read again only when old or asked', async () => {
    const { calls } = await showPanel(() => Promise.resolve(USAGE))

    expect(bars()).toEqual([
      ['five_hour', '45', 'Current session45% used'],
      ['seven_day', '25', 'Current week (all models)25% used'],
      ['model:Fable', '96', 'Current week (Fable)96% used']
    ])
    expect(screen.getByTestId('plan-usage').textContent).toContain('max')
    expect(screen.getAllByRole('progressbar')[2]?.firstElementChild?.className).toContain('bg-failed')

    // Shown again within two minutes: what was read stays; the ↻ reads again.
    act(() => {
      showExecutor('claude-code')
    })
    expect(calls.filter(method => method === 'executor.usage')).toHaveLength(1)
    await act(async () => {
      fireEvent.click(screen.getByTestId('usage-refresh'))
      await Promise.resolve()
    })
    expect(calls.filter(method => method === 'executor.usage')).toHaveLength(2)
  })

  it('takes what an instance tells as it works', async () => {
    const { hear } = await showPanel(() => Promise.resolve(USAGE))

    const windows = USAGE.windows.map(window => (window.id === 'five_hour' ? { ...window, used: 52 } : window))
    hear({ conversation_id: '', payload: { ...USAGE, windows }, type: 'executor.usage' })

    expect(bars()[0]).toEqual(['five_hour', '52', 'Current session52% used'])
  })

  it('says why a login has none', async () => {
    await showPanel(() => Promise.resolve({ ...USAGE, plan: '', unavailable: 'An API key pays.', windows: [] }))

    expect(screen.getByTestId('plan-usage').textContent).toContain('no plan limits')
    expect(screen.queryByTestId('usage-window')).toBeNull()
  })

  it('warns when past its limits the plan bills rather than stops (21/10/2026)', async () => {
    await showPanel(() => Promise.resolve({ ...USAGE, extra_usage: true }))

    expect(screen.getByTestId('usage-extra').textContent).toContain('Extra usage is on')
  })

  it('says nothing of extra usage when it is off or not known', async () => {
    await showPanel(() => Promise.resolve({ ...USAGE, extra_usage: null }))

    expect(screen.queryByTestId('usage-extra')).toBeNull()
  })

  it('says why a read failed', async () => {
    await showPanel(() => Promise.reject(new Error('Claude Code did not say its plan usage.')))

    expect(screen.getByRole('alert').textContent).toContain('did not say its plan usage')
  })
})

const server = (name: string, status: string, origin: string, plugin = ''): ExecutorConnector => ({
  error: '',
  name,
  origin,
  plugin,
  status,
  tools: status === 'connected' ? 2 : 0
})

const skill = (name: string, group = 'skill'): SlashCommandRow => ({
  description: `The ${name}`,
  group,
  hint: '',
  name
})

const WHAT_IT_HAS = {
  'executor.commands': {
    commands: [skill('compact', 'command'), skill('review'), skill('marketing:seo-audit'), skill('design:critique')]
  },
  'executor.connectors': {
    connectors: [
      server('files', 'connected', 'user'),
      server('Drive', 'connected', 'account'),
      server('Gmail', 'needs-auth', 'account'),
      server('Stripe', 'failed', 'account'),
      server('slack', 'needs-auth', 'plugin', 'marketing'),
      server('notion', 'connected', 'plugin', 'marketing')
    ],
    listed: true
  }
}

describe('what the side panel says the executor has (22/10/2026)', () => {
  it('lists its own skills, a plugin’s under the plugin, and no command', async () => {
    await showPanel(() => Promise.resolve(USAGE), WHAT_IT_HAS)

    const skills = screen.getByTestId('executor-skills')
    expect(skills.querySelectorAll('[data-testid=skill-row]')).toHaveLength(1)
    expect(skills.textContent).toContain('/review')
    expect(skills.textContent).not.toContain('compact')

    const plugins = screen
      .getAllByTestId('plugin-row')
      .map(row => [row.getAttribute('data-plugin'), row.querySelector('summary')?.textContent])
    expect(plugins).toEqual([
      ['design', '›design1 skill'],
      ['marketing', '›marketing1 skill · 1 of 2 MCP servers']
    ])
  })

  it('shows the MCP servers that are up, and those down only when asked', async () => {
    await showPanel(() => Promise.resolve(USAGE), WHAT_IT_HAS)

    const servers = screen.getByTestId('executor-servers')
    const names = () =>
      [...servers.querySelectorAll('[data-testid=connector-row]')].map(row => row.getAttribute('data-status'))
    expect(servers.textContent).toContain('2 of 4')
    expect(names()).toEqual(['connected', 'connected'])

    fireEvent.click(screen.getByTestId('servers-down'))
    expect(names()).toEqual(['connected', 'connected', 'needs-auth', 'failed'])

    fireEvent.click(screen.getByTestId('servers-down'))
    expect(names()).toHaveLength(2)
  })
})
