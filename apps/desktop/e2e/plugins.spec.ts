import { type ChildProcess, spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { atlas, type Desktop, launch, openSettings, ROOT, stopTree } from './harness'

/** The token tests/fake_telegram.py takes. */
const TOKEN = '123456:fake-token'
const ANA = 100

/** tests/fake_telegram.py in a process of its own, driven over HTTP as a person would write. */
class FakeTelegram {
  private constructor(
    private readonly child: ChildProcess,
    readonly url: string
  ) {}

  static async start(): Promise<FakeTelegram> {
    const child = spawn('uv', ['run', '--project', ROOT, 'python', 'tests/fake_telegram.py'], {
      cwd: ROOT,
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
    const port = await new Promise<number>((resolve, reject) => {
      let output = ''
      const timer = setTimeout(() => {
        reject(new Error(`The fake Telegram did not start.\n${output}`))
      }, 60_000)
      child.stdout.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
        const match = /FAKE_TELEGRAM_READY port=(\d+)/.exec(output)

        if (match?.[1]) {
          clearTimeout(timer)
          resolve(Number(match[1]))
        }
      })
      child.stderr.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
      })
    })

    return new FakeTelegram(child, `http://127.0.0.1:${String(port)}`)
  }

  async say(user: number, text: string): Promise<void> {
    await fetch(`${this.url}/_fake/say`, {
      body: JSON.stringify({ name: 'Ana', text, user }),
      headers: { 'content-type': 'application/json' },
      method: 'POST'
    })
  }

  /** What the bot sent `user`. */
  async sent(user: number): Promise<string[]> {
    const response = await fetch(`${this.url}/_fake/calls`)
    const { calls } = (await response.json()) as { calls: { method: string; params: Record<string, unknown> }[] }

    return calls
      .filter(call => call.method === 'sendMessage' && String(call.params.chat_id) === String(user))
      .map(call => String(call.params.text))
  }

  stop(): void {
    stopTree(this.child)
  }
}

let desktop: Desktop | undefined
let telegram: FakeTelegram | undefined

test.afterEach(async () => {
  await desktop?.close()
  telegram?.stop()
  desktop = undefined
  telegram = undefined
})

test('Telegram is turned on from the Plugins screen with consent, and a person let in talks to Atlas', async () => {
  telegram = await FakeTelegram.start()
  desktop = await launch()
  const { model, page } = desktop
  const fake = telegram

  await openSettings(page, 'extensions')
  // By its name: another plugin's description may name Telegram (voice's does).
  const row = page.locator('[data-testid="plugin-row"][data-plugin="telegram"]')
  await expect(row).toContainText('telegram')
  await expect(row.getByTestId('plugin-state')).toHaveText('off')

  // Consent first: what it declares, before anything runs.
  await row.getByTestId('plugin-enable').click()
  const consent = row.getByTestId('plugin-consent')
  await expect(consent).toContainText('no sandbox')
  await expect(consent).toContainText('TELEGRAM_BOT_TOKEN')
  await consent.getByTestId('plugin-consent-yes').click()
  // On, but with no token it stops and says where to get one.
  await expect(row.getByTestId('plugin-problem')).toContainText('@BotFather')

  // Pointed at the fake Telegram, and the token stored: it runs.
  await row.locator('summary').click()
  const address = row.getByLabel('api_url')
  await address.fill(fake.url)
  await row.getByRole('button', { name: 'Save' }).last().click()
  await row.getByTestId('plugin-secret-input').fill(TOKEN)
  await row.getByTestId('plugin-secret-save').click()
  await expect(row.getByTestId('plugin-state')).toHaveText('running')
  await expect(row.getByTestId('plugin-problem')).toHaveCount(0)
  // Stored, it is no longer asked for: only a way to change it.
  await expect(row.getByTestId('plugin-secret')).toHaveAttribute('data-stored', 'true')
  await expect(row.getByTestId('plugin-secret-input')).toHaveCount(0)
  await row.getByTestId('plugin-secret-change').click()
  await expect(row.getByTestId('plugin-secret-input')).toBeVisible()

  // A stranger writes: they get a code, the screen shows them waiting, and the user lets them in.
  await fake.say(ANA, 'hi')
  await expect(page.getByTestId('plugins-waiting')).toHaveText('1 waits')
  const pairing = page.getByTestId('pairing-row')
  await expect(pairing).toContainText('Ana')
  await expect.poll(async () => (await fake.sent(ANA)).join('\n')).toContain('Your pairing code is')
  await pairing.getByTestId('pairing-approve').click()
  await expect(pairing).toContainText('let in')

  // Now they talk to Atlas, and the conversation is listed, marked as Telegram's.
  await model.reply({ text: 'Hi Ana, Atlas here.' })
  await fake.say(ANA, 'hello from my phone')
  await expect.poll(async () => fake.sent(ANA)).toContain('Hi Ana, Atlas here.')
  await expect(page.getByTestId('conversation-row')).toContainText('hello from my phone')
  await expect(page.getByTestId('conversation-via')).toHaveText('via telegram')

  // Off: it stops.
  await openSettings(page, 'extensions')
  await row.getByTestId('plugin-disable').click()
  await expect(row.getByTestId('plugin-state')).toHaveText('off')
})

/** A plugin with a screen (step 16m): a page whose button calls the plugin's Python, and a panel
 * that follows the conversation on screen. */
