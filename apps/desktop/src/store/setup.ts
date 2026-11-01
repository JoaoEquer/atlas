/**
 * The first run (step 18d, docs/ARCHITECTURE.md, The installer): the app's first steps, kept
 * apart per home in the window's storage, so a window closed halfway reopens on the step it was on.
 * It opens by itself while no provider has a key, and from the settings' General at any time; each
 * step is done or skipped, and leaving it at the end closes it.
 */
import { atom } from 'nanostores'

/** The steps, in their order. */
export const STEPS = ['model', 'executors', 'telegram', 'connections', 'work'] as const

export type Step = (typeof STEPS)[number]

/** What became of a step: done (its key stored, an executor on, someone let in on Telegram, a
 * service connected, the work brought) or skipped. */
export type Mark = 'done' | 'skipped'

export interface FirstRun {
  /** Opened and not finished: it shows whether a key is stored or not. */
  open: boolean
  step: Step
  marks: Partial<Record<Step, Mark>>
  /** Open, but stepped away from for a conversation it opened (Google's setup, which the agent
   * leads): the app shows, with a band back to the step. */
  away?: boolean
  /** Opened on this step alone (a first step's Start, store/first-steps.ts): done or skipped, it
   * closes instead of going on. */
  only?: Step
}

const KEY = 'atlas.firstRun'

const FRESH: FirstRun = { marks: {}, open: false, step: 'model' }

function read(): Record<string, FirstRun> {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(KEY) ?? '{}')

    return stored !== null && typeof stored === 'object' ? (stored as Record<string, FirstRun>) : {}
  } catch {
    return {}
  }
}

/** By home, its first run; a home with none has not begun it. */
export const $firstRun = atom<Record<string, FirstRun>>(read())

export function firstRunOf(all: Record<string, FirstRun>, home: null | string): FirstRun {
  const found = all[home ?? '']

  return found && (STEPS as readonly string[]).includes(found.step) ? found : FRESH
}

function update(home: null | string, change: (run: FirstRun) => FirstRun): void {
  const all = $firstRun.get()
  const next = { ...all, [home ?? '']: change(firstRunOf(all, home)) }
  $firstRun.set(next)
  localStorage.setItem(KEY, JSON.stringify(next))
}

/** Open it: by itself on a home with no key, or from the settings, where it starts over. */
export function openFirstRun(home: null | string, again = false): void {
  update(home, run => (again ? { ...FRESH, open: true } : { ...run, open: true }))
}

/** Open it on one step alone, what was done kept: a first step's Start (store/first-steps.ts). */
export function openStep(home: null | string, step: Step): void {
  update(home, run => ({ ...run, away: false, only: step, open: true, step }))
}

export function showStep(home: null | string, step: Step): void {
  update(home, run => ({ ...run, step }))
}

/** Step away to the app, the first run kept open on its step; `away` false comes back to it. */
export function stepAway(home: null | string, away = true): void {
  update(home, run => ({ ...run, away }))
}

/** A step done or skipped, and the next one shown; after the last, or opened on it alone, the
 * first run closes. */
export function finishStep(home: null | string, step: Step, mark: Mark): void {
  update(home, run => {
    const next = run.only ? undefined : STEPS[STEPS.indexOf(step) + 1]

    return {
      marks: { ...run.marks, [step]: mark },
      open: next !== undefined,
      step: next ?? 'model'
    }
  })
}

/** Whether the home has a first run of its own yet: begun, left, or offered. */
export function begun(home: null | string): boolean {
  return $firstRun.get()[home ?? ''] !== undefined
}

/** "Bring your work" offered alone (store/work.ts, `offerWork`), its earlier steps taken as done;
 * or, with nothing to offer, the home only marked, so it is never asked. */
export function markOffered(home: null | string, offered: boolean): void {
  update(home, () => ({
    marks: { executors: 'done', model: 'done' },
    open: offered,
    step: offered ? 'work' : 'model'
  }))
}

/** Closed before its end ("Go to Atlas"): what was done stays done. */
export function closeFirstRun(home: null | string): void {
  update(home, run => ({ ...run, away: false, only: undefined, open: false, step: 'model' }))
}

/** The window a "Bring your work" offers, in days; `null`, everything. */
export const WINDOWS = [7, 30, 90, null] as const

export type WorkWindow = (typeof WINDOWS)[number]
