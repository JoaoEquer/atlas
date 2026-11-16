/**
 * The local servers the browser's empty page offers (docs/GLOSSARY.md, `local server`): the
 * addresses of this machine a conversation already mentioned, joined with the ports a quick look
 * found answering. Pure: the look itself is the main process's (electron/local-servers.ts).
 */

/** A server on this machine that answers, as the empty page lists it. */
export interface LocalServer {
  port: number
  /** Where it came from: an address the conversation showed, or the look at the common ports. */
  source: 'conversation' | 'scan'
  url: string
}

const LOOPBACK = new Set(['localhost', '127.0.0.1', '0.0.0.0', '[::1]'])

/** The port of an address on this machine, or `null` for any other address. */
export function loopbackPort(address: string): null | number {
  try {
    const url = new URL(address)

    if ((url.protocol !== 'http:' && url.protocol !== 'https:') || !LOOPBACK.has(url.hostname)) {
      return null
    }

    return Number(url.port || (url.protocol === 'https:' ? 443 : 80))
  } catch {
    return null
  }
}

/** The ports the conversation's addresses name, each once. */
export function conversationPorts(addresses: string[]): number[] {
  return [...new Set(addresses.map(loopbackPort).filter((port): port is number => port !== null))]
}

/**
 * The servers to list: those whose port answered. A port the conversation named keeps the address
 * it was named by, the latest one when several (a path may matter); the others are the plain
 * address of this machine. The conversation's come first, the latest shown last, then the
 * rest by port; one port is one entry whatever the host's spelling.
 */
export function mergeLocalServers(addresses: string[], answering: number[]): LocalServer[] {
  const alive = new Set(answering)
  const named = new Map<number, string>()

  for (const address of addresses) {
    const port = loopbackPort(address)

    if (port !== null && alive.has(port)) {
      named.delete(port)
      named.set(port, address.replace('0.0.0.0', 'localhost'))
    }
  }

  const fromConversation = [...named].map(([port, url]): LocalServer => ({ port, source: 'conversation', url }))
  const scanned = [...alive]
    .filter(port => !named.has(port))
    .sort((a, b) => a - b)
    .map((port): LocalServer => ({ port, source: 'scan', url: `http://localhost:${String(port)}/` }))

  return [...fromConversation, ...scanned]
}
