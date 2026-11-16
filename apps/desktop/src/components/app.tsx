import { useStore } from '@nanostores/react'
import { Activity, type CSSProperties, useEffect } from 'react'

import { t } from '@/i18n'
import { PluginPage } from '@/plugins/view'
import { MAC } from '@/shortcuts'
import {
  $approved,
  $bots,
  $cards,
  $connection,
  $home,
  $keyLater,
  $keyNotice,
  $modelOptions,
  $proposals,
  $read,
  $selected,
  $sending,
  $session,
  $tabs,
  $threads,
  $view,
  $workbench,
  type Card,
  currentFolder,
  DRAFT,
  keyScreenShown
} from '@/store/app'
import { $browserExpanded, browserHost, followBrowser, toggleBrowser } from '@/store/browser'
import { followScreen } from '@/store/browser-screen'
import { $panelWide, $panelWidth, $sidebarWidth, shownWidth, toggleSidebar } from '@/store/layout'
import { $firstRun, firstRunOf } from '@/store/setup'
import { $terminals, openTerminal, refreshTerminalAvailable, terminalAvailable, togglePanel } from '@/store/terminals'
import { EMPTY_THREAD } from '@/store/thread'
import { waitingCalls } from '@/store/waiting'
import { offerWork } from '@/store/work'

import { ApprovalCard } from './approval-card'
import { AskCard } from './ask-card'
import { BotDialogHost } from './bot-dialog'
import { BotEmpty, BotHeader } from './bot-header'
import { Composer } from './composer'
import { DictationsView } from './dictation'
import { ExtensionsView } from './extensions-view'
import { FirstRun, FirstRunBand } from './first-run'
import { HomeDialogHost } from './home-dialog'
import { KeyBand } from './key-screen'
import { MarketplaceView } from './marketplace-view'
import { PreviewLinks } from './preview-links'
import { ProposalCard } from './proposal-card'
import { approvedHere, ProposalsView } from './proposals-view'
import { RoutinesView } from './routines-view'
import { SecretCard } from './secret-card'
import { SessionView } from './session-view'
import { SettingsView } from './settings-view'
import { SidePanel } from './side-panel'
import { Sidebar } from './sidebar'
import { SpeechBand } from './speech-view'
import { StartScreen } from './start-screen'
import { TabStrip } from './tab-strip'
import { ThreadView } from './thread-view'

