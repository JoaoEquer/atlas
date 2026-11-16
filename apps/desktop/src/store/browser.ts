/**
 * The side panel's browser, as the window sees it (docs/GLOSSARY.md, `app browser`): the browser
 * tabs the main process keeps (electron/app-browser.ts), told as they change. The pages live in
 * native views the main process lays over the panel; this holds only what the toolbar and the tabs
 * show.
 *
 * The main process keeps every tab of every conversation and home; the window shows the ones of the
 * conversation and the home on screen, as the previews' are (store/previews.ts), and the main process
 * lets the agent of a conversation drive all of that conversation's. What the window shows is chosen
 * here (`tabsOf`), and kept on a tab that is there (`settledSelection`); the agent never takes the
 * selection (`surfaceTarget` decides what its first page of a conversation does to the screen).
 */
import { atom, computed } from 'nanostores'

import { $panelOpen, $panelTab } from './terminals'

/** The panel's tab with the browser, in `$panelTab` (store/terminals.ts). */
export const BROWSER_TAB = 'browser'

export const $browser = atom<BrowserState>({ selected: null, tabs: [] })

/** The browser the app brings: none in the tests' window, nor outside the app. */
export function browserHost(): BrowserBridge | undefined {
  return (window as Partial<Window>).atlas?.browser
}

/** Follow the browser's state until the returned function is called. */
export function followBrowser(): () => void {
  const host = browserHost()

  if (!host) {
    return () => undefined
  }

  let live = true
  void host.state().then(state => {
    if (live) {
      $browser.set(state)
    }
  })
  const stop = host.onChange(state => {
    $browser.set(state)
  })

  return () => {
    live = false
    stop()
  }
}

/** Ask the browser for something; `null` where there is none. */
export async function browserCommand(command: BrowserCommand): Promise<BrowserAnswer | null> {
  return (await browserHost()?.command(command)) ?? null
}

/** The browser asked to take the conversation's place (its header's expand button). */
export const $browserWanted = atom(false)

/** Whether it does: asked for, the panel open and on the browser. The panel keeps its place beside
 * the conversation on any other tab, and goes back to it when the browser is left. */
export const $browserExpanded = computed(
  [$browserWanted, $panelOpen, $panelTab],
  (wanted, open, tab) => wanted && open && tab === BROWSER_TAB
)

export function toggleBrowserExpanded(): void {
  $browserWanted.set(!$browserWanted.get())
}

/** Show the panel on the browser, from the globe or its shortcut. */
export function openBrowser(): void {
  $panelOpen.set(true)
  $panelTab.set(BROWSER_TAB)
}

/** The globe's switch: the browser when it is not on screen, else the panel hides. */
export function toggleBrowser(): void {
  if ($panelOpen.get() && $panelTab.get() === BROWSER_TAB) {
    $panelOpen.set(false)
  } else {
    openBrowser()
  }
}

/** The tabs of a conversation and a home: what the panel shows. */
export function tabsOf(state: BrowserState, owner: string, home: string): BrowserTab[] {
  return state.tabs.filter(tab => tab.owner === owner && tab.home === home)
}

/** The tab to select so that one of the shown ones is, or `null` when one is or none can be: the
 * last of the conversation's, as when a tab closes. */
export function settledSelection(state: BrowserState, owner: string, home: string): null | string {
  const shown = tabsOf(state, owner, home)

  if (shown.length === 0 || shown.some(tab => tab.id === state.selected)) {
    return null
  }

  return shown.at(-1)?.id ?? null
}

/** The tab a page the agent opened (or asked for) brings forward: only when its conversation and
 * home are the ones on screen; another conversation's is never selected, nor opens the panel. */
export function surfaceTarget(surface: BrowserSurface, owner: string, home: string): null | string {
  return surface.owner === owner && surface.home === home ? surface.id : null
}

/** A conversation just made takes the tabs the draft had (`''`), those of the home on screen. */
export function adoptDraft(conversationId: string, home: string): void {
  void browserCommand({ command: 'adopt', from: '', home, to: conversationId })
}

/** The ports where a web server answers on this machine, the common ones and `extra`. */
export async function lookForServers(extra: number[]): Promise<number[]> {
  return (await browserHost()?.servers(extra)) ?? []
}
