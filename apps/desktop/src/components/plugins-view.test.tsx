import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { PluginRow, PluginSettingRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { PluginsSection, withValues } from './plugins-view'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$plugins.set([])
  app.$modelOptions.set(null)
})

const setting = (name: string, more: Partial<PluginSettingRow> = {}): PluginSettingRow => ({
  choices: [],
  choices_from: '',
  default: '',
  description: '',
  name,
  value: '',
  ...more
})

const voice = (settings: PluginSettingRow[], enabled = true): PluginRow => ({
  author: '',
  bundled: true,
  channels: [],
  consent: 'c',
  consented: true,
  declaration: [],
  description: 'Talk to Atlas by voice.',
  enabled,
  name: 'voice',
  problem: null,
  running: enabled,
  secrets: [],
  settings,
  version: '0.4.0'
})

/** The plugin's settings, opened as the user opens them. */
function shown(settings: PluginSettingRow[], enabled = true) {
  app.$plugins.set([voice(settings, enabled)])
  render(<PluginsSection />)
  fireEvent.click(screen.getByText('Settings'))
}

describe("a plugin's setting with choices (SDK version 25)", () => {
  it('is picked from the list the plugin gives, and saved as it is picked', async () => {
    const asked = vi.spyOn(app, 'settingChoices').mockResolvedValue({
      choices: [
        { label: "Gemini's choice: gemini-3.5-transcribe", value: '' },
        { label: 'Gemini 3.5 Transcribe', value: 'gemini-3.5-transcribe' },
        { label: 'Gemini 3.8 Flash', value: 'gemini-3.8-flash' }
      ],
      why: ''
    })
    const saved = vi.spyOn(app, 'setPluginSetting').mockResolvedValue(true)
    shown([setting('transcription_model', { choices_from: 'plugin' })])

    const list = await screen.findByRole<HTMLSelectElement>('combobox')

    expect(asked).toHaveBeenCalledWith('voice', expect.objectContaining({ name: 'transcription_model' }))
    expect(Array.from(list.options).map(option => option.text)).toEqual([
      "Gemini's choice: gemini-3.5-transcribe",
      'Gemini 3.5 Transcribe',
      'Gemini 3.8 Flash',
      'Other…'
    ])
    expect(screen.queryByText('Save')).toBeNull()

    fireEvent.change(list, { target: { value: 'gemini-3.8-flash' } })

    expect(saved).toHaveBeenCalledWith('voice', 'transcription_model', 'gemini-3.8-flash')
  })

  it('types a value the list does not hold on "Other…", and goes back to the list', async () => {
    vi.spyOn(app, 'settingChoices').mockResolvedValue({ choices: [{ label: 'A', value: 'a' }], why: '' })
    const saved = vi.spyOn(app, 'setPluginSetting').mockResolvedValue(true)
    shown([setting('voice', { choices_from: 'plugin' })])

    fireEvent.change(await screen.findByRole('combobox'), { target: { value: '\u0000other' } })
    const box = screen.getByRole('textbox')
    fireEvent.change(box, { target: { value: 'my-own-voice' } })
    fireEvent.click(screen.getByText('Save'))

    expect(saved).toHaveBeenCalledWith('voice', 'voice', 'my-own-voice')

    fireEvent.click(screen.getByText('List'))

    expect(screen.getByRole('combobox')).toBeTruthy()
  })

  it('says why a list was kept short, and is typed when it holds nothing', async () => {
    vi.spyOn(app, 'settingChoices').mockResolvedValue({ choices: [], why: 'There is no GEMINI_API_KEY in the vault.' })
    shown([setting('speech_model', { choices_from: 'plugin' })])

    expect(await screen.findByText('There is no GEMINI_API_KEY in the vault.')).toBeTruthy()
    expect(screen.getByRole('textbox')).toBeTruthy()
  })

  it('does not ask a plugin that is off, whose Python answers no call', () => {
    const asked = vi.spyOn(app, 'settingChoices')
    shown([setting('speech_model', { choices_from: 'plugin' })], false)

    expect(asked).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox')).toBeTruthy()
  })

  it('lists the fixed values and the models of the picker without asking the plugin', async () => {
    app.$modelOptions.set({
      default: { effort: null, model: null, picked: false, provider: null },
      providers: [
        {
          id: 'gemini',
          models: [
            { default_effort: null, efforts: null, id: 'gemini-3.8-flash', name: 'Gemini 3.8 Flash', reasoning: null }
          ],
          name: 'Google Gemini'
        }
      ]
    })

    await expect(app.settingChoices('voice', setting('provider', { choices: ['openai', 'gemini'] }))).resolves.toEqual({
      choices: [
        { label: 'openai', value: 'openai' },
        { label: 'gemini', value: 'gemini' }
      ],
      why: ''
    })
    await expect(app.settingChoices('voice', setting('turns', { choices_from: 'models' }))).resolves.toEqual({
      choices: [{ label: 'Google Gemini · Gemini 3.8 Flash', value: 'gemini/gemini-3.8-flash' }],
      why: ''
    })
  })

  it('always holds the value in use and the default', () => {
    expect(withValues([{ label: 'A', value: 'a' }], ['old-model', '', 'a'])).toEqual([
      { label: 'A', value: 'a' },
      { label: 'old-model', value: 'old-model' },
      { label: '', value: '' }
    ])
  })
})

describe('a setting with no choices', () => {
  it('is typed and saved with its button, as before', async () => {
    const asked = vi.spyOn(app, 'settingChoices')
    const saved = vi.spyOn(app, 'setPluginSetting').mockResolvedValue(true)
    shown([setting('dictation_shortcut', { default: 'Capslock', value: 'Capslock' })])

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'F9' } })
    fireEvent.click(screen.getByText('Save'))

    await waitFor(() => {
      expect(saved).toHaveBeenCalledWith('voice', 'dictation_shortcut', 'F9')
    })
    expect(asked).not.toHaveBeenCalled()
  })
})
