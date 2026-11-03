import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useState } from 'react'

import { t } from '@/i18n'
import { $contributions, inArea } from '@/plugins/registry'
import { PlaceContributions } from '@/plugins/view'
import type { ExecutorRow } from '@/protocol/protocol.generated'
import {
  $home,
  $keyLater,
  $keyNotice,
  $keyProviders,
  $logins,
  $modelOptions,
  $pairings,
  $pluginError,
  $plugins,
  $sessionFolders,
  $sessions,
  $workbench,
  approvePairing,
  chooseKeyLater,
  dismissKeyNotice,
  enablePlugin,
  keyScreenShown,
  logInToGithub,
  newConversation,
  refreshLogins,
  refreshPlugins,
  send,
  showPluginConsent,
  storePluginSecret
} from '@/store/app'
import {
  $firstRun,
  closeFirstRun,
  finishStep,
  firstRunOf,
  type Mark,
  openFirstRun,
  showStep,
  type Step,
  stepAway,
  STEPS,
  WINDOWS,
  type WorkWindow
} from '@/store/setup'
import { bringWork, readPlan, readWork } from '@/store/work'

import icon from '../../resources/icon.png'

import { LoginForm } from './executor-accounts'
import { ExecutorAvatar } from './executor-avatar'
import { ExecutorOffers } from './executor-offers'
import { Providers } from './key-screen'
import { Check } from './ui/icons'
import { InlineError } from './ui/inline-error'

/**
 * The first run (step 18d, docs/ARCHITECTURE.md, The installer): the app's first steps, which
 * replaced the key screen as the window's first sight on 01/11/2026, asked for by the user so a
 * first run says what Atlas is: a personal assistant, and the coding harnesses it drives with
 * accounts that take over from one another. The steps are numbered on the left, each saying what
 * became of it; one is shown at a time, each can be skipped, and "Go to Atlas" leaves at any point.
 * Leaving with no key stored leaves the key band, as "choose later" did.
 */
export function FirstRun() {
  const home = useStore($home)
  const run = firstRunOf(useStore($firstRun), home)
  const step = run.step
  const index = STEPS.indexOf(step)
  const noKey = keyScreenShown(useStore($modelOptions), useStore($keyLater), null)

  // Opened because no key is stored, it stays open once one is: the steps after it follow. Opened
  // only to say what a key's check found (a key stored from the settings), it is not.
  useEffect(() => {
    if (!run.open && noKey) {
      openFirstRun(home)
    }
  }, [home, noKey, run.open])

  const leave = () => {
    chooseKeyLater(true)
    closeFirstRun(home)
  }
  const finish = (mark: Mark) => {
    if (step === STEPS[STEPS.length - 1]) {
      chooseKeyLater(true)
    }
    finishStep(home, step, mark)
  }

  return (
    <div className="flex min-h-0 flex-1" data-step={step} data-testid="first-run">
      <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-subtle p-5">
        <div className="flex items-center gap-2 pb-8">
          <img alt="" className="h-7 w-7 rounded-md" src={icon} />
          <span className="font-semibold">Atlas</span>
        </div>
        <p className="pb-3 text-xs font-medium tracking-wider text-muted-foreground uppercase">{t.firstRun.setup}</p>
        <ol className="space-y-1">
          {STEPS.map((each, position) => (
            <StepRow
              current={each === step}
              key={each}
              mark={run.marks[each]}
              number={position + 1}
              onPick={() => {
                showStep(home, each)
              }}
              step={each}
            />
          ))}
        </ol>
        <p className="mt-auto text-xs text-muted-foreground">{t.firstRun.reopen}</p>
      </aside>
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-xl px-8 pt-[12vh] pb-12">
          <p className="text-sm text-muted-foreground" data-testid="first-run-count">
            {t.firstRun.stepOf(index + 1, STEPS.length)}
          </p>
          <h1 className="mt-1 text-3xl tracking-tight text-soft" data-testid="first-run-title">
            {t.firstRun.titles[step].lead}{' '}
            <span className="font-semibold text-foreground">{t.firstRun.titles[step].word}</span>
          </h1>
          <p className="mt-2 text-sm text-muted-foreground">{t.firstRun.intros[step]}</p>
          <div className="mt-6 space-y-5">
            {step === 'model' && <ModelStep onDone={run.open ? finish : dismissKeyNotice} />}
            {step === 'executors' && <ExecutorsStep onDone={finish} />}
            {step === 'telegram' && <TelegramStep onDone={finish} />}
            {step === 'connections' && <ConnectionsStep onDone={finish} />}
            {step === 'work' && <WorkStep onDone={finish} />}
          </div>
          <button
            className="mt-8 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
            data-testid="first-run-leave"
            onClick={leave}
            type="button"
          >
            {t.firstRun.leave}
          </button>
        </div>
      </div>
    </div>
  )
}

