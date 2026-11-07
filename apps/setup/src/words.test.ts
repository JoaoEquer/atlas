import { describe, expect, it } from 'vitest'

import { languageOf, WORDS, type Words } from './words'

describe('languageOf', () => {
  it('is Portuguese when the system first prefers it, from Brazil or Portugal', () => {
    expect(languageOf(['pt-BR', 'en-US'])).toBe('pt-BR')
    expect(languageOf(['PT-pt'])).toBe('pt-BR')
  })

  it('is English otherwise, and with no preference', () => {
    expect(languageOf(['en-US', 'pt-BR'])).toBe('en')
    expect(languageOf(['de'])).toBe('en')
    expect(languageOf([])).toBe('en')
  })
})

describe('WORDS', () => {
  it('says everything in both languages', () => {
    for (const words of Object.values(WORDS)) {
      for (const key of Object.keys(words) as (keyof Words)[]) {
        expect(words[key].trim(), key).not.toBe('')
      }
    }
  })
})
