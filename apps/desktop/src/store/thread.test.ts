import { describe, expect, it } from 'vitest'

import type { EventParams } from '@/protocol/channel'
import { CONVERSATION_EVENT_NAMES, type MessageRow } from '@/protocol/protocol.generated'

import {
  applyEvent,
  DRAWN,
  EMPTY_THREAD,
  fromHistory,
  keepKeys,
  parseSkill,
  reasoningAbout,
  type Thread
} from './thread'

let seq = 0

function event(type: string, payload: unknown): EventParams {
  seq += 1

  return { conversation_id: 'c1', payload, seq, type }
}

/** The events at one second apart, from 0. */
function play(...events: EventParams[]): Thread {
  return events.reduce((thread, item, index) => applyEvent(thread, item, index * 1000), EMPTY_THREAD)
}

const complete = (status = 'answered', extra: Record<string, unknown> = {}) =>
  event('message.complete', { cost_usd: null, error: null, model_calls: 1, status, text: '', unsent: [], ...extra })

const toolComplete = (status: string, extra: Record<string, unknown> = {}) =>
  event('tool.complete', {
    duration_s: 1.5,
    loaded: [],
    name: 'find_files',
    result_text: 'a\nb',
    spill_path: null,
    status,
    tool_id: 't1',
    ...extra
  })

function row(fields: Partial<MessageRow> & Pick<MessageRow, 'number' | 'role' | 'text'>): MessageRow {
  return {
    active: true,
    attachments: [],
    call_id: null,
    calls: [],
    created_at: '2026-10-15T00:00:00.000Z',
    kind: 'message',
    reasoning: '',
    status: null,
    tool_name: null,
    ...fields
  }
}

