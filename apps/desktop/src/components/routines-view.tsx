import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { RoutineRow, RunRow } from '@/protocol/protocol.generated'
import {
  $plugins,
  $readError,
  $routines,
  $runs,
  $scoreboard,
  $workbench,
  actOnRoutine,
  openMarketItem,
  openRoutineForm,
  readRuns,
  refreshPlugins,
  routineDraft,
  selectConversation,
  showMarketplace
} from '@/store/app'
import { templateDraft, templatesOf } from '@/store/routine-templates'

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
  const [tab, setTab] = useState<'routines' | 'templates'>('routines')

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
        <div className="flex gap-1 border-b border-border text-sm" role="tablist">
          {(['routines', 'templates'] as const).map(name => (
            <button
              aria-selected={tab === name}
              className={`-mb-px border-b-2 px-3 py-1.5 ${tab === name ? 'border-primary font-medium' : 'border-transparent text-muted-foreground'}`}
              data-testid={`routines-tab-${name}`}
              key={name}
              onClick={() => {
                setTab(name)

                if (name === 'templates') {
                  refreshPlugins()
                }
              }}
              role="tab"
              type="button"
            >
              {name === 'routines' ? t.routines.title : t.templates.tab}
            </button>
          ))}
        </div>
        {tab === 'templates' ? <Templates /> : null}
        {score && (score.alone > 0 || score.needed > 0 || score.failed > 0) ? (
          <p className="text-sm text-soft" data-testid="routines-scoreboard">
            {t.routines.scoreboard(score.alone, score.needed, score.failed)}
          </p>
        ) : null}
        {tab === 'routines' && (
          <>
            {failed && <ReadFailed error={failed} what="routines" />}
            {!failed && routines.length === 0 && <p className="text-sm text-muted-foreground">{t.routines.empty}</p>}
            {routines.map(routine => (
              <Routine key={routine.id} routine={routine} />
            ))}
          </>
        )}
      </div>
    </div>
  )
}

/** The templates (step 25): a card each, by group, the essential first; Add opens the form. */
function Templates() {
  const plugins = useStore($plugins)
  const executors = useStore($workbench).executors.filter(found => found.installed)
  const on = (name: string) => plugins.some(found => found.name === name && found.enabled && found.consented)

  return (
    <div className="space-y-4" data-testid="routine-templates">
      <p className="text-sm text-muted-foreground">{t.templates.intro}</p>
      {(['project', 'personal'] as const).map(group => (
        <section className="space-y-2" key={group}>
          <h2 className="text-sm font-medium text-muted-foreground">{t.templates.groups[group]}</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {templatesOf(group).map(template => {
              const words = t.templates.words[template.id]
              const missing = template.plugin && !on(template.plugin) ? template.plugin : null

              return (
                <div
                  className="flex flex-col gap-2 rounded-lg border border-border p-3"
                  data-template={template.id}
                  data-testid="routine-template"
                  key={template.id}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium">{words.title}</p>
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-xs text-soft">
                      {t.templates.tiers[template.tier]}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {template.schedule} · {group === 'project' ? t.templates.agentRuns : t.templates.atlasRuns} ·{' '}
                    {t.templates.effort(template.effort)}
                  </p>
                  {missing && (
                    <p className="text-xs text-waiting-text" data-testid="routine-template-needs">
                      {t.templates.needs(missing)}
                    </p>
                  )}
                  <div className="flex justify-end">
                    <button
                      className={BUTTON}
                      data-testid="routine-template-add"
                      onClick={() => {
                        if (missing) {
                          showMarketplace()
                          openMarketItem(missing)

                          return
                        }

                        openRoutineForm(templateDraft(template, words, executors[0]?.name ?? ''))
                      }}
                      type="button"
                    >
                      {missing ? t.templates.install(missing) : t.templates.add}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      ))}
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
