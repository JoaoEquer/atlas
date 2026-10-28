/**
 * The app's main process: the window, closed to everything but the app, and the Atlas servers
 * behind it (`server.ts`), one per open home.
 *
 * The window rules: context isolation, the sandbox, no Node in the page; every new window denied,
 * never opened as a side effect (GHSA-9f4c-93c8-jc8g); an `http(s)` link the page asks to open
 * goes to the system browser; and a navigation away from the app is refused. The preload hands the
 * page only the connections, and a way to bring the window forward from a notification.
 *
 * **One server per open home**, each pinned to its home (docs/ARCHITECTURE.md, Backup and homes):
 * the window asks for a home's connection, and its server starts then. The window decides which ones
 * to let go (at most 3 live, the least recently used one without a running turn leaving first),
 * because it knows which turns run.
 *
 * **The homes from the app** (step 10c, docs/ARCHITECTURE.md, Homes in the app): the first home
 * is the last one the window showed (`homes.ts`), and a backup's path comes from the system's save
 * and open dialogs; the home's server reads and writes the file there.
 *
 * **Several machines** (step 15, docs/ARCHITECTURE.md, Several machines): a home always on is
 * reached at its service's server (`always-on.ts`), never started nor stopped here; another
 * machine's Atlas, from the machine list (`machines.ts`), over Tailscale or an SSH tunnel
 * (`remote-machine.ts`). The window names it `machine:<id>`; a home's name, or `null`, is this
 * machine's. This machine's switches run the terminal's own commands (`atlas always-on`), and its
 * connection code goes to the clipboard from here: the window never holds a token.
 */
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  app,
  BrowserWindow,
  clipboard,
  dialog,
  globalShortcut,
  ipcMain,
  Menu,
  protocol,
  safeStorage,
  screen,
  shell
} from 'electron'

import { messagesFor, pickLocale } from '../src/i18n'

import { activeHome, AttachedServer, cliRunner, failure, homeFolder, readAlwaysOn } from './always-on'
import { BackupDownloads } from './backup-down'
import { Bar, e2ePowers, registerDesktopPowers, systemKeys } from './desktop-powers'
import { openInEditor } from './editor'
import { backupName, homesRoot, lastHome, rememberHome } from './homes'
import { closesTab } from './keys'
import { type Machine, MachineError, Machines, type Transport } from './machines'
import { PREVIEW_SCHEME, registerPreviews } from './preview-ipc'
import { RemoteMachine } from './remote-machine'
import { type Endpoint, OwnedServer, remoteEndpoint, repositoryCommand, repositoryRoot } from './server'
import { readToolchain, toolchainFolders, withPath } from './toolchain'
import {
  CHECK_EVERY_MS,
  checkUpdate,
  type HandedOff,
  handOff,
  installationPython,
  takeResult,
  updateFiles,
  type UpdateRun,
  updateRunning,
  type UpdateStatus
} from './updates'

const here = path.dirname(fileURLToPath(import.meta.url))
const appRoot = path.resolve(here, '..')
/** The app's icon (`npm run icons` draws it): the window's and the taskbar's, and the Dock's on macOS.
 * On Windows the .ico, whose small sizes are drawn for them rather than scaled down. */
const icon = path.join(appRoot, 'resources', process.platform === 'win32' ? 'icon.ico' : 'icon.png')
/** Who the app is to the Windows taskbar. Opened from its Start Menu shortcut, Windows takes the
 * shortcut's icon; opened by an update, or by `atlas desktop`, it is Electron's own program with
 * Electron's icon, unless the window names itself, its icon and how it opens again (`createWindow`). */
const APP_ID = 'Atlas.Desktop'
const devServer = process.env.ATLAS_DESKTOP_DEV_SERVER
/** Started by the end-to-end tests: the window may drop its own connection, and the tests read the
 * servers' PIDs from this process, and name the stand-in of the update's Python
 * (`ATLAS_E2E_UPDATE`). Nothing else changes. */