describe('a turn as it arrives', () => {
  it('shows the user, the reasoning and the text as parts of their own, and ends', () => {
    const thread = play(
      event('message.start', { text: 'hi' }),
      event('reasoning.delta', { text: 'think' }),
      event('message.delta', { text: 'Hel' }),
      event('message.delta', { text: 'lo' }),
      complete('answered', { text: 'Hello' })
    )

    expect(thread.running).toBe(false)
    expect(thread.items.map(item => item.kind)).toEqual(['user', 'reasoning', 'text'])
    expect(thread.items[1]).toMatchObject({ seconds: 1, streaming: false, text: 'think' })
    expect(thread.items[2]).toMatchObject({ role: 'final', streaming: false, text: 'Hello' })
  })

  it('runs while the turn runs', () => {
    expect(play(event('message.start', { text: 'hi' })).running).toBe(true)
  })

  it('leaves a text open until the server says it was interim, and never moves it', () => {
    const streaming = play(event('message.start', { text: 'list' }), event('message.delta', { text: 'Looking.' }))
    const said = applyEvent(streaming, event('message.interim', { text: 'Looking.' }), 2000)

    expect(streaming.items[1]).toMatchObject({ role: 'open', text: 'Looking.' })
    expect(said.items[1]).toMatchObject({ role: 'interim', text: 'Looking.' })
    expect(said.items).toHaveLength(2)
  })

  it('opens a tool with its arguments, its output while it runs, and how and when it ended', () => {
    const thread = play(
      event('message.start', { text: 'list' }),
      event('message.delta', { text: 'Looking.' }),
      event('message.interim', { text: 'Looking.' }),
      event('tool.start', { args_text: '{"path":"."}', name: 'find_files', tool_id: 't1' }),
      event('tool.progress', { event: { text: 'found 2' }, name: 'find_files', tool_id: 't1' }),
      toolComplete('completed'),
      event('message.delta', { text: 'Two files.' }),
      complete()
    )

    expect(thread.items.map(item => (item.kind === 'text' ? `text:${item.role}` : item.kind))).toEqual([
      'user',
      'text:interim',
      'tool',
      'text:final'
    ])
    expect(thread.items[2]).toMatchObject({
      args: '{"path":"."}',
      progress: ['found 2'],
      result: 'a\nb',
      seconds: 1.5,
      startedAt: 3000,
      status: 'completed'
    })
  })

  it.each(['failed', 'denied', 'cancelled', 'unanswered'])('keeps a %s call as the server said', status => {
    const thread = play(
      event('message.start', { text: 'go' }),
      event('tool.start', { args_text: '{}', name: 'find_files', tool_id: 't1' }),
      toolComplete(status)
    )

    expect(thread.items[1]).toMatchObject({ status })
  })

  it('shows a message sent mid-turn where it entered', () => {
    const thread = play(
      event('message.start', { text: 'write' }),
      event('message.delta', { text: 'Sure' }),
      event('message.steered', { text: 'in English' }),
      event('message.delta', { text: 'Done' }),
      complete()
    )

    expect(thread.items.map(item => (item.kind === 'user' ? `user:${String(item.steered)}` : item.kind))).toEqual([
      'user:false',
      'text',
      'user:true',
      'text'
    ])
  })

  it('says how a turn that did not answer ended, and a call nobody closed stops running', () => {
    const thread = play(
      event('message.start', { text: 'go' }),
      event('tool.start', { args_text: '{}', name: 'run_command', tool_id: 't1' }),
      complete('failed', { error: 'TypeError' })
    )

    expect(thread.items.at(-1)).toMatchObject({ kind: 'end', status: 'failed' })
    expect(thread.items[1]).toMatchObject({ status: 'cancelled' })
  })

  it('keeps what a failed turn said, which tells the user what to do', () => {
    const said = 'There is no key for OpenAI. Store it with `atlas secrets set OPENAI_API_KEY`.'
    const thread = play(
      event('message.start', { text: 'hi' }),
      complete('failed', { error: 'ModelKeyError', text: said })
    )

    expect(thread.items.at(-1)).toMatchObject({ error: 'ModelKeyError', kind: 'end', status: 'failed', text: said })
  })

  it('shows a reply that never streamed', () => {
    const thread = play(event('message.start', { text: 'hi' }), complete('answered', { text: 'Whole' }))

    expect(thread.items.at(-1)).toMatchObject({ kind: 'text', role: 'final', text: 'Whole' })
  })

  it("shows the review's summary as its own row, with the proposals it made", () => {
    const thread = play(
      event('review.summary', { actions: ['Memory updated'], diffs: {}, proposals: [], text: 'Memory updated' }),
      event('review.summary', {
        actions: ['User profile change proposed', "Skill 'notes' updated"],
        diffs: { "Skill 'notes' updated": '--- a/SKILL.md\n+++ b/SKILL.md\n-Old.\n+New.' },
        proposals: ['3f9a1c2e'],
        text: "User profile change proposed · Skill 'notes' updated"
      })
    )

    expect(thread.items).toEqual([
      expect.objectContaining({ actions: ['Memory updated'], diffs: {}, kind: 'review', proposals: [] }),
      expect.objectContaining({
        actions: ['User profile change proposed', "Skill 'notes' updated"],
        diffs: { "Skill 'notes' updated": '--- a/SKILL.md\n+++ b/SKILL.md\n-Old.\n+New.' },
        kind: 'review',
        proposals: ['3f9a1c2e']
      })
    ])
  })
})

describe('a file a tool sent the user', () => {
  it('is a part of the turn, its attachment as the server keeps it (step 17a)', () => {
    const attachment = {
      id: 'a1',
      kind: 'audio',
      media_type: 'audio/ogg',
      name: 'atlas.ogg',
      path: '/home/attachments/a1/atlas.ogg',
      size: 42
    }
    const thread = play(event('message.start', { text: 'say it' }), event('message.file', attachment))

    expect(thread.items.at(-1)).toMatchObject({ attachment, kind: 'file' })
  })
})

