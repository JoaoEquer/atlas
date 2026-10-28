import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import type { AtlasClient } from '@/protocol/client'
import type { McpList, McpServerRow, PluginRow, SkillRow } from '@/protocol/protocol.generated'
import { openHome, startApp } from '@/store/app'
import { matches } from '@/store/extensions'
import { $extensionsTab, $settingsSection, closeSettings, openSettings, SECTIONS } from '@/store/settings'

import { ExtensionsSection } from './extensions-view'

const plugin = (name: string, change: Partial<PluginRow> = {}): PluginRow => ({
  author: 'someone',
  bundled: false,
  channels: [],
  consent: 'c',
  consented: false,
  declaration: [`Run the tools of ${name}`],
  description: `The ${name} plugin.`,
  enabled: false,
  name,
  problem: null,
  running: false,
  secrets: [],
  settings: [],
  version: '1.0.0',
  ...change
})

const skill = (name: string, change: Partial<SkillRow> = {}): SkillRow => ({
  description: `How to ${name}.`,
  name,
  plugin: '',
  source: 'home',
  user_invocable: true,
  ...change
})

const server = (name: string, change: Partial<McpServerRow> = {}): McpServerRow => ({
  login: null,
  name,
  plugin: false,
  problem: null,
  state: 'connected',
  tools: 3,
  transport: 'stdio',
  ...change
})

interface Home {
  mcp: McpList
  plugins: PluginRow[]
  skills: SkillRow[]
}

const HOME: Home = {
  mcp: {
    problem: null,
    servers: [
      server('notes'),
      server('erp', {
        login: 'atlas mcp login erp',
        problem: 'The MCP server erp signs in with OAuth. Run `atlas mcp login erp` in a terminal to sign in.',
        state: 'left_out',
        tools: 0,
        transport: 'http'
      }),
      server('video', { plugin: true, tools: 1 })
    ]
  },
  plugins: [
    plugin('telegram', { bundled: true, description: 'Talk to Atlas from Telegram.' }),
    plugin('video', { consented: true, enabled: true, running: true })
  ],
  skills: [
    skill('atlas', { source: 'atlas', user_invocable: false }),
    skill('invoices'),
    skill('video:cut', { plugin: 'video', source: 'plugin' })
  ]
}

let hear: (event: EventParams) => void = () => undefined
let stop: () => void = () => undefined

