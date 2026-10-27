import { useStore } from '@nanostores/react'
import type { Icon } from '@tabler/icons-react'
import { type ReactNode, useEffect, useRef } from 'react'

import { t } from '@/i18n'
import { $contributions, $pluginPage, inArea } from '@/plugins/registry'
import { host } from '@/plugins/sdk'
import { shortcut } from '@/shortcuts'
import {
  $bots,
  $cards,
  $conversations,
  $listMore,
  $pairings,
  $proposals,
  $read,
  $readError,
  $search,
  $selected,
  $sidebarTab,
  $used,
  $view,
  $workbench,
  moreConversations,
  newConversation,
  showMarketplace,
  showProposals,
  showRoutines,
  showSidebarTab,
  type SidebarTab
} from '@/store/app'
import { $sidebarOpen, toggleSidebar } from '@/store/layout'
import { openSettings } from '@/store/settings'
import { byLastUse, type Dot, lastUse, worst } from '@/store/status'

import { BotsList } from './bots-list'
import { Conversation } from './conversation-row'
import { ExecutorsList } from './executors-list'
import { HomeBar } from './home-bar'
import { SearchField, SearchResults } from './list-search'
import { RailEdge } from './rail-edge'
import { ReadFailed } from './read-failed'
import { StatusDot, useConversationsDot, useInstancesDot } from './status-dot'
import { Bulb, CalendarTime, LayoutSidebar, MessagePlus, Messages, Plug, Robot, Store, Terminal } from './ui/icons'
import { SkeletonRows } from './ui/skeleton'
import { IconButton, Tip } from './ui/tip'
import { UpdateBar, UpdateStrip } from './update-bar'

/**
 * The conversations, the latest activity first;
 * above them, while any wait, how many memory proposals there are (step 9b); and at the foot,
 * always, the home on screen with the others and what the app does with homes (step 10c),
 * with the app's update above it while there is one. The list is the conversations,
 * the bots or the executors (step 16f), whose tab says
 * when one of their sessions waits for the user. Hidden (asked for by
 * the user on 16/10/2026), it leaves a strip that brings it back and still says when something
 * waits, or that an update is there. Each entry has its icon, and the tabs are only theirs, named
 * on hover (asked for by the user on 18/10/2026): their words did not fit. Its edge toward the
 * conversation drags it wider or narrower (store/layout.ts).
 */
