/**
 * The Setup (docs/ARCHITECTURE.md, The Setup): one window that runs the installer it carries and
 * shows each step, or opens, repairs and updates an Atlas already installed. `--auto` installs with
 * no window and exits with the installer's code, for CI. Every line goes to `setup.log` in the
 * system's logs folder for the Setup.
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { app, BrowserWindow, ipcMain, Menu, shell } from 'electron'

import { CHANNELS, type SetupState } from './bridge'
import { installation, installerCommand, installerEnvironment, installerFile } from './install'
import { runInstaller, type Running } from './run'
import { languageOf } from './words'

// An AppImage cannot carry Chromium's setuid helper, and Ubuntu 24.04 refuses the unprivileged
// sandbox; the window loads only its own page.
if (process.env.APPIMAGE) {
  app.commandLine.appendSwitch('no-sandbox')
}

const AUTO = process.argv.includes('--auto')
const where = installation(process.env, process.platform, os.homedir())
const environment = installerEnvironment(process.env)
let running: null | Running = null

function logFile(): string {
  return path.join(app.getPath('logs'), 'setup.log')
}

/** The installer the Setup carries; in a checkout, the repository's own. */
function installerScript(): string {
  const file = installerFile(process.platform)

  return app.isPackaged
    ? path.join(process.resourcesPath, file)
    : path.join(app.getAppPath(), '..', '..', 'scripts', file)
}

function startInstaller(onLine: Parameters<typeof runInstaller>[0]['onLine']): Running {
  running = runInstaller({
    command: installerCommand(installerScript(), process.platform, process.env),
    env: environment,
    log: logFile(),
    onLine
  })
  void running.ended.finally(() => {
    running = null
  })

  return running
}

/** `atlas desktop` from the installation, on its own: it builds the app when it must and opens it. */
function openAtlas(): void {
  const child = spawn(where.atlas, ['desktop'], {
    detached: true,
    env: environment,
    stdio: 'ignore',
    windowsHide: true
  })
  child.unref()
}

function createWindow(): void {
  const window = new BrowserWindow({
    autoHideMenuBar: true,
    backgroundColor: '#101114',
    height: 560,
    minHeight: 440,
    minWidth: 480,
    show: false,
    title: 'Atlas Setup',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs'),
      sandbox: true
    },
    width: 600
  })

  window.once('ready-to-show', () => {
    window.show()
  })
  window.webContents.on('will-navigate', event => {
    event.preventDefault()
  })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  void window.loadFile(path.join(__dirname, 'index.html'))
}

ipcMain.handle(CHANNELS.state, (): SetupState => ({
  installed: existsSync(where.atlas),
  language: languageOf(app.getPreferredSystemLanguages()),
  root: where.root
}))

ipcMain.on(CHANNELS.install, event => {
  if (running) {
    return
  }

  const sender = event.sender
  const run = startInstaller(line => {
    if (!sender.isDestroyed()) {
      sender.send(CHANNELS.line, line)
    }
  })
  void run.ended.then(ended => {
    if (!sender.isDestroyed()) {
      sender.send(CHANNELS.end, ended)
    }
  })
})

ipcMain.on(CHANNELS.open, () => {
  openAtlas()
  // Time for the child to leave before the Setup does.
  setTimeout(() => {
    app.quit()
  }, 1500)
})

ipcMain.on(CHANNELS.log, () => {
  void shell.openPath(logFile())
})

ipcMain.on(CHANNELS.close, () => {
  app.quit()
})

if (!app.requestSingleInstanceLock()) {
  app.exit(0)
} else {
  app.on('second-instance', () => {
    const window = BrowserWindow.getAllWindows()[0]

    if (window) {
      if (window.isMinimized()) {
        window.restore()
      }

      window.focus()
    }
  })

  app.on('window-all-closed', () => {
    // A window closed halfway stops the installer; running it again repairs what it left.
    running?.stop()
    app.quit()
  })

  void app.whenReady().then(async () => {
    if (AUTO) {
      const ended = await startInstaller(line => {
        process.stdout.write(`${line.text}\n`)
      }).ended
      app.exit(ended.code === 0 ? 0 : 1)

      return
    }

    if (process.platform !== 'darwin') {
      Menu.setApplicationMenu(null)
    }

    createWindow()
  })
}
