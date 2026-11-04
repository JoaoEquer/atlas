import { atom } from 'nanostores'

import { t } from '@/i18n'
import type { DictationRow } from '@/protocol/protocol.generated'

import { $dictationsSeen, current } from './app'

/**
 * Dictation (step 26, the voice plugin's until then): the box's microphone, and dictation anywhere,
 * a shortcut heard in every program while the app is open. A press records, the next stops, and
 * the words are written down on the server (`dictation.write`, as `[voice] transcription` says)
 * and pasted where the user is, the floating bar saying what goes on. A press shorter than half a
 * second is dropped unsent, a recording stops on its own at 10 minutes, and a press while the
 * words are being written down starts nothing. Every dictation is kept in the home, with its
 * recording, on the Dictations page.
 *
 * Dictation anywhere is off until the user chooses a key (decided with the user on 03/11/2026):
 * the key is this computer's, kept by the app.
 */

/** A dictation shorter than this is a press by mistake, dropped before anyone hears it. */
export const SHORTEST_MS = 500
/** A dictation stops on its own past this: the server takes 10 MB. */
export const LONGEST_MS = 10 * 60 * 1000
/** How long the bar stays after a dictation landed, and after it failed. */
export const DONE_MS = 1500
export const FAILED_MS = 5000
/** The key the app holds the shortcut under, in the main process. */
const SHORTCUT_ID = 'atlas:dictate'
/** Where the app keeps the chosen key. */
const STORAGE_KEY = 'atlas.dictation.shortcut'

export interface BarState {
  text: string
  tone: 'done' | 'error' | 'recording' | 'working'
}

/** A recording under way. */
export interface Recording {
  cancel: () => void
  stop: () => Promise<{ blob: Blob; ms: number }>
}

export interface Recorder {
  start: () => Promise<Recording>
}

/** What dictation anywhere does, apart from the microphone and the system, so it is tested
 *  without either. */
export interface AnywhereParts {
  bar: (state: BarState | null) => Promise<void> | void
  key?: string
  later: (run: () => void, ms: number) => () => void
  onDone?: () => void
  paste: (text: string) => Promise<'app' | 'copied' | 'pasted'>
  recorder: Recorder
  write: (blob: Blob) => Promise<DictationRow>
}

type State = 'idle' | 'recording' | 'starting' | 'writing'

/** Dictation anywhere: a press records, the next writes it down and pastes it. */
export class AnywhereDictation {
  key: string
  state: State = 'idle'
  private recording: null | Recording = null
  private cancelTimer: () => void = () => undefined

  constructor(private readonly parts: AnywhereParts) {
    this.key = parts.key ?? ''
  }

  /** The shortcut was pressed: record, stop, or nothing while the words are written down. */
  pressed(): void {
    if (this.state === 'idle') {
      void this.start()
    } else if (this.state === 'recording') {
      void this.stop()
    }
  }

  async start(): Promise<void> {
    this.state = 'starting'
    this.cancelTimer()

    try {
      this.recording = await this.parts.recorder.start()
    } catch (failed) {
      this.state = 'idle'
      this.fail(failed)

      return
    }

    this.state = 'recording'
    this.show({ text: t.dictation.recording(this.key), tone: 'recording' })
    this.cancelTimer = this.parts.later(() => void this.stop(), LONGEST_MS)
  }

  async stop(): Promise<void> {
    if (this.state !== 'recording' || this.recording === null) {
      return
    }

    this.state = 'writing'
    this.cancelTimer()
    const recording = this.recording
    this.recording = null

    try {
      const { blob, ms } = await recording.stop()

      if (ms < SHORTEST_MS || !blob.size) {
        this.show(null)

        return
      }

      this.show({ text: t.dictation.writing, tone: 'working' })
      const written = await this.parts.write(blob)
      this.parts.onDone?.()

      if (written.error) {
        this.fail(written.error)

        return
      }

      const text = written.text.trim()

      if (!text) {
        this.flash({ text: t.dictation.nothing, tone: 'error' }, FAILED_MS)

        return
      }

      const where = await this.parts.paste(text)

      if (where === 'copied') {
        this.flash({ text: t.dictation.copied, tone: 'error' }, FAILED_MS)
      } else {
        this.flash({ text: t.dictation.pasted, tone: 'done' }, DONE_MS)
      }
    } catch (failed) {
      this.parts.onDone?.()
      this.fail(failed)
    } finally {
      this.state = 'idle'
    }
  }

