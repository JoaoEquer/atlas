/**
 * A conversation as the screen shows it: what was stored, then what its events add. Pure, so every
 * transition is tested without a window.
 *
 * A turn is a list of parts, in the order they happened:
 * reasoning, text, a tool call, and "other" for an event this app does not know (ACP v2: shown
 * generically, never dropped). The screen draws them as a trail.
 *
 * The text of a model call is neither interim nor final while it streams: the server says which it
 * was, `message.interim` before the call's tools start, `message.complete` for the answer. The app
 * never guesses, and nothing moves or goes away.
 *
 * A conversation reopened shows the same parts: the stored messages carry the reasoning,
 * the calls and each result's status. Only the times are not kept.
 */
import type { EventParams } from '@/protocol/channel'
import type {
  AttachmentRow,
  CONVERSATION_EVENT_NAMES,
  MessageComplete,
  MessageRow,
  ReviewSummary,
  Said,
  StatusUpdate,
  ToolComplete,
  ToolProgressPayload,
  ToolStart,
  UserSaid
} from '@/protocol/protocol.generated'

export type ToolStatus = 'running' | ToolComplete['status']

export type Item =
  | {
      /** The kind of error that ended a failed turn. */
      error: null | string
      key: string
      kind: 'end'
      status: Exclude<MessageComplete['status'], 'answered'>
      /** What the server said of it: for a failed turn, why, and often what to do. */
      text: string
    }
  /** A file a tool sent the user (`message.file`, the voice plugin's spoken answer). */
  | { attachment: AttachmentRow; key: string; kind: 'file' }
  | { key: string; kind: 'notice'; text: string }
  | { key: string; kind: 'other'; payload: unknown; type: string }
  | {
      key: string
      kind: 'reasoning'
      /** How long it took, once it ended live; `null` reopened, where the time is not kept. */
      seconds: null | number
      startedAt: null | number
      streaming: boolean
      text: string
    }
  | {
      actions: string[]
      /** The skill diff behind an action that changed a skill, by that action's text. */
      diffs: Record<string, string>
      key: string
      kind: 'review'
      proposals: string[]
      text: string
    }
  | { key: string; kind: 'status'; text: string }
  | { key: string; kind: 'summary'; text: string }
  | {
      key: string
      kind: 'text'
      /** `open` while its call runs; `interim` when the call asked for tools; `final`, the answer. */
      role: 'final' | 'interim' | 'open'
      streaming: boolean
      text: string
    }
  | {
      args: string
      key: string
      kind: 'tool'
      name: string
      progress: string[]
      result: string
      /** The call's time, `tool.complete`'s `duration_s`; `null` reopened. */
      seconds: null | number
      spillPath: null | string
      startedAt: null | number
      status: ToolStatus
      toolId: string
    }
  | {
      attachments: AttachmentRow[]
      key: string
      kind: 'user'
      /** The skill the user invoked with `/name`; its text is what they typed. */
      skill?: string
      steered: boolean
      text: string
    }
  | ({ key: string; kind: 'terminal'; steered: boolean } & TerminalRow)

/**
 * A command the user ran from the conversation and what came of it (step 16f): the ▶ on a shell
 * block, or a message starting with `!`. It travels as Claude Code's bash-mode tags, which the
 * model reads and the screen reads back, live and reopened alike.
 */
export interface TerminalRow {
  command: string
  exitCode: null | number
  output: string
  /** What the terminal said of a command that did not end as commands do; empty when it did. */
  state: string
}

/** A command's outcome as Claude Code's bash-mode tags, for a harness that reads them (an
 * executor's session); the server writes Atlas's own the same way (`atlas.agent.terminal_text`). */
