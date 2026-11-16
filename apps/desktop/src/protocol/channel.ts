/**
 * The transport-agnostic half of a connection to the Atlas server: request ids, pending calls with
 * deadlines, errors, `event` notifications, server requests, and the `ping` heartbeat.
 *
 * Decided on 11/10/2026 (docs/ARCHITECTURE.md, Desktop app). There is no `client.capabilities` or
 * `decline` (every Atlas client answers server requests), and liveness is not judged by responses
 * alone.
 */
import { ERROR_CODES, type RpcMethod, type RpcMethods } from './protocol.generated'

export type RequestId = number | string

export interface ErrorPayload {
  code?: number
  data?: unknown
  message?: string
}

export interface Frame {
  error?: ErrorPayload
  id?: null | RequestId
  method?: string
  params?: unknown
  result?: unknown
}

/** One `event` notification's params: the type, the conversation and number when it has them. */
export interface EventParams {
  conversation_id?: string
  payload?: unknown
  /** Client-local: delivered by a replay after a reconnect, not live. */
  replayed?: boolean
  seq?: number
  type: string
}

/** One server request (`ask_user`, `approval`), as handed to a handler. */
export interface ServerRequest {
  /** Answer with a JSON-RPC error: the server counts the request as unanswered. */
  fail: (code: number, message: string) => void
  id: string
  method: string
  params: Record<string, unknown>
  /** Delivered again after a reconnect (`open_requests`), not live. */
  replayed: boolean
  /**
   * Answer it. The first `respond` or `fail` wins; a request delivered again after a reconnect keeps
   * its id, so a card answering twice sends nothing the second time.
   */
  respond: (result: Record<string, unknown>) => void
}

/** Handles a server request: `true` when it took it, `false` to leave it to the next handler. */
export type ServerRequestHandler = (request: ServerRequest) => boolean

export class RpcError extends Error {
  readonly code: number | undefined
  readonly data: unknown

  constructor(message: string, options: { code?: number | undefined; data?: unknown } = {}) {
    super(message)
    this.name = 'RpcError'
    this.code = options.code
    this.data = options.data
  }
}

/** The server has no such thing: a conversation or a session deleted since it was named. */
export function notFound(error: unknown): boolean {
  return error instanceof RpcError && error.code === ERROR_CODES.NOT_FOUND
}

export function rpcErrorFromFrame(raw: unknown): RpcError {
  const payload = (raw && typeof raw === 'object' ? raw : {}) as ErrorPayload
  const said = typeof payload.message === 'string' ? payload.message : ''
  const code = typeof payload.code === 'number' ? payload.code : undefined

  return new RpcError(said || 'The Atlas call failed.', { code, data: payload.data })
}

/** Anything that carries one frame of text to the server: the WebSocket, or a test's spy. */
export interface Transport {
  send(text: string): void
}

export interface ChannelOptions {
  heartbeatDeadlineMs?: number
  heartbeatIntervalMs?: number
  /** The heartbeat went unanswered past its deadline, or could not be sent: drop the transport. */
  onHeartbeatFailure?: (error: Error) => void
  onEvent?: (event: EventParams) => void
  /** A handler threw; the channel already answered `-32603`. */
  onRequestHandlerError?: (error: Error, request: { id: string; method: string }) => void
  /** No handler took a request; the channel already answered `-32601`. */
  onUnhandledRequest?: (request: { id: string; method: string }) => void
  requestTimeoutMs?: number
}

export const DEFAULT_REQUEST_TIMEOUT_MS = 120_000
// The heartbeat exists because a connection can die in silence: a laptop that sleeps, a proxy that
// drops idle sockets or a VPN that reconnects leaves no `close` event behind.
export const DEFAULT_HEARTBEAT_INTERVAL_MS = 15_000
export const DEFAULT_HEARTBEAT_DEADLINE_MS = 45_000
/** How many unanswered ping ids are remembered; older ones are forgotten first. */
const PING_MEMORY = 8

/** A call sent and not yet answered. */
interface Waiter {
  deadline?: ReturnType<typeof setTimeout>
  fail: (error: Error) => void
  succeed: (value: unknown) => void
}

/** One server request's identity, as the option callbacks receive it. */
interface RequestRef {
  id: string
  method: string
}

const utf8 = new TextDecoder()

/** A socket message's data as text; `null` for anything else. */
export function frameText(raw: unknown): null | string {
  if (typeof raw === 'string') {
    return raw
  }

  const binary = raw instanceof ArrayBuffer || ArrayBuffer.isView(raw)

  return binary ? utf8.decode(raw) : null
}

/** A plain JSON object: not `null`, not an array. */
export function isRecord(candidate: unknown): candidate is Record<string, unknown> {
  if (candidate === null || Array.isArray(candidate)) {
    return false
  }

  return typeof candidate === 'object'
}

function toError(thrown: unknown): Error {
  return thrown instanceof Error ? thrown : new Error(String(thrown))
}

