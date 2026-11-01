// The changelog is main's history: each commit reaches the app's Update button, with its subject
// in English and its `Notes-pt-BR` trailer in Portuguese, read the way the update check reads them.
import { execFileSync } from 'node:child_process'

export interface Change {
  sha: string
  date: string
  subject: string
  subjectPt: string
}

const FORMAT = '%H%x1f%cs%x1f%s%x1f%(trailers:key=Notes-pt-BR,valueonly,unfold,separator=%x20)%x1e'

/** Parses `git log` output in FORMAT: records apart by 0x1e, fields by 0x1f. */
export function parseLog(output: string): Change[] {
  return output
    .split('\x1e')
    .map(record => record.trim())
    .filter(record => record.length > 0)
    .map(record => {
      const [sha = '', date = '', subject = '', subjectPt = ''] = record.split('\x1f')
      return { sha, date, subject: subject.trim(), subjectPt: subjectPt.trim() }
    })
    .filter(change => change.sha.length === 40 && change.subject.length > 0)
}

let cached: Change[] | undefined

/** The newest changes first. A build without git history gets an empty list, not an error. */
export function changes(limit = 400): Change[] {
  if (cached) {
    return cached.slice(0, limit)
  }
  try {
    const output = execFileSync('git', ['log', '--no-merges', `-n`, '400', `--format=${FORMAT}`], {
      encoding: 'utf8',
      maxBuffer: 16 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'ignore']
    })
    cached = parseLog(output)
  } catch {
    cached = []
  }
  return cached.slice(0, limit)
}

/** Changes grouped by day, newest day first. */
export function byDay(list: Change[]): { date: string; changes: Change[] }[] {
  const days = new Map<string, Change[]>()
  for (const change of list) {
    const day = days.get(change.date) ?? []
    day.push(change)
    days.set(change.date, day)
  }
  return Array.from(days, ([date, items]) => ({ date, changes: items }))
}

/** A day as people read it, in English (the default) or another locale, such as `pt-BR`. */
export function formatDay(date: string, locale = 'en-GB'): string {
  const parsed = new Date(`${date}T12:00:00Z`)
  if (Number.isNaN(parsed.getTime())) {
    return date
  }
  return parsed.toLocaleDateString(locale, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
}
