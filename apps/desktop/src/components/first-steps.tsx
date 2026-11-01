import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { t } from '@/i18n'
import {
  $bots,
  $connection,
  $home,
  $pairings,
  $read,
  $routines,
  $workbench,
  newBot,
  newConversationWith,
  showSidebarTab
} from '@/store/app'
import { $firstSteps, doneNow, firstStepsOf, hideFirstSteps, markDone, type Task, TASKS } from '@/store/first-steps'
import { $firstRun, firstRunOf, openStep } from '@/store/setup'

import { ArrowRight, Check } from './ui/icons'

/**
 * The first steps (step 18e, store/first-steps.ts), at the sidebar's foot: five tasks that teach
 * Atlas by doing them, asked for by the user on 01/11/2026 after the first run, each with its Start;
 * the first one not done is the one offered. The card goes once all are done, or when hidden.
 */
export function FirstSteps() {
  const home = useStore($home)
  const { state } = useStore($connection)
  const read = useStore($read)
  const { executors, instances } = useStore($workbench)
  const bots = useStore($bots)
  const pairings = useStore($pairings)
  const routines = useStore($routines)
  const steps = firstStepsOf(useStore($firstSteps), home)
  const setup = firstRunOf(useStore($firstRun), home)
  // Only once the home's lists are read: before that, every task would look not done.
  const known = state === 'open' && read.workbench && read.bots
  const now = known ? doneNow({ bots, executors, instances, pairings, routines }) : []
  const fresh = now.join(' ')

  useEffect(() => {
    if (fresh) {
      markDone(home, fresh.split(' ') as Task[])
    }
  }, [fresh, home])

  const done = TASKS.filter(task => steps.done.includes(task) || now.includes(task))
  const next = TASKS.find(task => !done.includes(task))

  if (!known || steps.hidden || setup.open || next === undefined) {
    return null
  }

  const executorOn = executors.some(row => row.enabled && row.installed && row.supported)
  const start: Record<Task, () => void> = {
    account: () => {
      openStep(home, 'executors')
    },
    bot: newBot,
    delegate: () => {
      if (executorOn) {
        showSidebarTab('executors')
      } else {
        openStep(home, 'executors')
      }
    },
    routine: () => {
      newConversationWith(t.firstSteps.routinePrompt)
    },
    telegram: () => {
      openStep(home, 'telegram')
    }
  }

  return (
    <section className="mx-3 mb-2 rounded-lg border border-border bg-raised p-3" data-testid="first-steps">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold">{t.firstSteps.title}</h2>
        <span className="text-xs text-muted-foreground" data-testid="first-steps-count">
          {t.firstSteps.count(done.length, TASKS.length)}
        </span>
      </div>
      <div aria-hidden className="mt-2 h-1 overflow-hidden rounded-full bg-border">
        <div
          className="h-full rounded-full bg-foreground transition-[width]"
          style={{ width: `${String((done.length / TASKS.length) * 100)}%` }}
        />
      </div>
      <ol className="mt-2 space-y-0.5">
        {TASKS.map(task => {
          const finished = done.includes(task)
          const offered = task === next

          return (
            <li key={task}>
              <button
                className={`group flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                  offered ? 'bg-selected' : finished ? '' : 'hover:bg-hover'
                }`}
                data-done={finished ? 'true' : undefined}
                data-testid={`first-step-${task}`}
                disabled={finished}
                onClick={start[task]}
                type="button"
              >
                <span
                  aria-hidden
                  className={`grid size-4 shrink-0 place-items-center rounded-full ${
                    finished
                      ? 'bg-primary text-primary-foreground'
                      : offered
                        ? 'border border-foreground'
                        : 'border border-border-strong'
                  }`}
                >
                  {finished ? (
                    <Check size={11} stroke={2.5} />
                  ) : (
                    offered && <span className="size-1.5 rounded-full bg-foreground" />
                  )}
                </span>
                <span
                  className={`min-w-0 flex-1 truncate ${
                    finished ? 'text-muted-foreground line-through' : offered ? 'font-medium' : 'text-soft'
                  }`}
                >
                  {t.firstSteps.tasks[task]}
                </span>
                {!finished && (
                  <span
                    className={`flex shrink-0 items-center gap-0.5 text-xs font-medium ${
                      offered ? '' : 'invisible group-hover:visible'
                    }`}
                  >
                    {t.firstSteps.start}
                    <ArrowRight aria-hidden size={12} stroke={2} />
                  </span>
                )}
              </button>
            </li>
          )
        })}
      </ol>
      <div className="mt-1 flex justify-end">
        <button
          className="text-xs text-muted-foreground hover:text-foreground"
          data-testid="first-steps-hide"
          onClick={() => {
            hideFirstSteps(home)
          }}
          type="button"
        >
          {t.firstSteps.hide}
        </button>
      </div>
    </section>
  )
}
