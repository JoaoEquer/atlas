import { describe, expect, it } from 'vitest'

import type { ExecutorUsageRow, UsageWindowRow } from '@/protocol/protocol.generated'

import { level, reading, resetsIn, stale, told, windowName } from './usage'

const NAMES = {
  days: (count: number) => `${String(count)} days`,
  session: 'Session',
  week: 'Week',
  weekOf: (model: string) => `Week (${model})`
}
const window = (minutes: number, model = '', title = 'Its own'): UsageWindowRow => ({
  id: 'w',
  minutes,
  model,
  resets_at: '',
  title,
  used: 10
})
const NOW = Date.parse('2026-10-18T12:00:00Z')
const usage = (readAt: string): ExecutorUsageRow => ({
  executor: 'claude-code',
  extra_usage: null,
  listed: true,
  plan: '',
  read_at: readAt,
  unavailable: '',
  windows: []
})

describe('a plan window on screen', () => {
  it('is named by how long it lasts, its harness title for any other', () => {
    expect(windowName(window(300), NAMES)).toBe('Session')
    expect(windowName(window(10080), NAMES)).toBe('Week')
    expect(windowName(window(10080, 'Opus'), NAMES)).toBe('Week (Opus)')
    expect(windowName(window(0), NAMES)).toBe('Its own')
    expect(windowName(window(2880), NAMES)).toBe('2 days')
    expect(windowName(window(43200), NAMES)).toBe('30 days')
    expect(windowName(window(2000), NAMES)).toBe('Its own')
  })

  it('says its reset in hours within a day, nothing when past or unknown', () => {
    expect(resetsIn('2026-10-18T14:10:00Z', NOW)).toBe('2 h 10 min')
    expect(resetsIn('2026-10-18T12:00:20Z', NOW)).toBe('1 min')
    expect(resetsIn('2026-10-18T13:59:45Z', NOW)).toBe('2 h 0 min')
    expect(resetsIn('2026-10-18T12:59:40Z', NOW)).toBe('1 h 0 min')
    expect(resetsIn('2026-10-18T11:00:00Z', NOW)).toBe('')
    expect(resetsIn('', NOW)).toBe('')
    expect(resetsIn('2026-10-23T08:00:00Z', NOW, 'en')).not.toBe('')
  })

  it('turns amber from 80% and red from 95%', () => {
    expect([level(79), level(80), level(94.9), level(95)]).toEqual(['ok', 'high', 'high', 'full'])
  })
})

describe('when the panel reads the usage again', () => {
  it('on a first show, after two minutes, or after a failure; never while a read runs', () => {
    expect(stale(undefined, NOW)).toBe(true)
    expect(stale(reading(undefined), NOW)).toBe(false)
    const fresh = told(undefined, usage('2026-10-18T11:59:00Z'))
    expect(stale(fresh, NOW)).toBe(false)
    expect(stale(told(undefined, usage('2026-10-18T11:57:00Z')), NOW)).toBe(true)
    expect(stale(reading(fresh), NOW)).toBe(false)
    expect(stale({ error: 'x', reading: false, usage: null }, NOW)).toBe(true)
    expect(reading(fresh).usage).toBe(fresh.usage)
  })
})
