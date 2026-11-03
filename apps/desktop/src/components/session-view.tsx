import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { t } from '@/i18n'
import { PlaceContributions } from '@/plugins/view'
import type { ExecutorModel, ExecutorRow, InstanceRow } from '@/protocol/protocol.generated'
import {
  $cards,
  $connection,
  $executorModels,
  $openSession,
  $read,
  $sessionError,
  $sessionSettings,
  $sessionThread,
  $slashCommands,
  $workbench,
  chooseElsewhere,
  type OpenSession,
  readExecutorCommands,
  runUserCommand,
  sendToSession,
  sessionOwner,
  setSessionMode,
  setSessionModel,
  setSessionSwitch,
  showExtensions,
  slashKey,
  stopSession
} from '@/store/app'
import { fromNames } from '@/store/slash'
import { EMPTY_THREAD } from '@/store/thread'
import { waitingCalls } from '@/store/waiting'

import { WherePill } from './environment'
import { type ExecutorCard, ExecutorCardView } from './executor-cards'
import { baseName, ConsentAgain, TooOld } from './executors-list'
import { HarnessAutoWarning, useHarnessAuto } from './harness-auto'
import { MessageBox } from './message-box'
import { ModeMenu } from './mode-menu'
import { PlanRing } from './plan-ring'
import { ThreadView } from './thread-view'
import { PillSelect } from './ui/pill'

/**
 * An executor's session as a conversation (step 16f, decided with the user on 15/10/2026): the
 * trail of Atlas's own (step 9c), what its harness kept and what it does live. The workbench card
 * of step 16c comes apart into the usual places: its prompt is the box, which steers while it
 * works; its model and effort sit above the box and its automatic mode is "Auto", as in Atlas's
 * conversation (one box for every conversation, asked for by the user on 16/10/2026, when
 * "Atlas answers" left the screen); what it asks shows in the conversation; it stops from the box,
 * in send's place while it works. Its account and its route are the accounts plugin's, by the box
 * (step 22); each account's login switches from its row in the sidebar. Its executor not running,
 * the tab says why
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
          {/* The account and the route, the accounts plugin's (step 22). */}
          <PlaceContributions
            area="session"
            place={{
              conversation: instance?.transcript_id ?? null,
              executor: instance?.executor ?? open.executor,
              folder: open.folder,
              instance: instance ?? null
            }}
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
