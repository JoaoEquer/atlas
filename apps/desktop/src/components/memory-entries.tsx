import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useState } from 'react'

import { t } from '@/i18n'
import type { MemoryEntries as Entries, MemoryEdit } from '@/protocol/protocol.generated'
import { $home, editMemory, readMemory } from '@/store/app'

import { Pencil, Trash } from './ui/icons'
import { ICON_BUTTON } from './ui/tip'

type Target = Entries['target']

/**
 * "About you" (`USER.md`) or the memory (`MEMORY.md`) on the settings screen's Personalization
 * (step 15b): each entry read, edited or removed, and one added, with the limit in characters in
 * view. Every edit names the exact entry it changes and goes through
 * the memory tool's own door on the server, so an entry the agent changed meanwhile is refused, not
 * overwritten. Read again whenever it shows, the home changes or its limit does (`limit`, the
 * setting as written; `children`, the field that edits it).
 */
export function MemoryEntries({
  children,
  hint,
  limit,
  target,
  title
}: {
  children?: ReactNode
  hint: string
  limit?: null | string
  target: Target
  title: string
}) {
  const home = useStore($home)
  const [read, setRead] = useState<Entries | null>(null)
  const [editing, setEditing] = useState<null | number>(null)
  const [draft, setDraft] = useState('')
  const [adding, setAdding] = useState('')
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    let live = true
    setRead(null)
    setError(null)
    readMemory(target).then(
      entries => {
        if (live) {
          setRead(entries)
        }
      },
      (failure: unknown) => {
        if (live) {
          setError(failure instanceof Error ? failure.message : String(failure))
        }
      }
    )

    return () => {
      live = false
    }
  }, [home, target, limit])

  const run = async (edit: Omit<MemoryEdit, 'target'>): Promise<boolean> => {
    setWorking(true)
    setError(null)

    try {
      setRead(await editMemory({ ...edit, target }))

      return true
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))

      return false
    } finally {
      setWorking(false)
    }
  }

  return (
    <section className="space-y-3" data-testid={`memory-${target}`}>
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold">{title}</h3>
        {read && (
          <span className="text-xs text-muted-foreground" data-testid="memory-used">
            {t.settings.used(read.used, read.limit)}
          </span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">{hint}</p>
      {children}
      {read && (
        <code className="block truncate text-xs text-muted-foreground" title={read.path}>
          {read.path}
        </code>
      )}
      {error && (
        <p className="rounded-md bg-failed/10 px-3 py-2 text-sm text-failed-text" role="alert">
          {error}
        </p>
      )}
      {read && read.entries.length === 0 && <p className="text-sm text-muted-foreground">{t.settings.empty}</p>}
      {read && read.entries.length > 0 && (
        <ul className="space-y-2">
          {read.entries.map((entry, index) =>
            editing === index ? (
              <li className="space-y-2" key={entry}>
                <textarea
                  aria-label={t.settings.edit}
                  className={FIELD}
                  data-testid="memory-entry-text"
                  onChange={event => {
                    setDraft(event.target.value)
                  }}
                  value={draft}
                />
                <div className="flex justify-end gap-2">
                  <button
                    className={SECONDARY}
                    onClick={() => {
                      setEditing(null)
                    }}
                    type="button"
                  >
                    {t.settings.cancel}
                  </button>
                  <button
                    className={PRIMARY}
                    data-testid="memory-entry-save"
                    disabled={working || !draft.trim() || draft.trim() === entry}
                    onClick={() => {
                      void run({ action: 'replace', content: draft, entry }).then(done => {
                        if (done) {
                          setEditing(null)
                        }
                      })
                    }}
                    type="button"
                  >
                    {t.settings.save}
                  </button>
                </div>
              </li>
            ) : (
              <li
                className="group flex items-start gap-2 rounded-md border border-border px-3 py-2 text-sm"
                data-testid="memory-entry"
                key={entry}
              >
                <span className="min-w-0 flex-1 whitespace-pre-wrap">{entry}</span>
                <button
                  aria-label={t.settings.edit}
                  className={ICON_BUTTON}
                  data-testid="memory-entry-edit"
                  disabled={working}
                  onClick={() => {
                    setEditing(index)
                    setDraft(entry)
                  }}
                  type="button"
                >
                  <Pencil aria-hidden size={15} stroke={1.75} />
                </button>
                <button
                  aria-label={t.settings.remove}
                  className={ICON_BUTTON}
                  data-testid="memory-entry-remove"
                  disabled={working}
                  onClick={() => {
                    void run({ action: 'remove', entry })
                  }}
                  type="button"
                >
                  <Trash aria-hidden size={15} stroke={1.75} />
                </button>
              </li>
            )
          )}
        </ul>
      )}
      {read && (
        <div className="flex items-start gap-2">
          <textarea
            aria-label={t.settings.addPlaceholder}
            className={FIELD}
            data-testid="memory-add-text"
            onChange={event => {
              setAdding(event.target.value)
            }}
            placeholder={t.settings.addPlaceholder}
            rows={2}
            value={adding}
          />
          <button
            className={PRIMARY}
            data-testid="memory-add"
            disabled={working || !adding.trim()}
            onClick={() => {
              void run({ action: 'add', content: adding }).then(done => {
                if (done) {
                  setAdding('')
                }
              })
            }}
            type="button"
          >
            {t.settings.add}
          </button>
        </div>
      )}
    </section>
  )
}

const FIELD = 'min-h-16 w-full rounded-md border border-border-strong bg-raised p-2 text-sm'
const PRIMARY =
  'shrink-0 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-40'
const SECONDARY = 'rounded-md px-3 py-1.5 text-sm text-soft hover:bg-muted'
