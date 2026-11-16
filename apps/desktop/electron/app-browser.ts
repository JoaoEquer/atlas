/**
 * The app browser (docs/GLOSSARY.md, `app browser`): the side panel's Browser tab, a page of the
 * web in a native view laid over the panel, one `WebContentsView` per browser tab.
 *
 * - **A site reaches nothing of Atlas.** Every view is in its home's partition (`partitionOfKey`:
 *   cookies and logins are one home's, not another's), with no preload, the sandbox, context
 *   isolation and no Node. Camera, microphone, location, notifications and the rest are refused
 *   (`app-browser-rules.ts`); only `http(s)` pages load, a redirect or a link to any other scheme is
 *   stopped, and a request to a cloud metadata address is cancelled whoever made it; a download is
 *   refused (the browser has no place to put one yet).
 * - **The window's own commands are the only way in** for the user. `registerAppBrowser` answers the
 *   channels of the preload, from the window alone, and each command is checked for its shape
 *   before it is run.
 * - **A tab belongs to a conversation and a home**, whoever opened it (the user in the panel, or the
 *   agent through the browser host, `browser-host.ts`); a pop-up belongs to the tab that opened it.
 *   The panel shows the tabs of the conversation and home on screen; the agent of a conversation
 *   sees and drives all of that conversation's.
 * - **The agent drives the same views**: every view keeps a `webContents.debugger` attached, which
 *   never reaches the window's own page, and `cdp` passes the agent's commands to it, only the ones
 *   `cdpAllowed` lets through. Found by the feasibility test of 24/10/2026, a view that is hidden,
 *   laid off the window or in a minimized window ignores `Input.*` and never answers
 *   `Page.captureScreenshot`, unless it is told it has the focus (`Emulation.setFocusEmulationEnabled`),
 *   is active (`Page.setWebLifecycleState`) and is not throttled; so each view gets the three as it
 *   is made and again after each navigation. A hidden view has a desktop-sized box, which the page
 *   lays itself out in; shown, it takes the panel's.
 * - **The agent never takes the user's place on screen.** A tab it opens does not become the
 *   selected one; the window is told (`atlas:browser:surface`) of the first page the agent opens in
 *   a conversation, and when the agent asks for a tab to come forward, and it decides.
 * - **A pop-up is a tab**, made from the `webContents` Electron hands over, so `window.opener`
 *   stays (a sign-in through a pop-up needs it).
 */
import { randomUUID } from 'node:crypto'

import { type BrowserWindow, type IpcMain, session, type WebContents, WebContentsView } from 'electron'

import {
  blockedRequest,
  type BrowserCommand,
  cdpAllowed,
  firstAgentPage,
  normalizeAddress,
  pageAllowed,
  parseCommand,
  partitionOfKey,
  permissionGranted,
  type Rect
} from './app-browser-rules'
import { closesTab } from './keys'
import { lookForServers, parsePorts, portsToLook } from './local-servers'

/** A browser tab as the window shows it. */
export interface BrowserTabState {
  /** Opened by the agent (or by a page of one of its tabs). */
  agent: boolean
  canGoBack: boolean
  canGoForward: boolean
  /** Why the page did not load, or `''`. */
  error: string
  /** The home it belongs to, as `browserHome` writes it. */
  home: string
  id: string
  loading: boolean
  /** The conversation it belongs to: `''` for one not made yet, `session:<id>` for an executor's. */
  owner: string
  title: string
  url: string
}

/** The browser as the window sees it. */
export interface BrowserState {
  selected: null | string
  tabs: BrowserTabState[]
}

/** What a command came to. */
export type BrowserAnswer =
  { id?: string; ok: true } | { ok: false; reason: 'address' | 'closed' | 'invalid' | 'unknown' }

/** What the window is told when the agent wants a tab seen: its first page in a conversation, or a
 * request to bring one forward. */
export interface Surface {
  home: string
  id: string
  owner: string
  reason: 'activate' | 'page'
}

/** A tab as the agent's server sees it. */
export interface AgentTab {
  id: string
  loading: boolean
  owner: string
  selected: boolean
  title: string
  url: string
}

