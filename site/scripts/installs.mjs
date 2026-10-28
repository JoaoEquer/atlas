// How many installs began: `npm run installs [-- <days>]`, 30 by default and at most 90. It reads
// the installer downloads the site's Worker counted, from Analytics Engine's SQL API, with
// CLOUDFLARE_ACCOUNT_ID and a CLOUDFLARE_API_TOKEN allowed to read Account Analytics.
import { installsQuery, installsReport, MAX_DAYS } from '../src/lib/installs-report.ts'

const days = Number(process.argv[2] ?? 30)
if (!Number.isInteger(days) || days < 1 || days > MAX_DAYS) {
  console.error(`Usage: npm run installs -- <days, 1 to ${MAX_DAYS}>`)
  process.exit(1)
}

const account = process.env.CLOUDFLARE_ACCOUNT_ID
const token = process.env.CLOUDFLARE_API_TOKEN
if (!account || !token) {
  console.error('Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (a token with Account Analytics: Read) first.')
  process.exit(1)
}

const response = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account}/analytics_engine/sql`, {
  method: 'POST',
  headers: { Authorization: `Bearer ${token}` },
  body: installsQuery(days)
})
if (!response.ok) {
  console.error(`Analytics Engine answered ${response.status}: ${await response.text()}`)
  process.exit(1)
}

const { data } = await response.json()
console.log(installsReport(data, days))
