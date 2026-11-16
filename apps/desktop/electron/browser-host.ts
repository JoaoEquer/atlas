/**
 * The browser host (docs/GLOSSARY.md, `browser host`): the main process lending the app's browser to
 * an Atlas server on this computer, so the server's `browser_exec` drives the pages the user sees in
 * the panel.
 *
 * - **A connection of its own, never the window's.** The window never gets a way to send raw DevTools
 *   commands to pages that hold the user's logins; the main process dials each server itself and
 *   answers the server's `browser.*` requests from `AppBrowser` (`app-browser.ts`).
 * - **The proof is a file.** An SSH tunnel or a published container port reach the server from the
 *   loopback too, so no address proves that the server shares this computer. The app writes a file
 *   with a random nonce in its own folder, names it in `browser.host`, and removes it when the
 *   server has answered; a server that cannot read the file refuses, and `browser_exec` stays on
 *   Atlas's Chrome. Every connection offers it again (the server forgets a host with the
 *   connection).
 * - **It answers only for the home the server named.** The server's answer says which home it
 *   serves; a request for a tab of another home is an error, and so is any DevTools command outside
 *   the list (`app-browser-rules.ts`, `cdpAllowed`).
 * - **Events leave only for tabs the server watches**, as notifications; a result too large for a
 *   frame is an error, not a dropped connection.
 */
import { randomBytes } from 'node:crypto'
import { mkdirSync, unlinkSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { browserHome } from '../src/browser-home'

import type { AgentBrowser } from './app-browser'
import { parseHostRequest } from './app-browser-rules'

/** The server's frame limit is 16 MiB; a result near it is refused, so the connection stays. */
export const MAX_RESULT_BYTES = 15 * 1024 * 1024
/** The folder, under the app's own, where proofs are written, and what the server looks for. */
export const PROOF_FOLDER = 'browser-proof'

/** JSON-RPC's own codes, as the channel uses them. */
const INVALID_PARAMS = -32602
const SERVER_ERROR = -32000
const METHOD_NOT_FOUND = -32601

/** A server request, as the connection hands it over (`ServerRequest` in `src/protocol/channel.ts`). */
export interface HostRequestFrame {
  fail: (code: number, message: string) => void
  method: string
  params: Record<string, unknown>
  respond: (result: Record<string, unknown>) => void
}

/** What the host needs of a connection to a server: the app's `AtlasClient`, or a stand-in. */
export interface HostConnection {
  call: (method: 'browser.host', params: { nonce: string; proof_file: string }) => Promise<unknown>
  notify: (method: 'browser.event', params: { method: string; params: unknown; tab: string }) => boolean
  onRequest: (handler: (request: HostRequestFrame) => boolean) => () => void
  onState: (listener: (state: string) => void) => () => void
  start: () => void
  stop: () => void
}

export interface HostOptions {
  browser: AgentBrowser
  connection: HostConnection
  log?: (message: string) => void
  /** The app's own folder: the proofs go in its `browser-proof/`. */
  userData: string
}

/** The browser, offered to one server. */
export class BrowserHost {
  /** The home the server serves, as the tabs name it, once it accepted; `null` before and after. */
  private home: null | string = null
  private readonly log: (message: string) => void
  private readonly stops: (() => void)[] = []

  constructor(private readonly options: HostOptions) {
    this.log = options.log ?? (() => undefined)
    const { browser, connection } = options
    this.stops.push(
      connection.onState(state => {
        if (state === 'open') {
          void this.offer()
        } else {
          this.home = null
        }
      }),
      connection.onRequest(request => this.take(request)),
      browser.onEvent((home, tab, method, params) => {
        if (home === this.home) {
          connection.notify('browser.event', { method, params, tab })
        }
      })
    )
  }

  /** Dial the server, and offer the browser each time the connection opens. */
  start(): void {
    this.options.connection.start()
  }

  stop(): void {
    this.home = null

    for (const stop of this.stops.splice(0)) {
      stop()
    }

    this.options.connection.stop()
  }

  /** Whether the server accepted the browser on this connection. */
  get hosting(): boolean {
    return this.home !== null
  }

  /** Write the proof, offer it, and remove it once the server has answered, whatever it said. */
  private async offer(): Promise<void> {
    const folder = path.join(this.options.userData, PROOF_FOLDER)
    const file = path.join(folder, randomBytes(16).toString('hex'))
    const nonce = randomBytes(32).toString('hex')

    try {
      mkdirSync(folder, { recursive: true })
      writeFileSync(file, nonce, { mode: 0o600 })
      const answer = (await this.options.connection.call('browser.host', { nonce, proof_file: file })) as {
        accepted?: boolean
        home?: string
        reason?: string
      }

      if (answer.accepted === true && typeof answer.home === 'string') {
        this.home = browserHome(null, answer.home)
      } else {
        this.home = null
        this.log(`The server did not take the app's browser: ${answer.reason ?? 'no reason given'}`)
      }
    } catch (error) {
      this.home = null
      this.log(`The browser host could not offer the app's browser: ${String(error)}`)
    } finally {
      try {
        unlinkSync(file)
      } catch {
        // Never written, or already gone.
      }
    }
  }

  private take(request: HostRequestFrame): boolean {
    // The server sends its other requests (a card to answer) to every connection, this one too: they
    // are the window's. Taken but never answered, so no answer of this connection's refuses them.
    if (!request.method.startsWith('browser.')) {
      return true
    }

    const home = this.home

    if (home === null) {
      request.fail(SERVER_ERROR, "The app's browser is not offered to this server.")

      return true
    }

    const asked = parseHostRequest(request.method, request.params)

    if (asked === null) {
      request.fail(
        request.method === 'browser.host' ? METHOD_NOT_FOUND : INVALID_PARAMS,
        `Not a request: ${request.method}`
      )

      return true
    }

    const { browser } = this.options
    const unknown = () => {
      request.fail(SERVER_ERROR, 'No such tab.')
    }

    switch (asked.kind) {
      case 'tabs': {
        const found = browser.list(home)
        request.respond({ product: found.product, tabs: found.tabs, user_agent: found.userAgent })

        break
      }

      case 'open': {
        request.respond({ id: browser.open(home, asked.owner, asked.url, asked.background) })

        break
      }

      case 'close':
      case 'show': {
        if (browser[asked.kind](home, asked.id)) {
          request.respond({})
        } else {
          unknown()
        }

        break
      }

      case 'attach':
      case 'detach': {
        if (browser.watch(home, asked.id, asked.kind === 'attach')) {
          request.respond({})
        } else {
          unknown()
        }

        break
      }

      case 'cdp': {
        browser.cdp(home, asked.id, asked.method, asked.params).then(
          result => {
            // In UTF-8 bytes, as the server's frame limit counts them: `length` counts UTF-16 units.
            const size = Buffer.byteLength(JSON.stringify(result ?? null), 'utf8')

            if (size > MAX_RESULT_BYTES) {
              request.fail(SERVER_ERROR, `The result of ${asked.method} is too large (${String(size)} bytes).`)
            } else {
              request.respond({ result: result ?? null })
            }
          },
          (error: unknown) => {
            request.fail(SERVER_ERROR, error instanceof Error ? error.message : String(error))
          }
        )

        break
      }
    }

    return true
  }
}