const e2e = process.argv.includes('--atlas-e2e')

function log(line: string): void {
  if (line) {
    process.stderr.write(`[atlas] ${line}\n`)
  }
}

const remote = remoteEndpoint(process.env)
/** This machine's servers, by home: `null` is the home that opens first, the last one the window
 * showed, or the one `atlas home use` chose. One the app started, or an always-on home's. */
const owned = new Map<null | string, AttachedServer | OwnedServer>()
/** The last home the window showed, in the app's own folder. */
const lastHomeFile = path.join(app.getPath('userData'), 'last-home.json')
/** The other machines' Atlas, and the connections to the ones opened. */
const machines = new Machines(app.getPath('userData'), {
  available: () => safeStorage.isEncryptionAvailable(),
  decrypt: sealed => safeStorage.decryptString(sealed),
  encrypt: text => safeStorage.encryptString(text)
})
const reached = new Map<string, RemoteMachine>()
const MACHINE = 'machine:'

/** The `atlas` command for a home, as the app's server runs it. Read each time, so a toolchain
 * the installer repaired meanwhile is seen. */
function command(home: null | string) {
  return repositoryCommand(appRoot, process.pid, home, readToolchain(repositoryRoot(appRoot)))
}

const cli = cliRunner(home => command(home), process.env)

/** The name of the home a key opens on this machine. */
function homeOf(key: null | string): string {
  const root = homesRoot(process.env)

  return key ?? lastHome(lastHomeFile, root) ?? (root ? activeHome(root) : 'default')
}

function server(home: null | string): AttachedServer | OwnedServer {
  let found = owned.get(home)

  if (!found) {
    const root = homesRoot(process.env)
    const name = homeOf(home)
    const folder = root ? homeFolder(root, name) : null
    found =
      folder && readAlwaysOn(folder)
        ? new AttachedServer(name, folder, cli, log)
        : new OwnedServer({ command: command(home ?? lastHome(lastHomeFile, root)), env: process.env, log })
    owned.set(home, found)
  }

  return found
}

/** Let go of a home's servers, so the next dial reaches the right one: the app's, or always on. */
function forgetHome(name: string): void {
  for (const [key, found] of owned) {
    if (homeOf(key) === name) {
      found.stop()
      owned.delete(key)
    }
  }
}

function remoteMachine(id: string): RemoteMachine {
  let found = reached.get(id)

  if (!found) {
    const machine = machines.find(id)

    if (!machine) {
      throw new MachineError('That machine is no longer in the list.')
    }

    found = new RemoteMachine(machine, () => machines.token(id), log)
    reached.set(id, found)
  }

  return found
}

function endpoint(key: null | string): Promise<Endpoint> {
  if (remote) {
    // A server reached by address serves its one home; the window offers no other.
    return key === null ? Promise.resolve(remote) : Promise.reject(new Error('A remote server serves one home.'))
  }

  if (key?.startsWith(MACHINE)) {
    try {
      return remoteMachine(key.slice(MACHINE.length)).endpoint()
    } catch (error) {
      return Promise.reject(error instanceof Error ? error : new Error(String(error)))
    }
  }

  return server(key).endpoint()
}

// -- the update (electron/updates.ts) -------------------------------------------------------------

/** Where an update keeps its marker, its result and its log: this app's own folder. */
const updates = updateFiles(app.getPath('userData'))
/** How long after it opens the app first looks: a look starts Python and fetches. */
const FIRST_LOOK_MS = 10_000
/** The tests look for an update only through their stand-in. */
const looksForUpdates = !e2e || Boolean(process.env.ATLAS_E2E_UPDATE)
let updateStatus: null | UpdateStatus = null
let updateCheckedAt = 0
let updateChecking: null | Promise<UpdateStatus> = null
/** How the last update ended, read as the app opens and given to the window once. */
let updateEnded: HandedOff | null = null