/** What the browser host asks of the browser, for one home. */
export interface AgentBrowser {
  /** Run a DevTools command in a tab. Rejects with the command's own message. */
  cdp: (home: string, id: string, method: string, params: Record<string, unknown>) => Promise<unknown>
  close: (home: string, id: string) => boolean
  /** Where the agent's tabs say something: a DevTools event, or that the debugger let go. */
  onEvent: (listener: (home: string, id: string, method: string, params: unknown) => void) => () => void
  open: (home: string, owner: string, url: string, background: boolean) => string
  show: (home: string, id: string) => boolean
  list: (home: string) => { product: string; tabs: AgentTab[]; userAgent: string }
  /** Start (or stop) sending a tab's DevTools events. */
  watch: (home: string, id: string, on: boolean) => boolean
}

interface Tab {
  agent: boolean
  error: string
  home: string
  id: string
  owner: string
  view: WebContentsView
  /** The agent has a session on it: its DevTools events are sent. */
  watched: boolean
}

/** The channel the browser tells the window its state on. */
export const BROWSER_CHANGED = 'atlas:browser:changed'
/** The channel it tells the window of a page the agent wants seen. */
export const BROWSER_SURFACE = 'atlas:browser:surface'

/** What Chromium reports for a load a newer one replaced: not an error. */
const ABORTED = -3

/** The size of a view that is not on screen: a page lays itself out as on a desktop. */
const HIDDEN_SIZE = { height: 800, width: 1280 }

const configured = new Set<string>()

/** Refuse what a site may ask the machine for, any download and any request to a metadata address;
 * once for each partition. */
export function configureBrowserSession(partition: string): void {
  if (configured.has(partition)) {
    return
  }

  configured.add(partition)
  const browsing = session.fromPartition(partition)
  browsing.setPermissionRequestHandler((_contents, permission, callback) => {
    callback(permissionGranted(permission))
  })
  browsing.setPermissionCheckHandler((_contents, permission) => permissionGranted(permission))
  browsing.setDevicePermissionHandler(() => false)
  browsing.on('will-download', event => {
    event.preventDefault()
  })
  browsing.webRequest.onBeforeRequest((details, callback) => {
    callback({ cancel: blockedRequest(details.url) })
  })
}

export class AppBrowser implements AgentBrowser {
  private rect: null | Rect = null
  private selected: null | string = null
  private readonly agentPages = new Set<string>()
  private readonly listeners = new Set<(home: string, id: string, method: string, params: unknown) => void>()
  private readonly tabs = new Map<string, Tab>()
  private tell: null | ReturnType<typeof setImmediate> = null

  constructor(
    private readonly window: BrowserWindow,
    private readonly log: (message: string) => void = () => undefined
  ) {
    window.on('closed', () => {
      this.dispose()
    })
  }

  /** The window's page: the only one the browser answers. */
  get owner(): WebContents {
    return this.window.webContents
  }

  snapshot(): BrowserState {
    return {
      selected: this.selected,
      tabs: [...this.tabs.values()].map(tab => this.stateOf(tab))
    }
  }

  /** Run a command from the window. */
  run(command: BrowserCommand): BrowserAnswer {
    switch (command.command) {
      case 'open': {
        return { id: this.create({ agent: false, home: command.home, owner: command.owner }).id, ok: true }
      }

      case 'go': {
        return this.go(command.id, command.text, command.owner, command.home)
      }

      case 'adopt': {
        for (const tab of this.tabs.values()) {
          if (tab.home === command.home && tab.owner === command.from) {
            tab.owner = command.to
          }
        }

        this.changed()

        return { ok: true }
      }

      case 'show': {
        this.rect = command.rect
          ? {
              height: Math.max(0, Math.round(command.rect.height)),
              width: Math.max(0, Math.round(command.rect.width)),
              x: Math.round(command.rect.x),
              y: Math.round(command.rect.y)
            }
          : null
        this.layout()

        return { ok: true }
      }

      case 'select': {
        if (!this.tabs.has(command.id)) {
          return { ok: false, reason: 'unknown' }
        }

        this.selected = command.id
        this.layout()
        this.changed()

        return { ok: true }
      }

      case 'close': {
        const tab = this.tabs.get(command.id)

        if (!tab) {
          return { ok: false, reason: 'unknown' }
        }

        this.remove(tab, true)

        return { ok: true }
      }

      default: {
        return this.navigate(command.command, command.id)
      }
    }
  }

