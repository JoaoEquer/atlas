import { describe, expect, it } from 'vitest'

import { t } from '@/i18n'
import type { AttachmentRow, InstanceActivityPayload, TranscriptRow } from '@/protocol/protocol.generated'

import { applyActivity, caughtUp, fromTranscript, resumeTime, turnEnded, withNotes } from './session'
import { EMPTY_THREAD, type Item, parseTerminal, terminalText } from './thread'

function entry(kind: string, text: string, extra: Partial<TranscriptRow> = {}): TranscriptRow {
  return { at: '', attachments: [], by: 'harness', call: '', failed: false, kind, text, tool: '', ...extra }
}

function activity(kind: string, text: string, extra: Partial<InstanceActivityPayload> = {}): InstanceActivityPayload {
  return { attachments: [], call_id: '', failed: false, instance_id: 'in-1', kind, text, tool: '', ...extra }
}

/** What the trail draws of each item: its kind and what tells it apart. */
function shape(items: Item[]): string[] {
  return items.map(item => {
    switch (item.kind) {
      case 'user':
        return `user${item.steered ? ' (steered)' : ''}: ${item.text}`
      case 'text':
        return `${item.role}: ${item.text}`
      case 'tool':
        return `tool ${item.name} ${item.args} -> ${item.status} ${item.result}`.trim()
      default:
        return item.kind
    }
  })
}

