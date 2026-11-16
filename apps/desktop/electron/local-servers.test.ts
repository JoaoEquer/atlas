import http from 'node:http'
import net from 'node:net'

import { afterEach, describe, expect, it } from 'vitest'

import { COMMON_PORTS, lookForServers, parsePorts, portsToLook } from './local-servers'

const open: { close: () => void }[] = []

afterEach(() => {
  for (const each of open.splice(0)) {
    each.close()
  }
})

function listen(server: net.Server): Promise<number> {
  open.push(server)

  return new Promise(resolve => {
    server.listen(0, '127.0.0.1', () => {
      resolve((server.address() as net.AddressInfo).port)
    })
  })
}

describe('the look for local servers', () => {
  it('finds a web server, whatever it answers, and not a closed port nor a silent one', async () => {
    const web = await listen(http.createServer((_request, response) => response.writeHead(404).end()))
    // Accepts the connection and never answers, as a database or a hung server would.
    const silent = await listen(net.createServer(() => undefined))
    const closed = await listen(net.createServer())
    open[open.length - 1]?.close()

    expect(await lookForServers([closed, silent, web], 150)).toEqual([web])
  })

  it('takes a short list of valid ports from the window, and nothing else', () => {
    expect(parsePorts([3000, 8080])).toEqual([3000, 8080])
    expect(parsePorts([])).toEqual([])

    for (const value of [
      null,
      '3000',
      [0],
      [65_536],
      [1.5],
      ['3000'],
      [Number.NaN],
      Array.from({ length: 21 }, (_, index) => index + 1)
    ]) {
      expect(parsePorts(value), JSON.stringify(value)).toBeNull()
    }
  })

  it("looks at the common ports and the window's, each once", () => {
    expect(portsToLook([5173, 4321])).toEqual([...COMMON_PORTS, 4321])
  })
})
