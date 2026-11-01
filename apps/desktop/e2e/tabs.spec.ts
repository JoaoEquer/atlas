import path from 'node:path'

import { _electron, expect, type Page, test } from '@playwright/test'

import { cleanEnvironment, DESKTOP, type Desktop, launch, say } from './harness'

/**
 * The tabs of the central area (step 9d, decided with the user on 25/10/2026): a click on a row
 * opens the passing tab, which the next click takes and a message keeps; the keyboard shows and
 * closes them, a drag orders them; and the app reopens on the same tabs, the one on screen read,
 * never blank, and a closed one never back.
 */

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

const tabs = (page: Page) => page.getByTestId('tab')
const titles = (page: Page) => page.getByTestId('tab-title')

/** Three conversations, each begun from a new tab, each answered. */
async function threeConversations(page: Page): Promise<void> {
  for (const [index, [said, answer]] of [
    ['Plan the trip', 'Answer one.'],
    ['Pick a book', 'Answer two.'],
    ['Fix the bike', 'Answer three.']
  ].entries()) {
    if (index > 0) {
      // A new tab opens on the start screen, where Atlas's card starts the conversation.
      await page.getByTestId('tab-new').click()
      await page.getByTestId('start-atlas').getByTestId('new-conversation').click()
    }

    await say(page, said ?? '')
    await expect(page.getByTestId('assistant-message').last()).toContainText(answer ?? '')
  }

  // What a first message makes opens in a tab of its own.
  await expect(titles(page)).toHaveText(['Plan the trip', 'Pick a book', 'Fix the bike'])
}

test('a row opens the passing tab, the next row takes it and a message keeps it; the keyboard shows and closes them', async () => {
  const { model, page } = desktop
  await model.reply({ text: 'Answer one.' }, { text: 'Answer two.' }, { text: 'Answer three.' }, { text: 'Kept.' })
  await threeConversations(page)

  // Ctrl+W closes the tab on screen; the one before it shows, the last having none after it.
  await page.keyboard.press('Control+w')
  await expect(titles(page)).toHaveText(['Plan the trip', 'Pick a book'])
  await expect(page.getByTestId('assistant-message').last()).toContainText('Answer two.')
  await page.keyboard.press('Control+w')
  await expect(titles(page)).toHaveText(['Plan the trip'])

  // A row opens in the passing tab, in italic, and the next row takes its place.
  await page.getByTestId('conversation-row').filter({ hasText: 'Fix the bike' }).click()
  await expect(titles(page)).toHaveText(['Plan the trip', 'Fix the bike'])
  await expect(tabs(page).nth(1)).toHaveAttribute('data-preview', 'true')
  await page.getByTestId('conversation-row').filter({ hasText: 'Pick a book' }).click()
  await expect(titles(page)).toHaveText(['Plan the trip', 'Pick a book'])
  await expect(page.getByTestId('assistant-message').last()).toContainText('Answer two.')

  // Used, it stays.
  await say(page, 'One more thing')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Kept.')
  await expect(tabs(page).nth(1)).not.toHaveAttribute('data-preview')

  // Ctrl+1 shows the first, Ctrl+Tab the next; a tab is dragged before another.
  await page.keyboard.press('Control+1')
  await expect(tabs(page).nth(0)).toHaveAttribute('data-active', 'true')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Answer one.')
  await page.keyboard.press('Control+Tab')
  await expect(tabs(page).nth(1)).toHaveAttribute('data-active', 'true')
  await tabs(page)
    .nth(1)
    .dragTo(tabs(page).nth(0), { targetPosition: { x: 4, y: 12 } })
  await expect(titles(page)).toHaveText(['Pick a book', 'Plan the trip'])
})

test('reopened, the tabs come back as they were, the one on screen read, and a closed one never returns', async () => {
  const { home, model, page } = desktop
  await model.reply({ text: 'Answer one.' }, { text: 'Answer two.' }, { text: 'Answer three.' })
  await threeConversations(page)
  await tabs(page).nth(1).hover()
  await tabs(page).nth(1).getByTestId('tab-close').click()
  await tabs(page).nth(0).click()
  await expect(page.getByTestId('assistant-message').last()).toContainText('Answer one.')
  await desktop.app.close()

  const root = path.dirname(home)
  const app = await _electron.launch({
    args: ['.', '--atlas-e2e', '--lang=en-US', `--user-data-dir=${path.join(root, 'profile')}`],
    cwd: DESKTOP,
    env: cleanEnvironment({ ATLAS_HOME: home })
  })

  try {
    const again = await app.firstWindow()
    await expect(titles(again)).toHaveText(['Plan the trip', 'Fix the bike'], { timeout: 90_000 })
    await expect(tabs(again).nth(0)).toHaveAttribute('data-active', 'true')
    // The tab on screen is read, never blank; the other reads as it shows.
    await expect(again.getByTestId('assistant-message').last()).toContainText('Answer one.')
    await tabs(again).nth(1).click()
    await expect(again.getByTestId('assistant-message').last()).toContainText('Answer three.')
  } finally {
    await app.close().catch(() => undefined)
  }
})
