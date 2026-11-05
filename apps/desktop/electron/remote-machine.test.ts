import { createServer, type Server } from 'node:http'

import { afterEach, describe, expect, it } from 'vitest'

import { PROTOCOL } from '../src/protocol/protocol.generated'
import { readRelayUrl } from '../src/protocol/relay-address'

import { localAddress, RemoteMachine, sameProtocol } from './remote-machine'

const servers: Server[] = []

afterEach(() => {
  for (const server of servers.splice(0)) {
    server.close()
  }
})

/** A server whose /health says `body`. */
async function health(body: unknown): Promise<string> {
  const server = createServer((_request, response) => {
    response.setHeader('content-type', 'application/json')
    response.end(JSON.stringify(body))
  })
  servers.push(server)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()

  return `http://127.0.0.1:${typeof address === 'object' && address ? address.port : 0}`
}

describe("the other machine's protocol", () => {
  const number = Number(PROTOCOL.replace('atlas.v', ''))

  it('lets the same one, and one from before step 15, through', async () => {
    await sameProtocol(await health({ protocol: PROTOCOL, status: 'ok' }), 'Shop')
    await sameProtocol(await health({ status: 'ok' }), 'Shop')
  })

  it('says which side to update', async () => {
    await expect(sameProtocol(await health({ protocol: `atlas.v${number + 1}` }), 'Shop')).rejects.toThrow(
      /Shop runs a newer Atlas .*; update this app/
    )
    await expect(sameProtocol(await health({ protocol: `atlas.v${number - 1}` }), 'Shop')).rejects.toThrow(
      /Shop runs an older Atlas .*; update the Atlas there/
    )
  })

  it('says when the machine does not answer', async () => {
    const base = await health({})
    servers.splice(0).forEach(server => server.close())
    await new Promise(resolve => setTimeout(resolve, 100))

    await expect(sameProtocol(base, 'Shop')).rejects.toThrow(/Shop did not answer/)
  })
})

describe('a site up on the other machine, for the preview', () => {
  const shop = (transport: 'ssh' | 'tailscale') =>
    new RemoteMachine(
      {
        home: 'default',
        host: transport === 'tailscale' ? '100.101.102.103' : null,
        id: 'm1',
        name: 'Shop',
        port: 18800,
        sshHost: transport === 'ssh' ? 'me@shop' : null,
        transport
      },
      () => 'token',
      () => undefined
    )

  it('is an address on its loopback, with its port', () => {
    expect(localAddress('http://localhost:3000/app?x=1')?.port).toBe(3000)
    expect(localAddress('http://0.0.0.0:5173')?.port).toBe(5173)
    expect(localAddress('https://127.0.0.1')?.port).toBe(443)
    expect(localAddress('http://[::1]:8080/')?.port).toBe(8080)
    expect(localAddress('https://example.com:3000')).toBeNull()
    expect(localAddress('file:///etc/passwd')).toBeNull()
    expect(localAddress('not a url')).toBeNull()
  })

  it("opens as it is when it is not local, and over Tailscale says it is that machine's", async () => {
    expect(await shop('tailscale').reach('https://example.com/')).toEqual({ url: 'https://example.com/' })
    const said = await shop('tailscale').reach('http://localhost:3000/')

    expect('error' in said && said.error).toMatch(/is on Shop: open it there, or add Shop by SSH/)
  })
})

describe('a machine through a relay (step 20)', () => {
  it("is reached at the relay, its key in the address the window's client reads, nothing probed", async () => {
    const key = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8'
    const shop = new RemoteMachine(
      {
        home: 'default',
        host: null,
        id: 'm1',
        name: 'Shop',
        port: 18800,
        relay: { room: 'room-1', url: 'wss://relay.example.com' },
        sshHost: null,
        transport: 'relay'
      },
      () => 'token',
      () => undefined,
      {},
      () => key
    )

    const endpoint = await shop.endpoint()

    expect(endpoint.token).toBe('token')
    expect(readRelayUrl(endpoint.url)).toEqual({ key, room: 'room-1', url: 'wss://relay.example.com' })
    expect(await shop.reach('http://localhost:3000')).toHaveProperty('error')
  })
})
