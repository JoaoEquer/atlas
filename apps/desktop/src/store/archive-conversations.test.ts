import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import type { AtlasClient } from '@/protocol/client'
import type { ConversationList, ConversationRow, ListParams } from '@/protocol/protocol.generated'

import { current, type HomeSession, openHome, startApp } from './app'

function row(id: string, title: string): ConversationRow {
  return {
    archived_at: '',
    id,
    messages: 2,
    running: false,
    source: 'app',
    started_at: '',
    title,
    updated_at: ''
  }
}

let stop: () => void = () => undefined

afterEach(() => {
  stop()
})

/** A home whose server answers each call with `answer`, and the calls it got. */
async function home(answer: (method: string, params: unknown) => Promise<unknown>) {
  let hear: (event: EventParams) => void = () => undefined
  const calls: [string, unknown][] = []
  const none = () => () => undefined
  const client = {
    call: (method: string, params: unknown) => {
      calls.push([method, params])

      return answer(method, params)
    },
    onError: none,
    onEvent: (handler: (event: EventParams) => void) => {
      hear = handler

      return () => undefined
    },
    onRequest: none,
    onResync: none,
    onState: none,
    start: vi.fn(),
    stop: vi.fn(),
    watch: vi.fn()
  } as unknown as AtlasClient
  stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
  await openHome('work')
  const session = current()
  session.$conversations.set([row('c1', 'plan the trip'), row('c2', 'fix the bike')])
  session.$archived.set({ ...session.$archived.get(), count: 1 })

  return {
    calls,
    hear: (event: EventParams) => {
      hear(event)
    },
    session
  }
}

const listed = (session: HomeSession) => session.$conversations.get().map(open => open.title)
const shelved = (session: HomeSession) => session.$archived.get().rows.map(open => open.title)

/** The server's list, and its archived ones when asked for them. */
function lists(archived: ConversationRow[]) {
  return (method: string, params: unknown): Promise<unknown> => {
    if (method === 'conversation.list') {
      const asked = params as ListParams
      const answer: ConversationList = {
        archived: archived.length,
        // A copy, as a socket hands it: the store never holds the server's own rows.
        conversations: asked.archived ? [...archived] : [row('c2', 'fix the bike')],
        next_cursor: ''
      }

      return Promise.resolve(answer)
    }

    if (method === 'conversation.archive') {
      return Promise.resolve({ archived_at: '2026-10-27T10:00:00.000Z' })
    }

    return method === 'conversation.unarchive' ? Promise.resolve({}) : new Promise(() => undefined)
  }
}

describe('archiving a conversation (step 9e)', () => {
  it('takes it out of the list at once, and the archived ones count it', async () => {
    const { session } = await home(lists([]))

    await session.archiveConversation('c1')

    expect(listed(session)).toEqual(['fix the bike'])
    expect(session.$archived.get().count).toBe(2)
  })

  it('a refusal leaves it in the list, and says why to whoever archived it', async () => {
    const { session } = await home(() => Promise.reject(new Error('Its turn runs.')))

    await expect(session.archiveConversation('c1')).rejects.toThrow('Its turn runs.')

    expect(listed(session)).toEqual(['plan the trip', 'fix the bike'])
    expect(session.$archived.get().count).toBe(1)
  })

  it('the Archived section reads the archived ones as it opens, and again as anything changes', async () => {
    const shelf = [row('c3', 'old notes')]
    const { calls, hear, session } = await home(lists(shelf))

    session.toggleArchived()
    await vi.waitFor(() => {
      expect(shelved(session)).toEqual(['old notes'])
    })
    expect(calls).toContainEqual(['conversation.list', { archived: true, limit: 50 }])

    shelf.push(row('c4', 'older notes'))
    hear({ payload: { conversation_id: 'c4', status: '', title: '' }, type: 'conversations.changed' })
    await vi.waitFor(() => {
      expect(shelved(session)).toEqual(['old notes', 'older notes'])
    })
    expect(session.$archived.get().count).toBe(2)

    // Closed, it reads nothing more.
    session.toggleArchived()
    const reads = () => calls.filter(([method]) => method === 'conversation.list')
    const shelfReads = () => reads().filter(([, params]) => (params as ListParams).archived === true).length
    const before = { all: reads().length, shelf: shelfReads() }
    hear({ payload: { conversation_id: 'c4', status: '', title: '' }, type: 'conversations.changed' })
    await vi.waitFor(() => {
      expect(reads().length).toBeGreaterThan(before.all)
    })
    expect(shelfReads()).toBe(before.shelf)
  })

  it('each read of the list says how many are archived, the section closed', async () => {
    const { calls, hear, session } = await home(lists([row('c3', 'old'), row('c4', 'older'), row('c5', 'oldest')]))

    hear({ payload: { conversation_id: 'c5', status: '', title: '' }, type: 'conversations.changed' })

    await vi.waitFor(() => {
      expect(session.$archived.get().count).toBe(3)
    })
    expect(shelved(session)).toEqual([])
    expect(calls.filter(([, params]) => (params as ListParams | undefined)?.archived === true)).toEqual([])
  })

  it('brought back, it leaves the archived ones at once', async () => {
    const { session } = await home(lists([row('c3', 'old notes'), row('c4', 'older notes')]))
    session.toggleArchived()
    await vi.waitFor(() => {
      expect(shelved(session)).toEqual(['old notes', 'older notes'])
    })

    await session.unarchiveConversation('c3')

    expect(shelved(session)).toEqual(['older notes'])
    expect(session.$archived.get().count).toBe(1)
  })

  it('the last one brought back closes the section, for the next one archived to find it closed', async () => {
    const { session } = await home(lists([row('c3', 'old notes')]))
    session.toggleArchived()
    await vi.waitFor(() => {
      expect(shelved(session)).toEqual(['old notes'])
    })

    await session.unarchiveConversation('c3')
    await session.archiveConversation('c1')

    expect(session.$archived.get()).toMatchObject({ count: 1, open: false, rows: [] })
  })
})
