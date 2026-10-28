/**
 * What the window may ask of the main process: a home's server address and token, to let a home's
 * server go, to be brought forward, a backup's path from the system's dialogs, to keep the home on
 * screen for next time, a session's folder opened in VS Code, the app's update, and a plugin's
 * screen's powers outside the window (a shortcut, a paste, a bar). Nothing else
 * crosses: the bridge stays narrow. A sandboxed preload loads only Electron's own modules, so this
 * file imports nothing else.
 */
import { contextBridge, ipcRenderer } from 'electron'

// Asked once, synchronously, before the page runs.
const e2e = ipcRenderer.sendSync('atlas:e2e') === true
const remote = ipcRenderer.sendSync('atlas:remote') === true
const terminalWait = ipcRenderer.sendSync('atlas:terminal-wait') as null | number

contextBridge.exposeInMainWorld('atlas', {
  /** Where a home's backup goes, from the system's save dialog; `null` when the user cancelled.
   * `label` names the zip files in its filter. */
  backupTarget: (home: string, label: string): Promise<null | string> =>
    ipcRenderer.invoke('atlas:backup-target', home, label),
  /** A backup to import, from the system's open dialog; `null` when the user cancelled. */
  backupSource: (label: string): Promise<null | string> => ipcRenderer.invoke('atlas:backup-source', label),
  /** A part of a remote machine's backup, written where the save dialog said (step 15). */
  backupWrite: (file: string, offset: number, data: string): Promise<void> =>
    ipcRenderer.invoke('atlas:backup:write', file, offset, data),
  /** The last part came (`whole`), or the backup failed. */
  backupFinish: (file: string, whole: boolean): Promise<void> => ipcRenderer.invoke('atlas:backup:finish', file, whole),
  /** The window's tabs: on macOS ⌘W, which the main process takes before the menu would close
   * the window, closes the tab on screen (electron/keys.ts). */
  tabs: {
    onClose: (listener: () => void): (() => void) => {
      const handler = () => {
        listener()
      }
      ipcRenderer.on('atlas:tabs:close', handler)

      return () => {
        ipcRenderer.removeListener('atlas:tabs:close', handler)
      }
    }
  },
  /** A folder, from the system's open dialog: where a new executor session works (step 16f). */
  chooseFolder: (): Promise<null | string> => ipcRenderer.invoke('atlas:choose-folder'),
  /** A home's server, started if needed; `null` is the home that opens first. */
  connection: (home: null | string = null): Promise<{ token: string; url: string }> =>
    ipcRenderer.invoke('atlas:connection', home),
  /** What a plugin's screen does outside the window (step 17b): a shortcut heard in every program,
   * a text pasted into the program in focus, the floating bar. */
  desktop: {
    bar: (state: unknown): Promise<void> => ipcRenderer.invoke('atlas:desktop:bar', state),
    onPressed: (listener: (key: string) => void): (() => void) => {
      const handler = (_event: unknown, key: string) => {
        listener(key)
      }
      ipcRenderer.on('atlas:desktop:pressed', handler)

      return () => {
        ipcRenderer.removeListener('atlas:desktop:pressed', handler)
      }
    },
    paste: (text: string): Promise<string> => ipcRenderer.invoke('atlas:desktop:paste', text),
    shortcut: (key: string, accelerator: string): Promise<string> =>
      ipcRenderer.invoke('atlas:desktop:shortcut', key, accelerator)
  },
  e2e,
  /** Only under the end-to-end tests: how long a command runs before it is "still running". */
  terminalWait,
  focus: (): void => {
    ipcRenderer.send('atlas:focus')
  },
  /** The other machines' Atlas this app opens (step 15): their rows, never their tokens. */
  machines: {
    add: (given: unknown): Promise<unknown> => ipcRenderer.invoke('atlas:machines:add', given),
    list: (): Promise<unknown> => ipcRenderer.invoke('atlas:machines:list'),
    remove: (id: string): Promise<unknown> => ipcRenderer.invoke('atlas:machines:remove', id),
    rename: (id: string, name: string): Promise<unknown> => ipcRenderer.invoke('atlas:machines:rename', id, name),
    test: (id: string): Promise<unknown> => ipcRenderer.invoke('atlas:machines:test', id)
  },
  /** This machine's home always on and open (step 15); its code goes to the clipboard from the
   * main process, never through here. */
  thisMachine: {
    copyCode: (home: null | string): Promise<unknown> => ipcRenderer.invoke('atlas:this-machine:copy-code', home),
    newToken: (home: null | string): Promise<unknown> => ipcRenderer.invoke('atlas:this-machine:new-token', home),
    set: (home: null | string, change: string): Promise<unknown> =>
      ipcRenderer.invoke('atlas:this-machine:set', home, change),
    status: (home: null | string): Promise<unknown> => ipcRenderer.invoke('atlas:this-machine:status', home)
  },
  /** A session's folder opened in VS Code; `''` once handed over, else why not. */
  openInEditor: (folder: string): Promise<string> => ipcRenderer.invoke('atlas:open-in-editor', folder),
  /** Stop a home's server; the window lets one go when a fourth opens. */
  release: (home: null | string): Promise<void> => ipcRenderer.invoke('atlas:release', home),
  /** Open this home first next time (the last home). */
  remember: (home: string): Promise<void> => ipcRenderer.invoke('atlas:remember', home),
  /** A site up on another machine, as this one reaches it, or why not (step 15). */
  previewReach: (machine: null | string, url: string): Promise<unknown> =>
    ipcRenderer.invoke('atlas:preview:reach', machine, url),
  /** Serve a preview's page; its address, for the sandboxed frame. */
  previewPage: (html: string): Promise<string> => ipcRenderer.invoke('atlas:preview:host', html),
  /** The app talks to a server it did not start, which serves one home. */
  remote,
  /** The app's update: the last look, a look now, the hand-off, how the last one ended, its log. */
  updates: {
    apply: (): Promise<unknown> => ipcRenderer.invoke('atlas:update:apply'),
    check: (): Promise<unknown> => ipcRenderer.invoke('atlas:update:check'),
    ended: (): Promise<unknown> => ipcRenderer.invoke('atlas:update:ended'),
    onChange: (listener: (status: unknown) => void): (() => void) => {
      const handler = (_event: unknown, status: unknown) => {
        listener(status)
      }
      ipcRenderer.on('atlas:update:changed', handler)

      return () => {
        ipcRenderer.removeListener('atlas:update:changed', handler)
      }
    },
    openLog: (): Promise<string> => ipcRenderer.invoke('atlas:update:log'),
    status: (): Promise<unknown> => ipcRenderer.invoke('atlas:update:status')
  }
})
