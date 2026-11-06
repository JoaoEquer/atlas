import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import type { Item } from '@/store/thread'

import { $modelMenu } from './model-picker'
import { ThreadView } from './thread-view'
import { clampForDisplay, inputFields, MAX_SHOWN_CHARS, type Shown, toolTitle } from './tool-row'

afterEach(cleanup)
beforeAll(() => {
  // jsdom lays nothing out, so it has no scrollIntoView.
  Element.prototype.scrollIntoView = () => undefined
})

const text = (value: string, role: 'final' | 'interim' | 'open' = 'final'): Item => ({
  key: `t-${role}-${String(value.length)}`,
  kind: 'text',
  role,
  streaming: false,
  text: value
})

const tool = (fields: Partial<Extract<Item, { kind: 'tool' }>>): Item => ({
  args: '{}',
  key: 'tool',
  kind: 'tool',
  name: 'find_files',
  progress: [],
  result: '',
  seconds: null,
  spillPath: null,
  startedAt: null,
  status: 'completed',
  toolId: 't1',
  ...fields
})

describe('a code block in a reply', () => {
  it('can be copied and not downloaded', async () => {
    const block = 'Run it:\n\n```powershell\natlas secrets set ANTHROPIC_API_KEY\n```\n'
    const { container } = render(<ThreadView thread={{ items: [text(block)], running: false }} />)

    expect(await screen.findByTitle('Copy Code')).toBeTruthy()
    expect(container.querySelector('[data-streamdown="code-block-download-button"]')).toBeNull()
  })
})

describe('the trail', () => {
  it('draws one dot per part, colored by its state, and leaves the user off the line', () => {
    const { container } = render(
      <ThreadView
        thread={{
          items: [
            { attachments: [], key: 'u', kind: 'user', steered: false, text: 'go' },
            { key: 'r', kind: 'reasoning', seconds: 4, startedAt: 0, streaming: false, text: '**Reading** the log' },
            text('Let me look.', 'interim'),
            tool({ key: 'a', status: 'completed' }),
            tool({ key: 'b', status: 'failed', toolId: 't2' }),
            tool({ key: 'c', status: 'denied', toolId: 't3' }),
            text('Done.')
          ],
          running: false
        }}
      />
    )

    const dots = [...container.querySelectorAll('[data-testid="part"]')].map(part => part.getAttribute('data-dot'))
    expect(dots).toEqual(['reasoning', 'interim', 'ok', 'failed', 'denied', 'final'])
    const trails = container.querySelectorAll<HTMLElement>('[data-testid="trail"]')
    expect(trails).toHaveLength(1)
    expect(within(trails[0] as HTMLElement).queryByText('go')).toBeNull()
  })

  it('says what a reasoning is about, and how long it took', () => {
    render(
      <ThreadView
        thread={{
          items: [
            { key: 'r', kind: 'reasoning', seconds: 4, startedAt: 0, streaming: false, text: '**Reading** the log' }
          ],
          running: false
        }}
      />
    )

    expect(screen.getByTestId('reasoning').querySelector('summary')?.textContent).toBe(
      `${t.thread.thoughtFor(t.elapsed(4))} · Reading`
    )
  })

  it('reopened, a reasoning only says it thought', () => {
    const reasoning: Item = { key: 'r', kind: 'reasoning', seconds: null, startedAt: null, streaming: false, text: 'x' }
    render(<ThreadView thread={{ items: [reasoning], running: false }} />)

    expect(screen.getByTestId('reasoning').querySelector('summary')?.textContent).toBe(`${t.thread.thought} · x`)
  })

  it('shows an event it does not know as its name and its payload', () => {
    const other: Item = { key: 'o', kind: 'other', payload: { a: 1 }, type: 'browser.preview' }
    render(<ThreadView thread={{ items: [other], running: false }} />)

    const shown = screen.getByTestId('other-part')
    expect(shown.textContent).toContain('browser.preview')
    expect(shown.textContent).toContain('"a": 1')
  })

  it('says why a turn failed in the words the server sent, not only the kind of error', () => {
    const said = 'There is no key for OpenAI. Store it with `atlas secrets set OPENAI_API_KEY`.'
    const end: Item = { error: 'ModelKeyError', key: 'e', kind: 'end', status: 'failed', text: said }
    render(<ThreadView thread={{ items: [end], running: false }} />)

    expect(screen.getByTestId('turn-end').textContent).toBe(t.thread.failed(said))
  })
})