export function Sidebar() {
  const proposals = useStore($proposals)
  const view = useStore($view)
  // The plugins' pages (step 16m), each an entry below the routines.
  const pages = inArea(useStore($contributions), 'page')
  const pluginPage = useStore($pluginPage)
  const tab = useStore($sidebarTab)
  const bots = useStore($bots)
  const waiting = useStore($pairings).filter(pairing => !pairing.approved).length
  const cards = useStore($cards)
  const { instances } = useStore($workbench)
  const asking =
    cards.filter(card => card.kind === 'executor-approval' || card.kind === 'executor-question').length ||
    instances.filter(row => row.waiting > 0).length
  const open = useStore($sidebarOpen)
  const conversationsDot = useConversationsDot(useStore($conversations).map(row => row.id))
  const botsDot = useConversationsDot(bots.map(bot => bot.conversation_id))
  const executorsDot = useInstancesDot(() => true)

  if (!open) {
    return (
      <nav
        aria-label="Conversations"
        className="flex w-9 shrink-0 flex-col items-center gap-2 border-r border-border bg-subtle py-3"
      >
        <IconButton
          icon={LayoutSidebar}
          label={t.layout.showSidebar(shortcut('B'))}
          onClick={() => {
            toggleSidebar(true)
          }}
          side="right"
          testId="sidebar-show"
        />
        {asking > 0 ? (
          <span className="rounded-full bg-waiting px-1.5 text-xs leading-4 font-medium text-waiting-foreground">
            {asking}
          </span>
        ) : (
          <StatusDot dot={worst([conversationsDot, botsDot, executorsDot])} />
        )}
        <UpdateStrip />
      </nav>
    )
  }

  return (
    <nav aria-label="Conversations" className="sidebar-open flex flex-col border-r border-border bg-subtle">
      <div className="space-y-1 p-3">
        <div className="flex items-center gap-1 pb-1">
          <IconButton
            icon={LayoutSidebar}
            label={t.layout.hideSidebar(shortcut('B'))}
            onClick={() => {
              toggleSidebar(false)
            }}
            side="bottom"
            testId="sidebar-hide"
          />
          <div className="flex min-w-0 flex-1 rounded-md bg-selected p-0.5" role="tablist">
            <Tab current={tab} dot={conversationsDot} icon={Messages} label={t.conversations.tab} tab="conversations" />
            <Tab count={bots.length} current={tab} dot={botsDot} icon={Robot} label={t.bots.tab} tab="bots" />
            <Tab
              alert={asking}
              current={tab}
              dot={executorsDot}
              icon={Terminal}
              label={t.executors.tab}
              tab="executors"
            />
          </div>
        </div>
        <Entry
          current={view === 'routines'}
          icon={CalendarTime}
          label={t.routines.title}
          onClick={showRoutines}
          testId="routines-entry"
        />
        <Entry
          current={view === 'marketplace'}
          icon={Store}
          label={t.marketplace.title}
          onClick={showMarketplace}
          testId="marketplace-entry"
        />
        {pages.map(page => (
          <Entry
            current={view === 'plugin' && pluginPage === page.key}
            icon={Plug}
            key={page.key}
            label={page.title}
            onClick={() => {
              host.openPage(page.key)
            }}
            testId={`plugin-page-${page.key}`}
          />
        ))}
        {waiting > 0 && (
          // The soul and the plugins live on the settings screen (step 15b); someone waiting with a
          // code still shows here, as the proposals do, and opens them there.
          <Entry
            current={false}
            icon={Plug}
            label={t.plugins.title}
            onClick={() => {
              openSettings('plugins')
            }}
            testId="plugins-entry"
          >
            <span
              className="rounded-full bg-waiting px-2 text-xs leading-5 font-medium text-waiting-foreground"
              data-testid="plugins-waiting"
            >
              {t.plugins.waiting(waiting)}
            </span>
          </Entry>
        )}
        {proposals.length > 0 && (
          <Entry
            current={view === 'proposals'}
            icon={Bulb}
            label={t.proposals.title}
            onClick={showProposals}
            testId="proposals-entry"
          >
            <span className="rounded-full bg-waiting px-2 text-xs leading-5 font-medium text-waiting-foreground">
              {proposals.length}
            </span>
          </Entry>
        )}
      </div>
      {/* The tab's name heads its list, the tabs being only icons (asked for by the user on
          22/10/2026). */}
      <h2 className="px-5 pb-2 text-sm font-semibold" data-testid="sidebar-title">
        {tab === 'bots' ? t.bots.tab : tab === 'executors' ? t.executors.tab : t.conversations.tab}
      </h2>
      {tab === 'bots' ? <BotsList /> : tab === 'executors' ? <ExecutorsList /> : <Conversations />}
      <UpdateBar />
      <HomeBar />
      <RailEdge rail="sidebar" />
    </nav>
  )
}

