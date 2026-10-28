import { useStore } from '@nanostores/react'
import {
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
  type SetStateAction,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState
} from 'react'

import { t } from '@/i18n'
import { $boxInsert } from '@/plugins/registry'
import type { AttachmentRow, SlashCommandRow } from '@/protocol/protocol.generated'
import { $pending, attach, detach, type Pending, putBack, takeReady } from '@/store/attachments'
import { $drafts, draftOf, setDraft } from '@/store/drafts'
import { offered, slashQuery } from '@/store/slash'

import { AttachmentChip } from './attachment-chip'
import { ArrowUp, Plus, Stop } from './ui/icons'
import { Tip } from './ui/tip'

/** "Auto" beside send: a conversation's auto mode, or an executor's own automatic mode. */
export interface AutoToggle {
  label: string
  on: boolean
  set: (on: boolean) => void
  /** What it does, on hover. */
  title: string
}

export interface MessageBoxProps {
  /** The box's name for screen readers, and what it says when empty. */
  label: string
  placeholder: string
  connected: boolean
  /** It takes nothing now (an executor busy and not taking corrections). */
  blocked?: boolean
  /** Send the words and the files that arrived; `false` when they did not reach the server, and
   * both come back to the box. */
  onSend: (text: string, attachments: AttachmentRow[]) => Promise<boolean>
  /** A message starting with `!`: a command for the terminal (step 16f). */
  onCommand?: (command: string) => void
  /** Whose chips the box holds; unset, the box takes no files. */
  owner?: string
  /** Whose words the box holds: kept per conversation while unsent, so switching to another
   * conversation leaves them in theirs (store/drafts.ts); unset, the box keeps its own. */
  draftKey?: string
  /** What sits below the box: the model, its effort, the terminal, what is particular to the screen. */
  toolbar: ReactNode
  /** A line above the box, such as a note on what the next prompt starts. */
  note?: ReactNode
  auto?: AutoToggle
  /** A turn runs: the button that stops it. */
  onStop?: () => void
  /** The commands `/` offers as the user types: an executor's as its harness describes them,
   * Atlas's own and its skills elsewhere. */
  commands?: SlashCommandRow[]
  /** A `/name` message the screen runs itself (Atlas's `/new`, `/stop`): `true` when it did, and
   * the message is not sent. */
  onSlash?: (name: string, rest: string) => boolean
  /** The `/` list opened: the screen may read its commands again (a skill written meanwhile). */
  onSlashOpen?: () => void
  /** Text to put back in the box (a stopped turn's unsent message), taken once. */
  restore?: () => string
  /** The textarea's test id, for the end-to-end tests. */
  testId?: string
  /** Buttons beside the words: what the plugins' screens add to the conversation's box (step 17a);
   *  a box with them also takes the words a screen puts into it (a dictation). */
  actions?: ReactNode
}

/**
 * The one box of every conversation: Atlas's, a bot's and an executor's session (asked for by the
 * user on 16/10/2026). What it holds is the same everywhere: the chips of the files attached, "+" to
 * attach (where a clip was), the words, "Auto" (where a ⚡ was, asked for by the user on
 * 18/10/2026), send, which is stop while a turn runs and the box is empty, and the screen's
 * controls below it (asked for by the user on 22/10/2026, where they sat above). Each screen
 * gives only what is its own (`toolbar`, `auto`, `commands`), and whether it takes files.
 *
 * Files dropped on it, pasted into it or picked with "+" become chips above it (step 16g): each
 * starts sending at once, and the message goes with those that arrived.
 */
