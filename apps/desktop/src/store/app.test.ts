import { afterEach, describe, expect, it, vi } from 'vitest'

import type { EventParams, ServerRequest } from '@/protocol/channel'
import type { AtlasClient, ConnectionState } from '@/protocol/client'
import type { InstanceRow } from '@/protocol/protocol.generated'

import {
  $home,
  $homeNotice,
  backUpHome,
  current,
  folderKey,
  MAX_OPEN_HOMES,
  openHome,
  startApp,
  toggleExecutor,
  toggleFolder
} from './app'
import { instanceKey } from './status'
import { EMPTY_THREAD } from './thread'

/** A client that never connects: the pool's choices depend only on recency and running turns. */
function quietClient(): AtlasClient {
  const none = () => () => undefined

  return {
    call: () => new Promise(() => undefined),
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
  vi.useRealTimers()
})

describe('the open homes (a server per home)', () => {
  it('keeps at most three, letting go of the one used longest ago with no turn running', async () => {
    vi.useFakeTimers()
    const released: (null | string)[] = []
    stop = startApp({
      client: () => quietClient(),
      release: home => {
        released.push(home)

        return Promise.resolve()
      },
      shell: { focus: () => undefined, notify: () => undefined }
    })

    vi.advanceTimersByTime(10)
    await openHome('work')
    const work = current()
    work.$threads.setKey('c1', { ...EMPTY_THREAD, running: true })
    vi.advanceTimersByTime(10)
    await openHome('study')
    expect(released).toEqual([])

    // A fourth: the first home, used longest ago and idle, goes.
    vi.advanceTimersByTime(10)
    await openHome('trips')
    expect(MAX_OPEN_HOMES).toBe(3)
    expect(released).toEqual([null])

    // A fifth: 'work' is older, but its turn runs, so 'study' goes; the home on screen never does.
    vi.advanceTimersByTime(10)
    await openHome('notes')
    expect(released).toEqual([null, 'study'])
    expect(current().name).toBe('notes')
  })

  it('switching to an open home starts nothing new', async () => {
    const opened: (null | string)[] = []
    stop = startApp({
      client: home => {
        opened.push(home)

        return quietClient()
      },
      shell: { focus: () => undefined, notify: () => undefined }
    })

    await openHome('work')
    current().name = 'work'
    await openHome('work')

    expect(opened).toEqual([null, 'work'])
  })
})

