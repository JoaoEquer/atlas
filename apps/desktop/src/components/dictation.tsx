import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import { insertIntoBox } from '@/plugins/registry'
import type { DictationRow } from '@/protocol/protocol.generated'
import { $dictationsSeen } from '@/store/app'
import {
  $shortcut,
  acceleratorOf,
  chooseShortcut,
  deleteDictation,
  dictationAudio,
  DONE_MS,
  listDictations,
  microphone,
  pauseShortcut,
  type Recording,
  resumeShortcut,
  writeAgain,
  writeDictation
} from '@/store/dictation'

import { Microphone } from './ui/icons'

/**
 * Dictation in the app (step 26, docs/ARCHITECTURE.md, Speech): the microphone beside the box's
 * words, whose words land in the box to be read before they are sent, and the Dictations page,
 * where every dictation is kept with its audio and dictation anywhere's key is chosen.
 */

const BOX_BUTTON =
  'grid size-8 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-selected disabled:opacity-40'

/** The box's microphone: a click records, a second click writes it down into the box. */
export function DictateButton() {
  const [state, setState] = useState<'idle' | 'recording' | 'writing'>('idle')
  const [error, setError] = useState('')
  const recording = useRef<null | Recording>(null)

  useEffect(
    () => () => {
      recording.current?.cancel()
    },
    []
  )

  const start = async () => {
    setError('')

    try {
      recording.current = await microphone.start()
      setState('recording')
    } catch (failed) {
      setError(failed instanceof Error ? failed.message : String(failed))
    }
  }

  const stop = async () => {
    const made = recording.current
    recording.current = null

    if (!made) {
      return
    }

    setState('writing')

    try {
      const { blob } = await made.stop()
      const written = await writeDictation(blob, 'box')

      if (written.error) {
        setError(written.error)
      } else {
        insertIntoBox(written.text)
      }
    } catch (failed) {
      setError(failed instanceof Error ? failed.message : String(failed))
    } finally {
      setState('idle')
    }
  }

  const label =
    state === 'recording'
      ? t.dictation.recordingBox
      : state === 'writing'
        ? t.dictation.transcribing
        : t.dictation.dictate

  return (
    <button
      aria-label={label}
      aria-pressed={state === 'recording'}
      className={`${BOX_BUTTON} ${state === 'recording' ? 'bg-failed text-white hover:bg-failed' : error ? 'text-failed-text' : ''}`}
      data-state={state}
      data-testid="dictate"
      disabled={state === 'writing'}
      onClick={() => void (state === 'recording' ? stop() : start())}
      title={error ? `${label}\n${error}` : label}
      type="button"
    >
      <Microphone aria-hidden size={16} stroke={1.75} />
    </button>
  )
}

const PAGE_BUTTON = 'rounded-md px-3 py-1.5 text-sm text-soft hover:bg-selected'

function DictationItem({
  copied,
  onAgain,
  onCopy,
  onListen,
  onRemove,
  row
}: {
  copied: boolean
  onAgain: () => void
  onCopy: () => void
  onListen: () => void
  onRemove: () => void
  row: DictationRow
}) {
  return (
    <li className="rounded-lg border border-border p-4" data-testid="dictation">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="text-sm text-muted-foreground">
            {`${new Date(row.at).toLocaleString()} · ${row.where === 'anywhere' ? t.dictation.anywhere : t.dictation.inBox}`}
          </div>
          {row.error ? (
            <p className="mt-2 text-sm text-waiting-text">{t.dictation.notWritten(row.error)}</p>
          ) : (
            <p className="mt-1 text-sm" data-testid="dictation-text">
              {row.text || '—'}
            </p>
          )}
        </div>
        <div className="mt-1 flex gap-2">
          <button className={PAGE_BUTTON} data-testid="dictation-listen" onClick={onListen} type="button">
            {t.dictation.listen}
          </button>
          {row.error ? (
            <button className={PAGE_BUTTON} data-testid="dictation-again" onClick={onAgain} type="button">
              {t.dictation.again}
            </button>
          ) : (
            <button className={PAGE_BUTTON} data-testid="dictation-copy" onClick={onCopy} type="button">
              {copied ? t.dictation.copyDone : t.dictation.copy}
            </button>
          )}
          <button className={PAGE_BUTTON} data-testid="dictation-remove" onClick={onRemove} type="button">
            {t.dictation.remove}
          </button>
        </div>
      </div>
    </li>
  )
}

