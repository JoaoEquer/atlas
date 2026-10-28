/**
 * The voice plugin in the real app (step 17a): turned on from the terminal, its screen adds the
 * box's two buttons; the microphone dictates into the box, through the plugin's Python and the fake
 * model server's transcription; the live conversation asks the plugin's Python for its session,
 * which reaches OpenAI's session endpoint with the app's WebRTC offer.
 *
 * Its settings are picked from lists its Python asks the provider for (SDK version 25), and switch
 * to Gemini's when the provider changes.
 *
 * Chromium's fake microphone stands in for a real one. The voice itself, spoken and heard over
 * WebRTC, needs OpenAI: the fake answers no real WebRTC, so the conversation ends there, saying why.
 */
import { spawnSync } from 'node:child_process'

import { expect, test } from '@playwright/test'

import { atlas, cleanEnvironment, type Desktop, type FakeModel, launch, openSettings, ROOT } from './harness'

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

/** The voice plugin on, pointed at the fake model server, as a user does from a terminal. */
function voiceOn(home: string, model: FakeModel): void {
  atlas(home, 'plugins', 'set', 'voice', 'api_url', `http://127.0.0.1:${String(model.port)}/v1`)
  const stored = spawnSync('uv', ['run', '--project', ROOT, 'atlas', 'plugins', 'secret', 'voice', 'OPENAI_API_KEY'], {
    cwd: ROOT,
    encoding: 'utf8',
    env: cleanEnvironment({ ATLAS_HOME: home }),
    input: 'fake-key\n',
    windowsHide: true
  })
  expect(stored.status, stored.stderr).toBe(0)
  atlas(home, 'plugins', 'enable', 'voice', '--yes')
}

const MICROPHONE = ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream']

test('the box dictates through the voice plugin, and the words wait there to be sent', async () => {
  desktop = await launch({ chromium: MICROPHONE, setup: voiceOn })
  const { model, page } = desktop
  await model.transcribe('Remind me to call the dentist on Thursday.')

  const dictate = page.getByTestId('voice-dictate')
  await expect(dictate).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('voice-talk')).toBeVisible()

  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  await box.fill('Please:')
  await dictate.click()
  await expect(dictate).toHaveAttribute('data-state', 'recording')
  await page.waitForTimeout(800)
  await dictate.click()

  await expect(box).toHaveValue('Please: Remind me to call the dentist on Thursday.', { timeout: 30_000 })
  expect((await model.requests()).some(request => request.path === '/v1/audio/transcriptions')).toBe(true)
  // Dictated, not sent: no turn started.
  expect((await model.requests()).some(request => request.path === '/v1/chat/completions')).toBe(false)
})

test('the live conversation opens its session through the plugin, the key kept on the server', async () => {
  desktop = await launch({ chromium: MICROPHONE, setup: voiceOn })
  const { model, page } = desktop

  const talk = page.getByTestId('voice-talk')
  await expect(talk).toBeVisible({ timeout: 30_000 })
  await talk.click()

  // The fake answers with no real WebRTC: the session is asked for, then the conversation ends.
  await expect
    .poll(async () => (await model.requests()).filter(request => request.path === '/v1/live/sessions').length, {
      timeout: 30_000
    })
    .toBe(1)
  const [session] = (await model.requests()).filter(request => request.path === '/v1/live/sessions')
  const body = session?.body as unknown as { session: { model: string }; transport: { sdp: string; type: string } }
  expect(body.session.model).toBe('gpt-live-1')
  expect(body.transport.type).toBe('webrtc')
  expect(body.transport.sdp).toMatch(/^v=0/)
  expect(session?.authorization).toBe('Bearer fake-key')
  await expect(talk).toHaveAttribute('data-state', 'idle', { timeout: 30_000 })
})

interface Probe {
  appFocused: boolean | null
  bars: ({ text: string; tone: string } | null)[]
  held: Map<string, () => void>
  pasted: string[]
  press: (accelerator: string) => boolean
}