describe('a title a model wrote', () => {
  it('renames the row in the list, and a row not listed yet reads the list again', async () => {
    let hear: (event: EventParams) => void = () => undefined
    const calls: string[] = []
    const client = Object.assign(quietClient(), {
      call: (method: string) => {
        calls.push(method)

        return new Promise(() => undefined)
      },
      onEvent: (handler: (event: EventParams) => void) => {
        hear = handler

        return () => undefined
      }
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    const session = current()
    const row = {
      id: 'c1',
      messages: 2,
      running: false,
      source: 'app',
      started_at: '',
      title: 'me ajuda com o nginx',
      updated_at: ''
    }
    session.$conversations.set([row, { ...row, id: 'c2', title: 'other' }])

    hear({ conversation_id: 'c1', payload: { title: 'Configurar nginx' }, type: 'title.changed' })
    expect(session.$conversations.get().map(r => r.title)).toEqual(['Configurar nginx', 'other'])
    expect(calls).not.toContain('conversation.list')

    hear({ conversation_id: 'c3', payload: { title: 'New one' }, type: 'title.changed' })
    // The list is read in its turn, after the reads of it asked before (step 9e); only the row not
    // listed reads it.
    await vi.waitFor(() => {
      expect(calls.filter(method => method === 'conversation.list')).toHaveLength(1)
    })
  })
})

describe('a message on its way (the UI review, 25/10/2026)', () => {
  /** A home on 'c1' whose sends wait for `settle`, and whose events `hear` delivers. */
  async function home() {
    let hear: (event: EventParams) => void = () => undefined
    const sends: { refuse: (error: Error) => void; settle: () => void }[] = []
    const client = Object.assign(quietClient(), {
      call: (method: string) =>
        method === 'conversation.send'
          ? new Promise<unknown>((resolve, reject) => {
              sends.push({
                refuse: reject,
                settle: () => {
                  resolve({})
                }
              })
            })
          : new Promise(() => undefined),
      onEvent: (handler: (event: EventParams) => void) => {
        hear = handler

        return () => undefined
      }
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    const session = current()
    session.$selected.set('c1')
    session.$threads.setKey('c1', EMPTY_THREAD)
    const said = (type: string, text: string, seq: number) => {
      hear({ conversation_id: 'c1', payload: { attachments: [], kind: 'message', text }, seq, type })
    }

    return { hear, said, sends, session }
  }

  const shown = (rows: { state: string; text: string }[] | undefined) => rows?.map(row => `${row.state} ${row.text}`)

  it("shows at once, gives way to the server's own, and goes when the send fails", async () => {
    const { said, sends, session } = await home()

    const sent = session.send('Plan the trip')
    expect(shown(session.$sending.get().c1)).toEqual(['sending Plan the trip'])
    said('message.start', 'Plan the trip', 1)
    expect(session.$sending.get().c1).toBeUndefined()
    sends[0]?.settle()
    await sent

    const failed = session.send('Again')
    expect(shown(session.$sending.get().c1)).toEqual(['sending Again'])
    sends[1]?.refuse(new Error('gone'))
    await expect(failed).rejects.toThrow('gone')
    expect(session.$sending.get().c1).toBeUndefined()
  })

  it('in a turn that runs, waits to enter it, and goes when it entered or the turn ended', async () => {
    const { hear, said, sends, session } = await home()
    said('message.start', 'First', 1)

    const steering = session.send('Use Vitest')
    sends[0]?.settle()
    await steering
    expect(shown(session.$sending.get().c1)).toEqual(['queued Use Vitest'])
    said('message.steered', 'Use Vitest', 2)
    expect(session.$sending.get().c1).toBeUndefined()

    const late = session.send('And the docs')
    sends[1]?.settle()
    await late
    expect(shown(session.$sending.get().c1)).toEqual(['queued And the docs'])
    // The turn stopped first: the words come back to the box, and the bubble goes.
    hear({
      conversation_id: 'c1',
      payload: {
        cost_usd: null,
        error: null,
        model_calls: 1,
        status: 'interrupted',
        text: '',
        unsent: ['And the docs']
      },
      seq: 3,
      type: 'message.complete'
    })
    expect(session.$sending.get().c1).toBeUndefined()
    expect(session.$restored.get().c1).toBe('And the docs')
  })
})

describe('a card answered (the UI review, 25/10/2026)', () => {
  /** A home whose requests, events and socket states the test hands it. */
  async function home() {
    let ask: (request: ServerRequest) => boolean = () => false
    let hear: (event: EventParams) => void = () => undefined
    let state: (now: ConnectionState) => void = () => undefined
    const client = Object.assign(quietClient(), {
      onEvent: (handler: (event: EventParams) => void) => {
        hear = handler

        return () => undefined
      },
      onRequest: (handler: (request: ServerRequest) => boolean) => {
        ask = handler

        return () => undefined
      },
      onState: (handler: (now: ConnectionState) => void) => {
        state = handler

        return () => undefined
      }
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    const request = (): ServerRequest => ({
      fail: vi.fn(),
      id: 'srq-1',
      method: 'approval',
      params: { conversation_id: 'c1', reason: 'recursive delete', target: 'rm -r build', tool: 'run_command' },
      replayed: false,
      respond: vi.fn()
    })

    return { ask, hear, request, session: current(), state }
  }

  it('says the answer is on its way until the server has it, then goes', async () => {
    const { ask, hear, request, session } = await home()
    const asked = request()
    ask(asked)

    session.answer('srq-1', { choice: 'once' })
    expect(asked.respond).toHaveBeenCalledWith({ choice: 'once' })
    expect(session.$cards.get().map(card => card.answered)).toEqual([true])
    hear({
      conversation_id: 'c1',
      payload: { id: 'srq-1', method: 'approval', reason: 'resolved' },
      type: 'request.cancel'
    })
    expect(session.$cards.get()).toEqual([])
  })

  it('answered as the socket fell, takes an answer again once the request comes again', async () => {
    const { ask, request, session, state } = await home()
    ask(request())
    session.answer('srq-1', { choice: 'deny' })

    state('open')
    expect(session.$cards.get()).toEqual([])
    ask({ ...request(), replayed: true })
    expect(session.$cards.get().map(card => card.answered ?? false)).toEqual([false])
  })
})

describe("an executor's session on screen after a reconnect", () => {
  it('is read again: the turn that ended while the socket was gone shows its answer', async () => {
    let connected: (state: ConnectionState) => void = () => undefined
    const said = (text: string) => ({ at: '', by: 'harness', call: '', failed: false, kind: 'text', text, tool: '' })
    const client = Object.assign(quietClient(), {
      call: (method: string) => {
        if (method === 'workbench.list') {
          return Promise.resolve({ executors: [], instances: [{ instance_id: 'in-1', state: 'idle' }] })
        }

        if (method === 'executor.session') {
          return Promise.resolve({ entries: [said('Looking.'), said('Found it.')], expired: false })
        }

        return new Promise(() => undefined)
      },
      onState: (listener: (state: ConnectionState) => void) => {
        connected = listener

        return () => undefined
      }
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    const session = current()
    const open = { choice: null, elsewhere: '', executor: 'claude-code', expired: false, folder: 'C:/work' }
    session.$openSession.set({ ...open, instanceId: 'in-1', sessionId: 's-1', title: '' })
    const live = { key: 'a1', kind: 'text', role: 'interim', streaming: false, text: 'Looking.' } as const
    session.$sessionThread.set({ items: [live], running: true })

    connected('open')
    await vi.waitFor(() => {
      expect(session.$sessionThread.get().running).toBe(false)
    })

    expect(session.$sessionThread.get().items.map(item => (item.kind === 'text' ? item.text : item.kind))).toEqual([
      'Looking.',
      'Found it.'
    ])
  })
})

describe("the executors tab's folders as an instance changes", () => {
  it('reads the open folder again after the read running, and leaves a closed one to read when it opens', async () => {
    vi.useFakeTimers()
    let hear: (event: EventParams) => void = () => undefined
    const calls: [string, unknown][] = []
    const reads: ((value: unknown) => void)[] = []
    const client = Object.assign(quietClient(), {
      call: (method: string, params: unknown) => {
        calls.push([method, params])

        if (method === 'executor.folders') {
          return Promise.resolve({ folders: [] })
        }

        return method === 'executor.sessions'
          ? new Promise(resolve => {
              reads.push(resolve)
            })
          : new Promise(() => undefined)
      },
      onEvent: (handler: (event: EventParams) => void) => {
        hear = handler

        return () => undefined
      }
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    const session = current()
    const sessionsOf = (folder: string) =>
      calls.filter(
        ([method, params]) => method === 'executor.sessions' && (params as { folder: string }).folder === folder
      )
    await session.refreshFolders('claude-code')
    session.openFolder('claude-code', 'C:/work/shop')
    // The api folder was read once, then closed.
    session.openFolder('claude-code', 'C:/work/api')
    reads[1]?.({ sessions: [] })
    await vi.waitFor(() => {
      expect(session.$sessions.get()[folderKey('claude-code', 'C:/work/api')]?.sessions).toEqual([])
    })
    session.toggleFolder('claude-code', 'C:/work/api')

    // The shop's read still runs when its instance changes: one more read follows it, not two.
    const row = { executor: 'claude-code', folder: 'C:/work/shop', instance_id: 'in-1', state: 'working', waiting: 0 }
    hear({ conversation_id: '', payload: row, type: 'workbench.update' })
    hear({ conversation_id: '', payload: { ...row, state: 'idle' }, type: 'workbench.update' })
    vi.advanceTimersByTime(300)
    expect(sessionsOf('C:/work/shop')).toHaveLength(1)
    reads[0]?.({ sessions: [] })
    await vi.waitFor(() => {
      expect(sessionsOf('C:/work/shop')).toHaveLength(2)
    })
    expect(sessionsOf('C:/work/api')).toHaveLength(1)
    expect(session.$sessions.get()[folderKey('claude-code', 'C:/work/api')]?.stale).toBe(true)

    // Opened again, the closed one is read again.
    session.openFolder('claude-code', 'C:/work/api')
    expect(sessionsOf('C:/work/api')).toHaveLength(2)
  })

  it('a folder and a session that work keep their time as read again, and take the new one once they stop', async () => {
    let at = '2026-10-22T10:00:00Z'
    const client = Object.assign(quietClient(), {
      call: (method: string) =>
        method === 'executor.folders'
          ? Promise.resolve({
              folders: [{ account_id: '', executor: 'claude-code', folder: 'C:/work/shop', updated_at: at }]
            })
          : method === 'executor.sessions'
            ? Promise.resolve({ sessions: [{ instance_id: 'in-1', session_id: 's-1', updated_at: at }] })
            : new Promise(() => undefined)
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    const session = current()
    const times = () => [
      session.$sessionFolders.get()['claude-code']?.folders?.[0]?.updated_at,
      session.$sessions.get()[folderKey('claude-code', 'C:/work/shop')]?.sessions?.[0]?.updated_at
    ]
    const working = (state: string) => {
      session.$workbench.set({
        executors: [],
        instances: [{ executor: 'claude-code', folder: 'C:/work/shop', instance_id: 'in-1', state } as InstanceRow]
      })
    }
    await session.refreshFolders('claude-code')
    await session.refreshSessions('claude-code', 'C:/work/shop')

    // Its tools write its transcript as it works: that moves nothing.
    working('working')
    at = '2026-10-22T10:05:00Z'
    await session.refreshFolders('claude-code')
    await session.refreshSessions('claude-code', 'C:/work/shop')
    expect(times()).toEqual(['2026-10-22T10:00:00Z', '2026-10-22T10:00:00Z'])

    working('idle')
    await session.refreshFolders('claude-code')
    await session.refreshSessions('claude-code', 'C:/work/shop')
    expect(times()).toEqual([at, at])
  })
})

describe('a backup from the app (step 10c)', () => {
  /** A client whose `home.backup` answers `answer`, keeping each call with its deadline. */
  function backupClient(answer: () => Promise<unknown>, calls: unknown[][]): AtlasClient {
    return Object.assign(quietClient(), {
      call: (...args: unknown[]) => {
        calls.push(args)

        return args[0] === 'home.backup' ? answer() : new Promise(() => undefined)
      }
    })
  }

  it('does nothing when the save dialog is cancelled, and remembers the home on screen', async () => {
    const calls: unknown[][] = []
    const remembered: string[] = []
    stop = startApp({
      client: () => backupClient(() => Promise.resolve({}), calls),
      shell: {
        backupTarget: () => Promise.resolve(null),
        focus: () => undefined,
        notify: () => undefined,
        remember: home => remembered.push(home)
      }
    })
    $home.set('work')

    await backUpHome()

    expect(calls.filter(call => call[0] === 'home.backup')).toEqual([])
    expect($homeNotice.get()).toBeNull()
    expect(remembered).toEqual(['work'])
  })

  it('replaces only the file the dialog confirmed, waits as long as the zip takes, and says how it ended', async () => {
    const calls: unknown[][] = []
    const written = { files: 3, path: 'C:/backups/atlas.zip', size: 2_400_000 }
    let answer = () => Promise.resolve<unknown>(written)
    stop = startApp({
      client: () => backupClient(() => answer(), calls),
      shell: {
        backupTarget: home => Promise.resolve(`C:/backups/${home}.zip`),
        focus: () => undefined,
        notify: () => undefined
      }
    })
    $home.set('default')

    await backUpHome()

    expect(calls.filter(call => call[0] === 'home.backup')).toEqual([
      ['home.backup', { path: 'C:/backups/default.zip', replace: true }, 0]
    ])
    expect($homeNotice.get()).toEqual({ kind: 'backed-up', written })

    answer = () => Promise.reject(new Error('C:/backups is not writable.'))
    await backUpHome()
    expect($homeNotice.get()).toEqual({ kind: 'failed', message: 'C:/backups is not writable.' })
  })
})

describe('the plugins changing while an executor works', () => {
  it('reads the executors again, and an older list never takes back what the events said', async () => {
    let hear: (event: EventParams) => void = () => undefined
    let answer: (value: unknown) => void = () => undefined
    const client = Object.assign(quietClient(), {
      call: (method: string) =>
        method === 'workbench.list' ? new Promise(resolve => (answer = resolve)) : new Promise(() => undefined),
      onEvent: (handler: (event: EventParams) => void) => {
        hear = handler

        return () => undefined
      }
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')
    const session = current()
    const idle = { executor: 'claude-code', instance_id: 'in-1', state: 'idle', waiting: 0 }
    session.$workbench.set({ executors: [], instances: [idle] as never[] })

    // A look for the harnesses tells the plugins changed; its list is read while the session
    // starts a turn, and comes back after the turn's own event (found by the CI on 18/10/2026).
    hear({ payload: { waiting: 0 }, type: 'plugins.changed' })
    hear({ payload: { ...idle, state: 'working' }, type: 'workbench.update' })
    const found = { name: 'claude-code', title: 'Claude Code' }
    answer({ executors: [found], instances: [idle], open_requests: [] })
    await vi.waitFor(() => {
      expect(session.$workbench.get().executors).toEqual([found])
    })

    expect(session.$workbench.get().instances.map(row => row.state)).toEqual(['working'])
  })
})

describe("the sidebar's dots (asked for by the user on 20/10/2026)", () => {
  /** The app on a client whose events the test says, and whose list says `running` of each row. */
  async function hearing(running: Record<string, boolean> = {}) {
    let hear: (event: EventParams) => void = () => undefined
    const client = Object.assign(quietClient(), {
      call: (method: string) =>
        method === 'conversation.list'
          ? Promise.resolve({
              conversations: Object.entries(running).map(([id, on]) => ({
                id,
                messages: 1,
                running: on,
                source: 'app',
                started_at: '',
                title: id,
                updated_at: ''
              }))
            })
          : new Promise(() => undefined),
      onEvent: (handler: (event: EventParams) => void) => {
        hear = handler

        return () => undefined
      }
    })
    stop = startApp({ client: () => client, shell: { focus: () => undefined, notify: () => undefined } })
    await openHome('work')

    return {
      hear: (event: EventParams) => {
        hear(event)
      },
      session: current()
    }
  }

  const turn = (conversationId: string, status: string): EventParams => ({
    payload: { conversation_id: conversationId, status },
    type: 'conversations.changed'
  })

  it('a conversation works from its start, and its end not seen stays until it opens', async () => {
    const { hear, session } = await hearing()
    session.$selected.set('c2')

    hear(turn('c1', 'started'))
    expect(session.$live.get()['c1']).toBe(true)
    expect(session.$used.get()['c1']).toBeGreaterThan(0)

    hear(turn('c1', 'answered'))
    expect(session.$live.get()['c1']).toBeUndefined()
    expect(session.$marks.get()['c1']).toBe('unread')

    // The one on screen keeps nothing; a failure elsewhere is red; the user's own stop is nothing.
    hear(turn('c2', 'failed'))
    hear(turn('c3', 'failed'))
    hear(turn('c4', 'interrupted'))
    expect(session.$marks.get()).toEqual({ c1: 'unread', c3: 'failed' })

    void session.selectConversation('c1')
    expect(session.$marks.get()['c1']).toBeUndefined()
  })

  it('a row read just as its turn ends does not make it work again', async () => {
    const { hear, session } = await hearing({ c1: true, c2: true })

    await session.refreshList()
    expect(session.$live.get()).toEqual({ c1: true, c2: true })

    hear(turn('c1', 'answered'))
    await session.refreshList()
    expect(session.$live.get()).toEqual({ c2: true })
  })

  it("an executor's session that stops off screen keeps it, and only its answer puts it first", async () => {
    const { hear, session } = await hearing()
    const row = { executor: 'claude-code', folder: 'C:/work/api', instance_id: 'in-1', state: 'working' }

    // Its work, and what it asks, move nothing (asked for by the user on 22/10/2026).
    hear({ payload: row, type: 'workbench.update' })
    hear({ payload: { ...row, waiting: 1 }, type: 'workbench.update' })
    expect(session.$used.get()).toEqual({})

    hear({ payload: { ...row, state: 'idle' }, type: 'workbench.update' })
    expect(session.$marks.get()[instanceKey('in-1')]).toBe('unread')
    const used = session.$used.get()
    expect(used['claude-code']).toBeUndefined()
    expect(used[instanceKey('in-1')]).toBeGreaterThan(0)
    expect(used[folderKey('claude-code', 'C:/work/api')]).toBe(used[instanceKey('in-1')])

    hear({ payload: { ...row, state: 'working' }, type: 'workbench.update' })
    expect(session.$marks.get()[instanceKey('in-1')]).toBeUndefined()

    // On screen, its end is seen.
    session.$view.set('session')
    session.$openSession.set({
      choice: null,
      elsewhere: '',
      executor: 'claude-code',
      expired: false,
      folder: 'C:/work/api',
      instanceId: 'in-1',
      sessionId: 's-1',
      title: ''
    })
    hear({ payload: { ...row, state: 'idle' }, type: 'workbench.update' })
    expect(session.$marks.get()[instanceKey('in-1')]).toBeUndefined()
  })

  it('opens several executors at once; closing one leaves the others, and the panel goes to the one clicked', async () => {
    const { session } = await hearing()

    toggleExecutor('claude-code')
    toggleExecutor('claude-code@ab12')
    expect(session.$openExecutors.get()).toEqual({ 'claude-code': true, 'claude-code@ab12': true })
    expect(session.$openExecutor.get()).toBe('claude-code@ab12')

    toggleExecutor('claude-code')
    expect(session.$openExecutors.get()).toEqual({ 'claude-code@ab12': true })
    expect(session.$openExecutor.get()).toBe('claude-code')

    toggleFolder('claude-code@ab12', 'C:/work/api')
    expect(session.$openExecutor.get()).toBe('claude-code@ab12')

    toggleExecutor('claude-code@ab12')
    expect(session.$openExecutors.get()).toEqual({})
    expect(session.$openExecutor.get()).toBe('claude-code@ab12')
  })

  it("a session's tab takes the panel to its executor, whatever the sidebar chose last", async () => {
    const { session } = await hearing()
    const place = {
      kind: 'session' as const,
      session: {
        choice: null,
        elsewhere: '',
        executor: 'claude-code@ab12',
        expired: false,
        folder: 'C:/work/api',
        instanceId: '',
        sessionId: 's-1',
        title: 'Example'
      }
    }
    session.tabs.show(place, 'Example')
    const tab = session.tabs.find(place)
    toggleExecutor('claude-code')
    expect(session.$openExecutor.get()).toBe('claude-code')

    void session.showTab(tab?.id ?? '')
    expect(session.$openExecutor.get()).toBe('claude-code@ab12')
  })
})
