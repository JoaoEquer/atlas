import { mkdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, openSettings, say } from './harness'

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

/** Every request the model got, as text. */
async function asked(): Promise<string[]> {
  return ((await desktop?.model.requests()) ?? []).map(request => JSON.stringify(request.body))
}

test('a rule and an entry saved on the settings screen apply without a restart', async () => {
  desktop = await launch()
  const { folder, home, model, page } = desktop
  const config = () => readFileSync(path.join(home, 'config.toml'), 'utf8')

  // A conversation open before anything changes.
  await model.reply({ text: 'Ready.' })
  await say(page, 'Hello')
  await expect(page.getByTestId('assistant-message')).toHaveText('Ready.')

  // Safety: a blocked command, saved when the field is left, with no Save button.
  let settings = await openSettings(page, 'safety')
  const deny = settings.getByTestId('setting-trust.deny')
  await deny.fill('echo forbidden*')
  await deny.blur()
  await expect(settings.getByTestId('setting-status').first()).toHaveText('Saved')
  expect(config()).toContain('deny = ["echo forbidden*"]')
  // Atlas's own lines stay as they were.
  expect(config()).toContain('[workspace]')

  // The open conversation's next command meets it, in auto too.
  await page.keyboard.press('Escape')
  await model.reply(
    { calls: [{ arguments: JSON.stringify({ command: 'echo forbidden' }), name: 'run_command' }] },
    { text: 'It was blocked.' }
  )
  await say(page, 'Run it')
  await expect(page.getByTestId('assistant-message').last()).toHaveText('It was blocked.')
  expect((await asked()).at(-1)).toContain("the user's deny rule 'echo forbidden*'")

  // Personalization: "About you", an entry added and edited, kept in USER.md.
  settings = await openSettings(page, 'personalization')
  const about = settings.getByTestId('memory-user')
  await about.getByTestId('memory-add-text').fill('Prefers answers in one line.')
  await about.getByTestId('memory-add').click()
  await expect(about.getByTestId('memory-entry')).toHaveText('Prefers answers in one line.')
  await about.getByTestId('memory-entry-edit').click()
  await about.getByTestId('memory-entry-text').fill('Prefers answers in two lines.')
  await about.getByTestId('memory-entry-save').click()
  await expect(about.getByTestId('memory-entry')).toHaveText('Prefers answers in two lines.')
  await expect(about.getByTestId('memory-used')).toHaveText('29 of 1,375 characters')
  expect(readFileSync(path.join(home, 'memory', 'USER.md'), 'utf8')).toContain('Prefers answers in two lines.')

  // Workspace: the starting folder, saved on Enter.
  settings = await openSettings(page, 'workspace')
  const elsewhere = path.join(folder, 'elsewhere')
  mkdirSync(elsewhere)
  const field = settings.getByTestId('setting-workspace.folder')
  await field.fill(elsewhere)
  await field.press('Enter')
  await expect(settings.getByTestId('setting-status')).toHaveText('Saved')
  expect(config()).toContain(JSON.stringify(elsewhere))

  // The next conversation knows the user as the screen left them, and starts in the new folder.
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'New conversation' }).click()
  await model.reply({ text: 'Two lines, then.' })
  await say(page, 'Who am I?')
  await expect(page.getByTestId('assistant-message')).toHaveText('Two lines, then.')
  const last = (await asked()).at(-1) ?? ''
  expect(last).toContain('Prefers answers in two lines.')
  expect(last).toContain(JSON.stringify(elsewhere).slice(1, -1))
})

test('each section opens from the left, a variable locks its setting, and the language reloads the window', async () => {
  desktop = await launch({ environment: { ATLAS_TIME_ZONE: 'Europe/Lisbon' } })
  const { page } = desktop

  // The soul and the plugins left the sidebar for the settings screen.
  await expect(page.getByTestId('soul-entry')).toHaveCount(0)
  await expect(page.getByTestId('plugins-entry')).toHaveCount(0)

  // The gear opens on General, with Atlas's version.
  await page.getByTestId('settings-open').click()
  const settings = page.getByTestId('settings')
  await expect(settings.getByTestId('settings-section')).toHaveAttribute('data-section', 'general')
  await expect(settings.getByTestId('settings-about')).toContainText(/Atlas \d/)

  // Model: the default model the file names, and the providers' keys.
  await openSettings(page, 'model')
  await expect(settings.getByTestId('setting-default-model')).toBeVisible()
  await expect(settings.getByTestId('key-provider-openrouter')).toBeVisible()

  // A setting an ATLAS_* variable sets shows locked, naming it.
  await openSettings(page, 'workspace')
  await expect(settings.getByTestId('setting-time.zone')).toBeDisabled()
  await expect(settings.getByTestId('setting-locked')).toHaveText(
    'Set by ATLAS_TIME_ZONE, which wins over config.toml.'
  )

  // Extensions and Machines are sections too; Extensions opens on its plugins.
  await openSettings(page, 'extensions')
  await expect(settings.getByTestId('plugins-view')).toBeVisible()
  await openSettings(page, 'machines')
  await expect(settings.getByTestId('this-machine')).toBeVisible()

  // Portuguese: the window reloads in it, and the server goes on.
  await openSettings(page, 'general')
  await settings.getByTestId('setting-language').locator('[data-value="pt-BR"]').click()
  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })
  await expect(page.getByTestId('settings-open')).toHaveAccessibleName('Configurações')
})
