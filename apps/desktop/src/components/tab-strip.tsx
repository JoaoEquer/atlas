import { useStore } from '@nanostores/react'
import { type DragEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import {
  $bots,
  $cards,
  $connection,
  $openSession,
  $read,
  $remote,
  $selected,
  $tabs,
  $view,
  activeTab,
  closeTab,
  keepTab,
  moveTab,
  openWaiting,
  showStart,
  showTab
} from '@/store/app'
import { away, named, samePlace, screenPlace, type Tab } from '@/store/tabs'
import { $terminals, openTerminal, terminalAvailable, togglePanel } from '@/store/terminals'

import { PanelShow, usePanelState } from './side-panel'
import { rowDot, StatusDot, useConversationDot, useSessionDot } from './status-dot'
import { Close, Folder, Plus, Robot, Terminal, VsCode } from './ui/icons'
import { IconButton } from './ui/tip'

/** What a dragged tab carries, so a drop takes only a tab. */
const DRAGGED = 'application/x-atlas-tab'

/**
 * The tabs above the central area (store/tabs.ts): each conversation, bot and session open in this
 * home, with its state's dot and its title, in italic for the passing tab and in bold while what
 * it ended with is unseen; what is not made yet at the end, the start screen a new tab opens on
 * included; and, on the right, how many cards wait
 * for the user away from the screen and the hidden side panel's switch. Where the window has no
 * title bar (Windows), the strip is its top and moves it (styles.css). Ctrl+1 to 8 show a tab and Ctrl+9 the last, Ctrl+Tab the next,
 * Ctrl+W closes the one on screen (in the terminal they stay its shell's); a tab is dragged to
 * another place, and a middle click closes it.
 */
export function TabStrip() {
  const tabs = useStore($tabs)
  const view = useStore($view)
  const selected = useStore($selected)
  const open = useStore($openSession)
  const cards = useStore($cards)
  const { state } = useStore($connection)
  const read = useStore($read)
  const place = screenPlace(view, selected, open)
  const active = place ? (tabs.find(tab => samePlace(tab.place, place)) ?? null) : null
  // What the screen shows that is not made yet, a new conversation or a new session, once the home
  // answered: while Atlas opens, the tabs it kept are still on their way.
  const opening = state !== 'open' && !read.conversations
  const draft =
    !opening &&
    (view === 'start' ||
      (view === 'conversation' ? selected === null : view === 'session' && open !== null && !named(open)))
  const waiting = away(cards, view, selected, open)
  const list = useRef<HTMLDivElement>(null)
  const [dropAt, setDropAt] = useState<null | number>(null)
  const panel = usePanelState()

  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (
        !(event.ctrlKey || event.metaKey) ||
        event.altKey ||
        (event.target instanceof Element && event.target.closest('.xterm'))
      ) {
        return
      }

      const all = $tabs.get()
      const shown = activeTab()
      const digit = /^(?:Digit|Numpad)([1-9])$/.exec(event.code)?.[1]

      if (digit !== undefined && !event.shiftKey) {
        const tab = digit === '9' ? all.at(-1) : all[Number(digit) - 1]

        if (tab) {
          event.preventDefault()
          void showTab(tab.id)
        }
      } else if (event.key === 'Tab' && all.length > 0) {
        event.preventDefault()
        const at = shown ? all.indexOf(shown) : event.shiftKey ? 0 : -1
        const next = all[(at + (event.shiftKey ? -1 : 1) + all.length) % all.length]

        if (next) {
          void showTab(next.id)
        }
      } else if (event.key.toLowerCase() === 'w' && !event.shiftKey && shown) {
        event.preventDefault()
        closeTab(shown.id)
      }
    }
    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  // On macOS ⌘W comes from the main process, which takes it before the menu would close the
  // window (electron/keys.ts): the tab on screen closes, else the window, as a browser's does.
  useEffect(() => {
    const bridge = (window as { atlas?: AtlasBridge }).atlas

    return bridge?.tabs?.onClose(() => {
      const shown = activeTab()

      if (shown) {
        closeTab(shown.id)
      } else {
        window.close()
      }
    })
  }, [])

  // The tab on screen is never out of sight, however many are open.
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [active?.id])

  const drop = (event: DragEvent<HTMLElement>) => {
    const id = event.dataTransfer.getData(DRAGGED)

    if (id && dropAt !== null) {
      event.preventDefault()
      const from = tabs.findIndex(tab => tab.id === id)
      moveTab(id, from >= 0 && from < dropAt ? dropAt - 1 : dropAt)
    }

    setDropAt(null)
  }

  return (
    <div
      className={`title-bar flex h-9 shrink-0 items-stretch border-b border-border bg-subtle ${
        panel === 'open' ? '' : 'title-bar-end'
      }`}
      data-testid="tab-strip"
    >
      <div
        aria-label={t.tabs.label}
        className="flex min-w-0 flex-1 items-stretch overflow-x-auto [scrollbar-width:none]"
        onDragLeave={event => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setDropAt(null)
          }
        }}
        onDrop={drop}
        onWheel={event => {
          // A mouse's wheel scrolls the tabs sideways.
          if (Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
            event.currentTarget.scrollLeft += event.deltaY
          }
        }}
        ref={list}
        role="tablist"
      >
        {tabs.map((tab, index) => (
          <TabView
            active={tab === active}
            dropAt={dropAt === index ? 'before' : dropAt === index + 1 && index === tabs.length - 1 ? 'after' : null}
            focusable={tab === active || (active === null && index === 0)}
            key={tab.id}
            onDragOver={event => {
              if (event.dataTransfer.types.includes(DRAGGED)) {
                event.preventDefault()
                const { left, width } = event.currentTarget.getBoundingClientRect()
                setDropAt(event.clientX < left + width / 2 ? index : index + 1)
              }
            }}
            tab={tab}
          />
        ))}
        {draft && (
          <div
            aria-selected
            className="relative flex max-w-56 min-w-0 shrink-0 items-center border-r border-border bg-background px-3 text-sm text-foreground italic after:absolute after:inset-x-0 after:-bottom-px after:h-px after:bg-background"
            data-testid="tab-draft"
            role="tab"
            tabIndex={-1}
          >
            <span className="truncate">
              {view === 'start' ? t.tabs.new : view === 'session' ? t.executors.newSession : t.conversations.new}
            </span>
          </div>
        )}
        <button
          aria-label={t.tabs.new}
          className="grid w-9 shrink-0 place-items-center text-muted-foreground hover:bg-hover hover:text-foreground"
          data-testid="tab-new"
          onClick={showStart}
          title={t.tabs.new}
          type="button"
        >
          <Plus aria-hidden size={16} stroke={1.75} />
        </button>
      </div>
      {waiting.length > 0 && (
        <button
          className="mx-2 my-auto flex shrink-0 items-center gap-1.5 rounded-full bg-waiting/15 px-2.5 py-0.5 text-xs font-medium text-waiting-text hover:bg-waiting/25"
          data-testid="tabs-waiting"
          onClick={() => void openWaiting()}
          type="button"
        >
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-waiting" />
          {t.tabs.waiting(waiting.length)}
        </button>
      )}
      <TitleActions />
      {panel === 'offered' && <PanelShow />}
    </div>
  )
}

