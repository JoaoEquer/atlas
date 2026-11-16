/**
 * What the browser shows for the conversation and the home on screen (docs/GLOSSARY.md, `app
 * browser`): their tabs and the selected one, the commands that open a tab for them, and the pages
 * the agent opens. The conversation is `currentOwner()`, as for the previews, followed here as an atom.
 */
import { atom, computed } from 'nanostores'

import { browserHome } from '@/browser-home'

import { $home, $machine, $session, currentOwner, DRAFT } from './app'
import { $browser, browserCommand, browserHost, openBrowser, settledSelection, surfaceTarget, tabsOf } from './browser'

/** Whose tabs the panel shows: the conversation on screen, as `currentOwner()` says. */
export const $browserOwner = atom<string>(DRAFT)

/** The home on screen, as the main process names the homes of its tabs. */
export const $browserHome = computed([$machine, $home], (machine, home) => browserHome(machine, home ?? 'default'))

/** The tabs the panel shows. */
export const $shownTabs = computed([$browser, $browserOwner, $browserHome], (state, owner, home) =>
  tabsOf(state, owner, home)
)

/** The tab on the panel, if one of the shown ones is selected. */
export const $shownTab = computed([$shownTabs, $browser], (tabs, state) => tabs.find(tab => tab.id === state.selected))

/** The tab's owner and home, for a command that opens one. */
export function where(): { home: string; owner: string } {
  return { home: $browserHome.get(), owner: $browserOwner.get() }
}

/** Open an empty tab for the conversation and home on screen. */
export function openTab(): Promise<BrowserAnswer | null> {
  return browserCommand({ command: 'open', ...where() })
}

/** Follow the conversation on screen through its session's own atoms, until the function returned runs. */
function followOwner(): () => void {
  let release: () => void = () => undefined
  const stop = $session.subscribe(session => {
    release()

    if (!session) {
      $browserOwner.set(DRAFT)
      release = () => undefined

      return
    }

    const update = () => {
      $browserOwner.set(currentOwner())
    }
    update()
    const stops = [session.$view.listen(update), session.$openSession.listen(update), session.$selected.listen(update)]
    release = () => {
      for (const each of stops) {
        each()
      }
    }
  })

  return () => {
    stop()
    release()
  }
}

/**
 * Keep the browser in step with the screen, for as long as the app is open (not only while the
 * panel shows it): follow its state, keep one of the shown tabs selected, and bring the panel
 * forward for a page the agent opened in the conversation on screen.
 */
export function followScreen(): () => void {
  const host = browserHost()

  if (!host) {
    return () => undefined
  }

  const settle = () => {
    const id = settledSelection($browser.get(), $browserOwner.get(), $browserHome.get())

    if (id !== null) {
      void browserCommand({ command: 'select', id })
    }
  }
  const stops = [
    followOwner(),
    $browser.listen(settle),
    $browserOwner.listen(settle),
    $browserHome.listen(settle),
    host.onSurface(surface => {
      const id = surfaceTarget(surface, $browserOwner.get(), $browserHome.get())

      if (id !== null) {
        openBrowser()
        void browserCommand({ command: 'select', id })
      }
    })
  ]
  settle()

  return () => {
    for (const stop of stops) {
      stop()
    }
  }
}
