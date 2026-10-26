/**
 * The window's look (asked for by the user on 18/10/2026): light or dark, or a theme a plugin
 * brings (SDK version 24). It is kept as a `dark` class on the page's root, which Tailwind's `dark:`
 * reads, and the choice kept in the window's own storage, a look of this computer and not of a
 * home. Light, dark, or the system's (on the settings screen since step 15b), which it follows as
 * it changes; until the user chooses, the system's.
 *
 * A plugin's theme sits on light or dark (its `base`) and repaints the app's named colors: each of
 * its colors becomes the page root's `--<name>` (`--background`, `--soft`), which the components
 * already paint with. It is a hex color for a named color and nothing else, here as in the server,
 * so a theme is data.
 * A choice whose plugin is off paints as the system's and is kept, for when it is on again.
 */
import { atom } from 'nanostores'

import type { PluginThemeRow } from '@/protocol/protocol.generated'

export type Theme = 'dark' | 'light'
/** `plugin:<plugin>:<theme>` is a theme a plugin brings. */
export type ThemeChoice = 'system' | Theme | `plugin:${string}`

const KEY = 'atlas.theme'
/** The plugin theme last on screen, to paint it before the server has said what is on. */
const ROW_KEY = 'atlas.theme.row'
const SYSTEM_DARK = '(prefers-color-scheme: dark)'
/** The app's named colors (styles.css), the only ones a theme paints; the server holds the same list. */
const TOKENS = new Set([
  'background',
  'foreground',
  'soft',
  'muted-foreground',
  'faint',
  'subtle',
  'raised',
  'muted',
  'hover',
  'selected',
  'border',
  'border-strong',
  'primary',
  'primary-foreground',
  'ring',
  'scrim',
  'working',
  'waiting',
  'waiting-text',
  'waiting-foreground',
  'done',
  'done-text',
  'failed',
  'failed-text',
  'danger',
  'added',
  'removed'
])
const HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i

/** The system's dark query; none in the tests' window, which has no `matchMedia`. */
function media(): MediaQueryList | undefined {
  return typeof globalThis.matchMedia === 'function' ? globalThis.matchMedia(SYSTEM_DARK) : undefined
}

export const themeKey = (row: Pick<PluginThemeRow, 'plugin' | 'name'>): ThemeChoice =>
  `plugin:${row.plugin}:${row.name}`

function chosen(): ThemeChoice {
  const stored = localStorage.getItem(KEY)

  return stored === 'dark' || stored === 'light' || stored?.startsWith('plugin:') ? (stored as ThemeChoice) : 'system'
}

function remembered(): PluginThemeRow | undefined {
  try {
    const row = JSON.parse(localStorage.getItem(ROW_KEY) ?? 'null') as PluginThemeRow | null

    return row && themeKey(row) === chosen() ? row : undefined
  } catch {
    return undefined
  }
}

function system(): Theme {
  return media()?.matches ? 'dark' : 'light'
}

/** The themes of the plugins that are on in the home on screen. */
export const $pluginThemes = atom<PluginThemeRow[]>([])

/** The plugin theme a choice names, when its plugin is on (or, before the server answers, when it was on). */
function pluginTheme(choice: ThemeChoice, known: boolean): PluginThemeRow | undefined {
  if (!choice.startsWith('plugin:')) {
    return undefined
  }

  return known ? $pluginThemes.get().find(row => themeKey(row) === choice) : remembered()
}

function resolved(choice: ThemeChoice, row: PluginThemeRow | undefined): Theme {
  if (row) {
    return row.base === 'dark' ? 'dark' : 'light'
  }

  return choice === 'dark' || choice === 'light' ? choice : system()
}

let known = false
let painted: string[] = []

/** What the user chose. */
export const $themeChoice = atom<ThemeChoice>(chosen())
/** The look on screen. */
export const $theme = atom<Theme>(resolved(chosen(), pluginTheme(chosen(), false)))

function paint(theme: Theme, row: PluginThemeRow | undefined): void {
  const root = document.documentElement
  root.classList.toggle('dark', theme === 'dark')
  root.style.colorScheme = theme
  for (const property of painted) {
    root.style.removeProperty(property)
  }
  painted = []
  for (const [token, color] of Object.entries(row?.colors ?? {})) {
    if (TOKENS.has(token) && HEX.test(color)) {
      root.style.setProperty(`--${token}`, color)
      painted.push(`--${token}`)
    }
  }
}

function show(): void {
  const choice = $themeChoice.get()
  const row = pluginTheme(choice, known)
  $theme.set(resolved(choice, row))
  paint($theme.get(), row)
}

/** Paint the look now, and follow the system's while it is the one chosen. */
export function startTheme(): void {
  show()
  media()?.addEventListener('change', () => {
    if ($themeChoice.get() === 'system') {
      show()
    }
  })
}

export function setTheme(choice: ThemeChoice): void {
  if (choice === 'system') {
    localStorage.removeItem(KEY)
  } else {
    localStorage.setItem(KEY, choice)
  }
  $themeChoice.set(choice)
  const row = pluginTheme(choice, true)
  if (row) {
    localStorage.setItem(ROW_KEY, JSON.stringify(row))
  } else {
    localStorage.removeItem(ROW_KEY)
  }
  known = true
  show()
}

/** The home on screen says which plugin themes are on: the chosen one is painted, or the system's. */
export function setPluginThemes(rows: PluginThemeRow[]): void {
  $pluginThemes.set(rows)
  known = true
  const row = pluginTheme($themeChoice.get(), true)
  if (row) {
    localStorage.setItem(ROW_KEY, JSON.stringify(row))
  }
  show()
}
