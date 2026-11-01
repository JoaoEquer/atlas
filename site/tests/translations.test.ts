import { createHash } from 'node:crypto'
import { readdirSync, readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import * as en from '../src/lib/home'
import * as ptBr from '../src/lib/home.pt-br'
import { WORDS } from '../src/lib/i18n'

const PAGES = new URL('../../src/atlas/docs/pages/', import.meta.url)
const TRANSLATIONS = new URL('../src/translations/pt-br/', import.meta.url)

/** A file's SHA-256, its line ends read as `\n` so a Windows checkout hashes as CI does. */
function sha256(url: URL): string {
  return createHash('sha256').update(readFileSync(url, 'utf8').replace(/\r\n/g, '\n')).digest('hex')
}

describe('the Portuguese documentation', () => {
  const sources = JSON.parse(readFileSync(new URL('sources.json', TRANSLATIONS), 'utf8')) as Record<string, string>
  const translated = readdirSync(TRANSLATIONS).filter(name => name.endsWith('.md'))

  it('names the English page each translation was made from', () => {
    expect(Object.keys(sources).sort()).toEqual(translated.sort())
  })

  // A page changed in English changes its translation in the same commit, then records the new
  // hash in sources.json: a translation never says what the English page no longer does.
  it.each(translated)('%s is translated from the English page as it is now', name => {
    expect(sha256(new URL(name, PAGES)), `${name} changed in English; update its translation and sources.json`).toBe(
      sources[name]
    )
  })

  it.each(translated)('%s links to the Portuguese documentation', name => {
    const text = readFileSync(new URL(name, TRANSLATIONS), 'utf8')

    expect(text).not.toMatch(/\]\(\/docs\//)
  })
})

describe('the Portuguese home', () => {
  it('says each English chapter, question and card, in the same order', () => {
    expect(ptBr.chapters.map(c => c.id)).toEqual(en.chapters.map(c => c.id))
    expect(ptBr.faq.items.map(item => item.href?.replace('/pt-br', ''))).toEqual(en.faq.items.map(item => item.href))
    expect(ptBr.catalogue.items.map(item => item.href.replace('/pt-br', ''))).toEqual(
      en.catalogue.items.map(item => item.href)
    )
    expect(ptBr.gate.commands.map(c => [c.command, c.tone])).toEqual(en.gate.commands.map(c => [c.command, c.tone]))
    expect(ptBr.compare.rows).toHaveLength(en.compare.rows.length)
    expect(ptBr.proof).toHaveLength(en.proof.length)
  })

  it('leads to the Portuguese documentation', () => {
    for (const href of [...ptBr.catalogue.items.map(item => item.href), ptBr.gate.link.href]) {
      expect(href).toMatch(/^\/pt-br\/docs\//)
    }
  })
})

describe('the frame’s words', () => {
  it('are all said in both languages', () => {
    const keys = (value: unknown, path = ''): string[] =>
      typeof value === 'object' && value !== null
        ? Object.entries(value).flatMap(([key, inner]) => keys(inner, `${path}.${key}`))
        : [path]

    expect(keys(WORDS['pt-br']).sort()).toEqual(keys(WORDS.en).sort())
  })
})