export function terminalText(row: {
  command: string
  exitCode: null | number
  output: string
  status: string
}): string {
  const states: Record<string, string> = {
    ended: 'A command that was still running has ended.',
    running: 'It is still running after 30 seconds: this is its output so far.',
    unfinished: 'The terminal did not take it: it never started.'
  }
  const lines = [`<bash-input>${row.command}</bash-input>`, `<bash-stdout>${row.output}</bash-stdout>`]

  if (row.exitCode !== null) {
    lines.push(`<bash-exit-code>${String(row.exitCode)}</bash-exit-code>`)
  }

  if (states[row.status]) {
    lines.push(`<bash-status>${states[row.status] ?? ''}</bash-status>`)
  }

  return lines.join('\n')
}

/** A terminal row's text, read back; `null` for any other text. */
export function parseTerminal(text: string): null | TerminalRow {
  if (!text.startsWith('<bash-input>')) {
    return null
  }

  const field = (tag: string) => new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`).exec(text)?.[1] ?? ''
  const exit = field('bash-exit-code')

  return {
    command: field('bash-input'),
    exitCode: exit === '' ? null : Number(exit),
    output: field('bash-stdout'),
    state: field('bash-status')
  }
}

/** How a skill the user invoked with `/name` begins, and what they added: the invocation
 * message, the skill's text loaded (the server's `skill_text`). */
const SKILL_MARK = '[IMPORTANT: The user has invoked the "'
const SKILL_ASKED = 'The user has provided the following instruction alongside the skill invocation: '

/** A skill the user invoked, read back as they typed it (`/name` and their words); `null` for any
 * other text. */
export function parseSkill(text: string): null | { name: string; typed: string } {
  if (!text.startsWith(SKILL_MARK)) {
    return null
  }

  const name = text.slice(SKILL_MARK.length).split('"', 1)[0] ?? ''
  const at = text.lastIndexOf(SKILL_ASKED)
  const asked = at < 0 ? '' : text.slice(at + SKILL_ASKED.length).trim()

  return { name, typed: asked ? `/${name} ${asked}` : `/${name}` }
}

/** The kinds of item drawn on the trail, one dot each. */
export const PART_KINDS = ['other', 'reasoning', 'text', 'tool'] as const
export type Part = Extract<Item, { kind: (typeof PART_KINDS)[number] }>

export interface Thread {
  items: Item[]
  running: boolean
}

export const EMPTY_THREAD: Thread = { items: [], running: false }

type ConversationEvent = (typeof CONVERSATION_EVENT_NAMES)[number]

/**
 * Where each event of a conversation is drawn: in the thread, or by the app's store (a list, a dock,
 * a card, the title). A new protocol event fails `tsc` here until it has a place, so the generic
 * part never hides an event we forgot to draw.
 */
export const DRAWN: Record<ConversationEvent, 'store' | 'thread'> = {
  'command.update': 'store',
  'message.complete': 'thread',
  'message.delta': 'thread',
  'message.file': 'thread',
  'message.interim': 'thread',
  'message.start': 'thread',
  'message.steered': 'thread',
  'preview.close': 'store',
  'preview.open': 'store',
  'reasoning.delta': 'thread',
  'request.cancel': 'store',
  'review.summary': 'thread',
  'status.update': 'thread',
  'subagent.update': 'store',
  'title.changed': 'store',
  'tool.complete': 'thread',
  'tool.progress': 'thread',
  'tool.start': 'thread'
}

/** The stored messages, archived ones included: the whole conversation, as the database keeps it. */
export function fromHistory(messages: MessageRow[], running: boolean): Thread {
  const items: Item[] = []
  // The tool parts made from an assistant message's calls, by call id, for their results.
  const calls = new Map<string, number>()

  for (const message of messages) {
    const key = `m${message.number}`

    if (message.kind === 'model_switch') {
      // A note for the model (a model switch): the user never wrote it.
      continue
    }

    if (message.kind === 'notice') {
      items.push({ key, kind: 'notice', text: message.text })
      continue
    }

    const terminal = message.kind === 'terminal' ? parseTerminal(message.text) : null

    if (terminal) {
      items.push({ key, kind: 'terminal', steered: false, ...terminal })
      continue
    }

    const skill = message.kind === 'skill' ? parseSkill(message.text) : null

    if (skill) {
      items.push({
        attachments: message.attachments,
        key,
        kind: 'user',
        skill: skill.name,
        steered: false,
        text: skill.typed
      })
      continue
    }

    if (message.kind === 'summary') {
      items.push({ key, kind: 'summary', text: message.text })
    } else if (message.role === 'user') {
      items.push({ attachments: message.attachments, key, kind: 'user', steered: false, text: message.text })
    } else if (message.role === 'assistant') {
      if (message.reasoning.trim()) {
        items.push(reasoningPart(`${key}-r`, message.reasoning, null))
      }

      if (message.text) {
        const role = message.calls.length ? 'interim' : 'final'
        items.push({ key: `${key}-t`, kind: 'text', role, streaming: false, text: message.text })
      }

      for (const call of message.calls) {
        calls.set(call.id, items.length)
        items.push(toolPart(`${key}-${call.id}`, call.id, call.name, call.args_text, null))
      }
    } else if (message.role === 'tool') {
      const at = message.call_id === null ? undefined : calls.get(message.call_id)
      const tool = at === undefined ? undefined : items[at]
      const done = {
        result: message.text,
        status: message.status ?? 'completed'
      } as const

      if (at !== undefined && tool?.kind === 'tool') {
        items[at] = { ...tool, ...done }
      } else {
        // A result whose call is not here: shown alone, as before step 9c.
        const name = message.tool_name ?? 'tool'
        items.push({ ...toolPart(key, message.call_id ?? key, name, '', null), ...done })
      }
    }
  }

  // A call with no result runs now, in the turn the conversation was read during, and waits for its
  // events; any other never got one, and shows as cut short.
  const turn = running
    ? items.findLastIndex(item => (item.kind === 'user' || item.kind === 'terminal') && !item.steered)
    : items.length

  return {
    items: items.map((item, index) =>
      item.kind === 'tool' && item.status === 'running' && index < turn ? { ...item, status: 'cancelled' } : item
    ),
    running
  }
}

/** A conversation read again (`read`) keeps the keys of the thread already on screen (`shown`), item
 * by item while they are of the same kind: what came as events is keyed by their numbers and what
 * was read by the messages', and a key that changes draws its part again from its Markdown. A key
 * another of the read items holds stays theirs. */
export function keepKeys(shown: Thread, read: Thread): Thread {
  const taken = new Set(read.items.map(item => item.key))
  const used = new Set<string>()
  const items = read.items.map((item, index) => {
    const before = shown.items[index]
    const same =
      before?.kind === item.kind &&
      (before.kind !== 'tool' || (item.kind === 'tool' && before.toolId === item.toolId)) &&
      (before.key === item.key || !taken.has(before.key)) &&
      !used.has(before.key)
    const key = same ? before.key : item.key
    used.add(key)

    return key === item.key ? item : { ...item, key }
  })

  return { ...read, items }
}

/** The thread after one event, at `now` (for the timers). An event that changes nothing returns the
 * same object. */
export function applyEvent(thread: Thread, event: EventParams, now = Date.now()): Thread {
  const key = `e${event.seq ?? Math.random().toString(36).slice(2)}`
  const type = event.type as ConversationEvent

  switch (type) {
    case 'message.start': {
      const said = event.payload as UserSaid
      // A turn Atlas started (a background command ended) is a notice, not the user's words; a
      // command the user ran is a terminal row (step 16f).
      const item: Item = said.kind === 'notice' ? { key, kind: 'notice', text: said.text } : userItem(key, said, false)

      return { items: [...closeParts(thread.items, now), item], running: true }
    }

    case 'message.steered': {
      const said = event.payload as UserSaid

      return { ...thread, items: [...closeParts(thread.items, now), userItem(key, said, true)] }
    }

    case 'message.delta':
      return { ...thread, items: append(thread.items, key, 'text', (event.payload as Said).text, now) }

    case 'reasoning.delta':
      return { ...thread, items: append(thread.items, key, 'reasoning', (event.payload as Said).text, now) }

    case 'message.interim':
      // The server says the call's text was interim; the text itself already came in deltas.
      return { ...thread, items: settleText(closeParts(thread.items, now), 'interim') }

    case 'tool.start': {
      const start = event.payload as ToolStart
      const items = closeParts(thread.items, now)

      // Already there when a conversation reopened during the turn reads its stored calls.
      if (items.some(item => item.kind === 'tool' && item.toolId === start.tool_id)) {
        return { ...thread, items: updateTool(items, start.tool_id, tool => ({ ...tool, startedAt: now })) }
      }

      return { ...thread, items: [...items, toolPart(key, start.tool_id, start.name, start.args_text, now)] }
    }

    case 'tool.progress': {
      const progress = event.payload as ToolProgressPayload

      return {
        ...thread,
        items: updateTool(thread.items, progress.tool_id, tool => ({
          ...tool,
          progress: [...tool.progress, progressText(progress.event)]
        }))
      }
    }

    case 'tool.complete': {
      const complete = event.payload as ToolComplete

      return {
        ...thread,
        items: updateTool(thread.items, complete.tool_id, tool => ({
          ...tool,
          result: complete.result_text,
          seconds: complete.duration_s,
          spillPath: complete.spill_path,
          status: complete.status
        }))
      }
    }

    case 'status.update': {
      const status = event.payload as StatusUpdate

      return { ...thread, items: [...thread.items, { key, kind: 'status', text: status.text }] }
    }

    case 'message.file':
      return { ...thread, items: [...thread.items, { attachment: event.payload as AttachmentRow, key, kind: 'file' }] }

    case 'review.summary': {
      const review = event.payload as ReviewSummary

      return {
        ...thread,
        items: [
          ...thread.items,
          {
            actions: review.actions,
            diffs: review.diffs,
            key,
            kind: 'review',
            proposals: review.proposals,
            text: review.text
          }
        ]
      }
    }

    case 'message.complete': {
      const complete = event.payload as MessageComplete
      let items = settleText(closeParts(thread.items, now), 'final')

      // A reply that never streamed (no delta reached this screen) still shows its text.
      if (complete.text && complete.status === 'answered' && !turnHasFinal(items)) {
        items = [...items, { key: `${key}-text`, kind: 'text', role: 'final', streaming: false, text: complete.text }]
      }

      if (complete.status !== 'answered') {
        items = [...items, { error: complete.error, key, kind: 'end', status: complete.status, text: complete.text }]
      }

      // The server tells every cut call as cancelled; one it could not tell (a turn that failed
      // inside Atlas) must not spin forever.
      items = items.map(item =>
        item.kind === 'tool' && item.status === 'running' ? { ...item, status: 'cancelled' } : item
      )

      return { items, running: false }
    }

    case 'command.update':
    case 'preview.close':
    case 'preview.open':
    case 'request.cancel':
    case 'subagent.update':
    case 'title.changed':
      return thread

    default:
      // An event this app does not know, from a newer server: shown, not dropped (ACP v2).
      return { ...thread, items: [...thread.items, { key, kind: 'other', payload: event.payload, type: event.type }] }
  }
}

/** What the user said, or a command they ran (a terminal row). */
function userItem(key: string, said: UserSaid, steered: boolean): Item {
  const terminal = said.kind === 'terminal' ? parseTerminal(said.text) : null
  const skill = said.kind === 'skill' ? parseSkill(said.text) : null

  if (skill) {
    return { attachments: said.attachments, key, kind: 'user', skill: skill.name, steered, text: skill.typed }
  }

  return terminal
    ? { key, kind: 'terminal', steered, ...terminal }
    : { attachments: said.attachments, key, kind: 'user', steered, text: said.text }
}

/** Whether an item is drawn on the trail. */
export function isPart(item: Item): item is Part {
  return (PART_KINDS as readonly string[]).includes(item.kind)
}

/** What a reasoning is about, for its closed line: its latest bold title, or its first words. The
 * one piece of the trail no reference has, approved by the user on the prototype. */
export function reasoningAbout(text: string): string {
  const titles = [...text.matchAll(/\*\*([^*\n]+)\*\*/g)]
  const title = titles.at(-1)?.[1]?.trim()

  if (title) {
    return title
  }

  const words = text.replace(/\s+/g, ' ').trim()

  return words.length > 70 ? `${words.slice(0, 69)}…` : words
}

function reasoningPart(key: string, text: string, now: null | number): Extract<Item, { kind: 'reasoning' }> {
  return { key, kind: 'reasoning', seconds: null, startedAt: now, streaming: now !== null, text }
}

function toolPart(
  key: string,
  toolId: string,
  name: string,
  args: string,
  now: null | number
): Extract<Item, { kind: 'tool' }> {
  return {
    args,
    key,
    kind: 'tool',
    name,
    progress: [],
    result: '',
    seconds: null,
    spillPath: null,
    startedAt: now,
    status: 'running',
    toolId
  }
}

/** A piece of reasoning or text: it joins the part streaming before it when that is of its kind,
 * and a new part starts otherwise, so each reads in the order it happened. */
function append(items: Item[], key: string, kind: 'reasoning' | 'text', text: string, now: number): Item[] {
  const last = items.at(-1)

  if (last?.kind === kind && last.streaming) {
    return [...items.slice(0, -1), { ...last, text: last.text + text }]
  }

  const closed = closeParts(items, now)
  const part: Item =
    kind === 'reasoning' ? reasoningPart(key, text, now) : { key, kind: 'text', role: 'open', streaming: true, text }

  return [...closed, part]
}

/** The part streaming at the end stops streaming; a reasoning part keeps how long it took. */
function closeParts(items: Item[], now: number): Item[] {
  const last = items.at(-1)

  if ((last?.kind === 'reasoning' || last?.kind === 'text') && last.streaming) {
    const closed =
      last.kind === 'reasoning' && last.startedAt !== null
        ? { ...last, seconds: Math.max(0, (now - last.startedAt) / 1000), streaming: false }
        : { ...last, streaming: false }

    return [...items.slice(0, -1), closed]
  }

  return items
}

/** The texts of the call in course, said interim or final by the server. */
function settleText(items: Item[], role: 'final' | 'interim'): Item[] {
  return items.map(item => (item.kind === 'text' && item.role === 'open' ? { ...item, role } : item))
}

function updateTool(items: Item[], toolId: string, change: (tool: Extract<Item, { kind: 'tool' }>) => Item): Item[] {
  const index = items.findIndex(item => item.kind === 'tool' && item.toolId === toolId)
  const tool = items[index]

  if (tool?.kind !== 'tool') {
    return items
  }

  return [...items.slice(0, index), change(tool), ...items.slice(index + 1)]
}

/** Whether the turn in course (since its last user message) already shows its answer. */
function turnHasFinal(items: Item[]): boolean {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]

    if ((item?.kind === 'user' || item?.kind === 'terminal') && !item.steered) {
      return false
    }

    if (item?.kind === 'text' && item.role === 'final') {
      return true
    }
  }

  return false
}

/** A tool's progress as one line: its text when it has one, its JSON otherwise. */
export function progressText(event: unknown): string {
  if (typeof event === 'string') {
    return event
  }

  if (event && typeof event === 'object' && 'text' in event && typeof event.text === 'string') {
    return event.text
  }

  return JSON.stringify(event)
}
