import { describe, expect, it } from 'vitest'

import { en } from './en'
import { ptBR } from './pt-BR'

import { locale, messagesFor, pickLocale } from './index'

/** Every leaf's path in a catalog: `composer.send`, `ask.title`... */
function leaves(value: unknown, prefix = ''): string[] {
  if (typeof value !== 'object' || value === null) {
    return [prefix]
  }

  return Object.entries(value).flatMap(([key, inner]) => leaves(inner, prefix ? `${prefix}.${key}` : key))
}

describe('the screen language', () => {
  it('comes from the system, Portuguese for any pt, English for the rest', () => {
    expect(pickLocale(['pt-BR', 'en-US'])).toBe('pt-BR')
    expect(pickLocale(['pt-PT'])).toBe('pt-BR')
    expect(pickLocale(['pt'])).toBe('pt-BR')
    expect(pickLocale(['en-GB', 'pt-BR'])).toBe('en')
    expect(pickLocale(['fr-FR', 'pt-BR'])).toBe('pt-BR')
    expect(pickLocale(['fr-FR'])).toBe('en')
    expect(pickLocale([])).toBe('en')
    expect(locale).toBe('en') // jsdom's navigator says en-US
  })

  it('has every text in Portuguese, a complete locale holding every key', () => {
    expect(leaves(ptBR).sort()).toEqual(leaves(en).sort())
  })

  it('merges over English and keeps the functions', () => {
    const pt = messagesFor('pt-BR')
    expect(pt.composer.send).toBe('Enviar')
    expect(pt.ask.title(3)).toBe('O agente faz 3 perguntas')
    expect(pt.composer.effortLevel('xhigh')).toBe('Muito alto')
    expect(messagesFor('en').composer.effortLevel('none')).toBe('Off')
    expect(messagesFor('en').composer.effortLevel('ultra')).toBe('ultra')
  })

  it("says a tool's time in the unit it reads in once rounded, never 1000ms, 10.0s or 60s", () => {
    const { duration } = messagesFor('en')
    expect([duration(0.012), duration(0.9996), duration(4.24), duration(9.97)]).toEqual(['12ms', '1.0s', '4.2s', '10s'])
    expect([duration(25), duration(59.6), duration(65)]).toEqual(['25s', '1m 0s', '1m 5s'])
    const pt = messagesFor('pt-BR').duration
    expect([pt(0.9996), pt(4.24), pt(9.97), pt(59.6)]).toEqual(['1,0s', '4,2s', '10s', '1min 0s'])
  })

  it("says a file's size in the unit it reads in once rounded, never 1024 KB", () => {
    const { size } = messagesFor('en').attachments
    expect([size(500), size(2048), size(1024 * 1024 - 100), size(5 * 1024 * 1024)]).toEqual([
      '500 B',
      '2 KB',
      '1.0 MB',
      '5.0 MB'
    ])
    expect(messagesFor('pt-BR').attachments.size(1024 * 1024 - 100)).toBe('1,0 MB')
  })
})
