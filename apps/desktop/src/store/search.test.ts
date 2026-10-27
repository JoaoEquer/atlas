import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { AtlasClient } from '@/protocol/client'
import type { SearchHit, SearchParams, SearchResults } from '@/protocol/protocol.generated'

import { current, openHome, SEARCH_PAUSE_MS, startApp } from './app'

/** A server whose searches wait until the test answers them, each on its own. */
class Server {
  asked: SearchParams[] = []
  private answers: { reject: (error: Error) => void; resolve: (found: SearchResults) => void }[] = []

  search(params: SearchParams): Promise<SearchResults> {
    this.asked.push(params)

    return new Promise((resolve, reject) => {
      this.answers.push({ reject, resolve })
    })
  }

  /** Answer the search asked `index`th with a conversation for each id. */
  answer(index: number, ...ids: string[]): void {
    this.answers[index]?.resolve({ hits: ids.map(found), more: false })
  }

  fail(index: number, message: string): void {
    this.answers[index]?.reject(new Error(message))
  }
}

function found(id: string): SearchHit {
  const title = `Topic ${id}`

  return {
    conversation: { id, messages: 2, running: false, source: 'app', started_at: '', title, updated_at: '' },
    said_by: '',
    snippet: [],
    title: [title]
  }
}

function clientOf(server: Server): AtlasClient {
  const none = () => () => undefined

  return {
    call: (method: string, params: SearchParams) =>
      method === 'conversation.search' ? server.search(params) : new Promise(() => undefined),
    onError: none,
    onEvent: none,
    onRequest: none,
    onResync: none,
    onState: none,
    start: vi.fn(),
    stop: vi.fn(),
    watch: vi.fn()
  } as unknown as AtlasClient
}

let stop: () => void = () => undefined

beforeEach(() => {
  vi.useFakeTimers()
})

afterEach(() => {
  stop()
  vi.useRealTimers()
})

async function home(server: Server) {
  stop = startApp({ client: () => clientOf(server), shell: { focus: () => undefined, notify: () => undefined } })
  await openHome('work')

  return current()
}

const ids = (session: Awaited<ReturnType<typeof home>>) => session.$search.get().hits.map(hit => hit.conversation.id)

describe("the list's search (step 9e)", () => {
  it('searches once the typing pauses, for the latest words only', async () => {
    const server = new Server()
    const session = await home(server)

    session.typeSearch('q')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS / 2)
    session.typeSearch('quo')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS / 2)
    session.typeSearch('quok ')
    expect(server.asked).toEqual([])
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS)

    expect(server.asked).toEqual([{ limit: 50, query: 'quok' }])
    expect(session.$search.get().text).toBe('quok ')
  })

  it('shows what the latest words found, never an older answer that came after it', async () => {
    const server = new Server()
    const session = await home(server)
    session.typeSearch('alpha')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS)
    session.typeSearch('alpha beta')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS)

    server.answer(1, 'both')
    await vi.advanceTimersByTimeAsync(0)
    server.answer(0, 'alpha only', 'both')
    await vi.advanceTimersByTimeAsync(0)

    expect(ids(session)).toEqual(['both'])
    expect(session.$search.get()).toMatchObject({ query: 'alpha beta', reading: false })
  })

  it('Esc ends the search at once, and an answer still on its way is dropped', async () => {
    const server = new Server()
    const session = await home(server)
    session.typeSearch('alpha')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS)

    session.clearSearch()
    server.answer(0, 'alpha')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS)

    expect(session.$search.get()).toMatchObject({ error: null, hits: [], query: '', reading: false, text: '' })
  })

  it('a search that failed says why, and its button searches again', async () => {
    const server = new Server()
    const session = await home(server)
    session.typeSearch('alpha')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS)
    server.fail(0, 'the server went away')
    await vi.advanceTimersByTimeAsync(0)
    expect(session.$search.get().error).toBe('the server went away')

    session.searchAgain()
    await vi.advanceTimersByTimeAsync(0)
    expect(server.asked).toEqual([
      { limit: 50, query: 'alpha' },
      { limit: 50, query: 'alpha' }
    ])
    server.answer(1, 'alpha')
    await vi.advanceTimersByTimeAsync(0)

    expect(session.$search.get()).toMatchObject({ error: null, reading: false })
    expect(ids(session)).toEqual(['alpha'])
  })

  it('blank words search nothing', async () => {
    const server = new Server()
    const session = await home(server)

    session.typeSearch('   ')
    await vi.advanceTimersByTimeAsync(SEARCH_PAUSE_MS)

    expect(server.asked).toEqual([])
    expect(session.$search.get()).toMatchObject({ hits: [], text: '   ' })
  })
})
