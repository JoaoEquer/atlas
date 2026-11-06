import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ModeMenu } from './mode-menu'

const MODES = ['default', 'acceptEdits', 'plan', 'auto']

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('the mode menu', () => {
  it('names the mode the session runs in', () => {
    render(<ModeMenu current="plan" disabled={false} modes={MODES} onPick={vi.fn()} />)

    expect(screen.getByTestId('mode-select').textContent).toBe('Plan')
  })

  it('lists each mode with what it does, the default marked, and picks one by its number', () => {
    const pick = vi.fn()
    render(<ModeMenu current="default" disabled={false} modes={MODES} onPick={pick} />)
    fireEvent.keyDown(screen.getByTestId('mode-select'), { key: 'Enter' })

    const options = screen.getAllByTestId('mode-option')
    expect(options.map(option => option.textContent)).toEqual([
      'ManualDefaultAlways asks before it changes anything',
      'Accept editsTakes file edits without asking2',
      'PlanMakes a plan before it changes anything3',
      'AutomaticIts own classifier decides what it may do4'
    ])
    expect(options[0]?.dataset.current).toBe('true')

    fireEvent.keyDown(screen.getByTestId('mode-menu'), { key: '3' })
    expect(pick).toHaveBeenCalledWith('plan')
  })
})
