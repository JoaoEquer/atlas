import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import type { ExecutorRow } from '@/protocol/protocol.generated'
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
        effort: '',
        enabled: true,
        executor: null,
        id: 'r1',
        last_run_at: null,
        last_status: 'ok',
        model: '',
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

describe('a routine an executor runs (step 25)', () => {
  it('says which executor runs it, and where', () => {
    vi.spyOn(app, 'readRuns').mockResolvedValue(undefined)
    app.$routines.set([
      {
        bot: null,
        command: null,
        created_by: 'user',
        effort: '',
        enabled: true,
        executor: { auto: false, draft: false, folder: '/work/shop', name: 'claude-code', workspace: true },
        id: 'r2',
        last_run_at: null,
        last_status: 'needed',
        model: '',
        name: 'sweep',
        next_run_at: null,
        probe: null,
        prompt: 'Sweep the dependencies.',
        running: false,
        schedule: 'every 1d'
      }
    ])
    render(<RoutinesView />)

    expect(screen.getByTestId('routine-executor').textContent).toBe(
      t.routines.executor('claude-code', '/work/shop', true)
    )
    expect(screen.getByText(t.routines.last(t.routines.status('needed')), { exact: false })).toBeTruthy()
  })
})

describe("this week's scoreboard (step 25)", () => {
  it('counts the runs that ran alone, needed the user, and failed', () => {
    app.$scoreboard.set({ alone: 12, failed: 1, needed: 2, since: '2026-11-02T03:00:00Z' })
    render(<RoutinesView />)

    expect(screen.getByTestId('routines-scoreboard').textContent).toBe(t.routines.scoreboard(12, 2, 1))
    app.$scoreboard.set(null)
  })
})

describe('a routine made and changed in the form (step 25)', () => {
  it('makes one an executor runs in a folder, in a new workspace each run', async () => {
    const saved = vi.spyOn(app, 'saveRoutine').mockResolvedValue(undefined)
    app.$workbench.set({
      executors: [{ installed: true, name: 'claude-code', title: 'Claude Code' } as ExecutorRow],
      instances: []
    })
    render(<RoutinesView />)

    fireEvent.click(screen.getByTestId('routine-new'))
    const type = (testId: string, value: string) => {
      fireEvent.change(screen.getByTestId(testId), { target: { value } })
    }
    type('routine-form-name', 'sweep')
    type('routine-form-prompt', 'Sweep the dependencies.')
    type('routine-form-schedule', 'every monday 9am')
    type('routine-form-runner', 'executor')
    type('routine-form-executor', 'claude-code')
    type('routine-form-folder', '/work/shop')
    fireEvent.click(screen.getByTestId('routine-form-workspace'))
    fireEvent.click(screen.getByTestId('routine-form-draft'))
    fireEvent.click(screen.getByTestId('routine-form-run'))
    await Promise.resolve()

    expect(saved).toHaveBeenCalledWith(
      {
        bot: '',
        effort: '',
        executor: { auto: false, draft: true, folder: '/work/shop', name: 'claude-code', workspace: true },
        model: '',
        name: 'sweep',
        probe: '',
        prompt: 'Sweep the dependencies.',
        routine_id: '',
        schedule: 'every monday 9am'
      },
      true
    )
    app.$workbench.set({ executors: [], instances: [] })
    app.closeRoutineForm()
  })

  it('opens a routine there is with what it is now', () => {
    vi.spyOn(app, 'readRuns').mockResolvedValue(undefined)
    app.$routines.set([
      {
        bot: null,
        command: null,
        created_by: 'user',
        effort: 'high',
        enabled: true,
        executor: null,
        id: 'r3',
        last_run_at: null,
        last_status: null,
        model: 'openrouter/fake-model',
        name: 'mail',
        next_run_at: null,
        probe: 'python unread.py',
        prompt: 'Triage the new mail.',
        running: false,
        schedule: 'every 5m'
      }
    ])
    render(<RoutinesView />)

    fireEvent.click(screen.getByTestId('routine-edit'))

    expect(screen.getByDisplayValue('Triage the new mail.').dataset.testid).toBe('routine-form-prompt')
    expect(screen.getByDisplayValue('python unread.py').dataset.testid).toBe('routine-form-probe')
    app.closeRoutineForm()
  })
})

describe('the templates (step 25)', () => {
  it('lists them by group, says the plugin one needs, and Add opens the form filled in', () => {
    vi.spyOn(app, 'refreshPlugins').mockImplementation(() => undefined)
    const market = vi.spyOn(app, 'openMarketItem').mockImplementation(() => undefined)
    vi.spyOn(app, 'showMarketplace').mockImplementation(() => undefined)
    app.$workbench.set({
      executors: [{ installed: true, name: 'claude-code', title: 'Claude Code' } as ExecutorRow],
      instances: []
    })
    render(<RoutinesView />)

    fireEvent.click(screen.getByTestId('routines-tab-templates'))
    const cards = screen.getAllByTestId('routine-template')
    expect(cards.map(card => card.dataset.template)).toEqual([
      'securitySweep',
      'stalePullRequests',
      'missingTests',
      'mailSummary',
      'agenda',
      'backup'
    ])
    const stale = cards[1]
    expect(stale?.textContent).toContain(t.templates.needs('github'))
    fireEvent.click(within(stale as HTMLElement).getByTestId('routine-template-add'))
    expect(market).toHaveBeenCalledWith('github')
    fireEvent.click(within(cards[0] as HTMLElement).getByTestId('routine-template-add'))

    expect(app.$routineForm.get()).toEqual({
      effort: '',
      executor: { auto: false, draft: false, folder: '', name: 'claude-code', workspace: false },
      name: t.templates.words.securitySweep.name,
      prompt: t.templates.words.securitySweep.prompt,
      schedule: 'every monday at 9am'
    })
    app.closeRoutineForm()
    app.$workbench.set({ executors: [], instances: [] })
  })
})