function updateRun(): UpdateRun {
  const root = repositoryRoot(appRoot)
  const stand = e2e ? process.env.ATLAS_E2E_UPDATE : undefined

  return {
    cwd: root,
    // The installation's own Git first, as for its server.
    env: withPath(process.env, toolchainFolders(readToolchain(root))),
    module: stand ? [stand] : ['-m', 'atlas.installation'],
    python: installationPython(root)
  }
}

/** Look for an update: the last look's answer while it is fresh, unless `now`. */
function lookForUpdate(now = false): Promise<null | UpdateStatus> {
  if (!looksForUpdates) {
    return Promise.resolve(null)
  }

  if (!now && updateStatus && Date.now() - updateCheckedAt < CHECK_EVERY_MS) {
    return Promise.resolve(updateStatus)
  }

  updateChecking ??= checkUpdate(updateRun())
    .then(status => {
      updateStatus = status
      updateCheckedAt = Date.now()
      window?.webContents.send('atlas:update:changed', status)

      if (status.error) {
        log(`The look for an update failed: ${status.error}`)
      }

      return status
    })
    .finally(() => {
      updateChecking = null
    })

  return updateChecking
}

let window: BrowserWindow | null = null

// No File, Edit, View bar on the window (asked for by the user on 17/10/2026): Windows and Linux
// get no application menu, set before Electron installs its default one.
// On macOS the menu is the system's bar, not the window's, and its Edit menu is what copies and
// pastes, so it stays.
if (process.platform !== 'darwin') {
  Menu.setApplicationMenu(null)
}

if (process.platform === 'win32') {
  app.setAppUserModelId(APP_ID)
}

/** Only the system browser opens links, and only web ones. */
function openOutside(url: string): void {
  try {
    const { protocol } = new URL(url)

    if (protocol === 'http:' || protocol === 'https:') {
      void shell.openExternal(url)
    }
  } catch {
    // Not a URL: nothing to open.
  }
}

function createWindow(): void {
  window = new BrowserWindow({
    height: 820,
    icon,
    minHeight: 480,
    minWidth: 720,
    show: false,
    title: 'Atlas',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(here, 'preload.cjs'),
      sandbox: true,
      webSecurity: true
    },
    width: 1240
  })

  if (process.platform === 'win32') {
    // The taskbar's icon however the app was opened, and a pin that opens the app, not Electron.
    window.setAppDetails({
      appIconIndex: 0,
      appIconPath: icon,
      appId: APP_ID,
      relaunchCommand: `"${process.execPath}" "${appRoot}"`,
      relaunchDisplayName: 'Atlas'
    })
  }

  window.webContents.setWindowOpenHandler(({ url }) => {
    openOutside(url)

    return { action: 'deny' }
  })
  // âW closes the tab on screen, not the window, which the menu's Close Window would (keys.ts).
  window.webContents.on('before-input-event', (event, input) => {
    if (closesTab(input, process.platform)) {
      event.preventDefault()
      window?.webContents.send('atlas:tabs:close')
    }
  })
  window.webContents.on('will-navigate', (event, url) => {
    if ((devServer && url.startsWith(devServer)) || (!devServer && url.startsWith('file:'))) {
      return
    }

    event.preventDefault()
    openOutside(url)
  })
  window.once('ready-to-show', () => window?.show())
  window.on('closed', () => {
    window = null
    // The bar is a window too: left open, the app would not quit.
    desktopBar?.close()
  })
  // Back after an hour away, the window looks for an update again.
  window.on('focus', () => {
    void lookForUpdate()
  })

  const loaded = devServer
    ? window.loadURL(devServer)
    : window.loadFile(path.join(appRoot, 'out', 'renderer', 'index.html'))
  loaded.catch((error: unknown) => {
    log(`The window failed to load: ${String(error)}`)
  })
}

const homeArgument = (value: unknown): null | string => (typeof value === 'string' && value ? value : null)

