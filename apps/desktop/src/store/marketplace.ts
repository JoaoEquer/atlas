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

/** The categories a tab's items are filed under, as their sources write them, the fullest first. */
export function categories(list: MarketplaceList | null, kind: MarketKind): { count: number; name: string }[] {
  const found = new Map<string, number>()

  for (const row of list?.items ?? []) {
    if (row.kind === kind && row.category) {
      found.set(row.category, (found.get(row.category) ?? 0) + 1)
    }
  }

  return [...found]
    .map(([name, count]) => ({ count, name }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
}

/** How many plugins lead a catalog, and how many each category shows before its whole list. */
const MOST_INSTALLED = 12
const CATEGORY_PREVIEW = 6

/**
 * A catalog with categories as it opens: the most installed, then a shelf per category, the fullest
 * first, each a short look at the rest. An item that cannot install comes after the ones that can,
 * and one with no category keeps a shelf, so every item is reachable.
 */
export function organized(rows: MarketplaceRow[]): {
  groups: { category: string; rows: MarketplaceRow[]; total: number }[]
  top: MarketplaceRow[]
  unfiled: MarketplaceRow[]
} {
  const ready = [...rows].sort((a, b) => Number(actionOf(a) === 'unavailable') - Number(actionOf(b) === 'unavailable'))
  const top = ready.slice(0, MOST_INSTALLED)
  const rest = ready.slice(MOST_INSTALLED)
  const totals = new Map<string, number>()

  for (const row of ready) {
    if (row.category) {
      totals.set(row.category, (totals.get(row.category) ?? 0) + 1)
    }
  }

  const groups = [...totals]
    .sort(([a, one], [b, other]) => other - one || a.localeCompare(b))
    .map(([category, total]) => ({
      category,
      rows: rest.filter(row => row.category === category).slice(0, CATEGORY_PREVIEW),
      total
    }))

  return { groups, top, unfiled: rest.filter(row => !row.category) }
}

/** Words a plugin's name holds that read as letters, not as a word. */
const INITIALS = new Set('ai amd api aws cli crm css gcp html lsp mcp php pr qa sap sdk seo sql ui ui5 ux'.split(' '))

/** Names a plugin's identifier lowers, as their owners write them. */
const SPELLED: Record<string, string | undefined> = {
  bigquery: 'BigQuery',
  clickhouse: 'ClickHouse',
  cockroachdb: 'CockroachDB',
  duckdb: 'DuckDB',
  github: 'GitHub',
  gitlab: 'GitLab',
  mysql: 'MySQL',
  pagerduty: 'PagerDuty',
  paypal: 'PayPal',
  postgresql: 'PostgreSQL',
  posthog: 'PostHog',
  sonarqube: 'SonarQube',
  typescript: 'TypeScript',
  wordpress: 'WordPress'
}

/** Words that stay small inside a name. */
const LINKING = new Set('a and by for in of on the to with'.split(' '))

/**
 * What a card calls an item. A plugin of Claude Code's directory that gives no display name has only
 * its identifier, which is written for a command line, so it reads as words here (`api-security`:
 * `API Security`), a word of its author's name as the author writes it. Only the spelling changes:
 * its page still shows the identifier.
 */
export function plainTitle(row: MarketplaceRow): string {
  if (row.source !== 'claude-plugins' || row.name !== `${row.source}:${row.title}`) {
    return row.title
  }

  // An account's lowercase login says nothing of how its owner spells the name.
  const maker = row.author.split(/\s+/).filter(name => name !== name.toLowerCase())

  return row.title
    .split(/[-_]+/)
    .filter(Boolean)
    .map((word, place) => {
      const spelled = maker.find(name => name.toLowerCase() === word.toLowerCase()) ?? SPELLED[word]

      if (spelled) {
        return spelled
      }

      if (INITIALS.has(word)) {
        return word.toUpperCase()
      }

      return place > 0 && LINKING.has(word) ? word : word.charAt(0).toUpperCase() + word.slice(1)
    })
    .join(' ')
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