type TabViewProps = {
  active: boolean
  /** Where a dragged tab would land, drawn as a line: before this one, or after the last. */
  dropAt: 'after' | 'before' | null
  /** The one tab the keyboard reaches with Tab; the arrows move along the others. */
  focusable: boolean
  onDragOver: (event: DragEvent<HTMLElement>) => void
  tab: Tab
}

function TabView({ active, dropAt, focusable, onDragOver, tab }: TabViewProps) {
  const title = useTitle(tab)
  const dot = useTabDot(tab)

  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const tabs = [
      ...(event.currentTarget.closest('[role="tablist"]')?.querySelectorAll<HTMLElement>('[data-tab]') ?? [])
    ]
    const at = tabs.indexOf(event.currentTarget)
    const to =
      event.key === 'ArrowRight'
        ? tabs[at + 1]
        : event.key === 'ArrowLeft'
          ? tabs[at - 1]
          : event.key === 'Home'
            ? tabs[0]
            : event.key === 'End'
              ? tabs.at(-1)
              : undefined

    if (to) {
      event.preventDefault()
      to.focus()
    } else if (event.key === 'Delete') {
      event.preventDefault()
      closeTab(tab.id)
    }
  }

  return (
    <div
      className={`group relative flex max-w-56 min-w-28 shrink-0 items-center border-r border-border text-sm ${
        active
          ? 'bg-background text-foreground after:absolute after:inset-x-0 after:-bottom-px after:h-px after:bg-background'
          : 'text-muted-foreground hover:bg-hover hover:text-foreground'
      }`}
      data-active={active || undefined}
      data-preview={tab.preview || undefined}
      data-testid="tab"
      draggable
      onAuxClick={event => {
        if (event.button === 1) {
          event.preventDefault()
          closeTab(tab.id)
        }
      }}
      onDragOver={onDragOver}
      onDragStart={event => {
        event.dataTransfer.setData(DRAGGED, tab.id)
        event.dataTransfer.effectAllowed = 'move'
      }}
      onMouseDown={event => {
        // A middle click closes, it does not scroll.
        if (event.button === 1) {
          event.preventDefault()
        }
      }}
    >
      {dropAt && (
        <span
          aria-hidden
          className={`absolute inset-y-1 w-0.5 rounded-full bg-foreground ${dropAt === 'before' ? '-left-px' : '-right-px'}`}
        />
      )}
      <button
        aria-selected={active}
        className="flex min-w-0 flex-1 items-center gap-1.5 self-stretch pl-3 text-left"
        data-tab=""
        onClick={() => {
          if (!active) {
            void showTab(tab.id)
          }
        }}
        onDoubleClick={() => {
          keepTab(tab.id)
        }}
        onKeyDown={onKeyDown}
        role="tab"
        tabIndex={focusable ? 0 : -1}
        title={title}
        type="button"
      >
        <StatusDot dot={rowDot(dot)} />
        {tab.place.kind === 'session' && <Terminal aria-hidden className="shrink-0" size={14} stroke={1.75} />}
        {tab.place.kind === 'conversation' && <BotMark conversationId={tab.place.conversationId} />}
        <span
          className={`min-w-0 truncate ${tab.preview ? 'italic' : ''} ${dot === 'unread' ? 'font-semibold' : ''}`}
          data-testid="tab-title"
        >
          {title}
        </span>
      </button>
      <button
        aria-label={t.tabs.close(title)}
        className={`mx-1 grid h-5 w-5 shrink-0 place-items-center rounded hover:bg-selected ${
          active ? '' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
        }`}
        data-testid="tab-close"
        onClick={() => {
          closeTab(tab.id)
        }}
        tabIndex={-1}
        title={t.tabs.close(title)}
        type="button"
      >
        <Close aria-hidden size={14} stroke={1.75} />
      </button>
    </div>
  )
}

