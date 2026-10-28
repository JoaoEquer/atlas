/**
 * The voice plugin's live conversation through Gemini, the screen Atlas ships as the app loads it:
 * Gemini's messages read into the engine's events, the PCM the microphone and the speaker carry,
 * and the link with a fake WebSocket and no microphone, so what Gemini is sent is checked without
 * either. The setup itself is the Python's (tests/plugins/test_voice.py).
 */
import { describe, expect, it, vi } from 'vitest'

// @ts-expect-error -- the plugin's screen is plain JavaScript, loaded uncompiled by the app.
import * as voice from '../../../../src/atlas/plugins/bundled/voice/desktop/plugin.js'

interface Event {
  [key: string]: unknown
  type: string
}

interface Link {
  close: () => void
  open: (history: unknown[]) => Promise<void>
  send: (event: Event) => void
}

const { GeminiLink, geminiEvents, pcmBase64, pcmSamples } = voice as {
  GeminiLink: new (
    ctx: { call: (name: string, params: unknown) => Promise<unknown> },
    onEvent: (event: Event) => void,
    onClosed: (reason: string) => void,
    options: { audio: unknown; socket: (url: string) => FakeSocket }
  ) => Link
  geminiEvents: (message: unknown) => Event[]
  pcmBase64: (samples: Float32Array) => string
  pcmSamples: (base64: string) => Float32Array
}

/** A WebSocket that records what is sent and lets the test speak as Gemini. */
class FakeSocket {
  binaryType = 'blob'
  closed = false
  readyState = 0
  sent: Record<string, unknown>[] = []
  private listeners = new Map<string, ((event: unknown) => void)[]>()

  constructor(readonly url: string) {}

  addEventListener(type: string, listener: (event: unknown) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {
    this.closed = true
    this.readyState = 3
  }

  emit(type: string, event: unknown = {}) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event)
    }
  }

  /** Gemini's messages come as binary frames of UTF-8 JSON. */
  gemini(message: unknown) {
    this.emit('message', { data: new TextEncoder().encode(JSON.stringify(message)).buffer })
  }

  open() {
    this.readyState = 1
    this.emit('open')
  }

  send(text: string) {
    this.sent.push(JSON.parse(text) as Record<string, unknown>)
  }
}

function linked() {
  const sockets: FakeSocket[] = []
  const events: Event[] = []
  const closed: string[] = []
  const played: string[] = []
  const chunks: ((chunk: string) => void)[] = []
  const speaker = { close: vi.fn(), flush: vi.fn(), play: (data: string) => played.push(data) }
  const microphone = { stop: vi.fn() }
  const audio = {
    microphone: (onChunk: (chunk: string) => void) => {
      chunks.push(onChunk)

      return Promise.resolve(microphone)
    },
    speaker: () => speaker
  }
  const call = vi.fn((_name: string, params: unknown) => {
    const resume = (params as { resume?: string }).resume ?? ''

    return Promise.resolve({
      messages: [{ setup: { model: 'models/gemini-live', resume } }, { clientContent: { turns: ['before'] } }],
      url: `wss://example.com/live?access_token=t${String(sockets.length + 1)}`
    })
  })
  const link = new GeminiLink(
    { call },
    event => events.push(event),
    reason => closed.push(reason),
    {
      audio,
      socket: url => {
        const socket = new FakeSocket(url)
        sockets.push(socket)

        return socket
      }
    }
  )

  /** The n-th WebSocket the link made. */
  const at = (index: number): FakeSocket => {
    const socket = sockets[index]

    if (!socket) {
      throw new Error(`No WebSocket ${String(index)} was made.`)
    }

    return socket
  }

  /** The link opened: the socket opens, Gemini says the setup is done. */
  const open = async (history: unknown[] = []) => {
    const opening = link.open(history)
    await vi.waitFor(() => {
      expect(sockets.length).toBeGreaterThan(0)
    })
    at(0).open()
    at(0).gemini({ setupComplete: {} })
    await opening
  }

  return { at, call, chunks, closed, events, link, microphone, open, played, sockets, speaker }
}

describe("Gemini's messages", () => {
  it('become the words heard each way, and a call of ask_atlas a delegation', () => {
    expect(
      geminiEvents({
        serverContent: { inputTranscription: { text: 'book a ' }, outputTranscription: { text: 'On it.' } }
      })
    ).toEqual([
      { delta: 'book a ', type: 'session.input_transcript.delta' },
      { delta: 'On it.', type: 'session.output_transcript.delta' }
    ])
    expect(
      geminiEvents({
        toolCall: {
          functionCalls: [
            { args: { request: 'book a table' }, id: 'call_1', name: 'ask_atlas' },
            { args: {}, id: 'call_2', name: 'something_else' }
          ]
        }
      })
    ).toEqual([{ delegation: { id: 'call_1' }, request: 'book a table', type: 'session.delegation.created' }])
    expect(geminiEvents({ setupComplete: {} })).toEqual([])
  })
})

describe('the PCM Gemini takes and gives', () => {
  it('is 16-bit little-endian samples in base64, both ways', () => {
    const samples = new Float32Array([0, 0.5, -0.5, 1, -1])
    const text = pcmBase64(samples)

    expect(atob(text).length).toBe(10)
    expect(Array.from(pcmSamples(text)).map(sample => Math.round(sample * 1000) / 1000)).toEqual([0, 0.5, -0.5, 1, -1])
  })
})

