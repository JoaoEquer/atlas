import { describe, expect, it } from 'vitest'

import type { MarketplaceList, MarketplacePage, MarketplaceRow } from '@/protocol/protocol.generated'

import { actionOf, categories, counts, resolved, shown, withRow } from './marketplace'

function row(name: string, change: Partial<MarketplaceRow> = {}): MarketplaceRow {
  return {
    author: 'someone',
    image: '',
    category: '',
    installs: null,
    kind: 'skill',
    license: 'MIT',
    name,
    problem: null,
    shipped: false,
    source: 'atlas',
    source_url: '',
    state: 'available',
    summary: `The ${name} item.`,
    tier: 'community',
    title: name,
    update: false,
    updated: '',
    version: '',
    ...change
  }
}

function list(items: MarketplaceRow[], change: Partial<MarketplaceList> = {}): MarketplaceList {
  return {
    description: '',
    items,
    name: 'Test',
    problem: null,
    read_at: '2026-10-25T12:00:00Z',
    source_problems: [],
    ...change
  }
}

describe("an item's button says what a click does", () => {
  it('by where the item stands in the home', () => {
    expect(actionOf(row('a'))).toBe('install')
    expect(actionOf(row('a', { problem: 'You have a skill named a already.' }))).toBe('unavailable')
    expect(actionOf(row('a', { state: 'installed' }))).toBe('installed')
    expect(actionOf(row('a', { state: 'installed', update: true }))).toBe('update')
    expect(actionOf(row('a', { kind: 'plugin', state: 'off' }))).toBe('turn-on')
    expect(actionOf(row('a', { kind: 'plugin', state: 'on' }))).toBe('on')
    expect(actionOf(row('a', { state: 'retired', update: true }))).toBe('retired')
  })
})

describe('a tab shows its kind, the Atlas team first', () => {
  const listing = list([
    row('zip', { title: 'Zip' }),
    row('github', { tier: 'official', title: 'GitHub' }),
    row('pdf', { summary: 'Read PDF files.', title: 'PDF' }),
    row('docs', { kind: 'mcp', title: 'Docs' })
  ])

  it('splits official from community, each by title', () => {
    const { community, official } = shown(listing, 'skill', '')
    expect(official.map(found => found.name)).toEqual(['github'])
    expect(community.map(found => found.name)).toEqual(['pdf', 'zip'])
  })

  it('keeps what every word of the search finds in its name, title, summary or author', () => {
    expect(shown(listing, 'skill', 'read files').community.map(found => found.name)).toEqual(['pdf'])
    expect(shown(listing, 'skill', 'nothing').community).toEqual([])
    expect(shown(listing, 'mcp', '').community.map(found => found.name)).toEqual(['docs'])
  })

  it('counts each kind for its tab', () => {
    expect(counts(listing)).toEqual({ executor: 0, mcp: 1, plugin: 0, skill: 3 })
    expect(counts(null)).toEqual({ executor: 0, mcp: 0, plugin: 0, skill: 0 })
  })
})

describe('a category filters a tab by what its source files', () => {
  const filed = (name: string, category: string) => row(name, { category, kind: 'plugin', source: 'claude-plugins' })
  const list = (items: MarketplaceRow[]): MarketplaceList => ({
    description: '',
    items,
    name: '',
    problem: null,
    read_at: null,
    source_problems: []
  })

  it('lists the categories of the tab alone, each once, as the source writes them', () => {
    const found = list([filed('a', 'security'), filed('b', 'design'), filed('c', 'design'), filed('d', ''), row('e')])

    expect(categories(found, 'plugin')).toEqual(['design', 'security'])
    expect(categories(found, 'skill')).toEqual([])
  })

  it('keeps only the rows filed under it, and all of them with none', () => {
    const found = list([filed('a', 'security'), filed('b', 'design'), row('own', { kind: 'plugin' })])
    const names = (category: string) =>
      shown(found, 'plugin', '', category)
        .outside.flatMap(shelf => shelf.rows)
        .map(item => item.name)

    expect(names('design')).toEqual(['b'])
    expect(names('')).toEqual(['a', 'b'])
    expect(shown(found, 'plugin', '', 'design').community).toEqual([])
    expect(shown(found, 'plugin', '').community.map(item => item.name)).toEqual(['own'])
  })
})

describe("a README's addresses stay in the item's tree", () => {
  const base = 'https://raw.githubusercontent.com/someone/tools/abc/pdf/'

  it('resolves a relative one at the commit and keeps an https one', () => {
    expect(resolved('docs/shot.png', base)).toBe(`${base}docs/shot.png`)
    expect(resolved('./shot.png', base)).toBe(`${base}shot.png`)
    expect(resolved('https://example.com/a', base)).toBe('https://example.com/a')
  })

  it('drops whatever else a stranger could write', () => {
    expect(resolved('javascript:alert(1)', base)).toBeNull()
    expect(resolved('http://example.com/a', base)).toBeNull()
    expect(resolved('file:///etc/passwd', base)).toBeNull()
    expect(resolved('data:text/html,hi', base)).toBeNull()
    expect(resolved('//example.com/a', base)).toBeNull()
    expect(resolved('shot.png', '')).toBeNull()
  })
})

describe("an open page follows the list's latest row", () => {
  it('takes the row the list read again', () => {
    const page: MarketplacePage = {
      entry: '',
      homepage: '',
      item: row('pdf'),
      link_base: '',
      needs: [],
      picture_base: '',
      readme: 'Read PDFs.',
      screenshots: [],
      secrets: []
    }
    const updated = withRow(page, list([row('pdf', { state: 'installed' })]))
    expect(updated?.item.state).toBe('installed')
    expect(updated?.readme).toBe('Read PDFs.')
    expect(withRow(page, list([]))).toBe(page)
    expect(withRow(null, list([]))).toBeNull()
  })
})