ipcMain.handle('atlas:connection', (_event, home: unknown) => endpoint(homeArgument(home)))
ipcMain.handle('atlas:release', (_event, home: unknown) => {
  const name = homeArgument(home)

  if (name?.startsWith(MACHINE)) {
    reached.get(name.slice(MACHINE.length))?.close()
    reached.delete(name.slice(MACHINE.length))

    return
  }

  const found = owned.get(name)

  if (found) {
    found.stop()
    owned.delete(name)
  }
})

// -- the machine list and this machine (step 15) ---------------------------------------------------

/** What the window gets back: the value, or why not in words. */
async function answer<T>(work: () => Promise<T> | T): Promise<{ error: string; ok: false } | { ok: true; value: T }> {
  try {
    return { ok: true, value: await work() }
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error), ok: false }
  }
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '')

ipcMain.handle('atlas:machines:list', () => machines.list())
ipcMain.handle('atlas:machines:add', (_event, given: unknown) =>
  answer(() => {
    const fields = (typeof given === 'object' && given !== null ? given : {}) as Record<string, unknown>
    const transport: Transport = fields.transport === 'ssh' ? 'ssh' : 'tailscale'

    return machines.add({ code: text(fields.code), name: text(fields.name), sshHost: text(fields.sshHost), transport })
  })
)
ipcMain.handle('atlas:machines:rename', (_event, id: unknown, name: unknown) =>
  answer(() => machines.rename(text(id), text(name)))
)
ipcMain.handle('atlas:machines:remove', (_event, id: unknown) =>
  answer(() => {
    reached.get(text(id))?.close()
    reached.delete(text(id))
    machines.remove(text(id))
  })
)
/** Test both legs of a machine on a connection of its own. */
ipcMain.handle('atlas:machines:test', (_event, id: unknown) =>
  answer(async () => {
    const machine: Machine | undefined = machines.find(text(id))

    if (!machine) {
      throw new MachineError('That machine is no longer in the list.')
    }

    const testing = new RemoteMachine(machine, () => machines.token(machine.id), log)

    try {
      await testing.endpoint()
    } finally {
      testing.close()
    }
  })
)

/** This machine's home: whether it is always on and open, and its tailnet. */
ipcMain.handle('atlas:this-machine:status', (_event, home: unknown) =>
  answer(async () => {
    const ran = await cli(homeOf(homeArgument(home)), ['always-on', 'status', '--json'])

    if (ran.code !== 0) {
      throw new Error(failure(ran))
    }

    return JSON.parse(ran.stdout.trim().split('\n').at(-1) ?? '{}') as unknown
  })
)
/** Turn this machine's home always on or off, open or closed. The app's own server for it goes
 * first (the service's takes the home), and the window's next dial reaches the right one. */
