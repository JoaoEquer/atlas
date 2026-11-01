import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { Streamdown } from 'streamdown'

import { t } from '@/i18n'
import type { AttachmentRow } from '@/protocol/protocol.generated'
import { $remote, currentOwner, type Sending } from '@/store/app'
import { draftOf, setDraft } from '@/store/drafts'
import { openPreview, targetOf } from '@/store/previews'
import { openSettings } from '@/store/settings'
import { isPart, type Item, type Part, reasoningAbout, type Thread } from '@/store/thread'
import type { Waiting } from '@/store/waiting'

import { ARTIFACT_LANGUAGES, ArtifactBlock } from './artifact-block'
import { AttachmentChip, useThumbnail } from './attachment-chip'
import { ReviewRow } from './review-row'
import { AnswerStreaming, SHELL_RENDERERS } from './shell-block'
import { ToolRow } from './tool-row'
import { ArrowDown } from './ui/icons'
import { InlineError } from './ui/inline-error'
import { SkeletonThread } from './ui/skeleton'
import { elapsed, useNow } from './use-now'
import { Wordmark } from './wordmark'

// A code block in a reply only copies: saving a command as `file.ps1` is no use.
const CONTROLS = { code: { copy: true, download: false } } as const
// A shell's block also runs, in the conversation's terminal (step 16f); a block
// of HTML, mermaid or Markdown opens beside, in a preview.
const ANSWER_PLUGINS = {
  renderers: [...SHELL_RENDERERS, { component: ArtifactBlock, language: ARTIFACT_LANGUAGES }]
}

// How close to the end, in pixels, still counts as being at it.
const NEAR_END = 48

const NONE_WAITING: ReadonlyMap<string, Waiting> = new Map()
const NONE_SENDING: readonly Sending[] = []

/** Where the user was in each thread they left, by its `place`, found again when it comes back:
 * read up a conversation, look at another and return, and the reading goes on there. */
const PLACES = new Map<string, { following: boolean; top: number }>()

/**
 * A conversation's thread. `empty` is what an empty thread shows in place of the invitation, Atlas's
 * name set in characters as on the website (a bot's face and name); `loading`, that it is being read, which shows a skeleton and never the
 * invitation; `place`, whose thread it is, for where the user was in it; `sending`, the user's
 * messages on their way; `waiting`, the calls the open cards hold, by call id (store/waiting.ts).
 */
