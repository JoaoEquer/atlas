import { useStore } from '@nanostores/react'
import { useState } from 'react'
import { Streamdown } from 'streamdown'

import { t } from '@/i18n'
import type { MarketplacePage, MarketplaceRow, MarketplaceSecretRow } from '@/protocol/protocol.generated'
import {
  $market,
  actOnMarket,
  cancelMarketUpdate,
  closeMarketItem,
  openMarketItem,
  PLUGIN_GUIDE,
  refreshMarketplace,
  setMarketKind,
  setMarketQuery,
  storeMarketSecret,
  writePlugin
} from '@/store/app'
import { actionOf, counts, resolved, shown } from '@/store/marketplace'
import { openSettings } from '@/store/settings'

import { Badge, KindPicture, KindTabs } from './extension-card'
import { PageHeader } from './page-header'
import { Robot } from './ui/icons'

/**
 * The marketplace (step 16s): skills, MCP servers, plugins and executors made for Atlas, a tab
 * each, the Atlas team's first, and a page per item with its README at the commit that was
 * reviewed. The home's server reads the marketplace and installs; a plugin arrives off, and turning
 * it on is the consent of the Plugins screen. No downloads or ratings yet (decided by the user on
 * 24/10/2026).
 */
export function MarketplaceView() {
  const market = useStore($market)

  return (
    <div className="flex-1 overflow-y-auto" data-testid="marketplace-view">
      <div className="mx-auto max-w-5xl space-y-4 px-6 py-6">{market.open === null ? <Listing /> : <ItemPage />}</div>
    </div>
  )
}

function Listing() {
  const market = useStore($market)
  const { community, official } = shown(market.list, market.kind, market.query)
  const total = counts(market.list)
  const nothing = official.length + community.length === 0

  return (
    <>
      <PageHeader
        actions={
          <>
            <input
              aria-label={t.marketplace.search}
              className="w-56 rounded-md border border-border-strong bg-transparent px-2 py-1 text-sm"
              data-testid="marketplace-search"
              onChange={event => {
                setMarketQuery(event.target.value)
              }}
              placeholder={t.marketplace.search}
              type="search"
              value={market.query}
            />
            <button
              className={BUTTON}
              data-testid="marketplace-refresh"
              disabled={market.reading}
              onClick={refreshMarketplace}
              type="button"
            >
              {market.reading ? t.marketplace.reading : t.marketplace.refresh}
            </button>
          </>
        }
        intro={t.marketplace.intro}
        title={t.marketplace.title}
      >
        {/* Sharing starts with making one: Atlas writes it with the user, and the guide says how it
            enters (decided by the user on 25/10/2026). */}
        <p className="mt-1 flex gap-3 text-sm">
          <button
            className="link"
            data-testid="marketplace-make"
            onClick={writePlugin}
            title={t.marketplace.makeHint}
            type="button"
          >
            {t.marketplace.makeYourOwn}
          </button>
          <a className="link" href={PLUGIN_GUIDE} rel="noreferrer" target="_blank">
            {t.marketplace.guide}
          </a>
        </p>
      </PageHeader>
      <Notice />
      <KindTabs counts={total} current={market.kind} onPick={setMarketKind} testId="marketplace-tab" />
      {market.failed && <Failed text={market.failed} />}
      {nothing && (
        <p className="text-sm text-muted-foreground">
          {market.list === null && market.reading
            ? t.marketplace.reading
            : market.query
              ? t.marketplace.noMatch
              : t.marketplace.empty}
        </p>
      )}
      <Shelf rows={official} title={t.marketplace.official} />
      <Shelf rows={community} title={t.marketplace.community} />
    </>
  )
}

