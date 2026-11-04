import './styles.css'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import { App } from './components/app'
import { locale, t } from './i18n'
import { startScreens } from './plugins/runtime'
import { AtlasClient } from './protocol/client'
import { $session, current, startApp } from './store/app'
import { startDictationAnywhere } from './store/dictation'
import { startMachines } from './store/machines'
import { setTerminals } from './store/terminals'
import { startTheme } from './store/theme'
import { startUpdates } from './store/updates'
import { ConnectionTerminals } from './terminal/connection'
import { TIMING } from './terminal/terminal'

document.documentElement.lang = locale
startTheme()
startUpdates()
startMachines()
// The side panel's shells are the server's, over the connection of the home on screen (step 15).
setTerminals(
  new ConnectionTerminals(
    () => $session.get()?.client ?? null,
    window.atlas.terminalWait === null ? TIMING : { ...TIMING, endMs: window.atlas.terminalWait }
  )
)

startApp({
  client: home => new AtlasClient({ endpoint: () => window.atlas.connection(home) }),
  release: home => window.atlas.release(home),
  remote: window.atlas.remote,
  shell: {
    backupSource: () => window.atlas.backupSource(t.homes.zipFilter),
    backupTarget: home => window.atlas.backupTarget(home, t.homes.zipFilter),
    backupWrite: (file, offset, data) => window.atlas.backupWrite(file, offset, data),
    backupFinish: (file, whole) => window.atlas.backupFinish(file, whole),
    // A folder of this machine is one of the server's only when the app started that server.
    chooseFolder: window.atlas.remote ? undefined : () => window.atlas.chooseFolder(),
    focus: () => {
      window.atlas.focus()
    },
    notify: (title, body, onClick) => {
      const notification = new Notification(title, { body })
      notification.onclick = onClick
    },
    remember: home => {
      void window.atlas.remember(home).catch(() => undefined)
    }
  }
})

// The plugins' screens of the home on screen (step 16m).
startScreens()
startDictationAnywhere()

if (window.atlas.e2e) {
  window.atlasE2e = {
    reconnect: () => {
      current().client.reconnect()
    }
  }
}

const root = document.getElementById('root')

if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>
  )
}