export function ThreadView({
  empty,
  loading = false,
  place,
  sending = NONE_SENDING,
  thread,
  waiting = NONE_WAITING
}: {
  empty?: ReactNode
  loading?: boolean
  place?: string
  sending?: readonly Sending[]
  thread: Thread
  waiting?: ReadonlyMap<string, Waiting>
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const end = useRef<HTMLDivElement>(null)
  // The thread follows the reply only while the user is at its end: scrolled up to read what came
  // first, it stays still, and a button brings them back down (asked for by the user on 22/10/2026).
  const following = useRef(true)
  const lastTop = useRef(0)
  const [away, setAway] = useState(false)
  // Which thread has settled on screen: its parts rise into place only after that, so a
  // conversation that opens is there at once instead of rising as a whole (styles.css, `.enter`).
  const [settled, setSettled] = useState<null | string>(null)
  const last = thread.items.at(-1)
  const size = last ? growth(last) : 0
  const sent = last?.kind === 'user' || sending.length > 0

  // Another thread on screen: the user's place in it, or its end the first time.
  useLayoutEffect(() => {
    const kept = place === undefined ? undefined : PLACES.get(place)
    following.current = kept?.following ?? true
    lastTop.current = kept?.top ?? 0
    setAway(kept ? !kept.following : false)

    if (kept && !kept.following && scroller.current) {
      scroller.current.scrollTop = kept.top
    } else {
      end.current?.scrollIntoView({ block: 'end' })
    }
  }, [place])

  useEffect(() => {
    if (loading) {
      return
    }
    // Two frames: the first draws the thread, the second lets what comes after it rise.
    let frame = requestAnimationFrame(() => {
      frame = requestAnimationFrame(() => {
        setSettled(place ?? '')
      })
    })

    return () => {
      cancelAnimationFrame(frame)
    }
  }, [loading, place])

  useEffect(() => {
    // What the user sends, they want to see go out; at the end, the thread follows again.
    if (sent || following.current) {
      end.current?.scrollIntoView({ block: 'end' })
    }
  }, [thread.items.length, size, sent, sending.length])

  function onScroll() {
    const element = scroller.current
    if (!element) {
      return
    }
    const near = element.scrollHeight - element.scrollTop - element.clientHeight <= NEAR_END
    // Only the user scrolls up: the thread growing, or following it, only moves it down.
    if (near) {
      following.current = true
    } else if (element.scrollTop < lastTop.current) {
      following.current = false
    }
    lastTop.current = element.scrollTop
    setAway(!near)

    if (place !== undefined) {
      PLACES.set(place, { following: following.current, top: element.scrollTop })
    }
  }

  function toLatest() {
    following.current = true
    end.current?.scrollIntoView({ behavior: still() ? 'auto' : 'smooth', block: 'end' })
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        className="flex-1 overflow-y-auto"
        data-settled={settled === (place ?? '') ? '' : undefined}
        data-testid="thread"
        data-thread
        onScroll={onScroll}
        ref={scroller}
      >
        <AnswerStreaming.Provider value={thread.running}>
          <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
            {thread.items.length === 0 &&
              sending.length === 0 &&
              (loading ? <SkeletonThread label={t.thread.reading} /> : (empty ?? <Invitation />))}
            {segments(thread.items).map(segment =>
              Array.isArray(segment) ? (
                <Trail key={segment[0]?.key} parts={segment} waiting={waiting} />
              ) : (
                // The user's own message does not rise: it takes the place of the one on its way,
                // which rose already.
                <div className={segment.kind === 'user' ? undefined : 'enter'} key={segment.key}>
                  <ItemView
                    item={segment}
                    latest={segment === last}
                    place={place}
                    said={segment === last ? lastSaid(thread.items) : null}
                  />
                </div>
              )
            )}
            {sending.map(row => (
              <SendingView key={row.key} row={row} />
            ))}
            {/* Only where nothing on the trail shows the turn at work: between a call's end and
                the model's next words. */}
            {thread.running && !atWork(thread.items) && <Working />}
            <div ref={end} />
          </div>
        </AnswerStreaming.Provider>
      </div>
      {away && (
        <button
          aria-label={t.thread.toLatest}
          className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full border border-border bg-raised p-2 text-soft shadow-md hover:text-foreground"
          data-testid="to-latest"
          onClick={toLatest}
          title={t.thread.toLatest}
          type="button"
        >
          <ArrowDown aria-hidden size={16} stroke={2} />
        </button>
      )}
    </div>
  )
}

/** The items with each run of consecutive parts gathered: one trail each. */
function segments(items: Item[]): (Exclude<Item, Part> | Part[])[] {
  const out: (Exclude<Item, Part> | Part[])[] = []

  for (const item of items) {
    const previous = out.at(-1)

    if (!isPart(item)) {
      out.push(item)
    } else if (Array.isArray(previous)) {
      previous.push(item)
    } else {
      out.push([item])
    }
  }

  return out
}

function growth(item: Item): number {
  switch (item.kind) {
    case 'reasoning':
    case 'text':
      return item.text.length
    case 'tool':
      return item.progress.length + item.result.length
    default:
      return 0
  }
}

/** Something on the trail shows the turn at work already: a part that streams, or a call that runs
 * or waits for the user. */
function atWork(items: Item[]): boolean {
  return items.some(item =>
    item.kind === 'tool'
      ? item.status === 'running'
      : (item.kind === 'text' || item.kind === 'reasoning') && item.streaming
  )
}

/**
 * A turn's parts as a trail: a vertical line from the first part to
 * the last, one dot per part, its color the part's state.
 */
function Trail({ parts, waiting }: { parts: Part[]; waiting: ReadonlyMap<string, Waiting> }) {
  return (
    <div className="space-y-3" data-testid="trail">
      {parts.map((part, index) => (
        <div
          className="enter relative min-w-0 pl-6"
          data-dot={dot(part, waiting)}
          data-kind={part.kind}
          data-testid="part"
          key={part.key}
        >
          {parts.length > 1 && (
            <span
              aria-hidden
              className={`absolute left-[6px] w-px bg-border-strong ${index === 0 ? 'top-[0.75em]' : '-top-3'} ${index === parts.length - 1 ? 'h-[calc(0.75em+0.75rem)]' : 'bottom-0'}`}
            />
          )}
          <span
            aria-hidden
            className={`absolute top-[calc(0.75em-4.5px)] left-[2px] size-[9px] rounded-full ${DOT[dot(part, waiting)]}`}
          />
          <PartView part={part} waiting={waiting} />
        </div>
      ))}
    </div>
  )
}

type Dot = 'denied' | 'failed' | 'final' | 'interim' | 'muted' | 'ok' | 'reasoning' | 'running' | 'waiting'

const DOT: Record<Dot, string> = {
  denied: 'bg-faint',
  failed: 'bg-failed',
  final: 'bg-primary',
  interim: 'bg-faint',
  muted: 'bg-faint',
  ok: 'bg-done',
  reasoning: 'border-[1.5px] border-faint bg-background',
  running: 'bg-working motion-safe:animate-pulse',
  // Still, as the sidebar's own waiting dot: the call waits on the user, nothing runs.
  waiting: 'bg-waiting'
}

/** The dot's color is the part's state; a call a card holds waits, it does not run. */
function dot(part: Part, waiting: ReadonlyMap<string, Waiting>): Dot {
  switch (part.kind) {
    case 'reasoning':
      return 'reasoning'
    case 'text':
      return part.role === 'interim' ? 'interim' : 'final'
    case 'other':
      return 'muted'
    case 'tool':
      switch (part.status) {
        case 'completed':
          return 'ok'
        case 'failed':
          return 'failed'
        case 'running':
          return waiting.has(part.toolId) ? 'waiting' : 'running'
        case 'denied':
        case 'cancelled':
        case 'unanswered':
          return 'denied'
      }
  }
}

function PartView({ part, waiting }: { part: Part; waiting: ReadonlyMap<string, Waiting> }) {
  switch (part.kind) {
    case 'reasoning':
      return <ReasoningPart part={part} />

    case 'text':
      // The interim text is lighter; the answer has the full weight. Nothing moves or goes away.
      return (
        <div
          className={part.role === 'interim' ? 'text-sm text-muted-foreground' : ''}
          data-role={part.role}
          data-testid="assistant-message"
        >
          <Streamdown
            controls={CONTROLS}
            isAnimating={part.streaming}
            mode={part.streaming ? 'streaming' : 'static'}
            plugins={ANSWER_PLUGINS}
          >
            {part.text}
          </Streamdown>
        </div>
      )

    case 'tool':
      return <ToolRow tool={part} waiting={waiting.get(part.toolId)} />

    case 'other':
      // ACP v2: what the app does not know is kept raw and shown generically.
      return (
        <details className="text-sm text-muted-foreground" data-testid="other-part">
          <summary className="cursor-pointer list-none select-none [&::-webkit-details-marker]:hidden">
            <span className="font-mono">{part.type}</span> · {t.thread.other}
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto font-mono text-xs whitespace-pre-wrap">
            {JSON.stringify(part.payload, null, 2)}
          </pre>
        </details>
      )
  }
}

/** Reasoning in one closed line: "Thinking…" and its timer while it comes,
 * "Thought for 4 s" after, "Thought" reopened, and what it is about. Open, it is Markdown. */
function ReasoningPart({ part }: { part: Extract<Part, { kind: 'reasoning' }> }) {
  const now = useNow(part.streaming)
  const running = part.streaming ? elapsed(part.startedAt, now) : null
  // Under a second it thought briefly, and the time is whole seconds.
  const label = part.streaming
    ? [t.thread.thinking, running === null ? '' : t.elapsed(Math.floor(running))].filter(Boolean).join(' ')
    : part.seconds === null
      ? t.thread.thought
      : part.seconds < 1
        ? t.thread.thoughtBriefly
        : t.thread.thoughtFor(t.elapsed(Math.round(part.seconds)))
  const about = reasoningAbout(part.text)

  return (
    <details className="text-sm text-muted-foreground" data-testid="reasoning">
      <summary className="cursor-pointer list-none truncate select-none [&::-webkit-details-marker]:hidden">
        {label}
        {about && <span className="text-muted-foreground"> · {about}</span>}
      </summary>
      <div className="mt-1">
        {/* Streaming, as the answer streams: in static mode Streamdown sets its blocks from an effect
            on every delta, and a fast stream of them passed React's limit of nested updates. */}
        <Streamdown controls={CONTROLS} mode={part.streaming ? 'streaming' : 'static'}>
          {part.text}
        </Streamdown>
      </div>
    </details>
  )
}

/** `latest`, it is the thread's last item; `place`, whose box the thread's is (its draft's key);
 * `said`, the user's last words in the thread. */
function ItemView({
  item,
  latest,
  place,
  said
}: {
  item: Exclude<Item, Part>
  latest: boolean
  place?: string
  said: null | string
}) {
  switch (item.kind) {
    case 'user':
      return (
        <div className="flex flex-col items-end" data-testid="user-message">
          {item.attachments.length > 0 && (
            <div className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-2" data-testid="message-attachments">
              {item.attachments.map(attachment => (
                <SentAttachment attachment={attachment} key={attachment.id} />
              ))}
            </div>
          )}
          {item.text && (
            <div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2 whitespace-pre-wrap">{item.text}</div>
          )}
          {item.skill && (
            <span className="mt-1 text-xs text-muted-foreground" data-skill={item.skill} data-testid="skill-invoked">
              {t.thread.skill}
            </span>
          )}
          {item.steered && <span className="mt-1 text-xs text-muted-foreground">{t.thread.steered}</span>}
        </div>
      )

    case 'summary':
      return (
        <details className="rounded-md border border-dashed border-border-strong px-3 py-2 text-sm">
          <summary className="cursor-pointer text-muted-foreground">{t.thread.summary}</summary>
          <p className="mt-2 whitespace-pre-wrap">{item.text}</p>
        </details>
      )

    case 'status':
      return <p className="text-center text-xs text-muted-foreground">{item.text}</p>

    case 'file':
      return (
        <div className="flex flex-col items-start gap-1" data-testid="file-sent">
          <span className="text-xs text-muted-foreground">{t.thread.fileSent}</span>
          <SentAttachment attachment={item.attachment} />
        </div>
      )

    case 'notice':
      return (
        <details
          className="rounded-md border border-dashed border-border-strong px-3 py-2 text-sm"
          data-testid="notice"
        >
          <summary className="cursor-pointer text-muted-foreground">{t.thread.notice}</summary>
          <p className="mt-2 whitespace-pre-wrap">{item.text}</p>
        </details>
      )

    case 'review':
      return <ReviewRow review={item} />

    case 'terminal':
      return <TerminalRowView item={item} />

    case 'end':
      return item.status === 'failed' ? (
        <TurnFailed item={item} latest={latest} place={place} said={said} />
      ) : (
        <p className="text-sm text-muted-foreground" data-testid="turn-end">
          {endText(item)}
        </p>
      )
  }
}

/** The user's last words in a thread: what a failed turn was answering. */
function lastSaid(items: Item[]): null | string {
  const found = items.findLast(item => item.kind === 'user')

  return found?.kind === 'user' && found.text ? found.text : null
}

/** Errors a key fixes: none stored, refused, or its account out of credit. */
const KEYED = new Set(['ModelAuthError', 'ModelKeyError', 'ModelOutOfCreditError'])

/**
 * A turn that failed, as an error in the server's words, and, as the conversation's last word, the
 * ways out that exist: its message back in the box to send again, the model to change, a key to
 * connect when one is what failed (the UI review, 25/10/2026: a failure read like "Stopped.", grey
 * and with no way out). Nothing is sent again by itself: that would store the message twice.
 */
function TurnFailed({
  item,
  latest,
  place,
  said
}: {
  item: Extract<Item, { kind: 'end' }>
  latest: boolean
  place?: string
  said: null | string
}) {
  return (
    <InlineError live={latest} testId="turn-failed">
      <p data-testid="turn-end">{endText(item)}</p>
      {latest && (
        <div className="mt-2 flex flex-wrap gap-2" data-testid="turn-actions">
          {said !== null && place !== undefined && (
            <TurnAction
              label={t.thread.putBack}
              onClick={() => {
                const kept = draftOf(place)
                setDraft(place, kept ? `${kept}\n\n${said}` : said)
                document.querySelector<HTMLTextAreaElement>('[data-testid="composer"] textarea')?.focus()
              }}
              testId="turn-put-back"
            />
          )}
          <TurnAction
            label={t.thread.changeModel}
            onClick={() => {
              const select = document.querySelector<HTMLSelectElement>('[data-testid="model-select"]')
              select?.focus()

              try {
                select?.showPicker()
              } catch {
                // A list that does not open on demand stays focused: the arrows choose.
              }
            }}
            testId="turn-change-model"
          />
          {item.error !== null && KEYED.has(item.error) && (
            <TurnAction
              label={t.thread.connectKey}
              onClick={() => {
                openSettings('model')
              }}
              testId="turn-connect-key"
            />
          )}
        </div>
      )}
    </InlineError>
  )
}

function TurnAction({ label, onClick, testId }: { label: string; onClick: () => void; testId: string }) {
  return (
    <button
      className="rounded-md border border-failed/40 bg-background/60 px-2.5 py-1 text-xs font-medium text-failed-text hover:bg-failed/10"
      data-testid={testId}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  )
}

/** A message of the user on its way: drawn as theirs at once, lighter, saying where it is, until
 * the conversation's own takes its place. */
function SendingView({ row }: { row: Sending }) {
  return (
    <div className="enter flex flex-col items-end" data-state={row.state} data-testid="sending-message">
      {row.attachments.length > 0 && (
        <div className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-2 opacity-60">
          {row.attachments.map(attachment => (
            <SentAttachment attachment={attachment} key={attachment.id} />
          ))}
        </div>
      )}
      {row.text && (
        <div className="max-w-[85%] rounded-2xl bg-muted px-4 py-2 whitespace-pre-wrap opacity-60">{row.text}</div>
      )}
      <span className="mt-1 text-xs text-muted-foreground" role="status">
        {row.state === 'queued' ? t.thread.queued : t.thread.sending}
      </span>
    </div>
  )
}

/** A file the user sent (step 16g), as its chip was in the box; a click opens it beside, when the
 * server is on this machine. */
function SentAttachment({ attachment }: { attachment: AttachmentRow }) {
  const thumbnail = useThumbnail(attachment.path, attachment.kind)
  const local = !useStore($remote) && Boolean(attachment.path)

  return (
    <AttachmentChip
      detail={t.attachments.size(attachment.size)}
      kind={attachment.kind}
      name={attachment.name}
      onOpen={
        local
          ? () => {
              openPreview(currentOwner(), targetOf(attachment.path, attachment.name), true)
            }
          : undefined
      }
      thumbnail={thumbnail}
    />
  )
}

/** A command the user ran and what came of it (step 16f), drawn as a terminal's lines. */
function TerminalRowView({ item }: { item: Extract<Item, { kind: 'terminal' }> }) {
  const failed = item.exitCode !== null && item.exitCode !== 0

  return (
    <div className="flex flex-col items-end" data-testid="terminal-row">
      <div className="dark w-full max-w-[85%] overflow-hidden rounded-xl bg-background font-mono text-xs text-foreground">
        <div className="border-b border-border-strong px-3 py-2 whitespace-pre-wrap">
          <span className="text-muted-foreground">$</span> {item.command}
        </div>
        {item.output && (
          <pre
            className="max-h-64 overflow-auto px-3 py-2 break-words whitespace-pre-wrap"
            data-testid="terminal-output"
          >
            {item.output}
          </pre>
        )}
        {(item.exitCode !== null || item.state) && (
          <div className="flex gap-3 border-t border-border-strong px-3 py-1.5 text-2xs text-muted-foreground">
            {item.exitCode !== null && (
              <span className={failed ? 'text-failed-text' : ''} data-testid="terminal-exit">
                {t.terminal.exit(item.exitCode)}
              </span>
            )}
            {item.state && <span data-testid="terminal-state">{item.state}</span>}
          </div>
        )}
      </div>
      {item.steered && <span className="mt-1 text-xs text-muted-foreground">{t.thread.steered}</span>}
    </div>
  )
}

function endText(item: Extract<Item, { kind: 'end' }>): string {
  switch (item.status) {
    case 'interrupted':
      return t.thread.interrupted
    case 'halted':
      return t.thread.halted
    case 'limited':
      return t.thread.limited
    case 'failed':
      // The server's words say why, and often what to do ("store it with atlas secrets set");
      // the kind of error alone does not (found in use, 15/10).
      return t.thread.failed(item.text || (item.error ?? ''))
  }
}

/** The turn at work where nothing else shows it: blue and pulsing, as a call that runs. */
function Working() {
  return (
    <div aria-label="Working" className="enter flex gap-1 py-2" role="status">
      <span className="h-2 w-2 rounded-full bg-working/70 motion-safe:animate-pulse" />
      <span className="h-2 w-2 rounded-full bg-working/70 motion-safe:animate-pulse motion-safe:[animation-delay:150ms]" />
      <span className="h-2 w-2 rounded-full bg-working/70 motion-safe:animate-pulse motion-safe:[animation-delay:300ms]" />
    </div>
  )
}

/** What a conversation with Atlas opens on: its name set in characters, as the website's, and the
 * invitation in words (asked for by the user on 01/11/2026). */
function Invitation() {
  return (
    <div className="pt-16 text-center" data-testid="invitation">
      <Wordmark className="mx-auto max-w-xl" ratio={0.3} word="Atlas" />
      <p className="mt-4 text-muted-foreground">{t.thread.empty}</p>
    </div>
  )
}

/** The system asks for less motion: a scroll jumps instead of gliding. */
function still(): boolean {
  // The tests' window has no `matchMedia`.
  const query = (globalThis as { matchMedia?: typeof matchMedia }).matchMedia

  return query?.('(prefers-reduced-motion: reduce)').matches ?? false
}