describe("an executor's session on screen (step 16f)", () => {
  it('reads as the trail of a conversation: each turn has its answer, its tools IN and OUT', () => {
    const thread = fromTranscript([
      entry('text', 'Make the total show cents.', { by: 'user' }),
      entry('tool', '{"file_path": "cart.js"}', { call: 't1', tool: 'Read' }),
      entry('result', 'const total', { call: 't1' }),
      entry('text', 'Reading the cart.'),
      entry('text', 'Fixed: two decimals.'),
      entry('text', 'Now run the tests.', { by: 'user' }),
      entry('tool', '{"command": "npm test"}', { call: 't2', tool: 'Bash' }),
      entry('result', '1 failing', { call: 't2', failed: true }),
      entry('question', 'Fix the test or the code?', { call: 't3', tool: 'AskUserQuestion' }),
      entry('answer', 'The code', { call: 't3' }),
      entry('tool', '{"command": "npm test"}', { call: 't4', tool: 'Bash' }),
      entry('text', 'The code rounds now.')
    ])

    expect(shape(thread.items)).toEqual([
      'user: Make the total show cents.',
      'tool Read {"file_path": "cart.js"} -> completed const total',
      'interim: Reading the cart.',
      'final: Fixed: two decimals.',
      'user: Now run the tests.',
      'tool Bash {"command": "npm test"} -> failed 1 failing',
      'tool AskUserQuestion Fix the test or the code? -> completed The code',
      // Its result never came: the harness stopped first.
      'tool Bash {"command": "npm test"} -> cancelled',
      'final: The code rounds now.'
    ])
    expect(thread.running).toBe(false)
  })

  it('read while its instance works, leaves the turn going on open: its call runs, nothing is the answer', () => {
    const entries = [
      entry('text', 'Clean the build.', { by: 'user' }),
      entry('tool', '{"command": "ls"}', { call: 't1', tool: 'Bash' }),
      entry('text', 'Fixed earlier.'),
      entry('text', 'Run the tests.', { by: 'user' }),
      entry('text', 'Running them.'),
      // The harness waits for the user's permission on this one: no result yet.
      entry('tool', '{"command": "npm test"}', { call: 't2', tool: 'Bash' })
    ]
    const thread = fromTranscript(entries, true)

    expect(shape(thread.items)).toEqual([
      'user: Clean the build.',
      // A turn before: it stopped there.
      'tool Bash {"command": "ls"} -> cancelled',
      'final: Fixed earlier.',
      'user: Run the tests.',
      'interim: Running them.',
      'tool Bash {"command": "npm test"} -> running'
    ])
    expect(thread.running).toBe(true)
  })

  it('grows live with what its instance does, and settles when the turn ends', () => {
    let thread = fromTranscript([entry('text', 'Hi.', { by: 'user' }), entry('text', 'Hello.')])
    const steps = [
      activity('prompted', 'Fix the header.'),
      activity('thought', 'The logo is wide.'),
      activity('said', 'Looking at it.'),
      activity('called', 'Edit {"file_path": "index.html"}', { call_id: 'c1', tool: 'Edit' }),
      activity('steered', 'Keep the logo.'),
      activity('returned', 'done', { call_id: 'c1' }),
      activity('answered', 'allowed Edit'),
      activity('said', 'Fixed, the logo kept.')
    ]
    steps.forEach((step, index) => {
      thread = applyActivity(thread, step, index, 1_000 + index * 500)
    })

    expect(thread.running).toBe(true)
    const edit = thread.items.find(item => item.kind === 'tool')
    expect(edit?.kind === 'tool' && [edit.args, edit.status, edit.seconds]).toEqual([
      '{"file_path": "index.html"}',
      'completed',
      1
    ])

    thread = turnEnded(thread)

    expect(thread.running).toBe(false)
    expect(shape(thread.items)).toEqual([
      'user: Hi.',
      'final: Hello.',
      'user: Fix the header.',
      'reasoning',
      'interim: Looking at it.',
      'tool Edit {"file_path": "index.html"} -> completed done',
      'user (steered): Keep the logo.',
      'status',
      'final: Fixed, the logo kept.'
    ])
  })

  it('shows a step it does not know, and ignores a result of no call it drew', () => {
    const thread = applyActivity(
      applyActivity(EMPTY_THREAD, activity('returned', 'orphan', { call_id: 'nope' }), 1),
      activity('rewound', 'x'),
      2
    )

    expect(shape(thread.items)).toEqual(['other'])
  })

  it('takes a read as its turn ends only once the harness kept the last words on screen', () => {
    const live = turnEnded(
      applyActivity(
        applyActivity(EMPTY_THREAD, activity('prompted', 'Brief me.'), 1),
        activity('said', 'The brief.'),
        2
      )
    )
    const before = [entry('text', 'Brief me.', { by: 'user' })]

    expect(caughtUp(before, live)).toBe(false)
    expect(caughtUp([...before, entry('text', 'The brief.')], live)).toBe(true)
    // The user's words are not the harness's; a screen with none of its words waits for nothing.
    expect(caughtUp([entry('text', 'The brief.', { by: 'user' })], live)).toBe(false)
    expect(caughtUp(before, turnEnded(applyActivity(EMPTY_THREAD, activity('prompted', 'Brief me.'), 1)))).toBe(true)
  })

  it('shows where the harness compacted the session, live and read back alike', () => {
    const said = 'The conversation was compacted from about 180,000 to 12,000 tokens.'
    const live = applyActivity(EMPTY_THREAD, activity('compacted', said), 1)
    const reopened = fromTranscript([entry('compacted', said), entry('text', 'Went on.')])

    expect(live.items).toEqual([{ key: 'a1', kind: 'status', text: said }])
    expect(reopened.items[0]).toEqual({ key: 's0', kind: 'status', text: said })
    expect(shape(reopened.items)).toEqual(['status', 'final: Went on.'])
  })

  it("says when a session stopped at its plan's limit goes on by itself, or that it waits", () => {
    const at = '2026-11-03T18:05:00Z'
    const going = applyActivity(EMPTY_THREAD, activity('resume', at), 1)
    const waiting = applyActivity(EMPTY_THREAD, activity('resume', '', { failed: true }), 1)

    expect(going.items).toEqual([{ key: 'a1', kind: 'status', text: t.executor.resumesAt(resumeTime(at)) }])
    expect(waiting.items).toEqual([{ key: 'a1', kind: 'status', text: t.executor.noReset }])
    // Today, the hour alone; another day, the day too.
    const hour = new Date(at).toLocaleString(undefined, { hour: '2-digit', minute: '2-digit' })
    expect(resumeTime(at, new Date(at))).toBe(hour)
    expect(resumeTime(at, new Date('2026-11-01T12:00:00Z'))).not.toBe(hour)
    expect(resumeTime(at, new Date('2026-11-01T12:00:00Z'))).toContain(hour)
  })

  it("puts the screen's own lines back where they were said when the session is read again", () => {
    const read = fromTranscript([
      entry('text', 'Hi.', { by: 'user' }),
      entry('text', 'Hello.'),
      entry('text', 'Go on.', { by: 'user' })
    ])
    const notes = [
      { at: 2, text: 'Now on Work.' },
      { at: 4, text: 'Now on Home.' }
    ]

    expect(shape(withNotes(read, notes).items)).toEqual([
      'user: Hi.',
      'final: Hello.',
      'status',
      'user: Go on.',
      'status'
    ])
    expect(withNotes(read, []).items).toBe(read.items)
  })

  it('shows the files a prompt went with above it, live and read back alike (step 16i)', () => {
    const shot: AttachmentRow = {
      id: 'a1b2',
      kind: 'image',
      media_type: 'image/png',
      name: 'shot.png',
      path: '/home/attachments/a1b2/shot.png',
      size: 2048
    }
    const live = applyActivity(EMPTY_THREAD, activity('prompted', 'What is wrong here?', { attachments: [shot] }), 1)
    const steered = applyActivity(live, activity('steered', '', { attachments: [shot] }), 2)
    const reopened = fromTranscript([entry('text', 'What is wrong here?', { attachments: [shot], by: 'user' })])

    expect(live.items[0]).toMatchObject({ attachments: [shot], kind: 'user', text: 'What is wrong here?' })
    expect(steered.items[1]).toMatchObject({ attachments: [shot], kind: 'user', steered: true, text: '' })
    expect(reopened.items[0]).toMatchObject({ attachments: [shot], kind: 'user', text: 'What is wrong here?' })
  })
})

describe('a command the user ran, as a terminal row (step 16f)', () => {
  it('reads back from the tags the model and the harness read, live and reopened alike', () => {
    const text = terminalText({ command: 'npm test', exitCode: 1, output: '1 failing', status: 'completed' })
    const reopened = fromTranscript([entry('text', text, { by: 'user' }), entry('text', 'It fails on rounding.')])
    const live = applyActivity(EMPTY_THREAD, activity('prompted', text), 1)

    for (const thread of [reopened, live]) {
      expect(thread.items[0]).toMatchObject({ command: 'npm test', exitCode: 1, kind: 'terminal', output: '1 failing' })
    }

    expect(
      parseTerminal(terminalText({ command: 'npm run dev', exitCode: null, output: 'up', status: 'running' }))
    ).toEqual({
      command: 'npm run dev',
      exitCode: null,
      output: 'up',
      state: 'It is still running after 30 seconds: this is its output so far.'
    })
    expect(parseTerminal('Just words.')).toBeNull()
  })
})
