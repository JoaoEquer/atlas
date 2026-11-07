/**
 * The voice plugin's screen (step 17a), the file Atlas ships, as the app loads it: the live
 * conversation's engine with a fake link to the voice model and a fake conversation, so what the
 * voice is told and what Atlas is sent are checked without a microphone or WebRTC.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

// @ts-expect-error -- the plugin's screen is plain JavaScript, loaded uncompiled by the app.
import * as voice from '../../../../plugins/voice/desktop/plugin.js'

interface Event {
  [key: string]: unknown
  type: string
}

interface Engine {
  answered: (event: { payload?: unknown; type: string }) => void
  end: (reason?: string) => Promise<void>
  ended: null | string
  heard: (event: Event) => void
  start: (history: unknown[]) => Promise<void>
  state: string
  tick: () => void
}

const { appends, delegated, isStopPhrase, LiveTalk, speakable } = voice as {
  appends: (text: string, most?: number) => string[]
  delegated: (fragments: { text: string; who: string }[], request?: string) => { prompt: string; spoken: string }
  isStopPhrase: (said: string) => boolean
  LiveTalk: new (options: Record<string, unknown>) => Engine
  speakable: (text: string) => string
}

afterEach(() => {
  vi.useRealTimers()
})

function talk(quietSeconds = 120) {
  let now = 1_000
  const sent: Event[] = []
  const done = () => Promise.resolve(undefined)
  const atlas = {
    conversation: () => 'c-1',
    mark: vi.fn((conversation: string, spoken: string) => Promise.resolve(Boolean(conversation && spoken))),
    putModelBack: vi.fn(done),
    send: vi.fn((text: string) => Promise.resolve(text.length)),
    useVoiceModel: vi.fn((conversation: string) => Promise.resolve(conversation))
  }
  const link = { close: vi.fn(), open: vi.fn(done), send: (event: Event) => sent.push(event) }
  const states: string[] = []
  const engine = new LiveTalk({
    atlas,
    link,
    now: () => now,
    onState: (state: string) => states.push(state),
    quietSeconds
  })

  return {
    atlas,
    engine,
    later: (ms: number) => {
      now += ms
    },
    link,
    sent,
    states
  }
}

describe('what the voice hands over', () => {
  it('is what the user said last, with the exchange for the model', () => {
    const turn = delegated([
      { text: 'What is ', who: 'user' },
      { text: 'on today?', who: 'user' },
      { text: 'Let me check.', who: 'voice' },
      { text: 'No, tomorrow.', who: 'user' }
    ])

    expect(turn.prompt).toBe('No, tomorrow.')
    expect(turn.spoken).toBe('User: What is on today?\nVoice: Let me check.\nUser: No, tomorrow.')
  })

  it('is the request the voice model wrote when it heard nothing of the user yet', () => {
    expect(delegated([], 'book a table').prompt).toBe('book a table')
    expect(delegated([{ text: 'Book it.', who: 'user' }], 'book a table').prompt).toBe('Book it.')
  })

  it('ends the conversation on a stop phrase said alone, in either language', () => {
    for (const said of ['Stop!', 'pare.', 'Tchau', ' encerrar ']) {
      expect(isStopPhrase(said)).toBe(true)
    }

    for (const said of ['stop the container', 'pare o servidor', 'para quem é isso?']) {
      expect(isStopPhrase(said)).toBe(false)
    }
  })
})

describe('what the voice is told to say', () => {
  it('has no Markdown, code or addresses', () => {
    expect(speakable('## Done\n- **Two** meetings, see [the list](https://example.com).\n```\nx = 1\n```')).toBe(
      'Done Two meetings, see the list.'
    )
  })

  it('comes in appends the voice model takes, cut between sentences', () => {
    expect(appends('One. Two. Three.', 10)).toEqual(['One. Two.', 'Three.'])
    expect(appends('x'.repeat(25), 10)).toEqual(['xxxxxxxxxx', 'xxxxxxxxxx', 'xxxxx'])
  })
})

describe('a live conversation', () => {
  it('sends a request to Atlas as a spoken turn on the voice model, and says the answer', async () => {
    const { atlas, engine, sent, states } = talk()
    await engine.start([])
    engine.heard({ delta: 'what is on today', type: 'session.input_transcript.delta' })
    engine.heard({ delegation: { id: 'd1' }, type: 'session.delegation.created' })
    await vi.waitFor(() => {
      expect(atlas.send).toHaveBeenCalledWith('what is on today')
    })

    expect(atlas.useVoiceModel).toHaveBeenCalledWith('c-1')
    expect(atlas.mark).toHaveBeenCalledWith('c-1', 'User: what is on today')
    expect(engine.state).toBe('thinking')

    engine.answered({ payload: { name: 'calendar_read' }, type: 'tool.start' })
    engine.answered({ payload: { status: 'answered', text: '**Two** meetings.' }, type: 'message.complete' })

    expect(sent).toEqual([
      {
        content: 'Atlas is working (calendar_read). Not done yet.',
        delegation_id: 'd1',
        type: 'session.thinking.append'
      },
      { content: 'Two meetings.', delegation_id: 'd1', type: 'session.commentary.append' }
    ])
    expect(states).toEqual(['starting', 'listening', 'thinking', 'listening'])
  })

  it('says a failed turn left nothing to say', async () => {
    const { engine, sent } = talk()
    await engine.start([])
    engine.heard({ delegation: { id: 'd1' }, type: 'session.delegation.created' })
    engine.answered({ payload: { status: 'failed', text: 'boom' }, type: 'message.complete' })

    // Said, not thought: Gemini's voice waits on its call until it gets an answer.
    expect(sent.at(-1)).toEqual({
      content: 'Atlas finished without an answer to say.',
      delegation_id: 'd1',
      type: 'session.commentary.append'
    })
  })

  it('closes after the silence, which is billed too, and puts the model back', async () => {
    const { atlas, engine, later, link, sent } = talk(120)
    await engine.start([])
    later(119_000)
    engine.tick()
    expect(engine.ended).toBeNull()

    later(2_000)
    engine.tick()

    expect(engine.ended).toBe('quiet')
    expect(sent.at(-1)).toEqual({ type: 'session.close' })
    expect(link.close).toHaveBeenCalled()
    expect(atlas.putModelBack).toHaveBeenCalled()
  })

  it('never closes for silence while Atlas works', async () => {
    const { engine, later } = talk(120)
    await engine.start([])
    engine.heard({ delegation: { id: 'd1' }, type: 'session.delegation.created' })
    later(600_000)
    engine.tick()

    expect(engine.ended).toBeNull()
  })

  it('ends on a stop phrase the user says alone', async () => {
    vi.useFakeTimers()
    const { atlas, engine } = talk()
    await engine.start([])
    engine.heard({ delta: 'Pare.', type: 'session.input_transcript.delta' })
    vi.advanceTimersByTime(2_000)

    expect(engine.ended).toBe('stop_phrase')
    expect(atlas.send).not.toHaveBeenCalled()
  })
})