describe('a tool', () => {
  it('opens on IN and OUT, one argument bare and several under their names', () => {
    expect(inputFields('{"path":"a.md"}')).toEqual([{ name: 'path', value: 'a.md' }])
    expect(inputFields('{"path":"a.md","limit":5}')).toEqual([
      { name: 'path', value: 'a.md' },
      { name: 'limit', value: '5' }
    ])
    expect(inputFields('not json')).toEqual([{ name: '', value: 'not json' }])
    expect(inputFields('{}')).toEqual([])
  })

  it("titles Atlas's own tools by what they do, and leaves the others generic", () => {
    const titled = (name: string, args: unknown, shown: Shown = 'completed') => {
      const title = toolTitle(name, JSON.stringify(args), shown)

      return title && `${title.action} ${title.target}${title.prose ? ' (words)' : ''}`
    }

    expect(titled('run_command', { command: 'uv run pytest -q\necho' })).toBe('Ran uv run pytest -q')
    expect(titled('read_file', { path: 'a.md' }, 'running')).toBe('Reading a.md')
    expect(titled('web_search', { query: 'kv cache' }, 'failed')).toBe('Searched “kv cache”')
    expect(titled('write_file', { content: 'x', path: 'notes/today.md' })).toBe('Wrote notes/today.md')
    expect(
      titled('ask_user', { questions: [{ question: 'Which colour?' }, { question: 'Which size?' }] }, 'waiting')
    ).toBe('Ask Which colour? and 1 more (words)')
    expect(titled('delegate', { tasks: [{ goal: 'Fix the footer' }] }, 'running')).toBe(
      'Delegating: Fix the footer (words)'
    )
    expect(titled('delegate', { action: 'stop', subagent_id: 'sa-1' })).toBe('Stopped sa-1')
    expect(titled('cronjob_manage', { action: 'create', name: 'Daily digest', schedule: 'every day 9am' })).toBe(
      'Scheduled Daily digest'
    )
    expect(titled('secrets', { action: 'request', name: 'STRIPE_API_KEY' }, 'waiting')).toBe(
      'Ask for the key STRIPE_API_KEY'
    )
    expect(titled('memory', { action: 'add', content: 'Likes tea.', target: 'user' })).toBe(
      'Updated your profile: Likes tea. (words)'
    )
    expect(titled('browser_exec', { code: "# Searching for paper towels\npage.goto('x')" }, 'running')).toBe(
      'Browser: Searching for paper towels (words)'
    )
    expect(titled('plan', {})).toBe('Read the plan ')
    expect(titled('tool_search', { query: 'secrets' })).toBe('Searched the tools for “secrets”')
    // A tool with no words for what it does, or whose arguments name nothing, keeps the generic line.
    expect(titled('mcp__notes__add', { text: 'x' })).toBeNull()
    expect(titled('cronjob_manage', { action: 'teleport' })).toBeNull()
    expect(titled('read_file', {})).toBeNull()
  })

  it('never titles a call that did not run as one that did', () => {
    const command = '{"command":"rm -r build"}'

    for (const shown of ['waiting', 'denied', 'cancelled', 'unanswered'] as const) {
      expect(toolTitle('run_command', command, shown)?.action).toBe(t.tool.titles.run_command?.plain)
    }
  })

  it('shows a call a card holds as waiting for the user, not as running', () => {
    const { container } = render(
      <ThreadView
        thread={{
          items: [
            tool({ args: '{"command":"rm -r build"}', key: 'a', name: 'run_command', startedAt: 0, status: 'running' }),
            tool({ key: 'b', name: 'ask_user', startedAt: 0, status: 'running', toolId: 't2' }),
            tool({ key: 'c', name: 'find_files', startedAt: 0, status: 'running', toolId: 't3' })
          ],
          running: true
        }}
        waiting={
          new Map([
            ['t1', 'approval'],
            ['t2', 'answer']
          ])
        }
      />
    )

    const parts = [...container.querySelectorAll('[data-testid="part"]')]
    expect(parts.map(part => part.getAttribute('data-dot'))).toEqual(['waiting', 'waiting', 'running'])
    const rows = screen.getAllByTestId('tool-row')
    expect(rows.map(row => row.getAttribute('data-status'))).toEqual(['waiting', 'waiting', 'running'])
    expect(within(rows[0] as HTMLElement).getByText(t.tool.titles.run_command?.plain ?? '')).toBeTruthy()
    expect(screen.getAllByTestId('tool-state')[0]?.textContent).toBe(t.tool.waitingFor.approval)
    expect(screen.getAllByTestId('tool-state')[1]?.textContent).toBe(t.tool.waitingFor.answer)
  })

  it('says it runs once: in the title when it has one, else beside the name', () => {
    render(
      <ThreadView
        thread={{
          items: [
            tool({ args: '{"command":"ping"}', key: 'a', name: 'run_command', status: 'running' }),
            tool({ key: 'b', status: 'running', toolId: 't2' })
          ],
          running: true
        }}
      />
    )

    expect(screen.getAllByTestId('tool-state').map(state => state.textContent)).toEqual(['', t.tool.running])
  })

  it('cuts a long output at 20,000 characters and copies it whole', () => {
    const long = 'x'.repeat(MAX_SHOWN_CHARS + 5)
    render(<ThreadView thread={{ items: [tool({ result: long })], running: false }} />)

    expect(clampForDisplay(long).startsWith('x'.repeat(MAX_SHOWN_CHARS))).toBe(true)
    expect(clampForDisplay(long)).toContain(t.tool.clamped('5'))
    expect(screen.getByRole('button', { name: t.tool.copyAll })).toBeTruthy()
  })

  it('says how it ended: a failure with its time, a refusal or no answer without one, success only its time', () => {
    render(
      <ThreadView
        thread={{
          items: [
            tool({ key: 'a', seconds: 2, status: 'completed' }),
            tool({ key: 'b', seconds: 1, status: 'failed', toolId: 't2' }),
            tool({ key: 'c', status: 'denied', toolId: 't3' }),
            tool({ key: 'd', status: 'unanswered', toolId: 't4' })
          ],
          running: false
        }}
      />
    )

    expect(screen.getAllByTestId('tool-state').map(state => state.textContent)).toEqual([
      t.duration(2),
      `${t.tool.failed} · ${t.duration(1)}`,
      t.tool.denied,
      t.tool.unanswered
    ])
  })
})

