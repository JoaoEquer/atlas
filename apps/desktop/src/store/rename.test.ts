import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import type { AtlasClient } from '@/protocol/client'
import type { ConversationRow, Opened, Renamed, RenameParams } from '@/protocol/protocol.generated'

import { current, type HomeSession, openHome, startApp } from './app'

function row(id: string, title: string): ConversationRow {
  return { archived_at: '', id, messages: 2, running: false, source: 'app', started_at: '', title, updated_at: '' }
}

let stop: () => void = () => undefined

afterEach(() => {
  stop()
})

/** A home whose server answers a rename with `rename` and opens any conversation under `opened`'s
 * title, its list holding two conversations and the first open in a tab. */
async function home(rename: (params: RenameParams) => Promise<Renamed>, opened = 'As stored') {
  let hear: (event: EventParams) => void = () => undefined
  const none = () => () => undefined
  const client = {
    call: (method: string, params: RenameParams) => {
      if (method === 'conversation.rename') {
        return rename(params)
      }

      if (method === 'conversation.open') {
        const open: Opened = {
          auto_mode: true,
          conversation: row(params.conversation_id, opened),
          epoch: 'e',
          folder: '',
          messages: [],
          open_requests: [],
          running: false,
          seq: 0
        }

        return Promise.resolve(open)
      }

      return new Promise(() => undefined)
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
  session.$conversations.set([row('c1', 'plan the trip'), row('c2', 'other')])
  session.tabs.made({ conversationId: 'c1', kind: 'conversation' }, 'plan the trip')

  return {
    hear: (event: EventParams) => {
      hear(event)
    },
    session
  }
}

const tabTitles = (session: HomeSession) => session.tabs.$tabs.get().map(tab => tab.title)
const listed = (session: HomeSession) => session.$conversations.get().map(open => open.title)

describe('renaming a conversation (step 9e)', () => {
  it('names it in the list and in its tab, as the server kept the name', async () => {
    const { session } = await home(() => Promise.resolve({ title: 'Trip to Lisbon' }))

    await session.renameConversation('c1', '  Trip to   Lisbon ')

    expect(listed(session)).toEqual(['Trip to Lisbon', 'other'])
    expect(tabTitles(session)).toEqual(['Trip to Lisbon'])
  })

  it('a refusal leaves the name as it was, and says why to whoever renamed it', async () => {
    const { session } = await home(() => Promise.reject(new Error('the server went away')))

    await expect(session.renameConversation('c1', 'Trip')).rejects.toThrow('the server went away')

    expect(listed(session)).toEqual(['plan the trip', 'other'])
    expect(tabTitles(session)).toEqual(['plan the trip'])
  })

  it('a name given in another app reaches the list and a tab whose conversation the list lacks', async () => {
    const { hear, session } = await home(() => new Promise(() => undefined))
    session.tabs.made({ conversationId: 'c9', kind: 'conversation' }, 'Old name')

    hear({ payload: { conversation_id: 'c9', status: '', title: 'New name' }, type: 'conversations.changed' })
    hear({ payload: { conversation_id: 'c2', status: '', title: 'Renamed too' }, type: 'conversations.changed' })

    expect(tabTitles(session)).toEqual(['plan the trip', 'New name'])
    expect(listed(session)).toEqual(['plan the trip', 'Renamed too'])
  })

  it('a conversation read again gives its tab the title the server holds', async () => {
    const { session } = await home(() => new Promise(() => undefined), 'Renamed while away')

    await session.readConversation('c1')

    expect(tabTitles(session)).toEqual(['Renamed while away'])
  })
})
