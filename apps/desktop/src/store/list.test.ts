import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AtlasClient } from '@/protocol/client'
import type { ConversationList, ConversationRow, ListParams } from '@/protocol/protocol.generated'

import { current, LIST_PAGE, openHome, startApp } from './app'

/**
 * The list of conversations a page at a time (step 9e), against a server that pages as Atlas's does:
 * the latest activity first, the id breaking a tie, each page after the cursor the one before gave.
 */
class Server {
  rows: ConversationRow[] = []
  calls: ListParams[] = []
  /** Hold the next answers until `release`. */
  held = false
  private waiting: (() => void)[] = []
  failNext = false

  add(id: string, minute: number): void {
    this.rows.push({
      id,
      messages: 2,
      running: false,
      source: 'app',
      started_at: '',
      title: `Topic ${id}`,
      updated_at: at(minute)
    })
  }

  /** A conversation gets a message: its activity is the newest. */
  touch(id: string, minute: number): void {
    this.rows = this.rows.map(row => (row.id === id ? { ...row, updated_at: at(minute) } : row))
  }

  list(params: ListParams): Promise<ConversationList> {
    this.calls.push(params)
    const sorted = [...this.rows].sort((a, b) => key(b).localeCompare(key(a)))
    const { cursor } = params
    const after = cursor ? sorted.filter(row => key(row) < cursor) : sorted
    const limit = params.limit ?? LIST_PAGE
    const page = after.slice(0, limit)
    const last = page.at(-1)
    const answer = { conversations: page, next_cursor: after.length > limit && last ? key(last) : '' }

    if (this.failNext) {
      this.failNext = false

      return Promise.reject(new Error('the server went away'))
    }

    return this.held
      ? new Promise(resolve => {
          this.waiting.push(() => {
            resolve(answer)
          })
        })
      : Promise.resolve(answer)
  }

  release(): void {
    this.held = false

    for (const answer of this.waiting.splice(0)) {
      answer()
    }
  }
}

function at(minute: number): string {
  return `2026-10-27T${String(10 + Math.floor(minute / 60)).padStart(2, '0')}:${String(minute % 60).padStart(2, '0')}:00.000Z`
}

function key(row: ConversationRow): string {
  return `${row.updated_at} ${row.id}`
}

function clientOf(server: Server): AtlasClient {
  const none = () => () => undefined

  return {
    call: (method: string, params: ListParams) =>
      method === 'conversation.list' ? server.list(params) : new Promise(() => undefined),
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

afterEach(() => {
  stop()
})

async function home(server: Server) {
  stop = startApp({ client: () => clientOf(server), shell: { focus: () => undefined, notify: () => undefined } })
  await openHome('work')

  return current()
}

const ids = (rows: ConversationRow[]) => rows.map(row => row.id)

/** `count` conversations a minute apart, `c0` the oldest. */
function seeded(count: number): Server {
  const server = new Server()

  for (let n = 0; n < count; n++) {
    server.add(`c${String(n).padStart(3, '0')}`, n)
  }

  return server
}

const newestFirst = (server: Server) => ids([...server.rows].sort((a, b) => key(b).localeCompare(key(a))))

describe('the list a page at a time (step 9e)', () => {
  it('reads the next page as the list nears its end, each conversation once, and stops when it is whole', async () => {
    const server = seeded(120)
    const session = await home(server)

    await session.refreshList()
    expect(session.$conversations.get()).toHaveLength(50)
    await session.moreConversations()
    await session.moreConversations()
    expect(session.$listMore.get().cursor).toBe('')

    expect(ids(session.$conversations.get())).toEqual(newestFirst(server))
    const asked = server.calls.length
    await session.moreConversations()
    expect(server.calls).toHaveLength(asked)
  })

  it('reads again as many rows as it holds, so activity in the middle neither repeats nor skips one', async () => {
    const server = seeded(70)
    const session = await home(server)
    await session.refreshList()

    // A conversation not read yet gets a message, and a new one begins: both go to the top.
    server.touch('c005', 200)
    server.add('new', 201)
    await session.refreshList()
    expect(server.calls.at(-1)).toEqual({ limit: 50 })
    expect(ids(session.$conversations.get()).slice(0, 2)).toEqual(['new', 'c005'])
    await session.moreConversations()

    expect(session.$listMore.get().cursor).toBe('')
    expect(ids(session.$conversations.get())).toEqual(newestFirst(server))

    // Holding every row, a refresh asks for all of them.
    await session.refreshList()
    expect(server.calls.at(-1)).toEqual({ limit: 71 })
  })

  it('reads a page after the refresh asked before it, never across it', async () => {
    const server = seeded(80)
    const session = await home(server)
    await session.refreshList()

    server.held = true
    server.touch('c010', 300)
    const before = server.calls.length
    const refreshed = session.refreshList()
    const more = session.moreConversations()
    // The page waits for the refresh: read from the old end, it would skip the row the refresh
    // pushed out of it.
    await new Promise(resolve => setTimeout(resolve, 10))
    expect(server.calls.slice(before)).toEqual([{ limit: 50 }])
    server.release()
    await refreshed
    await more

    expect(server.calls.slice(before)).toEqual([{ limit: 50 }, { cursor: expect.any(String) as string, limit: 50 }])
    expect(ids(session.$conversations.get())).toEqual(newestFirst(server))
  })

  it('says a page failed at its end, and only its button reads it again', async () => {
    const server = seeded(60)
    const session = await home(server)
    await session.refreshList()

    server.failNext = true
    await session.moreConversations()
    expect(session.$listMore.get().error).toBe('the server went away')
    const asked = server.calls.length
    await session.moreConversations()
    expect(server.calls).toHaveLength(asked)

    await session.moreConversations(true)
    expect(session.$listMore.get()).toEqual({ cursor: '', error: null, reading: false })
    expect(ids(session.$conversations.get())).toEqual(newestFirst(server))
  })
})
