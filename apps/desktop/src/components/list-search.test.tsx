import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { SearchHit } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { Sidebar } from './sidebar'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$search.set(app.NO_SEARCH)
  app.$conversations.set([])
  app.$read.setKey('conversations', false)
})

function hit(id: string, title: string[], snippet: string[] = [], saidBy = ''): SearchHit {
  return {
    conversation: {
      id,
      messages: 2,
      running: false,
      source: 'app',
      started_at: '2026-10-27T10:00:00.000Z',
      title: title.join(''),
      updated_at: '2026-10-27T10:00:00.000Z'
    },
    said_by: saidBy,
    snippet,
    title
  }
}

const field = () => screen.getByRole('textbox', { name: 'Search conversations' })

describe("the list's search (step 9e)", () => {
  it('searches what is typed, shows what it found in place of the list, and Esc ends it', () => {
    const typed = vi.spyOn(app, 'typeSearch').mockReturnValue()
    const cleared = vi.spyOn(app, 'clearSearch').mockReturnValue()
    app.$read.setKey('conversations', true)
    render(<Sidebar />)
    const list = screen.getByTestId('conversation-list')
    expect(list.hidden).toBe(false)

    fireEvent.change(field(), { target: { value: 'quokka' } })
    expect(typed).toHaveBeenCalledWith('quokka')

    act(() => {
      app.$search.set({ ...app.NO_SEARCH, query: 'quokka', text: 'quokka' })
    })
    // Hidden, not gone: it comes back where it was.
    expect(list.hidden).toBe(true)
    expect(screen.getByTestId('search-status').textContent).toBe('No conversation holds “quokka”.')

    fireEvent.keyDown(field(), { key: 'Escape' })
    expect(cleared).toHaveBeenCalled()
  })

  it('says where each conversation matched, and a click or Enter opens it', () => {
    const opened = vi.spyOn(app, 'selectConversation').mockResolvedValue()
    app.$search.set({
      ...app.NO_SEARCH,
      hits: [
        hit('named', ['', 'Quokka', ' facts']),
        hit('walk', ['How was the walk?'], ['Then a ', 'quokka', ' crossed the trail.'], 'assistant')
      ],
      query: 'quok',
      text: 'quok'
    })
    render(<Sidebar />)

    expect(screen.getByTestId('search-status').textContent).toBe('2 conversations')
    const hits = screen.getAllByTestId('search-hit')
    expect(hits).toHaveLength(2)
    const [byTitle, byMessage] = hits as [HTMLElement, HTMLElement]
    expect(byTitle.querySelector('mark')?.textContent).toBe('Quokka')
    expect(byTitle.querySelector('[data-testid="search-hit-snippet"]')).toBeNull()
    expect(byMessage.querySelector('[data-testid="search-hit-snippet"]')?.textContent).toBe(
      'Atlas: Then a quokka crossed the trail.'
    )
    expect(byMessage.querySelector('mark')?.textContent).toBe('quokka')

    fireEvent.click(byMessage)
    expect(opened).toHaveBeenCalledWith('walk')
    fireEvent.keyDown(field(), { key: 'Enter' })
    expect(opened).toHaveBeenLastCalledWith('named')
  })

  it('says when the first matches are not all, and why a search failed', () => {
    const again = vi.spyOn(app, 'searchAgain').mockReturnValue()
    app.$search.set({ ...app.NO_SEARCH, hits: [hit('a', ['Garden'])], more: true, query: 'garden', text: 'garden' })
    render(<Sidebar />)
    expect(screen.getByTestId('search-status').textContent).toBe('Showing the first 1. Add a word to narrow them.')

    act(() => {
      app.$search.set({ ...app.NO_SEARCH, error: 'the server went away', query: 'garden', text: 'garden' })
    })
    expect(screen.getByTestId('read-failed').textContent).toContain('the server went away')
    fireEvent.click(screen.getByTestId('read-again'))
    expect(again).toHaveBeenCalled()
  })
})
