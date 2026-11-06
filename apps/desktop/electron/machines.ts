/**
 * The machine list (step 15, docs/ARCHITECTURE.md, Several machines): the other machines' Atlas this
 * app opens, each a name, a transport and the home it serves, read from a connection code.
 *
 * Its rules:
 *
 * - **Names are unique**, case aside ("Shop" and "shop" cannot both be), up to 64 characters, and
 *   one machine is never saved twice (the same address, port and home; for `ssh`, the same host).
 * - **The list holds no secret.** `machines.json` keeps names and addresses; the tokens sit apart
 *   in `machine-tokens.json`, encrypted by the system (Electron's `safeStorage`: DPAPI on Windows,
 *   the keychain on macOS) where it can, else owner-only. Only this process
 *   reads them: the window gets the rows, never a token.
 * - **The code** is `atlas1.` and the base64url of its JSON (`atlas.instances`, read here the same
 *   way); its words never reach an error, which may be shown, since it holds the token.
 */
import { randomUUID } from 'node:crypto'
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'

export type Transport = 'relay' | 'ssh' | 'tailscale'

/** A relay the home waits at (step 20): its address, the home's room, and the tunnel's key. */
export interface RelayCode {
  key: string
  room: string
  url: string
}

/** What a connection code holds. */
export interface ConnectionCode {
  home: string
  port: number
  relay: null | RelayCode
  tailnet: null | string
  token: string
}

/** A machine as the list keeps it, and as the window sees it: no token. */
export interface Machine {
  /** The tailnet's name or address; `null` for an `ssh` machine, reached through its tunnel. */
  host: null | string
  home: string
  id: string
  name: string
  port: number
  /** The host as `~/.ssh/config` names it, for an `ssh` machine. */
  sshHost: null | string
  transport: Transport
  /** The relay and the home's room there, for a `relay` machine; its key is kept with the token. */
  relay?: null | { room: string; url: string }
}

/** The system's encryption, as Electron's `safeStorage` gives it. */
export interface Cipher {
  available: () => boolean
  decrypt: (sealed: Buffer) => string
  encrypt: (text: string) => Buffer
}

export const CODE_PREFIX = 'atlas1.'
const NAME_LIMIT = 64
// eslint-disable-next-line no-control-regex -- control characters are refused in a host
const CONTROL = /[\x00-\x1f\x7f]/

export class MachineError extends Error {}

/** A code read back; never says its text, which holds a token. */
export function decodeCode(text: string): ConnectionCode {
  const trimmed = text.trim()

  if (!trimmed.startsWith(CODE_PREFIX)) {
    throw new MachineError(
      /^atlas\d+\./.test(trimmed)
        ? 'This code comes from a newer Atlas; update this app first.'
        : 'This is not an Atlas connection code; copy it again, whole.'
    )
  }

  let data: unknown

  try {
    data = JSON.parse(Buffer.from(trimmed.slice(CODE_PREFIX.length), 'base64url').toString('utf8'))
  } catch {
    throw new MachineError('This code is cut or damaged; copy it again, whole.')
  }

  const fields = (typeof data === 'object' && data !== null ? data : {}) as Record<string, unknown>
  const { home, port, relay, tailnet, token } = fields

  if (
    typeof home !== 'string' ||
    !home ||
    typeof port !== 'number' ||
    !Number.isInteger(port) ||
    port < 1 ||
    port > 65_535 ||
    typeof token !== 'string' ||
    token.length < 16 ||
    (tailnet !== undefined && (typeof tailnet !== 'string' || !/^[A-Za-z0-9.:-]{1,253}$/.test(tailnet)))
  ) {
    throw new MachineError('This code is cut or damaged; copy it again, whole.')
  }

  return { home, port, relay: readRelay(relay), tailnet: tailnet ?? null, token }
}

function readRelay(value: unknown): null | RelayCode {
  if (value === undefined || value === null) {
    return null
  }

  const { key, room, url } = (typeof value === 'object' ? value : {}) as Record<string, unknown>

  if (
    typeof url !== 'string' ||
    !/^wss?:\/\//.test(url) ||
    typeof room !== 'string' ||
    !room ||
    typeof key !== 'string' ||
    key.length !== 43
  ) {
    throw new MachineError('This code is cut or damaged; copy it again, whole.')
  }

  return { key, room, url }
}

/** An `ssh` host as `~/.ssh/config` or `user@host:port` names it, refused when it could be read as
 * an option or holds more than a destination. */
export function checkSshHost(host: string): string {
  const trimmed = host.trim()

  if (!trimmed) {
    throw new MachineError('Say the SSH host, as `ssh` reaches it (a name from ~/.ssh/config, or user@host).')
  }

  if (trimmed.startsWith('-') || CONTROL.test(trimmed) || /\s/.test(trimmed)) {
    throw new MachineError(
      'The SSH host is only the destination (user@host or a name from ~/.ssh/config): no "ssh", no options, no spaces.'
    )
  }

  return trimmed
}

/** The list and its tokens, in the app's own folder. */
export class Machines {
  private readonly listFile: string
  private readonly tokensFile: string

  constructor(
    folder: string,
    private readonly cipher: Cipher
  ) {
    this.listFile = path.join(folder, 'machines.json')
    this.tokensFile = path.join(folder, 'machine-tokens.json')
  }

  list(): Machine[] {
    const data = readJson(this.listFile)

    return Array.isArray(data) ? data.filter(isMachine) : []
  }

