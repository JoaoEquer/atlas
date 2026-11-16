import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { AgentBrowser, AgentTab } from './app-browser'
import { BrowserHost, type HostConnection, type HostRequestFrame, MAX_RESULT_BYTES } from './browser-host'

const HOME = 'local/work'

/** What a request came to, for the test to read. */
interface Outcome {
  error?: { code: number; message: string }
  result?: Record<string, unknown>
}

/** A connection that holds what the host said and lets the test play the server. */
class FakeConnection implements HostConnection {
  accept: (nonce: string, file: string) => Promise<unknown> = () => Promise.resolve({ accepted: true, home: 'work' })
  readonly notifications: { method: string; params: unknown; tab: string }[] = []
  readonly seen: { file: string; nonce: string; stillThere: boolean; text: string }[] = []
  started = 0
  stopped = 0
  private taker: ((request: HostRequestFrame) => boolean) | null = null
  private listener: ((state: string) => void) | null = null

  call(_method: 'browser.host', params: { nonce: string; proof_file: string }): Promise<unknown> {
    this.seen.push({
      file: params.proof_file,
      nonce: params.nonce,
      stillThere: existsSync(params.proof_file),
      text: existsSync(params.proof_file) ? readFileSync(params.proof_file, 'utf8') : ''
    })

    return this.accept(params.nonce, params.proof_file)
  }

  notify(_method: 'browser.event', params: { method: string; params: unknown; tab: string }): boolean {
    this.notifications.push(params)

    return true
  }

  onRequest(handler: (request: HostRequestFrame) => boolean): () => void {
    this.taker = handler

    return () => {
      this.taker = null
    }
  }

  onState(listener: (state: string) => void): () => void {
    this.listener = listener

    return () => {
      this.listener = null
    }
  }

  start(): void {
    this.started += 1
  }

  stop(): void {
    this.stopped += 1
  }

  state(state: string): void {
    this.listener?.(state)
  }

  /** The server asks; the answer, or what the host did with it. */
  ask(method: string, params: Record<string, unknown>): { outcome: Promise<Outcome>; taken: boolean } {
    let settle: (value: Outcome) => void = () => undefined
    const outcome = new Promise<Outcome>(resolve => {
      settle = resolve
    })
    const taken =
      this.taker?.({
        fail: (code, message) => {
          settle({ error: { code, message } })
        },
        method,
        params,
        respond: result => {
          settle({ result })
        }
      }) ?? false

    return { outcome, taken }
  }
}

class FakeBrowser implements AgentBrowser {
  readonly calls: string[] = []
  cdpResult: unknown = { ok: true }
  failure: null | string = null
  known = new Set(['t1'])
  private listener: ((home: string, id: string, method: string, params: unknown) => void) | null = null

  cdp(home: string, id: string, method: string, params: Record<string, unknown>): Promise<unknown> {
    this.calls.push(`cdp ${home} ${id} ${method} ${JSON.stringify(params)}`)

    return this.failure === null ? Promise.resolve(this.cdpResult) : Promise.reject(new Error(this.failure))
  }

  close(home: string, id: string): boolean {
    this.calls.push(`close ${home} ${id}`)

    return this.known.has(id) && home === HOME
  }

  onEvent(listener: (home: string, id: string, method: string, params: unknown) => void): () => void {
    this.listener = listener

    return () => {
      this.listener = null
    }
  }

  open(home: string, owner: string, url: string, background: boolean): string {
    this.calls.push(`open ${home} ${owner} ${url} ${String(background)}`)

    return 't2'
  }

  show(home: string, id: string): boolean {
    this.calls.push(`show ${home} ${id}`)

    return this.known.has(id)
  }

  list(home: string): { product: string; tabs: AgentTab[]; userAgent: string } {
    this.calls.push(`tabs ${home}`)

    return {
      product: 'Chrome/1',
      tabs: [{ id: 't1', loading: false, owner: 'c-1', selected: true, title: 'T', url: 'https://example.com/' }],
      userAgent: 'agent/1'
    }
  }

  watch(home: string, id: string, on: boolean): boolean {
    this.calls.push(`watch ${home} ${id} ${String(on)}`)

    return this.known.has(id)
  }

  say(home: string, id: string, method: string, params: unknown): void {
    this.listener?.(home, id, method, params)
  }
}

let folder: string
let connection: FakeConnection
let browser: FakeBrowser
let host: BrowserHost
const logged: string[] = []

/** The host, with the server having accepted it. */
async function hosted(): Promise<void> {
  host.start()
  connection.state('open')
  await expect.poll(() => host.hosting).toBe(true)
}

beforeEach(() => {
  folder = mkdtempSync(path.join(tmpdir(), 'atlas-host-'))
  connection = new FakeConnection()
  browser = new FakeBrowser()
  logged.length = 0
  host = new BrowserHost({
    browser,
    connection,
    log: line => {
      logged.push(line)
    },
    userData: folder
  })
})

afterEach(() => {
  host.stop()
  rmSync(folder, { force: true, recursive: true })
})

