import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import type { ApprovalParams } from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import type { Tab } from '@/store/tabs'

import { TabStrip } from './tab-strip'

beforeAll(() => {
  Element.prototype.scrollIntoView = () => undefined
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$tabs.set([])
  app.$cards.set([])
  app.$selected.set(null)
  app.$view.set('conversation')
  app.$connection.set({ error: null, state: 'idle' })
})

const tab = (id: string, title: string, preview = false): Tab => ({
  id,
  place: { conversationId: id, kind: 'conversation' },
  preview,
  title
})

const approval = (conversationId: string): app.Card => ({
  conversationId,
  id: `srq-${conversationId}`,
  kind: 'approval',
  params: {
    allow_always: true,
    allow_conversation: true,
    call_id: 'call',
    conversation_id: conversationId,
    guardian_denied: false,
    reason: 'deletes files',
    target: 'rm -r build',
    tool: 'run_command'
  } satisfies ApprovalParams
})

describe('the tabs above the central area', () => {
  it('moves a tab dropped before the frame that marks where it lands (found by the CI on 03/11/2026)', () => {
    app.$tabs.set([tab('a', 'Groceries'), tab('b', 'Trip')])
    const moved = vi.spyOn(app, 'moveTab').mockImplementation(() => undefined)
    render(<TabStrip />)
    const [, last] = screen.getAllByTestId('tab') as [HTMLElement, HTMLElement]
    const carried = new Map<string, string>([['application/x-atlas-tab', 'a']])
    const data = {
      effectAllowed: 'move',
      getData: (kind: string) => carried.get(kind) ?? '',
      setData: (kind: string, value: string) => carried.set(kind, value),
      types: [...carried.keys()]
    }

    // A quick hand: the first tab over the last one's right half (jsdom draws it with no width), and
    // dropped, with no render between.
    act(() => {
      fireEvent.dragOver(last, { dataTransfer: data })
      fireEvent.drop(last, { dataTransfer: data })
    })

    expect(moved).toHaveBeenCalledWith('a', 1)
  })

  it('show each tab, the passing one in italic and the one on screen chosen, and a new conversation not made yet', () => {
    app.$tabs.set([tab('a', 'Groceries'), tab('b', 'Trip', true)])
    app.$selected.set('b')
    app.$connection.set({ error: null, state: 'open' })
    render(<TabStrip />)

    expect(screen.getAllByTestId('tab-title').map(title => title.textContent)).toEqual(['Groceries', 'Trip'])
    expect(screen.getByRole('tab', { selected: true }).textContent).toBe('Trip')
    expect(screen.getAllByTestId('tab-title')[1]?.className).toContain('italic')
    expect(screen.queryByTestId('tab-draft')).toBeNull()

    act(() => {
      app.$selected.set(null)
    })
    expect(screen.getByRole('tab', { selected: true }).textContent).toBe('New conversation')
  })

  it('show a tab by Ctrl and its number, the last by Ctrl+9, close the one on screen by Ctrl+W, and leave the terminal its keys', () => {
    const shown = vi.spyOn(app, 'showTab').mockResolvedValue(undefined)
    const closed = vi.spyOn(app, 'closeTab').mockImplementation(() => undefined)
    const tabs = [tab('a', 'Groceries'), tab('b', 'Trip'), tab('c', 'Bike')]
    vi.spyOn(app, 'activeTab').mockReturnValue(tabs[0] ?? null)
    app.$tabs.set(tabs)
    render(<TabStrip />)

    fireEvent.keyDown(window, { code: 'Digit2', ctrlKey: true, key: '2' })
    fireEvent.keyDown(window, { code: 'Digit9', ctrlKey: true, key: '9' })
    fireEvent.keyDown(window, { code: 'Tab', ctrlKey: true, key: 'Tab' })
    expect(shown.mock.calls).toEqual([['b'], ['c'], ['b']])

    fireEvent.keyDown(window, { code: 'KeyW', ctrlKey: true, key: 'w' })
    expect(closed).toHaveBeenCalledWith('a')

    const terminal = document.createElement('div')
    terminal.className = 'xterm'
    document.body.append(terminal)
    fireEvent.keyDown(terminal, { code: 'KeyW', ctrlKey: true, key: 'w' })
    expect(closed).toHaveBeenCalledTimes(1)
    terminal.remove()
  })

  it("close the tab on screen on macOS's ⌘W, which the main process hands over, else the window", () => {
    const closed = vi.spyOn(app, 'closeTab').mockImplementation(() => undefined)
    const quit = vi.spyOn(window, 'close').mockImplementation(() => undefined)
    let heard: () => void = () => undefined
    Object.assign(window, {
      atlas: {
        tabs: {
          onClose: (listener: () => void) => {
            heard = listener

            return () => undefined
          }
        }
      }
    })
    const tabs = [tab('a', 'Groceries')]
    const active = vi.spyOn(app, 'activeTab').mockReturnValue(tabs[0] ?? null)
    app.$tabs.set(tabs)
    render(<TabStrip />)

    heard()
    expect(closed).toHaveBeenCalledWith('a')
    active.mockReturnValue(null)
    heard()
    expect(quit).toHaveBeenCalled()
    Reflect.deleteProperty(window, 'atlas')
  })

  it('close a tab by its button or a middle click, and keep the passing one by a double click', () => {
    const closed = vi.spyOn(app, 'closeTab').mockImplementation(() => undefined)
    const kept = vi.spyOn(app, 'keepTab').mockImplementation(() => undefined)
    app.$tabs.set([tab('a', 'Groceries'), tab('b', 'Trip', true)])
    render(<TabStrip />)

    fireEvent.click(screen.getByRole('button', { name: 'Close Groceries' }))
    screen.getAllByTestId('tab')[1]?.dispatchEvent(new MouseEvent('auxclick', { bubbles: true, button: 1 }))
    fireEvent.doubleClick(screen.getByRole('tab', { name: 'Trip' }))

    expect(closed.mock.calls).toEqual([['a'], ['b']])
    expect(kept).toHaveBeenCalledWith('b')
  })

  it('say how many cards wait away from the screen, and go to the first', () => {
    const opened = vi.spyOn(app, 'openWaiting').mockResolvedValue(undefined)
    app.$tabs.set([tab('a', 'Groceries'), tab('b', 'Trip')])
    app.$selected.set('a')
    app.$cards.set([approval('a'), approval('b'), approval('c')])
    render(<TabStrip />)

    fireEvent.click(screen.getByTestId('tabs-waiting'))

    expect(screen.getByTestId('tabs-waiting').textContent).toBe('2 waiting for you')
    expect(opened).toHaveBeenCalled()
  })

  it('open the start screen on the new tab button, its tab named a new tab until something is chosen', () => {
    const started = vi.spyOn(app, 'showStart').mockImplementation(() => {
      app.$view.set('start')
    })
    app.$tabs.set([tab('a', 'Groceries')])
    app.$selected.set('a')
    app.$connection.set({ error: null, state: 'open' })
    render(<TabStrip />)

    fireEvent.click(screen.getByTestId('tab-new'))

    expect(started).toHaveBeenCalled()
    expect(screen.getByTestId('tab-draft').textContent).toBe('New tab')
  })
})
