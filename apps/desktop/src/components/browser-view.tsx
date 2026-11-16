import { useStore } from '@nanostores/react'
import { type SyntheticEvent, useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import { $machine, $selected, $threads } from '@/store/app'
import { $browserExpanded, browserCommand, lookForServers, toggleBrowserExpanded } from '@/store/browser'
import { $shownTab, $shownTabs, openTab, where } from '@/store/browser-screen'
import { conversationPorts, type LocalServer, mergeLocalServers } from '@/store/local-servers'
import { previewables } from '@/store/previews'
import { togglePanel } from '@/store/terminals'

import { ArrowLeft, ArrowRight, Close, Collapse, Expand, Plus, Radar, Refresh, Robot, Stop, Web } from './ui/icons'
import { IconButton } from './ui/tip'

/** What lies over the panel, which a native view would cover: a dialog or a menu. A tooltip does not
 * count: it opens above the toolbar. */
const OVERLAY = '[role="alertdialog"], [role="dialog"], [role="menu"]'

/** Whether a dialog or a menu of the app is open, followed as the page's popups come and go (Radix
 * puts them in portals, directly under `body`). */
function useObscured(): boolean {
  const [obscured, setObscured] = useState(false)

  useEffect(() => {
    const look = () => {
      setObscured(document.body.querySelector(OVERLAY) !== null)
    }
    look()
    const observer = new MutationObserver(look)
    observer.observe(document.body, { childList: true })

    return () => {
      observer.disconnect()
    }
  }, [])

  return obscured
}

/** A tab's name: its page's title, else its address's host, else the new tab's. */
function tabName(tab: BrowserTab): string {
  if (tab.title) {
    return tab.title
  }

  try {
    return new URL(tab.url).host || t.browser.untitled
  } catch {
    return t.browser.untitled
  }
}

/**
 * The Browser tab of the side panel (docs/GLOSSARY.md, `app browser`), laid out as a browser is: the
 * tabs on top, with the buttons that expand it over the conversation and close it; below them the
 * address bar with back, forward and reload; then the page's box, where the page's native view is
 * laid. The main process places the view from the box's rectangle in the window, reported as the box
 * moves or resizes, expanded or not. While the box is not on screen, a dialog or a menu of the app
 * lies over it, or the tab has no page yet (the empty page shows instead), the view is hidden: a
 * native view is always above the app's own page.
 */
export function BrowserView() {
  const tabs = useStore($shownTabs)
  const tab = useStore($shownTab)
  const selected = tab?.id
  const expanded = useStore($browserExpanded)
  const box = useRef<HTMLDivElement>(null)
  const address = useRef<HTMLInputElement>(null)
  const [draft, setDraft] = useState<null | string>(null)
  const [invalid, setInvalid] = useState(false)
  const obscured = useObscured()
  const showsPage = tab !== undefined && tab.url !== ''

  useEffect(() => {
    const element = box.current

    if (!element || obscured || !showsPage) {
      return
    }

    const report = () => {
      const { height, width, x, y } = element.getBoundingClientRect()
      void browserCommand({ command: 'show', rect: { height, width, x, y } })
    }
    report()
    const observer = new ResizeObserver(report)
    observer.observe(element)
    observer.observe(document.documentElement)
    window.addEventListener('resize', report)

    return () => {
      observer.disconnect()
      window.removeEventListener('resize', report)
      void browserCommand({ command: 'show', rect: null })
    }
  }, [obscured, showsPage])

  const go = (text: string) =>
    browserCommand({ command: 'go', id: tab?.id ?? null, text, ...where() }).then(answer => {
      if (answer && !answer.ok && answer.reason === 'address') {
        setInvalid(true)

        return
      }

      setInvalid(false)
      setDraft(null)
      address.current?.blur()
    })

  const submit = (event: SyntheticEvent) => {
    event.preventDefault()
    void go(draft ?? tab?.url ?? '')
  }

  return (
    <div className="absolute inset-0 flex flex-col bg-background" data-testid="browser-view">
      <div className="flex items-center gap-1 border-b border-border px-1.5 py-1 text-xs">
        <div
          className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto"
          data-testid="browser-tabs"
          role="tablist"
        >
          {tabs.length === 0 ? (
            <div
              aria-selected
              className="flex max-w-40 shrink-0 items-center gap-1 rounded bg-muted px-2 py-0.5"
              data-testid="browser-page"
              role="tab"
            >
              <Web aria-hidden className="shrink-0" size={12} stroke={1.75} />
              <span className="truncate">{t.browser.untitled}</span>
            </div>
          ) : (
            tabs.map(each => (
              <div
                aria-selected={each.id === selected}
                className={`flex max-w-40 shrink-0 items-center gap-1 rounded px-2 py-0.5 ${
                  each.id === selected ? 'bg-muted' : 'text-muted-foreground'
                }`}
                data-testid="browser-page"
                key={each.id}
                role="tab"
              >
                <button
                  className="flex min-w-0 items-center gap-1"
                  onClick={() => {
                    void browserCommand({ command: 'select', id: each.id })
                  }}
                  title={each.url}
                  type="button"
                >
                  {each.agent ? (
                    <span className="shrink-0" data-testid="browser-agent-mark" title={t.browser.openedByAtlas}>
                      <Robot aria-label={t.browser.openedByAtlas} role="img" size={12} stroke={1.75} />
                    </span>
                  ) : (
                    <Web aria-hidden className="shrink-0" size={12} stroke={1.75} />
                  )}
                  <span className="truncate">{tabName(each)}</span>
                </button>
                <button
                  aria-label={t.browser.closeTab}
                  className="text-muted-foreground hover:text-soft"
                  onClick={() => {
                    void browserCommand({ command: 'close', id: each.id })
                  }}
                  type="button"
                >
                  <Close aria-hidden size={11} stroke={2} />
                </button>
              </div>
            ))
          )}
          <IconButton
            icon={Plus}
            label={t.browser.newTab}
            onClick={() => {
              void openTab().then(() => {
                address.current?.focus()
              })
            }}
            testId="browser-new"
          />
        </div>
        <IconButton
          icon={expanded ? Collapse : Expand}
          label={expanded ? t.browser.collapse : t.browser.expand}
          onClick={toggleBrowserExpanded}
          pressed={expanded}
          testId="browser-expand"
        />
        <IconButton
          icon={Close}
          label={t.browser.close}
          onClick={() => {
            togglePanel(false)
          }}
          testId="browser-close"
        />
      </div>
      <form className="flex items-center gap-0.5 border-b border-border px-1.5 py-1" onSubmit={submit}>
        <IconButton
          disabled={!tab?.canGoBack}
          icon={ArrowLeft}
          label={t.browser.back}
          onClick={() => {
            if (tab) {
              void browserCommand({ command: 'back', id: tab.id })
            }
          }}
          testId="browser-back"
        />
        <IconButton
          disabled={!tab?.canGoForward}
          icon={ArrowRight}
          label={t.browser.forward}
          onClick={() => {
            if (tab) {
              void browserCommand({ command: 'forward', id: tab.id })
            }
          }}
          testId="browser-forward"
        />
        <IconButton
          disabled={!tab}
          icon={tab?.loading ? Stop : Refresh}
          label={tab?.loading ? t.browser.stop : t.browser.reload}
          onClick={() => {
            if (tab) {
              void browserCommand({ command: tab.loading ? 'stop' : 'reload', id: tab.id })
            }
          }}
          testId="browser-reload"
        />
        <input
          aria-invalid={invalid}
          aria-label={t.browser.address}
          autoComplete="off"
          className={`min-w-0 flex-1 rounded-md border bg-muted px-2 py-1 text-xs outline-none focus:bg-background ${
            invalid ? 'border-failed' : 'border-transparent focus:border-ring'
          }`}
          data-testid="browser-address"
          onBlur={() => {
            setDraft(null)
          }}
          onChange={event => {
            setInvalid(false)
            setDraft(event.target.value)
          }}
          onFocus={event => {
            event.target.select()
          }}
          placeholder={t.browser.placeholder}
          ref={address}
          spellCheck={false}
          type="text"
          value={draft ?? tab?.url ?? ''}
        />
      </form>
      {invalid && (
        <p className="px-2 py-1 text-xs text-failed-text" role="alert">
          {t.browser.invalid}
        </p>
      )}
      {tab?.error && <p className="px-2 py-1 text-xs text-failed-text">{t.browser.failed(tab.error)}</p>}
      <div className="relative min-h-0 flex-1" data-testid="browser-box" ref={box}>
        {!showsPage && (
          <EmptyPage
            open={url => {
              void go(url)
            }}
          />
        )}
      </div>
    </div>
  )
}

/** What a tab with no page shows: what the browser is for, and the servers on this machine to open
 * with a click, which a look finds only when asked (and only where the server is this computer). */
function EmptyPage({ open }: { open: (url: string) => void }) {
  const threads = useStore($threads)
  const selected = useStore($selected)
  const machine = useStore($machine)
  const [servers, setServers] = useState<LocalServer[] | null>(null)
  const [looking, setLooking] = useState(false)
  const items = (selected ? threads[selected]?.items : undefined) ?? []
  // The server is this computer's: a machine of the list, or a server the app did not start, may be elsewhere.
  const here = machine === null && !window.atlas.remote

  const look = () => {
    const addresses = previewables(items).flatMap(target => (target.url ? [target.url] : []))
    setLooking(true)
    void lookForServers(conversationPorts(addresses)).then(answering => {
      setServers(mergeLocalServers(addresses, answering))
      setLooking(false)
    })
  }

  return (
    <div
      className="absolute inset-0 flex flex-col items-center justify-center gap-2 overflow-y-auto p-6 text-center"
      data-testid="browser-empty"
    >
      <Web aria-hidden className="text-faint" size={44} stroke={1.25} />
      <h2 className="text-sm font-medium">{t.browser.emptyTitle}</h2>
      <p className="max-w-64 text-xs text-muted-foreground">{t.browser.emptyHint}</p>
      {here && (
        <div className="mt-2 flex flex-col items-center gap-2">
          <button
            className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs hover:bg-hover disabled:opacity-50"
            data-testid="browser-detect"
            disabled={looking}
            onClick={look}
            type="button"
          >
            <Radar aria-hidden size={14} stroke={1.75} />
            {looking ? t.browser.detecting : t.browser.detect}
          </button>
          {servers !== null && servers.length === 0 && (
            <p className="text-xs text-muted-foreground" data-testid="browser-no-servers">
              {t.browser.noServers}
            </p>
          )}
          {servers !== null && servers.length > 0 && (
            <div className="flex flex-col items-stretch gap-1" data-testid="browser-servers">
              <p className="text-xs text-muted-foreground">{t.browser.servers}</p>
              {servers.map(server => (
                <button
                  className="truncate rounded-md bg-muted px-2 py-1 text-xs hover:bg-selected"
                  data-testid="browser-server"
                  key={server.port}
                  onClick={() => {
                    open(server.url)
                  }}
                  type="button"
                >
                  {server.url}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
