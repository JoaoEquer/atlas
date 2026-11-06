/**
 * The marketplace's screen as pure state (step 16s): what the server listed, the tab and the search,
 * the item whose page is open, and what a click is doing. Every transition is a function, so the
 * screen's logic is tested without a window; the session holds the state and calls the server.
 */
import type { MarketplaceList, MarketplacePage, MarketplaceRow } from '@/protocol/protocol.generated'

export type MarketKind = MarketplaceRow['kind']
export type MarketSource = MarketplaceRow['source']

/** The outside catalogs (step 24), in their order on screen. */
export const OUTSIDE: Exclude<MarketSource, 'atlas'>[] = ['mcp-registry', 'claude-plugins', 'skills.sh']

/** The tabs, in their order on screen. */
export const KINDS: MarketKind[] = ['plugin', 'skill', 'mcp', 'executor']

export interface Market {
  list: MarketplaceList | null
  /** The list is being read; what was read stays on screen meanwhile. */
  reading: boolean
  /** Why the server could not be asked at all. */
  error: null | string
  kind: MarketKind
  query: string
  /** The category the tab is filtered by, as its source writes it; empty for all of them. */
  category: string
  /** The item whose page is open, and its page once read. */
  open: null | string
  page: MarketplacePage | null
  pageError: null | string
  /** The item a click acts on right now. */
  busy: null | string
  /** What the server refused of the last click, said on the page or the card. */
  failed: null | string
  /** An update that waits for the user to accept that the home's changes are replaced. */
  confirming: null | string
  /** What came along with the last install (a skill's MCP servers). */
  brought: string[]
}

export const EMPTY_MARKET: Market = {
  brought: [],
  busy: null,
  category: '',
  confirming: null,
  error: null,
  failed: null,
  kind: 'plugin',
  list: null,
  open: null,
  page: null,
  pageError: null,
  query: '',
  reading: false
}

/** What an item's button does, from where it stands in the home. */
export type MarketAction = 'install' | 'installed' | 'on' | 'retired' | 'turn-on' | 'unavailable' | 'update'

export function actionOf(row: MarketplaceRow): MarketAction {
  if (row.state === 'retired') {
    return 'retired'
  }

  if (row.update) {
    return 'update'
  }

  if (row.state === 'available') {
    return row.problem ? 'unavailable' : 'install'
  }

  if (row.state === 'off') {
    return 'turn-on'
  }

  return row.state === 'on' ? 'on' : 'installed'
}

/**
 * The rows a tab shows for a search: the Atlas team's first, then the community's, each by title;
 * then each outside catalog's (step 24), as the server found them for the search, in its order.
 * A category keeps only the rows filed under it.
 */
export function shown(
  list: MarketplaceList | null,
  kind: MarketKind,
  query: string,
  category = ''
): {
  community: MarketplaceRow[]
  official: MarketplaceRow[]
  outside: { rows: MarketplaceRow[]; source: Exclude<MarketSource, 'atlas'> }[]
} {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  const ofKind = (list?.items ?? []).filter(row => row.kind === kind && (!category || row.category === category))
  const outside = OUTSIDE.map(source => ({ rows: ofKind.filter(row => row.source === source), source })).filter(
    shelf => shelf.rows.length > 0
  )
  const rows = ofKind
    .filter(row => row.source === 'atlas')
    .filter(row => {
      const text = `${row.name} ${row.title} ${row.summary} ${row.author}`.toLowerCase()

      return words.every(word => text.includes(word))
    })
    .sort((a, b) => a.title.localeCompare(b.title))

  return {
    community: rows.filter(row => row.tier === 'community'),
    official: rows.filter(row => row.tier === 'official'),
    outside
  }
}

/** The categories a tab's items are filed under, as their sources write them. */
export function categories(list: MarketplaceList | null, kind: MarketKind): string[] {
  const found = (list?.items ?? []).filter(row => row.kind === kind && row.category).map(row => row.category)

  return [...new Set(found)].sort((a, b) => a.localeCompare(b))
}

/** How many items each tab holds, for its label. */
export function counts(list: MarketplaceList | null): Record<MarketKind, number> {
  const found: Record<MarketKind, number> = { executor: 0, mcp: 0, plugin: 0, skill: 0 }

  for (const row of list?.items ?? []) {
    found[row.kind] += 1
  }

  return found
}

/**
 * Where a README's picture or link leads: a relative address resolved against the item's tree at
 * its commit; an `https` one as it is; anything else (`javascript:`, `file:`, `data:`) dropped, as
 * a README is a stranger's text.
 */
export function resolved(url: string, base: string): null | string {
  if (/^https:\/\//i.test(url)) {
    return url
  }

  if (/^[a-z][a-z0-9+.-]*:/i.test(url) || url.startsWith('//') || !base) {
    return null
  }

  try {
    return new URL(url.replace(/^\.\//, ''), base).toString()
  } catch {
    return null
  }
}

/** The page's item, kept to the latest row the list read, so a change shows on the page too. */
export function withRow(page: MarketplacePage | null, list: MarketplaceList | null): MarketplacePage | null {
  const row = page && list?.items.find(item => item.name === page.item.name)

  return page && row ? { ...page, item: row } : page
}
