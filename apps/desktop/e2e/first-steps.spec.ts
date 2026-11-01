import { expect, test } from '@playwright/test'

import { type Desktop, launch } from './harness'

/**
 * The first steps (step 18e): the card at the sidebar's foot, each task's Start, the first run's
 * step opened alone, a bot made from it and ticked, and the card hidden for good.
 */

const STARTED = { timeout: 30_000 }

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

test('the first steps start each task, tick what was done, and stay hidden once hidden', async () => {
  desktop = await launch()
  const { page } = desktop
  const card = page.getByTestId('first-steps')
  await expect(card).toBeVisible(STARTED)
  await expect(card.getByTestId('first-steps-count')).toHaveText('0 of 5')

  // A routine starts in a new conversation, its words in the box for the user to finish.
  await card.getByTestId('first-step-routine').click()
  await expect(page.getByTestId('composer').locator('textarea')).toHaveValue(/Every Monday at 9am/)

  // Telegram opens the first run on its step alone; leaving it brings the card back.
  await card.getByTestId('first-step-telegram').click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'telegram')
  await page.getByTestId('first-run-leave').click()
  await expect(card).toBeVisible()

  // A bot made from its Start is ticked.
  await card.getByTestId('first-step-bot').click()
  await page.getByTestId('bot-name').fill('Finance')
  await page.getByTestId('bot-title').fill('Finance')
  await page.getByTestId('bot-description').fill('Bills and taxes.')
  await page.getByTestId('bot-save').click()
  await expect(card.getByTestId('first-step-bot')).toHaveAttribute('data-done', 'true')
  await expect(card.getByTestId('first-steps-count')).toHaveText('1 of 5')

  // Hidden, it stays so after a reload.
  await card.getByTestId('first-steps-hide').click()
  await expect(card).toBeHidden()
  await page.reload()
  await expect(page.getByTestId('sidebar-title')).toBeVisible(STARTED)
  await expect(page.getByTestId('first-steps')).toBeHidden()
})