describe('an event the app does not know', () => {
  it('becomes a generic part with its name and payload, from a newer server (ACP v2)', () => {
    const payload = { kind: 'web', url: 'https://example.org' }
    const thread = play(event('message.start', { text: 'hi' }), event('browser.preview', payload))

    expect(thread.items.at(-1)).toMatchObject({ kind: 'other', payload, type: 'browser.preview' })
  })

  it('every event of the protocol has its place: the thread, or the store', () => {
    // `DRAWN` is a record over the protocol's events: one missing fails `tsc`.
    expect(Object.keys(DRAWN).sort()).toEqual([...CONVERSATION_EVENT_NAMES].sort())
  })

  it.each(CONVERSATION_EVENT_NAMES.filter(name => DRAWN[name] === 'store'))(
    'leaves %s, which the store draws, out of the thread',
    name => {
      const before = play(event('message.start', { text: 'hi' }))

      expect(applyEvent(before, event(name, {}))).toBe(before)
    }
  )

  it.each(CONVERSATION_EVENT_NAMES.filter(name => DRAWN[name] === 'thread'))(
    'draws %s, never as a generic part',
    name => {
      const payloads: Record<string, unknown> = {
        'message.complete': { error: null, status: 'answered', text: '', unsent: [] },
        'review.summary': { actions: [], diffs: {}, proposals: [], text: '' },
        'tool.complete': {
          duration_s: 0,
          name: 'x',
          result_text: '',
          spill_path: null,
          status: 'completed',
          tool_id: 't'
        },
        'tool.progress': { event: 'x', name: 'x', tool_id: 't' },
        'tool.start': { args_text: '{}', name: 'x', tool_id: 't' }
      }
      const thread = play(event('message.start', { text: 'hi' }), event(name, payloads[name] ?? { text: 'x' }))

      expect(thread.items.some(item => item.kind === 'other')).toBe(false)
    }
  )
})

describe('a stored conversation', () => {
  it('shows every message, archived ones and the summary included, its calls with their results', () => {
    const thread = fromHistory(
      [
        row({ number: 1, role: 'user', text: 'old' }),
        row({
          calls: [{ args_text: '{"path":"a.md"}', id: 'r1', name: 'read_file' }],
          number: 2,
          reasoning: '**Reading** the note',
          role: 'assistant',
          text: 'Let me read it.'
        }),
        row({ call_id: 'r1', number: 3, role: 'tool', status: 'failed', text: 'not found', tool_name: 'read_file' }),
        row({ number: 4, role: 'assistant', text: 'answer' }),
        row({ kind: 'summary', number: 5, role: 'user', text: 'summary text' })
      ],
      false
    )

    expect(thread.items.map(item => (item.kind === 'text' ? `text:${item.role}` : item.kind))).toEqual([
      'user',
      'reasoning',
      'text:interim',
      'tool',
      'text:final',
      'summary'
    ])
    expect(thread.items[1]).toMatchObject({ seconds: null, streaming: false, text: '**Reading** the note' })
    expect(thread.items[3]).toMatchObject({
      args: '{"path":"a.md"}',
      name: 'read_file',
      result: 'not found',
      seconds: null,
      status: 'failed',
      toolId: 'r1'
    })
  })

  it('reads a result stored before step 9c, with no call and no status, as completed', () => {
    const thread = fromHistory([row({ number: 1, role: 'tool', text: 'result', tool_name: 'read_file' })], false)

    expect(thread.items[0]).toMatchObject({ name: 'read_file', result: 'result', status: 'completed' })
  })

  it('reopened during a turn, keeps its last calls running for their events, and no other', () => {
    const calls = (id: string) => [{ args_text: '{}', id, name: 'slow' }]
    const messages = [
      row({ number: 1, role: 'user', text: 'one' }),
      row({ calls: calls('old'), number: 2, role: 'assistant', text: '' }),
      row({ number: 3, role: 'user', text: 'two' }),
      row({ calls: calls('now'), number: 4, role: 'assistant', text: '' })
    ]
    const reopened = fromHistory(messages, true)
    const after = applyEvent(
      applyEvent(reopened, event('tool.start', { args_text: '{}', name: 'slow', tool_id: 'now' }), 0),
      event('tool.complete', {
        duration_s: 2,
        loaded: [],
        name: 'slow',
        result_text: 'done',
        spill_path: null,
        status: 'completed',
        tool_id: 'now'
      }),
      0
    )

    expect(reopened.items.flatMap(item => (item.kind === 'tool' ? [item.status] : []))).toEqual([
      'cancelled',
      'running'
    ])
    expect(after.items.filter(item => item.kind === 'tool')).toHaveLength(2)
    expect(after.items.at(-1)).toMatchObject({ result: 'done', status: 'completed' })
  })
})

