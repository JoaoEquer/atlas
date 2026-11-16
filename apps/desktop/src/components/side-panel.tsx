import '@xterm/xterm/css/xterm.css'

import { useStore } from '@nanostores/react'
import { FitAddon } from '@xterm/addon-fit'
import { Unicode11Addon } from '@xterm/addon-unicode11'
import { Terminal } from '@xterm/xterm'
import { useEffect, useRef } from 'react'

import { t } from '@/i18n'
import { $contributions, inArea } from '@/plugins/registry'
import { ContributionView } from '@/plugins/view'
import { shiftShortcut } from '@/shortcuts'
import {
  $openExecutor,
  $openSession,
  $selected,
  $sidebarTab,
  $view,
  $workbench,
  currentFolder,
  currentOwner
} from '@/store/app'
import { $browserExpanded, BROWSER_TAB, browserHost, toggleBrowser } from '@/store/browser'
import { $panelWide } from '@/store/layout'
import { $previews, closePreview, type PreviewTab } from '@/store/previews'
import {
  $panelOpen,
  $panelTab,
  $selectedTerminal,
  $terminalAvailable,
  $terminals,
  closeTerminal,
  EXECUTOR_TAB,
  openTerminal,
  selectTerminal,
  terminalHost,
  type TerminalTab,
  togglePanel
} from '@/store/terminals'
import { workspaceAt } from '@/store/workspaces'

import { BrowserView } from './browser-view'
import { ChangesView } from './changes-view'
import { ExecutorPanel } from './executor-panel'
import { baseName } from './executors-list'
import { PreviewView } from './preview-view'
import { RailEdge } from './rail-edge'
import { Close, LayoutSidebarRight, Plus, Preview as PreviewGlyph, Terminal as TerminalGlyph, Web } from './ui/icons'
import { PILL, PILL_ON } from './ui/pill'
import { IconButton } from './ui/tip'
import { useWorkspaceRows } from './workspaces-list'

/** A plugin panel's tab in `$panelTab`: `plugin:` and its key. */
const PLUGIN_TAB = 'plugin:'
/** The Changes tab, for a session in a workspace (step 21, A2). */
export const CHANGES_TAB = 'changes'

/**
 * The side panel (step 16f, decided with the user on 15/10/2026): the
 * workbench's place on the right, now the terminals, where the ▶ and the `!` run, and the previews
 * of what is on screen, a tab each; and, while an executor is open in the sidebar, what it has
 * (asked for by the user on 16/10/2026). It opens when asked, when a command runs or when a preview
 * opens, and stays open or hidden from screen to screen; hidden, a button at the tab strip's end
 * brings it back, wherever it has something to offer (asked for by the user on 17/10/2026, on the
 * strip since the window lost its title bar on 01/11/2026).
 * It opens as wide as the sidebar, and its edge toward the conversation drags it wider or narrower
 * (store/layout.ts).
 */
