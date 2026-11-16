import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { $home, $machine } from './app'
import { $browser, adoptDraft, BROWSER_TAB, settledSelection, surfaceTarget, tabsOf } from './browser'
import { $browserHome, $browserOwner, $shownTab, $shownTabs, followScreen, openTab } from './browser-screen'
import { $panelOpen, $panelTab } from './terminals'

function tab(id: string, owner: string, home: string, agent = false): BrowserTab {
  return {
    agent,
    canGoBack: false,
    canGoForward: false,
    error: '',
    home,
    id,
    loading: false,
    owner,
    title: id,
    url: `https://${id}.example.com/`
  }
}

const WORK = 'local/work'
const OTHER = 'local/personal'

const state = (selected: null | string, ...tabs: BrowserTab[]): BrowserState => ({ selected, tabs })

describe('the tabs on screen', () => {
  const all = state(
    null,
    tab('a', 'c-1', WORK),
    tab('b', 'c-2', WORK),
    tab('c', 'c-1', WORK, true),
    tab('d', 'c-1', OTHER),
    tab('e', '', WORK)
  )

  it('are the ones of the conversation and the home, whoever opened them', () => {
    expect(tabsOf(all, 'c-1', WORK).map(each => each.id)).toEqual(['a', 'c'])
    expect(tabsOf(all, 'c-2', WORK).map(each => each.id)).toEqual(['b'])
    expect(tabsOf(all, 'c-1', OTHER).map(each => each.id)).toEqual(['d'])
    expect(tabsOf(all, '', WORK).map(each => each.id)).toEqual(['e'])
    expect(tabsOf(all, 'c-3', WORK)).toEqual([])
  })

  it('keep a selected one of theirs, else fall to the last of theirs, else none', () => {
    expect(settledSelection(state('a', ...all.tabs), 'c-1', WORK)).toBeNull()
    expect(settledSelection(state('b', ...all.tabs), 'c-1', WORK)).toBe('c')
    expect(settledSelection(state(null, ...all.tabs), 'c-1', WORK)).toBe('c')
    expect(settledSelection(state('a', ...all.tabs), 'c-2', WORK)).toBe('b')
    expect(settledSelection(state('a', ...all.tabs), 'c-3', WORK)).toBeNull()
  })

  it("are brought forward by the agent's page only in the conversation and home on screen", () => {
    const surface = { home: WORK, id: 'c', owner: 'c-1', reason: 'page' } as const

    expect(surfaceTarget(surface, 'c-1', WORK)).toBe('c')
    expect(surfaceTarget(surface, 'c-2', WORK)).toBeNull()
    expect(surfaceTarget(surface, 'c-1', OTHER)).toBeNull()
  })
})

describe('the browser following the screen', () => {
  const commands: BrowserCommand[] = []
  let surfaced: ((surface: BrowserSurface) => void) | null = null
  let stop: () => void

  beforeEach(() => {
    commands.length = 0
    ;(window as unknown as { atlas: { browser: Partial<BrowserBridge> } }).atlas = {
      browser: {
        command: (command: BrowserCommand) => {
          commands.push(command)

          return Promise.resolve({ ok: true })
        },
        onChange: () => () => undefined,
        onSurface: listener => {
          surfaced = listener

          return () => {
            surfaced = null
          }
        },
        state: () => Promise.resolve({ selected: null, tabs: [] })
      }
    }
    $machine.set(null)
    $home.set('work')
    $browser.set(state(null))
    $panelOpen.set(false)
    $panelTab.set('terminal')
    stop = followScreen()
    // Without a session on screen the conversation is the draft's; the tests pick one.
    $browserOwner.set('c-1')
  })

  afterEach(() => {
    stop()
    $browser.set(state(null))
  })

  it('shows the tabs of the conversation, and changes with the conversation and the home', () => {
    $browser.set(state('a', tab('a', 'c-1', WORK), tab('b', 'c-2', WORK), tab('d', 'c-1', OTHER)))

    expect($browserHome.get()).toBe(WORK)
    expect($shownTabs.get().map(each => each.id)).toEqual(['a'])
    expect($shownTab.get()?.id).toBe('a')

    $browserOwner.set('c-2')
    expect($shownTabs.get().map(each => each.id)).toEqual(['b'])
    expect($shownTab.get()).toBeUndefined()

    $browserOwner.set('c-1')
    $home.set('personal')
    expect($shownTabs.get().map(each => each.id)).toEqual(['d'])
    $machine.set('m1')
    expect($browserHome.get()).toBe('m1/personal')
    expect($shownTabs.get()).toEqual([])
  })

  it('selects a tab of the new conversation when the selected one is not its own', () => {
    $browser.set(state('a', tab('a', 'c-1', WORK), tab('b', 'c-2', WORK)))
    expect(commands).toEqual([])

    $browserOwner.set('c-2')
    expect(commands).toEqual([{ command: 'select', id: 'b' }])

    commands.length = 0
    $browserOwner.set('c-3')
    expect(commands).toEqual([])
  })

  it("opens the tabs for the conversation on screen, and gives a new conversation the draft's", async () => {
    await openTab()
    adoptDraft('c-9', WORK)

    expect(commands).toEqual([
      { command: 'open', home: WORK, owner: 'c-1' },
      { command: 'adopt', from: '', home: WORK, to: 'c-9' }
    ])
  })

  it("opens the hidden panel on the Browser tab for the agent's first page in the conversation on screen", () => {
    $browser.set(state('a', tab('a', 'c-1', WORK), tab('c', 'c-1', WORK, true)))
    surfaced?.({ home: WORK, id: 'c', owner: 'c-1', reason: 'page' })

    expect($panelOpen.get()).toBe(true)
    expect($panelTab.get()).toBe(BROWSER_TAB)
    expect(commands).toEqual([{ command: 'select', id: 'c' }])
  })

  it('does nothing for a page of another conversation or home', () => {
    surfaced?.({ home: WORK, id: 'b', owner: 'c-2', reason: 'page' })
    surfaced?.({ home: OTHER, id: 'd', owner: 'c-1', reason: 'activate' })

    expect($panelOpen.get()).toBe(false)
    expect(commands).toEqual([])
  })

  it("leaves the selection where it is when the agent's next tab comes in the background", () => {
    $browser.set(state('a', tab('a', 'c-1', WORK)))
    $browser.set(state('a', tab('a', 'c-1', WORK), tab('c', 'c-1', WORK, true)))

    expect(commands).toEqual([])
  })
})
