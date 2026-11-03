/**
 * What the plugins' screens added to the app (step 16m): a tab of the side panel (`panel`), a
 * page of the main area with its entry in the sidebar (`page`), or, since screen version 2 (step
 * 17a), a button beside the words of the conversation's box (`box`). Since screen version 4 (step
 * 22), a part of an executor's session, by its box (`session`), and of an executor's place in the
 * sidebar (`executor`), each rendered with the place it shows in. The app keeps its own surfaces
 * and adds these beside them. Pure stores.
 */
import { atom } from 'nanostores'
import type { ReactNode } from 'react'

import type { InstanceRow } from '@/protocol/protocol.generated'

/** Where a contribution goes. */
export type Area = 'box' | 'executor' | 'page' | 'panel' | 'session'

/** The executor session a `session` contribution shows by: its executor (on an account,
 *  `claude-code@1a2b3c4d`), its folder, the instance on it (`null` before it starts) and the
 *  conversation that keeps its card. */
export interface SessionPlace {
  conversation: null | string
  executor: string
  folder: string
  instance: InstanceRow | null
}

/** The executor an `executor` contribution shows under in the sidebar: its name, its harness's
 *  own name (`base`, itself for the harness's own login) and its account (`''`, the own login). */
export interface ExecutorPlace {
  account: string
  base: string
  executor: string
  title: string
}

/** Where a contribution is rendered: its place, for `session` and `executor`. */
export type Place = ExecutorPlace | SessionPlace

/** A contribution as a plugin writes it: the host names it after the plugin. */
export interface Contribution {
  area: Area
  /** Unique within the plugin. */
  id: string
  /** Sorts within its area, lower first; the plugin's name breaks ties. */
  order?: number
  /** What it shows; a `session` or `executor` contribution is given its place. */
  render: (place?: Place) => ReactNode
  /** Its tab's or its sidebar entry's name. */
  title: string
}

/** A contribution in the app: whose it is, and its key, `<plugin>:<id>`. */
export interface Registered extends Contribution {
  key: string
  plugin: string
}

export const $contributions = atom<Registered[]>([])
/** The plugin page on screen, by its key, while the main area shows a plugin's page. */
export const $pluginPage = atom<null | string>(null)
/** Words a screen puts into the conversation's box (a dictation, screen version 2), each once:
 *  `seq` tells a second same text from the first. */
export const $boxInsert = atom<null | { seq: number; text: string }>(null)

/** Put `text` into the conversation's box, after what it holds, for the user to send. */
export function insertIntoBox(text: string): void {
  const words = text.trim()

  if (words) {
    $boxInsert.set({ seq: ($boxInsert.get()?.seq ?? 0) + 1, text: words })
  }
}

/** Why a plugin's screen did not load, by plugin, for the Plugins screen. */
export const $screenErrors = atom<Record<string, string>>({})

const AREAS: readonly Area[] = ['box', 'executor', 'page', 'panel', 'session']

/** Add a contribution; returns what takes it away. The same key again replaces it. */
export function contribute(plugin: string, contribution: Contribution): () => void {
  if (!AREAS.includes(contribution.area)) {
    // A screen is plain JavaScript: the area it names is not checked before this.
    throw new Error(`${plugin}: no area ${contribution.area as string}; the areas are ${AREAS.join(', ')}.`)
  }

  if (typeof contribution.render !== 'function') {
    throw new Error(`${plugin}: the contribution ${contribution.id} has nothing to render.`)
  }

  const key = `${plugin}:${contribution.id}`
  const added: Registered = { ...contribution, key, plugin }
  $contributions.set(sorted([...$contributions.get().filter(item => item.key !== key), added]))

  return () => {
    $contributions.set($contributions.get().filter(item => item !== added))

    if ($pluginPage.get() === key) {
      $pluginPage.set(null)
    }
  }
}

export function inArea(items: readonly Registered[], area: Area): Registered[] {
  return items.filter(item => item.area === area)
}

export function screenFailed(plugin: string, why: null | string): void {
  const kept = Object.entries($screenErrors.get()).filter(([name]) => name !== plugin)
  $screenErrors.set(Object.fromEntries(why === null ? kept : [...kept, [plugin, why]]))
}

function sorted(items: Registered[]): Registered[] {
  return [...items].sort(
    (a, b) => (a.order ?? 100) - (b.order ?? 100) || a.plugin.localeCompare(b.plugin) || a.id.localeCompare(b.id)
  )
}