  find(id: string): Machine | undefined {
    return this.list().find(machine => machine.id === id)
  }

  /** Add a machine from its code. `ssh` takes the host the tunnel goes to. */
  add(options: { code: string; name: string; sshHost?: string; transport: Transport }): Machine {
    const code = decodeCode(options.code)
    const name = this.checkName(options.name)
    const transport = options.transport

    if (transport === 'relay' && !code.relay) {
      throw new MachineError(
        'This code has no relay: that home does not wait at one. Make its code with --relay there.'
      )
    }

    if (transport === 'tailscale' && !code.tailnet) {
      throw new MachineError(
        'This code has no Tailscale address: that home is not open to your machines. Open it there, or add it by SSH.'
      )
    }

    const machine: Machine = {
      home: code.home,
      host: transport === 'tailscale' ? code.tailnet : null,
      id: randomUUID(),
      name,
      port: code.port,
      relay: transport === 'relay' && code.relay ? { room: code.relay.room, url: code.relay.url } : null,
      sshHost: transport === 'ssh' ? checkSshHost(options.sshHost ?? '') : null,
      transport
    }
    const same = this.list().find(
      other =>
        other.transport === machine.transport &&
        other.home === machine.home &&
        other.port === machine.port &&
        (other.host ?? '').toLowerCase() === (machine.host ?? '').toLowerCase() &&
        (other.sshHost ?? '').toLowerCase() === (machine.sshHost ?? '').toLowerCase() &&
        (other.relay?.room ?? '') === (machine.relay?.room ?? '')
    )

    if (same) {
      throw new MachineError(`This home is already in the list, as "${same.name}".`)
    }

    this.writeToken(machine.id, code.token)

    if (machine.relay && code.relay) {
      this.writeToken(relayKeyId(machine.id), code.relay.key)
    }

    this.writeList([...this.list(), machine])

    return machine
  }

  rename(id: string, name: string): Machine {
    const found = this.find(id)

    if (!found) {
      throw new MachineError('That machine is no longer in the list.')
    }

    const renamed = { ...found, name: this.checkName(name, id) }
    this.writeList(this.list().map(machine => (machine.id === id ? renamed : machine)))

    return renamed
  }

  remove(id: string): void {
    this.writeList(this.list().filter(machine => machine.id !== id))
    writePrivate(
      this.tokensFile,
      Object.fromEntries(Object.entries(this.readTokens()).filter(([kept]) => kept !== id && kept !== relayKeyId(id)))
    )
  }

  /** A `relay` machine's tunnel key (step 20), for this process only, kept as its token is. */
  relayKey(id: string): string {
    return this.token(relayKeyId(id))
  }

  /** A machine's token, for this process only. */
  token(id: string): string {
    const kept = this.readTokens()[id]

    if (kept?.sealed !== undefined) {
      return this.cipher.decrypt(Buffer.from(kept.sealed, 'base64'))
    }

    if (kept?.plain !== undefined) {
      return kept.plain
    }

    throw new MachineError('This machine has no token kept; remove it and add it again with its code.')
  }

  private checkName(name: string, keeping?: string): string {
    const trimmed = name.trim()

    if (!trimmed || trimmed.length > NAME_LIMIT || CONTROL.test(trimmed)) {
      throw new MachineError(`A machine needs a name of 1 to ${NAME_LIMIT} characters.`)
    }

    const taken = this.list().find(
      machine => machine.id !== keeping && machine.name.toLowerCase() === trimmed.toLowerCase()
    )

    if (taken || trimmed.toLowerCase() === 'this computer') {
      throw new MachineError(`The name "${trimmed}" is taken.`)
    }

    return trimmed
  }

  private writeToken(id: string, token: string): void {
    const tokens = this.readTokens()
    tokens[id] = this.cipher.available() ? { sealed: this.cipher.encrypt(token).toString('base64') } : { plain: token }
    writePrivate(this.tokensFile, tokens)
  }

  private readTokens(): Record<string, undefined | { plain?: string; sealed?: string }> {
    const data = readJson(this.tokensFile)

    return typeof data === 'object' && data !== null && !Array.isArray(data)
      ? (data as Record<string, { plain?: string; sealed?: string }>)
      : {}
  }

  private writeList(machines: Machine[]): void {
    writePrivate(this.listFile, machines)
  }
}

function isMachine(value: unknown): value is Machine {
  if (typeof value !== 'object' || value === null) {
    return false
  }

  const machine = value as Partial<Machine>

  return (
    typeof machine.id === 'string' &&
    typeof machine.name === 'string' &&
    typeof machine.home === 'string' &&
    typeof machine.port === 'number' &&
    (machine.transport === 'ssh' || machine.transport === 'tailscale' || machine.transport === 'relay')
  )
}

/** Where a machine's relay key sits among the tokens. */
function relayKeyId(id: string): string {
  return `${id}#relay`
}

function readJson(file: string): unknown {
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as unknown
  } catch {
    return null
  }
}

/** Written whole, readable by the user alone. */
function writePrivate(file: string, data: unknown): void {
  mkdirSync(path.dirname(file), { recursive: true })
  const temporary = `${file}.tmp`
  writeFileSync(temporary, `${JSON.stringify(data, null, 2)}\n`, { encoding: 'utf8', mode: 0o600 })
  renameSync(temporary, file)
}