/** Why the marketplace could not be read now: the last read stands in, or nothing does. */
function Notice() {
  const { error, list } = useStore($market)
  const problem = error ?? list?.problem

  if (!problem) {
    return null
  }

  const text = list?.read_at ? t.marketplace.stale(problem, when(list.read_at)) : t.marketplace.unread(problem)

  return (
    <p
      className="rounded-md bg-waiting/10 px-3 py-2 text-sm text-waiting-text"
      data-testid="marketplace-notice"
      role="status"
    >
      {text}
    </p>
  )
}

function Shelf({ rows, title }: { rows: MarketplaceRow[]; title: string }) {
  if (rows.length === 0) {
    return null
  }

  return (
    <section className="space-y-2">
      <h2 className="text-sm font-medium text-muted-foreground">{title}</h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {rows.map(row => (
          <Card key={row.name} row={row} />
        ))}
      </div>
    </section>
  )
}

function Card({ row }: { row: MarketplaceRow }) {
  return (
    <div
      className="flex cursor-pointer flex-col gap-2 rounded-lg border border-border p-3 hover:border-border-strong"
      data-name={row.name}
      data-testid="marketplace-card"
      onClick={() => {
        openMarketItem(row.name)
      }}
      onKeyDown={event => {
        if (event.key === 'Enter') {
          openMarketItem(row.name)
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="flex items-start gap-3">
        <Picture row={row} size={40} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{row.title}</p>
          <p className="truncate text-xs text-muted-foreground">{t.marketplace.by(row.author)}</p>
        </div>
        {row.tier === 'official' && <Badge>{t.marketplace.officialBadge}</Badge>}
      </div>
      <p className="line-clamp-2 min-h-10 text-sm text-soft">{row.summary}</p>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs text-muted-foreground">
          {row.shipped ? t.marketplace.shipped : row.version}
        </span>
        <Action row={row} />
      </div>
    </div>
  )
}

function ItemPage() {
  const market = useStore($market)
  const page = market.page
  const row = page?.item ?? market.list?.items.find(item => item.name === market.open)

  return (
    <div className="space-y-4" data-testid="marketplace-page">
      <button className="link text-sm" onClick={closeMarketItem} type="button">
        ← {t.marketplace.back}
      </button>
      {row && <Head row={row} />}
      {market.failed && <Failed text={market.failed} />}
      {market.brought.length > 0 && (
        <p className="text-sm text-soft">{t.marketplace.brought(market.brought.join(', '))}</p>
      )}
      {market.confirming !== null && <Confirm name={market.confirming} />}
      {row?.state === 'off' && <p className="text-sm text-muted-foreground">{t.marketplace.turnOnHint}</p>}
      {market.pageError && <Failed text={market.pageError} />}
      {!page && !market.pageError && <p className="text-sm text-muted-foreground">{t.marketplace.loadingPage}</p>}
      {page && <Body page={page} />}
    </div>
  )
}

function Head({ row }: { row: MarketplaceRow }) {
  const market = useStore($market)
  const [removing, setRemoving] = useState(false)
  const removable = !row.shipped && row.state !== 'available'

  return (
    <div className="flex flex-wrap items-start gap-4">
      <Picture row={row} size={64} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">{row.title}</h1>
          {row.tier === 'official' && <Badge>{t.marketplace.officialBadge}</Badge>}
          <span className="text-xs text-muted-foreground">{t.marketplace.kinds[row.kind]}</span>
        </div>
        <p className="text-sm text-muted-foreground">
          {[
            t.marketplace.by(row.author),
            row.shipped ? t.marketplace.shipped : '',
            row.version ? t.marketplace.version(row.version) : '',
            row.license ? t.marketplace.license(row.license) : ''
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
        <p className="text-sm">{row.summary}</p>
        {row.problem && <p className="text-sm text-waiting-text">{row.problem}</p>}
        <div className="flex flex-wrap gap-3 text-sm">
          {row.source_url && (
            <a className="link" href={row.source_url} rel="noreferrer" target="_blank">
              {row.kind === 'mcp' ? t.marketplace.homepage : t.marketplace.source}
            </a>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Action row={row} />
        {removable &&
          (removing ? (
            <button
              className={`${BUTTON} text-failed-text`}
              data-testid="marketplace-remove-confirm"
              disabled={market.busy === row.name}
              onClick={() => {
                setRemoving(false)
                actOnMarket('remove', row.name)
              }}
              type="button"
            >
              {t.marketplace.confirmRemove}
            </button>
          ) : (
            <button
              className={BUTTON}
              data-testid="marketplace-remove"
              onClick={() => {
                setRemoving(true)
              }}
              type="button"
            >
              {t.marketplace.remove}
            </button>
          ))}
      </div>
    </div>
  )
}

function Body({ page }: { page: MarketplacePage }) {
  return (
    <div className="space-y-4">
      {page.needs.length > 0 && (
        <section className="space-y-1">
          <h2 className="text-sm font-medium">{t.marketplace.needs}</h2>
          <ul className="space-y-0.5 text-sm">
            {page.needs.map(need => (
              <li
                className={need.state === 'missing' ? 'text-waiting-text' : 'text-soft'}
                data-state={need.state}
                data-testid="marketplace-need"
                key={`${need.kind}-${need.name}`}
              >
                {t.marketplace.need[need.kind](need.name, need.state)}
              </li>
            ))}
          </ul>
        </section>
      )}
      {page.secrets.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">{t.marketplace.secrets}</h2>
          {page.secrets.map(secret => (
            <Secret item={page.item.name} key={secret.name} secret={secret} />
          ))}
        </section>
      )}
      {page.screenshots.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">{t.marketplace.screenshots}</h2>
          <div className="flex gap-3 overflow-x-auto">
            {page.screenshots.map(shot => (
              <img alt="" className="h-48 rounded-md border border-border" key={shot} src={shot} />
            ))}
          </div>
        </section>
      )}
      {page.entry && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">{t.marketplace.entry}</h2>
          <pre
            className="overflow-x-auto rounded-lg border border-border bg-subtle p-3 text-xs"
            data-testid="marketplace-entry-json"
          >
            {page.entry}
          </pre>
        </section>
      )}
      {page.readme && (
        <article className="rounded-lg border border-border p-4" data-testid="marketplace-readme">
          {/* A stranger's text: its raw HTML is skipped, and its addresses kept to the item's tree. */}
          <Streamdown
            mode="static"
            skipHtml
            urlTransform={(url, key) => resolved(url, key === 'src' ? page.picture_base : page.link_base)}
          >
            {page.readme}
          </Streamdown>
        </article>
      )}
    </div>
  )
}

function Secret({ item, secret }: { item: string; secret: MarketplaceSecretRow }) {
  const [value, setValue] = useState('')
  // A stored secret is not asked for again: only a way to change it.
  const [changing, setChanging] = useState(false)

  return (
    <div className="text-sm" data-stored={secret.stored} data-testid="marketplace-secret">
      <p>
        <span className="font-mono">{secret.name}</span>
        <span className="ml-2 text-muted-foreground">
          {secret.stored ? t.marketplace.secretStored : t.marketplace.secretMissing}
        </span>
        {secret.stored && !changing && (
          <button
            className="link ml-2"
            data-testid="marketplace-secret-change"
            onClick={() => {
              setChanging(true)
            }}
            type="button"
          >
            {t.plugins.changeSecret}
          </button>
        )}
      </p>
      {secret.description && (
        <p className="text-muted-foreground">
          {secret.description}{' '}
          {secret.url && (
            <a className="link" href={secret.url} rel="noreferrer" target="_blank">
              {secret.url}
            </a>
          )}
        </p>
      )}
      {(!secret.stored || changing) && (
        <form
          className="mt-1 flex gap-2"
          onSubmit={event => {
            event.preventDefault()
            void storeMarketSecret(item, secret.name, value).then(done => {
              if (done) {
                setValue('')
                setChanging(false)
              }
            })
          }}
        >
          <input
            aria-label={secret.name}
            autoComplete="off"
            className="w-72 rounded-md border border-border-strong bg-transparent px-2 py-1"
            data-testid="marketplace-secret-input"
            onChange={event => {
              setValue(event.target.value)
            }}
            placeholder={t.marketplace.paste}
            spellCheck={false}
            type="password"
            value={value}
          />
          <button className={BUTTON} data-testid="marketplace-secret-save" disabled={!value.trim()} type="submit">
            {t.marketplace.save}
          </button>
        </form>
      )}
    </div>
  )
}

/** The item's button, by where it stands: install, update, turn on, or what it is. */
function Action({ row }: { row: MarketplaceRow }) {
  const market = useStore($market)
  const busy = market.busy === row.name
  const action = actionOf(row)
  const act = (run: () => void) => (event: { stopPropagation: () => void }) => {
    event.stopPropagation()
    run()
  }

  if (action === 'install' || action === 'update') {
    const label =
      action === 'install'
        ? busy
          ? t.marketplace.installing
          : t.marketplace.install
        : busy
          ? t.marketplace.updating
          : t.marketplace.update

    return (
      <button
        className={PRIMARY}
        data-action={action}
        data-testid="marketplace-action"
        disabled={market.busy !== null}
        onClick={act(() => {
          actOnMarket(action, row.name)
        })}
        type="button"
      >
        {label}
      </button>
    )
  }

  if (action === 'turn-on') {
    return (
      <button
        className={BUTTON}
        data-action={action}
        data-testid="marketplace-action"
        onClick={act(() => {
          openSettings('extensions', 'plugin')
        })}
        type="button"
      >
        {t.marketplace.turnOn}
      </button>
    )
  }

  const labels: Record<typeof action, string> = {
    installed: t.marketplace.installed,
    on: t.marketplace.on,
    retired: t.marketplace.retired,
    unavailable: t.marketplace.unavailable
  }

  return (
    <span
      className={`text-xs ${action === 'retired' || action === 'unavailable' ? 'text-waiting-text' : 'text-muted-foreground'}`}
      data-action={action}
      data-testid="marketplace-action"
      title={row.problem ?? undefined}
    >
      {labels[action]}
    </span>
  )
}

/** An update that would replace what the home changed: the user's word first. */
function Confirm({ name }: { name: string }) {
  return (
    <div
      className="space-y-2 rounded-md border border-waiting/40 bg-waiting/10 p-3 text-sm"
      data-testid="marketplace-confirm"
    >
      <p>{t.marketplace.localChanges(name)}</p>
      <div className="flex gap-2">
        <button
          className={PRIMARY}
          data-testid="marketplace-update-anyway"
          onClick={() => {
            actOnMarket('update', name, true)
          }}
          type="button"
        >
          {t.marketplace.updateAnyway}
        </button>
        <button className={BUTTON} onClick={cancelMarketUpdate} type="button">
          {t.marketplace.cancel}
        </button>
      </div>
    </div>
  )
}

function Picture({ row, size }: { row: MarketplaceRow; size: number }) {
  if (row.image) {
    return (
      <img alt="" className="shrink-0 rounded-md object-cover" src={row.image} style={{ height: size, width: size }} />
    )
  }

  return <KindPicture glyph={row.kind === 'executor' && row.shipped ? Robot : undefined} kind={row.kind} size={size} />
}

function Failed({ text }: { text: string }) {
  return (
    <p className="rounded-md bg-failed/10 px-3 py-2 text-sm text-failed-text" role="alert">
      {text}
    </p>
  )
}

const BUTTON = 'rounded-md border border-border-strong px-2 py-1 text-sm hover:bg-muted disabled:opacity-40'
const PRIMARY =
  'rounded-md bg-primary px-3 py-1 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40'

/** A UTC time as the user's local date and time. */
function when(utc: string): string {
  return new Date(utc).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}