describe('a skill the user invoked', () => {
  it('shows the /name they typed, marked as a skill loaded', () => {
    const items: Item[] = [
      { attachments: [], key: 'u', kind: 'user', skill: 'weekly-report', steered: false, text: '/weekly-report May' }
    ]
    render(<ThreadView thread={{ items, running: false }} />)

    expect(screen.getByTestId('user-message').textContent).toContain('/weekly-report May')
    expect(screen.getByTestId('skill-invoked').textContent).toBe(t.thread.skill)
    expect(screen.getByTestId('skill-invoked').getAttribute('data-skill')).toBe('weekly-report')
  })
})

describe('a turn at work', () => {
  const working = () => screen.queryByRole('status', { name: 'Working' })

  it('shows its three dots only where nothing on the trail says it works', () => {
    const { rerender } = render(<ThreadView thread={{ items: [text('Let me look.', 'interim')], running: true }} />)
    expect(working()).not.toBeNull()

    rerender(<ThreadView thread={{ items: [tool({ status: 'running' })], running: true }} />)
    expect(working()).toBeNull()

    rerender(
      <ThreadView
        thread={{ items: [{ ...text('Here', 'open'), streaming: true } as Item], running: true }}
        waiting={new Map()}
      />
    )
    expect(working()).toBeNull()

    rerender(<ThreadView thread={{ items: [tool({ status: 'completed' })], running: false }} />)
    expect(working()).toBeNull()
  })
})

