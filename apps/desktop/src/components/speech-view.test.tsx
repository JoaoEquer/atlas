import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SpeechState } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { speechAsks, SpeechBand, SpeechModel } from './speech-view'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$speech.set(null)
})

const WANTED: SpeechState = {
  credit: 'Parakeet TDT 0.6B v3 by NVIDIA, CC BY 4.0',
  declined: false,
  downloading: null,
  error: '',
  present: false,
  size: 672_000_000,
  transcription: 'local',
  wanted: true
}

describe('the question about the speech model', () => {
  it('is asked only once a recording waited for a model that is not here, and never after a no', () => {
    expect(speechAsks(null)).toBe(false)
    expect(speechAsks(WANTED)).toBe(true)
    expect(speechAsks({ ...WANTED, wanted: false })).toBe(false)
    expect(speechAsks({ ...WANTED, declined: true })).toBe(false)
    expect(speechAsks({ ...WANTED, present: true })).toBe(false)
    expect(speechAsks({ ...WANTED, downloading: 0 })).toBe(false)
    expect(speechAsks({ ...WANTED, transcription: 'openai' })).toBe(false)
  })

  it('says the size, and downloads or keeps the no', () => {
    const download = vi.spyOn(app, 'downloadSpeech').mockResolvedValue()
    const decline = vi.spyOn(app, 'declineSpeech').mockResolvedValue()
    app.$speech.set(WANTED)
    render(<SpeechBand />)

    expect(screen.getByTestId('speech-band').textContent).toContain('672 MB')
    fireEvent.click(screen.getByTestId('speech-band-download'))
    fireEvent.click(screen.getByTestId('speech-band-decline'))
    expect(download).toHaveBeenCalledOnce()
    expect(decline).toHaveBeenCalledOnce()
  })

  it("shows the download's progress, and nothing once the model is here", () => {
    app.$speech.set({ ...WANTED, downloading: 336_000_000 })
    const { rerender } = render(<SpeechBand />)
    expect(screen.getByTestId('speech-band').textContent).toContain('336 of 672 MB')

    app.$speech.set({ ...WANTED, present: true })
    rerender(<SpeechBand />)
    expect(screen.queryByTestId('speech-band')).toBeNull()
  })
})

describe('the model in Settings, Voice', () => {
  it("credits the model's makers and offers its download with its size", () => {
    const download = vi.spyOn(app, 'downloadSpeech').mockResolvedValue()
    render(<SpeechModel speech={{ ...WANTED, error: 'The speech model could not be downloaded (HTTP 503).' }} />)

    expect(screen.getByTestId('speech-model').textContent).toContain('NVIDIA, CC BY 4.0')
    expect(screen.getByRole('alert').textContent).toContain('HTTP 503')
    fireEvent.click(screen.getByTestId('speech-model-download'))
    expect(download).toHaveBeenCalledOnce()
  })

  it('says it is here once downloaded', () => {
    render(<SpeechModel speech={{ ...WANTED, present: true }} />)

    expect(screen.getByTestId('speech-model-present')).toBeTruthy()
    expect(screen.queryByTestId('speech-model-download')).toBeNull()
  })
})
