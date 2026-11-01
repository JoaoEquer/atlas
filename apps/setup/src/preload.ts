/** The window's only way to the main process: `window.setup`. */
import { contextBridge, ipcRenderer } from 'electron'

import { CHANNELS, type Ended, type SetupApi, type SetupState } from './bridge'
import type { Line } from './install'

const api: SetupApi = {
  close: () => {
    ipcRenderer.send(CHANNELS.close)
  },
  install: () => {
    ipcRenderer.send(CHANNELS.install)
  },
  onEnd: listener => {
    ipcRenderer.on(CHANNELS.end, (_event, ended: Ended) => {
      listener(ended)
    })
  },
  onLine: listener => {
    ipcRenderer.on(CHANNELS.line, (_event, line: Line) => {
      listener(line)
    })
  },
  open: () => {
    ipcRenderer.send(CHANNELS.open)
  },
  openLog: () => {
    ipcRenderer.send(CHANNELS.log)
  },
  state: () => ipcRenderer.invoke(CHANNELS.state) as Promise<SetupState>
}

contextBridge.exposeInMainWorld('setup', api)
