import { expect, type Page, test } from '@playwright/test'

import { launch, say, seedConversations } from './harness'

/**
 * The list of conversations (step 9e, decided with the user on 27/10/2026): past its first page,
 * read as the sidebar scrolls near its end, each conversation once and in order.
 */

const rows = (page: Page) => page.getByTestId('conversation-row')
const titles = (page: Page) => page.getByTestId('conversation-title')

/** `count` conversations, `Topic 01` the oldest. */
function topics(count: number): { answer: string; said: string }[] {
  return Array.from({ length: count }, (_, index) => {
    const said = `Topic ${String(index + 1).padStart(2, '0')}`

    return { answer: `Notes on ${said.toLowerCase()}.`, said }
  })
}

test('past fifty conversations the list reads the rest as it scrolls, each once and in order, a new one on top', async () => {
  const seeded = topics(60)
  const desktop = await launch({
    setup: home => {
      seedConversations(home, seeded)
    }
  })

  try {
    const { model, page } = desktop
    await expect(rows(page)).toHaveCount(50)
    await expect(rows(page).first()).toContainText('Topic 60')
    await expect(rows(page).last()).toContainText('Topic 11')

    // A conversation begins before the rest is read: it goes on top, and the page after it still
    // starts where the list's end is.
    await model.reply({ text: 'Noted.' })
    await say(page, 'A brand new topic')
    await expect(page.getByTestId('assistant-message').last()).toContainText('Noted.')
    await expect(rows(page).first()).toContainText('A brand new topic')

    await rows(page).last().scrollIntoViewIfNeeded()
    await expect(rows(page)).toHaveCount(61)
    await expect(titles(page)).toHaveText(['A brand new topic', ...seeded.map(topic => topic.said).reverse()])
  } finally {
    await desktop.close()
  }
})
