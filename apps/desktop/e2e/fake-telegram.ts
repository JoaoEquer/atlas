import { type ChildProcess, spawn } from 'node:child_process'

import { ROOT, stopTree } from './harness'

/** The token tests/fake_telegram.py takes. */
export const TOKEN = '123456:fake-token'
export const ANA = 100

/** tests/fake_telegram.py in a process of its own, driven over HTTP as a person would write. */
export class FakeTelegram {
  private constructor(
    private readonly child: ChildProcess,
    readonly url: string
  ) {}

  static async start(): Promise<FakeTelegram> {
    const child = spawn('uv', ['run', '--project', ROOT, 'python', 'tests/fake_telegram.py'], {
      cwd: ROOT,
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
    const port = await new Promise<number>((resolve, reject) => {
      let output = ''
      const timer = setTimeout(() => {
        reject(new Error(`The fake Telegram did not start.\n${output}`))
      }, 60_000)
      child.stdout.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
        const match = /FAKE_TELEGRAM_READY port=(\d+)/.exec(output)

        if (match?.[1]) {
          clearTimeout(timer)
          resolve(Number(match[1]))
        }
      })
      child.stderr.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
      })
    })

    return new FakeTelegram(child, `http://127.0.0.1:${String(port)}`)
  }

  async say(user: number, text: string): Promise<void> {
    await fetch(`${this.url}/_fake/say`, {
      body: JSON.stringify({ name: 'Ana', text, user }),
      headers: { 'content-type': 'application/json' },
      method: 'POST'
    })
  }

  /** What the bot sent `user`. */
  async sent(user: number): Promise<string[]> {
    const response = await fetch(`${this.url}/_fake/calls`)
    const { calls } = (await response.json()) as { calls: { method: string; params: Record<string, unknown> }[] }

    return calls
      .filter(call => call.method === 'sendMessage' && String(call.params.chat_id) === String(user))
      .map(call => String(call.params.text))
  }

  stop(): void {
    stopTree(this.child)
  }
}
