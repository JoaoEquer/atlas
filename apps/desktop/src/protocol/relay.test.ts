import { describe, expect, it } from 'vitest'

import { readRelayUrl, RelaySocket, relayUrl, Tunnel } from './relay'

const KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8'
const APP_NONCE = Uint8Array.from({ length: 16 }, (_, index) => index)
const HOME_NONCE = Uint8Array.from({ length: 16 }, (_, index) => index + 16)

const b64 = (data: Uint8Array) => btoa(String.fromCharCode(...data))
const text = (value: string) => new TextEncoder().encode(value)

describe('the tunnel through the relay (step 20)', () => {
  it("seals exactly as the home's Python does, so the two ends read each other", async () => {
    // Printed by atlas.instances.tunnel with the same key and nonces.
    const app = await Tunnel.agree(KEY, APP_NONCE, HOME_NONCE, 'app')
    const home = await Tunnel.agree(KEY, APP_NONCE, HOME_NONCE, 'home')

    expect(b64(await home.seal(text('ready')))).toBe('yCe6Cq63ZvuGk3EdqUiwR+vZcv6Q')
    expect(b64(await app.seal(text('ready')))).toBe('KjwBDK2z5UY+0x9iCK5UfBbiMl2L')
    expect(b64(await app.seal(text('{"method":"routine.list"}')))).toBe(
      'RRJ4vyesI096jSyB/p5Z+TH55fU3VvoBOGHCwWcZIv6zQ4KOkca8Ky4='
    )
  })

  it('refuses a frame sealed with another key, or played again', async () => {
    const app = await Tunnel.agree(KEY, APP_NONCE, HOME_NONCE, 'app')
    const home = await Tunnel.agree(KEY, APP_NONCE, HOME_NONCE, 'home')
    const sealed = await app.seal(text('one'))

    expect(new TextDecoder().decode(await home.open(sealed))).toBe('one')
    await expect(home.open(sealed)).rejects.toThrow('did not open')
  })

  it('keeps the relay address in the endpoint URL', () => {
    const relay = { key: KEY, room: 'room-1', url: 'wss://relay.example.com' }
    expect(readRelayUrl(relayUrl(relay))).toEqual(relay)
  })
})

/** The relay's socket as the test plays it: what the app sends, and what the home answers. */
class FakeSocket extends EventTarget {
  binaryType = 'blob'
  readonly sent: Uint8Array[] = []

  constructor(readonly url: string) {
    super()
  }

  send(data: Uint8Array): void {
    this.sent.push(data)
  }

  close(code = 1000): void {
    this.dispatchEvent(Object.assign(new Event('close'), { code }))
  }

  deliver(data: Uint8Array): void {
    this.dispatchEvent(
      new MessageEvent('message', { data: data.buffer.slice(data.byteOffset, data.byteOffset + data.length) })
    )
  }
}

/** Wait until `done` holds: the tunnel's WebCrypto answers in its own time. */
async function until(done: () => boolean): Promise<void> {
  for (let tries = 0; tries < 200 && !done(); tries += 1) {
    await new Promise(resolve => setTimeout(resolve, 10))
  }
}

describe("the app's end of a connection through the relay", () => {
  it('opens once the home proved it holds the key, then carries the protocol sealed', async () => {
    let socket: FakeSocket | undefined
    const relay = new RelaySocket({ key: KEY, room: 'room-1', url: 'wss://relay.example.com/' }, url => {
      socket = new FakeSocket(url)

      return socket as unknown as WebSocket
    })
    const heard: string[] = []
    let opened = false
    relay.addEventListener('open', () => (opened = true))
    relay.addEventListener('message', event => heard.push(String((event as MessageEvent).data)))
    const fake = socket as FakeSocket
    expect(fake.url).toBe('wss://relay.example.com/room-1/app')
    fake.dispatchEvent(new Event('open'))
    const appNonce = fake.sent[0]?.slice(4) ?? new Uint8Array()
    const home = await Tunnel.agree(KEY, appNonce, HOME_NONCE, 'home')
    fake.deliver(new Uint8Array([...text('ATR1'), ...HOME_NONCE, ...(await home.seal(text('ready')))]))
    await until(() => opened && fake.sent.length > 1)

    expect(opened).toBe(true)
    expect(new TextDecoder().decode(await home.open(fake.sent[1] ?? new Uint8Array()))).toBe('ready')
    relay.send('{"method":"routine.list"}')
    await until(() => fake.sent.length > 2)
    expect(new TextDecoder().decode(await home.open(fake.sent[2] ?? new Uint8Array()))).toBe(
      '{"method":"routine.list"}'
    )
    fake.deliver(await home.seal(text('{"event":"server.ready"}')))
    await until(() => heard.length > 0)
    expect(heard).toEqual(['{"event":"server.ready"}'])
  })

  it('closes when the far end cannot prove it holds the key', async () => {
    let socket: FakeSocket | undefined
    const relay = new RelaySocket({ key: KEY, room: 'room-1', url: 'wss://relay.example.com' }, url => {
      socket = new FakeSocket(url)

      return socket as unknown as WebSocket
    })
    let closed = 0
    relay.addEventListener('close', event => (closed = (event as CloseEvent).code))
    const fake = socket as FakeSocket
    fake.dispatchEvent(new Event('open'))
    const stranger = await Tunnel.agree(
      'BwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwcHBwc',
      new Uint8Array(16),
      HOME_NONCE,
      'home'
    )
    fake.deliver(new Uint8Array([...text('ATR1'), ...HOME_NONCE, ...(await stranger.seal(text('ready')))]))
    await until(() => closed !== 0)

    expect(closed).toBe(1008)
  })
})
