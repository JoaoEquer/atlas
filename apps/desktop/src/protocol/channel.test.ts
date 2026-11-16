import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { Channel, type ChannelOptions, RpcError } from './channel'

type Sent = Record<string, unknown>

/** A channel bound to a spy transport; `outbox` holds every frame it sent, decoded. */
function harness(options: ChannelOptions = {}) {
  const outbox: Sent[] = []
  const channel = new Channel(options)
  channel.attach({ send: text => void outbox.push(JSON.parse(text) as Sent) })

  /** Hand the channel one frame, as the server would send it. */
  const receive = (frame: Record<string, unknown>) => channel.handleFrame(JSON.stringify(frame))

  return { channel, outbox, receive }
}

describe('a call the app makes', () => {
  it('settles with the result of the answer carrying its id', async () => {
    const { channel, outbox, receive } = harness()
    const pending = channel.call('conversation.list', {})
    const [request] = outbox
    receive({ id: request?.id, jsonrpc: '2.0', result: { conversations: [] } })

    expect(request).toMatchObject({ jsonrpc: '2.0', method: 'conversation.list', params: {} })
    await expect(pending).resolves.toEqual({ conversations: [] })
  })

  it('fails with an RpcError holding the error code and message', async () => {
    const { channel, outbox, receive } = harness()
    const pending = channel.call('conversation.open', { conversation_id: 'nope' })
    receive({ error: { code: 4001, message: 'There is no conversation.' }, id: outbox[0]?.id })

    await expect(pending).rejects.toEqual(new RpcError('There is no conversation.', { code: 4001 }))
  })

  it('fails with the reason given when the transport is detached', async () => {
    const { channel } = harness()
    const pending = channel.call('ping', {})
    channel.detach(new Error('gone'))

    await expect(pending).rejects.toThrow('gone')
  })

  it('returns null for text that is no JSON object', () => {
    const { channel } = harness()

    for (const text of ['null', 'not json']) {
      expect(channel.handleFrame(text)).toBeNull()
    }
  })
})

/** A server request the app must answer. */
const serverAsks = (method: string) => ({ id: 'srq-1', jsonrpc: '2.0', method, params: {} })

describe('a notification the app sends', () => {
  it('goes out with no id, and nothing waits for an answer', async () => {
    const { channel, outbox, receive } = harness()
    const event = { method: 'Page.loadEventFired', params: {}, tab: 't1' }

    expect(channel.notify('browser.event', event)).toBe(true)
    expect(outbox).toEqual([{ jsonrpc: '2.0', method: 'browser.event', params: event }])
    expect(outbox[0]).not.toHaveProperty('id')
    // The next call still takes the first id, and no answer is pending for the notification.
    const pending = channel.call('conversation.list', {})
    expect(outbox[1]?.id).toBe('r1')
    receive({ id: 'r1', jsonrpc: '2.0', result: { conversations: [] } })
    await expect(pending).resolves.toEqual({ conversations: [] })
  })

  it('is not sent, and says so, with no connection or a transport that throws', () => {
    const idle = new Channel()
    expect(idle.notify('browser.event', { method: 'x', params: {}, tab: 't' })).toBe(false)
    const broken = new Channel()
    broken.attach({
      send: () => {
        throw new Error('closed')
      }
    })
    expect(broken.notify('browser.event', { method: 'x', params: {}, tab: 't' })).toBe(false)
  })
})

describe('a request the server makes', () => {
  it('sends only the first of several answers', () => {
    const { channel, outbox, receive } = harness()
    channel.onRequest(asked => {
      for (const choice of ['once', 'deny']) {
        asked.respond({ choice })
      }

      return true
    })
    receive(serverAsks('approval'))

    expect(outbox).toEqual([{ id: 'srq-1', jsonrpc: '2.0', result: { choice: 'once' } }])
  })

  it('is refused with -32601 right away when no handler takes it', () => {
    const onUnhandledRequest = vi.fn()
    const { outbox, receive } = harness({ onUnhandledRequest })
    receive(serverAsks('sudo'))

    expect(outbox[0]).toMatchObject({ error: { code: -32601 }, id: 'srq-1' })
    expect(onUnhandledRequest).toHaveBeenCalledWith({ id: 'srq-1', method: 'sudo' })
  })

  it('is refused with -32603 when its handler throws', () => {
    const { channel, outbox, receive } = harness({ onRequestHandlerError: () => undefined })
    channel.onRequest(() => {
      throw new Error('boom')
    })
    receive(serverAsks('ask_user'))

    expect(outbox[0]).toMatchObject({ error: { code: -32603 }, id: 'srq-1' })
  })

  it('still open after a reconnect reaches the handlers from the answer listing it, as replayed', async () => {
    const { channel, outbox, receive } = harness()
    const handed: { id: string; replayed: boolean }[] = []
    channel.onRequest(({ id, replayed }) => {
      handed.push({ id, replayed })

      return true
    })
    const pending = channel.call('conversation.events', { after: 3, conversation_id: 'c1' })
    receive({
      id: outbox[0]?.id,
      result: {
        epoch: 'e',
        events: [],
        open_requests: [{ id: 'srq-9', method: 'ask_user', params: {} }],
        truncated: false
      }
    })
    await pending

    expect(handed).toEqual([{ id: 'srq-9', replayed: true }])
  })
})

describe('the keepalive', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  const timing = { heartbeatDeadlineMs: 45_000, heartbeatIntervalMs: 15_000 }

  it('gives up once a ping has gone unanswered past the deadline', () => {
    const onHeartbeatFailure = vi.fn()
    const { channel } = harness({ ...timing, onHeartbeatFailure })
    channel.startHeartbeat()
    vi.advanceTimersByTime(60_000)

    expect(onHeartbeatFailure).toHaveBeenCalledOnce()
  })

  it('holds on when ticks come a minute apart, as in a hidden window, and each ping is answered', () => {
    // Here the newest answer is always about 60 s old, so its age alone would condemn the socket.
    const onHeartbeatFailure = vi.fn()
    const { channel, outbox, receive } = harness({ ...timing, heartbeatIntervalMs: 60_000, onHeartbeatFailure })
    channel.startHeartbeat()

    for (let minute = 1; minute <= 5; minute += 1) {
      vi.advanceTimersByTime(60_000)
      receive({ id: outbox.at(-1)?.id, result: {} })
    }

    expect(onHeartbeatFailure).not.toHaveBeenCalled()
  })

  it('takes any frame for a sign of life, so a long stream of events keeps the socket', () => {
    const onHeartbeatFailure = vi.fn()
    const { channel, receive } = harness({ ...timing, onHeartbeatFailure })
    channel.startHeartbeat()

    for (let elapsed = 0; elapsed < 120_000; elapsed += 1_000) {
      vi.advanceTimersByTime(1_000)
      receive({ method: 'event', params: { payload: { text: 'x' }, type: 'message.delta' } })
    }

    expect(onHeartbeatFailure).not.toHaveBeenCalled()
  })
})
