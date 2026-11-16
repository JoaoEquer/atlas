import { describe, expect, it } from 'vitest'

import { conversationPorts, loopbackPort, mergeLocalServers } from './local-servers'

describe("the browser's local servers", () => {
  it("reads the port of an address on this machine, and of no other's", () => {
    expect(loopbackPort('http://localhost:3000/app')).toBe(3000)
    expect(loopbackPort('http://127.0.0.1:5173')).toBe(5173)
    expect(loopbackPort('http://0.0.0.0:8000/')).toBe(8000)
    expect(loopbackPort('http://localhost/')).toBe(80)
    expect(loopbackPort('https://localhost/')).toBe(443)
    expect(loopbackPort('https://example.com:3000/')).toBeNull()
    expect(loopbackPort('http://192.168.0.2:3000/')).toBeNull()
    expect(loopbackPort('file:///etc/passwd')).toBeNull()
    expect(loopbackPort('not an address')).toBeNull()
  })

  it("names each port the conversation showed once, whatever the host's spelling", () => {
    expect(
      conversationPorts([
        'http://localhost:3000/a',
        'http://127.0.0.1:3000/b',
        'http://0.0.0.0:4321/',
        'https://example.com:9999/'
      ])
    ).toEqual([3000, 4321])
  })

  it('lists the answering ports, the conversation first, by latest mention, and the rest by port, each once', () => {
    const merged = mergeLocalServers(
      ['http://localhost:5173/', 'http://127.0.0.1:4321/docs', 'http://localhost:5173/other'],
      [8080, 3000, 5173, 4321]
    )

    expect(merged).toEqual([
      { port: 4321, source: 'conversation', url: 'http://127.0.0.1:4321/docs' },
      { port: 5173, source: 'conversation', url: 'http://localhost:5173/other' },
      { port: 3000, source: 'scan', url: 'http://localhost:3000/' },
      { port: 8080, source: 'scan', url: 'http://localhost:8080/' }
    ])
  })

  it('leaves out a conversation address whose port did not answer, and lists nothing for no answer', () => {
    expect(mergeLocalServers(['http://localhost:3000/'], [])).toEqual([])
    expect(mergeLocalServers(['http://localhost:3000/'], [8000])).toEqual([
      { port: 8000, source: 'scan', url: 'http://localhost:8000/' }
    ])
  })

  it('writes this machine for 0.0.0.0, which is no address to browse', () => {
    expect(mergeLocalServers(['http://0.0.0.0:8000/x'], [8000])).toEqual([
      { port: 8000, source: 'conversation', url: 'http://localhost:8000/x' }
    ])
  })
})