describe('following the reply', () => {
  /** jsdom lays nothing out: the thread's height, window and place are set by hand. */
  function scrollTo(top: number) {
    const thread = screen.getByTestId('thread')
    Object.defineProperty(thread, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(thread, 'clientHeight', { configurable: true, value: 200 })
    thread.scrollTop = top
    fireEvent.scroll(thread)
  }

  it('stays still while the user reads above, and a click brings them back down', () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const { rerender } = render(<ThreadView thread={{ items: [text('a')], running: true }} />)

    scrollTo(800)
    expect(screen.queryByTestId('to-latest')).toBeNull()
    scrollTo(300)
    scroll.mockClear()
    rerender(<ThreadView thread={{ items: [text('a'), tool({})], running: true }} />)
    expect(scroll).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: t.thread.toLatest }))
    expect(scroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'end' })
    rerender(<ThreadView thread={{ items: [text('a'), tool({}), text('done')], running: true }} />)
    expect(scroll).toHaveBeenLastCalledWith({ block: 'end' })
    Element.prototype.scrollIntoView = () => undefined
  })

  it('follows again when the user sends a message', () => {
    const scroll = vi.fn()
    Element.prototype.scrollIntoView = scroll
    const { rerender } = render(<ThreadView thread={{ items: [text('a')], running: true }} />)

    scrollTo(800)
    scrollTo(300)
    scroll.mockClear()
    const sent: Item = { attachments: [], key: 'u', kind: 'user', steered: true, text: 'and this' }
    rerender(<ThreadView thread={{ items: [text('a'), sent], running: true }} />)
    expect(scroll).toHaveBeenCalledWith({ block: 'end' })
    Element.prototype.scrollIntoView = () => undefined
  })
})