const SCREEN = `import { host, useValue } from '@atlas/plugin-sdk'
import { useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

function Page({ ctx }) {
  const [said, setSaid] = useState('')
  return jsxs('div', {
    children: [
      jsx('p', { children: 'Hello from the counter' }),
      jsx('button', {
        'data-testid': 'counter-count',
        onClick: () => { void ctx.call('count', { n: 1 }).then(result => setSaid('n=' + result.n)) },
        children: 'Count'
      }),
      jsx('p', { 'data-testid': 'counter-said', children: said })
    ]
  })
}

function Panel() {
  const conversation = useValue(host.state.conversation)
  return jsx('p', { 'data-testid': 'counter-panel', children: 'on ' + (conversation ? 'a conversation' : 'nothing') })
}

export default {
  id: 'counter',
  register(ctx) {
    ctx.register({ area: 'page', id: 'page', title: 'Counter', render: () => jsx(Page, { ctx }) })
    ctx.register({ area: 'panel', id: 'panel', title: 'Counter', render: () => jsx(Panel, {}) })
  }
}
`

const PYTHON = `def register(ctx):
    async def count(params):
        return {"n": params["n"] + 1}

    ctx.register_call("count", count)
`

function plugin(home: string, name: string, manifest: object, files: Record<string, string>): void {
  const folder = path.join(home, 'plugins', name)
  mkdirSync(path.join(folder, '.claude-plugin'), { recursive: true })
  mkdirSync(path.join(folder, 'desktop'), { recursive: true })
  writeFileSync(path.join(folder, '.claude-plugin', 'plugin.json'), JSON.stringify({ name, ...manifest }))
  for (const [file, text] of Object.entries(files)) {
    writeFileSync(path.join(folder, file), text)
  }
}

test("A plugin's screen adds a page and a panel, and calls its Python; a screen that imports more fails alone", async () => {
  desktop = await launch({
    setup: home => {
      plugin(
        home,
        'counter',
        { atlas: { api: 17, calls: ['count'] } },
        {
          '__init__.py': PYTHON,
          'desktop/plugin.js': SCREEN
        }
      )
      plugin(
        home,
        'reacher',
        {},
        {
          'desktop/plugin.js': "import fs from 'node:fs'\nexport default { id: 'reacher', register() {} }\n"
        }
      )
      atlas(home, 'plugins', 'enable', 'counter', '--yes')
      atlas(home, 'plugins', 'enable', 'reacher', '--yes')
    }
  })
  const { page } = desktop

  // Its page: an entry in the sidebar, and its Python answers the button.
  await page.getByTestId('plugin-page-counter:page').click()
  const shown = page.getByTestId('plugin-page')
  await expect(shown).toContainText('Hello from the counter')
  await shown.getByTestId('counter-count').click()
  await expect(shown.getByTestId('counter-said')).toHaveText('n=2')

  // Its panel: a tab of the side panel, following the app's state.
  await page.getByTestId('panel-show').click()
  await page.getByTestId('plugin-panel-tab-counter:panel').click()
  await expect(page.getByTestId('counter-panel')).toHaveText('on nothing')

  // The other screen imports what a screen may not: it fails, said on the Plugins screen, alone.
  await openSettings(page, 'extensions')
  const row = page.getByTestId('plugin-row').filter({ hasText: 'reacher' })
  await expect(row.getByTestId('plugin-screen-problem')).toContainText('It imports node:fs')

  // Turned off, its page and its panel go.
  const counter = page.getByTestId('plugin-row').filter({ hasText: 'counter' })
  await expect(counter.getByTestId('plugin-consent-yes')).toHaveCount(0)
  await counter.getByTestId('plugin-disable').click()
  await expect(page.getByTestId('plugin-page-counter:page')).toHaveCount(0)
})

test('A plugin saved while the app is open loads again in place, its screen and its Python (step 16n)', async () => {
  let folder = ''
  desktop = await launch({
    setup: home => {
      plugin(
        home,
        'counter',
        { atlas: { api: 17, calls: ['count'] } },
        { '__init__.py': PYTHON, 'desktop/plugin.js': SCREEN }
      )
      atlas(home, 'plugins', 'enable', 'counter', '--yes')
      folder = path.join(home, 'plugins', 'counter')
    }
  })
  const { page } = desktop

  await page.getByTestId('plugin-page-counter:page').click()
  const shown = page.getByTestId('plugin-page')
  await expect(shown).toContainText('Hello from the counter')

  // Its author saves both halves; the server sees it, and the app loads the screen again.
  writeFileSync(path.join(folder, '__init__.py'), PYTHON.replace('params["n"] + 1', 'params["n"] + 10'))
  writeFileSync(path.join(folder, 'desktop', 'plugin.js'), SCREEN.replace('Hello from the counter', 'Hello again'))
  await expect(shown).toContainText('Hello again', { timeout: 20_000 })
  await shown.getByTestId('counter-count').click()
  await expect(shown.getByTestId('counter-said')).toHaveText('n=11')

  // And on asking, from the Plugins screen.
  await openSettings(page, 'extensions')
  await page.getByTestId('plugins-reload').click()
  const counter = page.getByTestId('plugin-row').filter({ hasText: 'counter' })
  await expect(counter.getByTestId('plugin-state')).toHaveText('on')
  await expect(page.getByRole('alert')).toHaveCount(0)

  // Another one is written with Atlas, in a new conversation, out of the settings.
  await page.getByTestId('plugins-write').click()
  await expect(page.getByTestId('plugins-view')).toBeHidden()
  await expect(page.getByRole('textbox', { name: 'Message Atlas' })).toHaveValue('/write-plugin ')
})
