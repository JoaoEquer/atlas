import { readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, openSettings, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

/** The instructions of each turn the model was asked for, the title model's left out. */
async function instructions(): Promise<string[]> {
  const requests = await desktop.model.requests()

  return requests.map(request => JSON.stringify(request.body)).filter(body => body.includes('working folder is'))
}

test('the soul is edited on the settings screen and is who Atlas is from the next conversation', async () => {
  const { home, model, page } = desktop
  const soul = path.join(home, 'SOUL.md')

  // A conversation started before the edit.
  await model.reply({ text: 'Hi.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toHaveText('Hi.')

  // The screen shows the soul the server seeded with Atlas's default, and where it is kept.
  await openSettings(page, 'personalization')
  const text = page.getByTestId('soul-text')
  await expect(text).toHaveValue(/^You are Atlas, a personal agent\./)
  await expect(page.getByTestId('soul-path')).toHaveText(soul)
  await expect(page.getByTestId('soul-save')).toBeDisabled()

  // Written in the user's own words and saved whole.
  await text.fill('You are Kestrel, dry and terse.')
  await expect(page.getByTestId('soul-unsaved')).toBeVisible()
  await page.getByTestId('soul-save').click()
  await expect(page.getByTestId('soul-saved')).toBeVisible()
  expect(readFileSync(soul, 'utf8')).toBe('You are Kestrel, dry and terse.')

  // The open conversation keeps the soul it started with, so the provider's cache holds.
  await page.keyboard.press('Escape')
  await page.getByTestId('conversation-row').first().click()
  await model.reply({ text: 'Still here.' })
  await say(page, 'Still there?')
  await expect(page.getByTestId('assistant-message').last()).toHaveText('Still here.')
  // The next conversation is Kestrel, and is told where its soul is kept.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await model.reply({ text: 'Kestrel.' })
  await say(page, 'Who are you?')
  // The conversation before stays drawn in its tab, hidden.
  await expect(page.getByTestId('assistant-message').filter({ visible: true })).toHaveText('Kestrel.')
  const [first, second, third] = await instructions()
  expect(first).toContain('You are Atlas, a personal agent.')
  expect(second).toContain('You are Atlas, a personal agent.')
  expect(second).not.toContain('Kestrel')
  expect(third).toContain('You are Kestrel, dry and terse.')
  expect(third).not.toContain('You are Atlas, a personal agent.')
  expect(third).toContain('Your identity is kept in')

  // An edit made elsewhere (the agent, an editor) is what the screen shows when it opens again.
  writeFileSync(soul, 'You are Wren, warm and brief.')
  await openSettings(page, 'personalization')
  await expect(text).toHaveValue('You are Wren, warm and brief.')

  // Left blank, Atlas's default stands in, and the screen shows it.
  await text.fill('')
  await expect(page.getByTestId('soul-default')).toContainText('You are Atlas, a personal agent.')
  await page.getByTestId('soul-save').click()
  await expect(page.getByTestId('soul-saved')).toBeVisible()
  expect(readFileSync(soul, 'utf8')).toBe('')
})
