import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ConversationRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { Sidebar } from './sidebar'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$conversations.set([])
  app.$archived.set(app.NO_ARCHIVED)
  app.$live.set({})
  app.$read.setKey('conversations', false)
})

function row(id: string, title: string): ConversationRow {
  return {
    archived_at: '',
    id,
    messages: 2,
    running: false,
    source: 'app',
    started_at: '2026-10-27T10:00:00.000Z',
    title,
    updated_at: '2026-10-27T10:00:00.000Z'
  }
}

function listed(...titles: string[]): void {
  app.$read.setKey('conversations', true)
  app.$conversations.set(titles.map((title, index) => row(`c${String(index)}`, title)))
  render(<Sidebar />)
}

function openMenu(): HTMLElement {
  fireEvent.keyDown(screen.getAllByTestId('conversation-menu')[0] as HTMLElement, { key: 'Enter' })

  return screen.getByTestId('conversation-menu-content')
}

describe('archiving a conversation from its row (step 9e)', () => {
  it("its menu's Archive archives it, and a refusal says why under the row", async () => {
    const archived = vi.spyOn(app, 'archiveConversation').mockRejectedValue(new Error('the server went away'))
    listed('plan the trip')

    fireEvent.click(within(openMenu()).getByTestId('conversation-archive'))

    expect(archived).toHaveBeenCalledWith('c0')
    expect(await screen.findByText('Not archived: the server went away')).toBeTruthy()
  })

  it('a conversation whose turn runs is not archived: the item waits for the turn to end', () => {
    const archived = vi.spyOn(app, 'archiveConversation').mockResolvedValue()
    app.$live.set({ c0: true })
    listed('plan the trip')

    const item = within(openMenu()).getByTestId('conversation-archive')
    fireEvent.click(item)

    expect(item.getAttribute('aria-disabled')).toBe('true')
    expect(archived).not.toHaveBeenCalled()
  })
})

describe('the Archived section (step 9e)', () => {
  it('is not there while nothing is archived', () => {
    listed('plan the trip')

    expect(screen.queryByTestId('archived-toggle')).toBeNull()
  })

  it('says how many there are, opens on them, and brings one back from its button', () => {
    const toggled = vi.spyOn(app, 'toggleArchived').mockReturnValue()
    const brought = vi.spyOn(app, 'unarchiveConversation').mockResolvedValue()
    const opened = vi.spyOn(app, 'selectConversation').mockResolvedValue()
    app.$archived.set({ ...app.NO_ARCHIVED, count: 2 })
    listed('plan the trip')

    const toggle = screen.getByTestId('archived-toggle')
    expect(toggle.textContent).toBe('Archived (2)')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(toggled).toHaveBeenCalled()

    act(() => {
      app.$archived.set({
        ...app.NO_ARCHIVED,
        count: 2,
        open: true,
        rows: [row('a1', 'old notes'), row('a2', 'older')]
      })
    })
    expect(screen.getAllByTestId('archived-row').map(each => each.textContent)).toEqual([
      expect.stringContaining('old notes'),
      expect.stringContaining('older')
    ])
    fireEvent.click(screen.getAllByTestId('archived-row')[0] as HTMLElement)
    expect(opened).toHaveBeenCalledWith('a1')
    fireEvent.click(screen.getByRole('button', { name: 'Bring older back to the list' }))
    expect(brought).toHaveBeenCalledWith('a2')
  })

  it('reads the next page from its button, and a failed read again from its own', () => {
    const read = vi.spyOn(app, 'readArchived').mockResolvedValue()
    app.$archived.set({ ...app.NO_ARCHIVED, count: 60, cursor: 'next', open: true, rows: [row('a1', 'old notes')] })
    listed('plan the trip')

    fireEvent.click(screen.getByTestId('archived-more'))
    expect(read).toHaveBeenCalledWith(true)

    act(() => {
      app.$archived.set({ ...app.$archived.get(), error: 'the server went away' })
    })
    expect(screen.queryByTestId('archived-more')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /read again/i }))
    expect(read).toHaveBeenLastCalledWith()
  })
})
