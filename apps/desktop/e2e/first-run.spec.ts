import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { ANA, FakeTelegram, TOKEN } from './fake-telegram'
import { atlas, type Desktop, launch, ROOT } from './harness'

/**
 * The first run's Telegram and Connections (step 18d): the bot connected and its first person let
 * in from the step, GitHub through gh's code in the browser, and Google set up in a conversation a
 * band away from the step. gh and gws are stand-ins (tests/fake_logins.py) found first on the
 * server's PATH, as the real ones are; the browser is a file the test writes.
 */

const STARTED = { timeout: 30_000 }
const python =
  process.platform === 'win32'
    ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT, '.venv', 'bin', 'python')

let desktop: Desktop | undefined
let telegram: FakeTelegram | undefined
/** The stand-ins' launchers and state. */
let scratch: string | undefined

test.afterEach(async () => {
  await desktop?.close()
  telegram?.stop()
  if (scratch) {
    rmSync(scratch, { force: true, maxRetries: 10, recursive: true, retryDelay: 500 })
  }
  desktop = undefined
  telegram = undefined
  scratch = undefined
})

/** The first run opened again from the settings, on `step`, once the home's one-time offer of
 * "Bring your work" was decided (it would otherwise mark the first run under the test's clicks). */
async function openStep(page: Page, step: string): Promise<void> {
  await page.waitForFunction(() => localStorage.getItem('atlas.firstRun') !== null, undefined, STARTED)
  await page.getByTestId('settings-open').click()
  await page.getByTestId('settings-first-run-open').click()
  await page.getByTestId(`first-run-step-${step}`).click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', step)
}

test('the Telegram step connects the bot with consent, and lets in the person who wrote to it', async () => {
  telegram = await FakeTelegram.start()
  const fake = telegram
  desktop = await launch({
    setup: home => {
      atlas(home, 'plugins', 'set', 'telegram', 'api_url', fake.url)
    }
  })
  const { page } = desktop
  await openStep(page, 'telegram')

  const step = page.getByTestId('first-run-telegram')
  await expect(step.getByTestId('first-run-telegram-consent')).toContainText('TELEGRAM_BOT_TOKEN')
  await step.getByTestId('first-run-telegram-token').fill(TOKEN)
  await step.getByTestId('first-run-telegram-connect').click()
  await expect(step.getByTestId('first-run-telegram-ready')).toContainText('Your bot is on', STARTED)
  await expect(page.getByTestId('first-run-continue')).toBeDisabled()

  // Ana writes to the bot: she gets her code, and the step lets her in.
  await fake.say(ANA, 'hi')
  const person = step.getByTestId('first-run-telegram-person')
  await expect(person).toContainText('Ana waits with', STARTED)
  await expect.poll(async () => (await fake.sent(ANA)).join('\n')).toContain('Your pairing code is')
  await person.getByTestId('first-run-telegram-let-in').click()
  await expect(person).toContainText('Ana is in')

  await page.getByTestId('first-run-continue').click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'connections')
  await expect(page.getByTestId('first-run-step-telegram')).toHaveAttribute('data-mark', 'done')
})

test("the Connections step logs GitHub in through gh's code, and sets Google up in a conversation", async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'atlas-logins-'))
  scratch = root
  const bin = path.join(root, 'bin')
  const state = path.join(root, 'state')
  mkdirSync(bin, { recursive: true })
  mkdirSync(state, { recursive: true })
  const script = path.join(ROOT, 'tests', 'fake_logins.py')
  for (const name of ['gh', 'gws']) {
    if (process.platform === 'win32') {
      writeFileSync(path.join(bin, `${name}.cmd`), `@"${python}" "${script}" ${name} %*\r\n`)
    } else {
      writeFileSync(path.join(bin, name), `#!/bin/sh\nexec "${python}" "${script}" ${name} "$@"\n`, { mode: 0o755 })
    }
  }
  const pathKey = Object.keys(process.env).find(name => name.toUpperCase() === 'PATH') ?? 'PATH'
  desktop = await launch({
    environment: { [pathKey]: `${bin}${path.delimiter}${process.env[pathKey] ?? ''}`, FAKE_LOGINS: state }
  })
  const { model, page } = desktop
  await openStep(page, 'connections')
  // The browser is the test's: what the app opens is kept, never shown.
  await page.evaluate(() => {
    const opened: string[] = []
    Object.assign(window, { opened })
    window.open = (url?: string | URL) => {
      opened.push(String(url))

      return null
    }
  })

  // Each is a plugin of Atlas's (step 26), offered with what it declares and turned on here.
  await page.getByTestId('first-run-offer-github').getByTestId('first-run-turn-on').click()
  await page.getByTestId('first-run-offer-google').getByTestId('first-run-turn-on').click()

  // GitHub: gh's code shown, its page opened, and Connected once the browser said yes.
  const github = page.getByTestId('first-run-github')
  await github.getByTestId('first-run-github-connect').click(STARTED)
  await expect(github.getByTestId('first-run-github-code')).toHaveText('AB12-CD34', STARTED)
  expect(await page.evaluate(() => (window as unknown as { opened: string[] }).opened)).toEqual([
    'https://github.com/login/device'
  ])
  writeFileSync(path.join(state, 'gh-approve'), 'example-user')
  await expect(github.getByTestId('first-run-github-connected')).toContainText('Connected as example-user', STARTED)
  await expect(page.getByTestId('first-run-continue')).toBeEnabled()

  // Google: a conversation where Atlas leads gws's setup, the step a band away.
  await model.reply({ text: 'Let us start with gws auth status.' })
  await page.getByTestId('first-run-google-set-up').click()
  await expect(page.getByTestId('first-run')).toHaveCount(0)
  await expect(page.getByTestId('user-message')).toContainText('gws auth status')
  await expect(page.getByText('Let us start with gws auth status.')).toBeVisible(STARTED)
  await expect(page.getByTestId('first-run-band')).toContainText('Connections')

  // The setup done in the conversation: back on the step, it is read again.
  writeFileSync(path.join(state, 'gws-login'), 'ana@example.com')
  await page.getByTestId('first-run-back').click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'connections')
  await expect(page.getByTestId('first-run-google-connected')).toContainText('Connected as ana@example.com', STARTED)
  await expect(page.getByTestId('first-run-band')).toHaveCount(0)
})
