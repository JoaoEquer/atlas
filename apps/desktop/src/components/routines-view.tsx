import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { RoutineRow, RunRow } from '@/protocol/protocol.generated'
import {
  $readError,
  $routines,
  $runs,
  $scoreboard,
  actOnRoutine,
  openRoutineForm,
  readRuns,
  routineDraft,
  selectConversation
} from '@/store/app'

import { PageHeader } from './page-header'
import { ReadFailed } from './read-failed'
import { RoutineFormHost } from './routine-form'

/**
 * The routines, kept short (decided on 11/10/2026): the list, run now,
 * pause or resume, remove, and each routine's runs, a prompt's opening as its conversation. Since
 * step 25 a prompt routine is made and changed here too, in a form, beside the conversation
 * (cronjob_manage) and the terminal (atlas routines); a command routine stays the terminal's.
 */
export function RoutinesView() {
  const routines = useStore($routines)
  const score = useStore($scoreboard)
  const failed = useStore($readError).routines

  return (
    <div className="flex-1 overflow-y-auto" data-testid="routines-view">
      <div className="mx-auto max-w-3xl space-y-4 px-6 py-6">
        <PageHeader
          actions={
            <button
              className={BUTTON}
              data-testid="routine-new"
              onClick={() => {
                openRoutineForm()
              }}
              type="button"
            >
              {t.routines.newRoutine}
            </button>
          }
          intro={t.routines.intro}
          title={t.routines.title}
        />
        <RoutineFormHost />
        {score && (score.alone > 0 || score.needed > 0 || score.failed > 0) ? (
          <p className="text-sm text-soft" data-testid="routines-scoreboard">
            {t.routines.scoreboard(score.alone, score.needed, score.failed)}
          </p>
        ) : null}
        {failed && <ReadFailed error={failed} what="routines" />}
        {!failed && routines.length === 0 && <p className="text-sm text-muted-foreground">{t.routines.empty}</p>}
        {routines.map(routine => (
          <Routine key={routine.id} routine={routine} />
        ))}
      </div>
    </div>
  )
}

function Routine({ routine }: { routine: RoutineRow }) {
  const runs = useStore($runs)[routine.id]
  const [open, setOpen] = useState(false)
  const [removing, setRemoving] = useState(false)
  const act = (action: 'pause' | 'remove' | 'resume' | 'run') => void actOnRoutine(action, routine.id)

  return (
    <div className="rounded-lg border border-border p-4" data-testid="routine-row">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-medium">
            {routine.name}
            {routine.bot && (
              <span className="ml-2 rounded bg-muted px-1.5 py-0.5 font-mono text-xs font-normal text-soft">
                @{routine.bot}
              </span>
            )}
          </p>
          <p className="text-sm text-muted-foreground">
            {routine.schedule} · {routine.enabled ? t.routines.next(when(routine.next_run_at)) : t.routines.paused}
            {routine.last_status
              ? ` · ${t.routines.last(t.routines.status(routine.running ? 'running' : routine.last_status))}`
              : ''}
          </p>
          <p className="mt-1 truncate text-sm">{routine.prompt ?? `$ ${routine.command ?? ''}`}</p>
          {routine.probe ? (
            <p className="mt-1 truncate font-mono text-xs text-muted-foreground" data-testid="routine-probe">
              {t.routines.probe(routine.probe)}
            </p>
          ) : null}
          {routine.executor ? (
            <p className="mt-1 truncate text-xs text-muted-foreground" data-testid="routine-executor">
              {t.routines.executor(routine.executor.name, routine.executor.folder, routine.executor.workspace)}
            </p>
          ) : null}
        </div>
        <div className="flex shrink-0 gap-2 text-sm">
          <button
            className={BUTTON}
            disabled={routine.running}
            onClick={() => {
              act('run')
            }}
            type="button"
          >
            {t.routines.runNow}
          </button>
          {routine.prompt !== null && (
            <button
              className={BUTTON}
              data-testid="routine-edit"
              onClick={() => {
                openRoutineForm(routineDraft(routine))
              }}
              type="button"
            >
              {t.routines.edit}
            </button>
          )}
          <button
            className={BUTTON}
            onClick={() => {
              act(routine.enabled ? 'pause' : 'resume')
            }}
            type="button"
          >
            {routine.enabled ? t.routines.pause : t.routines.resume}
          </button>
          {removing ? (
            <button
              className={`${BUTTON} text-failed-text`}
              onClick={() => {
                act('remove')
              }}
              type="button"
            >
              {t.routines.confirmRemove}
            </button>
          ) : (
            <button
              className={BUTTON}
              onClick={() => {
                setRemoving(true)
              }}
              type="button"
            >
              {t.routines.remove}
            </button>
          )}
        </div>
      </div>
      <button
        className="mt-2 text-sm text-muted-foreground hover:underline"
        onClick={() => {
          setOpen(!open)
          if (!open) {
            void readRuns(routine.id)
          }
        }}
        type="button"
      >
        {open ? t.routines.hideRuns : t.routines.showRuns}
      </button>
      {open && <Runs runs={runs} />}
    </div>
  )
}

function Runs({ runs }: { runs: RunRow[] | undefined }) {
  if (!runs) {
    return null
  }

  if (runs.length === 0) {
    return <p className="mt-2 text-sm text-muted-foreground">{t.routines.noRuns}</p>
  }

  return (
    <ul className="mt-2 space-y-1 text-sm" data-testid="routine-runs">
      {runs.map(run => (
        <li className="flex items-baseline gap-2" key={run.number}>
          <span className="text-muted-foreground">{when(run.started_at)}</span>
          <span>{t.routines.status(run.status)}</span>
          {run.lateness === 'late' || run.lateness === 'catch_up' ? (
            <span className="text-waiting-text">{t.routines.late}</span>
          ) : null}
          {run.event ? (
            <span className="text-muted-foreground" data-testid="run-event" title={run.event}>
              {t.routines.probed}
            </span>
          ) : null}
          {run.conversation_id ? (
            <button className="link" onClick={() => void selectConversation(run.conversation_id ?? '')} type="button">
              {t.routines.open}
            </button>
          ) : (
            <span className="truncate text-muted-foreground">{run.error ?? run.output}</span>
          )}
        </li>
      ))}
    </ul>
  )
}

const BUTTON = 'rounded-md border border-border-strong px-2 py-1 hover:bg-muted disabled:opacity-40'

/** A UTC time as the user's local date and time. */
function when(utc: null | string): string {
  return utc ? new Date(utc).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : '—'
}
