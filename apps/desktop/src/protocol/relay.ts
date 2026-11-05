/**
 * A home reached through a relay (step 20), the app's end: the window dials out to the relay as the
 * home does, and only the two of them read what crosses it (`atlas.instances.tunnel`, the same
 * here with WebCrypto).
 *
 * - **The key** comes in the connection code and never reaches the relay. Each connection agrees
 *   its own keys from it: this end sends a fresh nonce, the home answers with its own, and
 *   HKDF-SHA256 salted with both gives one AES-256-GCM key per direction.
 * - **Each frame is sealed**, its nonce the frame's number in its direction; a frame that does not
 *   open closes the connection.
 * - **Each end proves it holds the key** with a sealed `ready`, the home first.
 *
 * `RelaySocket` is a WebSocket to the client (`client.ts`'s `socketFactory`): it opens once the
 * keys are agreed, and carries the protocol's text frames sealed.
 */

const HELLO = new TextEncoder().encode('ATR1')
const NONCE_BYTES = 16
const READY = 'ready'

export { readRelayUrl, RELAY_SCHEME, type RelayAddress, relayUrl } from './relay-address'
import type { RelayAddress } from './relay-address'

/** One connection's two keys, from one side: it seals what it sends and opens what it receives. */
export class Tunnel {
  private sent = 0n
  private received = 0n

  private constructor(
    private readonly sending: CryptoKey,
    private readonly receiving: CryptoKey
  ) {}

  static async agree(key: string, appNonce: Uint8Array, homeNonce: Uint8Array, side: 'app' | 'home'): Promise<Tunnel> {
    const base = await crypto.subtle.importKey('raw', bytes(fromBase64url(key)), 'HKDF', false, ['deriveKey'])
    const salt = new Uint8Array([...appNonce, ...homeNonce])
    const derive = (who: string) =>
      crypto.subtle.deriveKey(
        { hash: 'SHA-256', info: new TextEncoder().encode(`atlas relay ${who}`), name: 'HKDF', salt },
        base,
        { length: 256, name: 'AES-GCM' },
        false,
        ['encrypt', 'decrypt']
      )
    const other = side === 'app' ? 'home' : 'app'

    return new Tunnel(await derive(side), await derive(other))
  }

  async seal(data: Uint8Array): Promise<Uint8Array> {
    const iv = counter(this.sent++)

    return new Uint8Array(await crypto.subtle.encrypt({ iv, name: 'AES-GCM' }, this.sending, bytes(data)))
  }

  async open(data: Uint8Array): Promise<Uint8Array> {
    const iv = counter(this.received++)

    try {
      return new Uint8Array(await crypto.subtle.decrypt({ iv, name: 'AES-GCM' }, this.receiving, bytes(data)))
    } catch {
      throw new Error(
        "A frame through the relay did not open: the code's key differs, or the relay changed what it carried."
      )
    }
  }
}

/** The app's end of a connection through the relay, as the client sees a WebSocket. */
export class RelaySocket extends EventTarget {
  private socket: WebSocket
  private tunnel: null | Tunnel = null
  /** Frames are opened and sealed one after another, in their order. */
  private queue: Promise<void> = Promise.resolve()
  private closed = false

  constructor(relay: RelayAddress, connect: (url: string) => WebSocket = url => new WebSocket(url)) {
    super()
    this.socket = connect(`${relay.url.replace(/\/$/, '')}/${relay.room}/app`)
    this.socket.binaryType = 'arraybuffer'
    const appNonce = crypto.getRandomValues(new Uint8Array(NONCE_BYTES))
    this.socket.addEventListener('open', () => {
      this.socket.send(bytes(new Uint8Array([...HELLO, ...appNonce])))
    })
    this.socket.addEventListener('message', ({ data }) => {
      this.queue = this.queue
        .then(() => this.received(data, relay.key, appNonce))
        .catch((error: unknown) => {
          this.fail(error)
        })
    })
    this.socket.addEventListener('close', ({ code }) => {
      this.end(code)
    })
    this.socket.addEventListener('error', () => {
      this.dispatchEvent(new Event('error'))
    })
  }

  send(text: string): void {
    const tunnel = this.tunnel

    if (!tunnel) {
      throw new Error('The connection through the relay is not open yet.')
    }

    this.queue = this.queue.then(async () => {
      this.socket.send(bytes(await tunnel.seal(new TextEncoder().encode(text))))
    })
  }

  close(code = 1000): void {
    this.socket.close(code)
  }

  private async received(data: unknown, key: string, appNonce: Uint8Array): Promise<void> {
    // By its tag, not `instanceof`: a buffer may come from another realm (a test's, a worker's).
    if (Object.prototype.toString.call(data) !== '[object ArrayBuffer]') {
      throw new Error('The relay carried text where a sealed frame belongs.')
    }

    const frame = new Uint8Array(data as ArrayBuffer)

    if (this.tunnel) {
      const text = new TextDecoder().decode(await this.tunnel.open(frame))
      this.dispatchEvent(new MessageEvent('message', { data: text }))

      return
    }

    if (!startsWith(frame, HELLO) || frame.length < HELLO.length + NONCE_BYTES) {
      throw new Error('The other end does not speak this tunnel: is it an Atlas?')
    }

    const homeNonce = frame.slice(HELLO.length, HELLO.length + NONCE_BYTES)
    const tunnel = await Tunnel.agree(key, appNonce, homeNonce, 'app')

    if (new TextDecoder().decode(await tunnel.open(frame.slice(HELLO.length + NONCE_BYTES))) !== READY) {
      throw new Error('The far end of the relay is not your home.')
    }

    this.socket.send(bytes(await tunnel.seal(new TextEncoder().encode(READY))))
    this.tunnel = tunnel
    this.dispatchEvent(new Event('open'))
  }

  private fail(error: unknown): void {
    this.dispatchEvent(Object.assign(new Event('error'), { message: String(error) }))
    this.socket.close(1008)
  }

  private end(code: number): void {
    if (!this.closed) {
      this.closed = true
      this.dispatchEvent(Object.assign(new Event('close'), { code }))
    }
  }
}

function counter(value: bigint): Uint8Array<ArrayBuffer> {
  const iv = new Uint8Array(12)
  new DataView(iv.buffer).setBigUint64(4, value)

  return iv
}

function startsWith(data: Uint8Array, head: Uint8Array): boolean {
  return head.every((byte, index) => data[index] === byte)
}

/** A copy backed by its own ArrayBuffer, as WebCrypto and WebSocket take. */
function bytes(data: Uint8Array): Uint8Array<ArrayBuffer> {
  return new Uint8Array(data)
}

function fromBase64url(text: string): Uint8Array {
  const plain = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4))

  return Uint8Array.from(plain, char => char.charCodeAt(0))
}
