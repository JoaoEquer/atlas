import { describe, expect, it } from 'vitest'

import { installsQuery, installsReport } from '../src/lib/installs-report'
import { clientOf, downloadPoint, installerOf } from '../worker/installs'

describe('installerOf', () => {
  it('knows the two installers and nothing else', () => {
    expect(installerOf('/install.ps1')).toBe('windows')
    expect(installerOf('/install.sh')).toBe('unix')
    expect(installerOf('/docs/install/')).toBeNull()
    expect(installerOf('/scripts/install.sh')).toBeNull()
  })
})

describe('clientOf', () => {
  it('names the clients the install page gives', () => {
    expect(clientOf('Mozilla/5.0 (Windows NT; Windows NT 10.0; en-US) WindowsPowerShell/5.1.26100.1')).toBe(
      'powershell'
    )
    expect(clientOf('Mozilla/5.0 (Windows NT 10.0; Microsoft Windows 10.0.26100; en-US) PowerShell/7.5.0')).toBe(
      'powershell'
    )
    expect(clientOf('curl/8.7.1')).toBe('curl')
  })

  it('puts browsers, crawlers and no header apart', () => {
    expect(clientOf('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15')).toBe(
      'other'
    )
    expect(clientOf('Mozilla/5.0 (compatible; Googlebot/2.1)')).toBe('other')
    expect(clientOf(null)).toBe('other')
  })
})

describe('downloadPoint', () => {
  it('holds the system, the client and the country, and nothing about the person', () => {
    expect(downloadPoint('windows', 'curl/8.7.1', 'BR')).toEqual({
      blobs: ['windows', 'curl', 'BR'],
      indexes: ['windows']
    })
    expect(downloadPoint('unix', null, undefined).blobs).toEqual(['unix', 'other', 'unknown'])
  })
})

describe('installsReport', () => {
  it('asks for the days given', () => {
    expect(installsQuery(7)).toContain("INTERVAL '7' DAY")
    expect(installsQuery(7)).toContain('FROM atlas_installs')
  })

  it('counts the install lines and lists browsers and crawlers apart', () => {
    const report = installsReport(
      [
        { day: '2026-10-27 00:00:00', system: 'windows', client: 'powershell', country: 'BR', downloads: '3' },
        { day: '2026-10-28 00:00:00', system: 'windows', client: 'powershell', country: 'US', downloads: '1' },
        { day: '2026-10-28 00:00:00', system: 'unix', client: 'curl', country: 'BR', downloads: 2 },
        { day: '2026-10-28 00:00:00', system: 'unix', client: 'other', country: 'BR', downloads: 5 }
      ],
      30
    )
    expect(report).toContain('Installs begun in the last 30 days: 6')
    expect(report).toMatch(/Windows\s+4/)
    expect(report).toMatch(/macOS and Linux\s+2/)
    expect(report).toMatch(/2026-10-28\s+3/)
    expect(report).toMatch(/BR\s+5/)
    expect(report).toContain('not counted above: 5')
  })

  it('says none when nothing was downloaded', () => {
    expect(installsReport([], 30)).toBe(
      'Installs begun in the last 30 days: 0\n\nOpened by a browser or a crawler, not counted above: 0'
    )
  })
})
