/**
 * Dictation anywhere (step 17b, Atlas's own since step 26) with a fake microphone, a fake
 * transcription and a fake paste: what the bar showed, what was written down and pasted, and the
 * timers waiting.
 */
import { describe, expect, it } from 'vitest'

import type { DictationRow } from '@/protocol/protocol.generated'

import { acceleratorOf, AnywhereDictation, type BarState } from './dictation'

function row(text: string, error = ''): DictationRow {
  return {
    at: '2026-11-04T10:00:00-0300',
    error,
    id: '20261104-100000-ab12',
    media_type: 'audio/webm',
    text,
    where: 'anywhere'
  }
}

/** Dictation anywhere with a fake microphone, a fake transcription and a fake paste: what the
 *  bar showed, what was written down and pasted, and the timers waiting. */
function dictation({
  ms = 2_000,
  pasted = 'pasted',
  said = 'Buy bread.',
  write
}: { ms?: number; pasted?: 'app' | 'copied' | 'pasted'; said?: string; write?: () => Promise<DictationRow> } = {}) {
  const bars: (BarState | null)[] = []
  const written: Blob[] = []
  const pastes: string[] = []
  const timers: { ms: number; run: () => void }[] = []
  let recordings = 0
  const engine = new AnywhereDictation({
    bar: (state: BarState | null) => {
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

        return Promise.resolve(row(said))
      })
  })
  const settle = () => new Promise(resolve => setTimeout(resolve, 0))

  return { bars, engine, pastes, recordings: () => recordings, settle, timers, written }
}

describe('dictation anywhere', () => {
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
    let finish: (value: DictationRow) => void = () => undefined
    const run = dictation({ write: () => new Promise(resolve => (finish = resolve)) })

    run.engine.pressed()
    await run.settle()
    run.engine.pressed()
    await run.settle()
    expect(run.engine.state).toBe('writing')
    run.engine.pressed()
    finish(row('Buy bread.'))
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

  it('says why a recording kept on the server was not written down', async () => {
    const run = dictation({ write: () => Promise.resolve(row('', 'The speech model is not on this computer yet.')) })

    run.engine.pressed()
    await run.settle()
    run.engine.pressed()
    await run.settle()
    await run.settle()

    expect(run.pastes).toEqual([])
    expect(run.bars.at(-1)?.text).toContain('not on this computer yet')
  })

  it('names the key the user presses as Electron does', () => {
    expect(acceleratorOf({ code: 'CapsLock' })).toBe('Capslock')
    expect(acceleratorOf({ code: 'F9' })).toBe('F9')
    expect(acceleratorOf({ code: 'Space', ctrlKey: true, shiftKey: true })).toBe('Control+Shift+Space')
    expect(acceleratorOf({ code: 'KeyD', altKey: true })).toBe('Alt+D')
    expect(acceleratorOf({ code: 'ControlLeft', ctrlKey: true })).toBe('')
  })
})