  // -- what the agent asks, through the browser host ------------------------------------------------

  list(home: string): { product: string; tabs: AgentTab[]; userAgent: string } {
    const own = [...this.tabs.values()].filter(tab => tab.home === home)
    const browsing = session.fromPartition(partitionOfKey(home))

    return {
      product: `Chrome/${process.versions.chrome}`,
      tabs: own.map(tab => {
        const state = this.stateOf(tab)

        return {
          id: state.id,
          loading: state.loading,
          owner: state.owner,
          selected: state.id === this.selected,
          title: state.title,
          url: state.url
        }
      }),
      userAgent: browsing.getUserAgent()
    }
  }

  /** A tab for a conversation, opened by the agent: behind the selected one when `background`. */
  open(home: string, owner: string, url: string, background: boolean): string {
    const tab = this.create({ agent: true, home, owner, select: !background })

    if (url && url !== 'about:blank' && pageAllowed(url)) {
      tab.view.webContents.loadURL(url).catch(() => undefined)
    }

    return tab.id
  }

  close(home: string, id: string): boolean {
    const tab = this.tabs.get(id)

    if (tab?.home !== home) {
      return false
    }

    this.remove(tab, true)

    return true
  }

  show(home: string, id: string): boolean {
    const tab = this.tabs.get(id)

    if (tab?.home !== home) {
      return false
    }

    this.surface({ home, id, owner: tab.owner, reason: 'activate' })

    return true
  }

  watch(home: string, id: string, on: boolean): boolean {
    const tab = this.tabs.get(id)

    if (tab?.home !== home) {
      return false
    }

    tab.watched = on

    return true
  }

  async cdp(home: string, id: string, method: string, params: Record<string, unknown>): Promise<unknown> {
    const tab = this.tabs.get(id)

    if (tab?.home !== home) {
      throw new Error('No such tab.')
    }

    if (!cdpAllowed(method)) {
      throw new Error(`${method} is not available in the app's browser.`)
    }

    if (method === 'Page.navigate' && !(typeof params.url === 'string' && pageAllowed(params.url))) {
      throw new Error("Only web pages open in the app's browser.")
    }

    const contents = tab.view.webContents

    if (contents.isDestroyed()) {
      throw new Error('The tab is gone.')
    }

    if (!contents.debugger.isAttached()) {
      this.keepAwake(contents)
    }

    return contents.debugger.sendCommand(method, params)
  }

  onEvent(listener: (home: string, id: string, method: string, params: unknown) => void): () => void {
    this.listeners.add(listener)

    return () => {
      this.listeners.delete(listener)
    }
  }

  /** Every view gone, with the window. */
  dispose(): void {
    if (this.tell !== null) {
      clearImmediate(this.tell)
      this.tell = null
    }

    for (const tab of [...this.tabs.values()]) {
      this.remove(tab, true, false)
    }
  }

  private go(id: null | string, text: string, owner: string, home: string): BrowserAnswer {
    const address = normalizeAddress(text)

    if (address === null) {
      return { ok: false, reason: 'address' }
    }

    const tab = (id === null ? undefined : this.tabs.get(id)) ?? this.create({ agent: false, home, owner })

    if (this.selected !== tab.id) {
      this.selected = tab.id
      this.layout()
      this.changed()
    }

    // A load a newer one replaces rejects; the tab says what failed.
    tab.view.webContents.loadURL(address).catch(() => undefined)

    return { id: tab.id, ok: true }
  }

  private navigate(command: 'back' | 'forward' | 'reload' | 'stop', id: string): BrowserAnswer {
    const tab = this.tabs.get(id)

    if (!tab) {
      return { ok: false, reason: 'unknown' }
    }

    const contents = tab.view.webContents

    if (command === 'back' && contents.navigationHistory.canGoBack()) {
      contents.navigationHistory.goBack()
    } else if (command === 'forward' && contents.navigationHistory.canGoForward()) {
      contents.navigationHistory.goForward()
    } else if (command === 'reload') {
      contents.reload()
    } else if (command === 'stop') {
      contents.stop()
    }

    return { ok: true }
  }

