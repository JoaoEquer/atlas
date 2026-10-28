// What `npm run installs` asks Analytics Engine and how it prints the answer: the installer
// downloads the Worker counted (worker/installs.ts), by system, by day and by country.

/** Analytics Engine keeps a data point three months. */
export const MAX_DAYS = 90

/** One row of the answer: the downloads of one day, system, client and country. */
export interface DownloadRow {
  day: string
  system: string
  client: string
  country: string
  downloads: number | string
}

/** The SQL for the last `days` days, a row per day, system, client and country. */
export function installsQuery(days: number): string {
  return [
    "SELECT toStartOfInterval(timestamp, INTERVAL '1' DAY) AS day,",
    '  blob1 AS system, blob2 AS client, blob3 AS country, SUM(_sample_interval) AS downloads',
    'FROM atlas_installs',
    `WHERE timestamp > NOW() - INTERVAL '${days}' DAY`,
    'GROUP BY day, system, client, country',
    'ORDER BY day'
  ].join('\n')
}

const LABELS: Record<string, string> = { windows: 'Windows', unix: 'macOS and Linux' }

function add(totals: Map<string, number>, key: string, count: number): void {
  totals.set(key, (totals.get(key) ?? 0) + count)
}

function lines(totals: Map<string, number>): string[] {
  const width = Math.max(...Array.from(totals.keys(), key => key.length))
  return Array.from(totals)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([key, count]) => `  ${key.padEnd(width)}  ${count}`)
}

/**
 * The report. Only the lines the install page gives count as installs begun, PowerShell's for
 * Windows and curl's for the rest; a browser or a crawler opening the file is listed apart.
 */
export function installsReport(rows: DownloadRow[], days: number): string {
  const begun = rows.filter(row => row.client !== 'other')
  const bySystem = new Map<string, number>()
  const byDay = new Map<string, number>()
  const byCountry = new Map<string, number>()
  for (const row of begun) {
    const count = Number(row.downloads)
    add(bySystem, LABELS[row.system] ?? row.system, count)
    add(byDay, row.day.slice(0, 10), count)
    add(byCountry, row.country, count)
  }
  const total = Array.from(bySystem.values()).reduce((sum, count) => sum + count, 0)
  const others = rows.filter(row => row.client === 'other').reduce((sum, row) => sum + Number(row.downloads), 0)
  const report = [`Installs begun in the last ${days} days: ${total}`]
  if (total > 0) {
    report.push('', 'By system:', ...lines(bySystem))
    report.push('', 'By day:', ...Array.from(byDay).map(([day, count]) => `  ${day}  ${count}`))
    report.push('', 'By country:', ...lines(byCountry))
  }
  report.push('', `Opened by a browser or a crawler, not counted above: ${others}`)
  return report.join('\n')
}
