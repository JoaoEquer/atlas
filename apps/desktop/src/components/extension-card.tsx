import type { ComponentType, ReactNode } from 'react'

import { t } from '@/i18n'
import { KINDS, type MarketKind } from '@/store/marketplace'

import { Bulb, Cpu, McpServer, Plug } from './ui/icons'

/**
 * What the marketplace and the settings screen's Extensions share, so an extension looks the same in
 * both (asked for by the user on 28/10/2026): a tab per kind with its count, shelves of cards in a
 * grid, a card with its picture, its name, the Atlas team's mark, a short summary and its action.
 */

/** The tabs of the four kinds, each with its count. */
export function KindTabs({
  counts,
  current,
  onPick,
  testId
}: {
  counts: Record<MarketKind, number>
  current: MarketKind
  onPick: (kind: MarketKind) => void
  /** The tabs' test ids are `${testId}-${kind}`. */
  testId: string
}) {
  return (
    <div className="flex flex-wrap gap-1 border-b border-border" role="tablist">
      {KINDS.map(kind => {
        const on = current === kind

        return (
          <button
            aria-selected={on}
            className={`-mb-px border-b-2 px-3 py-1.5 text-sm ${
              on ? 'border-foreground font-medium' : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
            data-testid={`${testId}-${kind}`}
            key={kind}
            onClick={() => {
              onPick(kind)
            }}
            role="tab"
            type="button"
          >
            {t.marketplace.tabs[kind]}
            <span className="ml-1.5 text-xs text-muted-foreground">{counts[kind]}</span>
          </button>
        )
      })}
    </div>
  )
}

/** A titled group of cards, two columns once the room holds them; nothing when it has none. */
export function Shelf({ children, count, title }: { children: ReactNode; count: number; title: string }) {
  if (count === 0) {
    return null
  }

  return (
    <section className="@container space-y-2">
      <h3 className="text-sm font-medium text-muted-foreground">{title}</h3>
      <div className="grid gap-3 @lg:grid-cols-2">{children}</div>
    </section>
  )
}

/** One extension: its head, its summary, what it needs said, and its foot with the action. */
export function ExtensionCard({
  action,
  badge,
  children,
  data,
  footer,
  kind,
  subtitle,
  summary,
  title,
  wide = false
}: {
  action?: ReactNode
  badge?: ReactNode
  /** What it needs said, between the summary and the foot. */
  children?: ReactNode
  /** Its test id and attributes. */
  data: Record<`data-${string}`, string | boolean | undefined>
  /** The foot's left side. */
  footer?: ReactNode
  kind: MarketKind
  subtitle?: ReactNode
  summary: string
  title: string
  /** Across both columns, while it asks for more room (a consent). */
  wide?: boolean
}) {
  return (
    <div
      className={`flex min-w-0 flex-col gap-2 rounded-lg border border-border p-3 ${wide ? '@lg:col-span-2' : ''}`}
      {...data}
    >
      <div className="flex items-start gap-3">
        <KindPicture kind={kind} size={40} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium" title={title}>
            {title}
          </p>
          {subtitle && <p className="truncate text-xs text-muted-foreground">{subtitle}</p>}
        </div>
        {badge}
      </div>
      <p className="line-clamp-2 min-h-10 text-sm text-soft" title={summary}>
        {summary}
      </p>
      {children}
      <div className="mt-auto flex items-center justify-between gap-2">
        <span className="min-w-0 truncate text-xs text-muted-foreground">{footer}</span>
        {action}
      </div>
    </div>
  )
}

/** The kind's own icon, where an item has no picture. */
export function KindPicture({
  kind,
  size,
  glyph
}: {
  glyph?: ComponentType<GlyphProps>
  kind: MarketKind
  size: number
}) {
  const Glyph = glyph ?? GLYPHS[kind]

  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"
      style={{ height: size, width: size }}
    >
      <Glyph aria-hidden size={size / 2} stroke={1.5} />
    </span>
  )
}

interface GlyphProps {
  'aria-hidden'?: boolean
  size?: number
  stroke?: number
}

const GLYPHS: Record<MarketKind, ComponentType<GlyphProps>> = {
  executor: Cpu,
  mcp: McpServer,
  plugin: Plug,
  skill: Bulb
}

/** The Atlas team's mark, or another short word on a card's head. */
export function Badge({ children }: { children: ReactNode }) {
  return <span className="shrink-0 rounded-full bg-selected px-2 py-0.5 text-xs font-medium text-soft">{children}</span>
}
