/** What the preload hands the window (electron/preload.ts). */
interface AtlasBridge {
  /** The side panel's browser (electron/app-browser.ts). */
  browser: BrowserBridge
  /** Where a home's backup goes, from the system's save dialog; `null` when cancelled. */
  backupTarget: (home: string, label: string) => Promise<null | string>
  /** A part of a remote machine's backup, written where the save dialog said (step 15). */
  backupWrite: (file: string, offset: number, data: string) => Promise<void>
  /** The last part came (`whole`), or the backup failed. */
  backupFinish: (file: string, whole: boolean) => Promise<void>
  /** A backup to import, from the system's open dialog; `null` when cancelled. */
  backupSource: (label: string) => Promise<null | string>
  /** A folder, from the system's open dialog; `null` when cancelled. */
  chooseFolder: () => Promise<null | string>
  /** A home's server, started if needed; `null` (the default) is the home that opens first. */
  connection: (home?: null | string) => Promise<{ token: string; url: string }>
  /** What a plugin's screen does outside the window (step 17b, electron/desktop-powers.ts). */
  desktop: DesktopBridge
  /** Started by the end-to-end tests (`--atlas-e2e`). */
  e2e: boolean
  focus: () => void
  /** A session's folder opened in VS Code; `''` once handed over, else why not. */
  openInEditor: (folder: string) => Promise<string>
  openFolder: (folder: string) => Promise<string>
  /** Stop a home's server. */
  release: (home: null | string) => Promise<void>
  /** Open this home first next time (the last home). */
  remember: (home: string) => Promise<void>
  /** A site up on another machine, as this one reaches it, or why not (step 15). */
  previewReach: (machine: null | string, url: string) => Promise<{ error: string } | { url: string }>
  /** Serve a preview's page; its address, for the sandboxed frame. */
  previewPage: (html: string) => Promise<string>
  /** The app talks to a server it did not start, which serves one home. */
  remote: boolean
  /** Only under the end-to-end tests: how long a command runs before it is "still running". */
  terminalWait: null | number
  /** The colors of the window's own buttons, as hex, where it has no title bar (Windows). */
  titleBar?: (color: string, symbolColor: string) => void
  /** The window's tabs, as the main process tells of them (electron/keys.ts). */
  tabs?: TabsBridge
  /** The app's update (electron/updates.ts). */
  updates: UpdatesBridge
  /** The other machines' Atlas this app opens (step 15, electron/machines.ts). */
  machines: MachinesBridge
  /** This machine's home: always on, open to the user's machines (step 15, electron/always-on.ts). */
  thisMachine: ThisMachineBridge
}

interface TabsBridge {
  /** On macOS, ⌘W: close the tab on screen. */
  onClose: (listener: () => void) => () => void
}

/** A browser tab, as the main process tells it (electron/app-browser.ts, `BrowserTabState`). */
interface BrowserTab {
  /** Opened by the agent. */
  agent: boolean
  canGoBack: boolean
  canGoForward: boolean
  /** Why the page did not load, or `''`. */
  error: string
  /** The home it belongs to, as `browserHome` (store/browser.ts) writes it. */
  home: string
  id: string
  loading: boolean
  /** The conversation it belongs to (`currentOwner()`). */
  owner: string
  title: string
  url: string
}

/** The agent opened a page, or asked for a tab to come forward (electron/app-browser.ts, `Surface`). */
interface BrowserSurface {
  home: string
  id: string
  owner: string
  reason: 'activate' | 'page'
}

interface BrowserState {
  selected: null | string
  tabs: BrowserTab[]
}

/** What a browser command came to. */
type BrowserAnswer = { id?: string; ok: true } | { ok: false; reason: 'address' | 'closed' | 'invalid' | 'unknown' }

type BrowserCommand =
  | { command: 'adopt'; from: string; home: string; to: string }
  | { command: 'back' | 'close' | 'forward' | 'reload' | 'select' | 'stop'; id: string }
  | { command: 'go'; home: string; id: null | string; owner: string; text: string }
  | { command: 'open'; home: string; owner: string }
  | { command: 'show'; rect: null | { height: number; width: number; x: number; y: number } }

interface BrowserBridge {
  command: (command: BrowserCommand) => Promise<BrowserAnswer>
  onChange: (listener: (state: BrowserState) => void) => () => void
  onSurface: (listener: (surface: BrowserSurface) => void) => () => void
  /** The ports where a web server answers on this machine: the common ones and `extra`. */
  servers: (extra: number[]) => Promise<number[]>
  state: () => Promise<BrowserState>
}

/** What a call to the main process says: its value, or why not in words. */
type Answer<T> = { error: string; ok: false } | { ok: true; value: T }

/** How the app reaches a machine. */
type Transport = 'relay' | 'ssh' | 'tailscale'

/** A machine of the list, as the window sees it: never its token. */
interface MachineRow {
  host: null | string
  home: string
  id: string
  name: string
  port: number
  sshHost: null | string
  transport: Transport
  /** The relay and the home's room there, for a `relay` machine (step 20). */
  relay?: null | { room: string; url: string }
}

