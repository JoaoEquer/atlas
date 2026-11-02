/**
 * Several machines (step 15, docs/ARCHITECTURE.md): another machine's Atlas, added from its
 * connection code in Settings, opened from the machine picker, and spoken to there, while this
 * computer's own server stays as it was. The other machine is a second real `atlas serve` on this
 * one's loopback, its code naming `127.0.0.1` as its tailnet address: the transport the app uses is
 * the Tailscale one, a plain HTTP and WebSocket connection to that address.
 */
import { randomBytes } from 'node:crypto'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, editHome, HandServer, launch, openSettings, pointAtFakeModel, say, writeConfig } from './harness'

let desktop: Desktop
let shop: HandServer
let shopRoot: string
let shopCode: string

test.beforeEach(async () => {
  desktop = await launch()
  shopRoot = mkdtempSync(path.join(tmpdir(), 'atlas-e2e-shop-'))
  const home = path.join(shopRoot, 'home')
  const folder = path.join(shopRoot, 'work')
  mkdirSync(home, { recursive: true })
  mkdirSync(folder, { recursive: true })
  writeFileSync(path.join(folder, 'shop-only.txt'), 'kept at the shop\n')
  pointAtFakeModel(home, desktop.model)
  writeConfig(home, { model: 'fake-model', provider: 'vllm' }, folder)
  const token = randomBytes(24).toString('base64url')
  shop = await HandServer.start(home, token)
  const fields = { home: 'default', port: shop.port, tailnet: '127.0.0.1', token }
  shopCode = `atlas1.${Buffer.from(JSON.stringify(fields)).toString('base64url')}`
})

test.afterEach(async () => {
  await desktop.close()
  shop.stop()
  rmSync(shopRoot, { force: true, maxRetries: 10, recursive: true, retryDelay: 500 })
})

test("another machine's Atlas opens from its code, works there, and leaves this one's as it was", async () => {
  const { model, page } = desktop

  // A conversation on this computer first.
  await model.reply({ text: 'Answered here.' })
  await say(page, 'Hello here')
  await expect(page.getByTestId('assistant-message')).toHaveText('Answered here.')

  // No machine yet: no picker, only Settings.
  await expect(page.getByTestId('machine-button')).toBeHidden()
  const settings = await openSettings(page, 'machines')
  await expect(settings).toBeVisible()
  await expect(settings.getByTestId('always-on')).toBeVisible()

  // A damaged code says why, never what it held.
  await settings.getByTestId('machine-add').click()
  await settings.getByTestId('machine-code').fill(`${shopCode.slice(0, 20)}!`)
  await settings.getByTestId('machine-name').fill('Shop')
  await settings.getByTestId('machine-add-save').click()
  await expect(settings.getByTestId('machine-add-problem')).toContainText('copy it again')

  await settings.getByTestId('machine-code').fill(shopCode)
  await settings.getByTestId('machine-add-save').click()
  const row = settings.locator('[data-testid="machine-row"][data-machine="Shop"]')
  await expect(row).toBeVisible()
  await row.getByTestId('machine-test').click()
  await expect(row.getByTestId('machine-said')).toHaveAttribute('data-kind', 'ok', { timeout: 30_000 })
  await page.keyboard.press('Escape')
  await expect(settings).toBeHidden()

  // The picker opens the shop's Atlas: its home, its conversations (none), its model's answer.
  await page.getByTestId('machine-button').click()
  await page.locator('[data-testid="machine-option"][data-machine="Shop"]').click()
  await expect(page.getByTestId('machine-button')).toHaveAttribute('data-machine', 'Shop')
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByTestId('assistant-message')).toHaveCount(0)

  await model.reply({ text: 'Answered at the shop.' })
  await say(page, 'Hello shop')
  await expect(page.getByTestId('assistant-message')).toHaveText('Answered at the shop.')

  // The terminal is the shop's too: its shell opens in the shop's folder, through its server
  // (`cat` is PowerShell's alias and bash's command alike; the file is only the shop's).
  await model.reply({ text: 'That is the shop.' })
  await say(page, '!cat shop-only.txt')
  const terminalRow = page.getByTestId('terminal-row')
  await expect(terminalRow.getByTestId('terminal-output')).toHaveText('kept at the shop', { timeout: 60_000 })
  await expect(page.getByTestId('assistant-message').last()).toHaveText('That is the shop.')

  // The shop's home is backed up here, where this machine's save dialog said: the zip comes down
  // the connection; a new home or an import there stays the shop's terminal's.
  const backup = path.join(desktop.folder, 'from the shop', 'shop.zip')
  mkdirSync(path.dirname(backup), { recursive: true })
  await desktop.app.evaluate(({ dialog }, chosen) => {
    dialog.showSaveDialog = () => Promise.resolve({ canceled: false, filePath: chosen })
  }, backup)
  const profile = await editHome(page)
  await expect(profile.getByTestId('home-import')).toBeDisabled()
  await profile.getByTestId('home-backup').click()
  const notice = page.getByTestId('home-notice')
  await expect(notice).toHaveAttribute('data-kind', 'backed-up', { timeout: 30_000 })
  await expect(notice).toContainText(backup)
  // A zip (its first bytes), whole (its manifest, named in the directory at its end).
  const zip = readFileSync(backup)
  expect(zip.subarray(0, 2).toString()).toBe('PK')
  expect(zip.includes('manifest.json')).toBe(true)
  expect(existsSync(`${backup}.part`)).toBe(false)
  await page.keyboard.press('Escape')
  await expect(profile).toBeHidden()

  // A long turn at the shop, left for this computer: the shop stays connected, and its turn's end
  // is told by the system's notification, as a home's is.
  await page.evaluate(() => {
    const seen: string[][] = []
    Object.assign(window, { seenNotifications: seen })
    window.Notification = class {
      onclick: (() => void) | null = null

      constructor(title: string, options?: NotificationOptions) {
        seen.push([title, options?.body ?? ''])
      }
    } as unknown as typeof Notification
  })
  const story = Array.from({ length: 60 }, (_, line) => `Line ${String(line + 1)}.`).join('\n')
  await model.reply({ pause: 0.02, text: story })
  await say(page, 'Tell me a long story')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Line 1')

  // Back on this computer, its conversation is as it was; and the shop's is there when it opens again.
  await page.getByTestId('machine-button').click()
  await page.locator('[data-testid="machine-option"][data-machine=""]').click()
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 60_000 })
  await expect(page.getByTestId('conversation-title').filter({ hasText: 'Hello here' })).toBeVisible()
  await expect(page.getByTestId('conversation-title').filter({ hasText: 'Hello shop' })).toBeHidden()
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { seenNotifications: string[][] }).seenNotifications), {
      timeout: 30_000
    })
    .toContainEqual(['Atlas answered', 'Hello shop (Shop)'])

  await page.getByTestId('machine-button').click()
  await page.locator('[data-testid="machine-option"][data-machine="Shop"]').click()
  await expect(page.getByTestId('conversation-title').filter({ hasText: 'Hello shop' })).toBeVisible({
    timeout: 60_000
  })

  // Removed, the shop is gone from the picker, and this computer is on screen again.
  await openSettings(page, 'machines')
  await settings.locator('[data-testid="machine-row"][data-machine="Shop"]').getByTestId('machine-remove').click()
  await settings.getByTestId('machine-remove-confirm').click()
  await expect(settings.getByTestId('machine-row')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('machine-button')).toBeHidden()
  await expect(page.getByTestId('conversation-title').filter({ hasText: 'Hello here' })).toBeVisible({
    timeout: 60_000
  })
})