describe('the proof', () => {
  it('is a private file in the proof folder, with a nonce the server reads, removed when it answers', async () => {
    await hosted()

    expect(connection.started).toBe(1)
    const [proof] = connection.seen
    expect(proof?.stillThere).toBe(true)
    expect(proof?.nonce).toMatch(/^[0-9a-f]{64}$/)
    expect(proof?.text).toBe(proof?.nonce)
    expect(path.basename(path.dirname(proof?.file ?? ''))).toBe('browser-proof')
    expect(path.basename(proof?.file ?? '')).toMatch(/^[0-9a-f]{32}$/)
    expect(existsSync(proof?.file ?? '')).toBe(false)
  })

  it('is offered again, new, on every connection, and is gone when the server refuses', async () => {
    await hosted()
    connection.accept = () => Promise.resolve({ accepted: false, home: '', reason: 'No files shared.' })
    connection.state('closed')
    expect(host.hosting).toBe(false)
    connection.state('open')
    await expect.poll(() => connection.seen.length).toBe(2)

    expect(connection.seen[1]?.nonce).not.toBe(connection.seen[0]?.nonce)
    await expect.poll(() => logged.some(line => line.includes('No files shared.'))).toBe(true)
    expect(host.hosting).toBe(false)
    expect(readdirSync(path.join(folder, 'browser-proof'))).toEqual([])
  })

  it('is removed when the call fails', async () => {
    connection.accept = () => Promise.reject(new Error('The connection closed.'))
    host.start()
    connection.state('open')
    await expect.poll(() => logged.length).toBeGreaterThan(0)

    expect(host.hosting).toBe(false)
    expect(readdirSync(path.join(folder, 'browser-proof'))).toEqual([])
  })
})

describe("the server's requests", () => {
  it('are answered for the home the server named', async () => {
    await hosted()

    expect((await connection.ask('browser.tabs', {}).outcome).result).toEqual({
      product: 'Chrome/1',
      tabs: [{ id: 't1', loading: false, owner: 'c-1', selected: true, title: 'T', url: 'https://example.com/' }],
      user_agent: 'agent/1'
    })
    expect(
      (await connection.ask('browser.open', { background: true, owner: 'c-1', url: 'about:blank' }).outcome).result
    ).toEqual({
      id: 't2'
    })
    expect((await connection.ask('browser.show', { id: 't1' }).outcome).result).toEqual({})
    expect((await connection.ask('browser.attach', { id: 't1' }).outcome).result).toEqual({})
    expect((await connection.ask('browser.detach', { id: 't1' }).outcome).result).toEqual({})
    expect((await connection.ask('browser.close', { id: 't1' }).outcome).result).toEqual({})
    expect(browser.calls).toEqual([
      `tabs ${HOME}`,
      `open ${HOME} c-1 about:blank true`,
      `show ${HOME} t1`,
      `watch ${HOME} t1 true`,
      `watch ${HOME} t1 false`,
      `close ${HOME} t1`
    ])
  })

  it('leave the others alone, and are errors when the browser is not offered', async () => {
    host.start()

    // Another request is the window's: taken, and never answered by this connection.
    const card = connection.ask('executor.approval', {})
    expect(card.taken).toBe(true)
    expect(await Promise.race([card.outcome, Promise.resolve('unanswered')])).toBe('unanswered')
    const early = connection.ask('browser.tabs', {})
    expect(early.taken).toBe(true)
    expect((await early.outcome).error?.message).toContain('not offered')
    expect(browser.calls).toEqual([])
  })

  it('refuse a tab that is not there or of another home, and a shape that is wrong', async () => {
    await hosted()

    expect((await connection.ask('browser.close', { id: 'nope' }).outcome).error?.message).toBe('No such tab.')
    expect((await connection.ask('browser.show', { id: 'nope' }).outcome).error?.message).toBe('No such tab.')
    expect((await connection.ask('browser.attach', { id: 'nope' }).outcome).error?.message).toBe('No such tab.')
    expect((await connection.ask('browser.close', {}).outcome).error?.code).toBe(-32602)
    expect((await connection.ask('browser.eval', { id: 't1' }).outcome).error?.code).toBe(-32602)
  })

  it('pass a DevTools command, and say why one failed or was too large', async () => {
    await hosted()
    const run = connection.ask('browser.cdp', { id: 't1', method: 'Page.enable', params: {} })

    expect((await run.outcome).result).toEqual({ result: { ok: true } })
    expect(browser.calls).toEqual([`cdp ${HOME} t1 Page.enable {}`])

    browser.failure = 'Cannot navigate to invalid URL'
    expect(
      (await connection.ask('browser.cdp', { id: 't1', method: 'Page.navigate', params: {} }).outcome).error
    ).toEqual({
      code: -32000,
      message: 'Cannot navigate to invalid URL'
    })

    browser.failure = null
    browser.cdpResult = { data: 'x'.repeat(MAX_RESULT_BYTES + 1) }
    const big = await connection.ask('browser.cdp', { id: 't1', method: 'Page.captureScreenshot', params: {} }).outcome
    expect(big.error?.message).toContain('too large')

    // Fewer characters than the cap, more bytes: each "é" is one UTF-16 unit and two UTF-8 bytes.
    browser.cdpResult = { data: 'é'.repeat(Math.ceil(MAX_RESULT_BYTES / 2) + 1) }
    const wide = await connection.ask('browser.cdp', { id: 't1', method: 'Page.captureScreenshot', params: {} }).outcome
    expect(wide.error?.message).toContain('too large')
  })
})

describe("the browser's events", () => {
  it('leave as notifications, only for the home the server named, and stop with the connection', async () => {
    await hosted()
    browser.say(HOME, 't1', 'Page.loadEventFired', { timestamp: 1 })
    browser.say('local/personal', 't9', 'Page.loadEventFired', {})

    expect(connection.notifications).toEqual([{ method: 'Page.loadEventFired', params: { timestamp: 1 }, tab: 't1' }])

    connection.state('closed')
    browser.say(HOME, 't1', 'Page.frameNavigated', {})

    expect(connection.notifications).toHaveLength(1)
  })

  it('stop for good with the host', async () => {
    await hosted()
    host.stop()
    browser.say(HOME, 't1', 'Page.loadEventFired', {})

    expect(connection.stopped).toBe(1)
    expect(connection.notifications).toEqual([])
  })
})