interface MachinesBridge {
  add: (given: { code: string; name: string; sshHost?: string; transport: Transport }) => Promise<Answer<MachineRow>>
  list: () => Promise<MachineRow[]>
  remove: (id: string) => Promise<Answer<undefined>>
  rename: (id: string, name: string) => Promise<Answer<MachineRow>>
  /** Both legs, `/health` and the WebSocket with the token, on a connection of its own. */
  test: (id: string) => Promise<Answer<undefined>>
}

/** `atlas always-on status --json`. */
interface AlwaysOnStatus {
  home: string
  installed: boolean
  on: boolean
  open: boolean
  port: null | number
  served: boolean
  tailnet: null | { address: string; name: null | string }
  tailnet_problem: null | string
}

interface ThisMachineBridge {
  /** Put the home's connection code on the clipboard; the window never holds it. */
  copyCode: (home: null | string) => Promise<Answer<undefined>>
  newToken: (home: null | string) => Promise<Answer<undefined>>
  set: (home: null | string, change: 'close' | 'off' | 'on' | 'open') => Promise<Answer<undefined>>
  status: (home: null | string) => Promise<Answer<AlwaysOnStatus>>
}

/** What a look for an update says (`atlas.installation.UpdateStatus`). */
interface UpdateStatus {
  available: boolean
  behind: number
  commits: string[]
  /** Each commit's `Notes-pt-BR` trailer, its subject in Portuguese, or "" when it has none. */
  commits_pt_br: string[]
  current: string
  dirty: boolean
  error: string
  /** How the installation updates: `native`, or `compose`, a server's host updater. */
  kind?: string
  reason: string
  supported: boolean
  target: string
  /** Why CI did not verify the target, which the user then confirms; "" or absent when it did. */
  unverified?: string
}

/** How the update the app handed off ended (`atlas.installation.HandedOff`). */
interface HandedOff {
  after: string
  before: string
  commits: string[]
  ended_at: string
  error: string
  log: string
  ok: boolean
  reopen: string
}

/** A plugin's screen's powers outside the window (step 17b). */
interface DesktopBridge {
  /** Show the floating bar, or hide it with `null`. */
  bar: (state: null | { text: string; tone: 'done' | 'error' | 'recording' | 'working' }) => Promise<void>
  /** Hear a shortcut held for `key` (`<plugin>:<id>`). */
  onPressed: (listener: (key: string) => void) => () => void
  /** Put a text where the user is: typed in the app (`app`), pasted in the program in focus
   *  (`pasted`), or left on the clipboard (`copied`). */
  paste: (text: string) => Promise<'app' | 'copied' | 'pasted'>
  /** Hold `accelerator` for `key`, `''` to let go; `''`, or why it cannot be held. */
  shortcut: (key: string, accelerator: string) => Promise<string>
}

interface UpdatesBridge {
  /** Hand the update off; on `ok` the app closes, and opens again once updated. */
  apply: () => Promise<{ error: string; ok: boolean }>
  check: () => Promise<null | UpdateStatus>
  /** How the last update ended, once, after it opened the app again. */
  ended: () => Promise<HandedOff | null>
  onChange: (listener: (status: UpdateStatus) => void) => () => void
  openLog: () => Promise<string>
  /** The last look's answer; `null` before the first, or where the app looks for none. */
  status: () => Promise<null | UpdateStatus>
}

/** A file read for a preview (electron/preview-ipc.ts). */
type PreviewFile =
  | { dataUrl: string; kind: 'image' }
  | { error: string; kind: 'error' }
  | { kind: 'html' | 'markdown' | 'text'; text: string }

/** A command's end, as the terminal read it from its shell's marks (src/terminal/terminal.ts). */
interface TerminalRun {
  exitCode: null | number
  output: string
  status: 'completed' | 'running' | 'unfinished'
}

/** The side panel's terminals: shells on the server's machine, over the connection (step 15,
 * src/terminal/connection.ts). */
interface TerminalBridge {
  attach: (id: string) => Promise<boolean>
  /** The server's machine opens terminals. */
  available: () => Promise<boolean>
  busy: (id: string) => Promise<boolean>
  dispose: (id: string) => Promise<void>
  onData: (listener: (id: string, text: string) => void) => () => void
  /** A command given as still running has ended. */
  onEnded: (
    listener: (id: string, end: { command: string; exitCode: null | number; output: string }) => void
  ) => () => void
  onExit: (listener: (id: string, code: number) => void) => () => void
  resize: (id: string, cols: number, rows: number) => Promise<void>
  run: (id: string, command: string) => Promise<TerminalRun>
  start: (options: {
    cols: number
    cwd: string
    rows: number
  }) => Promise<{ id: string; marks: boolean; shell: string }>
  write: (id: string, data: string) => Promise<void>
}

interface Window {
  atlas: AtlasBridge
  /** Only under the end-to-end tests: drop the current home's connection, as a network loss does. */
  atlasE2e?: { reconnect: () => void }
}