/** A JSON-RPC 2.0 frame for `id`, its other members after the version. */
function encode(id: string, body: Record<string, unknown>): string {
  return JSON.stringify({ id, jsonrpc: '2.0', ...body })
}

function paramsOf(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {}
}

/** A request the server sends to the app carries a string id and a method other than `event`. */
function asServerRequest(frame: Frame): null | RequestRef {
  const { id, method } = frame

  if (typeof id !== 'string' || typeof method !== 'string' || method === 'event') {
    return null
  }

  return { id, method }
}

export class Channel {
  private callCount = 0
  private readonly waiters = new Map<RequestId, Waiter>()
  private link: null | Transport = null
  private pulse: null | ReturnType<typeof setInterval> = null
  private pingCount = 0
  /** Ping ids sent and not yet answered, oldest first. */
  private readonly unansweredPings = new Set<string>()
  /** When the server last showed it is alive: any frame from it, or a new transport. */
  private heardAt = 0
  private readonly takers: ServerRequestHandler[] = []
  private readonly settings: ChannelOptions & {
    heartbeatDeadlineMs: number
    heartbeatIntervalMs: number
    requestTimeoutMs: number
  }

  constructor(options: ChannelOptions = {}) {
    const heartbeatDeadlineMs = options.heartbeatDeadlineMs ?? DEFAULT_HEARTBEAT_DEADLINE_MS
    const heartbeatIntervalMs = options.heartbeatIntervalMs ?? DEFAULT_HEARTBEAT_INTERVAL_MS
    const requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS
    this.settings = { ...options, heartbeatDeadlineMs, heartbeatIntervalMs, requestTimeoutMs }
  }

  get connected(): boolean {
    return this.link !== null
  }

  /** Bind a new connection; the previous one's heartbeat stops. */
  attach(transport: Transport): void {
    this.stopHeartbeat()
    this.link = transport
    this.heardAt = Date.now()
  }

  /** Drop the transport and fail every call in flight with `error`. */
  detach(error: Error): void {
    this.stopHeartbeat()
    this.link = null
    const stranded = [...this.waiters.values()]
    this.waiters.clear()

    for (const waiter of stranded) {
      clearTimeout(waiter.deadline)
      waiter.fail(error)
    }
  }

  call<M extends RpcMethod>(
    method: M,
    params: RpcMethods[M]['params'],
    timeoutMs = this.settings.requestTimeoutMs
  ): Promise<RpcMethods[M]['result']> {
    return this.request(method, params as Record<string, unknown>, timeoutMs) as Promise<RpcMethods[M]['result']>
  }

  /**
   * Tell the server something and expect nothing back: a frame with no `id`, which the server never
   * answers. `false` when there is no connection or it would not take the frame.
   */
  notify<M extends RpcMethod>(method: M, params: RpcMethods[M]['params']): boolean {
    const link = this.link

    if (link === null) {
      return false
    }

    try {
      link.send(JSON.stringify({ jsonrpc: '2.0', method, params }))
    } catch {
      return false
    }

    return true
  }

  request(
    method: string,
    params: Record<string, unknown>,
    timeoutMs = this.settings.requestTimeoutMs
  ): Promise<unknown> {
    const link = this.link

    if (link === null) {
      return Promise.reject(new Error('Not connected to Atlas.'))
    }

    this.callCount += 1
    const id = `r${this.callCount}`

    return new Promise((succeed, fail) => {
      const waiter: Waiter = { fail, succeed }

      if (timeoutMs > 0) {
        waiter.deadline = setTimeout(() => {
          if (!this.waiters.delete(id)) {
            return
          }

          fail(new Error(`The call ${method} got no answer in ${Math.round(timeoutMs / 1000)} s.`))
        }, timeoutMs)
      }

      this.waiters.set(id, waiter)

      try {
        link.send(encode(id, { method, params }))
      } catch (thrown) {
        this.forget(id)
        fail(toError(thrown))
      }
    })
  }

  /**
   * Handlers for server requests, tried in order until one takes it. A request nobody takes is
   * answered `-32601` at once, so the turn does not wait out its deadline.
   */
  onRequest(handler: ServerRequestHandler): () => void {
    this.takers.push(handler)

    return () => {
      const at = this.takers.indexOf(handler)

      if (at !== -1) {
        this.takers.splice(at, 1)
      }
    }
  }

  /** Hand a server request to the handlers: live, or one still open after a reconnect. */
  deliverRequest(id: string, method: string, params: Record<string, unknown>, replayed = false): boolean {
    const request = this.answerable(id, method, params, replayed)
    const ref: RequestRef = { id, method }

    for (const taker of this.takers) {
      let took: boolean

      try {
        took = taker(request)
      } catch (thrown) {
        // The turn waits on this answer: a card that throws still has to send one.
        request.fail(ERROR_CODES.INTERNAL_ERROR, `The card for ${method} failed.`)
        this.settings.onRequestHandlerError?.(toError(thrown), ref)

        return false
      }

      if (took) {
        return true
      }
    }

    request.fail(ERROR_CODES.METHOD_NOT_FOUND, `Nothing here answers ${method}.`)
    this.settings.onUnhandledRequest?.(ref)

    return false
  }

