import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import type { AtlasClient } from '@/protocol/client'
import type { MarketplaceDone, MarketplaceList, MarketplacePage, MarketplaceRow } from '@/protocol/protocol.generated'
import { $extensionsTab, $restored, $view, DRAFT, openHome, showMarketplace, startApp } from '@/store/app'

import { MarketplaceView } from './marketplace-view'

function row(name: string, change: Partial<MarketplaceRow> = {}): MarketplaceRow {
  return {
    author: 'someone',
    category: '',
    image: '',
    installs: null,
    kind: 'skill',
    license: 'MIT',
    name,
    problem: null,
    shipped: false,
    source: 'atlas',
    source_url: `https://github.com/someone/tools/tree/abc/${name}`,
    state: 'available',
    summary: `The ${name} item.`,
    tier: 'community',
    title: name.toUpperCase(),
    update: false,
    updated: '',
    version: '1.0.0',
    ...change
  }
}

function page(item: MarketplaceRow, change: Partial<MarketplacePage> = {}): MarketplacePage {
  return {
    entry: '',
    homepage: '',
    item,
    link_base: 'https://github.com/someone/tools/blob/abc/pdf/',
    needs: [],
    picture_base: 'https://raw.githubusercontent.com/someone/tools/abc/pdf/',
    readme: `# ${item.title}\n\nHow to use it.`,
    screenshots: [],
    secrets: [],
    ...change
  }
}

type Answer = (method: string, params: Record<string, unknown>) => unknown

/** The app's handler of what the server says on its own, once the app started. */
let hear: (event: EventParams) => void = () => undefined

