import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { installWays } from '../src/lib/install'

/** The files the Setup's build names (apps/setup/electron-builder.yml), `${arch}` read out. */
function setupFiles(): string[] {
  const config = readFileSync(new URL('../../apps/setup/electron-builder.yml', import.meta.url), 'utf8')
  const names = [...config.matchAll(/artifactName: (\S+)/g)].map(match => match[1] ?? '')
  const archs: Record<string, string[]> = {
    'Atlas-Setup-${arch}.dmg': ['arm64', 'x64'],
    'Atlas-Setup-${arch}.AppImage': ['x86_64']
  }

  return names.flatMap(name => (archs[name] ?? ['']).map(arch => name.replace('${arch}', arch)))
}

describe('the Setup beside each system', () => {
  it('links every file the Setup is built as, from the release `setup`', () => {
    const links = installWays.flatMap(way => way.downloads ?? []).map(download => download.href)

    expect(links.map(href => href.split('/').pop()).sort()).toEqual(setupFiles().sort())
    for (const href of links) {
      expect(href).toMatch(/^https:\/\/github\.com\/marqueskaio\/atlas\/releases\/download\/setup\//)
    }
  })

  it('is offered for each desktop system, not for Docker', () => {
    const offered = installWays.filter(way => way.downloads).map(way => way.id)

    expect(offered).toEqual(['windows', 'macos', 'linux'])
  })
})
