/**
 * A quick look for web servers on this machine, for the browser's empty page: a request to a few
 * ports of `127.0.0.1`, each with a short deadline. Nothing leaves the machine, and nothing but the
 * ports that answered goes back to the window.
 */
import http from 'node:http'

/** The ports development servers favour. */
export const COMMON_PORTS = [3000, 3001, 4200, 5000, 5173, 8000, 8080]
/** How many more ports the window may ask about (the addresses a conversation showed). */
const MAX_EXTRA = 20
/** How long a port has to answer. */
export const LOOK_TIMEOUT_MS = 700

/** The ports the window asked about, or `null` when it is not a short list of valid ports. */
export function parsePorts(value: unknown): null | number[] {
  if (!Array.isArray(value) || value.length > MAX_EXTRA) {
    return null
  }

  const ports: number[] = []

  for (const port of value as unknown[]) {
    if (typeof port !== 'number' || !Number.isInteger(port) || port < 1 || port > 65_535) {
      return null
    }

    ports.push(port)
  }

  return ports
}

/** The common ports and the window's own, each once. */
export function portsToLook(extra: number[]): number[] {
  return [...new Set([...COMMON_PORTS, ...extra])]
}

/** Whether a web server answers on `port` of `127.0.0.1`: any status counts, a refusal or a silence
 * does not. */
function answers(port: number, timeout: number): Promise<boolean> {
  return new Promise(resolve => {
    const request = http.request({ host: '127.0.0.1', method: 'HEAD', path: '/', port, timeout }, response => {
      response.resume()
      resolve(true)
    })
    request.on('timeout', () => {
      request.destroy()
      resolve(false)
    })
    request.on('error', () => {
      resolve(false)
    })
    request.end()
  })
}

/** The ports among `ports` that answer, in the order given. */
export async function lookForServers(ports: number[], timeout = LOOK_TIMEOUT_MS): Promise<number[]> {
  const found = await Promise.all(ports.map(async port => ((await answers(port, timeout)) ? port : null)))

  return found.filter((port): port is number => port !== null)
}
