/**
 * The sidebar on the left shown or hidden (asked for by the user on 16/10/2026):
 * `mod+b` and a button hide it, leaving a narrow strip that brings it back. The side
 * panel on the right has its own switch (`$panelOpen`, store/terminals.ts).
 *
 * **Their widths** (asked for by the user on 18/10/2026): both open at the same width so the two rails
 * match, and a drag on the edge each has toward the conversation changes it, a double click putting
 * it back. A width is kept in the window's own storage, a look of this window and not
 * of a home, as the theme is; the conversation keeps its own minimum whatever the widths
 * (styles.css).
 *
 * **The Browser tab goes wider** (asked for in the contributor's manual proof on 27/10/2026): a
 * page of the web wants room, so on that tab the panel drags past `RAIL_MAX`, as far as the
 * conversation keeps its minimum. On any other tab it shows within the rails' bounds again, the
 * wider width kept for the browser.
 */
import { atom } from 'nanostores'

export const $sidebarOpen = atom(true)

export function toggleSidebar(open = !$sidebarOpen.get()): void {
  $sidebarOpen.set(open)
}

export type Rail = 'panel' | 'sidebar'

/** Where both open: 18rem, so a terminal still has columns. */
export const RAIL_DEFAULT = 288
export const RAIL_MIN = 224
export const RAIL_MAX = 640
/** The Browser tab's bound, past any screen's room: the conversation's minimum is what stops it. */
export const WIDE_MAX = 4096
/** The conversation's own minimum (`.main-pane`, styles.css). */
export const MAIN_MIN = 384

const KEYS: Record<Rail, string> = { panel: 'atlas.panelWidth', sidebar: 'atlas.sidebarWidth' }

/** Whether the panel shows the Browser tab (side-panel.tsx sets it): then it may pass `RAIL_MAX`. */
export const $panelWide = atom(false)

/** How wide a rail may go: the panel past `RAIL_MAX` only when `wide`. */
export function railMax(rail: Rail, wide: boolean): number {
  return rail === 'panel' && wide ? WIDE_MAX : RAIL_MAX
}

/** A width within the rails' bounds, whole pixels. */
export function boundWidth(width: number, max = RAIL_MAX): number {
  return Math.round(Math.min(max, Math.max(RAIL_MIN, width)))
}

/** The width the panel shows: a width kept for the browser shows within the bounds on another tab. */
export function shownWidth(width: number, wide: boolean): number {
  return wide ? width : Math.min(RAIL_MAX, width)
}

function stored(rail: Rail): number {
  const found = Number(localStorage.getItem(KEYS[rail]))

  return Number.isFinite(found) && found > 0 ? boundWidth(found, railMax(rail, true)) : RAIL_DEFAULT
}

export const $sidebarWidth = atom(stored('sidebar'))
export const $panelWidth = atom(stored('panel'))

function widthOf(rail: Rail) {
  return rail === 'sidebar' ? $sidebarWidth : $panelWidth
}

/**
 * The widest a rail `current` wide may be dragged beside a conversation `main` wide: as far as the
 * conversation keeps its minimum, and `max` at most.
 */
export function widest(current: number, main: number, max = RAIL_MAX): number {
  return boundWidth(current + main - MAIN_MIN, max)
}

/** Set a rail's width while it is dragged; `keep` writes it down, once the drag ends. */
export function setRailWidth(rail: Rail, width: number, keep = false, max = RAIL_MAX): void {
  const bounded = boundWidth(width, max)
  widthOf(rail).set(bounded)

  if (keep) {
    localStorage.setItem(KEYS[rail], String(bounded))
  }
}

/** Back to where both open (a double click on the edge). */
export function resetRailWidth(rail: Rail): void {
  widthOf(rail).set(RAIL_DEFAULT)
  localStorage.removeItem(KEYS[rail])
}
