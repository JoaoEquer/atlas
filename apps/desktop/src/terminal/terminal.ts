/**
 * The side panel's terminal (step 16f): a real shell in a pseudo-terminal on the server's machine,
 * which the server opens and carries over the connection (step 15, `atlas.terminals`), the same
 * on this computer, over Tailscale, over SSH and in the container. The shell's choice, the
 * environment it gets and the marks' launch moved into the server; reading the marks stays here.
 *
 * The output is held until the view attaches. The terminal knows a command's end from the marks
 * its shells write (shell-marks.ts):
 *
 * - **A command waits for the terminal to be ready** (the first prompt's mark, 30 seconds at most:
 *   PowerShell on a busy Windows took more than 15), so one clicked too early is not lost.
 * - **It must start within 2 seconds** (its start mark), else it is interrupted and said so.
 * - **It ends with its end mark and exit code**; after 30 seconds it is "still running", its
 *   output so far given, and its end, when it comes, is told later.
 * - **A shell with no marks** (cmd) ends a command when it is quiet for a second and shows its prompt
 *   again, with no exit code.
 */
import { MarkReader, plainOutput } from './shell-marks'

/** A shell the server opened, as this terminal drives it (node-pty's shape, as before). */
export interface Pty {
  kill: () => void
  onData: (listener: (data: string) => void) => unknown
  onExit: (listener: (event: { exitCode: number }) => void) => unknown
  resize: (cols: number, rows: number) => void
  write: (data: string) => void
}

/** What the server said of the shell it opened. */
export interface Opened {
  id: string
  /** Its shell writes marks: a command's end and exit code are known. */
  marks: boolean
  shell: string
}

/** A nonce for a terminal's marks: 32 hex characters, as the server takes them. */
export function newNonce(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')
}

export interface RunResult {
  exitCode: null | number
  output: string
  /** `completed`; `running` (still running past the wait: its output so far); `unfinished` (the
   * shell never took it). */
  status: 'completed' | 'running' | 'unfinished'
}

/** A command that was still running when its result was given, and ended since. */
export interface LateEnd {
  command: string
  exitCode: null | number
  output: string
}

export interface Timing {
  /** For the first prompt. */
  readyMs: number
  /** For a command's start mark. */
  startMs: number
  /** For its end, before it is "still running". */
  endMs: number
  /** Quiet before a shell with no marks is taken to have ended. */
  quietMs: number
}

export const TIMING: Timing = { endMs: 30_000, quietMs: 1_000, readyMs: 30_000, startMs: 2_000 }
/** What a command's output keeps at most: its end, 102,400 characters. */
export const OUTPUT_CHARS = 102_400

/** Output before the window attached, at most a megabyte. */
const HELD_CHARS = 1024 * 1024

interface Capture {
  command: string
  /** Its output since its start mark (or since it was typed, with no marks). */
  raw: string
  /** Whether its start mark came. */
  started: boolean
  /** Its result was given as "still running": its end is told later. */
  late: boolean
  done: (result: RunResult) => void
}

export class Terminal {
  readonly id: string
  readonly marks: boolean
  readonly shell: string
  /** Its first prompt came. */
  ready = false
  private readonly pty: Pty
  private readonly reader: MarkReader
  private readonly waitingReady: (() => void)[] = []
  private capture: Capture | null = null
  private quiet: null | ReturnType<typeof setTimeout> = null
  private attached: ((text: string) => void) | null = null
  private held = ''
  private exited: null | number = null
  private readonly exitListeners: ((code: number) => void)[] = []

  /** `nonce`: the one the server was given for this shell's marks. */
  constructor(
    pty: Pty,
    opened: Opened,
    nonce: string,
    private readonly onLateEnd: (id: string, end: LateEnd) => void,
    private readonly timing: Timing = TIMING
  ) {
    this.id = opened.id
    this.marks = opened.marks
    this.shell = opened.shell
    this.reader = new MarkReader(nonce)
    this.pty = pty
    this.pty.onData(data => {
      this.heard(data)
    })
    this.pty.onExit(({ exitCode }) => {
      this.exited = exitCode
      this.finish({ exitCode: null, output: this.output(), status: 'unfinished' })

      for (const listener of this.exitListeners) {
        listener(exitCode)
      }
    })
  }

  /** The window shows it from now on: what came before is handed over first. */
  attach(listener: (text: string) => void): void {
    this.attached = listener

    if (this.held) {
      listener(this.held)
      this.held = ''
    }
  }

  onExit(listener: (code: number) => void): void {
    if (this.exited !== null) {
      listener(this.exited)
    } else {
      this.exitListeners.push(listener)
    }
  }

