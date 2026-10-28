import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { ExecutorRow } from '@/protocol/protocol.generated'
import { $detecting, $workbench, $workbenchError, detectExecutors, turnOnExecutor } from '@/store/app'
import { matches } from '@/store/extensions'

import { Refresh } from './ui/icons'
import { IconButton } from './ui/tip'

/**
 * The harnesses Atlas supports, as it found them on this computer (step 16c, decided with the user
 * on 14/10/2026): each one installed to turn on, its consent given right here after reading what
 * its plugin declares; each one not found, or too old, greyed out with why, which says how to
 * install or update it. Atlas looks every time it opens, when the executors tab shows, and on
 * "Look again", so one installed while Atlas runs is found. Shown on the key screen and in
 * Settings → Extensions → Executors, there in the marketplace's grid (`grid`) and searched
 * (`query`); never in the executors sidebar, which shows only the ones turned on.
 */
export function ExecutorOffers({ grid = false, query = '' }: { grid?: boolean; query?: string }) {
  const { executors } = useStore($workbench)
  const errors = useStore($workbenchError)
  const detecting = useStore($detecting)

  if (executors.length === 0) {
    return null
  }

  return (
    <section className="space-y-2" data-testid="executor-offers">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">{t.executors.harnesses}</h2>
          <p className="text-xs text-muted-foreground">{t.executors.harnessesIntro}</p>
        </div>
        <IconButton
          className={detecting ? 'animate-spin' : ''}
          disabled={detecting}
          icon={Refresh}
          label={detecting ? t.executors.looking : t.executors.lookAgain}
          onClick={() => {
            void detectExecutors()
          }}
          testId="executors-look-again"
        />
      </div>
      <div className={grid ? '@container' : ''}>
        <div className={grid ? 'grid gap-3 @lg:grid-cols-2' : 'space-y-2'}>
          {executors
            .filter(row => matches(query, row.name, row.title))
            .map(row => (
              <Offer key={row.name} row={row} />
            ))}
        </div>
      </div>
      {errors.offer && <p className="text-xs text-failed-text">{errors.offer}</p>}
    </section>
  )
}

function Offer({ row }: { row: ExecutorRow }) {
  const [asking, setAsking] = useState(false)
  const usable = row.installed && row.supported
  const account = row.logged_in ? row.account : t.executors.notLoggedIn
  const found = [row.version && `${row.title} ${row.version}`, account].filter(Boolean).join(' · ')

  return (
    <div
      className={`space-y-2 rounded-lg border p-3 text-sm ${
        usable ? 'border-border-strong' : 'border-dashed border-border-strong'
      }`}
      data-testid="executor-offer"
      data-usable={usable}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <p className={`font-medium ${usable ? '' : 'text-muted-foreground'}`}>{row.title}</p>
          {usable ? (
            <p className="truncate text-xs text-muted-foreground">{found}</p>
          ) : (
            // Why, in full: it says the command that installs or updates it.
            <p className="text-xs break-words whitespace-pre-wrap text-soft select-text" data-testid="executor-problem">
              {row.problem || t.executors.notFound}
            </p>
          )}
        </div>
        {usable && row.enabled && <span className="text-xs text-muted-foreground">{t.executors.on}</span>}
        {usable && !row.enabled && !asking && (
          <button
            className="rounded bg-primary px-3 py-1 text-xs font-medium text-primary-foreground"
            onClick={() => {
              setAsking(true)
            }}
            type="button"
          >
            {t.executors.turnOn}
          </button>
        )}
      </div>
      {asking && (
        <div className="space-y-2 text-xs" data-testid="executor-consent">
          <ul className="list-disc space-y-1 pl-4">
            {row.declaration.map(line => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="flex gap-2">
            <button
              className="rounded bg-primary px-3 py-1 font-medium text-primary-foreground"
              onClick={() => {
                void turnOnExecutor(row).then(() => {
                  setAsking(false)
                })
              }}
              type="button"
            >
              {t.executors.confirmTurnOn}
            </button>
            <button
              className="rounded border border-border-strong px-3 py-1"
              onClick={() => {
                setAsking(false)
              }}
              type="button"
            >
              {t.executors.cancel}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