function client(home: Home, calls: string[]): AtlasClient {
  const none = () => () => undefined
  const answers: Record<string, () => unknown> = {
    'mcp.list': () => home.mcp,
    'pairing.list': () => ({ pairings: [] }),
    'plugin.enable': () => ({ ...home.plugins[0], consented: true, enabled: true }),
    'plugin.list': () => ({ plugins: home.plugins }),
    'skill.list': () => ({ skills: home.skills })
  }

  return {
    call: (method: string) => {
      calls.push(method)

      return Promise.resolve(answers[method]?.() ?? {})
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
}

async function open(home: Home = HOME, tab?: Parameters<typeof openSettings>[1]): Promise<string[]> {
  const calls: string[] = []
  stop = startApp({ client: () => client(home, calls), shell: { focus: () => undefined, notify: () => undefined } })
  await openHome('work')
  render(<ExtensionsSection />)
  await act(async () => {
    openSettings('extensions', tab)
    await new Promise(resolve => setTimeout(resolve, 0))
  })

  return calls
}

afterEach(() => {
  cleanup()
  closeSettings()
  $extensionsTab.set('plugin')
  stop()
})

/** The item at `index`, which the test expects to be there. */
function at<T>(items: readonly T[], index: number): T {
  const item = items[index]
  if (item === undefined) {
    throw new Error(`Nothing at ${String(index)}.`)
  }

  return item
}

const tab = (kind: string) => screen.getByTestId(`extensions-tab-${kind}`)

describe('the settings screen has one Extensions section, in the place of Plugins and Executors', () => {
  it('lists Extensions among its sections, and no Plugins or Executors of their own', () => {
    expect(SECTIONS).toContain('extensions')
    expect(SECTIONS).not.toContain('plugins')
    expect(SECTIONS).not.toContain('executors')
  })
})

describe('Extensions shows a tab per kind, as the marketplace does', () => {
  it('opens on the plugins, each tab with its count, and reads every list once', async () => {
    const calls = await open()

    expect($settingsSection.get()).toBe('extensions')
    expect(tab('plugin').getAttribute('aria-selected')).toBe('true')
    expect(tab('plugin').textContent).toBe('Plugins2')
    expect(tab('skill').textContent).toBe('Skills3')
    expect(tab('mcp').textContent).toBe('MCPs3')
    expect(tab('executor').textContent).toBe('Executors0')
    expect(calls).toEqual(expect.arrayContaining(['plugin.list', 'mcp.list', 'skill.list']))
  })

  it("shows the plugins as cards, the Atlas team's first, and turns one on only after its consent", async () => {
    const calls = await open()
    const rows = screen.getAllByTestId('plugin-row')

    expect(rows.map(row => row.dataset.plugin)).toEqual(['telegram', 'video'])
    expect(within(at(rows, 0)).getByText('Official')).toBeTruthy()
    expect(within(at(rows, 1)).getByTestId('plugin-state').textContent).toBe('running')

    fireEvent.click(within(at(rows, 0)).getByTestId('plugin-enable'))
    expect(calls).not.toContain('plugin.enable')
    expect(within(at(rows, 0)).getByTestId('plugin-consent').textContent).toContain('Run the tools of telegram')

    await act(async () => {
      fireEvent.click(within(at(rows, 0)).getByTestId('plugin-consent-yes'))
      await Promise.resolve()
    })
    expect(calls).toContain('plugin.enable')
  })

  it('says where each skill came from, on its shelf', async () => {
    await open(HOME, 'skill')
    const cards = screen.getAllByTestId('skill-card')

    expect(cards.map(card => [card.dataset.name, card.dataset.source])).toEqual([
      ['atlas', 'atlas'],
      ['invoices', 'home'],
      ['video:cut', 'plugin']
    ])
    expect(at(cards, 0).textContent).toContain('The model loads it when it needs it')
    expect(at(cards, 1).textContent).toContain('/invoices')
    expect(at(cards, 2).textContent).toContain('From the plugin video')
  })

  it('says how each MCP server stands, and sends the one that signs in with OAuth to the terminal', async () => {
    await open(HOME, 'mcp')
    const cards = screen.getAllByTestId('mcp-card')
    const notes = at(cards, 0)
    const erp = at(cards, 1)
    const video = at(cards, 2)

    expect(cards.map(card => card.dataset.name)).toEqual(['notes', 'erp', 'video'])
    expect(within(notes).getByTestId('mcp-state').textContent).toBe('Connected · 3 tools')
    expect(within(notes).queryByTestId('mcp-sign-in')).toBeNull()
    expect(within(erp).getByTestId('mcp-state').textContent).toBe('Left out')
    expect(within(erp).getByTestId('mcp-login').textContent).toBe('atlas mcp login erp')
    expect(within(erp).getByTestId('mcp-sign-in').textContent).toContain('Sign in from a terminal')
    // The sign-in opens a browser where the home runs: the app gives the command, not a button.
    expect(within(erp).queryByRole('button', { name: /sign in/i })).toBeNull()
    expect(within(video).getByTestId('mcp-state').textContent).toBe('Connected · 1 tool')
    expect(video.textContent).toContain('From a plugin that is on')
  })

  it('reads the MCP servers again when the server says they changed', async () => {
    const home: Home = { ...HOME, mcp: { problem: null, servers: [server('erp', { state: 'connecting', tools: 0 })] } }
    await open(home, 'mcp')
    expect(screen.getByTestId('mcp-state').textContent).toBe('Connecting…')

    home.mcp = { problem: null, servers: [server('erp', { tools: 5 })] }
    await act(async () => {
      hear({ payload: {}, type: 'mcp.changed' })
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(screen.getByTestId('mcp-state').textContent).toBe('Connected · 5 tools')
  })

  it('says why mcp.json could not be read, and that there is no server', async () => {
    await open({ ...HOME, mcp: { problem: 'mcp.json is not valid JSON.', servers: [] } }, 'mcp')

    expect(screen.getByTestId('mcp-problem').textContent).toBe('mcp.json is not valid JSON.')
    expect(screen.getByTestId('extensions-panel').textContent).toContain('No MCP servers.')
  })

  it('searches the tab on screen', async () => {
    await open(HOME, 'skill')
    fireEvent.change(screen.getByTestId('extensions-search'), { target: { value: 'invo' } })

    expect(screen.getAllByTestId('skill-card').map(card => card.dataset.name)).toEqual(['invoices'])

    fireEvent.click(tab('plugin'))
    fireEvent.change(screen.getByTestId('extensions-search'), { target: { value: 'nothing like it' } })
    expect(screen.queryAllByTestId('plugin-row')).toHaveLength(0)
    expect(screen.getByTestId('extensions-panel').textContent).toContain('Nothing matches the search.')
  })
})

describe('the search', () => {
  it('matches every word, case aside, in any of the texts', () => {
    expect(matches('', 'anything')).toBe(true)
    expect(matches('Tele GRAM', 'telegram', 'Talk to Atlas')).toBe(true)
    expect(matches('tele voice', 'telegram', 'Talk to Atlas')).toBe(false)
  })
})
