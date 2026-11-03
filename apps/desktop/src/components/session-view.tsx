import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import type {
  AccountRow,
  AccountSwitchRow,
  ExecutorModel,
  ExecutorRow,
  InstanceRow,
  RouteTargetParams,
  SessionRouteRow
} from '@/protocol/protocol.generated'
import {
  $cards,
  $connection,
  $executorAccounts,
  $executorModels,
  $executorUsage,
  $openSession,
  $read,
  $sessionError,
  $sessionFolders,
  $sessionRoute,
  $sessionSettings,
  $sessionThread,
  $slashCommands,
  $workbench,
  checkSessionAccount,
  chooseElsewhere,
  type OpenSession,
  readExecutorCommands,
  readExecutorModels,
  readSessionRoute,
  readUsageIfStale,
  runUserCommand,
  sendToSession,
  sessionOwner,
  setSessionMode,
  setSessionModel,
  setSessionRoute,
  setSessionSwitch,
  showExtensions,
  slashKey,
  stopSession,
  switchSessionAccount
} from '@/store/app'
import { fromNames } from '@/store/slash'
import { EMPTY_THREAD } from '@/store/thread'
import type { UsageRead } from '@/store/usage'
import { waitingCalls } from '@/store/waiting'

import { WherePill } from './environment'
import { type ExecutorCard, ExecutorCardView } from './executor-cards'
import { baseName, ConsentAgain, TooOld } from './executors-list'
import { HarnessAutoWarning, useHarnessAuto } from './harness-auto'
import { MessageBox } from './message-box'
import { ModeMenu } from './mode-menu'
import { PlanRing } from './plan-ring'
import { ThreadView } from './thread-view'
import { ArrowDown, ArrowUp, Close, Route } from './ui/icons'
import { PILL, PILL_ON, PillSelect } from './ui/pill'
import { IconButton } from './ui/tip'

/**
 * An executor's session as a conversation (step 16f, decided with the user on 15/10/2026): the
 * trail of Atlas's own (step 9c), what its harness kept and what it does live. The workbench card
 * of step 16c comes apart into the usual places: its prompt is the box, which steers while it
 * works; its model and effort sit above the box and its automatic mode is "Auto", as in Atlas's
 * conversation (one box for every conversation, asked for by the user on 16/10/2026, when
 * "Atlas answers" left the screen); what it asks shows in the conversation; it stops from the box,
 * in send's place while it works. Its account is a pill beside its model (step 16h): switching goes
 * on with the conversation in a copy on the other account, after the user read what changes; each
 * account's login switches from its row in the sidebar. Its executor not running, the tab says why
 * and where to turn it on, where its session used to be read forever (found on 01/11/2026, when an
 * update left Claude Code waiting for the user's consent again).
 */
export function SessionView() {
  const open = useStore($openSession)
  const thread = useStore($sessionThread)
  const cards = useStore($cards)
  const { executors, instances } = useStore($workbench)
  const error = useStore($sessionError)
  const read = useStore($read).workbench
  const instance = open?.instanceId ? instances.find(row => row.instance_id === open.instanceId) : undefined
  const live = instance !== undefined && instance.state !== 'ended'
  const executor = open ? executors.find(row => row.name === open.executor) : undefined
  // An executor on another account is its harness's: it waits when its harness does.
  const harness = open ? (open.executor.split('@')[0] ?? open.executor) : ''
  const own = executors.find(row => row.name === harness && !row.base)
  const stopped = read && !live && !(executor?.enabled && executor.installed && executor.supported)

  if (!open) {
    return null
  }

  const title = executor?.title ?? own?.title ?? open.executor
  const asked = cards.filter(
    (card): card is ExecutorCard =>
      (card.kind === 'executor-approval' || card.kind === 'executor-question') &&
      live &&
      card.params.instance_id === open.instanceId
  )
  const elsewhere = !live && open.elsewhere !== '' && open.choice === null
  const readOnly = open.expired || open.choice === 'read'

  if (stopped) {
    return (
      <>
        <NotRunning own={own} title={title} />
      </>
    )
  }

  return (
    <>
      {elsewhere && <Elsewhere where={open.elsewhere} />}
      <ThreadView
        empty={
          <p className="pt-24 text-center text-muted-foreground">
            {open.sessionId ? '' : t.executors.emptySession(title, baseName(open.folder))}
          </p>
        }
        // A session kept by its harness is read as it opens: never shown as an empty one meanwhile.
        loading={open.sessionId !== '' && thread === EMPTY_THREAD && !error}
        place={sessionOwner(open)}
        thread={thread}
        waiting={waitingCalls(asked, thread.items)}
      />
      {(asked.length > 0 || error) && (
        <div className="mx-auto max-h-[50vh] w-full max-w-3xl space-y-3 overflow-y-auto px-6 pb-3">
          {asked.map(card => (
            <ExecutorCardView card={card} key={card.id} />
          ))}
          {error && (
            <p className="text-sm text-failed-text" data-testid="session-error">
              {error}
            </p>
          )}
        </div>
      )}
      {readOnly ? (
        <p
          className="border-t border-border p-4 text-center text-sm text-muted-foreground"
          data-testid="session-read-only"
        >
          {open.expired ? t.executors.expired(title) : t.executors.readOnly}
        </p>
      ) : (
        !elsewhere && <SessionComposer instance={live ? instance : undefined} open={open} title={title} />
      )}
    </>
  )
}

