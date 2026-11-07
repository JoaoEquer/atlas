/**
 * An executor's plan usage as the side panel shows it (asked for by the user on 18/10/2026): how
 * much of each of its plan's limits the account it is logged in as has used, as its harness's own
 * usage screen says (Claude Code's `/usage`). Read when the panel shows the executor and the last
 * read is older than two minutes, and told by its
 * instances as they work. Pure, so every transition is tested without a window.
 */
import type { ExecutorUsageRow, UsageWindowRow } from '@/protocol/protocol.generated'

/** How old a read may be when the panel shows the executor again. */
export const STALE_MS = 120_000
const HOUR_MS = 3_600_000
const DAY_MINUTES = 1440

/** Being read (the last read kept meanwhile), read, or why it could not be. */
export interface UsageRead {
  error: null | string
  reading: boolean
  usage: ExecutorUsageRow | null
}

/** Whether the panel reads it again on showing: never read, failed, or older than `STALE_MS`. */
export function stale(read: undefined | UsageRead, now: number): boolean {
  if (!read?.usage) {
    return !read?.reading
  }

  if (read.reading) {
    return false
  }

  const at = Date.parse(read.usage.read_at)

  return Number.isNaN(at) || now - at > STALE_MS
}

/** A read starts: what was read stays on screen meanwhile. */
export function reading(read: undefined | UsageRead): UsageRead {
  return { error: null, reading: true, usage: read?.usage ?? null }
}

/** What a read answered, or what an instance told (already merged by the server). */
export function told(read: undefined | UsageRead, usage: ExecutorUsageRow): UsageRead {
  return { error: null, reading: read?.reading ?? false, usage }
}

export function failed(read: undefined | UsageRead, error: string): UsageRead {
  return { error, reading: false, usage: read?.usage ?? null }
}

/** How the panel names a window, by how long it lasts, in the screen's language. */
export interface WindowNames {
  days: (count: number) => string
  session: string
  week: string
  weekOf: (model: string) => string
}

export function windowName(window: UsageWindowRow, names: WindowNames): string {
  if (window.minutes > 0 && window.minutes < DAY_MINUTES) {
    return names.session
  }

  if (window.minutes === 7 * DAY_MINUTES) {
    return window.model ? names.weekOf(window.model) : names.week
  }

  // Another whole number of days, such as the 30 of Codex's free plan (step 16o): not a week.
  if (window.minutes > 0 && window.minutes % DAY_MINUTES === 0) {
    return names.days(window.minutes / DAY_MINUTES)
  }

  return window.title
}

/** When a window starts again, as the panel says it: hours and minutes within a day, the day and
 * the time after (Claude Code's `/usage` says the date). Empty when the harness did not say. */
export function resetsIn(resetsAt: string, now: number, locale?: string): string {
  const at = Date.parse(resetsAt)

  if (Number.isNaN(at)) {
    return ''
  }

  const left = at - now

  if (left <= 0) {
    return ''
  }

  if (left < 24 * HOUR_MS) {
    // Rounded as a whole, so 1 h 59 min 45 s reads 2 h 0 min, never 1 h 60 min.
    const total = Math.max(1, Math.round(left / 60_000))
    const hours = Math.floor(total / 60)
    const minutes = total % 60

    return hours > 0 ? `${String(hours)} h ${String(minutes)} min` : `${String(minutes)} min`
  }

  return new Date(at).toLocaleString(locale, { day: 'numeric', hour: '2-digit', minute: '2-digit', weekday: 'short' })
}

/** The bar's colour: amber from 80% used, red from 95%. */
export function level(used: number): 'high' | 'full' | 'ok' {
  return used >= 95 ? 'full' : used >= 80 ? 'high' : 'ok'
}
