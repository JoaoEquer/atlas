/**
 * The voice plugin's screen (step 17a), the file Atlas ships, as the app loads it: the live
 * conversation's engine with a fake link to the voice model and a fake conversation, so what the
 * voice is told and what Atlas is sent are checked without a microphone or WebRTC.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

// @ts-expect-error -- the plugin's screen is plain JavaScript, loaded uncompiled by the app.
import * as voice from '../../../../src/atlas/plugins/bundled/voice/desktop/plugin.js'

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

interface Dictation {
  dispose: () => void
  pressed: () => void
  state: string
}

const { acceleratorOf, AnywhereDictation } = voice as {
  acceleratorOf: (event: Partial<KeyboardEvent>) => string
  AnywhereDictation: new (options: Record<string, unknown>) => Dictation
}

/** Dictation anywhere with a fake microphone, a fake transcription and a fake paste: what the
 *  bar showed, what was written down and pasted, and the timers waiting. */
function dictation({
  ms = 2_000,
  pasted = 'pasted',
  said = 'Buy bread.',
  write
}: { ms?: number; pasted?: string; said?: string; write?: () => Promise<{ text: string }> } = {}) {
  const bars: (null | { text: string; tone: string })[] = []
  const written: Blob[] = []
  const pastes: string[] = []
  const timers: { ms: number; run: () => void }[] = []
  let recordings = 0
  const engine = new AnywhereDictation({
    bar: (state: null | { text: string; tone: string }) => {
      bars.push(state)
    },
    key: 'Capslock',
    later: (run: () => void, after: number) => {
      const timer = { ms: after, run }
      timers.push(timer)

      return () => timers.splice(timers.indexOf(timer), 1)
    },
    paste: (text: string) => {
      pastes.push(text)

      return Promise.resolve(pasted)
    },
    recorder: {
      start: () => {
        recordings += 1

        return Promise.resolve({
          cancel: () => undefined,
          stop: () => Promise.resolve({ blob: new Blob(['audio'], { type: 'audio/webm' }), ms })
        })
      }
    },
    write:
      write ??
      ((blob: Blob) => {
        written.push(blob)

        return Promise.resolve({ text: said })
      })
  })
  const settle = () => new Promise(resolve => setTimeout(resolve, 0))

  return { bars, engine, pastes, recordings: () => recordings, settle, timers, written }
}

describe('dictation anywhere (step 17b)', () => {
  it('records on a press, and on the next writes it down and pastes it where the user is', async () => {
    const run = dictation()

    run.engine.pressed()
    await run.settle()
    expect(run.engine.state).toBe('recording')
    run.engine.pressed()
    await run.settle()
    await run.settle()

    expect(run.written).toHaveLength(1)
    expect(run.pastes).toEqual(['Buy bread.'])
    expect(run.bars.map(bar => bar?.tone ?? null)).toEqual(['recording', 'working', 'done'])
    expect(run.engine.state).toBe('idle')
  })

  it('drops a press shorter than half a second unsent', async () => {
    const run = dictation({ ms: 200 })

    run.engine.pressed()
    await run.settle()
    run.engine.pressed()
    await run.settle()

    expect(run.written).toEqual([])
    expect(run.bars.at(-1)).toBeNull()
  })

  it('starts nothing on a press while the words are being written down', async () => {
    let finish: (value: { text: string }) => void = () => undefined
    const run = dictation({ write: () => new Promise(resolve => (finish = resolve)) })

    run.engine.pressed()
    await run.settle()
    run.engine.pressed()
    await run.settle()
    expect(run.engine.state).toBe('writing')
    run.engine.pressed()
    finish({ text: 'Buy bread.' })
    await run.settle()
    await run.settle()

    expect(run.recordings()).toBe(1)
    expect(run.pastes).toEqual(['Buy bread.'])
  })

  it('stops on its own at 10 minutes', async () => {
    const run = dictation()

    run.engine.pressed()
    await run.settle()
    const [stop] = run.timers
    expect(stop?.ms).toBe(10 * 60 * 1000)
    stop?.run()
    await run.settle()
    await run.settle()

    expect(run.pastes).toEqual(['Buy bread.'])
  })

  it('says when the program took no paste, when nothing was heard, and when it failed', async () => {
    const copied = dictation({ pasted: 'copied' })
    const nothing = dictation({ said: '  ' })
    const failed = dictation({ write: () => Promise.reject(new Error('OpenAI is down')) })

    for (const run of [copied, nothing, failed]) {
      run.engine.pressed()
      await run.settle()
      run.engine.pressed()
      await run.settle()
      await run.settle()
    }

    expect(copied.bars.at(-1)).toMatchObject({ tone: 'error' })
    expect(copied.bars.at(-1)?.text).toContain('Ctrl+V')
    expect(nothing.pastes).toEqual([])
    expect(nothing.bars.at(-1)).toMatchObject({ tone: 'error' })
    expect(failed.bars.at(-1)?.text).toContain('OpenAI is down')
    expect(failed.engine.state).toBe('idle')
  })

  it('names the key the user presses as Electron does', () => {
    expect(acceleratorOf({ code: 'CapsLock' })).toBe('Capslock')
    expect(acceleratorOf({ code: 'F9' })).toBe('F9')
    expect(acceleratorOf({ code: 'Space', ctrlKey: true, shiftKey: true })).toBe('Control+Shift+Space')
    expect(acceleratorOf({ code: 'KeyD', altKey: true })).toBe('Alt+D')
    expect(acceleratorOf({ code: 'ControlLeft', ctrlKey: true })).toBe('')
  })
})