export function App() {
  const selected = useStore($selected)
  const threads = useStore($threads)
  const cards = useStore($cards)
  const proposals = useStore($proposals)
  const approved = useStore($approved)
  const view = useStore($view)
  const thread = (selected ? threads[selected] : undefined) ?? EMPTY_THREAD
  // A conversation opened for the first time is read before it shows: never as an empty one.
  const reading = selected !== null && threads[selected] === undefined
  const sendingAll = useStore($sending)
  const sending = sendingAll[selected ?? DRAFT]
  const read = useStore($read)
  const shown = cardsOf(cards, selected)
  // The calls those cards hold wait for the user on the trail; they do not run.
  const waiting = waitingCalls(shown, thread.items)
  // Every conversation open in a tab and read keeps its thread drawn, hidden while another shows:
  // going back to one only shows it. Drawn again, a long conversation's Markdown held the click for
  // about a second (found by the user on 01/11/2026). The one on screen comes last, as the only one
  // the page reads.
  const place = selected ?? DRAFT
  const kept = useStore($tabs).flatMap(({ place: held }) => {
    const other = held.kind === 'conversation' && held.conversationId !== selected ? held.conversationId : null
    const loaded = other === null ? undefined : threads[other]

    return other !== null && loaded ? [{ place: other, thread: loaded }] : []
  })
  // A proposal shows in the conversation its review read, until it is decided.
  const fromHere = (proposal: { conversation_id: null | string }) =>
    selected !== null && proposal.conversation_id === selected
  const proposed = proposals.filter(fromHere)
  const done = approvedHere(approved, proposals, fromHere)
  const { state } = useStore($connection)
  // Until the home first answered, the window says Atlas opens, not that it has nothing.
  const opening = state !== 'open' && !read.conversations
  const session = useStore($session)
  // A bot's fixed conversation says whose it is.
  const bot = useStore($bots).find(row => selected !== null && row.conversation_id === selected)
  const options = useStore($modelOptions)
  const later = useStore($keyLater)
  const notice = useStore($keyNotice)
  // With no provider's key, the app opens on the first run; left with none stored, a band. Opened
  // from the settings, it shows until it is left.
  const home = useStore($home)
  const setup = firstRunOf(useStore($firstRun), home)
  // Stepped away from for a conversation it opened, it waits a band away.
  const setupAway = setup.open && setup.away === true
  const firstRun = state === 'open' && ((setup.open && !setupAway) || keyScreenShown(options, later, notice))
  // A home that had its key before the first run existed is offered "Bring your work" once.
  const keyed = options !== null && !keyScreenShown(options, false, null)
  const executorsOn = useStore($workbench)
    .executors.filter(row => row.enabled && row.installed && row.supported)
    .map(row => row.name)
    .join('\n')
  useEffect(() => {
    if (state === 'open' && keyed && read.workbench) {
      void offerWork(home, executorsOn ? executorsOn.split('\n') : [])
    }
  }, [executorsOn, home, keyed, read.workbench, state])
  const keyBand = state === 'open' && later && keyScreenShown(options, false, null)
  // The rails' widths, which styles.css gives the sidebar and the side panel.
  const panelWidth = shownWidth(useStore($panelWidth), useStore($panelWide))
  const sidebarWidth = useStore($sidebarWidth)
  // The browser asked to take the conversation's place (store/browser.ts).
  const expanded = useStore($browserExpanded)
  const widths = {
    '--panel-width': `${String(panelWidth)}px`,
    '--sidebar-width': `${String(sidebarWidth)}px`
  } as CSSProperties

  // The browser's tabs and the pages the agent opens, for as long as the app is open: the panel need not show it.
  useEffect(() => {
    const stops = [followBrowser(), followScreen()]

    return () => {
      for (const stop of stops) {
        stop()
      }
    }
  }, [])

  // Whether the server's machine opens terminals, asked each time a connection opens (another
  // machine's may not); Ctrl+` shows or hides it, Ctrl+B the sidebar.
  useEffect(() => {
    if (state === 'open') {
      void refreshTerminalAvailable()
    }
  }, [state, session])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Ctrl+B, and ⌘B on a Mac as its own apps take it.
      if (
        (event.ctrlKey || (MAC && event.metaKey)) &&
        !event.altKey &&
        !event.shiftKey &&
        event.key.toLowerCase() === 'b'
      ) {
        event.preventDefault()
        toggleSidebar()
      }

      // Ctrl+Shift+B for the browser, and ⇧⌘B on a Mac.
      if (
        (event.ctrlKey || (MAC && event.metaKey)) &&
        event.shiftKey &&
        !event.altKey &&
        event.key.toLowerCase() === 'b' &&
        browserHost()
      ) {
        event.preventDefault()
        toggleBrowser()
      }

      if (event.ctrlKey && event.key === '`') {
        event.preventDefault()

        if ($terminals.get().length === 0) {
          void terminalAvailable().then(found => (found ? openTerminal(currentFolder()) : undefined))
        } else {
          togglePanel()
        }
      }
    }
    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  if (firstRun) {
    return (
      <div className="relative flex h-full bg-background text-foreground" data-connection={state}>
        <FirstRun />
        <SettingsView />
      </div>
    )
  }

  return (
    <div
      className="relative flex h-full bg-background text-foreground"
      data-connection={state}
      data-panel-expanded={expanded ? '' : undefined}
      style={widths}
    >
      <Sidebar />
      <main className="main-pane flex min-w-0 flex-1 flex-col">
        <TabStrip />
        <ConnectionBand />
        {keyBand && <KeyBand />}
        {setupAway && <FirstRunBand step={setup.step} />}
        {state === 'open' && <SpeechBand />}
        {view === 'proposals' ? (
          <ProposalsView />
        ) : view === 'routines' ? (
          <RoutinesView />
        ) : view === 'dictations' ? (
          <DictationsView />
        ) : view === 'marketplace' ? (
          <MarketplaceView />
        ) : view === 'extensions' ? (
          <ExtensionsView />
        ) : view === 'plugin' ? (
          <PluginPage />
        ) : view === 'session' ? (
          <SessionView />
        ) : view === 'start' ? (
          <StartScreen />
        ) : (
          <>
            {bot && <BotHeader bot={bot} />}
            {/* One list, keyed by conversation, so a thread hidden or shown again is the same one. */}
            {[
              ...kept.map(other => (
                <Activity key={other.place} mode="hidden">
                  <ThreadView
                    place={other.place}
                    sending={sendingAll[other.place]}
                    thread={other.thread}
                    waiting={waitingCalls(cardsOf(cards, other.place), other.thread.items)}
                  />
                </Activity>
              )),
              <Activity key={place} mode="visible">
                <ThreadView
                  empty={opening ? <Opening /> : bot ? <BotEmpty bot={bot} /> : undefined}
                  loading={reading}
                  place={place}
                  sending={sending}
                  thread={thread}
                  waiting={waiting}
                />
              </Activity>
            ]}
            {shown.length + proposed.length + done.length > 0 && (
              <div className="mx-auto max-h-[50vh] w-full max-w-3xl space-y-3 overflow-y-auto px-6 pb-3">
                {shown.map(card => {
                  if (card.kind === 'ask') {
                    return <AskCard card={card} key={card.id} />
                  }
                  if (card.kind === 'secret') {
                    return <SecretCard card={card} key={card.id} />
                  }

                  return <ApprovalCard card={card} key={card.id} />
                })}
                {done.map(({ proposal, result }) => (
                  <ProposalCard approved={result} key={proposal.id} proposal={proposal} />
                ))}
                {proposed.map(proposal => (
                  <ProposalCard key={proposal.id} proposal={proposal} />
                ))}
              </div>
            )}
            <PreviewLinks thread={thread} />
            <Composer connected={state === 'open'} running={thread.running} to={bot?.title} />
          </>
        )}
      </main>
      <SidePanel />
      <BotDialogHost />
      <HomeDialogHost />
      <SettingsView />
    </div>
  )
}

