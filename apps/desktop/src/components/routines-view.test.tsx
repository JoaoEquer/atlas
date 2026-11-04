import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import * as app from '@/store/app'

import { RoutinesView } from './routines-view'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$routines.set([])
  app.$runs.set({})
})

describe('a routine woken by its probe (step 23)', () => {
  it('shows its probe, and which of its runs the probe woke', () => {
    vi.spyOn(app, 'readRuns').mockResolvedValue(undefined)
    app.$routines.set([
      {
        bot: null,
        command: null,
        created_by: 'agent',
        enabled: true,
        id: 'r1',
        last_run_at: null,
        last_status: 'ok',
        name: 'mail',
        next_run_at: null,
        probe: 'python unread.py',
        prompt: 'Triage the new mail.',
        running: false,
        schedule: 'every 5m'
      }
    ])
    app.$runs.setKey('r1', [
      {
        conversation_id: null,
        ended_at: null,
        error: null,
        event: '1 new mail',
        lateness: 'on_time',
        number: 2,
        output: 'Answered.',
        started_at: '2026-11-04T13:05:00Z',
        status: 'ok'
      },
      {
        conversation_id: null,
        ended_at: null,
        error: null,
        event: '',
        lateness: 'manual',
        number: 1,
        output: 'Its probe heard nothing new.',
        started_at: '2026-11-04T13:00:00Z',
        status: 'silent'
      }
    ])
    render(<RoutinesView />)

    expect(screen.getByTestId('routine-probe').textContent).toBe(t.routines.probe('python unread.py'))
    fireEvent.click(screen.getByText(t.routines.showRuns))
    const woken = screen.getAllByTestId('run-event')
    expect(woken).toHaveLength(1)
    expect(woken[0]?.getAttribute('title')).toBe('1 new mail')
  })
})