/** The Dictations page: every dictation, newest first, and dictation anywhere's key. */
export function DictationsView() {
  const seen = useStore($dictationsSeen)
  const shortcut = useStore($shortcut)
  const [rows, setRows] = useState<DictationRow[] | null>(null)
  const [error, setError] = useState('')
  const [choosing, setChoosing] = useState(false)
  const [copied, setCopied] = useState('')

  useEffect(() => {
    let live = true
    listDictations().then(
      found => {
        if (live) {
          setRows(found)
        }
      },
      (failed: unknown) => {
        if (live) {
          setError(failed instanceof Error ? failed.message : String(failed))
        }
      }
    )

    return () => {
      live = false
    }
  }, [seen])

  useEffect(() => {
    if (!choosing) {
      return undefined
    }

    // The key held now would be heard instead of chosen: let it go while choosing.
    void pauseShortcut().catch(() => undefined)
    const onKey = (event: KeyboardEvent) => {
      event.preventDefault()
      event.stopPropagation()

      if (event.key === 'Escape') {
        setChoosing(false)
        void resumeShortcut()

        return
      }

      const chosen = acceleratorOf(event)

      if (chosen) {
        setChoosing(false)
        void chooseShortcut(chosen)
      }
    }
    window.addEventListener('keydown', onKey, true)

    return () => {
      window.removeEventListener('keydown', onKey, true)
    }
  }, [choosing])

  const run = (what: () => Promise<unknown>) => {
    setError('')
    what().catch((failed: unknown) => {
      setError(failed instanceof Error ? failed.message : String(failed))
    })
  }

  const listen = (id: string) => {
    run(async () => {
      const found = await dictationAudio(id)
      const bytes = Uint8Array.from(atob(found.audio), character => character.charCodeAt(0))
      const url = URL.createObjectURL(new Blob([bytes], { type: found.media_type }))
      const audio = new Audio(url)
      audio.addEventListener(
        'ended',
        () => {
          URL.revokeObjectURL(url)
        },
        { once: true }
      )
      await audio.play()
    })
  }

  const copy = (row: DictationRow) => {
    run(async () => {
      await navigator.clipboard.writeText(row.text)
      setCopied(row.id)
      window.setTimeout(() => {
        setCopied('')
      }, DONE_MS)
    })
  }

  return (
    <div className="flex-1 overflow-y-auto" data-testid="dictations">
      <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
        <h1 className="text-lg font-semibold">{t.dictation.page}</h1>
        <p className="text-sm text-muted-foreground">{t.dictation.pageIntro(shortcut.key)}</p>
        <div className="flex items-center justify-between gap-4">
          <div className="text-sm">
            {`${t.dictation.shortcut}: `}
            <span className="font-mono" data-testid="dictation-shortcut">
              {choosing ? t.dictation.pressKey : shortcut.key || '—'}
            </span>
          </div>
          <div className="flex gap-2">
            <button
              className={PAGE_BUTTON}
              data-testid="dictation-shortcut-change"
              onClick={() => {
                if (choosing) {
                  setChoosing(false)
                  void resumeShortcut()
                } else {
                  setChoosing(true)
                }
              }}
              type="button"
            >
              {choosing ? t.dictation.cancel : shortcut.key ? t.dictation.change : t.dictation.choose}
            </button>
            {shortcut.key && !choosing && (
              <button
                className={PAGE_BUTTON}
                data-testid="dictation-shortcut-off"
                onClick={() => void chooseShortcut('')}
                type="button"
              >
                {t.dictation.turnOff}
              </button>
            )}
          </div>
        </div>
        {shortcut.why && (
          <p className="text-sm text-waiting-text" data-testid="dictation-shortcut-failed">
            {t.dictation.shortcutFailed(shortcut.why)}
          </p>
        )}
        {error && (
          <p className="rounded-md bg-failed/10 px-3 py-2 text-sm text-failed-text" role="alert">
            {error}
          </p>
        )}
        {rows !== null &&
          (rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t.dictation.none}</p>
          ) : (
            <ul className="space-y-2">
              {rows.map(row => (
                <DictationItem
                  copied={copied === row.id}
                  key={row.id}
                  onAgain={() => {
                    run(() => writeAgain(row.id))
                  }}
                  onCopy={() => {
                    copy(row)
                  }}
                  onListen={() => {
                    listen(row.id)
                  }}
                  onRemove={() => {
                    run(() => deleteDictation(row.id))
                  }}
                  row={row}
                />
              ))}
            </ul>
          ))}
      </div>
    </div>
  )
}
