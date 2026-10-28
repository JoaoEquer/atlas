import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { launch, openApp, say, seedConversations } from './harness'

/**
 * The list of conversations (step 9e, decided with the user on 27/10/2026): past its first page,
 * read as the sidebar scrolls near its end, each conversation once and in order; its search, by
 * title and by message, which says where it matched; a conversation renamed in place; and one
 * archived, out of the list until brought back or written to.
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

test('a conversation renamed in place, by F2 or its menu, takes the name in the list and in its tab, and Esc keeps the old one', async () => {
  const desktop = await launch({
    setup: home => {
      seedConversations(home, topics(3))
    }
  })

  try {
    const { page } = desktop
    const field = page.getByTestId('conversation-name-field')
    const tabTitles = page.getByTestId('tab-title')
    await expect(titles(page)).toHaveText(['Topic 03', 'Topic 02', 'Topic 01'])
    // Kept in a tab of its own.
    await rows(page).nth(1).dblclick()
    await expect(tabTitles).toHaveText(['Topic 02'])

    await rows(page).nth(1).press('F2')
    await expect(field).toHaveValue('Topic 02')
    await field.fill('Trip to Lisbon')
    await field.press('Enter')
    await expect(field).toHaveCount(0)
    await expect(titles(page)).toHaveText(['Topic 03', 'Trip to Lisbon', 'Topic 01'])
    await expect(tabTitles).toHaveText(['Trip to Lisbon'])
    // The row has the focus back, for the keyboard to go on.
    await expect(rows(page).nth(1)).toBeFocused()

    await rows(page).nth(1).press('F2')
    await field.fill('Not this one')
    await field.press('Escape')
    await expect(field).toHaveCount(0)
    await expect(titles(page)).toHaveText(['Topic 03', 'Trip to Lisbon', 'Topic 01'])

    // A right click opens the row's menu, whose Rename does the same.
    await rows(page).last().click({ button: 'right' })
    await page.getByTestId('conversation-rename').click()
    await field.fill('Bike repairs')
    await field.press('Enter')
    await expect(titles(page)).toHaveText(['Topic 03', 'Trip to Lisbon', 'Bike repairs'])

    // The server keeps the names: the list read again has them.
    await page.reload()
    await expect(titles(page)).toHaveText(['Topic 03', 'Trip to Lisbon', 'Bike repairs'])
    await expect(tabTitles).toHaveText(['Trip to Lisbon'])
  } finally {
    await desktop.close()
  }
})

test('an archived conversation leaves the list for the Archived section, stays open in its tab and archived after a restart, and comes back when brought back or written to', async () => {
  const desktop = await launch({
    setup: home => {
      seedConversations(home, topics(3))
    }
  })
  let again: Awaited<ReturnType<typeof openApp>> | undefined

  try {
    const { home, model, page } = desktop
    const toggle = page.getByTestId('archived-toggle')
    const shelf = page.getByTestId('archived-row')
    await expect(titles(page)).toHaveText(['Topic 03', 'Topic 02', 'Topic 01'])
    await expect(toggle).toHaveCount(0)

    // Kept in a tab of its own, then archived from its row's menu.
    await rows(page).nth(1).dblclick()
    await rows(page).nth(1).click({ button: 'right' })
    await page.getByTestId('conversation-archive').click()
    await expect(titles(page)).toHaveText(['Topic 03', 'Topic 01'])
    await expect(page.getByTestId('tab-title')).toHaveText(['Topic 02'])
    await expect(page.getByTestId('assistant-message').last()).toContainText('Notes on topic 02.')
    await expect(toggle).toHaveText('Archived (1)')

    await toggle.click()
    await expect(shelf).toHaveText([/Topic 02/])
    await shelf.first().hover()
    await page.getByRole('button', { name: 'Bring Topic 02 back to the list' }).click()
    await expect(titles(page)).toHaveText(['Topic 03', 'Topic 02', 'Topic 01'])
    await expect(toggle).toHaveCount(0)

    // Archived again, it stays archived when the app opens again.
    await rows(page).nth(2).click({ button: 'right' })
    await page.getByTestId('conversation-archive').click()
    await expect(titles(page)).toHaveText(['Topic 03', 'Topic 02'])
    await desktop.app.close()
    again = await openApp(path.dirname(home), home, {
      CLAUDE_CONFIG_DIR: desktop.claude,
      CODEX_HOME: desktop.codex,
      DSH_HOME: desktop.dsh,
      GEMINI_CLI_HOME: desktop.gemini
    })
    const reopened = again.page
    await expect(titles(reopened)).toHaveText(['Topic 03', 'Topic 02'])
    await expect(reopened.getByTestId('archived-toggle')).toHaveText('Archived (1)')

    // Opened from the Archived section and written to, it is back on top of the list.
    await reopened.getByTestId('archived-toggle').click()
    await reopened.getByTestId('archived-row').first().click()
    await expect(reopened.getByTestId('assistant-message').last()).toContainText('Notes on topic 01.')
    await model.reply({ text: 'Back on it.' })
    await say(reopened, 'One more thing')
    await expect(reopened.getByTestId('assistant-message').last()).toContainText('Back on it.')
    await expect(titles(reopened)).toHaveText(['Topic 01', 'Topic 03', 'Topic 02'])
    await expect(reopened.getByTestId('archived-toggle')).toHaveCount(0)
  } finally {
    await again?.app.close().catch(() => undefined)
    await desktop.close()
  }
})
