import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ExecutorUsageRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { PlanRing } from './plan-ring'

const USAGE: ExecutorUsageRow = {
  executor: 'claude-code',
  extra_usage: false,
  listed: true,
  plan: 'max',
  read_at: new Date().toISOString(),
  unavailable: '',
  windows: [
    { id: 'five_hour', minutes: 300, model: '', resets_at: '', title: 'Current session', used: 76.4 },
    { id: 'seven_day', minutes: 10080, model: '', resets_at: '', title: 'Current week', used: 11 }
  ]
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$executorUsage.set({})
})

describe('the plan ring', () => {
  it('shows the fullest of the plan limits', () => {
    vi.spyOn(app, 'readUsageIfStale').mockImplementation(() => undefined)
    app.$executorUsage.setKey('claude-code', { error: null, reading: false, usage: USAGE })
    render(<PlanRing executor="claude-code" />)

    const ring = screen.getByTestId('plan-ring')
    expect(ring.getAttribute('aria-valuenow')).toBe('76')
    expect(ring.getAttribute('aria-label')).toBe('76% used · Current session')
    expect(ring.className).toContain('text-muted-foreground')
  })

  it('turns to the warning color near the limit', () => {
    vi.spyOn(app, 'readUsageIfStale').mockImplementation(() => undefined)
    const windows = USAGE.windows.map(window => ({ ...window, used: window.minutes === 300 ? 91 : 11 }))
    app.$executorUsage.setKey('claude-code', { error: null, reading: false, usage: { ...USAGE, windows } })
    render(<PlanRing executor="claude-code" />)

    expect(screen.getByTestId('plan-ring').className).toContain('text-waiting')
  })

  it('is not there for a login with no plan limits, and reads the usage when it is old', () => {
    const read = vi.spyOn(app, 'readUsageIfStale').mockImplementation(() => undefined)
    app.$connection.set({ error: null, state: 'open' })
    app.$executorUsage.setKey('claude-code', {
      error: null,
      reading: false,
      usage: { ...USAGE, unavailable: 'An API key', windows: [] }
    })
    render(<PlanRing executor="claude-code" />)

    expect(screen.queryByTestId('plan-ring')).toBeNull()
    expect(read).toHaveBeenCalledWith('claude-code')
    app.$connection.set({ error: null, state: 'idle' })
  })
})
