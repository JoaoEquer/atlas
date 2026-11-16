import { spawnSync } from 'node:child_process'
import http from 'node:http'

import { expect, type Locator, type Page, test } from '@playwright/test'

import { type Desktop, launch, say } from './harness'

let desktop: Desktop | undefined
let site: http.Server | undefined
let began = 0

/**
 * Stop the browser engine's daemons this test started. The app stops its server by force, which gives
 * the server no time to stop them, and a daemon keeps its working folder (and the test's home) open.
 */
function stopEngines(since: number): void {
  if (process.platform === 'win32') {
    const from = new Date(since).toISOString()
    spawnSync(
      'powershell',
      [
        '-NoProfile',
        '-Command',
        `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'browser_harness.daemon' -and $_.CreationDate.ToUniversalTime() -gt [DateTime]::Parse('${from}').ToUniversalTime() } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }`
      ],
      { windowsHide: true }
    )
  } else {
    spawnSync('pkill', ['-f', 'browser_harness.daemon'])
  }
}

test.beforeEach(() => {
  began = Date.now()
})

test.afterEach(async () => {
  await desktop?.app.close().catch(() => undefined)
  stopEngines(began)
  await desktop?.close()
  desktop = undefined
  site?.close()
  site = undefined
})

const PAGES: Record<string, string> = {
  '/a': '<title>Agent page A</title><p>a</p>',
  '/b': '<title>User page B</title><p>b</p>',
  '/c': '<title>Third page C</title><p>c</p>'
}

/** A site on this machine with three pages of known titles. */
async function serve(): Promise<string> {
  site = http.createServer((request, response) => {
    response.setHeader('content-type', 'text/html')
    response.end(PAGES[request.url ?? '/'] ?? '<title>Not found</title>')
  })
  await new Promise<void>(resolve => site?.listen(0, '127.0.0.1', resolve))

  return `http://127.0.0.1:${String((site.address() as { port: number }).port)}`
}

const search = { calls: [{ arguments: JSON.stringify({ query: 'browser' }), name: 'tool_search' }] }
const run = (code: string) => ({ calls: [{ arguments: JSON.stringify({ code }), name: 'browser_exec' }] })

/** The browser_exec rows of the conversation on screen: a tab off screen keeps its thread drawn, hidden. */
const calls = (page: Page): Locator =>
  page
    .getByTestId('tool-row')
    .filter({ visible: true })
    .filter({ has: page.locator('summary', { hasText: /^browser_exec/ }) })

/** What the nth browser_exec call printed. */
async function printed(page: Page, nth: number): Promise<string> {
  const row = calls(page).nth(nth)
  await expect(row).toHaveAttribute('data-status', /completed|failed/, { timeout: 90_000 })
  await row.locator('summary').click()

  return (await row.textContent()) ?? ''
}

const strip = (page: Page) => page.getByTestId('browser-tabs').getByTestId('browser-page')
const selected = (page: Page) => page.getByTestId('browser-tabs').locator('[role="tab"][aria-selected="true"]')

test('the agent opens a page, which comes to the panel by itself; another conversation does not see it', async () => {
  const base = await serve()
  desktop = await launch()
  const { model, page } = desktop
  await page.setViewportSize({ height: 820, width: 1200 })
  await expect(page.getByTestId('browser-view')).toBeHidden()

  await model.reply(search, run(`new_tab("${base}/a")\nwait_for_load()\nprint(page_info()["title"])`), {
    text: 'Opened it.'
  })
  await say(page, 'Open page A')

  // The panel was hidden: it opens on the Browser tab, with the agent's page selected and marked.
  await expect(page.getByTestId('browser-view')).toBeVisible({ timeout: 90_000 })
  await expect(strip(page)).toHaveCount(1)
  await expect(selected(page)).toContainText('Agent page A')
  await expect(page.getByTestId('browser-agent-mark')).toHaveCount(1)
  expect(await printed(page, 0)).toContain('Agent page A')
  await expect(page.getByTestId('assistant-message')).toContainText('Opened it.')

  // Another conversation has no tab of this one's.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(page.getByTestId('browser-empty')).toBeVisible()
  await expect(page.getByTestId('browser-agent-mark')).toHaveCount(0)
})

test("the user's tab is the conversation's, and the agent's next tab opens behind it", async () => {
  const base = await serve()
  desktop = await launch()
  const { model, page } = desktop
  await page.setViewportSize({ height: 820, width: 1200 })

  await model.reply(
    search,
    run(`new_tab("${base}/a")\nwait_for_load()\nprint(page_info()["title"])`),
    { text: 'Opened it.' },
    run('print(sorted(t["url"] for t in list_tabs()))'),
    { text: 'Listed them.' }
  )
  await say(page, 'Open page A')
  await expect(selected(page)).toContainText('Agent page A', { timeout: 90_000 })
  await expect(page.getByTestId('assistant-message').last()).toContainText('Opened it.')

  // The user opens a page of their own, in the same conversation.
  await page.getByTestId('browser-new').click()
  const address = page.getByTestId('browser-address')
  await address.fill(`${base}/b`)
  await address.press('Enter')
  await expect(selected(page)).toContainText('User page B')
  await expect(strip(page)).toHaveCount(2)

  // The agent sees both, whoever opened them.
  await say(page, 'Which pages are open?')
  const listed = await printed(page, 1)
  expect(listed).toContain(`${base}/b`)
  expect(listed).toContain(`${base}/a`)

  // A second conversation never sees the user's page.
  await model.reply(search, run('print(sorted(t["url"] for t in list_tabs()))'), { text: 'Listed them.' })
  await page.getByRole('button', { name: 'New conversation' }).click()
  await say(page, 'Which pages are open here?')
  const other = await printed(page, 0)
  expect(other).not.toContain(`${base}/b`)
  expect(other).not.toContain(`${base}/a`)
})

test('a tab the agent opens next comes in the background, and comes forward only when it asks', async () => {
  const base = await serve()
  desktop = await launch()
  const { model, page } = desktop
  await page.setViewportSize({ height: 820, width: 1200 })

  await model.reply(
    search,
    run(`new_tab("${base}/a")\nwait_for_load()\nprint(page_info()["title"])`),
    { text: 'Opened A.' },
    run(`tab = new_tab("${base}/b")\nprint(tab)`),
    { text: 'Opened B behind.' },
    run(`for t in list_tabs():\n    if t["url"].endswith("/b"):\n        activate_tab(t["targetId"])`),
    { text: 'Brought B forward.' }
  )
  await say(page, 'Open page A')
  await expect(selected(page)).toContainText('Agent page A', { timeout: 90_000 })
  await expect(page.getByTestId('assistant-message').last()).toContainText('Opened A.')

  await say(page, 'Open page B too')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Opened B behind.', { timeout: 90_000 })
  // The new tab is in the strip, and the selection stayed on the first page.
  await expect(strip(page)).toHaveCount(2)
  await expect(strip(page).filter({ hasText: 'User page B' })).toHaveCount(1)
  await expect(selected(page)).toContainText('Agent page A')

  await say(page, 'Show me page B')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Brought B forward.', { timeout: 90_000 })
  await expect(selected(page)).toContainText('User page B')
})
