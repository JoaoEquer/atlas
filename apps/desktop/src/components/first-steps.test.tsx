import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { BotRow, ExecutorRow, InstanceRow, PairingRow, PluginRow, RoutineRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import { $firstSteps, firstStepsOf, markDone } from '@/store/first-steps'
import { $firstRun, finishStep, firstRunOf, openStep } from '@/store/setup'

import { FirstSteps } from './first-steps'

const CLAUDE = {
  base: '',
  enabled: true,
  installed: true,
  name: 'claude-code',
  supported: true
} as ExecutorRow
const BOT = { name: 'writer', title: 'Writer' } as BotRow
const TELEGRAM_USER = { approved: true, channel: 'telegram', name: 'Ana', user: '1' } as PairingRow

function opened(): void {
  app.$connection.set({ error: null, state: 'open' })
  app.$read.setKey('workbench', true)
  app.$read.setKey('bots', true)
}

beforeEach(() => {
  app.$home.set('default')
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  localStorage.clear()
  $firstSteps.set({})
  $firstRun.set({})
  app.$connection.set({ error: null, state: 'idle' })
  app.$read.setKey('workbench', false)
  app.$read.setKey('bots', false)
  app.$workbench.set({ executors: [], instances: [] })
  app.$plugins.set([])
  app.$bots.set([])
  app.$pairings.set([])
  app.$routines.set([])
  app.$home.set(null)
})

describe('the first steps', () => {
  it('wait for the home to be read, so nothing looks undone before it is', () => {
    render(<FirstSteps />)

    expect(screen.queryByTestId('first-steps')).toBeNull()
  })

  it('count what the home already did and offer the first task not done', () => {
    app.$bots.set([BOT])
    app.$pairings.set([TELEGRAM_USER])
    opened()
    render(<FirstSteps />)

    expect(screen.getByTestId('first-steps-count').textContent).toBe('2 of 5')
    expect(screen.getByTestId('first-step-bot').dataset.done).toBe('true')
    expect(screen.getByTestId('first-step-telegram').dataset.done).toBe('true')
    expect(screen.getByTestId('first-step-delegate').className).toContain('bg-selected')
  })

  it('keep a task done once its bot, routine or session is gone', () => {
    app.$routines.set([{ id: 'r1' } as RoutineRow])
    opened()
    const { unmount } = render(<FirstSteps />)
    unmount()
    app.$routines.set([])
    render(<FirstSteps />)

    expect(screen.getByTestId('first-step-routine').dataset.done).toBe('true')
    expect(firstStepsOf($firstSteps.get(), 'default').done).toEqual(['routine'])
  })

  it('go once every task is done', () => {
    markDone('default', ['delegate', 'store', 'telegram', 'routine', 'bot'])
    opened()
    render(<FirstSteps />)

    expect(screen.queryByTestId('first-steps')).toBeNull()
  })

  it('stay hidden once hidden', () => {
    opened()
    render(<FirstSteps />)
    fireEvent.click(screen.getByTestId('first-steps-hide'))

    expect(screen.queryByTestId('first-steps')).toBeNull()
    expect(JSON.parse(localStorage.getItem('atlas.firstSteps') ?? '{}')).toEqual({
      default: { done: [], hidden: true }
    })
  })

  it('see a session run on an executor and a plugin from outside Atlas as done', () => {
    app.$workbench.set({
      executors: [CLAUDE],
      instances: [{ executor: 'claude-code', instance_id: 'i1' } as InstanceRow]
    })
    app.$plugins.set([{ bundled: true, name: 'voice' } as PluginRow, { bundled: false, name: 'weather' } as PluginRow])
    opened()
    render(<FirstSteps />)

    expect(screen.getByTestId('first-step-delegate').dataset.done).toBe('true')
    expect(screen.getByTestId('first-step-store').dataset.done).toBe('true')
  })

  it('start a routine in a new conversation, its words in the box', () => {
    const start = vi.spyOn(app, 'newConversationWith').mockImplementation(() => undefined)
    opened()
    render(<FirstSteps />)
    fireEvent.click(screen.getByTestId('first-step-routine'))

    expect(start).toHaveBeenCalledWith('Every Monday at 9am, remind me to review what I have for the week.')
  })

  it('open the executors tab to hand a task when one is on, its first-run step when none is', () => {
    const tab = vi.spyOn(app, 'showSidebarTab').mockImplementation(() => undefined)
    opened()
    const { unmount } = render(<FirstSteps />)
    fireEvent.click(screen.getByTestId('first-step-delegate'))

    expect(tab).not.toHaveBeenCalled()
    expect(firstRunOf($firstRun.get(), 'default')).toMatchObject({ only: 'executors', open: true, step: 'executors' })

    unmount()
    $firstRun.set({})
    app.$workbench.set({ executors: [CLAUDE], instances: [] })
    render(<FirstSteps />)
    fireEvent.click(screen.getByTestId('first-step-delegate'))

    expect(tab).toHaveBeenCalledWith('executors')
  })

  it('hide while the first run is open', () => {
    opened()
    openStep('default', 'telegram')
    render(<FirstSteps />)

    expect(screen.queryByTestId('first-steps')).toBeNull()
  })
})

describe('a first-run step opened alone', () => {
  it('closes once done instead of going on, what was done kept', () => {
    finishStep('default', 'model', 'done')
    openStep('default', 'telegram')
    finishStep('default', 'telegram', 'done')

    expect(firstRunOf($firstRun.get(), 'default')).toMatchObject({
      marks: { model: 'done', telegram: 'done' },
      open: false
    })
  })
})
