import { spawnSync } from 'node:child_process'
import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { cleanEnvironment, type Desktop, launch, ROOT, say } from './harness'

// Two models the fake server answers as, declared in the home's models.toml as a user declares
// a local server's models: the picker offers them under vLLM.
const MODELS = `[[models]]
provider = "vllm"
id = "fake-model"
name = "Fake"
tool_call = true
reasoning = true
efforts = ["low", "high"]

[[models]]
provider = "vllm"
id = "fake-other"
name = "Fake Other"
tool_call = true
reasoning = true
efforts = ["low", "high"]
`

let desktop: Desktop | undefined

/** Pick a model from the composer's pill, by its name. */
async function pickModel(page: Page, name: string): Promise<void> {
  await page.getByTestId('model-select').click()
  await page.getByTestId('model-menu').getByTestId('model-option').filter({ hasText: name }).first().click()
  await expect(page.getByTestId('model-menu')).toBeHidden()
}

/** Pick an effort from the pill's Effort menu, by the keyboard: the pointer moved at once from the
 * menu's row to an option of the submenu not placed yet left its grace area on a busy runner, and
 * the submenu closed under it (found by the CI on 03/11/2026). */
async function pickEffort(page: Page, label: string): Promise<void> {
  await page.getByTestId('model-select').click()
  await page.getByTestId('effort-menu').press('ArrowRight')
  const option = page.getByTestId('effort-option').filter({ hasText: label })
  await option.focus()
  await option.press('Enter')
  await expect(page.getByTestId('model-menu')).toBeHidden()
}

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

test('a conversation picks another model and effort, and its next turn runs on them', async () => {
  desktop = await launch({ models: MODELS })
  const { model, page } = desktop
  const name = page.getByTestId('model-name')
  const effort = page.getByTestId('model-effort')
  // One pill says both: the default model, and Atlas's default effort, medium onto what it takes.
  await expect(name).toHaveText('Fake')
  await expect(effort).toHaveText('Low')
  // Its menu numbers the provider's models and marks the default and the current one.
  await page.getByTestId('model-select').click()
  const options = page.getByTestId('model-menu').getByTestId('model-option')
  await expect(options).toHaveText([/^FakeDefault$/, /^Fake Other2$/])
  await expect(options.first()).toHaveAttribute('data-current', 'true')
  // The keyboard opens the submenu: a click raced the pointer's own opening of it (pickEffort).
  await page.getByTestId('effort-menu').press('ArrowRight')
  await expect(page.getByTestId('effort-option')).toHaveText(['LowDefault', 'High'])
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('model-menu')).toBeHidden()

  await model.reply({ text: 'On the default.' }, { text: 'On the other.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toContainText('On the default.')

  await pickModel(page, 'Fake Other')
  await expect(name).toHaveText('Fake Other')
  await pickEffort(page, 'High')
  await expect(effort).toHaveText('High')
  await say(page, 'And now?')
  await expect(page.getByTestId('assistant-message').last()).toContainText('On the other.')

  const requests = (await model.requests()).filter(request => request.path.endsWith('/chat/completions'))
  const bodies = requests.map(request => request.body as { model?: string; reasoning_effort?: string })
  expect(bodies.map(body => body.model)).toEqual(['fake-model', 'fake-other'])
  // What the picker showed is what went: the default effort, then the one picked.
  expect(bodies.map(body => body.reasoning_effort)).toEqual(['low', 'high'])
  expect(JSON.stringify(requests[1])).toContain('The active model for this chat has changed to fake-other')
  // The note is for the model: the thread shows only what the user wrote.
  await expect(page.getByTestId('user-message')).toHaveText(['Hello', 'And now?'])

  // A new conversation starts from the default again.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(name).toHaveText('Fake')
  await page.getByTestId('conversation-row').filter({ hasText: 'Hello' }).click()
  await expect(name).toHaveText('Fake Other')

  // The keys pick by number.
  await page.getByTestId('model-select').click()
  await page.keyboard.press('1')
  await expect(name).toHaveText('Fake')
})

test('the window speaks Portuguese on a system in Portuguese', async () => {
  desktop = await launch({ lang: 'pt-BR' })
  const { page } = desktop
  await expect(page.getByPlaceholder('Mensagem para o Atlas')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Enviar' })).toBeVisible()
  await expect(page.getByText('Pergunte qualquer coisa ao Atlas.')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.lang)).toBe('pt-BR')
})

/** The conversations stored in a home, empty ones included: the sidebar hides those. */
function stored(home: string): number {
  const read =
    'import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); ' +
    'print(db.execute("SELECT count(*) FROM conversations_threads").fetchone()[0])'
  const done = spawnSync('uv', ['run', '--project', ROOT, 'python', '-c', read, path.join(home, 'atlas.db')], {
    cwd: ROOT,
    encoding: 'utf8',
    env: cleanEnvironment({}),
    windowsHide: true
  })

  return Number(done.stdout.trim())
}

test('a new conversation is made by its first message, not by a pick or "Auto" before it', async () => {
  desktop = await launch({ models: MODELS })
  const { home, model, page } = desktop
  const name = page.getByTestId('model-name')
  const auto = page.getByRole('button', { name: 'Auto mode' })

  await page.getByRole('button', { name: 'New conversation' }).click()
  await pickModel(page, 'Fake Other')
  // On from the start (20/10/2026): turned off before the first message, it stays off.
  await expect(auto).toHaveAttribute('aria-pressed', 'true')
  await auto.click()
  await expect(auto).toHaveAttribute('aria-pressed', 'false')
  expect(stored(home)).toBe(0)

  await model.reply({ text: 'On the other, asking first.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toContainText('On the other, asking first.')

  expect(stored(home)).toBe(1)
  const requests = await model.requests()
  expect((requests[0]?.body as { model?: string }).model).toBe('fake-other')
  await expect(name).toHaveText('Fake Other')
  await expect(auto).toHaveAttribute('aria-pressed', 'false')

  await page.getByRole('button', { name: 'New conversation' }).click()
  await expect(name).toHaveText('Fake')
  await expect(auto).toHaveAttribute('aria-pressed', 'true')
  expect(stored(home)).toBe(1)
})