ipcMain.handle('atlas:this-machine:set', (_event, home: unknown, change: unknown) =>
  answer(async () => {
    const name = homeOf(homeArgument(home))
    const action = text(change)

    if (!['close', 'off', 'on', 'open'].includes(action)) {
      throw new Error(`Not a change: ${action}`)
    }

    if (action === 'on') {
      forgetHome(name)
    }

    const ran = await cli(name, ['always-on', action])
    forgetHome(name)

    if (ran.code !== 0) {
      throw new Error(failure(ran))
    }
  })
)
/** The home's connection code, put on the clipboard: the window never holds it. */
ipcMain.handle('atlas:this-machine:copy-code', (_event, home: unknown) =>
  answer(async () => {
    const ran = await cli(homeOf(homeArgument(home)), ['server', 'code'])

    if (ran.code !== 0) {
      throw new Error(failure(ran))
    }

    clipboard.writeText(ran.stdout.trim().split('\n').at(-1) ?? '')
  })
)
/** A new token for the home: every code given before stops working. */
ipcMain.handle('atlas:this-machine:new-token', (_event, home: unknown) =>
  answer(async () => {
    const name = homeOf(homeArgument(home))
    const ran = await cli(name, ['server', 'token', '--new'])
    forgetHome(name)

    if (ran.code !== 0) {
      throw new Error(failure(ran))
    }
  })
)
ipcMain.handle('atlas:remember', (_event, home: unknown) => {
  const name = homeArgument(home)

  // A remote server's home is not this machine's to open.
  if (name && !remote && !name.startsWith(MACHINE)) {
    try {
      rememberHome(lastHomeFile, name)
    } catch (error) {
      log(`The last home could not be kept: ${String(error)}`)
    }
  }
})
/** A site up on another machine, for the preview: through one more SSH tunnel, or why not (step 15). */
ipcMain.handle('atlas:preview:reach', async (_event, id: unknown, url: unknown) => {
  try {
    return typeof id === 'string' ? await remoteMachine(id).reach(text(url)) : { url: text(url) }
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error) }
  }
})
/** A remote machine's backups, coming down in parts to where the save dialog said (step 15). */
const downloads = new BackupDownloads()
ipcMain.handle('atlas:backup:write', (_event, file: unknown, offset: unknown, data: unknown) =>
  downloads.write(file, offset, data)
)
ipcMain.handle('atlas:backup:finish', (_event, file: unknown, whole: unknown) => downloads.finish(file, whole))
ipcMain.handle('atlas:backup-target', async (_event, home: unknown, label: unknown) => {
  const options = {
    defaultPath: path.join(os.homedir(), backupName(homeArgument(home) ?? 'default', new Date())),
    filters: [{ extensions: ['zip'], name: typeof label === 'string' ? label : 'Atlas backup' }]
  }
  const chosen = window ? await dialog.showSaveDialog(window, options) : await dialog.showSaveDialog(options)

  if (chosen.canceled || !chosen.filePath) {
    return null
  }

  downloads.allow(chosen.filePath)

  return chosen.filePath
})
ipcMain.handle('atlas:backup-source', async (_event, label: unknown) => {
  const options = {
    filters: [{ extensions: ['zip'], name: typeof label === 'string' ? label : 'Atlas backup' }],
    properties: ['openFile' as const]
  }
  const chosen = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options)

  return chosen.canceled ? null : (chosen.filePaths[0] ?? null)
})
ipcMain.handle('atlas:choose-folder', async () => {
  const options = { properties: ['openDirectory' as const] }
  const chosen = window ? await dialog.showOpenDialog(window, options) : await dialog.showOpenDialog(options)

  return chosen.canceled ? null : (chosen.filePaths[0] ?? null)
})
/** A session's folder in VS Code (`editor.ts`): only with the server on this machine, whose folders
 * are this machine's. */
ipcMain.handle('atlas:open-in-editor', (_event, folder: unknown) =>
  remote === null ? openInEditor(folder, link => shell.openExternal(link)) : 'The folder is on another machine.'
)
/** The previews' pages are served from a protocol of their own (step 16f), declared before the app
 * is ready, as Electron asks. */
protocol.registerSchemesAsPrivileged([{ privileges: { secure: true, standard: true }, scheme: PREVIEW_SCHEME }])
ipcMain.on('atlas:e2e', event => {
  event.returnValue = e2e
})
ipcMain.on('atlas:remote', event => {
  event.returnValue = remote !== null
})
/** Only the end-to-end tests shorten the terminal's wait for a command's end, to see one still
 * running; the terminal runs through the server (step 15), its marks read in the window. */
ipcMain.on('atlas:terminal-wait', event => {
  event.returnValue =
    e2e && process.env.ATLAS_E2E_TERMINAL_WAIT_MS ? Number(process.env.ATLAS_E2E_TERMINAL_WAIT_MS) : null
})

