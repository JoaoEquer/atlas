/**
 * The app browser's rules (docs/GLOSSARY.md, `app browser`), pure: what an address is, which pages
 * a view may load, what a site may ask of the user's machine, and what the window may ask of the
 * browser. `app-browser.ts` applies them to the views; nothing here touches Electron.
 */

import { createHash } from 'node:crypto'

import { browserHome } from '../src/browser-home'

/**
 * The partition of a home's browser tabs: its cookies and logins are its own, not the window's, and
 * not another home's. The key is a hash of the machine and the home's name, so the name the user
 * chose (it may hold anything) never becomes a folder's name and no path enters it.
 */
export function partitionOf(machine: string, home: string): string {
  return partitionOfKey(browserHome(machine, home))
}

/** The partition of a home named as `browserHome` writes it. */
export function partitionOfKey(key: string): string {
  return `persist:atlas-browser-${createHash('sha256').update(key).digest('hex').slice(0, 16)}`
}

/** What a site may ask for and be given. Everything else (camera, microphone, location,
 * notifications, MIDI, USB, full screen…) is refused: the user never asked a site for them here. */
const GRANTED = new Set(['clipboard-sanitized-write'])

/** Whether a site's permission request is granted. */
export function permissionGranted(permission: string): boolean {
  return GRANTED.has(permission)
}

/** Only web pages load in a tab: never `file:`, `javascript:`, `data:`, `devtools:` or one of the
 * app's own schemes (`atlas-preview:`). `about:blank` is how a tab starts. */
export function pageAllowed(url: string): boolean {
  if (url === 'about:blank') {
    return true
  }

  try {
    const { protocol } = new URL(url)

    return protocol === 'http:' || protocol === 'https:'
  } catch {
    return false
  }
}