/** The cards that wait for the user in a conversation's thread. An executor's are in its session's
 * conversation, in the executors tab (step 16f). */
function cardsOf(
  cards: readonly Card[],
  conversationId: null | string
): Extract<Card, { kind: 'approval' | 'ask' | 'secret' }>[] {
  return cards.filter(
    (card): card is Extract<Card, { kind: 'approval' | 'ask' | 'secret' }> =>
      card.conversationId === conversationId &&
      (card.kind === 'ask' || card.kind === 'approval' || card.kind === 'secret')
  )
}

/** A band while the connection is not open, with why the last dial failed. Opening, the window
 * says so in the conversation's place and the band shows only a failure: a warning at every start
 * read as something wrong (the UI review, 25/10/2026). */
function ConnectionBand() {
  const { error, state } = useStore($connection)
  const opened = useStore($read).conversations

  if (state === 'open' || (!opened && !error)) {
    return null
  }

  const text = !opened
    ? t.connection.opening
    : state === 'connecting' || state === 'idle'
      ? t.connection.connecting
      : t.connection.closed

  // Quiet, with the state's dot: blue while it dials again, red once the line is lost; amber is
  // only what waits for the user (decided with the user on 25/10/2026).
  return (
    <div
      className="flex items-center gap-2 border-b border-border bg-subtle px-4 py-2 text-sm text-soft"
      data-testid="connection-band"
      role="status"
    >
      <span
        aria-hidden
        className={`size-2 shrink-0 rounded-full ${
          state === 'closed' ? 'bg-failed' : 'bg-working motion-safe:animate-pulse'
        }`}
      />
      <span>
        {text}
        {error && <span className="ml-2 text-muted-foreground">{t.connection.failed(error)}</span>}
      </span>
    </div>
  )
}

/** The conversation's place while the home's server starts: what happens, never "nothing here". */
function Opening() {
  return (
    <p className="pt-24 text-center text-muted-foreground" data-testid="opening" role="status">
      {t.connection.opening}
    </p>
  )
}
