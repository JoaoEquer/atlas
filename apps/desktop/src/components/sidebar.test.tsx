import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ConversationRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { Sidebar } from './sidebar'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$conversations.set([])
  app.$listMore.set({ cursor: '', error: null, reading: false })
  app.$read.setKey('conversations', false)
})

const rows = (count: number): ConversationRow[] =>
  Array.from({ length: count }, (_, index) => ({
    id: `c${String(index)}`,
    messages: 2,
    running: false,
    source: 'app',
    started_at: '2026-10-27T10:00:00.000Z',
    title: `Topic ${String(index)}`,
    updated_at: '2026-10-27T10:00:00.000Z'
  }))

/** jsdom lays nothing out: the list's height, its window and its place are set by hand. */
function scrollTo(list: HTMLElement, top: number) {
  Object.defineProperty(list, 'scrollHeight', { configurable: true, value: 1600 })
  Object.defineProperty(list, 'clientHeight', { configurable: true, value: 600 })
  list.scrollTop = top
  fireEvent.scroll(list)
}

describe('the list of conversations, a page at a time (step 9e)', () => {
  it('reads the next page as it scrolls near its end, and at once while it is shorter than its height', () => {
    const more = vi.spyOn(app, 'moreConversations').mockResolvedValue()
    app.$read.setKey('conversations', true)
    app.$conversations.set(rows(50))
    app.$listMore.set({ cursor: 'next', error: null, reading: false })

    render(<Sidebar />)
    // Laid out by nothing, the list is shorter than its height: the next page is read at once.
    expect(more).toHaveBeenCalledTimes(1)

    const list = screen.getByTestId('conversation-list')
    scrollTo(list, 300)
    expect(more).toHaveBeenCalledTimes(1)
    scrollTo(list, 800)
    expect(more).toHaveBeenCalledTimes(2)
  })

  it('shows a page being read at its end, and a failed one with a way to read it again', () => {
    const more = vi.spyOn(app, 'moreConversations').mockResolvedValue()
    app.$read.setKey('conversations', true)
    app.$conversations.set(rows(3))
    app.$listMore.set({ cursor: 'next', error: null, reading: true })

    const { rerender } = render(<Sidebar />)
    expect(screen.getByRole('status', { name: 'Reading more conversations…' })).toBeTruthy()

    app.$listMore.set({ cursor: 'next', error: 'the server went away', reading: false })
    rerender(<Sidebar />)
    expect(screen.getByTestId('read-failed').textContent).toContain('the server went away')
    more.mockClear()
    fireEvent.click(screen.getByTestId('read-again'))
    expect(more).toHaveBeenCalledWith(true)
  })
})
