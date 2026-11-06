// What `npm run home` asks Analytics Engine and how it prints the answer: the visits to the home the
// Worker counted (worker/home.ts) and what they did next, for each copy of the home's words.

/** Analytics Engine keeps a data point three months. */
export const MAX_DAYS = 90

/** One row of the answer: how many of one event, for one copy, new or returning. */
export interface HomeRow {
  event: string
  variant: string
  seen: string
  count: number | string
}

/** The SQL for the last `days` days, a row per event, copy and whether the visitor came before. */
export function homeQuery(days: number): string {
  return [
    'SELECT blob1 AS event, blob2 AS variant, blob3 AS seen, SUM(_sample_interval) AS count',
    'FROM atlas_home',
    `WHERE timestamp > NOW() - INTERVAL '${days}' DAY`,
    'GROUP BY event, variant, seen',
    'ORDER BY variant'
  ].join('\n')
}

/** The 95% Wilson interval of a rate of `hits` in `total`, as two fractions. */
export function wilson(hits: number, total: number): [number, number] {
  if (total === 0) {
    return [0, 0]
  }
  const z = 1.96
  const p = hits / total
  const centre = p + (z * z) / (2 * total)
  const margin = z * Math.sqrt((p * (1 - p)) / total + (z * z) / (4 * total * total))
  const scale = 1 + (z * z) / total
  return [Math.max(0, (centre - margin) / scale), Math.min(1, (centre + margin) / scale)]
}

function percent(fraction: number): string {
  return `${(fraction * 100).toFixed(1)}%`
}

function rate(hits: number, total: number): string {
  const [low, high] = wilson(hits, total)
  return `${percent(total ? hits / total : 0)} (${percent(low)} to ${percent(high)})`
}

/**
 * The report, a block per copy. A rate is per visitor, a new visit being one: of those who saw that
 * copy, how many copied the install line or opened GitHub, with its 95% interval. Two copies differ
 * only where their intervals do not overlap.
 */
export function homeReport(rows: HomeRow[], days: number): string {
  const variants = Array.from(new Set(rows.map(row => row.variant))).sort()
  const report = [`The home in the last ${days} days`]
  if (variants.length === 0) {
    report.push('', 'No visit counted yet.')
  }
  for (const variant of variants) {
    const sum = (event: string, seen?: string) =>
      rows
        .filter(row => row.variant === variant && row.event === event && (seen === undefined || row.seen === seen))
        .reduce((total, row) => total + Number(row.count), 0)
    const visitors = sum('visit', 'new')
    const copies = sum('copy')
    const github = sum('github')
    report.push(
      '',
      `Copy ${variant.toUpperCase()}:`,
      `  Visitors        ${visitors}`,
      `  Visits again    ${sum('visit', 'return')}`,
      `  Install copied  ${copies}, ${rate(copies, visitors)}`,
      `  GitHub opened   ${github}, ${rate(github, visitors)}`
    )
  }
  return report.join('\n')
}
