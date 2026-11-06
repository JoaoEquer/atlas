import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, say } from './harness'

let desktop: Desktop

test.beforeEach(async () => {
  desktop = await launch()
})

test.afterEach(async () => {
  await desktop.close()
})

test('a turn arrives in pieces, is listed, and is still there when opened again', async () => {
  const { model, page } = desktop
  await model.reply({ pause: 0.05, reasoning: 'They greet me.', text: 'Hello! **How** can I help today?' })
  await say(page, 'Hi Atlas')

  await expect(page.getByTestId('user-message')).toHaveText('Hi Atlas')
  const reply = page.getByTestId('assistant-message')
  await expect(reply).toContainText('How can I help today?')
  await expect(reply.locator('[data-streamdown="strong"]')).toHaveText('How')
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden()
  await expect(page.getByTestId('conversation-title')).toHaveText('Hi Atlas')
  // When it last changed, as a bot's row and an executor's session say it: today, the time.
  await expect(page.getByTestId('conversation-when')).toHaveText(/\d{1,2}[:.]\d{2}/)

  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(page.getByText('Ask Atlas anything.')).toBeVisible()
  await page.getByTestId('conversation-row').click()
  await expect(page.getByTestId('assistant-message')).toHaveCount(1)
  await expect(page.getByTestId('assistant-message')).toContainText('How can I help today?')
})

test('the box grows with its words and comes back, however it lost them or the room changed', async () => {
  const { model, page } = desktop
  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  const height = () => box.evaluate(element => element.getBoundingClientRect().height)
  const lines = Array.from({ length: 6 }, (_, line) => `Line ${String(line)}`).join(String.fromCharCode(10))
  const paragraph = 'words that wrap '.repeat(16)
  await page.setViewportSize({ height: 820, width: 1200 })
  const empty = await height()

  // Sent: the box is empty again, through the hint of a running turn.
  await box.fill(lines)
  expect(await height()).toBeGreaterThan(empty)
  await model.reply({ pause: 0.5, text: 'First answer, in no hurry at all.' })
  await model.reply({ text: 'Second.' })
  await box.press('Enter')
  await expect(page.getByRole('button', { name: 'Stop' })).toBeVisible()
  await expect.poll(height).toBe(empty)

  // Sent while the turn runs.
  await box.fill(lines)
  expect(await height()).toBeGreaterThan(empty)
  await box.press('Enter')
  await expect.poll(height).toBe(empty)
  await expect(page.getByRole('button', { name: 'Stop' })).toBeHidden()
  await expect.poll(height).toBe(empty)

  // The room changes with the words still there: a wider window takes them in fewer lines.
  await page.setViewportSize({ height: 820, width: 520 })
  await box.fill(paragraph)
  const narrow = await height()
  await page.setViewportSize({ height: 820, width: 1200 })
  await expect.poll(height).toBeLessThan(narrow)
  await box.fill('')
  await expect.poll(height).toBe(empty)
})

test('reasoning streamed at full speed leaves the window answering clicks', async () => {
  const { model, page } = desktop
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  // Thousands of deltas with no pause once passed React's limit of nested updates, and the stores
  // stopped telling the screen: the timer ran on while no click changed anything.
  await model.reply({ reasoning: 'Let me plan. '.repeat(400), text: 'Planned.' })
  await say(page, 'Plan it')

  await expect(page.getByTestId('assistant-message')).toContainText('Planned.')
  await page.getByTestId('tab-bots').click()
  await expect(page.getByText('No bots yet.')).toBeVisible()
  expect(errors).toEqual([])
})

test("the / of Atlas's box lists its commands and the skills, and a skill typed loads it", async () => {
  const { home, model, page } = desktop
  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  const skill = path.join(home, 'skills', 'weekly-report')

  // Written while the app runs: `/` reads the list again.
  await mkdir(skill, { recursive: true })
  await writeFile(
    path.join(skill, 'SKILL.md'),
    '---\nname: weekly-report\ndescription: Write the weekly report.\n---\nGather the numbers.\n'
  )
  await box.fill('/')
  const commands = page.getByTestId('slash-commands')
  await expect(commands.locator('[data-group="command"] [data-command]')).toHaveCount(2)
  await expect(commands.locator('[data-command="new"]')).toContainText('Start a new conversation')
  await expect(commands.locator('[data-group="skill"]')).toContainText('Write the weekly report.')
  await box.fill('/week')
  await box.press('Enter')
  await expect(box).toHaveValue('/weekly-report ')

  // Sent, the model reads the skill; the screen shows what the user typed.
  await model.reply({ text: 'Here is the report.' })
  await box.fill('/weekly-report May')
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message')).toContainText('Here is the report.')
  await expect(page.getByTestId('user-message')).toContainText('/weekly-report May')
  await expect(page.getByTestId('skill-invoked')).toBeVisible()
  await expect(page.getByTestId('conversation-title')).toHaveText('/weekly-report May')
  expect(await model.seen()).toContain('Gather the numbers.')

  // `/new` runs on the screen, and is never sent.
  await box.fill('/new')
  await page.keyboard.press('Escape')
  await box.press('Enter')
  await expect(page.getByText('Ask Atlas anything.')).toBeVisible()
  await expect(page.getByTestId('conversation-row')).toHaveCount(1)
})

test('a tool shows while it runs and opens its result', async () => {
  const { folder, model, page } = desktop
  const { writeFileSync } = await import('node:fs')
  const path = await import('node:path')
  writeFileSync(path.join(folder, 'notes.txt'), 'the answer is 42\n')
  await model.reply(
    { calls: [{ arguments: JSON.stringify({ path: 'notes.txt' }), name: 'read_file' }] },
    { text: 'The notes say 42.' }
  )
  await say(page, 'What do my notes say?')

  const tool = page.getByTestId('tool-row')
  await expect(tool).toHaveAttribute('data-status', 'completed')
  // read_file has a title of its own ("Read <path>").
  await expect(tool.locator('summary')).toContainText('Read notes.txt')
  await tool.locator('summary').click()
  await expect(tool).toContainText('the answer is 42')
  await expect(page.getByTestId('assistant-message')).toContainText('The notes say 42.')
})