export function SidePanel() {
  const open = useStore($panelOpen)
  const tabs = useStore($terminals)
  const terminal = useStore($selectedTerminal)
  const panelTab = useStore($panelTab)
  const available = useStore($terminalAvailable)
  // Read so the panel follows the conversation or session on screen.
  useStore($selected)
  useStore($view)
  useStore($openSession)
  const owner = currentOwner()
  const previews = useStore($previews).filter(tab => tab.owner === owner)
  const executor = useOpenExecutor()
  const workspace = useSessionWorkspace()
  const showsChanges = workspace !== undefined && panelTab === CHANGES_TAB
  const showsExecutor = !showsChanges && executor !== undefined && panelTab === EXECUTOR_TAB
  // The plugins' panels (step 16m), a tab each.
  const panels = inArea(useStore($contributions), 'panel')
  const panel = showsExecutor || showsChanges ? undefined : panels.find(item => `${PLUGIN_TAB}${item.key}` === panelTab)
  const preview = showsExecutor || showsChanges || panel ? undefined : previews.find(tab => tab.id === panelTab)
  // The app's browser (electron/app-browser.ts): a tab of its own, which none of the others shows with.
  const expanded = useStore($browserExpanded)
  const showsBrowser = !showsExecutor && !showsChanges && panelTab === BROWSER_TAB && browserHost() !== undefined
  // On the Browser tab the panel may drag past the rails' bound (store/layout.ts).
  const wide = open && showsBrowser
  useEffect(() => {
    $panelWide.set(wide)
  }, [wide])
  const selected =
    showsExecutor || showsChanges || showsBrowser || preview || panel
      ? null
      : tabs.some(tab => tab.id === panelTab)
        ? panelTab
        : terminal

  if (!open) {
    return null
  }

  return (
    <aside
      aria-label={t.terminal.panel}
      className="side-panel-open flex shrink-0 flex-col border-l border-border bg-subtle"
      data-testid="side-panel"
    >
      {!expanded && <RailEdge rail="panel" />}
      <div
        className="title-bar title-bar-end flex h-9 shrink-0 items-center gap-1 border-b border-border px-2 text-xs"
        role="tablist"
      >
        {executor && (
          <button
            aria-selected={showsExecutor}
            className={`truncate rounded px-2 py-1 ${showsExecutor ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            data-testid="executor-tab"
            onClick={() => {
              $panelTab.set(EXECUTOR_TAB)
            }}
            role="tab"
            title={executor.title}
            type="button"
          >
            {executor.title}
          </button>
        )}
        {workspace && (
          <button
            aria-selected={showsChanges}
            className={`truncate rounded px-2 py-1 ${showsChanges ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            data-testid="changes-tab"
            onClick={() => {
              $panelTab.set(CHANGES_TAB)
            }}
            role="tab"
            title={workspace.branch}
            type="button"
          >
            {t.changes.title}
          </button>
        )}
        {tabs.map(tab => (
          <div
            aria-selected={tab.id === selected}
            className={`flex items-center gap-1 rounded px-2 py-1 ${
              tab.id === selected ? 'bg-background shadow-sm' : 'text-muted-foreground'
            }`}
            data-testid="terminal-tab"
            key={tab.id}
            role="tab"
          >
            <button
              onClick={() => {
                selectTerminal(tab.id)
              }}
              title={tab.folder}
              type="button"
            >
              {tab.shell} · {baseName(tab.folder)}
            </button>
            <button
              aria-label={t.terminal.close}
              className="text-muted-foreground hover:text-soft"
              onClick={() => {
                closeTerminal(tab.id)
              }}
              type="button"
            >
              <Close aria-hidden size={12} stroke={2} />
            </button>
          </div>
        ))}
        {previews.map(tab => (
          <PreviewTabButton current={tab.id === preview?.id} key={tab.id} tab={tab} />
        ))}
        {panels.map(item => (
          <button
            aria-selected={item === panel}
            className={`truncate rounded px-2 py-1 ${item === panel ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            data-testid={`plugin-panel-tab-${item.key}`}
            key={item.key}
            onClick={() => {
              $panelTab.set(`${PLUGIN_TAB}${item.key}`)
            }}
            role="tab"
            title={item.title}
            type="button"
          >
            {item.title}
          </button>
        ))}
        {browserHost() && (
          <button
            aria-selected={showsBrowser}
            className={`flex items-center gap-1 rounded px-2 py-1 ${showsBrowser ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            data-testid="browser-tab"
            onClick={() => {
              $panelTab.set(BROWSER_TAB)
            }}
            role="tab"
            type="button"
          >
            <Web aria-hidden className="shrink-0" size={13} stroke={1.75} />
            {t.browser.name}
          </button>
        )}
        {available && (
          <IconButton
            icon={Plus}
            label={t.terminal.add}
            onClick={() => void openTerminal(currentFolder())}
            testId="terminal-add"
          />
        )}
        <IconButton
          className="ml-auto"
          icon={LayoutSidebarRight}
          label={t.layout.hidePanel}
          onClick={() => {
            togglePanel(false)
          }}
          side="left"
          testId="panel-hide"
        />
      </div>
      <div
        className={`relative min-h-0 flex-1 ${showsExecutor || panel ? 'overflow-y-auto' : showsChanges ? '' : showsBrowser ? 'overflow-hidden' : 'dark bg-background p-2'}`}
      >
        {tabs.map(tab => (
          <TerminalView active={tab.id === selected} key={tab.id} tab={tab} />
        ))}
        {showsExecutor && <ExecutorPanel executor={executor} />}
        {showsChanges && <ChangesView workspace={workspace} />}
        {panel && (
          <div className="h-full" data-testid="plugin-panel">
            <ContributionView item={panel} />
          </div>
        )}
        {tabs.length === 0 && !preview && !showsExecutor && !showsChanges && !showsBrowser && !panel && (
          <p className="p-2 text-xs text-muted-foreground">{t.terminal.empty}</p>
        )}
        {showsBrowser && <BrowserView />}
        {preview && (
          <div className="absolute inset-0" data-testid="preview-view">
            <PreviewView target={preview.target} />
          </div>
        )}
      </div>
    </aside>
  )
}

/** The executor open in the sidebar's executors tab, whose tab the panel has. */
function useOpenExecutor() {
  const open = useStore($openExecutor)
  const sidebarTab = useStore($sidebarTab)
  const { executors } = useStore($workbench)

  return sidebarTab === 'executors' ? executors.find(row => row.name === open && row.enabled) : undefined
}

/** The workspace the session on screen works in, whose changes the panel has a tab for. */
function useSessionWorkspace() {
  const view = useStore($view)
  const open = useStore($openSession)
  const rows = useWorkspaceRows()

  return view === 'session' && open ? workspaceAt(rows, open.folder) : undefined
}

/** The panel open, hidden with something to offer, or with nothing to offer. */
export function usePanelState(): 'hidden' | 'offered' | 'open' {
  const open = useStore($panelOpen)
  const tabs = useStore($terminals)
  const available = useStore($terminalAvailable)
  useStore($selected)
  useStore($view)
  useStore($openSession)
  const owner = currentOwner()
  const previews = useStore($previews).filter(tab => tab.owner === owner)
  const executor = useOpenExecutor()
  const workspace = useSessionWorkspace()
  const panels = inArea(useStore($contributions), 'panel')

  if (open) {
    return 'open'
  }

  return available || tabs.length + previews.length + panels.length > 0 || executor || workspace || browserHost()
    ? 'offered'
    : 'hidden'
}

/** The hidden panel's switch, at the tab strip's end, which brings it back. */
export function PanelShow() {
  return (
    <div className="mx-1.5 my-auto flex items-center gap-1">
      {browserHost() && (
        <IconButton
          icon={Web}
          label={t.browser.show(shiftShortcut('B'))}
          onClick={toggleBrowser}
          side="bottom"
          testId="browser-show"
        />
      )}
      <IconButton
        icon={LayoutSidebarRight}
        label={t.layout.showPanel}
        onClick={() => {
          togglePanel(true)
        }}
        side="bottom"
        testId="panel-show"
      />
    </div>
  )
}

function PreviewTabButton({ current, tab }: { current: boolean; tab: PreviewTab }) {
  return (
    <div
      aria-selected={current}
      className={`flex max-w-48 items-center gap-1 rounded px-2 py-1 ${
        current ? 'bg-background shadow-sm' : 'text-muted-foreground'
      }`}
      data-testid="preview-tab"
      role="tab"
    >
      <button
        className="flex min-w-0 items-center gap-1"
        onClick={() => {
          $panelTab.set(tab.id)
        }}
        title={tab.target.path ?? tab.target.url ?? tab.target.label}
        type="button"
      >
        <PreviewGlyph aria-hidden className="shrink-0" size={13} stroke={1.75} />
        <span className="truncate">{tab.target.label}</span>
      </button>
      <button
        aria-label={t.preview.close}
        className="text-muted-foreground hover:text-soft"
        onClick={() => {
          closePreview(tab.owner, { id: tab.id })
        }}
        type="button"
      >
        <Close aria-hidden size={12} stroke={2} />
      </button>
    </div>
  )
}

/**
 * One terminal: xterm.js with Unicode 11 widths,
 * fitted to its box as it changes, the shell's output written to it and the keys typed sent to
 * the shell. A hidden tab stays mounted, so its shell keeps its screen.
 */
function TerminalView({ active, tab }: { active: boolean; tab: TerminalTab }) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const box = host.current
    const bridge = terminalHost()

    if (!box || !bridge) {
      return
    }

    const term = new Terminal({
      allowProposedApi: true,
      convertEol: true,
      cursorBlink: true,
      fontFamily: 'ui-monospace, "Cascadia Mono", Consolas, monospace',
      fontSize: 12,
      scrollback: 2000,
      theme: { background: '#0a0a0a' }
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.loadAddon(new Unicode11Addon())
    term.unicode.activeVersion = '11'
    term.open(box)
    let frame = 0

    const resize = () => {
      if (box.clientWidth <= 0 || box.clientHeight <= 0) {
        return
      }

      try {
        fit.fit()
      } catch {
        return
      }

      void bridge.resize(tab.id, term.cols, term.rows)
    }

    // A hidden tab has no size: it is fitted when shown.
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(resize)
    })
    observer.observe(box)
    const stopData = bridge.onData((id, text) => {
      if (id === tab.id) {
        term.write(text)
      }
    })
    const typed = term.onData(data => {
      void bridge.write(tab.id, data)
    })
    void bridge.attach(tab.id)

    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
      stopData()
      typed.dispose()
      term.dispose()
    }
  }, [tab.id])

  return (
    <div
      className={active ? 'absolute inset-2' : 'hidden'}
      data-terminal={tab.id}
      data-testid="terminal-view"
      ref={host}
    />
  )
}

/** The panel's switch, beside the box: it opens a terminal in the folder on screen the first time. */
export function TerminalButton() {
  const available = useStore($terminalAvailable)
  const open = useStore($panelOpen)
  const tabs = useStore($terminals)

  if (!available) {
    return null
  }

  return (
    <button
      aria-pressed={open}
      className={`${PILL} ${open ? PILL_ON : ''}`}
      data-testid="terminal-button"
      onClick={() => {
        if (tabs.length === 0) {
          void openTerminal(currentFolder())
        } else {
          togglePanel()
        }
      }}
      title={t.terminal.toggle}
      type="button"
    >
      <TerminalGlyph aria-hidden size={14} stroke={1.75} />
      {t.terminal.name}
    </button>
  )
}
