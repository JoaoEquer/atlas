import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { $contributions, contribute, insertIntoBox } from '@/plugins/registry'
import { BoxActions } from '@/plugins/view'
import type { SlashCommandRow } from '@/protocol/protocol.generated'

import { MessageBox } from './message-box'

const COMMANDS: SlashCommandRow[] = [
  { description: 'Review a pull request', group: 'skill', hint: '[pr]', name: 'review' },
  { description: 'Clear the history but keep a summary', group: 'command', hint: '', name: 'compact' },
  { description: 'Show what fills the context', group: 'command', hint: '', name: 'context' }
]

afterEach(cleanup)

function box(onSend = vi.fn(() => Promise.resolve(true)), onSlash?: (name: string, rest: string) => boolean) {
  render(
    <MessageBox
      commands={COMMANDS}
      connected
      label="Message"
      onSend={onSend}
      onSlash={onSlash}
      placeholder="Message"
      testId="box"
      toolbar={null}
    />
  )

  return screen.getByTestId<HTMLTextAreaElement>('box')
}

const shown = () =>
  screen
    .getAllByTestId('slash-command')
    .map(row => [row.getAttribute('data-command'), row.getAttribute('aria-selected')])

describe("the box's / list", () => {
  it('shows the commands described, in their groups, and the arrows and Enter take one', () => {
    const onSend = vi.fn(() => Promise.resolve(true))
    const text = box(onSend)

    fireEvent.change(text, { target: { value: '/' } })
    const list = screen.getByTestId('slash-commands')
    expect([...list.querySelectorAll('[role=group]')].map(group => group.getAttribute('data-group'))).toEqual([
      'command',
      'skill'
    ])
    expect(list.textContent).toContain('Review a pull request')
    expect(list.textContent).toContain('[pr]')
    expect(shown()).toEqual([
      ['compact', 'true'],
      ['context', 'false'],
      ['review', 'false']
    ])

    fireEvent.keyDown(text, { key: 'ArrowDown' })
    fireEvent.keyDown(text, { key: 'ArrowDown' })
    expect(shown()[2]).toEqual(['review', 'true'])
    fireEvent.keyDown(text, { key: 'ArrowDown' })
    expect(shown()[0]).toEqual(['compact', 'true'])
    fireEvent.keyDown(text, { key: 'ArrowUp' })
    fireEvent.keyDown(text, { key: 'Enter' })

    expect(text.value).toBe('/review ')
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.queryByTestId('slash-commands')).toBeNull()
  })

  it('narrows as the name is typed, Tab takes it, and Escape hides the list until the text changes', () => {
    const text = box()

    fireEvent.change(text, { target: { value: '/con' } })
    expect(shown().map(([name]) => name)).toEqual(['context'])
    fireEvent.keyDown(text, { key: 'Escape' })
    expect(screen.queryByTestId('slash-commands')).toBeNull()
    fireEvent.change(text, { target: { value: '/co' } })
    expect(shown().map(([name]) => name)).toEqual(['compact', 'context'])
    fireEvent.keyDown(text, { key: 'Tab' })
    expect(text.value).toBe('/compact ')
  })

  it('runs on the screen a command the screen takes, and sends any other', () => {
    const onSend = vi.fn(() => Promise.resolve(true))
    const onSlash = vi.fn((name: string) => name === 'new')
    const text = box(onSend, onSlash)

    fireEvent.change(text, { target: { value: '/new' } })
    fireEvent.keyDown(text, { key: 'Escape' })
    fireEvent.keyDown(text, { key: 'Enter' })
    expect(onSlash).toHaveBeenCalledWith('new', '')
    expect(onSend).not.toHaveBeenCalled()
    expect(text.value).toBe('')

    fireEvent.change(text, { target: { value: '/review 42' } })
    fireEvent.keyDown(text, { key: 'Enter' })
    expect(onSlash).toHaveBeenLastCalledWith('review', '42')
    expect(onSend).toHaveBeenCalledWith('/review 42', [])
  })
})

describe("the box's plugin buttons (screen version 2)", () => {
  it('show beside the words, and what a screen dictates lands after what the box holds', () => {
    contribute('voice', {
      area: 'box',
      id: 'dictate',
      render: () => <button type="button">🎤</button>,
      title: 'Dictate'
    })
    render(
      <MessageBox
        actions={<BoxActions />}
        connected
        label="Message"
        onSend={vi.fn(() => Promise.resolve(true))}
        placeholder="Message"
        testId="box"
        toolbar={null}
      />
    )
    const text = screen.getByTestId<HTMLTextAreaElement>('box')

    expect(screen.getByTestId('box-actions').textContent).toBe('🎤')

    fireEvent.change(text, { target: { value: 'Remind me' } })
    act(() => {
      insertIntoBox(' to call the dentist ')
    })
    expect(text.value).toBe('Remind me to call the dentist')

    act(() => {
      insertIntoBox('on Thursday')
    })
    expect(text.value).toBe('Remind me to call the dentist on Thursday')
    $contributions.set([])
  })

  it('leave a box that takes no buttons, an executor session’s, as it is', () => {
    const text = box()
    act(() => {
      insertIntoBox('dictated elsewhere')
    })

    expect(text.value).toBe('')
  })
})

describe("each conversation's words (the UI review, 25/10/2026)", () => {
  function owned(draftKey: string, onSend = vi.fn(() => Promise.resolve(true))) {
    return (
      <MessageBox
        connected
        draftKey={draftKey}
        label="Message"
        onSend={onSend}
        placeholder="Message"
        testId="box"
        toolbar={null}
      />
    )
  }

  it('stay in their conversation when another comes on screen, and wait there', () => {
    const { rerender } = render(owned('c-words-a'))
    fireEvent.change(screen.getByTestId('box'), { target: { value: 'For the first one' } })

    rerender(owned('c-words-b'))
    expect(screen.getByTestId<HTMLTextAreaElement>('box').value).toBe('')
    rerender(owned('c-words-a'))
    expect(screen.getByTestId<HTMLTextAreaElement>('box').value).toBe('For the first one')
  })

  it('that did not reach the server come back to the conversation they left', async () => {
    let refuse: (sent: boolean) => void = () => undefined
    const onSend = vi.fn(() => new Promise<boolean>(resolve => (refuse = resolve)))
    const { rerender } = render(owned('c-back-a', onSend))
    const text = screen.getByTestId('box')
    fireEvent.change(text, { target: { value: 'Lost on the way' } })
    fireEvent.keyDown(text, { key: 'Enter' })
    expect(onSend).toHaveBeenCalled()

    rerender(owned('c-back-b', onSend))
    await act(async () => {
      refuse(false)
      await Promise.resolve()
    })
    expect(screen.getByTestId<HTMLTextAreaElement>('box').value).toBe('')
    rerender(owned('c-back-a', onSend))
    expect(screen.getByTestId<HTMLTextAreaElement>('box').value).toBe('Lost on the way')
  })
})