  /** A new browser tab. The user's is the one on screen (`select`, which is the default); the
   * agent's is not. `given` is a pop-up's `webContents`. */
  private create(options: { agent: boolean; given?: WebContents; home: string; owner: string; select?: boolean }): Tab {
    const partition = partitionOfKey(options.home)
    configureBrowserSession(partition)
    const view = new WebContentsView(
      options.given
        ? { webContents: options.given }
        : {
            webPreferences: {
              contextIsolation: true,
              nodeIntegration: false,
              partition,
              sandbox: true,
              webSecurity: true
            }
          }
    )
    const tab: Tab = {
      agent: options.agent,
      error: '',
      home: options.home,
      id: randomUUID(),
      owner: options.owner,
      view,
      watched: false
    }
    const contents = view.webContents
    this.tabs.set(tab.id, tab)
    this.window.contentView.addChildView(view)

    if (options.select ?? !options.agent) {
      this.selected = tab.id
    }

    this.layout()
    this.guard(tab)
    this.keepAwake(contents)
    this.changed()

    return tab
  }

  /** Only web pages, and a pop-up as a tab; the tab follows its page's own events. */
  private guard(tab: Tab): void {
    const contents = tab.view.webContents
    const refuse = (event: { preventDefault: () => void }, url: string) => {
      if (!pageAllowed(url)) {
        event.preventDefault()
      }
    }

    contents.on('will-navigate', (event, url) => {
      refuse(event, url)
    })
    contents.on('will-redirect', (event, url) => {
      refuse(event, url)
    })
    // ⌘W in a page closes its tab, as in a browser; the menu's Close Window would close the app's (keys.ts).
    contents.on('before-input-event', (event, input) => {
      if (closesTab(input, process.platform)) {
        event.preventDefault()
        this.remove(tab, true)
      }
    })
    contents.setWindowOpenHandler(({ url }) =>
      pageAllowed(url)
        ? {
            action: 'allow',
            // Electron's types leave out the `webContents` it puts in the options: the pop-up's own, which
            // the new view must be made from (a view of another one throws). It belongs to the
            // conversation and the home of the tab that opened it.
            createWindow: options =>
              this.create({
                agent: tab.agent,
                given: (options as { webContents: WebContents }).webContents,
                home: tab.home,
                owner: tab.owner
              }).view.webContents
          }
        : { action: 'deny' }
    )
    contents.on('did-start-navigation', (_event, _url, _inPlace, isMainFrame) => {
      if (isMainFrame) {
        tab.error = ''
      }
    })
    contents.on('did-fail-load', (_event, code, description, _url, isMainFrame) => {
      if (isMainFrame && code !== ABORTED) {
        tab.error = description
        this.changed()
      }
    })
    contents.on('render-process-gone', (_event, details) => {
      tab.error = details.reason
      this.changed()
    })
    contents.on('did-navigate', (_event, url) => {
      this.keepAwake(contents)

      // The agent's first page in a conversation brings the panel forward, if that conversation is on screen.
      if (tab.agent && url !== 'about:blank' && firstAgentPage(this.agentPages, tab.home, tab.owner)) {
        this.surface({ home: tab.home, id: tab.id, owner: tab.owner, reason: 'page' })
      }
    })
    contents.debugger.on('message', (_event, method, params, sessionId) => {
      // Only the page's own events: a frame of another process answers on a session of its own.
      if (tab.watched && !sessionId) {
        this.emit(tab, method, params)
      }
    })
    contents.debugger.on('detach', (_event, reason) => {
      this.emit(tab, 'Inspector.detached', { reason })
    })

    for (const event of [
      'did-navigate',
      'did-navigate-in-page',
      'did-start-loading',
      'did-stop-loading',
      'page-title-updated'
    ] as const) {
      contents.on(event as 'did-navigate', () => {
        this.changed()
      })
    }

    contents.on('destroyed', () => {
      this.remove(tab, false)
    })
  }

  private emit(tab: Tab, method: string, params: unknown): void {
    if (!tab.watched) {
      return
    }

    for (const listener of this.listeners) {
      listener(tab.home, tab.id, method, params)
    }
  }

