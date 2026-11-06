import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'

import * as app from '@/store/app'
import { EMPTY_THREAD } from '@/store/thread'

import { StartScreen } from './start-screen'
import { ThreadView } from './thread-view'

beforeAll(() => {
  Element.prototype.scrollIntoView = () => undefined
})

afterEach(() => {
  cleanup()
  app.$bots.set([])
  app.$conversations.set([])
})

describe('the start screen', () => {
  it('shows Atlas, the bots and the executors side by side, each named in characters, with its list', () => {
    app.$conversations.set([
      {
        archived_at: '',
        id: 'c1',
        messages: 2,
        running: false,
        source: 'app',
        started_at: '2026-11-01T10:00:00Z',
        title: 'Plan the trip',
        updated_at: '2026-11-01T10:00:00Z'
      }
    ])
    render(<StartScreen />)

    const cards = ['start-atlas', 'start-bots', 'start-executors'].map(id => screen.getByTestId(id))
    expect(cards.map(card => within(card).getByTestId('wordmark').dataset.word)).toEqual(['Atlas', 'Bots', 'Executors'])
    expect(within(cards[0] as HTMLElement).getByTestId('new-conversation')).toBeTruthy()
    expect(within(cards[0] as HTMLElement).getByText('Plan the trip')).toBeTruthy()
    expect(within(cards[1] as HTMLElement).getByTestId('new-bot')).toBeTruthy()
  })
})

describe('a conversation with Atlas, empty', () => {
  it("opens on Atlas's name in characters and the invitation in words", () => {
    render(<ThreadView thread={EMPTY_THREAD} />)

    expect(within(screen.getByTestId('invitation')).getByTestId('wordmark').dataset.word).toBe('Atlas')
    expect(screen.getByText('Ask Atlas anything.')).toBeTruthy()
  })
})
