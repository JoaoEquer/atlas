/**
 * The first steps (step 18e): five things to do in Atlas once the first run is over, each a way
 * of learning it by doing it, in a card at the sidebar's foot until they are all done or it is
 * hidden. Kept apart per home in the window's storage, like the first run: what was once done
 * stays done, though the session, the plugin or the bot that did it is gone since. Step 26 made
 * the second account's task (its accounts went to a plugin) installing something from the store.
 */
import { atom } from 'nanostores'

import type { BotRow, InstanceRow, PairingRow, PluginRow, RoutineRow } from '@/protocol/protocol.generated'

/** The tasks, in their order. */
export const TASKS = ['delegate', 'store', 'telegram', 'routine', 'bot'] as const

export type Task = (typeof TASKS)[number]

export interface FirstSteps {
  hidden: boolean
  done: Task[]
}

const KEY = 'atlas.firstSteps'

const FRESH: FirstSteps = { done: [], hidden: false }

function read(): Record<string, FirstSteps> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}')

    return stored !== null && typeof stored === 'object' ? (stored as Record<string, FirstSteps>) : {}
  } catch {
    return {}
  }
}

/** By home, its first steps; a home with none has done none and shows them. */
export const $firstSteps = atom<Record<string, FirstSteps>>(read())

export function firstStepsOf(all: Record<string, FirstSteps>, home: null | string): FirstSteps {
  const found = all[home ?? '']

  return found && Array.isArray(found.done) ? found : FRESH
}

function update(home: null | string, change: (steps: FirstSteps) => FirstSteps): void {
  const all = $firstSteps.get()
  const next = { ...all, [home ?? '']: change(firstStepsOf(all, home)) }
  $firstSteps.set(next)
  localStorage.setItem(KEY, JSON.stringify(next))
}

/** What the home shows done now: an executor session run (started on its card or delegated), a
 * plugin not Atlas's own (a skill from the store is marked as it is installed), someone let in on
 * Telegram, a routine, a bot. */
export function doneNow(state: {
  bots: BotRow[]
  instances: InstanceRow[]
  pairings: PairingRow[]
  plugins: PluginRow[]
  routines: RoutineRow[]
}): Task[] {
  const done: Record<Task, boolean> = {
    bot: state.bots.length > 0,
    delegate: state.instances.length > 0,
    routine: state.routines.length > 0,
    store: state.plugins.some(row => !row.bundled),
    telegram: state.pairings.some(row => row.channel === 'telegram' && row.approved)
  }

  return TASKS.filter(task => done[task])
}

/** Keep `tasks` as done, once: nothing is written when none is new. */
export function markDone(home: null | string, tasks: Task[]): void {
  const known = firstStepsOf($firstSteps.get(), home).done
  const fresh = tasks.filter(task => !known.includes(task))

  if (fresh.length > 0) {
    update(home, steps => ({ ...steps, done: TASKS.filter(task => known.includes(task) || fresh.includes(task)) }))
  }
}

export function hideFirstSteps(home: null | string): void {
  update(home, steps => ({ ...steps, hidden: true }))
}