type StepRowProps = { current: boolean; mark: Mark | undefined; number: number; onPick: () => void; step: Step }

function StepRow({ current, mark, number, onPick, step }: StepRowProps) {
  const said = current ? t.firstRun.inProgress : mark ? t.firstRun.marks[mark] : t.firstRun.hints[step]

  return (
    <li>
      <button
        aria-current={current ? 'step' : undefined}
        className={`flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left ${
          current ? 'border-border bg-raised shadow-sm' : 'border-transparent hover:bg-hover'
        }`}
        data-mark={mark ?? ''}
        data-testid={`first-run-step-${step}`}
        onClick={onPick}
        type="button"
      >
        <span
          className={`grid h-6 w-6 shrink-0 place-items-center rounded text-xs font-medium ${
            mark === 'done'
              ? 'bg-primary text-primary-foreground'
              : current
                ? 'border border-foreground text-foreground'
                : 'border border-border-strong text-muted-foreground'
          }`}
        >
          {mark === 'done' ? <Check aria-hidden size={14} stroke={2.25} /> : String(number).padStart(2, '0')}
        </span>
        <span className="min-w-0">
          <span className={`block text-sm ${current || mark ? 'font-medium text-foreground' : 'text-soft'}`}>
            {t.firstRun.steps[step]}
          </span>
          <span className="block truncate text-xs text-muted-foreground">{said}</span>
        </span>
      </button>
    </li>
  )
}

/** A step's way on: Continue once it is done, Skip until then. */
function Next({ done, onDone }: { done: boolean; onDone: (mark: Mark) => void }) {
  return (
    <div className="flex items-center gap-4 pt-2">
      <button
        className={PRIMARY}
        data-testid="first-run-continue"
        disabled={!done}
        onClick={() => {
          onDone('done')
        }}
        type="button"
      >
        {t.firstRun.continue}
      </button>
      {!done && (
        <button
          className="text-sm text-muted-foreground hover:text-foreground"
          data-testid="first-run-skip"
          onClick={() => {
            onDone('skipped')
          }}
          type="button"
        >
          {t.firstRun.skip}
        </button>
      )}
    </div>
  )
}

/** The model: the key screen's providers, and what the check said of a key just stored. */
function ModelStep({ onDone }: { onDone: (mark: Mark) => void }) {
  const providers = useStore($keyProviders)
  const options = useStore($modelOptions)
  const notice = useStore($keyNotice)
  const done = Boolean(options && (options.providers.length > 0 || options.default.provider !== null))

  if (notice) {
    return (
      <div className="space-y-3 rounded-lg border border-waiting/40 bg-waiting/10 p-4 text-sm">
        <p className="font-medium">{t.keys.saved}</p>
        <p data-testid="key-notice">{notice.detail}</p>
        <button
          className={PRIMARY}
          data-testid="key-continue"
          onClick={() => {
            dismissKeyNotice()
            onDone('done')
          }}
          type="button"
        >
          {t.keys.continue}
        </button>
      </div>
    )
  }

  return (
    <>
      {providers && <Providers providers={providers} />}
      <Next done={done} onDone={onDone} />
    </>
  )
}