  /** The three things a view needs to answer the agent while it is hidden (see the top). */
  private keepAwake(contents: WebContents): void {
    if (contents.isDestroyed()) {
      return
    }

    contents.setBackgroundThrottling(false)

    try {
      if (!contents.debugger.isAttached()) {
        contents.debugger.attach('1.3')
      }

      const send = (method: string, params: object) =>
        contents.debugger.sendCommand(method, params).catch((error: unknown) => {
          this.log(`The browser tab's ${method} failed: ${String(error)}`)
        })
      void send('Emulation.setFocusEmulationEnabled', { enabled: true })
      void send('Page.setWebLifecycleState', { state: 'active' })
    } catch (error) {
      this.log(`The browser tab's debugger did not attach: ${String(error)}`)
    }
  }

  private remove(tab: Tab, closeView: boolean, announce = true): void {
    if (!this.tabs.delete(tab.id)) {
      return
    }

    // A tab the agent drives tells it the tab is gone, as a page does that closes.
    this.emit(tab, 'Inspector.detached', { reason: 'target_closed' })

    if (this.selected === tab.id) {
      const rest = [...this.tabs.values()].filter(each => each.home === tab.home && each.owner === tab.owner)
      this.selected = rest.at(-1)?.id ?? null
    }

    if (!this.window.isDestroyed()) {
      this.window.contentView.removeChildView(tab.view)
    }

    if (closeView && !tab.view.webContents.isDestroyed()) {
      tab.view.webContents.close()
    }

    if (announce) {
      this.layout()
      this.changed()
    }
  }

  /** The selected tab's view in the panel's place; every other one, and all while the panel is not
   * showing the browser, hidden but desktop-sized (hidden views still answer the agent). */
  private layout(): void {
    for (const tab of this.tabs.values()) {
      const shown = this.rect !== null && tab.id === this.selected
      tab.view.setVisible(shown)
      tab.view.setBounds(shown && this.rect ? this.rect : { ...HIDDEN_SIZE, x: 0, y: 0 })
    }
  }

  private stateOf(tab: Tab): BrowserTabState {
    const contents = tab.view.webContents
    const url = contents.getURL()

    return {
      agent: tab.agent,
      canGoBack: contents.navigationHistory.canGoBack(),
      canGoForward: contents.navigationHistory.canGoForward(),
      error: tab.error,
      home: tab.home,
      id: tab.id,
      loading: contents.isLoading(),
      owner: tab.owner,
      title: contents.getTitle(),
      url: url === 'about:blank' ? '' : url
    }
  }

  private surface(surface: Surface): void {
    if (!this.window.isDestroyed() && !this.window.webContents.isDestroyed()) {
      this.window.webContents.send(BROWSER_SURFACE, surface)
    }
  }

  /** Tell the window, once for a burst of events. */
  private changed(): void {
    if (this.tell !== null) {
      return
    }

    this.tell = setImmediate(() => {
      this.tell = null

      if (!this.window.isDestroyed() && !this.window.webContents.isDestroyed()) {
        this.window.webContents.send(BROWSER_CHANGED, this.snapshot())
      }
    })
  }
}

/** The window's channels to the browser (a command, its state, a look for local servers); each answers the window's own page alone. */
export function registerAppBrowser(ipc: IpcMain, current: () => AppBrowser | null): void {
  ipc.handle('atlas:browser:command', (event, value: unknown): BrowserAnswer => {
    const browser = current()

    if (!browser || event.sender !== browser.owner) {
      return { ok: false, reason: 'closed' }
    }

    const command = parseCommand(value)

    return command ? browser.run(command) : { ok: false, reason: 'invalid' }
  })
  ipc.handle('atlas:browser:state', (event): BrowserState => {
    const browser = current()

    return browser && event.sender === browser.owner ? browser.snapshot() : { selected: null, tabs: [] }
  })
  // The ports among the common ones, and the window's own, that answer as web servers here.
  ipc.handle('atlas:browser:servers', async (event, extra: unknown): Promise<number[]> => {
    const browser = current()
    const ports = parsePorts(extra)

    return browser && event.sender === browser.owner && ports ? lookForServers(portsToLook(ports)) : []
  })
}