/** A client that never connects, whose calls `answer` settles, each kept in `calls`. */
function client(answer: Answer, calls: [string, Record<string, unknown>][]): AtlasClient {
  const none = () => () => undefined

  return {
    call: (method: string, params: Record<string, unknown>) => {
      calls.push([method, params])

      return Promise.resolve(answer(method, params))
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

let stop: () => void = () => undefined

afterEach(() => {
  cleanup()
  stop()
})

async function open(answer: Answer): Promise<[string, Record<string, unknown>][]> {
  const calls: [string, Record<string, unknown>][] = []
  stop = startApp({
    client: () => client(answer, calls),
    shell: { focus: () => undefined, notify: () => undefined }
  })
  await openHome('work')
  render(<MarketplaceView />)
  await act(async () => {
    showMarketplace()
    await Promise.resolve()
  })

  return calls
}

const listing = (items: MarketplaceRow[], change: Partial<MarketplaceList> = {}): MarketplaceList => ({
  description: '',
  items,
  name: 'Test',
  problem: null,
  read_at: '2026-10-25T12:00:00Z',
  source_problems: [],
  ...change
})

const cards = () => screen.getAllByTestId('marketplace-card').map(card => card.dataset.name)

describe('the marketplace lists each kind in its tab, the Atlas team first', () => {
  it('opens on the plugins, and a tab shows its kind with its count', async () => {
    const items = [
      row('telegram', { kind: 'plugin', shipped: true, state: 'off', tier: 'official' }),
      row('video', { kind: 'plugin' }),
      row('pdf'),
      row('github', { shipped: true, state: 'installed', tier: 'official' }),
      row('docs', { kind: 'mcp' })
    ]
    const calls = await open(method => (method === 'marketplace.list' ? listing(items) : {}))

    expect(calls[0]).toEqual(['marketplace.list', { query: '', refresh: false }])
    expect(cards()).toEqual(['telegram', 'video'])
    expect(screen.getByTestId('marketplace-tab-skill').textContent).toContain('2')
    fireEvent.click(screen.getByTestId('marketplace-tab-skill'))
    expect(cards()).toEqual(['github', 'pdf'])
    expect(screen.getByText('By the Atlas team')).toBeTruthy()
    fireEvent.change(screen.getByTestId('marketplace-search'), { target: { value: 'pdf' } })
    expect(cards()).toEqual(['pdf'])
  })

  it('says when GitHub could not be reached, showing the last read', async () => {
    await open(() => listing([row('pdf')], { problem: 'GitHub answered HTTP 503.' }))

    expect(screen.getByTestId('marketplace-notice').textContent).toContain('GitHub answered HTTP 503.')
  })
})

describe('the plugin directory says what it gives', () => {
  const plugin = (name: string, change: Partial<MarketplaceRow> = {}) =>
    row(`claude-plugins:${name}`, { kind: 'plugin', source: 'claude-plugins', title: name, version: '', ...change })

  it('filters by category, and a new tab starts with all of them', async () => {
    await open(() =>
      listing([
        plugin('audit', { category: 'security', installs: 1200 }),
        plugin('sketch', { category: 'design' }),
        row('pdf')
      ])
    )

    expect(cards()).toEqual(['claude-plugins:audit', 'claude-plugins:sketch'])
    expect(screen.getAllByTestId('marketplace-card-meta')[0]?.textContent).toBe('1,200 installs')
    const chip = (value: string) => screen.getAllByTestId('marketplace-category').find(c => c.dataset.value === value)
    fireEvent.click(chip('design') as HTMLElement)
    expect(cards()).toEqual(['claude-plugins:sketch'])
    expect(chip('design')?.getAttribute('aria-pressed')).toBe('true')

    fireEvent.click(screen.getByTestId('marketplace-tab-skill'))
    expect(screen.queryByTestId('marketplace-category')).toBeNull()
    fireEvent.click(screen.getByTestId('marketplace-tab-plugin'))
    expect(cards()).toEqual(['claude-plugins:audit', 'claude-plugins:sketch'])
  })

  it("opens on the most installed, and a category's whole list is a click away", async () => {
    const filed = Array.from({ length: 15 }, (_, at) => plugin(`p${String(at)}`, { category: 'design' }))
    await open(() => listing(filed))

    expect(cards()).toHaveLength(15)
    expect(screen.getByText('Most installed')).toBeTruthy()
    fireEvent.click(screen.getByTestId('marketplace-see-all'))

    expect(screen.queryByTestId('marketplace-directory')).toBeNull()
    expect(cards()).toHaveLength(15)
  })

  it("leaves the kind's own icon when a picture does not load", async () => {
    await open(() => listing([plugin('notes', { image: 'https://github.com/someone.png?size=96' })]))

    fireEvent.error(screen.getByTestId('marketplace-picture'))

    expect(screen.queryByTestId('marketplace-picture')).toBeNull()
  })

  it('shows no author line when nobody is named, and no number it was not given', async () => {
    await open(() => listing([plugin('notes', { author: '' })]))

    expect(screen.getByTestId('marketplace-card').textContent).not.toContain('by ')
    expect(screen.getByTestId('marketplace-card-meta').textContent).toBe('')
  })
})

describe('the outside catalogs (step 24) follow, each on its shelf, searched by the server', () => {
  it('shows what the registry found, newest data on the card, and asks again once typing stops', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const registry = row('mcp-registry:com.example/mcp', {
      author: 'example.com',
      kind: 'mcp',
      source: 'mcp-registry',
      title: 'Example',
      updated: '2026-11-01'
    })
    const calls = await open((_, params) =>
      listing(params.query === 'none' ? [row('docs', { kind: 'mcp' })] : [row('docs', { kind: 'mcp' }), registry], {
        source_problems: params.query === 'none' ? ['The MCP Registry could not be read: down'] : []
      })
    )
    fireEvent.click(screen.getByTestId('marketplace-tab-mcp'))

    expect(cards()).toEqual(['docs', 'mcp-registry:com.example/mcp'])
    expect(screen.getByText('From the MCP Registry')).toBeTruthy()
    const metas = screen.getAllByTestId('marketplace-card-meta').map(meta => meta.textContent)
    expect(metas[1]).toBe('1.0.0 · updated 2026-11-01')
    fireEvent.change(screen.getByTestId('marketplace-search'), { target: { value: 'none' } })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(calls.at(-1)).toEqual(['marketplace.list', { query: 'none', refresh: false }])
    expect(screen.getByTestId('marketplace-notice').textContent).toContain('The MCP Registry could not be read')
    vi.useRealTimers()
  })
})

describe('an item installs from its card, and its page shows what it is and needs', () => {
  it('installs, says what came along, and reads the list again', async () => {
    const installed = row('pdf', { state: 'installed' })
    let rows = [row('pdf')]
    const calls = await open(method => {
      if (method === 'marketplace.install') {
        rows = [installed]

        return { changed: ['docs', 'pdf'], item: installed, local_changes: false } satisfies MarketplaceDone
      }

      return method === 'marketplace.list' ? listing(rows) : {}
    })
    fireEvent.click(screen.getByTestId('marketplace-tab-skill'))
    await act(async () => {
      fireEvent.click(within(screen.getByTestId('marketplace-card')).getByTestId('marketplace-action'))
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(calls.some(([method, params]) => method === 'marketplace.install' && params.name === 'pdf')).toBe(true)
    expect(calls.filter(([method]) => method === 'marketplace.list')).toHaveLength(2)
    expect(within(screen.getByTestId('marketplace-card')).getByTestId('marketplace-action').dataset.action).toBe(
      'installed'
    )
  })

  it("opens a page with the README, what the skill needs, and the server's secrets", async () => {
    const pdf = row('pdf')
    const calls = await open((method, params) => {
      if (method === 'marketplace.page') {
        return page(pdf, {
          needs: [
            { kind: 'program', name: 'ffmpeg', state: 'missing' },
            { kind: 'server', name: 'docs', state: 'brought' }
          ],
          secrets: [{ description: 'The docs key.', name: 'DOCS_KEY', stored: false, url: '' }]
        })
      }

      if (method === 'marketplace.secret') {
        return page(pdf, { secrets: [{ description: '', name: String(params.secret), stored: true, url: '' }] })
      }

      return method === 'marketplace.list' ? listing([pdf]) : {}
    })
    fireEvent.click(screen.getByTestId('marketplace-tab-skill'))
    await act(async () => {
      fireEvent.click(screen.getByTestId('marketplace-card'))
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(screen.getByTestId('marketplace-page')).toBeTruthy()
    expect(screen.getByTestId('marketplace-readme').textContent).toContain('How to use it.')
    const needs = screen.getAllByTestId('marketplace-need')
    expect(needs.map(need => need.dataset.state)).toEqual(['missing', 'brought'])
    expect(needs[0]?.textContent).toContain('ffmpeg')
    fireEvent.change(screen.getByTestId('marketplace-secret-input'), { target: { value: 'k-123' } })
    await act(async () => {
      fireEvent.click(screen.getByTestId('marketplace-secret-save'))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    expect(calls).toContainEqual(['marketplace.secret', { name: 'pdf', secret: 'DOCS_KEY', value: 'k-123' }])
    expect(screen.getByTestId('marketplace-secret').dataset.stored).toBe('true')
  })

  it('asks before an update replaces what the home changed, then updates with force', async () => {
    const pdf = row('pdf', { state: 'installed', update: true })
    const calls = await open((method, params) => {
      if (method === 'marketplace.update') {
        return {
          changed: params.force ? ['pdf'] : [],
          item: pdf,
          local_changes: !params.force
        } satisfies MarketplaceDone
      }

      if (method === 'marketplace.page') {
        return page(pdf)
      }

      return method === 'marketplace.list' ? listing([pdf]) : {}
    })
    fireEvent.click(screen.getByTestId('marketplace-tab-skill'))
    await act(async () => {
      fireEvent.click(screen.getByTestId('marketplace-card'))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    await act(async () => {
      fireEvent.click(within(screen.getByTestId('marketplace-page')).getByTestId('marketplace-action'))
      await new Promise(resolve => setTimeout(resolve, 0))
    })
    expect(screen.getByTestId('marketplace-confirm').textContent).toContain('pdf was changed here')
    await act(async () => {
      fireEvent.click(screen.getByTestId('marketplace-update-anyway'))
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(calls.filter(([method]) => method === 'marketplace.update').map(([, params]) => params)).toEqual([
      { force: false, name: 'pdf' },
      { force: true, name: 'pdf' }
    ])
    expect(screen.queryByTestId('marketplace-confirm')).toBeNull()
  })

  it("turns a plugin on through the consent on Extensions' Plugins tab", async () => {
    await open(method =>
      method === 'marketplace.list' ? listing([row('video', { kind: 'plugin', state: 'off' })]) : {}
    )
    fireEvent.click(within(screen.getByTestId('marketplace-card')).getByTestId('marketplace-action'))

    expect($view.get()).toBe('extensions')
    expect($extensionsTab.get()).toBe('plugin')
  })

  it('reads the list again when a plugin is turned on over it, so its card says On', async () => {
    let state: MarketplaceRow['state'] = 'off'
    const calls = await open(method =>
      method === 'marketplace.list' ? listing([row('video', { kind: 'plugin', state })]) : {}
    )
    const action = () => within(screen.getByTestId('marketplace-card')).getByTestId('marketplace-action')
    expect(action().dataset.action).toBe('turn-on')

    state = 'on'
    await act(async () => {
      hear({ payload: {}, type: 'plugins.changed' })
      await new Promise(resolve => setTimeout(resolve, 0))
    })

    expect(calls.filter(([method]) => method === 'marketplace.list')).toHaveLength(2)
    expect(action().dataset.action).toBe('on')
  })
})

describe('making one starts from the marketplace', () => {
  it('opens a new conversation with the skill that writes plugins in its box', async () => {
    await open(() => listing([]))
    fireEvent.click(screen.getByTestId('marketplace-make'))

    expect($view.get()).toBe('conversation')
    expect($restored.get()[DRAFT]).toBe('/write-plugin ')
    expect(screen.getByRole('link', { name: 'Plugin guide' }).getAttribute('href')).toBe(
      'https://atlasv4.com/docs/extending/plugins/'
    )
  })
})