/** The executors found on this computer, turned on with their consent right there; and the
 * accounts that take over from one another, the accounts plugin's (step 22): its part shows here
 * once it is on, and until then a button turns it on. */
function ExecutorsStep({ onDone }: { onDone: (mark: Mark) => void }) {
  const { executors } = useStore($workbench)
  const on = executors.filter(row => row.enabled && row.installed && row.supported)
  const takeAccounts = on.filter(row => row.account_folders && !row.base)
  const accounts = on.filter(row => row.base)
  const plugin = inArea(useStore($contributions), 'executor').length > 0

  return (
    <>
      <ExecutorOffers />
      {takeAccounts.length > 0 && (
        <section className="space-y-2 rounded-lg border border-border p-4" data-testid="first-run-accounts">
          <p className="text-sm font-medium">{t.firstRun.accounts}</p>
          <p className="text-sm text-muted-foreground">{t.firstRun.accountsIntro}</p>
          {accounts.map(row => (
            <Account key={row.name} row={row} />
          ))}
          {plugin ? (
            takeAccounts.map(row => (
              <PlaceContributions
                area="executor"
                key={row.name}
                place={{ account: '', base: row.name, executor: row.name, title: row.title }}
              />
            ))
          ) : (
            <button
              className="rounded border border-border-strong px-2 py-0.5 text-xs"
              data-testid="first-run-accounts-plugin"
              onClick={() => {
                showPluginConsent('accounts')
              }}
              type="button"
            >
              {t.firstRun.accountsPlugin}
            </button>
          )}
        </section>
      )}
      <Next done={on.length > 0} onDone={onDone} />
    </>
  )
}

/** An account added here, and its login, which the harness itself does in the browser. */
function Account({ row }: { row: ExecutorRow }) {
  const [login, setLogin] = useState(false)

  return (
    <div className="rounded-md border border-border p-2 text-sm" data-testid="first-run-account">
      <div className="flex items-center gap-2">
        <ExecutorAvatar row={row} />
        <span className="min-w-0 flex-1 truncate">
          {row.title} · {row.nickname}
        </span>
        <span className="text-xs text-muted-foreground">{row.logged_in ? row.account : t.executors.notLoggedIn}</span>
        {!row.logged_in && !login && (
          <button
            className="rounded border border-border-strong px-2 py-0.5 text-xs"
            onClick={() => {
              setLogin(true)
            }}
            type="button"
          >
            {t.firstRun.logIn}
          </button>
        )}
      </div>
      {login && (
        <LoginForm
          executor={row}
          onDone={() => {
            setLogin(false)
          }}
        />
      )}
    </div>
  )
}

/** Telegram: the bot's token, the plugin turned on with the consent shown right there, and the
 * person who wrote to the bot let in from the step. */