  /** Let go: a recording under way is dropped. */
  dispose(): void {
    this.cancelTimer()
    this.recording?.cancel()
    this.recording = null
    this.state = 'idle'
  }

  private fail(failed: unknown): void {
    const why = failed instanceof Error ? failed.message : String(failed)
    this.flash({ text: t.dictation.failed(why), tone: 'error' }, FAILED_MS)
  }

  private show(state: BarState | null): void {
    void Promise.resolve()
      .then(() => this.parts.bar(state))
      .catch(() => undefined)
  }

  /** Show `state`, hidden after `ms` unless a new dictation started meanwhile. */
  private flash(state: BarState, ms: number): void {
    this.show(state)
    this.cancelTimer = this.parts.later(() => {
      if (this.state === 'idle') {
        this.show(null)
      }
    }, ms)
  }
}

/** The microphone, as the dictation's recorder: one recording at a time. */
export const microphone: Recorder = {
  async start() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const made = new MediaRecorder(stream)
    const chunks: Blob[] = []
    const began = Date.now()
    made.addEventListener('dataavailable', event => {
      if (event.data.size) {
        chunks.push(event.data)
      }
    })
    const ended = new Promise(resolve => {
      made.addEventListener('stop', resolve, { once: true })
    })
    made.start()
    const release = () => {
      stream.getTracks().forEach(track => {
        track.stop()
      })
    }

    return {
      cancel: () => {
        made.stop()
        release()
      },
      stop: async () => {
        const ms = Date.now() - began
        made.stop()
        await ended
        release()

        return { blob: new Blob(chunks, { type: made.mimeType || 'audio/webm' }), ms }
      }
    }
  }
}

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      resolve(typeof reader.result === 'string' ? (reader.result.split(',', 2)[1] ?? '') : '')
    }
    reader.onerror = () => {
      reject(reader.error ?? new Error('The recording could not be read.'))
    }
    reader.readAsDataURL(blob)
  })
}

// -- the server's dictations ----------------------------------------------------------------------

/** Keep a recording in the home and have it written down; one that could not be says why. */
export async function writeDictation(blob: Blob, where: 'anywhere' | 'box'): Promise<DictationRow> {
  return current().client.call('dictation.write', {
    audio: await blobToBase64(blob),
    media_type: blob.type || 'audio/webm',
    where
  })
}

export async function listDictations(): Promise<DictationRow[]> {
  return (await current().client.call('dictation.list', {})).dictations
}

export function dictationAudio(id: string): Promise<{ audio: string; media_type: string }> {
  return current().client.call('dictation.audio', { id })
}

export function writeAgain(id: string): Promise<DictationRow> {
  return current().client.call('dictation.again', { id })
}

export async function deleteDictation(id: string): Promise<void> {
  await current().client.call('dictation.delete', { id })
}

function dictationsChanged(): void {
  $dictationsSeen.set($dictationsSeen.get() + 1)
}

// -- dictation anywhere's shortcut ----------------------------------------------------------------

/** The key dictation anywhere holds, `''` when off, and why it could not be held. */
export const $shortcut = atom<{ key: string; why: string }>({ key: '', why: '' })

let engine: AnywhereDictation | null = null
let hearing: (() => void) | null = null

function bridge(): DesktopBridge | undefined {
  return (window as { atlas?: { desktop?: DesktopBridge } }).atlas?.desktop
}

/** Start dictation anywhere as the app opens, on the key the user chose; nothing in an app with no
 *  desktop powers (a browser, a test). */