describe('a conversation read, sent to and come back to (the UI review, 25/10/2026)', () => {
  it('being read shows a skeleton after a moment, never the invitation to start', () => {
    vi.useFakeTimers()
    render(<ThreadView loading thread={{ items: [], running: false }} />)

    expect(screen.queryByText(t.thread.empty)).toBeNull()
    const skeleton = screen.getByTestId('skeleton')
    expect(skeleton.getAttribute('aria-label')).toBe(t.thread.reading)
    // A quick read never flashes it.
    expect(skeleton.childElementCount).toBe(0)
    act(() => {
      vi.advanceTimersByTime(400)
    })
    expect(screen.getByTestId('skeleton').childElementCount).toBeGreaterThan(0)
    vi.useRealTimers()
  })

  it('shows a message on its way at once, saying whether it is sent or waits to enter the turn', () => {
    render(
      <ThreadView
        sending={[
          { attachments: [], key: 's1', state: 'sending', text: 'Plan the trip' },
          { attachments: [], key: 's2', state: 'queued', text: 'Use Vitest' }
        ]}
        thread={{ items: [], running: false }}
      />
    )

    expect(screen.queryByText(t.thread.empty)).toBeNull()
    const rows = screen.getAllByTestId('sending-message')
    expect(rows.map(row => row.getAttribute('data-state'))).toEqual(['sending', 'queued'])
    expect(within(rows[0] as HTMLElement).getByText(t.thread.sending)).toBeTruthy()
    expect(within(rows[1] as HTMLElement).getByText(t.thread.queued)).toBeTruthy()
  })

  it('finds again where the user read to, after another conversation was on screen', () => {
    const { rerender } = render(<ThreadView place="p-a" thread={{ items: [text('a long answer')], running: false }} />)
    const thread = screen.getByTestId('thread')
    Object.defineProperty(thread, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(thread, 'clientHeight', { configurable: true, value: 200 })
    thread.scrollTop = 800
    fireEvent.scroll(thread)
    thread.scrollTop = 300
    fireEvent.scroll(thread)

    rerender(<ThreadView place="p-b" thread={{ items: [text('another')], running: false }} />)
    thread.scrollTop = 0
    rerender(<ThreadView place="p-a" thread={{ items: [text('a long answer')], running: false }} />)

    expect(thread.scrollTop).toBe(300)
    expect(screen.getByTestId('to-latest')).toBeTruthy()
  })

  it('opens whole, and lets what comes after rise into place, never while it is read', async () => {
    const thread = { items: [text('an answer')], running: false }
    const settled = () => screen.getByTestId('thread').hasAttribute('data-settled')
    const { rerender } = render(<ThreadView place="p-a" thread={thread} />)
    // Drawn without the movement first: what it opened with is there, not rising.
    expect(settled()).toBe(false)
    await waitFor(() => {
      expect(settled()).toBe(true)
    })
    expect(screen.getByTestId('part').classList.contains('enter')).toBe(true)

    rerender(<ThreadView place="p-b" thread={thread} />)
    expect(settled()).toBe(false)
    await waitFor(() => {
      expect(settled()).toBe(true)
    })

    rerender(<ThreadView loading place="p-c" thread={{ items: [], running: false }} />)
    await new Promise(resolve => setTimeout(resolve, 100))
    expect(settled()).toBe(false)
  })
})

describe('a turn that failed (the UI review, 25/10/2026)', () => {
  const user: Item = { attachments: [], key: 'u', kind: 'user', steered: false, text: 'Plan my week' }
  const failed = (error: null | string): Item => ({
    error,
    key: 'e',
    kind: 'end',
    status: 'failed',
    text: 'The account cannot pay.'
  })

  it('is an error, and as the last word offers the ways out that exist', async () => {
    const { $drafts } = await import('@/store/drafts')
    const { $settings, $settingsSection } = await import('@/store/settings')
    render(<ThreadView place="c-failed" thread={{ items: [user, failed('ModelOutOfCreditError')], running: false }} />)

    expect(screen.getByTestId('turn-failed').getAttribute('role')).toBe('alert')
    fireEvent.click(screen.getByTestId('turn-put-back'))
    expect($drafts.get()['c-failed']).toBe('Plan my week')
    fireEvent.click(screen.getByTestId('turn-connect-key'))
    expect($settings.get()).toBe(true)
    expect($settingsSection.get()).toBe('model')
    fireEvent.click(screen.getByTestId('turn-change-model'))
    expect($modelMenu.get()).toBe(true)
    $modelMenu.set(false)
    $settings.set(false)
  })

  it('offers a key only where a key is what failed, and nothing once it is not the last word', () => {
    const { rerender } = render(
      <ThreadView place="c-failed-2" thread={{ items: [user, failed('ModelRateLimitError')], running: false }} />
    )
    expect(screen.queryByTestId('turn-connect-key')).toBeNull()
    expect(screen.getByTestId('turn-put-back')).toBeTruthy()

    rerender(
      <ThreadView
        place="c-failed-2"
        thread={{ items: [user, failed('ModelKeyError'), { ...user, key: 'u2', text: 'Again' }], running: false }}
      />
    )
    expect(screen.queryByTestId('turn-actions')).toBeNull()
    expect(screen.getByTestId('turn-failed').getAttribute('role')).toBeNull()
  })
})
