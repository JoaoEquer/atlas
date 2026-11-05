/**
 * Another machine's Atlas, from the machine list (step 15, docs/ARCHITECTURE.md, Several machines):
 * over Tailscale, straight to its tailnet address; over SSH, through the tunnel this app opens. The
 * connection opens the first time the machine is opened, never before (connect on demand: no
 * surprise tunnel), and **Test** probes both legs, `/health` and the WebSocket with the token, so a
 * pass means a conversation will work.
 *
 * A server of another protocol says its number in `/health`; the error says which side to update.
 *
 * **A site up there** (`localhost:3000` on that machine) opens in the preview through one more
 * `-L` on an `ssh` machine; over Tailscale the tab says the address is that machine's: a dev server
 * listens on its loopback.
 */
import { PROTOCOL } from '../src/protocol/protocol.generated'
import { relayUrl } from '../src/protocol/relay-address'

import type { Machine } from './machines'
import { type Endpoint, probe } from './server'
import { SshTunnel, type TunnelOptions } from './ssh-tunnel'

export class RemoteMachine {
  /** The tunnel to its Atlas, for an `ssh` machine. */
  private connection: null | SshTunnel = null
  /** The previews' tunnels, by the port on that machine. */
  private readonly sites = new Map<number, SshTunnel>()

  /** `tunnel`: more of each tunnel's options (the real-sshd test's config file). */
  constructor(
    readonly machine: Machine,
    private readonly token: () => string,
    private readonly log: (line: string) => void,
    private readonly tunnel: Pick<TunnelOptions, 'configFile'> = {},
    private readonly relayKey: () => string = () => ''
  ) {}

  /** Its address and token, the tunnel opened first for an `ssh` machine, both legs probed. A
   * `relay` machine's is the relay, its key in the address the window's client reads (step 20);
   * the tunnel proves the far end is the home when the window connects. */
  async endpoint(): Promise<Endpoint> {
    const { relay, transport } = this.machine

    if (transport === 'relay' && relay) {
      return { token: this.token(), url: relayUrl({ key: this.relayKey(), room: relay.room, url: relay.url }) }
    }

    const base = await this.base()
    const endpoint = { token: this.token(), url: `${base.replace(/^http/, 'ws')}/ws` }
    await sameProtocol(base, this.machine.name)
    await probe(base, endpoint)

    return endpoint
  }

  close(): void {
    this.connection?.close()
    this.connection = null

    for (const site of this.sites.values()) {
      site.close()
    }

    this.sites.clear()
  }

  /** A local address of that machine's, as this one reaches it, or why it cannot. */
  async reach(url: string): Promise<{ error: string } | { url: string }> {
    const address = localAddress(url)

    if (!address) {
      return { url }
    }

    if (this.machine.transport === 'relay') {
      return {
        error: `${url} is on ${this.machine.name}: open it there, or add ${this.machine.name} by SSH to see it here.`
      }
    }

    if (this.machine.transport !== 'ssh') {
      return {
        error: `${url} is on ${this.machine.name}: open it there, or add ${this.machine.name} by SSH to see it here.`
      }
    }

    let site = this.sites.get(address.port)

    if (!site) {
      site = new SshTunnel({
        ...this.tunnel,
        host: this.machine.sshHost ?? '',
        log: this.log,
        remotePort: address.port
      })
      this.sites.set(address.port, site)
    }

    address.url.hostname = '127.0.0.1'
    address.url.port = String(await site.port())

    return { url: address.url.toString() }
  }

  private async base(): Promise<string> {
    const { host, port, sshHost, transport } = this.machine

    if (transport === 'tailscale') {
      const named = host?.includes(':') ? `[${host}]` : (host ?? '')

      return `http://${named}:${port}`
    }

    this.connection ??= new SshTunnel({ ...this.tunnel, host: sshHost ?? '', log: this.log, remotePort: port })

    return `http://127.0.0.1:${await this.connection.port()}`
  }
}

/** A web address on the machine's own loopback, and its port; `null` for any other. */
export function localAddress(url: string): null | { port: number; url: URL } {
  let parsed: URL

  try {
    parsed = new URL(url)
  } catch {
    return null
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    return null
  }

  if (!['localhost', '127.0.0.1', '0.0.0.0', '[::1]'].includes(parsed.hostname)) {
    return null
  }

  const port = Number(parsed.port || (parsed.protocol === 'https:' ? 443 : 80))

  return { port, url: parsed }
}

/** Refuse a server that speaks another protocol, saying which side to update. */
export async function sameProtocol(base: string, name: string): Promise<void> {
  let told: unknown

  try {
    const health = await fetch(`${base}/health`, { signal: AbortSignal.timeout(10_000) })
    told = await health.json()
  } catch (error) {
    throw new Error(`${name} did not answer: ${error instanceof Error ? error.message : String(error)}`, {
      cause: error
    })
  }

  const theirs = (told as { protocol?: unknown }).protocol

  if (typeof theirs !== 'string' || theirs === PROTOCOL) {
    // An Atlas from before step 15 says no protocol; the WebSocket's own refusal decides then.
    return
  }

  const number = (protocol: string) => Number(protocol.replace(/^atlas\.v/, ''))
  throw new Error(
    number(theirs) > number(PROTOCOL)
      ? `${name} runs a newer Atlas (${theirs}); update this app.`
      : `${name} runs an older Atlas (${theirs}); update the Atlas there.`
  )
}
