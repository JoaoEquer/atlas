import { expect, type Page, test } from '@playwright/test'

import { launch, say, seedConversations } from './harness'

/**
 * The list of conversations (step 9e, decided with the user on 27/10/2026): past its first page,
 * read as the sidebar scrolls near its end, each conversation once and in order; and its search,
 * by title and by message, which says where it matched.
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

test('the search finds a conversation past the first page by a word begun in a message, or by its title, says where, and opens it', async () => {
  // The oldest, past the first page, holds a rare word in its answer; another a title model named.
  const seeded = topics(60).map((topic, index) =>
    index === 0
      ? { ...topic, answer: 'First we looked at the map. Then a quokka crossed the trail near the river.' }
      : index === 30
        ? { ...topic, title: 'Configuring the web server' }
        : topic
  )
  const desktop = await launch({
    setup: home => {
      seedConversations(home, seeded)
    }
  })

  try {
    const { page } = desktop
    await expect(rows(page)).toHaveCount(50)
    const search = page.getByRole('textbox', { name: 'Search conversations' })
    const hits = page.getByTestId('search-hit')

    await search.fill('quok')
    await expect(hits).toHaveCount(1)
    await expect(hits.first().getByTestId('search-hit-title')).toHaveText('Topic 01')
    await expect(hits.first().getByTestId('search-hit-snippet')).toHaveText(
      'Atlas: First we looked at the map. Then a quokka crossed the trail near the river.'
    )
    await expect(hits.first().getByTestId('search-match')).toHaveText('quokka')
    await hits.first().click()
    await expect(page.getByTestId('assistant-message').last()).toContainText('a quokka crossed')

    // A title no message holds is found by its words too.
    await search.fill('web serv')
    await expect(hits.first().getByTestId('search-hit-title')).toHaveText('Configuring the web server')
    await expect(hits).toHaveCount(1)
    await expect(hits.first().getByTestId('search-match')).toHaveText(['web', 'server'])

    await search.fill('zebra')
    await expect(page.getByTestId('search-status')).toHaveText('No conversation holds “zebra”.')

    // Esc ends the search, and the list is back as it was.
    await search.press('Escape')
    await expect(search).toHaveValue('')
    await expect(page.getByTestId('conversation-list')).toBeVisible()
    await expect(rows(page)).toHaveCount(50)
  } finally {
    await desktop.close()
  }
})