test('the shortcut dictates anywhere: pasted where the user is, and kept on the Dictations page', async () => {
  desktop = await launch({ chromium: MICROPHONE, setup: voiceOn })
  const { app, model, page } = desktop
  // Each runs in the main process, where the end-to-end powers note what the screen did.
  const held = () =>
    app.evaluate(() => [...(globalThis as unknown as { atlasDesktop: Probe }).atlasDesktop.held.keys()])
  const press = () =>
    app.evaluate(() => (globalThis as unknown as { atlasDesktop: Probe }).atlasDesktop.press('Capslock'))
  const focusApp = (focused: boolean) =>
    app.evaluate((_electron, given) => {
      ;(globalThis as unknown as { atlasDesktop: Probe }).atlasDesktop.appFocused = given
    }, focused)
  const pasted = () => app.evaluate(() => (globalThis as unknown as { atlasDesktop: Probe }).atlasDesktop.pasted)
  const tone = () =>
    app.evaluate(() => (globalThis as unknown as { atlasDesktop: Probe }).atlasDesktop.bars.at(-1)?.tone ?? null)

  // Caps Lock, unless the user chose another: held for the plugin as its screen loads.
  await expect.poll(() => held(), { timeout: 30_000 }).toEqual(['Capslock'])

  // Another program has the focus: the words are pasted there.
  await focusApp(false)
  await model.transcribe('Send the report to the team.')
  expect(await press()).toBe(true)
  await expect.poll(() => tone()).toBe('recording')
  await page.waitForTimeout(800)
  await press()
  await expect.poll(() => pasted(), { timeout: 30_000 }).toEqual(['Send the report to the team.'])
  await expect.poll(() => tone()).toBe('done')

  // The app has the focus, its box the cursor: the words are typed there, unsent.
  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  await box.click()
  await focusApp(true)
  await model.transcribe('And book the room.')
  await page.waitForTimeout(400)
  await press()
  await page.waitForTimeout(800)
  await press()
  await expect(box).toHaveValue('And book the room.', { timeout: 30_000 })
  expect(await pasted()).toHaveLength(1)
  expect((await model.requests()).some(request => request.path === '/v1/chat/completions')).toBe(false)

  // Both kept in the home, with their audio, on the plugin's page.
  await page.getByTestId('plugin-page-voice:dictations').click()
  const rows = page.getByTestId('voice-dictation-text')
  await expect(rows).toHaveCount(2, { timeout: 30_000 })
  await expect(page.getByTestId('voice-dictations')).toContainText('Send the report to the team.')
  await expect(page.getByTestId('voice-shortcut')).toHaveText('Capslock')

  // The key changed on the page: the old one let go, the new one held.
  await page.getByTestId('voice-shortcut-change').click()
  await page.keyboard.press('F9')
  await expect(page.getByTestId('voice-shortcut')).toHaveText('F9')
  await expect.poll(() => held()).toEqual(['F9'])

  // Deleted from the page, gone from the home.
  await page.getByTestId('voice-dictation-remove').first().click()
  await expect(rows).toHaveCount(1)
})

test("the voice's settings are picked from lists, which follow the provider", async () => {
  desktop = await launch({ setup: voiceOn })
  const { page } = desktop

  await openSettings(page, 'plugins')
  const row = page.locator('[data-testid="plugin-row"][data-plugin="voice"]')
  await row.locator('summary').click()
  const provider = row.getByLabel('provider', { exact: true })
  const model = row.getByLabel('transcription_model', { exact: true })

  await expect(provider.locator('option')).toHaveText(['openai', 'gemini', 'Other…'])
  // The fake's OpenAI lists no transcription model: the provider's own is left, and typing another.
  await expect(model.locator('option')).toHaveText(["OpenAI's choice: gpt-transcribe", 'Other…'])

  await provider.selectOption('gemini')

  await expect(model.locator('option').first()).toHaveText("Gemini's choice: gemini-3.5-transcribe")
  await expect(row).toContainText('There is no GEMINI_API_KEY in the vault')
})
