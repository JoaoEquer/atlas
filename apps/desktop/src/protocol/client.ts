/**
 * The connection to the Atlas server: one WebSocket at a time, its reconnects, and the events a
 * conversation missed while it was gone.
 *
 * Decided on 11/10/2026 (docs/ARCHITECTURE.md, Desktop app):
 *
 * - **Every dial asks for the address and the token again**, so a server the app started again
 *   (new port, new token) is found without a reload.
 * - **The token travels as a subprotocol** beside `atlas.v1`, never in the URL.
 * - **A conversation the screen holds is watched from a number**: the one `conversation.open`
 *   returned with its messages. Events at or below it are already in those messages and are
 *   dropped, so reading the history and replaying the gap cannot both paint a turn.
 * - **After a reconnect**, each watched conversation asks for the events after its number. The
 *   server attaches the socket in the same call, and its answer leaves before any later event, so
 *   nothing arrives out of order. A truncated window is never painted, and an
 *   epoch other than the one adopted means the server restarted: both ask the screen to read the
 *   conversation again (`onResync`).
 * - **Reconnects back off** from 300 ms to 15 s with jitter, and a socket that dies within 5 s of
 *   opening counts as a failure.
 */
import { backoffDelayMs, isStableOpen } from './backoff'
import { Channel, type EventParams, frameText, type ServerRequestHandler } from './channel'
import { PROTOCOL, type RpcMethod, type RpcMethods, TOKEN_PREFIX } from './protocol.generated'
import { readRelayUrl, RELAY_SCHEME, RelaySocket } from './relay'

export type ConnectionState = 'closed' | 'connecting' | 'idle' | 'open'

export interface Endpoint {
  token: string
  url: string
}

export interface ClientOptions {
  connectTimeoutMs?: number
  /** Where the server is now: asked before every dial. */
  endpoint: () => Promise<Endpoint>
  heartbeatDeadlineMs?: number
  heartbeatIntervalMs?: number
  /** What dials the socket; tests give their own. */
  socketFactory?: (url: string, protocols: string[]) => WebSocket
}

/** A replay is bounded so a wedged server cannot hold a conversation's catch-up forever. */
const REPLAY_TIMEOUT_MS = 10_000
// A dial after sleep must not hang in `connecting`: the box would stay disabled.
const DEFAULT_CONNECT_TIMEOUT_MS = 15_000

type Listener<T> = (value: T) => void

export class AtlasClient {
  private socket: null | WebSocket = null
  private state: ConnectionState = 'idle'
  private readonly channel: Channel
  private readonly eventListeners = new Set<Listener<EventParams>>()
  private readonly stateListeners = new Set<Listener<ConnectionState>>()
  private readonly resyncListeners = new Set<Listener<string>>()
  private readonly errorListeners = new Set<Listener<Error>>()
  /** The last event number each watched conversation has painted. */
  private readonly watermarks = new Map<string, number>()
  private epoch: null | string = null
  /** The Atlas version the server said it runs, in its first frame; `null` before it. */
  version: null | string = null
  private attempt = 0
  private openedAt: null | number = null
  private retryTimer: null | ReturnType<typeof setTimeout> = null
  private stopped = true
  private readonly options: ClientOptions

  constructor(options: ClientOptions) {
    this.options = options
    this.channel = new Channel({
      ...(options.heartbeatDeadlineMs === undefined ? {} : { heartbeatDeadlineMs: options.heartbeatDeadlineMs }),
      ...(options.heartbeatIntervalMs === undefined ? {} : { heartbeatIntervalMs: options.heartbeatIntervalMs }),
      onEvent: event => {
        this.handleEvent(event)
      },
      onHeartbeatFailure: error => {
        this.drop(error)
      }
    })
  }

  get connectionState(): ConnectionState {
    return this.state
  }

  /** Connect, and keep connecting again until `stop`. */
  start(): void {
    if (!this.stopped) {
      return
    }

    this.stopped = false
    void this.dial()
  }

  stop(): void {
    this.stopped = true

    if (this.retryTimer !== null) {
      clearTimeout(this.retryTimer)
      this.retryTimer = null
    }

    this.drop(new Error('The app closed the connection.'))
  }

  /** Drop the socket now and dial again, as after a silent network loss. */
  reconnect(): void {
    this.drop(new Error('Reconnecting.'))
  }

