/**
 * An executor's session as the screen shows it (step 16f): the trail of a conversation of Atlas's
 * (step 9c), built from what its plugin reads back where the harness keeps it, and from what its
 * instance does live. Nothing of it is copied into Atlas. Pure, so every transition is tested
 * without a window.
 *
 * The harness's transcript is the truth: when a turn ends the session is read again, and what the
 * live events drew is replaced by it once it holds their last words (`caughtUp`). After a reconnect
 * it is read again too: the live events sent meanwhile are not sent twice.
 */
import { t } from '@/i18n'
import type { AttachmentRow, InstanceActivityPayload, TranscriptRow } from '@/protocol/protocol.generated'

import { type Item, parseTerminal, type Thread } from './thread'

type Tool = Extract<Item, { kind: 'tool' }>

/** A session read back: the user's prompts, the harness's words, its tools with IN and OUT. In each
 * turn the harness's last words are its answer, the others its interim text (step 9c). `working`:
 * its instance is at work, so its last turn goes on, its calls with no result still running (or
 * waiting for the user), and none of its words the answer yet. */
export function fromTranscript(entries: TranscriptRow[], working = false): Thread {
  const items: Item[] = []
  const calls = new Map<string, number>()

  entries.forEach((entry, index) => {
    const key = `s${String(index)}`

    switch (entry.kind) {
      case 'text':
        items.push(
          entry.by === 'user'
            ? said(key, entry.text, false, entry.attachments)
            : { key, kind: 'text', role: 'interim', streaming: false, text: entry.text }
        )
        break

      case 'tool':
      case 'question':
        if (entry.call) {
          calls.set(entry.call, items.length)
        }

        items.push(tool(key, entry.call || key, entry.tool, entry.text, 'running'))
        break

      case 'result':
      case 'answer': {
        const at = entry.call ? calls.get(entry.call) : undefined
        const found = at === undefined ? undefined : items[at]
        const done = { result: entry.text, status: entry.failed ? 'failed' : 'completed' } as const

        if (at !== undefined && found?.kind === 'tool') {
          items[at] = { ...found, ...done }
        } else {
          items.push({ ...tool(key, entry.call || key, 'tool', '', 'running'), ...done })
        }

        break
      }

      case 'compacted':
        // Where the harness compacted the session: its summary stands for what came before.
        items.push({ key, kind: 'status', text: entry.text })
        break
    }
  })

  return { items: settle(items, working), running: working }
}

/** The session after one thing its instance did. `now` starts a running tool's timer. */
export function applyActivity(
  thread: Thread,
  activity: InstanceActivityPayload,
  seq: number,
  now = Date.now()
): Thread {
  const key = `a${String(seq)}`

  switch (activity.kind) {
    case 'prompted':
      return { items: [...thread.items, said(key, activity.text, false, activity.attachments)], running: true }

    case 'steered':
      return { ...thread, items: [...thread.items, said(key, activity.text, true, activity.attachments)] }

    case 'said':
      return {
        ...thread,
        items: [...thread.items, { key, kind: 'text', role: 'interim', streaming: false, text: activity.text }]
      }

    case 'thought':
      return {
        ...thread,
        items: [
          ...thread.items,
          { key, kind: 'reasoning', seconds: null, startedAt: null, streaming: false, text: activity.text }
        ]
      }

    case 'called': {
      // Its text is the tool and its input, as the workbench always said it.
      const args = activity.text.startsWith(`${activity.tool} `)
        ? activity.text.slice(activity.tool.length + 1)
        : activity.text
      const started = { ...tool(key, activity.call_id || key, activity.tool, args, 'running'), startedAt: now }

      return { ...thread, items: [...thread.items, started] }
    }

    case 'returned': {
      const index = thread.items.findIndex(item => item.kind === 'tool' && item.toolId === activity.call_id)
      const found = thread.items[index]

      if (found?.kind !== 'tool') {
        return thread
      }

      const done: Tool = {
        ...found,
        result: activity.text,
        seconds: found.startedAt === null ? null : Math.max(0, (now - found.startedAt) / 1000),
        status: activity.failed ? 'failed' : 'completed'
      }

      return { ...thread, items: [...thread.items.slice(0, index), done, ...thread.items.slice(index + 1)] }
    }

    case 'answered':
    case 'compacted':
    case 'note':
      // `note`: what the screen says of a switch, such as the model it went on with.
      return { ...thread, items: [...thread.items, { key, kind: 'status', text: activity.text }] }

    case 'resume': {
      // Its plan's limit stopped it: it goes on by itself when the plan resets, or waits.
      const text = activity.failed ? t.executor.noReset : t.executor.resumesAt(resumeTime(activity.text))

      return { ...thread, items: [...thread.items, { key, kind: 'status', text }] }
    }

    case 'account':
    case 'route': {
      // Its plan's limit was reached, and it went on elsewhere, or says why it could not.
      const text = activity.failed ? activity.text : t.route.wentOn(activity.text)

      return { ...thread, items: [...thread.items, { key, kind: 'status', text }] }
    }

    default:
      // A kind this app does not know, from a newer server: shown, not dropped (ACP v2).
      return { ...thread, items: [...thread.items, { key, kind: 'other', payload: activity, type: activity.kind }] }
  }
}

