import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkspaceDiff, WorkspaceRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import * as workspaces from '@/store/workspaces'

import { ChangesView } from './changes-view'

const WORKSPACE = {
  base: 'main',
  branch: 'atlas/cart',
  folder: '/home/worktrees/shop-1a2b3c/cart',
  id: 'w-cart',
  name: 'cart'
} as WorkspaceRow

const DIFF: WorkspaceDiff = {
  files: [
    {
      added: 1,
      binary: false,
      lines: [
        { kind: 'same', new: 11, old: 11, text: 'def total(cart):' },
        { kind: 'minus', new: 0, old: 12, text: '    return sum(cart)' },
        { kind: 'plus', new: 12, old: 0, text: '    return round(sum(cart), 2)' }
      ],
      path: 'shop/cart.py',
      removed: 1,
      status: 'modified',
      truncated: false
    },
    { added: 0, binary: true, lines: [], path: 'logo.png', removed: 0, status: 'added', truncated: false }
  ],
  more: false
}

beforeEach(() => {
  vi.spyOn(workspaces, 'readDiff').mockResolvedValue(DIFF)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  workspaces.$reviewComments.set({})
})

describe("a workspace's changes", () => {
  it('lists each file changed, numbered as the file has it', async () => {
    render(<ChangesView workspace={WORKSPACE} />)

    const files = await screen.findAllByTestId('changed-file')
    expect(files.map(file => file.dataset.path)).toEqual(['shop/cart.py', 'logo.png'])
    expect(screen.getByTestId('changes').textContent).toContain('2 files changed since main')
    const lines = screen.getAllByTestId('diff-line')
    expect(lines.map(line => [line.dataset.kind, line.dataset.line])).toEqual([
      ['same', '11'],
      ['minus', '12'],
      ['plus', '12']
    ])
    expect(screen.getByText('A binary file.')).toBeTruthy()
  })

  it('gathers comments on lines and sends them to the session as one message', async () => {
    const sent = vi.spyOn(app, 'sendToSession').mockResolvedValue(true)
    render(<ChangesView workspace={WORKSPACE} />)

    const [, removed, added] = await screen.findAllByTestId('diff-line')
    for (const [line, text] of [
      [added, 'Round in the view, not here.'],
      [removed, 'Keep this one.']
    ] as const) {
      if (line) {
        fireEvent.click(line)
      }
      fireEvent.change(screen.getByTestId('comment-text'), { target: { value: text } })
      fireEvent.click(screen.getByTestId('comment-add'))
    }

    expect(screen.getAllByTestId('review-comment')).toHaveLength(2)
    fireEvent.click(screen.getByTestId('review-send'))

    await waitFor(() => {
      expect(screen.queryByTestId('review-comments')).toBeNull()
    })
    expect(sent).toHaveBeenCalledWith(
      'Review comments on your changes:\n\n' +
        'shop/cart.py:12\n> return round(sum(cart), 2)\nRound in the view, not here.\n\n' +
        'shop/cart.py:12 (a line removed)\n> return sum(cart)\nKeep this one.'
    )
  })

  it('keeps the comments when they could not be sent', async () => {
    vi.spyOn(app, 'sendToSession').mockResolvedValue(false)
    render(<ChangesView workspace={WORKSPACE} />)

    const [first] = await screen.findAllByTestId('diff-line')
    if (first) {
      fireEvent.click(first)
    }
    fireEvent.change(screen.getByTestId('comment-text'), { target: { value: 'Why?' } })
    fireEvent.keyDown(screen.getByTestId('comment-text'), { ctrlKey: true, key: 'Enter' })
    fireEvent.click(screen.getByTestId('review-send'))

    await waitFor(() => {
      expect(screen.getByTestId('review-send')).toHaveProperty('disabled', false)
    })
    expect(screen.getAllByTestId('review-comment')).toHaveLength(1)
  })
})