if (e2e) {
  // The server of the home that opened first, and every one by home.
  Object.defineProperty(globalThis, 'atlasServer', { get: () => owned.get(null) ?? null })
  Object.defineProperty(globalThis, 'atlasServers', { get: () => owned })
}
ipcMain.on('atlas:focus', () => {
  if (window) {
    if (window.isMinimized()) {
      window.restore()
    }

    window.show()
    window.focus()
  }
})
// The last look's answer; the looks themselves run on their own schedule (below) and say so.
ipcMain.handle('atlas:update:status', () => updateStatus)
ipcMain.handle('atlas:update:check', () => lookForUpdate(true))
ipcMain.handle('atlas:update:ended', () => {
  const found = updateEnded
  updateEnded = null

  return found
})
ipcMain.handle('atlas:update:log', () => shell.openPath(updates.log))
ipcMain.handle('atlas:update:apply', async () => {
  try {
    // It opens again as it was opened: its own flags go along (a profile apart, a language).
    await handOff(updateRun(), updates, process.pid, { given: process.argv.slice(1) })
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error), ok: false }
  }

  // The hand-off took and waits for this process: the app closes as the user closes it, its
  // terminals and servers stopped before it goes (`before-quit`).
  setTimeout(() => {
    app.quit()
  }, 200)

  return { error: '', ok: true }
})

// One app per machine: a second launch brings the first window forward.
if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', () => {
    window?.show()
    window?.focus()
  })
  app.on('before-quit', () => {
    for (const each of owned.values()) {
      each.stop()
    }

    for (const each of reached.values()) {
      each.close()
    }
  })
  app.on('window-all-closed', () => {
    app.quit()
  })
  void app.whenReady().then(() => {
    // Opened from the checkout, macOS shows Electron's own icon in the Dock until told otherwise.
    app.dock?.setIcon(icon)

    if (updateRunning(updates)) {
      // The update replaces what this app runs from: it starts no server, and waits it out.
      void waitOutUpdate()

      return
    }

    updateEnded = takeResult(updates)
    registerPreviews(ipcMain, protocol)
    startDesktopPowers()
    // Start the opening home's server while the window loads; the window's first dial waits for it.
    void endpoint(null).catch((error: unknown) => {
      log(String(error))
    })
    createWindow()
    // The first look waits for the server's own start, which it would slow; then every hour.
    setTimeout(
      () => {
        void lookForUpdate(true)
      },
      e2e ? 0 : FIRST_LOOK_MS
    )
    setInterval(() => {
      void lookForUpdate(true)
    }, CHECK_EVERY_MS)
  })
}

/** A plugin's screen's powers outside the window (step 17b): under the end-to-end tests, ones
 * that reach nothing, seen as `atlasDesktop`. */
let desktopBar: Bar | null = null

function startDesktopPowers(): void {
  let letGo: () => void

  if (e2e) {
    const { powers, probe } = e2ePowers(clipboard)
    Object.defineProperty(globalThis, 'atlasDesktop', { value: probe })
    letGo = registerDesktopPowers(
      ipcMain,
      () => window,
      powers,
      clipboard,
      () => probe.appFocused ?? window?.isFocused() ?? false
    )
  } else {
    const bar = new Bar({ BrowserWindow, screen })
    desktopBar = bar
    const done = registerDesktopPowers(
      ipcMain,
      () => window,
      { bar, keys: systemKeys(), shortcuts: globalShortcut },
      clipboard
    )
    letGo = () => {
      done()
      bar.close()
    }
  }

  app.on('will-quit', () => {
    letGo()
    globalShortcut.unregisterAll()
  })
}

/** An app opened while an update runs says so, and closes. When the update ends first, it opens
 * again on what the update brought; the update opens the app too, and one of the two stays. */
async function waitOutUpdate(): Promise<void> {
  const said = messagesFor(pickLocale([app.getLocale()])).updates
  const shown = dialog.showMessageBox({ buttons: [said.ok], message: said.runningNow, title: 'Atlas', type: 'info' })
  const ended = new Promise<void>(resolve => {
    const timer = setInterval(() => {
      if (!updateRunning(updates)) {
        clearInterval(timer)
        resolve()
      }
    }, 1_000)
  })

  if (await Promise.race([shown.then(() => false), ended.then(() => true)])) {
    app.relaunch()
  }

  app.exit(0)
}
