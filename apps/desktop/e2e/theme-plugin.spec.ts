/**
 * A theme a plugin brings (SDK version 24, docs/ARCHITECTURE.md, Plugins): once the plugin is on,
 * the settings offer it beside light and dark, choosing it repaints the app with its colors, and
 * the look stays after the window reloads.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { atlas, type Desktop, launch, openSettings } from './harness'

let desktop: Desktop

const SAND = {
  base: 'light',
  colors: { background: '#e9e3d3', foreground: '#1c1b17', subtle: '#e0d9c6' },
  label: 'Sand',
  labels: { 'pt-BR': 'Areia' }
}

test.beforeEach(async () => {
  desktop = await launch({
    setup: home => {
      const folder = path.join(home, 'plugins', 'sand')
      mkdirSync(path.join(folder, '.claude-plugin'), { recursive: true })
      mkdirSync(path.join(folder, 'themes'))
      writeFileSync(
        path.join(folder, '.claude-plugin', 'plugin.json'),
        JSON.stringify({ atlas: { api: 24, themes: ['sand'] }, name: 'sand', version: '1.0.0' })
      )
      writeFileSync(path.join(folder, 'themes', 'sand.json'), JSON.stringify(SAND))
      atlas(home, 'plugins', 'enable', 'sand', '--yes')
    }
  })
})

test.afterEach(async () => {
  await desktop.close()
})

test('a theme of a plugin that is on is offered, repaints the app and stays after a reload', async () => {
  const { page } = desktop
  const paint = (name: string) => page.evaluate(n => document.documentElement.style.getPropertyValue(n), name)
  const settings = await openSettings(page, 'general')
  const choice = settings.getByTestId('setting-theme')

  await expect(choice.locator('[data-value]')).toHaveText(["The system's", 'Light', 'Dark', 'Sand'])
  await choice.locator('[data-value="plugin:sand:sand"]').click()
  await expect(choice.locator('[data-value="plugin:sand:sand"]')).toHaveAttribute('aria-checked', 'true')
  expect(await paint('--background')).toBe('#e9e3d3')
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(233, 227, 211)')

  await page.keyboard.press('Escape')
  await page.reload()
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })
  await expect.poll(() => paint('--background')).toBe('#e9e3d3')

  const again = await openSettings(page, 'general')
  await again.getByTestId('setting-theme').locator('[data-value="dark"]').click()
  expect(await paint('--background')).toBe('')
  expect(await page.evaluate(() => document.documentElement.classList.contains('dark'))).toBe(true)
})