export function MessageBox(props: MessageBoxProps) {
  const {
    auto,
    blocked = false,
    commands = [],
    connected,
    draftKey,
    label,
    note,
    onSlashOpen,
    onStop,
    owner,
    placeholder
  } = props
  const pending = useStore($pending)
  const [text, setText] = useWords(draftKey)
  const [dragging, setDragging] = useState(false)
  const picker = useRef<HTMLInputElement>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  const chips = owner === undefined ? [] : (pending[owner] ?? [])
  const sending = chips.some(chip => chip.status === 'sending')
  const ready = chips.some(chip => chip.status === 'ready')
  // `/` and a name: the commands that match, in their groups; Escape hides them until the text
  // changes.
  const [hidden, setHidden] = useState<null | string>(null)
  const [active, setActive] = useState(0)
  const query = slashQuery(text)
  const groups = query === null || hidden === text ? [] : offered(commands, query)
  const rows = groups.flatMap(group => group.rows)
  const chosen = rows[Math.min(active, rows.length - 1)]
  const restore = props.restore
  const actions = props.actions
  // While a turn runs, the send button stops it, unless there are words or files to steer it with.
  const stopping = onStop !== undefined && !text.trim() && !ready

  const listing = query !== null

  useEffect(() => {
    if (listing) {
      onSlashOpen?.()
    }
  }, [listing, onSlashOpen])

  // Another conversation's box: its own words, and the `/` list as it was never opened.
  useEffect(() => {
    setHidden(null)
    setActive(0)
  }, [draftKey])

  // The box grows with what is typed, up to its most, then scrolls (asked for by the user on
  // 22/10/2026, where a long prompt read through one line).
  useLayoutEffect(() => {
    const element = box.current

    if (!element) {
      return undefined
    }

    const fit = () => {
      element.style.height = 'auto'
      element.style.height = `${String(element.scrollHeight + element.offsetHeight - element.clientHeight)}px`
    }

    fit()

    // Measured while the box was still narrow (or hidden), the placeholder wraps letter by letter and
    // the height sticks huge: measure again when the width settles or changes.
    if (typeof ResizeObserver === 'undefined') {
      return undefined
    }

    let width = element.clientWidth
    const watcher = new ResizeObserver(() => {
      if (element.clientWidth !== width) {
        width = element.clientWidth
        fit()
      }
    })

    watcher.observe(element)

    return () => {
      watcher.disconnect()
    }
  }, [text])

  useEffect(() => {
    const back = restore?.()

    if (back) {
      setText(current => (current ? `${back}\n\n${current}` : back))
    }
  }, [restore, setText])

  // Words a plugin's screen puts in from now on (a dictation): after what the box holds.
  const takesWords = actions !== undefined

  useEffect(() => {
    if (!takesWords) {
      return undefined
    }

    return $boxInsert.listen(inserted => {
      if (inserted) {
        setText(current => (current.trim() ? `${current.trimEnd()} ${inserted.text}` : inserted.text))
        box.current?.focus()
      }
    })
  }, [takesWords, setText])

  const submit = () => {
    const message = text.trim()

    if ((!message && !ready) || !connected || sending || blocked) {
      return
    }

    const slash = /^\/(\S+)\s*([\s\S]*)$/.exec(message)

    if (slash && props.onSlash?.(slash[1] ?? '', slash[2] ?? '') && !ready) {
      setText('')

      return
    }

    if (message.startsWith('!') && message.length > 1 && props.onCommand && !ready) {
      setText('')
      props.onCommand(message.slice(1))

      return
    }

    setText('')
    const attachments = owner === undefined ? [] : takeReady(owner)
    const sentFrom = draftKey

    // A message that did not reach the server comes back to the box it left, its files too, also
    // when another conversation is on screen by then.
    void props.onSend(message, attachments).then(sent => {
      if (!sent) {
        setText(current => current || message, sentFrom)

        if (owner !== undefined) {
          putBack(owner, attachments)
        }
      }
    })
  }

  const takesFiles = owner !== undefined

  const take = (row: SlashCommandRow) => {
    setText(`/${row.name} `)
    setActive(0)
  }

  /** The list's keys while it shows: arrows move, Enter or Tab take the row, Escape hides it. */
  const onListKey = (event: KeyboardEvent): boolean => {
    if (!rows.length || event.nativeEvent.isComposing) {
      return false
    }

    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      const step = event.key === 'ArrowDown' ? 1 : rows.length - 1
      setActive(current => (Math.min(current, rows.length - 1) + step) % rows.length)
    } else if ((event.key === 'Enter' && !event.shiftKey) || event.key === 'Tab') {
      if (!chosen) {
        return false
      }

      take(chosen)
    } else if (event.key === 'Escape') {
      setHidden(text)
    } else {
      return false
    }

    event.preventDefault()

    return true
  }

  return (
    <div
      className={`relative border-t border-border p-4 ${dragging ? 'bg-muted' : ''}`}
      data-testid="composer"
      onDragLeave={event => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
          setDragging(false)
        }
      }}
      onDragOver={event => {
        if (takesFiles && event.dataTransfer.types.includes('Files')) {
          event.preventDefault()
          event.dataTransfer.dropEffect = 'copy'
          setDragging(true)
        }
      }}
      onDrop={(event: DragEvent) => {
        if (!takesFiles || !event.dataTransfer.types.includes('Files')) {
          return
        }

        event.preventDefault()
        setDragging(false)
        attach([...event.dataTransfer.files])
      }}
    >
      {dragging && (
        <div
          className="pointer-events-none absolute inset-2 z-10 grid place-items-center rounded-xl border-2 border-dashed border-faint text-sm font-medium text-soft"
          data-testid="drop-zone"
        >
          {t.attachments.drop}
        </div>
      )}
      {note && <div className="mx-auto mb-2 max-w-3xl">{note}</div>}
      {groups.length > 0 && (
        <div
          aria-label={t.composer.slashCommands}
          className="mx-auto mb-2 max-h-72 max-w-3xl overflow-y-auto rounded-lg border border-border p-1 text-sm"
          data-testid="slash-commands"
          role="listbox"
        >
          {groups.map(group => (
            <div data-group={group.group} key={group.group} role="group">
              <p className="px-2 pt-1 pb-0.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {t.composer.slashGroups[group.group]}
              </p>
              {group.rows.map(row => (
                <button
                  aria-selected={row === chosen}
                  className={`flex w-full items-baseline gap-2 rounded px-2 py-1 text-left ${
                    row === chosen ? 'bg-muted' : 'hover:bg-muted'
                  }`}
                  data-command={row.name}
                  data-testid="slash-command"
                  key={`${group.group}:${row.name}`}
                  onClick={() => {
                    take(row)
                  }}
                  onMouseEnter={() => {
                    setActive(rows.indexOf(row))
                  }}
                  role="option"
                  title={row.description || undefined}
                  type="button"
                >
                  <span className="shrink-0 font-mono font-medium">/{row.name}</span>
                  {row.hint && <span className="shrink-0 font-mono text-xs text-muted-foreground">{row.hint}</span>}
                  {row.description && (
                    <span className="min-w-0 truncate text-xs text-muted-foreground">{row.description}</span>
                  )}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
      {chips.length > 0 && owner !== undefined && (
        <div
          className="mx-auto mb-2 flex max-h-36 max-w-3xl flex-wrap gap-2 overflow-y-auto pt-1.5 pr-1.5"
          data-testid="composer-attachments"
        >
          {chips.map(chip => (
            <AttachmentChip
              detail={detail(chip)}
              failed={chip.status === 'error'}
              key={chip.key}
              kind={chip.kind}
              name={chip.name}
              onRemove={() => {
                detach(owner, chip.key)
              }}
              progress={chip.status === 'sending' ? chip.progress : null}
              thumbnail={chip.thumbnail}
            />
          ))}
        </div>
      )}
      <div className="mx-auto flex max-w-3xl items-end gap-2">
        {takesFiles && (
          <>
            <input
              className="hidden"
              data-testid="attachment-picker"
              multiple
              onChange={event => {
                attach([...(event.target.files ?? [])])
                event.target.value = ''
              }}
              ref={picker}
              type="file"
            />
            <Tip label={t.attachments.add}>
              <button
                aria-label={t.attachments.add}
                className="grid h-11 w-11 shrink-0 place-items-center rounded-lg border border-border-strong text-muted-foreground hover:bg-muted"
                onClick={() => picker.current?.click()}
                type="button"
              >
                <Plus aria-hidden size={18} stroke={1.75} />
              </button>
            </Tip>
          </>
        )}
        {actions}
        <textarea
          aria-label={label}
          className="max-h-[40vh] min-h-11 flex-1 resize-none overflow-y-auto rounded-lg border border-border-strong bg-raised px-3 py-2.5 placeholder:truncate"
          data-testid={props.testId}
          disabled={blocked}
          onChange={event => {
            setText(event.target.value)
            setActive(0)
          }}
          onKeyDown={event => {
            if (onListKey(event)) {
              return
            }

            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              submit()
            }
          }}
          onPaste={event => {
            // A copied file or a screenshot attaches; text pastes as text.
            const files = [...event.clipboardData.files]

            if (takesFiles && files.length) {
              event.preventDefault()
              attach(files)
            }
          }}
          placeholder={placeholder}
          ref={box}
          rows={1}
          value={text}
        />
        {auto && (
          <Tip label={auto.title}>
            <button
              aria-label={auto.label}
              aria-pressed={auto.on}
              className={`flex h-11 shrink-0 items-center gap-1.5 rounded-lg border border-border-strong px-3 text-sm font-medium ${
                auto.on ? 'bg-selected text-foreground' : 'text-muted-foreground hover:bg-muted'
              }`}
              onClick={() => {
                auto.set(!auto.on)
              }}
              type="button"
            >
              {/* On, a full dot; off, an empty one: amber is only what waits for the user (decided
                  with the user on 25/10/2026). */}
              <span
                aria-hidden
                className={`size-1.5 rounded-full border ${auto.on ? 'border-foreground bg-foreground' : 'border-faint'}`}
              />
              {t.composer.auto}
            </button>
          </Tip>
        )}
        {stopping ? (
          <Tip label={t.composer.stop}>
            <button
              aria-label={t.composer.stop}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground"
              data-testid="stop"
              onClick={onStop}
              type="button"
            >
              <Stop aria-hidden size={16} />
            </button>
          </Tip>
        ) : (
          <Tip label={sending ? t.attachments.waiting : t.composer.send}>
            <button
              aria-label={t.composer.send}
              className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground disabled:opacity-40"
              disabled={!connected || sending || blocked || (!text.trim() && !ready)}
              onClick={submit}
              type="button"
            >
              <ArrowUp aria-hidden size={18} stroke={2} />
            </button>
          </Tip>
        )}
      </div>
      <div className="mx-auto mt-2 flex max-w-3xl flex-wrap items-center gap-2 text-sm" data-testid="composer-toolbar">
        {props.toolbar}
      </div>
    </div>
  )
}

function detail(chip: Pending): string {
  switch (chip.status) {
    case 'error':
      return chip.error
    case 'sending':
      return t.attachments.uploading(Math.round(chip.progress * 100))
    case 'ready':
      return t.attachments.size(chip.size)
  }
}

/** The box's words: its conversation's draft when it says whose they are (`draftKey`), else its
 * own; the setter writes another conversation's when given its key. */
function useWords(draftKey: string | undefined): [string, (next: SetStateAction<string>, key?: string) => void] {
  const [own, setOwn] = useState('')
  const drafts = useStore($drafts)
  const set = useCallback(
    (next: SetStateAction<string>, key = draftKey) => {
      if (key === undefined) {
        setOwn(next)

        return
      }

      setDraft(key, typeof next === 'function' ? next(draftOf(key)) : next)
    },
    [draftKey]
  )

  return [draftKey === undefined ? own : (drafts[draftKey] ?? ''), set]
}