  /** A method's result; `timeoutMs` is the call's deadline (0, none: it ends with the answer or
   * the connection), the channel's own when not given. */
  call<M extends RpcMethod>(
    method: M,
    params: RpcMethods[M]['params'],
    timeoutMs?: number
  ): Promise<RpcMethods[M]['result']> {
    return this.channel.call(method, params, timeoutMs)
  }

  /** A notification: a method sent with no id and no answer; `false` when not connected. */
  notify<M extends RpcMethod>(method: M, params: RpcMethods[M]['params']): boolean {
    return this.channel.notify(method, params)
  }

  /** Paint this conversation's events after `seq`, the number its messages were read at. */
  watch(conversationId: string, seq: number): void {
    this.watermarks.set(conversationId, seq)
  }

  forget(conversationId: string): void {
    this.watermarks.delete(conversationId)
  }

  onEvent(listener: Listener<EventParams>): () => void {
    this.eventListeners.add(listener)

    return () => this.eventListeners.delete(listener)
  }

  onState(listener: Listener<ConnectionState>): () => void {
    this.stateListeners.add(listener)
    listener(this.state)

    return () => this.stateListeners.delete(listener)
  }

  /** A conversation's replay cannot be trusted: read it again with `conversation.open`. */
  onResync(listener: Listener<string>): () => void {
    this.resyncListeners.add(listener)

    return () => this.resyncListeners.delete(listener)
  }

  /** Why the last dial failed, for the screen. */
  onError(listener: Listener<Error>): () => void {
    this.errorListeners.add(listener)

    return () => this.errorListeners.delete(listener)
  }

  onRequest(handler: ServerRequestHandler): () => void {
    return this.channel.onRequest(handler)
  }

  private async dial(): Promise<void> {
    if (this.stopped || this.state === 'connecting' || this.state === 'open') {
      return
    }

    this.setState('connecting')

    try {
      const endpoint = await this.options.endpoint()
      await this.open(endpoint)
    } catch (error) {
      this.socket = null
      this.setState('closed')
      this.report(error)
      this.scheduleRetry()
    }
  }

  private open(endpoint: Endpoint): Promise<void> {
    const protocols = [PROTOCOL, TOKEN_PREFIX + endpoint.token]
    const socket =
      this.options.socketFactory?.(endpoint.url, protocols) ??
      (endpoint.url.startsWith(RELAY_SCHEME)
        ? // Through a relay (step 20): the token rides inside the tunnel's key, never past the relay.
          (new RelaySocket(readRelayUrl(endpoint.url)) as unknown as WebSocket)
        : new WebSocket(endpoint.url, protocols))
    this.socket = socket

    socket.addEventListener('message', ({ data }) => {
      // A socket this client already replaced may still deliver; its frames are dropped.
      const text = this.socket === socket ? frameText(data) : null

      if (text !== null) {
        this.channel.handleFrame(text)
      }
    })

    // Before it opens, the handshake below owns every failure and says which kind it was.
    let isOpen = false

    socket.addEventListener('close', event => {
      if (isOpen && this.socket === socket) {
        this.drop(new Error(`The connection closed (${event.code}).`))
      }
    })

    return new Promise<void>((resolve, reject) => {
      let settled = false

      const timer = setTimeout(() => {
        settle(
          new Error(
            `No answer from Atlas in ${(this.options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS) / 1000} s.`
          )
        )
      }, this.options.connectTimeoutMs ?? DEFAULT_CONNECT_TIMEOUT_MS)

      // Every failure says which kind it is: a refused token, a refused origin, or nothing there
      // (the boot screen shows the message as it is).
      const settle = (error?: Error) => {
        if (settled) {
          return
        }

        settled = true
        clearTimeout(timer)
        socket.removeEventListener('open', opened)
        socket.removeEventListener('error', failed)
        socket.removeEventListener('close', closed)

        if (error) {
          if (this.socket === socket) {
            this.socket = null

            try {
              socket.close()
            } catch {
              // Already gone.
            }
          }

          reject(error)
        } else {
          resolve()
        }
      }

      const opened = () => {
        isOpen = true
        this.channel.attach({
          send: text => {
            socket.send(text)
          }
        })
        this.openedAt = Date.now()
        this.setState('open')
        settle()
      }

      const failed = () => {
        settle(new Error('The connection to Atlas failed before it opened.'))
      }

      const closed = ({ code }: CloseEvent) => {
        settle(new Error(closeReason(code)))
      }

      const firstOnly = { once: true }
      socket.addEventListener('open', opened, firstOnly)
      socket.addEventListener('error', failed, firstOnly)
      socket.addEventListener('close', closed, firstOnly)
    })
  }