/** Why the session's executor is not running, in the conversation's place, as the sidebar says it:
 * waiting for the user's consent again after an update, or too old, with its update; otherwise
 * turned off or not found, with the way to Extensions. `own` is its harness's own row. */
function NotRunning({ own, title }: { own: ExecutorRow | undefined; title: string }) {
  return (
    <div
      className="flex flex-1 items-start justify-center overflow-y-auto px-6 pt-24"
      data-testid="session-not-running"
    >
      <div className="w-full max-w-md">
        {own?.consent_again ? (
          <ConsentAgain row={own} />
        ) : own?.enabled && own.installed && !own.supported ? (
          <TooOld row={own} />
        ) : (
          <div className="space-y-1 text-center text-sm">
            <p className="font-medium">{t.executors.notRunning(title)}</p>
            <p className="text-muted-foreground">{t.executors.notRunningBody}</p>
            <button
              className="link"
              data-testid="session-open-extensions"
              onClick={() => {
                showExtensions('executor')
              }}
              type="button"
            >
              {t.executors.toExtensions}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

/** The session is open in another program (decided with the user on 15/10/2026): a warning, with
 * the ways out. */
function Elsewhere({ where }: { where: string }) {
  const place = t.executors.places[where] ?? where

  return (
    <div className="mx-auto mt-4 w-full max-w-3xl px-6" data-testid="session-elsewhere">
      <div className="space-y-2 rounded-xl border border-waiting/40 bg-waiting/10 p-4 text-sm">
        <p className="font-semibold">⚠ {t.executors.elsewhereTitle(place)}</p>
        <p className="text-soft">{t.executors.elsewhereBody}</p>
        <div className="flex flex-wrap gap-2">
          <button
            className="rounded border border-border-strong bg-raised px-3 py-1"
            onClick={() => {
              chooseElsewhere('read')
            }}
            type="button"
          >
            {t.executors.elsewhereRead}
          </button>
          <button
            className="rounded bg-primary px-3 py-1 font-medium text-primary-foreground"
            onClick={() => {
              chooseElsewhere('copy')
            }}
            type="button"
          >
            {t.executors.elsewhereCopy}
          </button>
          <button
            className="rounded border border-border-strong bg-raised px-3 py-1"
            onClick={() => {
              chooseElsewhere('here')
            }}
            type="button"
          >
            {t.executors.elsewhereHere}
          </button>
        </div>
      </div>
    </div>
  )
}

/** The session's box, the one `MessageBox`: a prompt starts a turn, or steers the one running; `/`
 * offers the commands its harness says its channel takes (Claude Code's `slash_commands`); "Auto"
 * is the harness's own automatic mode, where Atlas's conversation has its own, on only after its
 * warning is read and confirmed. It takes files as Atlas's box does when its executor says it
 * does (step 16i): the same chips, sent with the prompt. */
function SessionComposer({
  instance,
  open,
  title
}: {
  instance: InstanceRow | undefined
  open: OpenSession
  title: string
}) {
  const { executors, instances } = useStore($workbench)
  const executor = executors.find(row => row.name === open.executor)
  const settings = useStore($sessionSettings)
  const { state } = useStore($connection)
  const working = instance?.state === 'working'
  // As its harness describes them in this folder, read when the session opens and again when its
  // instance's commands change; meanwhile the names its card says, or the last instance's.
  const names =
    instance?.commands ??
    instances.findLast(row => row.executor === open.executor && row.commands.length)?.commands ??
    []
  const described = useStore($slashCommands)[slashKey(open.executor, open.folder)]
  const commands = described ?? fromNames(names)
  const signature = instance?.commands.join(' ') ?? ''
  const instanceId = instance?.instance_id ?? ''

  useEffect(() => {
    readExecutorCommands(open.executor, open.folder, instanceId, signature)
  }, [open.executor, open.folder, instanceId, signature])
  const auto = useHarnessAuto(on => void setSessionSwitch('auto', on))
  const [switching, setSwitching] = useState<null | Switching>(null)
  const [routing, setRouting] = useState(false)
  const pick = (account: string, name: string) => {
    setRouting(false)
    setSwitching({ account, check: null, name })
    void checkSessionAccount(account).then(check => {
      setSwitching(check ? { account, check, name } : null)
    })
  }

  // A harness that lists its permission modes has them in a menu, its automatic one among them.
  const modes = instance?.modes.length ? instance.modes : (executor?.modes ?? [])
  const mode = instance
    ? instance.mode
    : settings.auto && modes.includes('auto')
      ? 'auto'
      : (settings.mode ?? modes[0] ?? '')

  return (
    <MessageBox
      auto={
        modes.length === 0 && (instance?.can_auto ?? true)
          ? {
              label: t.executors.auto,
              on: instance ? instance.auto : settings.auto,
              set: auto.toggle,
              title: t.executors.autoNote
            }
          : undefined
      }
      blocked={working && !instance.steer}
      commands={commands}
      connected={state === 'open'}
      // A new session's words are its folder's until it has an id.
      draftKey={open.sessionId || open.instanceId ? sessionOwner(open) : `session-new:${open.executor}:${open.folder}`}
      label={t.executors.placeholder(title)}
      note={
        auto.asking ? (
          <HarnessAutoWarning onCancel={auto.cancel} onConfirm={auto.confirm} title={title} />
        ) : switching ? (
          <AccountSwitchNote
            onCancel={() => {
              setSwitching(null)
            }}
            onConfirm={() => {
              setSwitching(null)
              void switchSessionAccount(
                switching.account,
                t.accounts.switched(switching.name, switching.check?.tokens ?? null)
              )
            }}
            switching={switching}
          />
        ) : routing ? (
          <SessionAccountRouteEditor
            instance={instance}
            onClose={() => {
              setRouting(false)
            }}
            onPick={pick}
            open={open}
          />
        ) : open.choice === 'copy' && !instance ? (
          <p className="text-xs text-waiting-text" data-testid="session-copy-note">
            {t.executors.copyNote}
          </p>
        ) : undefined
      }
      onCommand={command => void runUserCommand(command)}
      onSend={(text, attachments) => sendToSession(text, attachments)}
      onStop={working ? () => void stopSession() : undefined}
      owner={executor?.files ? sessionOwner(open) : undefined}
      placeholder={working ? t.executors.placeholderWorking : t.executors.placeholder(title)}
      testId="session-box"
      toolbar={
        <>
          <WherePill executor={open.executor} folder={open.folder} />
          <SessionAccountRoute
            instance={instance}
            on={routing}
            onToggle={() => {
              setRouting(!routing)
            }}
            open={open}
          />
          <SessionModelPicker instance={instance} open={open} />
          {modes.length > 0 && (
            <ModeMenu
              current={mode}
              disabled={state !== 'open'}
              modes={modes}
              onPick={picked => {
                if (picked === 'auto') {
                  // Its automatic mode is read about first, as the switch's was.
                  auto.toggle(true)
                } else {
                  void setSessionMode(picked)
                }
              }}
            />
          )}
          <span className="ml-auto">
            <PlanRing executor={usageKey(executors, instance, open, settings.account)} />
          </span>
        </>
      }
    />
  )
}

/** The session's account and route as one pill by its box (asked for by the user on 22/10/2026,
 * where each had its own, the route's first stop being the account): the account it runs on, once
 * its executor has another, and how many destinations its route has (asked for by the user on
 * 21/10/2026); pressed, both are chosen above the box. */
function SessionAccountRoute({
  instance,
  on,
  onToggle,
  open
}: {
  instance: InstanceRow | undefined
  on: boolean
  onToggle: () => void
  open: OpenSession
}) {
  const accounts = useSessionAccounts(instance, open)
  const route = useStore($sessionRoute)
  const { state } = useStore($connection)
  const instanceId = instance?.instance_id ?? ''

  useEffect(() => {
    if (state === 'open' && instanceId) {
      void readSessionRoute(instanceId)
    }
  }, [instanceId, state])

  if (!accounts && !instanceId) {
    return null
  }

  const count = instanceId && route?.instance_id === instanceId && route.enabled ? route.stops.length : 0

  return (
    <button
      aria-pressed={on}
      className={`${PILL} ${on ? PILL_ON : ''}`}
      data-testid="session-route"
      disabled={state !== 'open'}
      onClick={onToggle}
      title={accounts ? t.route.titleOn : t.route.title}
      type="button"
    >
      <Route aria-hidden size={14} stroke={1.75} />
      {accounts ? t.route.pillOn(accounts.named(accounts.chosen), count) : t.route.pill(count)}
    </button>
  )
}

/** What the pill opens above the box: the session's account, then its route once it runs. It
 * closes as a popover does (asked for by the user on 22/10/2026): a click outside it, Escape, or
 * its own button; the pill still toggles it. */
function SessionAccountRouteEditor({
  instance,
  onClose,
  onPick,
  open
}: {
  instance: InstanceRow | undefined
  onClose: () => void
  onPick: (account: string, name: string) => void
  open: OpenSession
}) {
  const accounts = useSessionAccounts(instance, open)
  const { state } = useStore($connection)
  const locked = state !== 'open' || instance?.state === 'working'
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const outside = (event: PointerEvent) => {
      const target = event.target

      if (
        target instanceof Node &&
        !panel.current?.contains(target) &&
        !(target instanceof Element && target.closest('[data-testid="session-route"]'))
      ) {
        onClose()
      }
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose()
      }
    }
    document.addEventListener('pointerdown', outside)
    window.addEventListener('keydown', escape)

    return () => {
      document.removeEventListener('pointerdown', outside)
      window.removeEventListener('keydown', escape)
    }
  }, [onClose])

  return (
    <div className="relative space-y-3 pr-7" data-testid="session-route-panel" ref={panel}>
      <IconButton
        className="absolute top-0 right-0"
        icon={Close}
        label={t.route.close}
        onClick={onClose}
        testId="session-route-close"
      />
      {accounts && (
        <fieldset className="space-y-1 text-xs" data-testid="session-account">
          <legend className="pb-1 font-medium">{t.accounts.sessionTitle}</legend>
          {accounts.listed.map(found => (
            <label className="flex items-center gap-2" key={found.account_id}>
              <input
                checked={found.account_id === accounts.chosen}
                data-account={found.account_id}
                data-testid="session-account-choice"
                disabled={locked}
                name="session-account"
                onChange={() => {
                  onPick(found.account_id, accounts.named(found.account_id))
                }}
                type="radio"
              />
              {found.account_id
                ? found.nickname
                : found.login
                  ? `${found.login} · ${t.accounts.main}`
                  : t.accounts.mainAccount}
            </label>
          ))}
          <p className="text-muted-foreground">{locked ? t.accounts.sessionBusy : t.accounts.session}</p>
        </fieldset>
      )}
      {instance && <SessionRouteEditor instance={instance} />}
    </div>
  )
}

/** A destination the route may take: an executor on one of its accounts, as its row names it. */
interface RouteChoice {
  key: string
  label: string
  target: RouteTargetParams
}

/** The executors' rows as destinations: each harness on each of its accounts, on and installed. */
function routeChoices(executors: ExecutorRow[]): RouteChoice[] {
  return executors
    .filter(row => row.installed && row.enabled)
    .map(row => {
      const executor = row.base || row.name

      return {
        key: row.name,
        label: row.base ? row.title : `${row.title} · ${t.route.main}`,
        target: { account_id: row.account_id, effort: null, executor, model: null }
      }
    })
}

/**
 * The session's route as a path (asked for by the user on 21/10/2026, redone as a path on
 * 25/10/2026): the stops it goes on to, in order, when the account it runs on reaches its plan's
 * limit, the one it runs on marked; each with whether it can take over (installed, logged in, not at
 * its own limit), how much of its plan is used, and the model and the effort the session goes on
 * with there, kept as they are unless one is chosen; whether the path is followed, and whether a
 * copy on another account compacts first (on by default). Each change is saved at once, for the
 * session's card.
 */
function SessionRouteEditor({ instance }: { instance: InstanceRow }) {
  const route = useStore($sessionRoute)
  const { executors } = useStore($workbench)
  const instanceId = instance.instance_id
  const stops = route?.instance_id === instanceId ? route.stops : null

  // What each stop offers and how much of its plan is used, read as the path opens.
  const names = stops?.map(stop => (stop.installed ? stop.name : '')).join('\n') ?? ''
  useEffect(() => {
    for (const name of names.split('\n').filter(Boolean)) {
      readExecutorModels(name)
      readUsageIfStale(name)
    }
  }, [names])

  if (!route || !stops) {
    return (
      <p className="text-xs text-muted-foreground" role="status">
        …
      </p>
    )
  }

  const targets = stops.map(stop => ({
    account_id: stop.account_id,
    effort: stop.effort ?? null,
    executor: stop.executor,
    model: stop.model ?? null
  }))
  const save = (changed: RouteTargetParams[], switches: { compact?: boolean; enabled?: boolean } = {}): void => {
    void setSessionRoute(instanceId, {
      compact: switches.compact ?? route.compact,
      enabled: switches.enabled ?? route.enabled,
      targets: changed
    })
  }
  const move = (index: number, by: number): void => {
    const changed = [...targets]
    const [row] = changed.splice(index, 1)

    if (row) {
      changed.splice(index + by, 0, row)
      save(changed)
    }
  }
  const others = routeChoices(executors).filter(choice => !stops.some(stop => stop.name === choice.key))

  return (
    <div className="space-y-2 text-xs" data-testid="session-route-editor">
      <p className="font-medium">{t.route.title}</p>
      <p className="text-muted-foreground">{stops.length ? t.route.note : t.route.empty}</p>
      <ol className="space-y-1.5" data-testid="session-route-stops">
        {stops.map((stop, index) => (
          <PathStop
            here={stop.name === instance.executor}
            index={index}
            key={stop.name}
            last={index === stops.length - 1}
            onChange={(model, effort) => {
              save(targets.map((target, at) => (at === index ? { ...target, effort, model } : target)))
            }}
            onMove={by => {
              move(index, by)
            }}
            onRemove={() => {
              save(targets.filter((_, at) => at !== index))
            }}
            row={executors.find(row => row.name === stop.name)}
            sameHarness={stop.executor === (instance.executor.split('@')[0] ?? instance.executor)}
            stop={stop}
          />
        ))}
      </ol>
      {others.length > 0 && (
        <select
          aria-label={t.route.addStop}
          className="w-full rounded-md border border-border-strong bg-raised px-2 py-1"
          data-testid="session-route-others"
          onChange={event => {
            const choice = others.find(found => found.key === event.target.value)

            if (choice) {
              save([...targets, choice.target])
            }
          }}
          value=""
        >
          <option value="">{t.route.addStop}</option>
          {others.map(choice => (
            <option key={choice.key} value={choice.key}>
              {choice.label}
            </option>
          ))}
        </select>
      )}
      <label className="flex items-center gap-2">
        <input
          checked={route.enabled}
          data-testid="session-route-enabled"
          onChange={event => {
            save(targets, { enabled: event.target.checked })
          }}
          type="checkbox"
        />
        {t.route.enabled}
      </label>
      <label className="flex items-start gap-2">
        <input
          checked={route.compact}
          className="mt-0.5"
          data-testid="session-route-compact"
          onChange={event => {
            save(targets, { compact: event.target.checked })
          }}
          type="checkbox"
        />
        <span>
          {t.route.compact}
          <span className="block text-muted-foreground">{t.route.compactHint}</span>
        </span>
      </label>
    </div>
  )
}

type PathStopProps = {
  /** The stop the session runs on now. */
  here: boolean
  index: number
  last: boolean
  onChange: (model: null | string, effort: null | string) => void
  onMove: (by: number) => void
  onRemove: () => void
  /** Its executor's row, which says whether it is logged in; none, not installed. */
  row: ExecutorRow | undefined
  /** On the harness the session runs on: "as now" keeps the model it runs on there. */
  sameHarness: boolean
  stop: SessionRouteRow['stops'][number]
}

/** One stop of the path: its place, whether it can take over, how much of its plan is used, and
 * the model and the effort the session goes on with there. */
function PathStop({ here, index, last, onChange, onMove, onRemove, row, sameHarness, stop }: PathStopProps) {
  const offered = useStore($executorModels)[stop.name] ?? []
  const used = usedOf(useStore($executorUsage)[stop.name])
  const label = stop.nickname ? `${stop.title} · ${stop.nickname}` : `${stop.title} · ${t.route.main}`
  const state = here
    ? t.route.here
    : !stop.installed || !row
      ? t.route.notInstalled
      : !row.logged_in
        ? t.route.notLoggedIn
        : stop.limited
          ? t.route.atLimit
          : t.route.ready
  const ready = here || state === t.route.ready
  const model = offered.find(found => found.id === stop.model)
  const efforts = (model ?? offered[0])?.efforts ?? []

  return (
    <li
      className={`relative space-y-1 pl-5 ${ready ? '' : 'text-muted-foreground'}`}
      data-here={here || undefined}
      data-testid="session-route-stop"
    >
      {/* The path: a dot per stop, joined to the next. */}
      {!last && <span aria-hidden className="absolute top-3 bottom-[-0.5rem] left-[0.3rem] w-px bg-border-strong" />}
      <span
        aria-hidden
        className={`absolute top-1 left-0 h-2.5 w-2.5 rounded-full border-2 ${
          here
            ? 'border-foreground bg-foreground'
            : ready
              ? 'border-foreground bg-background'
              : 'border-faint bg-background'
        }`}
      />
      <div className="flex items-center gap-1">
        <span className="min-w-0 flex-1 truncate font-medium" data-testid="session-route-name">
          {index + 1}. {label}
        </span>
        <span className="shrink-0" data-testid="session-route-ready">
          {used === null ? state : `${state} · ${t.route.used(used)}`}
        </span>
        <IconButton
          disabled={index === 0}
          icon={ArrowUp}
          label={t.route.up(label)}
          onClick={() => {
            onMove(-1)
          }}
        />
        <IconButton
          disabled={last}
          icon={ArrowDown}
          label={t.route.down(label)}
          onClick={() => {
            onMove(1)
          }}
        />
        <IconButton icon={Close} label={t.route.remove(label)} onClick={onRemove} />
      </div>
      {offered.length > 0 && (
        <div className="flex flex-wrap gap-1">
          <PillSelect
            aria-label={t.route.model(label)}
            data-testid="session-route-model"
            onChange={event => {
              const next = offered.find(found => found.id === event.target.value)
              // An effort the model does not take goes back to "as now".
              const kept = stop.effort && next?.efforts.includes(stop.effort) ? stop.effort : null
              onChange(event.target.value || null, kept)
            }}
            value={stop.model ?? ''}
          >
            <option value="">{sameHarness ? t.route.same : t.route.itsDefault}</option>
            {offered.map(found => (
              <option key={found.id} title={found.description} value={found.id}>
                {found.name}
              </option>
            ))}
          </PillSelect>
          {efforts.length > 0 && (
            <PillSelect
              aria-label={t.route.effort(label)}
              data-testid="session-route-effort"
              onChange={event => {
                onChange(stop.model ?? null, event.target.value || null)
              }}
              value={stop.effort ?? ''}
            >
              <option value="">{t.route.same}</option>
              {efforts.map(level => (
                <option key={level} value={level}>
                  {t.composer.effortLevel(level)}
                </option>
              ))}
            </PillSelect>
          )}
        </div>
      )}
    </li>
  )
}

/** How much of a stop's plan is used: its most used limit of every model, as last read. */
function usedOf(read: undefined | UsageRead): null | number {
  const windows = read?.usage?.windows.filter(window => !window.model) ?? []

  return windows.length ? Math.round(Math.max(...windows.map(window => window.used))) : null
}

/** A switch of the session's account asked for, while what changes is read and then shown. */
interface Switching {
  account: string
  /** `null` while it is read. */
  check: AccountSwitchRow | null
  name: string
}

/**
 * The session's accounts (step 16h, decided with the user on 18/10/2026 and redone on 19/10/2026),
 * once its executor has another one: the main account by its login, then the others by their
 * nicknames. A pick with an instance running is confirmed first (`AccountSwitchNote`); with none,
 * the next instance starts on it. Before a pick, a session not started shows its folder's account.
 * `null`, with only one.
 */
/** Whose plan the session runs on: its instance's account, or the one chosen for its first
 * prompt, named as its executor row (`claude-code@<id>` for another account). */
function usageKey(
  executors: ExecutorRow[],
  instance: InstanceRow | undefined,
  open: OpenSession,
  chosen: null | string | undefined
): string {
  if (instance) {
    return instance.executor
  }

  const row = executors.find(found => found.name === open.executor)
  const base = row?.base || open.executor
  const account = chosen ?? row?.account_id ?? ''

  return executors.find(found => (found.base || found.name) === base && found.account_id === account)?.name ?? base
}

function useSessionAccounts(
  instance: InstanceRow | undefined,
  open: OpenSession
): null | { chosen: string; listed: AccountRow[]; named: (account: string) => string } {
  const { executors } = useStore($workbench)
  const row = executors.find(found => found.name === open.executor)
  const base = row?.base || open.executor
  const read = useStore($executorAccounts)[base]
  const settings = useStore($sessionSettings)
  const folders = useStore($sessionFolders)[open.executor]?.folders
  const listed = read?.accounts ?? []

  if (listed.length < 2) {
    return null
  }

  const live = instance !== undefined && instance.state !== 'ended' && instance.state !== 'failed'
  const folder = folders?.find(found => found.folder === open.folder)?.account_id ?? ''
  const chosen = live ? instance.account_id : (settings.account ?? (row?.account_id || folder))
  const named = (account: string) => {
    const found = listed.find(found => found.account_id === account)

    return found?.account_id ? found.nickname : found?.login || t.accounts.mainAccount
  }

  return { chosen, listed, named }
}

/** What a switch of account changes, for the user to read before it happens (decided with the user
 * on 19/10/2026): the account that gets the conversation, the tools it lacks, the copy, and the
 * first message read again on its plan. */
function AccountSwitchNote({
  onCancel,
  onConfirm,
  switching
}: {
  onCancel: () => void
  onConfirm: () => void
  switching: Switching
}) {
  const { check, name } = switching
  const who = check?.login ? `${name} (${check.login})` : name

  return (
    <div className="space-y-1 text-xs" data-testid="account-switch" role="alertdialog">
      <p className="font-medium">{t.accounts.switchTitle(who)}</p>
      {check === null ? (
        <p className="text-muted-foreground" role="status">
          {t.accounts.switchChecking}
        </p>
      ) : (
        <>
          <p className="text-soft">{t.accounts.switchCopy}</p>
          {check.missing.length > 0 && (
            <p className="text-waiting-text" data-testid="account-switch-missing">
              {t.accounts.switchMissing(check.missing.join(', '))}
            </p>
          )}
          {!check.compared && <p className="text-muted-foreground">{t.accounts.switchUnknown}</p>}
          <p className="text-soft">{t.accounts.switchCost(check.tokens ?? null)}</p>
        </>
      )}
      <div className="flex gap-2">
        <button
          className="rounded bg-primary px-2 py-0.5 font-medium text-primary-foreground disabled:opacity-50"
          data-testid="account-switch-confirm"
          disabled={check === null}
          onClick={onConfirm}
          type="button"
        >
          {t.accounts.switchGo}
        </button>
        <button className="rounded px-2 py-0.5 text-muted-foreground" onClick={onCancel} type="button">
          {t.accounts.cancel}
        </button>
      </div>
    </div>
  )
}

/**
 * The session's model and effort, above its box where Atlas's conversation has its own (a model
 * pill and an effort pill): the models its harness's `/model` lists for the account, and the
 * efforts of the one chosen, gone for a model that takes none. A pick applies from the instance's
 * next request, or to the instance the next prompt starts; the first, the harness's default model
 * on Atlas's default effort. Both show what runs, never "Default" (asked for by the user on
 * 17/10/2026): the harness's default model by the name of the model it runs as, and the defaults
 * marked in the lists.
 */
function SessionModelPicker({ instance, open }: { instance: InstanceRow | undefined; open: OpenSession }) {
  const offered = useStore($executorModels)[open.executor]
  const settings = useStore($sessionSettings)
  const { state } = useStore($connection)

  if (!offered?.length || (instance && !instance.can_model)) {
    return null
  }

  const chosen = instance ? instance.chosen_model || null : settings.model
  const offer = modelOffer(offered, chosen, instance?.model ?? '')
  const effort = (instance ? instance.effort : settings.effort) || offer.current.default_effort || null

  const pickModel = (id: string) => {
    const next = offer.options.find(option => option.model.id === id)?.model
    // An effort the new model does not take goes back to Atlas's default for it.
    const kept = effort && next?.efforts.includes(effort) ? effort : null
    void setSessionModel(id === offered[0]?.id ? null : id, kept)
  }

  return (
    <div className="flex items-center gap-2">
      <PillSelect
        aria-label={t.executors.model}
        data-testid="session-model"
        disabled={state !== 'open'}
        onChange={event => {
          pickModel(event.target.value)
        }}
        title={offer.current.description || t.executors.model}
        value={offer.current.id}
      >
        {offer.options.map(({ label, model }) => (
          <option key={model.id} title={model.description} value={model.id}>
            {label}
          </option>
        ))}
      </PillSelect>
      {offer.current.efforts.length > 0 && (
        <PillSelect
          aria-label={t.executors.effort}
          data-testid="session-effort"
          disabled={state !== 'open'}
          onChange={event => {
            void setSessionModel(chosen, event.target.value || null)
          }}
          title={t.executors.effort}
          value={effort ?? ''}
        >
          {effort === null && <option value="" />}
          {offer.current.efforts.map(level => (
            <option key={level} value={level}>
              {level === offer.current.default_effort
                ? t.composer.defaulted(t.composer.effortLevel(level))
                : t.composer.effortLevel(level)}
            </option>
          ))}
        </PillSelect>
      )}
      {offer.ranAs && (
        <span className="text-muted-foreground" data-testid="session-ran-as">
          {t.executors.runsAs(offer.ranAs)}
        </span>
      )}
    </div>
  )
}

/** What the session's picker shows: the model chosen (the harness's default, the first, when none;
 * one the list lacks, added), and what its last turn ran as when that is not what the pick names
 * (a model from the user's own settings, say). The default goes by the name of the model it runs
 * as, marked, in the place of the other entry for that model (Claude Code's "Default
 * (recommended)" is "Opus 5.5 · default", and its own "Opus 5.5" is not listed twice). */
export function modelOffer(
  models: ExecutorModel[],
  chosen: null | string,
  ran: string
): { current: ExecutorModel; options: { label: string; model: ExecutorModel }[]; ranAs: string } {
  const found = models.find(model => model.id === chosen)
  const extra = (id: string): ExecutorModel => ({
    default_effort: '',
    description: '',
    efforts: [],
    id,
    name: id,
    resolved: ''
  })
  const current = found ?? (chosen ? extra(chosen) : models[0]) ?? extra('')
  const listed = models.includes(current) ? models : [...models, current]
  const first = models[0]
  const same = (model: ExecutorModel) =>
    first !== undefined && model !== first && first.resolved !== '' && model.resolved === first.resolved
  const named = models.find(same)?.name ?? first?.name ?? ''
  const options = listed
    .filter(model => !same(model) || model === current)
    .map(model => ({ label: model === first ? t.composer.defaulted(named) : model.name, model }))
  const ranAs = ran && ran !== current.resolved && ran !== current.id ? ran : ''

  return { current, options, ranAs }
}