  /** Route one frame: an answer settles its call, an event goes to `onEvent`, a request to the handlers. */
  handleFrame(text: string): Frame | null {
    let decoded: unknown

    try {
      decoded = JSON.parse(text)
    } catch {
      return null
    }

    if (!isRecord(decoded)) {
      return null
    }

    const frame = decoded as Frame
    // Whatever the server sends proves it alive, so a long streamed turn keeps the socket.
    this.heardAt = Date.now()
    const asked = asServerRequest(frame)

    if (asked !== null) {
      this.deliverRequest(asked.id, asked.method, paramsOf(frame.params))
    } else if (frame.id !== undefined && frame.id !== null) {
      this.settle(frame.id, frame)
    } else if (frame.method === 'event') {
      this.emitEvent(frame.params)
    }

    return frame
  }

  /**
   * The `ping` keepalive. Silence is judged against the last ping sent: a hidden window runs these
   * ticks a minute apart, so the previous answer is always old, and judging its age dropped every
   * idle hidden window's healthy socket.
   */
  startHeartbeat(): void {
    this.stopHeartbeat()
    this.heardAt = Date.now()
    const link = this.link
    const { heartbeatDeadlineMs, heartbeatIntervalMs } = this.settings

    if (link === null || heartbeatIntervalMs <= 0 || heartbeatDeadlineMs <= 0) {
      return
    }

    let pingedAt = 0

    this.pulse = setInterval(() => {
      if (this.link !== link) {
        return
      }

      const now = Date.now()
      const silentSincePing = pingedAt > this.heardAt

      if (silentSincePing && now - this.heardAt >= heartbeatDeadlineMs) {
        this.heartbeatFailed(new Error('The server stopped answering.'))

        return
      }

      pingedAt = now
      this.ping(link)
    }, heartbeatIntervalMs)
  }

  stopHeartbeat(): void {
    this.unansweredPings.clear()

    if (this.pulse === null) {
      return
    }

    clearInterval(this.pulse)
    this.pulse = null
  }

  /** The request object a handler sees; whichever of its answers comes first is the only one sent. */
  private answerable(id: string, method: string, params: Record<string, unknown>, replayed: boolean): ServerRequest {
    let answered = false

    const answer = (body: Record<string, unknown>): void => {
      if (answered) {
        return
      }

      answered = true

      try {
        this.link?.send(encode(id, body))
      } catch {
        // No connection to answer on; once the app reconnects the server delivers the request again.
      }
    }

    return {
      fail: (code, message) => {
        answer({ error: { code, message } })
      },
      id,
      method,
      params,
      replayed,
      respond: result => {
        answer({ result })
      }
    }
  }

  /** A frame with an id: a ping's answer, a call's answer, or an id nobody waits on. */
  private settle(id: RequestId, frame: Frame): void {
    if (typeof id === 'string' && this.unansweredPings.delete(id)) {
      return
    }

    const waiter = this.waiters.get(id)

    if (waiter === undefined) {
      return
    }

    this.forget(id)

    if (frame.error) {
      waiter.fail(rpcErrorFromFrame(frame.error))

      return
    }

    // The answers of `conversation.open` and `conversation.events` list the requests still open.
    // Those are no events, so they are handed to the handlers here, ahead of the caller's result.
    this.replayOpenRequests(frame.result)
    waiter.succeed(frame.result)
  }

  private emitEvent(params: unknown): void {
    if (isRecord(params) && typeof params.type === 'string') {
      this.settings.onEvent?.(params as unknown as EventParams)
    }
  }

  private ping(link: Transport): void {
    this.pingCount += 1
    const id = `ping-${this.pingCount}`
    this.unansweredPings.add(id)

    if (this.unansweredPings.size > PING_MEMORY) {
      const [oldest] = this.unansweredPings
      this.unansweredPings.delete(oldest as string)
    }

    try {
      link.send(encode(id, { method: 'ping', params: {} }))
    } catch (thrown) {
      this.heartbeatFailed(toError(thrown))
    }
  }

  private heartbeatFailed(error: Error): void {
    this.stopHeartbeat()
    this.settings.onHeartbeatFailure?.(error)
  }

  private replayOpenRequests(result: unknown): void {
    const listed: unknown = isRecord(result) ? result.open_requests : undefined

    if (!Array.isArray(listed)) {
      return
    }

    for (const item of listed as unknown[]) {
      if (!isRecord(item)) {
        continue
      }

      const { id, method } = item

      if (typeof id === 'string' && typeof method === 'string') {
        this.deliverRequest(id, method, paramsOf(item.params), true)
      }
    }
  }

  private forget(id: RequestId): void {
    clearTimeout(this.waiters.get(id)?.deadline)
    this.waiters.delete(id)
  }
}