  /** A command of the user's or Atlas's runs in it now, or it is gone. */
  get busy(): boolean {
    return this.capture !== null || this.exited !== null
  }

  write(data: string): void {
    this.pty.write(data)
  }

  resize(cols: number, rows: number): void {
    this.pty.resize(Math.max(2, cols), Math.max(2, rows))
  }

  dispose(): void {
    try {
      this.pty.kill()
    } catch {
      // Already gone.
    }
  }

  /** Run one command line, once the terminal is ready, and how it ended. Rejects when the terminal
   * is busy or gone: the window opens another. */
  async run(command: string): Promise<RunResult> {
    await this.untilReady()

    if (this.busy) {
      throw new Error('The terminal is busy.')
    }

    const result = new Promise<RunResult>(resolve => {
      this.capture = { command, done: resolve, late: false, raw: '', started: false }
    })
    this.pty.write(`${command}\r`)

    if (this.marks) {
      const started = setTimeout(() => {
        const capture = this.capture

        if (capture && !capture.started) {
          // It never started (a prompt still asking, a line the shell would not take): stopped.
          this.pty.write('\x03')
          setTimeout(() => {
            this.finish({ exitCode: null, output: this.output(), status: 'unfinished' })
          }, 500)
        }
      }, this.timing.startMs)
      void result.then(() => {
        clearTimeout(started)
      })
    }

    const running = setTimeout(() => {
      const capture = this.capture

      if (capture && !capture.late) {
        capture.late = true
        capture.done({ exitCode: null, output: this.output(), status: 'running' })
      }
    }, this.timing.endMs)
    void result.then(() => {
      clearTimeout(running)
    })

    return await result
  }

  private untilReady(): Promise<void> {
    if (this.ready) {
      return Promise.resolve()
    }

    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error('The terminal did not show its prompt.'))
      }, this.timing.readyMs)
      this.waitingReady.push(() => {
        clearTimeout(timer)
        resolve()
      })
    })
  }

  private heard(data: string): void {
    for (const piece of this.reader.feed(data)) {
      if (piece.kind === 'text') {
        this.show(piece.text)

        if (this.capture && (this.capture.started || !this.marks)) {
          this.capture.raw = (this.capture.raw + piece.text).slice(-OUTPUT_CHARS * 2)
        }

        if (!this.marks) {
          this.settleWithoutMarks()
        }

        continue
      }

      const { mark } = piece

      if (mark.kind === 'prompt') {
        this.becameReady()

        // No start mark (PowerShell without PSReadLine): the next prompt is the end.
        if (this.capture && !this.capture.started) {
          this.finish({ exitCode: null, output: this.output(), status: 'completed' })
        }
      } else if (mark.kind === 'start') {
        if (this.capture) {
          this.capture.started = true
          this.capture.raw = ''
        }
      } else {
        this.finish({ exitCode: mark.exit, output: this.output(), status: 'completed' })
      }
    }
  }

  private show(text: string): void {
    if (this.attached) {
      this.attached(text)
    } else {
      this.held = (this.held + text).slice(-HELD_CHARS)
    }

    // A shell with no marks is ready once it said something and went quiet.
    if (!this.marks && !this.ready) {
      setTimeout(() => {
        this.becameReady()
      }, this.timing.quietMs / 2)
    }
  }

  private becameReady(): void {
    if (this.ready) {
      return
    }

    this.ready = true

    for (const resolve of this.waitingReady.splice(0)) {
      resolve()
    }
  }

  /** With no marks: quiet for a while and a prompt at the end is the command's end. */
  private settleWithoutMarks(): void {
    if (this.quiet) {
      clearTimeout(this.quiet)
    }

    this.quiet = setTimeout(() => {
      const capture = this.capture

      if (capture && /[>$#]\s*$/.test(plainOutput(capture.raw))) {
        // The echoed command first, the prompt last: neither is its output.
        const lines = plainOutput(capture.raw).split('\n')
        const output = lines.slice(1, -1).join('\n').trim()
        this.finish({ exitCode: null, output, status: 'completed' }, output)
      }
    }, this.timing.quietMs)
  }

  private output(): string {
    const text = plainOutput(this.capture?.raw ?? '')

    return text.length > OUTPUT_CHARS ? text.slice(-OUTPUT_CHARS) : text
  }

  private finish(result: RunResult, output = result.output): void {
    const capture = this.capture

    if (!capture) {
      return
    }

    this.capture = null

    if (capture.late) {
      this.onLateEnd(this.id, { command: capture.command, exitCode: result.exitCode, output })
    } else {
      capture.done(result)
    }
  }
}