function TelegramStep({ onDone }: { onDone: (mark: Mark) => void }) {
  const plugin = useStore($plugins).find(row => row.name === TELEGRAM)
  const people = useStore($pairings).filter(row => row.channel === TELEGRAM)
  const error = useStore($pluginError)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    refreshPlugins()
  }, [])

  if (!plugin) {
    return (
      <>
        <p className="text-sm text-muted-foreground" data-testid="first-run-no-telegram">
          {t.firstRun.telegramMissing}
        </p>
        <Next done={false} onDone={onDone} />
      </>
    )
  }

  const on = plugin.enabled && plugin.consented
  const stored = plugin.secrets.find(secret => secret.name === TELEGRAM_TOKEN)?.stored ?? false
  const ready = on && stored && plugin.problem === null
  const letIn = people.filter(row => row.approved)

  const connect = async () => {
    setBusy(true)
    // Turned on first, with the consent to what the step shows; the channel waits for its token
    // and starts once it is stored.
    if ((on || (await enablePlugin(plugin))) && (await storePluginSecret(plugin.name, TELEGRAM_TOKEN, token.trim()))) {
      setToken('')
    }
    setBusy(false)
  }

  return (
    <>
      <section className="space-y-3 rounded-lg border border-border p-4" data-testid="first-run-telegram">
        <p className="text-sm">{t.firstRun.botFather}</p>
        {!ready && (
          <form
            className="space-y-2"
            onSubmit={event => {
              event.preventDefault()
              void connect()
            }}
          >
            <label className="block text-sm font-medium" htmlFor="first-run-telegram-token">
              {t.firstRun.tokenLabel}
            </label>
            <div className="flex gap-2">
              <input
                autoComplete="off"
                className="min-w-0 flex-1 rounded-md border border-border-strong bg-raised px-3 py-2 text-sm"
                data-testid="first-run-telegram-token"
                id="first-run-telegram-token"
                onChange={event => {
                  setToken(event.target.value)
                }}
                placeholder={t.plugins.secretPlaceholder}
                type="password"
                value={token}
              />
              <button
                className={PRIMARY}
                data-testid="first-run-telegram-connect"
                disabled={busy || !token.trim()}
                type="submit"
              >
                {t.firstRun.connect}
              </button>
            </div>
            {stored && plugin.problem !== null && (
              <InlineError testId="first-run-telegram-problem">{plugin.problem}</InlineError>
            )}
            {!on && (
              <div className="text-xs text-muted-foreground" data-testid="first-run-telegram-consent">
                <p>{t.firstRun.telegramOn}</p>
                <ul className="list-disc pl-5">
                  {plugin.declaration.map(line => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            )}
          </form>
        )}
        {ready && (
          <p className="text-sm text-muted-foreground" data-testid="first-run-telegram-ready">
            {plugin.running ? t.firstRun.botRunning : t.firstRun.botStarting}
          </p>
        )}
        {people.map(row => (
          <div className="flex items-center gap-2 text-sm" data-testid="first-run-telegram-person" key={row.user}>
            {row.approved ? (
              <>
                <Check aria-hidden className="text-primary" size={14} stroke={2.25} />
                <span>{t.firstRun.letInDone(row.name)}</span>
              </>
            ) : (
              <>
                <span className="min-w-0 flex-1">{t.firstRun.letInWaits(row.name, row.code)}</span>
                <button
                  className="rounded border border-border-strong px-2 py-0.5 text-xs"
                  data-testid="first-run-telegram-let-in"
                  onClick={() => void approvePairing(row.code)}
                  type="button"
                >
                  {t.firstRun.letIn}
                </button>
              </>
            )}
          </div>
        ))}
        {error && <InlineError>{error}</InlineError>}
      </section>
      <Next done={letIn.length > 0} onDone={onDone} />
    </>
  )
}

/** Connections: GitHub through gh's own login in the browser, and Gmail and Calendar through gws,
 * set up with Atlas in a conversation; each says who it is logged in as. */
function ConnectionsStep({ onDone }: { onDone: (mark: Mark) => void }) {
  const home = useStore($home)
  const logins = useStore($logins)
  const noKey = keyScreenShown(useStore($modelOptions), false, null)
  const [code, setCode] = useState<null | { code: string; url: string }>(null)
  const [error, setError] = useState<null | string>(null)
  const github = logins?.find(row => row.service === 'github')
  const google = logins?.find(row => row.service === 'google')

  useEffect(() => {
    void refreshLogins()
  }, [])

  const connectGithub = async () => {
    setError(null)
    const started = await logInToGithub()
    if ('error' in started) {
      setError(started.error)
    } else {
      setCode(started)
    }
  }

  return (
    <>
      <section className="space-y-3 rounded-lg border border-border p-4" data-testid="first-run-github">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{t.firstRun.github}</p>
            <p className="text-xs text-muted-foreground">{t.firstRun.githubIntro}</p>
          </div>
          {github?.logged_in ? (
            <Connected account={github.account} service="github" />
          ) : (
            github?.installed && (
              <button
                className={PRIMARY}
                data-testid="first-run-github-connect"
                onClick={() => void connectGithub()}
                type="button"
              >
                {t.firstRun.connect}
              </button>
            )
          )}
        </div>
        {github && !github.installed && (
          <p className="text-sm text-muted-foreground" data-testid="first-run-github-missing">
            {t.firstRun.githubMissing}
          </p>
        )}
        {code && !github?.logged_in && (
          <div className="space-y-1 rounded-md bg-subtle p-3 text-sm">
            <p>{t.firstRun.githubCode}</p>
            <p className="font-mono text-lg tracking-widest select-all" data-testid="first-run-github-code">
              {code.code}
            </p>
            <button
              className="text-xs underline underline-offset-4"
              onClick={() => {
                window.open(code.url)
              }}
              type="button"
            >
              {t.firstRun.githubPage}
            </button>
          </div>
        )}
        {error && <InlineError>{error}</InlineError>}
      </section>
      <section className="space-y-3 rounded-lg border border-border p-4" data-testid="first-run-google">
        <div className="flex items-center gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{t.firstRun.google}</p>
            <p className="text-xs text-muted-foreground">{t.firstRun.googleIntro}</p>
          </div>
          {google?.logged_in ? (
            <Connected account={google.account} service="google" />
          ) : (
            <button
              className={PRIMARY}
              data-testid="first-run-google-set-up"
              disabled={noKey}
              onClick={() => {
                // The agent leads gws's setup in a conversation; the first run waits on this step,
                // a band away, and reads the logins again when it shows.
                stepAway(home)
                newConversation()
                void send(t.firstRun.googlePrompt)
              }}
              type="button"
            >
              {t.firstRun.googleSetUp}
            </button>
          )}
        </div>
        {noKey && !google?.logged_in && <p className="text-xs text-muted-foreground">{t.firstRun.googleNoKey}</p>}
      </section>
      <button
        className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        data-testid="first-run-check-again"
        onClick={() => void refreshLogins()}
        type="button"
      >
        {t.firstRun.checkAgain}
      </button>
      <Next done={Boolean(github?.logged_in || google?.logged_in)} onDone={onDone} />
    </>
  )
}

function Connected({ account, service }: { account: string; service: string }) {
  return (
    <span className="flex shrink-0 items-center gap-1 text-sm" data-testid={`first-run-${service}-connected`}>
      <Check aria-hidden className="text-primary" size={14} stroke={2.25} />
      {t.firstRun.connectedAs(account)}
    </span>
  )
}

/** The band over the app while the first run waits on a step stepped away from. */
export function FirstRunBand({ step }: { step: Step }) {
  const home = useStore($home)

  return (
    <div
      className="flex items-center gap-3 border-b border-border bg-subtle px-4 py-2 text-sm text-foreground"
      data-testid="first-run-band"
      role="status"
    >
      <span>{t.firstRun.away(t.firstRun.steps[step])}</span>
      <button
        className="font-medium underline"
        data-testid="first-run-back"
        onClick={() => {
          stepAway(home, false)
        }}
        type="button"
      >
        {t.firstRun.awayBack}
      </button>
    </div>
  )
}

/** "Bring your work": the executors' sessions of a window, the rest archived. */
function WorkStep({ onDone }: { onDone: (mark: Mark) => void }) {
  const { executors } = useStore($workbench)
  const on = executors.filter(row => row.enabled && row.installed && row.supported)
  const [left, setLeft] = useState<string[]>([])
  const [span, setSpan] = useState<WorkWindow>(30)
  const [reading, setReading] = useState(false)
  const chosen = on.filter(row => !left.includes(row.name))
  const names = chosen.map(row => row.name).join('\n')
  // Read again whenever what the plan reads changes.
  useStore($sessionFolders)
  useStore($sessions)

  useEffect(() => {
    const picked = names ? names.split('\n') : []
    let current = true
    setReading(true)
    void Promise.all(picked.map(name => readWork(name, span))).finally(() => {
      if (current) {
        setReading(false)
      }
    })

    return () => {
      current = false
    }
  }, [names, span])

  if (on.length === 0) {
    return (
      <>
        <p className="text-sm text-muted-foreground" data-testid="first-run-no-executors">
          {t.firstRun.noExecutors}
        </p>
        <Start onDone={onDone} />
      </>
    )
  }

  const plans = chosen.map(row => ({ plan: readPlan(row.name, span), row }))
  const counted = plans.every(({ plan }) => plan !== null) && !reading
  const total = plans.reduce((sum, { plan }) => sum + (plan?.kept ?? 0), 0)

  return (
    <>
      <div className="space-y-4 rounded-lg border border-border-strong bg-raised p-5" data-testid="first-run-work">
        <p className="flex flex-wrap items-center gap-2 text-lg">
          <span>{t.firstRun.bring}</span>
          {on.map(row => {
            const picked = !left.includes(row.name)

            return (
              <button
                aria-pressed={picked}
                className={`flex items-center gap-2 rounded-md border px-2.5 py-1 ${
                  picked ? 'border-foreground font-medium' : 'border-dashed border-border-strong text-muted-foreground'
                }`}
                data-testid="first-run-executor"
                key={row.name}
                onClick={() => {
                  setLeft(picked ? [...left, row.name] : left.filter(name => name !== row.name))
                }}
                type="button"
              >
                <ExecutorAvatar row={row} />
                {row.base ? `${row.title} · ${row.nickname}` : row.title}
              </button>
            )
          })}
          <select
            aria-label={t.firstRun.windowLabel}
            className="rounded-md border border-foreground bg-raised px-2.5 py-1 font-medium"
            data-testid="first-run-window"
            onChange={event => {
              setSpan(event.target.value === 'all' ? null : (Number(event.target.value) as WorkWindow))
            }}
            value={span === null ? 'all' : String(span)}
          >
            {WINDOWS.map(each => (
              <option key={each ?? 'all'} value={each === null ? 'all' : String(each)}>
                {each === null ? t.firstRun.windowAll : t.firstRun.windowDays(each)}
              </option>
            ))}
          </select>
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <button
            className={PRIMARY}
            data-testid="first-run-bring"
            disabled={chosen.length === 0 || (span !== null && !counted)}
            onClick={() => {
              bringWork(
                chosen.map(row => row.name),
                span
              )
              onDone('done')
            }}
            type="button"
          >
            {span === null ? t.firstRun.bringAll : counted ? t.firstRun.bringCount(total) : t.firstRun.counting}
          </button>
          {span !== null && counted && (
            <span className="text-sm text-muted-foreground" data-testid="first-run-counts">
              {plans.map(({ plan, row }) => `${String(plan?.kept ?? 0)} ${row.title}`).join(' · ')}
            </span>
          )}
        </div>
        <p className="text-xs text-muted-foreground">{t.firstRun.bringNote}</p>
      </div>
      <Start onDone={onDone} />
    </>
  )
}

/** The way out of the last step with nothing brought: a conversation. */
function Start({ onDone }: { onDone: (mark: Mark) => void }): ReactNode {
  return (
    <button
      className="text-sm underline underline-offset-4"
      data-testid="first-run-start"
      onClick={() => {
        onDone('skipped')
      }}
      type="button"
    >
      {t.firstRun.start}
    </button>
  )
}

/** The Telegram plugin Atlas ships, and the secret its bot reads. */
const TELEGRAM = 'telegram'
const TELEGRAM_TOKEN = 'TELEGRAM_BOT_TOKEN'

const PRIMARY =
  'shrink-0 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40'
