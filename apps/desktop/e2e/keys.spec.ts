import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, say, writeConfig } from './harness'

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

test('a first run opens on its Model step: a refused key stays out, a good one goes on to the steps after it', async () => {
  desktop = await launch({ openRouterKey: 'sk-or-v1-good' })
  const { model, page } = desktop
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'model')

  // "Go to Atlas" with no key leaves a band that leads back.
  await page.getByTestId('first-run-leave').click()
  await expect(page.getByTestId('first-run')).toBeHidden()
  await page.getByTestId('key-band-connect').click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'model')

  await page.getByTestId('key-provider-openrouter').getByRole('radio').click()
  const field = page.getByTestId('key-input')
  await field.fill('sk-or-v1-wrong')
  await page.getByTestId('key-connect').click()
  await expect(page.getByTestId('key-error')).toContainText('refused the key (HTTP 401)')

  await field.fill('  sk-or-v1-good  ')
  await page.getByTestId('key-connect').click()
  // The key stored, the steps after it follow, and the whole is left at any point.
  await page.getByTestId('first-run-continue').click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'executors')
  await expect(page.getByTestId('first-run-step-model')).toHaveAttribute('data-mark', 'done')
  await page.getByTestId('first-run-leave').click()
  await expect(page.getByTestId('first-run')).toBeHidden()
  await expect(page.getByTestId('key-band')).toBeHidden()

  await model.reply({ text: 'Connected and answering.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toContainText('Connected and answering.')

  const requests = await model.requests()
  const checks = requests.filter(request => request.path === '/v1/key')
  expect(checks.map(request => request.authorization)).toEqual(['Bearer sk-or-v1-wrong', 'Bearer sk-or-v1-good'])
  const chat = requests.filter(request => request.path.endsWith('/chat/completions'))
  expect(chat.map(request => (request.body as { model?: string }).model)).toEqual(['openai/gpt-6-luna'])
  expect(chat[0]?.authorization).toBe('Bearer sk-or-v1-good')
})

test('a turn on a model whose key is not stored says how to store it', async () => {
  // A home restored from a backup on another machine: its model is chosen, its keys stayed behind
  // (found in use, 15/10: the screen said only "ModelKeyError").
  desktop = await launch({
    setup: home => {
      writeConfig(home, { model: 'gpt-6-luna', provider: 'openai' }, path.join(path.dirname(home), 'work'))
    }
  })
  const { page } = desktop
  await say(page, 'Hello')

  await expect(page.getByTestId('turn-end')).toHaveText(
    'The turn failed: There is no key for OpenAI: the vault has no OPENAI_API_KEY. ' +
      'Store it with `atlas secrets set OPENAI_API_KEY`.'
  )
  // The ways out that exist, on the turn that failed (the UI review, 25/10/2026): the message back
  // in the box to send again, and a key to connect, on the settings' Model.
  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  await expect(box).toHaveValue('')
  await page.getByTestId('turn-put-back').click()
  await expect(box).toHaveValue('Hello')
  await page.getByTestId('turn-connect-key').click()
  await expect(page.getByTestId('settings-section')).toHaveAttribute('data-section', 'model')
})