/** When a session goes on, as the user reads a time: the hour, and the day when it is not today. */
export function resumeTime(at: string, now = new Date()): string {
  const when = new Date(at)
  const today = when.toDateString() === now.toDateString()

  return when.toLocaleString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    ...(today ? {} : { day: 'numeric', weekday: 'short' })
  })
}

/** A line the screen said in a session, where it said it: `at` items were above it. */
export interface SessionNote {
  at: number
  text: string
}

/** The session with the screen's own lines put back where they were said, in order, each below
 * what was above it then, the earlier lines included (a session read shorter, at its end). */
export function withNotes(thread: Thread, notes: SessionNote[]): Thread {
  if (!notes.length) {
    return thread
  }

  const items = [...thread.items]

  notes.forEach((note, index) => {
    items.splice(Math.min(note.at, items.length), 0, { key: `n${String(index)}`, kind: 'status', text: note.text })
  })

  return { ...thread, items }
}

/** What the user gave the harness: their words and the files they attached (step 16i), or a
 * command they ran in the session's terminal, whose output went to the harness in Claude Code's
 * bash-mode tags (step 16f). */
function said(key: string, text: string, steered: boolean, attachments: AttachmentRow[] = []): Item {
  const terminal = parseTerminal(text)

  return terminal ? { key, kind: 'terminal', steered, ...terminal } : { attachments, key, kind: 'user', steered, text }
}

/** Whether a session read back ends on the harness's last words on screen. Claude Code writes its
 * file a moment after it says them, so a read as its turn ends can come before its answer (found on
 * 16/10/2026: the answer showed only once the session was opened again). */
export function caughtUp(entries: TranscriptRow[], thread: Thread): boolean {
  const shown = thread.items.findLast(item => item.kind === 'text')
  const kept = entries.findLast(entry => entry.kind === 'text' && entry.by === 'harness')

  return shown?.kind !== 'text' || kept?.text === shown.text
}

/** The turn ended: the instance waits for its next prompt. */
export function turnEnded(thread: Thread): Thread {
  return thread.running ? { items: settle(thread.items), running: false } : thread
}

function tool(key: string, toolId: string, name: string, args: string, status: Tool['status']): Tool {
  return {
    args,
    key,
    kind: 'tool',
    name,
    progress: [],
    result: '',
    seconds: null,
    spillPath: null,
    startedAt: null,
    status,
    toolId
  }
}

/** Each turn's last words from the harness are its answer; a call that never got its result was
 * cut short (the harness stopped first). While the session `working`, its last turn is left as it
 * goes on: a session opened while its harness waits for the user's permission showed that call as
 * cut short (found by the UI review, 25/10/2026). */
function settle(items: Item[], working = false): Item[] {
  // Where the turn going on starts: its prompt; with none working, past the end.
  const going = working
    ? items.findLastIndex(item => (item.kind === 'user' || item.kind === 'terminal') && !item.steered)
    : items.length
  const out = items.map((item, index) =>
    item.kind === 'tool' && item.status === 'running' && index < going
      ? { ...item, status: 'cancelled' as const }
      : item
  )
  let answered = false

  for (let index = out.length - 1; index >= 0; index -= 1) {
    const item = out[index]

    if ((item?.kind === 'user' || item?.kind === 'terminal') && !item.steered) {
      answered = false
    } else if (item?.kind === 'text' && !answered && index < going) {
      out[index] = { ...item, role: 'final' }
      answered = true
    }
  }

  return out
}