describe('a conversation read again', () => {
  it('keeps the keys of what came as events, so nothing on screen is drawn again', () => {
    const live = play(event('message.start', { text: 'hi' }), event('message.delta', { text: 'Hello.' }), complete())
    const read = fromHistory(
      [row({ number: 1, role: 'user', text: 'hi' }), row({ number: 2, role: 'assistant', text: 'Hello.' })],
      false
    )

    const kept = keepKeys(live, read)

    expect(kept.items.map(item => item.key)).toEqual(live.items.map(item => item.key))
    expect(kept.items.map(item => item.kind)).toEqual(['user', 'text'])
  })

  it('takes the read keys where the kinds part, and never a key another read item holds', () => {
    const shown = fromHistory(
      [row({ number: 2, role: 'user', text: 'two' }), row({ number: 3, role: 'assistant', text: 'three' })],
      false
    )
    // Read again from an earlier message: the first user's key on screen is the second's read.
    const read = fromHistory(
      [row({ number: 1, role: 'user', text: 'one' }), row({ number: 2, role: 'user', text: 'two' })],
      false
    )

    expect(keepKeys(shown, read).items.map(item => item.key)).toEqual(['m1', 'm2'])
  })
})

describe('a notice', () => {
  it('starts a turn as a notice, not as the user, live and when read back', () => {
    const notice = '[IMPORTANT: Background command cmd-1 completed normally (exit code 0).]'
    const live = play(event('message.start', { kind: 'notice', text: notice }), complete('answered', { text: 'Done.' }))
    const stored = fromHistory([row({ kind: 'notice', number: 1, role: 'user', text: notice })], false)

    expect(live.items.map(item => item.kind)).toEqual(['notice', 'text'])
    expect(stored.items).toEqual([{ key: 'm1', kind: 'notice', text: notice }])
  })
})

describe('a skill the user invoked', () => {
  const loaded = [
    '[IMPORTANT: The user has invoked the "weekly-report" skill, indicating they want you to follow its instructions.',
    'Gather the numbers.',
    '[Skill directory: /skills/weekly-report]',
    'The user has provided the following instruction alongside the skill invocation: May and June'
  ].join('\n\n')

  it('reads back as the user typed it, live and from the history', () => {
    const live = play(event('message.start', { attachments: [], kind: 'skill', text: loaded }), complete())
    const stored = fromHistory([row({ kind: 'skill', number: 1, role: 'user', text: loaded })], false)
    const typed = { kind: 'user', skill: 'weekly-report', steered: false, text: '/weekly-report May and June' }

    expect(live.items[0]).toMatchObject(typed)
    expect(stored.items).toEqual([{ attachments: [], key: 'm1', ...typed }])
    expect(parseSkill('[IMPORTANT: The user has invoked the "notes" skill, ...]\n\nBody.')).toEqual({
      name: 'notes',
      typed: '/notes'
    })
    expect(parseSkill('Hello')).toBeNull()
  })
})

describe('what a reasoning is about', () => {
  it('is its latest bold title, or its first words', () => {
    expect(reasoningAbout('**Reading the log**\n\nFirst.\n\n**Fixing the sum**\n\nThen.')).toBe('Fixing the sum')
    expect(reasoningAbout('The user wants the tests fixed.')).toBe('The user wants the tests fixed.')
    expect(reasoningAbout('word '.repeat(30))).toHaveLength(70)
  })
})