  private drop(error: Error): void {
    const socket = this.socket
    this.socket = null
    this.channel.detach(error)

    if (socket) {
      try {
        socket.close()
      } catch {
        // Already gone.
      }
    }

    if (this.state === 'open' || this.state === 'connecting') {
      if (isStableOpen(this.openedAt)) {
        this.attempt = 0
      }

      this.openedAt = null
      this.setState('closed')
      this.scheduleRetry()
    }
  }

  private scheduleRetry(): void {
    if (this.stopped || this.retryTimer !== null) {
      return
    }

    const delay = backoffDelayMs(this.attempt)
    this.attempt += 1
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null
      void this.dial()
    }, delay)
  }

  private handleEvent(event: EventParams): void {
    if (event.type === 'server.ready') {
      this.channel.startHeartbeat()
      const payload = event.payload as { epoch?: unknown; version?: unknown } | undefined
      const epoch = typeof payload?.epoch === 'string' ? payload.epoch : null
      this.version = typeof payload?.version === 'string' ? payload.version : null
      this.adoptEpoch(epoch)
      this.dispatch(event)

      return
    }

    if (this.isNew(event)) {
      this.dispatch(event)
    }
  }

  /**
   * The first frame of every connection. The same epoch: ask each watched conversation for what it
   * missed. Another: the server restarted, its numbers mean nothing, read each conversation again.
   */
  private adoptEpoch(epoch: null | string): void {
    const known = this.epoch
    this.epoch = epoch

    if (known !== null && epoch !== known) {
      for (const conversationId of [...this.watermarks.keys()]) {
        this.resync(conversationId)
      }

      return
    }

    for (const [conversationId, after] of [...this.watermarks]) {
      void this.replay(conversationId, after)
    }
  }

  private async replay(conversationId: string, after: number): Promise<void> {
    let result: RpcMethods['conversation.events']['result']

    try {
      result = (await this.channel.request(
        'conversation.events',
        { after, conversation_id: conversationId },
        REPLAY_TIMEOUT_MS
      )) as RpcMethods['conversation.events']['result']
    } catch {
      // A failed replay is not painted over: read the conversation again.
      if (this.state === 'open') {
        this.resync(conversationId)
      }

      return
    }

    if (result.epoch !== this.epoch || result.truncated) {
      this.resync(conversationId)

      return
    }

    for (const raw of result.events) {
      const event = { ...(raw as unknown as EventParams), replayed: true }

      if (this.isNew(event)) {
        this.dispatch(event)
      }
    }
  }

  private resync(conversationId: string): void {
    this.watermarks.delete(conversationId)

    for (const listener of this.resyncListeners) {
      listener(conversationId)
    }
  }

  /** Whether a numbered event is past its conversation's watermark; advances it when it is. */
  private isNew(event: EventParams): boolean {
    const conversationId = event.conversation_id
    const seq = event.seq

    if (conversationId === undefined || typeof seq !== 'number') {
      return true
    }

    const seen = this.watermarks.get(conversationId)

    if (seen === undefined) {
      // Not on screen yet (just created, or being read again): its open sets the number.
      return true
    }

    if (seq <= seen) {
      return false
    }

    this.watermarks.set(conversationId, seq)

    return true
  }

  private dispatch(event: EventParams): void {
    tell(this.eventListeners, event)
  }

  private report(thrown: unknown): void {
    tell(this.errorListeners, thrown instanceof Error ? thrown : new Error(String(thrown)))
  }

  private setState(next: ConnectionState): void {
    if (next !== this.state) {
      this.state = next
      tell(this.stateListeners, next)
    }
  }
}

/** Hand `value` to each listener, in the order they subscribed. */
function tell<T>(listeners: Set<Listener<T>>, value: T): void {
  for (const listener of listeners) {
    listener(value)
  }
}

/** The server's close codes, in words. */
export function closeReason(code: number): string {
  switch (code) {
    case 4401:
      return 'Atlas refused the token (4401).'
    case 4403:
      return 'Atlas refused this window as a web page (4403).'
    case 4408:
      return 'Atlas takes connections only from its own machine (4408).'
    case 4426:
      return 'That Atlas is another version: update this app and that machine to the same one (4426).'
    default:
      return `The connection closed before it opened (${code}).`
  }
}