describe('the Gemini link', () => {
  it('sends the setup, then the history once Gemini is set up, then the microphone', async () => {
    const { at, call, chunks, open } = linked()
    await open([{ role: 'user', text: 'hi' }])

    expect(call).toHaveBeenCalledWith('live_session', { history: [{ role: 'user', text: 'hi' }] })
    expect(at(0).url).toBe('wss://example.com/live?access_token=t1')
    expect(at(0).binaryType).toBe('arraybuffer')
    expect(at(0).sent).toEqual([
      { setup: { model: 'models/gemini-live', resume: '' } },
      { clientContent: { turns: ['before'] } }
    ])

    chunks[0]?.('AAAA')

    expect(at(0).sent.at(-1)).toEqual({
      realtimeInput: { audio: { data: 'AAAA', mimeType: 'audio/pcm;rate=16000' } }
    })
  })

  it('plays the voice, drops it when the user speaks over it, and hands what it heard to the engine', async () => {
    const { at, events, open, played, speaker } = linked()
    await open()
    at(0).gemini({
      serverContent: {
        modelTurn: { parts: [{ inlineData: { data: 'UENN', mimeType: 'audio/pcm;rate=24000' } }] },
        outputTranscription: { text: 'Hello' }
      }
    })
    at(0).gemini({ serverContent: { interrupted: true } })

    expect(played).toEqual(['UENN'])
    expect(speaker.flush).toHaveBeenCalled()
    expect(events).toEqual([{ delta: 'Hello', type: 'session.output_transcript.delta' }])
  })

  it("answers a call of ask_atlas with Atlas's answer, its parts in one response", async () => {
    const { at, events, link, open } = linked()
    await open()
    at(0).gemini({ toolCall: { functionCalls: [{ args: { request: 'x' }, id: 'call_1', name: 'ask_atlas' }] } })

    expect(events.at(-1)).toMatchObject({ delegation: { id: 'call_1' }, type: 'session.delegation.created' })

    link.send({ content: 'Thinking.', delegation_id: 'call_1', type: 'session.thinking.append' })
    link.send({ content: 'Two meetings.', delegation_id: 'call_1', type: 'session.commentary.append' })
    link.send({ content: 'Both online.', delegation_id: 'call_1', type: 'session.commentary.append' })
    await Promise.resolve()

    expect(at(0).sent.at(-1)).toEqual({
      toolResponse: {
        functionResponses: [{ id: 'call_1', name: 'ask_atlas', response: { result: 'Two meetings. Both online.' } }]
      }
    })
    expect(at(0).sent.filter(sent => 'toolResponse' in sent)).toHaveLength(1)
  })

  it('tells an answer to a call Gemini cancelled as a new turn', async () => {
    const { at, link, open } = linked()
    await open()
    at(0).gemini({ toolCall: { functionCalls: [{ args: {}, id: 'call_1', name: 'ask_atlas' }] } })
    at(0).gemini({ toolCallCancellation: { ids: ['call_1'] } })
    link.send({ content: 'Done.', delegation_id: 'call_1', type: 'session.commentary.append' })
    await Promise.resolve()

    expect(at(0).sent.at(-1)).toEqual({
      clientContent: { turnComplete: true, turns: [{ parts: [{ text: 'Atlas answered: Done.' }], role: 'user' }] }
    })
  })

  it('goes on in a new connection when Gemini says it closes this one, with the handle it gave', async () => {
    const { at, call, closed, open, sockets } = linked()
    await open()
    at(0).gemini({ sessionResumptionUpdate: { newHandle: 'h-1', resumable: true } })
    at(0).gemini({ goAway: { timeLeft: '10s' } })
    await vi.waitFor(() => {
      expect(sockets).toHaveLength(2)
    })
    at(1).open()
    at(1).gemini({ setupComplete: {} })
    await vi.waitFor(() => {
      expect(at(0).closed).toBe(true)
    })

    expect(call).toHaveBeenLastCalledWith('live_session', { resume: 'h-1' })
    expect(at(1).sent[0]).toEqual({ setup: { model: 'models/gemini-live', resume: 'h-1' } })
    expect(closed).toEqual([])
  })

  it('ends when the connection is lost with no handle to go on, and closes everything on close', async () => {
    const { at, closed, link, microphone, open, speaker } = linked()
    await open()
    at(0).emit('close', { reason: 'gone' })

    expect(closed).toEqual(['connection_lost'])

    link.send({ type: 'session.close' })

    expect(microphone.stop).toHaveBeenCalled()
    expect(speaker.close).toHaveBeenCalled()
    expect(at(0).closed).toBe(true)
  })

  it('fails to open with what Gemini said when it refuses the setup', async () => {
    const { at, link, sockets } = linked()
    const opening = link.open([])
    await vi.waitFor(() => {
      expect(sockets).toHaveLength(1)
    })
    at(0).open()
    at(0).emit('close', { reason: 'Invalid voice name.' })

    await expect(opening).rejects.toThrow('Invalid voice name.')
  })
})
