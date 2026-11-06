import { describe, expect, it } from 'vitest'

import type { MarketplaceList, MarketplacePage, MarketplaceRow } from '@/protocol/protocol.generated'

import { actionOf, categories, counts, organized, plainTitle, resolved, shown, withRow } from './marketplace'

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

  it('lists the categories of the tab alone, each once with its count, the fullest first', () => {
    const found = list([filed('a', 'security'), filed('b', 'design'), filed('c', 'design'), filed('d', ''), row('e')])

    expect(categories(found, 'plugin')).toEqual([
      { count: 2, name: 'design' },
      { count: 1, name: 'security' }
    ])
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

describe('a catalog of hundreds opens organized', () => {
  const plugin = (name: string, category: string, problem: null | string = null) =>
    row(name, { category, kind: 'plugin', problem, source: 'claude-plugins' })
  const many = (prefix: string, count: number, category: string) =>
    Array.from({ length: count }, (_, at) => plugin(`${prefix}${String(at)}`, category))

  it('leads with the first twelve, then a short look at the rest of each category, the fullest first', () => {
    const { groups, top } = organized([
      ...many('top', 12, 'design'),
      ...many('dev', 8, 'development'),
      plugin('s', 'security')
    ])

    expect(top.map(item => item.name)).toEqual(many('top', 12, 'design').map(item => item.name))
    expect(groups.map(group => [group.category, group.total, group.rows.length])).toEqual([
      ['design', 12, 0],
      ['development', 8, 6],
      ['security', 1, 1]
    ])
  })

  it('puts what cannot install after what can, and keeps every plugin with no category', () => {
    const { top, unfiled } = organized([
      plugin('broken', 'design', 'No.'),
      ...many('ok', 12, 'design'),
      ...many('u', 2, '')
    ])

    expect(top.map(item => item.name)).not.toContain('broken')
    expect(unfiled.map(item => item.name)).toEqual(['u0', 'u1'])
  })
})

describe("a plugin's identifier reads as words", () => {
  const plugin = (title: string, author = '') =>
    row(`claude-plugins:${title}`, { author, source: 'claude-plugins', title })

  it('splits it, keeps initials as letters and the author as the author writes it', () => {
    expect(plainTitle(plugin('frontend-design'))).toBe('Frontend Design')
    expect(plainTitle(plugin('api-security_testing'))).toBe('API Security Testing')
    expect(plainTitle(plugin('github', 'GitHub'))).toBe('GitHub')
    expect(plainTitle(plugin('cloudflare', 'cloudflare'))).toBe('Cloudflare')
    expect(plainTitle(plugin('deploy-on-aws'))).toBe('Deploy on AWS')
    expect(plainTitle(plugin('typescript-lsp'))).toBe('TypeScript LSP')
  })

  it('leaves a name the directory gave, and every other source', () => {
    const named = row('claude-plugins:gc-ai', { source: 'claude-plugins', title: 'GC AI' })

    expect(plainTitle(named)).toBe('GC AI')
    expect(plainTitle(row('notes-mcp'))).toBe('notes-mcp')
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