export function startDictationAnywhere(recorder: Recorder = microphone): void {
  const desktop = bridge()

  if (!desktop || engine) {
    return
  }

  engine = new AnywhereDictation({
    bar: state => desktop.bar(state),
    later: (run, ms) => {
      const id = window.setTimeout(run, ms)

      return () => {
        window.clearTimeout(id)
      }
    },
    onDone: dictationsChanged,
    paste: text => desktop.paste(text),
    recorder,
    write: blob => writeDictation(blob, 'anywhere')
  })
  hearing = desktop.onPressed(key => {
    if (key === SHORTCUT_ID) {
      engine?.pressed()
    }
  })
  void holdShortcut(localStorage.getItem(STORAGE_KEY) ?? '')
}

/** Hold `key` for dictation anywhere, kept for the next opening; `''` turns it off. */
export async function chooseShortcut(key: string): Promise<void> {
  if (key) {
    localStorage.setItem(STORAGE_KEY, key)
  } else {
    localStorage.removeItem(STORAGE_KEY)
  }

  await holdShortcut(key)
}

/** Let the key go while the user picks another, so the press is heard as a choice. */
export async function pauseShortcut(): Promise<void> {
  await bridge()?.shortcut(SHORTCUT_ID, '')
}

/** Hold the key chosen before, after a pick was cancelled. */
export async function resumeShortcut(): Promise<void> {
  await holdShortcut($shortcut.get().key)
}

async function holdShortcut(key: string): Promise<void> {
  const desktop = bridge()

  if (engine) {
    engine.key = key
  }

  let why = ''

  try {
    why = desktop ? await desktop.shortcut(SHORTCUT_ID, key) : ''
  } catch (failed) {
    why = failed instanceof Error ? failed.message : String(failed)
  }

  $shortcut.set({ key, why })
}

/** Let everything go, as the window closes. */
export function stopDictationAnywhere(): void {
  hearing?.()
  hearing = null
  engine?.dispose()
  engine = null
}

// -- the key the user presses ---------------------------------------------------------------------

/** Keys Electron names otherwise than the browser's `code`. */
const KEY_NAMES: Record<string, string> = {
  ArrowDown: 'Down',
  ArrowLeft: 'Left',
  ArrowRight: 'Right',
  ArrowUp: 'Up',
  Backquote: '`',
  BracketLeft: '[',
  BracketRight: ']',
  CapsLock: 'Capslock',
  Comma: ',',
  Equal: '=',
  Minus: '-',
  Period: '.',
  ScrollLock: 'Scrolllock',
  Semicolon: ';',
  Slash: '/'
}
/** Keys named the same in both. */
const SAME_NAMES = new Set([
  'Backspace',
  'Delete',
  'End',
  'Enter',
  'Home',
  'Insert',
  'PageDown',
  'PageUp',
  'Space',
  'Tab'
])

/** Electron's name for the key the user pressed, with what they held, or `''` for a modifier
 *  alone (Electron's shortcuts need a key). */
export function acceleratorOf(
  event: Partial<Pick<KeyboardEvent, 'altKey' | 'ctrlKey' | 'metaKey' | 'shiftKey'>> & Pick<KeyboardEvent, 'code'>
): string {
  const code = event.code
  let key = KEY_NAMES[code] ?? (SAME_NAMES.has(code) ? code : '')

  if (!key && /^Key[A-Z]$/.test(code)) {
    key = code.slice(3)
  } else if (!key && /^Digit\d$/.test(code)) {
    key = code.slice(5)
  } else if (!key && /^F\d{1,2}$/.test(code)) {
    key = code
  } else if (!key && /^Numpad\d$/.test(code)) {
    key = `num${code.slice(6)}`
  }

  if (!key) {
    return ''
  }

  const held = [
    event.ctrlKey && 'Control',
    event.altKey && 'Alt',
    event.shiftKey && 'Shift',
    event.metaKey && 'Super'
  ].filter((name): name is string => Boolean(name))

  return [...held, key].join('+')
}