/** One of the sidebar's screens: its icon and its name, and what waits there. */
function Entry({
  children,
  current,
  icon: Glyph,
  label,
  onClick,
  testId
}: {
  children?: ReactNode
  current: boolean
  icon: Icon
  label: string
  onClick: () => void
  testId: string
}) {
  return (
    <button
      aria-current={current ? 'page' : undefined}
      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
        current ? 'bg-selected font-medium' : 'hover:bg-hover'
      }`}
      data-testid={testId}
      onClick={onClick}
      type="button"
    >
      <Glyph aria-hidden className="shrink-0 text-muted-foreground" size={16} stroke={1.75} />
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {children}
    </button>
  )
}

function Tab({
  alert,
  count,
  current,
  dot = '',
  icon: Glyph,
  label,
  tab
}: {
  /** How many things wait for the user there: a badge, not a count of rows. */
  alert?: number
  count?: number
  current: SidebarTab
  /** The most urgent dot of its rows, shown while no badge says more. */
  dot?: Dot
  icon: Icon
  label: string
  tab: SidebarTab
}) {
  return (
    <Tip label={count ? `${label} (${String(count)})` : label} side="bottom">
      <button
        aria-label={label}
        aria-selected={current === tab}
        className={`relative flex min-w-0 flex-1 items-center justify-center gap-1 rounded px-2 py-1 ${
          current === tab ? 'bg-background text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'
        }`}
        data-testid={`tab-${tab}`}
        onClick={() => {
          showSidebarTab(tab)
        }}
        role="tab"
        type="button"
      >
        <Glyph aria-hidden size={16} stroke={1.75} />
        {count ? <span className="text-xs text-muted-foreground">{count}</span> : null}
        {alert ? (
          <span
            className="rounded-full bg-waiting px-1.5 text-xs leading-4 font-medium text-waiting-foreground"
            data-testid={`tab-${tab}-alert`}
          >
            {alert}
          </span>
        ) : (
          <StatusDot dot={dot} />
        )}
      </button>
    </Tip>
  )
}

/** How near its end the list reads its next page, in pixels: before the user gets there. */
const NEAR_END = 240

/** The next page of the list, once it is scrolled near its end or does not fill its height. */
function nearEnd(list: HTMLElement | null): void {
  if (list && list.scrollHeight - list.scrollTop - list.clientHeight <= NEAR_END) {
    void moreConversations()
  }
}

/** The conversations, the last used first: one whose turn began here goes up at once, before its
 * row is read again. A page at a time (step 9e): the next one is read as the list scrolls near its
 * end. Above it, the search, whose results take its place while its field holds words. */
function Conversations() {
  const used = useStore($used)
  const conversations = byLastUse(useStore($conversations), row => lastUse(row.updated_at, used[row.id]))
  const selected = useStore($selected)
  const view = useStore($view)
  const read = useStore($read).conversations
  const failed = useStore($readError).conversations
  const more = useStore($listMore)
  const searching = useStore($search).text.trim() !== ''
  const list = useRef<HTMLUListElement>(null)
  const rows = conversations.length

  // A page that leaves the list short of its height reads the next one at once.
  useEffect(() => {
    if (rows > 0 && more.cursor && !searching) {
      nearEnd(list.current)
    }
  }, [rows, more.cursor, searching])

  return (
    <>
      <div className="px-3 pb-2">
        <button
          className="flex w-full items-center gap-2 rounded-md border border-border-strong px-2 py-1.5 text-left text-sm font-medium hover:bg-muted"
          onClick={newConversation}
          type="button"
        >
          <MessagePlus aria-hidden size={16} stroke={1.75} />
          {t.conversations.new}
        </button>
      </div>
      <SearchField />
      {searching && <SearchResults />}
      {/* Hidden, not gone, while the search shows: it comes back where it was. */}
      <ul
        className="flex-1 overflow-y-auto px-2 pb-3"
        data-testid="conversation-list"
        hidden={searching}
        onScroll={() => {
          nearEnd(list.current)
        }}
        ref={list}
      >
        {!read && conversations.length === 0 && (
          <li className={failed ? 'px-1' : ''}>
            {failed ? (
              <ReadFailed error={failed} what="conversations" />
            ) : (
              <SkeletonRows label={t.conversations.reading} />
            )}
          </li>
        )}
        {read && conversations.length === 0 && (
          <li className="px-2 py-1 text-sm text-muted-foreground">{t.conversations.empty}</li>
        )}
        {conversations.map(conversation => (
          <li key={conversation.id}>
            <Conversation current={conversation.id === selected && view === 'conversation'} row={conversation} />
          </li>
        ))}
        {more.reading && (
          <li>
            <SkeletonRows count={2} label={t.conversations.readingMore} />
          </li>
        )}
        {more.error && (
          <li className="px-1">
            <ReadFailed
              error={more.error}
              onAgain={() => {
                void moreConversations(true)
              }}
              what="conversations"
            />
          </li>
        )}
      </ul>
    </>
  )
}