const LOCAL_HOST = /^(localhost|\[::1\]|127(\.\d{1,3}){3})(:\d+)?([/?#].*)?$/i
const HOST_LIKE = /^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(:\d+)?([/?#].*)?$/i
const ADDRESS_LIKE = /^(\d{1,3}(\.\d{1,3}){3})(:\d+)?([/?#].*)?$/
const WEB_ADDRESS = /^https?:\/\/\S+$/i

/**
 * What the user typed in the address bar, as an address to load, or `null` when it is not one. A
 * full `http(s)` address stays; a bare host gets `https://` (`http://` for this machine's own
 * addresses, where a dev server rarely has a certificate). Anything with another scheme, or words
 * that are no host, is no address: nothing is sent to a search engine on the user's behalf.
 */
export function normalizeAddress(typed: string): null | string {
  const text = typed.trim()
  let address: null | string = null

  if (LOCAL_HOST.test(text) || ADDRESS_LIKE.test(text)) {
    address = `http://${text}`
  } else if (HOST_LIKE.test(text)) {
    address = `https://${text}`
  } else if (WEB_ADDRESS.test(text)) {
    address = text
  }

  try {
    return address !== null && pageAllowed(address) ? new URL(address).href : null
  } catch {
    return null
  }
}

/** What the window asks of the browser. `owner` is what the panel shows tabs of (the conversation
 * on screen, `''` for a new one not made yet) and `home` the home on screen. */
export type BrowserCommand =
  | { command: 'adopt'; from: string; home: string; to: string }
  | { command: 'back' | 'forward' | 'reload' | 'stop'; id: string }
  | { command: 'close' | 'select'; id: string }
  | { command: 'go'; home: string; id: null | string; owner: string; text: string }
  | { command: 'open'; home: string; owner: string }
  | { command: 'show'; rect: null | Rect }

/** Where the browser's view is, in the window's coordinates. */
export interface Rect {
  height: number
  width: number
  x: number
  y: number
}

const COMMANDS_WITH_ID = new Set(['back', 'close', 'forward', 'reload', 'select', 'stop'])
/** An address longer than this is no address a person typed. */
const MAX_TEXT = 4096
/** A window larger than this does not exist; a bigger number is a bug or a lie. */
const MAX_PIXELS = 20_000
/** A conversation's id or a home's name is short; a longer one is a bug or a lie. */
const MAX_NAME = 256

function isName(value: unknown): value is string {
  return typeof value === 'string' && value.length <= MAX_NAME
}

function isRect(value: unknown): value is Rect {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const rect = value as Record<string, unknown>

  return ['height', 'width', 'x', 'y'].every(key => {
    const number = rect[key]

    return typeof number === 'number' && Number.isFinite(number) && Math.abs(number) <= MAX_PIXELS
  })
}

/** What came over the bridge, as a command, or `null` when it is not one the browser knows: the
 * window is trusted no further than its arguments' shape. */
export function parseCommand(value: unknown): BrowserCommand | null {
  if (typeof value !== 'object' || value === null) {
    return null
  }

  const given = value as Record<string, unknown>
  const command = given.command

  if (typeof command !== 'string') {
    return null
  }

  if (COMMANDS_WITH_ID.has(command)) {
    return typeof given.id === 'string' && given.id ? ({ command, id: given.id } as BrowserCommand) : null
  }

  if (command === 'open') {
    return isName(given.owner) && isName(given.home) ? { command, home: given.home, owner: given.owner } : null
  }

  if (command === 'adopt') {
    return isName(given.from) && isName(given.to) && isName(given.home)
      ? { command, from: given.from, home: given.home, to: given.to }
      : null
  }

  if (command === 'go') {
    const id = given.id === null || given.id === undefined ? null : given.id

    if (
      typeof given.text !== 'string' ||
      given.text.length > MAX_TEXT ||
      (id !== null && typeof id !== 'string') ||
      !isName(given.owner) ||
      !isName(given.home)
    ) {
      return null
    }

    return { command, home: given.home, id, owner: given.owner, text: given.text }
  }

  if (command === 'show') {
    if (given.rect === null) {
      return { command, rect: null }
    }

    return isRect(given.rect) ? { command, rect: given.rect } : null
  }

  return null
}

// -- what the agent may do through the server (the browser host) ------------------------------------

/** The DevTools domains a page may be driven through. What is not here (`Target`, `Browser`,
 * `Storage`, `IO`, tracing, profiling…) reaches past the page, and so do `DOMStorage`, `IndexedDB`
 * and `CacheStorage`, which take any origin. The server keeps the same list
 * (`atlas/browser/model.py`), and both tests hold the same table. */
const CDP_DOMAINS = new Set([
  'Accessibility',
  'Animation',
  'Audits',
  'CSS',
  'Console',
  'DOM',
  'DOMDebugger',
  'DOMSnapshot',
  'Debugger',
  'Emulation',
  'Fetch',
  'Input',
  'Inspector',
  'Log',
  'Network',
  'Overlay',
  'Page',
  'Performance',
  'Runtime',
  'Security'
])
/** Methods of an allowed domain that stay refused, since they reach past the page: one writes files
 * wherever it likes; one closes the view behind the browser's back (a tab closes through the
 * browser's own command); the cookies and the cache are the home's partition's, every site's and
 * every conversation's (`getCookies` takes any URL); and certificate errors would be ignored for
 * every page. */
const CDP_REFUSED = new Set([
  'Page.setDownloadBehavior',
  'Page.close',
  'Network.getAllCookies',
  'Network.getCookies',
  'Network.setCookie',
  'Network.setCookies',
  'Network.deleteCookies',
  'Network.clearBrowserCookies',
  'Network.clearBrowserCache',
  'Security.setIgnoreCertificateErrors',
  'Security.setOverrideCertificateErrors',
  'Security.handleCertificateError'
])

/** Whether a DevTools command may reach a page's debugger. */
export function cdpAllowed(method: string): boolean {
  const domain = method.split('.', 1)[0] ?? ''

  return method.includes('.') && CDP_DOMAINS.has(domain) && !CDP_REFUSED.has(method)
}

const METADATA_HOSTS = new Set(['metadata.google.internal', 'metadata.goog', '100.100.100.200', '[fd00:ec2::254]'])

/**
 * Whether a request of a page goes to a cloud metadata address or to the link-local range, by name
 * or by number: refused whatever started it (a click, a redirect, the page's own script, or the
 * agent). The URL's own parser has written numbers in their usual form by then.
 */
export function blockedRequest(url: string): boolean {
  let host: string

  try {
    host = new URL(url).hostname.toLowerCase().replace(/\.$/, '')
  } catch {
    return false
  }

  if (METADATA_HOSTS.has(host)) {
    return true
  }

  if (/^169\.254\.\d{1,3}\.\d{1,3}$/.test(host)) {
    return true
  }

  // An IPv4 address written as IPv6 (`::ffff:a9fe:a9fe`), which the parser keeps in hex.
  const mapped = /^\[::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})\]$/.exec(host)

  return mapped !== null && Number.parseInt(mapped[1] ?? '', 16) === 0xa9fe
}

/** What the server asks of the browser, once its shape is checked. */
export type HostRequest =
  | { id: string; kind: 'attach' | 'close' | 'detach' | 'show' }
  | { id: string; kind: 'cdp'; method: string; params: Record<string, unknown> }
  | { background: boolean; kind: 'open'; owner: string; url: string }
  | { kind: 'tabs' }

/** What the server asked, as a request, or `null` when it is not one the browser knows. The server
 * is trusted no further than its arguments' shape. */
export function parseHostRequest(method: string, value: unknown): HostRequest | null {
  const given = (typeof value === 'object' && value !== null ? value : {}) as Record<string, unknown>
  const id = given.id

  switch (method) {
    case 'browser.tabs': {
      return { kind: 'tabs' }
    }

    case 'browser.attach':
    case 'browser.close':
    case 'browser.detach':
    case 'browser.show': {
      return typeof id === 'string' && id
        ? { id, kind: method.slice('browser.'.length) as 'attach' | 'close' | 'detach' | 'show' }
        : null
    }

    case 'browser.open': {
      return isName(given.owner) && typeof given.url === 'string' && given.url.length <= MAX_TEXT
        ? { background: given.background !== false, kind: 'open', owner: given.owner, url: given.url }
        : null
    }

    case 'browser.cdp': {
      const params: unknown = given.params ?? {}

      return typeof id === 'string' &&
        id &&
        typeof given.method === 'string' &&
        typeof params === 'object' &&
        params !== null &&
        !Array.isArray(params)
        ? { id, kind: 'cdp', method: given.method, params: params as Record<string, unknown> }
        : null
    }

    default: {
      return null
    }
  }
}

/** Whether this is the first page of the agent's in a conversation (of a home) since the app
 * opened: the one that brings the panel forward. `seen` is the app's own record. */
export function firstAgentPage(seen: Set<string>, home: string, owner: string): boolean {
  const key = JSON.stringify([home, owner])

  if (seen.has(key)) {
    return false
  }

  seen.add(key)

  return true
}