/** A bot's conversation says it is the bot's. */
function BotMark({ conversationId }: { conversationId: string }) {
  const bot = useStore($bots).some(row => row.conversation_id === conversationId)

  return bot ? <Robot aria-hidden className="shrink-0" size={14} stroke={1.75} /> : null
}

/** A tab's title: the bot's, the list's, or the one it kept. */
function useTitle(tab: Tab): string {
  const bots = useStore($bots)

  if (tab.place.kind === 'session') {
    return tab.place.session.title || tab.title || t.executors.untitled
  }

  const { conversationId } = tab.place

  return bots.find(row => row.conversation_id === conversationId)?.title ?? (tab.title || t.conversations.untitled)
}

/** A tab's dot: its conversation's, or its session's instance's. */
function useTabDot(tab: Tab) {
  const { place } = tab
  const conversation = useConversationDot(place.kind === 'conversation' ? place.conversationId : null)
  const session = useSessionDot(
    place.kind === 'session'
      ? { instance_id: place.session.instanceId, session_id: place.session.sessionId }
      : { instance_id: '', session_id: '' }
  )

  return place.kind === 'conversation' ? conversation : session
}

/**
 * The session on screen's folder from the title bar (step 18g, asked for by the user on
 * 01/11/2026): in the system's file manager, in VS Code, and in a terminal, which shows or hides
 * the terminals once one is open, as Ctrl+` does. Only a folder of this machine: one of another
 * machine's is not here to open. What went wrong is said where the button's name is.
 */
function TitleActions() {
  const view = useStore($view)
  const open = useStore($openSession)
  const remote = useStore($remote)
  const [failed, setFailed] = useState('')

  if (view !== 'session' || !open?.folder || remote) {
    return null
  }

  const folder = open.folder
  const said = (done: string) => {
    setFailed(done)
  }

  return (
    <div className="flex shrink-0 items-center gap-0.5 px-1.5" data-testid="title-actions">
      <IconButton
        icon={Folder}
        label={failed || t.tabs.openFolder}
        onClick={() => void window.atlas.openFolder(folder).then(said)}
        side="bottom"
        testId="title-open-folder"
      />
      <IconButton
        icon={VsCode}
        label={failed || t.executors.openInEditor}
        onClick={() => void window.atlas.openInEditor(folder).then(said)}
        side="bottom"
        testId="title-open-editor"
      />
      <IconButton
        icon={Terminal}
        label={t.tabs.terminal}
        onClick={() => {
          if ($terminals.get().length === 0) {
            void terminalAvailable().then(found => (found ? openTerminal(folder) : undefined))
          } else {
            togglePanel()
          }
        }}
        side="bottom"
        testId="title-terminal"
      />
    </div>
  )
}
