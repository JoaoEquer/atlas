import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import * as app from '@/store/app'

import { Sidebar } from './sidebar'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$conversations.set([])
  app.$read.setKey('conversations', false)
})

function listed(...titles: string[]): void {
  app.$read.setKey('conversations', true)
  app.$conversations.set(
    titles.map((title, index) => ({
      id: `c${String(index)}`,
      messages: 2,
      running: false,
      source: 'app',
      started_at: '2026-10-27T10:00:00.000Z',
      title,
      updated_at: '2026-10-27T10:00:00.000Z'
    }))
  )
  render(<Sidebar />)
}

const firstRow = () => screen.getAllByTestId('conversation-row')[0] as HTMLElement
const nameField = () => screen.getByRole<HTMLInputElement>('textbox', { name: 'New name for plan the trip' })

describe('renaming a conversation in place (step 9e)', () => {
  it('F2 on its row opens its name, Enter keeps the new one and Esc the old', async () => {
    const renamed = vi.spyOn(app, 'renameConversation').mockResolvedValue()
    listed('plan the trip', 'other')

    fireEvent.keyDown(firstRow(), { key: 'F2' })
    expect(nameField().value).toBe('plan the trip')
    fireEvent.change(nameField(), { target: { value: 'Trip to Lisbon' } })
    fireEvent.keyDown(nameField(), { key: 'Enter' })
    expect(renamed).toHaveBeenCalledWith('c0', 'Trip to Lisbon')
    await vi.waitFor(() => {
      expect(screen.queryByTestId('conversation-name-field')).toBeNull()
    })
    // The row takes the focus back, for the keyboard to go on.
    expect(document.activeElement).toBe(firstRow())

    fireEvent.keyDown(firstRow(), { key: 'F2' })
    fireEvent.change(nameField(), { target: { value: 'Not this one' } })
    fireEvent.keyDown(nameField(), { key: 'Escape' })
    expect(screen.queryByTestId('conversation-name-field')).toBeNull()
    expect(renamed).toHaveBeenCalledTimes(1)
  })

  it("its row's menu renames it too, and a refusal keeps the field open, saying why", async () => {
    vi.spyOn(app, 'renameConversation').mockRejectedValue(new Error('the server went away'))
    listed('plan the trip')

    fireEvent.keyDown(screen.getByTestId('conversation-menu'), { key: 'Enter' })
    fireEvent.click(within(screen.getByTestId('conversation-menu-content')).getByTestId('conversation-rename'))
    fireEvent.change(nameField(), { target: { value: 'Trip' } })
    fireEvent.keyDown(nameField(), { key: 'Enter' })

    expect(await screen.findByText('Not renamed: the server went away')).toBeTruthy()
    expect(nameField().value).toBe('Trip')
  })

  it('a right click on a row opens its menu', () => {
    listed('plan the trip')

    fireEvent.contextMenu(firstRow())

    expect(screen.getByTestId('conversation-menu-content')).toBeTruthy()
  })

  it('a double click still keeps its tab, and renames nothing', () => {
    const kept = vi.spyOn(app, 'keepConversation').mockReturnValue()
    listed('plan the trip')

    fireEvent.doubleClick(firstRow())

    expect(kept).toHaveBeenCalledWith('c0')
    expect(screen.queryByTestId('conversation-name-field')).toBeNull()
  })
})
