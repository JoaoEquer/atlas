import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { t } from '@/i18n'
import { $screenErrors } from '@/plugins/registry'
import type { PairingRow, PluginRow, PluginSecretRow, PluginSettingRow } from '@/protocol/protocol.generated'
import {
  $pairings,
  $pluginError,
  $plugins,
  approvePairing,
  disablePlugin,
  enablePlugin,
  forgetPairing,
  PLUGIN_GUIDE,
  reloadPlugins,
  setPluginSetting,
  type SettingChoice,
  settingChoices,
  storePluginSecret,
  writePlugin
} from '@/store/app'
import { matches } from '@/store/extensions'
import { closeSettings } from '@/store/settings'

import { Badge, ExtensionCard, Shelf } from './extension-card'

/**
 * The plugins (docs/ARCHITECTURE.md, Plugins): each one off until turned on, and turning one on
 * shows what it declares first, for consent; its secrets and settings; and the
 * people its channels let in, or who wait with a code (a pairing, approved here instead of
 * only in a terminal). The settings screen's Plugins since step 15b, the Plugins tab of its
 * Extensions since 28/10/2026: a card each, in the marketplace's grid, the Atlas team's first.
 */
export function PluginsTab({ query }: { query: string }) {
  const plugins = useStore($plugins)
  const pairings = useStore($pairings)
  const error = useStore($pluginError)
  const channels = plugins.some(plugin => plugin.channels.length > 0)
  const shown = plugins.filter(plugin => matches(query, plugin.name, plugin.description, plugin.author))
  const shipped = shown.filter(plugin => plugin.bundled)
  const added = shown.filter(plugin => !plugin.bundled)

  return (
    <div data-testid="plugins-view">
      <div className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
          <p className="min-w-0 flex-1 basis-64 text-sm text-muted-foreground">{t.plugins.intro}</p>
          <div className="flex shrink-0 items-center gap-2">
            <a className="link text-sm" href={PLUGIN_GUIDE} rel="noreferrer" target="_blank">
              {t.plugins.guide}
            </a>
            <button
              className={`${BUTTON} text-sm`}
              data-testid="plugins-write"
              onClick={() => {
                closeSettings()
                writePlugin()
              }}
              title={t.plugins.writeHint}
              type="button"
            >
              {t.plugins.write}
            </button>
            {/* Step 16n: the plugins loaded again; the server also loads a saved file again. */}
            <button
              className={`${BUTTON} text-sm`}
              data-testid="plugins-reload"
              onClick={() => void reloadPlugins()}
              title={t.plugins.reloadHint}
              type="button"
            >
              {t.plugins.reload}
            </button>
          </div>
        </div>
        {error && (
          <p className="rounded-md bg-failed/10 px-3 py-2 text-sm text-failed-text" role="alert">
            {error}
          </p>
        )}
        {plugins.length === 0 && <p className="text-sm text-muted-foreground">{t.plugins.empty}</p>}
        {plugins.length > 0 && shown.length === 0 && (
          <p className="text-sm text-muted-foreground">{t.extensions.noMatch}</p>
        )}
        <Shelf count={shipped.length} title={t.marketplace.official}>
          {shipped.map(plugin => (
            <Plugin key={`${plugin.name}-true`} plugin={plugin} />
          ))}
        </Shelf>
        <Shelf count={added.length} title={t.extensions.added}>
          {added.map(plugin => (
            <Plugin key={`${plugin.name}-false`} plugin={plugin} />
          ))}
        </Shelf>
        {channels && <People pairings={pairings} />}
      </div>
    </div>
  )
}

function Plugin({ plugin }: { plugin: PluginRow }) {
  const [asking, setAsking] = useState(false)
  const on = plugin.enabled && plugin.consented
  const state = plugin.running ? t.plugins.running : on ? t.plugins.on : t.plugins.off
  // Its screen loads in the app (step 16m), so why it did not is the app's to say.
  const screenError = useStore($screenErrors)[plugin.name]

  return (
    <ExtensionCard
      action={
        on ? (
          <button
            className={`${BUTTON} shrink-0 text-sm`}
            data-testid="plugin-disable"
            onClick={() => void disablePlugin(plugin.name)}
            type="button"
          >
            {t.plugins.disable}
          </button>
        ) : (
          !asking && (
            <button
              className={`${BUTTON} shrink-0 text-sm`}
              data-testid="plugin-enable"
              onClick={() => {
                setAsking(true)
              }}
              type="button"
            >
              {t.plugins.enable}
            </button>
          )
        )
      }
      badge={plugin.bundled ? <Badge>{t.marketplace.officialBadge}</Badge> : undefined}
      data={{ 'data-plugin': plugin.name, 'data-testid': 'plugin-row' }}
      footer={
        <span
          className={`rounded px-1.5 py-0.5 ${plugin.running ? 'bg-selected text-foreground' : 'bg-muted text-soft'}`}
          data-testid="plugin-state"
        >
          {state}
        </span>
      }
      kind="plugin"
      subtitle={[plugin.version, plugin.bundled ? t.plugins.shipped : plugin.author].filter(Boolean).join(' · ')}
      summary={plugin.description}
      title={plugin.name}
      wide={asking && !on}
    >
      {plugin.problem && (
        <p className="text-sm text-waiting-text select-text" data-testid="plugin-problem">
          {plugin.problem}
        </p>
      )}
      {on && screenError && (
        <p className="text-sm text-waiting-text" data-testid="plugin-screen-problem">
          {t.plugins.screenNotLoaded(screenError)}
        </p>
      )}
      {asking && !on && (
        <div className="rounded-md bg-subtle p-3 text-sm" data-testid="plugin-consent">
          <p className="font-medium">{t.plugins.consentIntro}</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {plugin.declaration.map(line => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          <div className="mt-3 flex gap-2">
            <button
              className={`${BUTTON} bg-primary text-primary-foreground hover:bg-primary/90`}
              data-testid="plugin-consent-yes"
              onClick={() => {
                setAsking(false)
                void enablePlugin(plugin)
              }}
              type="button"
            >
              {t.plugins.enableNow}
            </button>
            <button
              className={BUTTON}
              onClick={() => {
                setAsking(false)
              }}
              type="button"
            >
              {t.plugins.cancel}
            </button>
          </div>
        </div>
      )}
      {plugin.secrets.map(secret => (
        <Secret key={secret.name} plugin={plugin.name} secret={secret} />
      ))}
      {plugin.settings.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">{t.plugins.settings}</summary>
          {plugin.settings.map(setting => (
            <Setting
              key={setting.name}
              on={plugin.enabled}
              plugin={plugin.name}
              revision={revisionOf(plugin)}
              setting={setting}
            />
          ))}
        </details>
      )}
    </ExtensionCard>
  )
}

/** A secret the plugin reads: a field to paste it in while the vault has none; once stored, only
 *  that it is, and a button to change it. A key stored for something else, such as the OpenAI
 *  provider's, counts: the field would read as if it were asked for again. */
function Secret({ plugin, secret }: { plugin: string; secret: PluginSecretRow }) {
  const [value, setValue] = useState('')
  const [changing, setChanging] = useState(false)

  return (
    <div className="min-w-0 text-sm" data-stored={secret.stored} data-testid="plugin-secret">
      <p>
        <span className="font-mono">{secret.name}</span>
        <span className="ml-2 text-muted-foreground">{secret.stored ? t.plugins.stored : t.plugins.notStored}</span>
        {secret.stored && !changing && (
          <button
            className="link ml-2"
            data-testid="plugin-secret-change"
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
            void storePluginSecret(plugin, secret.name, value).then(done => {
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
            className={INPUT}
            data-testid="plugin-secret-input"
            onChange={event => {
              setValue(event.target.value)
            }}
            placeholder={t.plugins.secretPlaceholder}
            type="password"
            value={value}
          />
          <button className={BUTTON} data-testid="plugin-secret-save" disabled={!value.trim()} type="submit">
            {t.plugins.save}
          </button>
        </form>
      )}
    </div>
  )
}

/** The option of the list that turns it into a box to type in. */
const OTHER = '\u0000other'

/**
 * A setting: a list when it has choices (SDK version 25), asked again when another of the plugin's
 * settings or keys changes (`revision`), since the list may hang on them; a box to type in when it
 * has none, or when the user picks "Other…". The list always holds the value in use and the
 * default, listed or not.
 */
function Setting({
  on,
  plugin,
  revision,
  setting
}: {
  on: boolean
  plugin: string
  revision: string
  setting: PluginSettingRow
}) {
  const [value, setValue] = useState(setting.value)
  const [listed, setListed] = useState<{ choices: SettingChoice[]; why: string } | null>(null)
  const [typing, setTyping] = useState(false)
  const id = `${plugin}-${setting.name}`
  // A plugin that is off answers no call: its own choices wait for it to be on.
  const offers =
    setting.choices.length > 0 || setting.choices_from === 'models' || (setting.choices_from === 'plugin' && on)

  useEffect(() => {
    setValue(setting.value)
  }, [setting.value])

  useEffect(() => {
    if (!offers) {
      return
    }

    let live = true
    settingChoices(plugin, setting)
      .then(found => {
        if (live) {
          setListed(found)
        }
      })
      .catch((error: unknown) => {
        if (live) {
          setListed({ choices: [], why: error instanceof Error ? error.message : String(error) })
        }
      })

    return () => {
      live = false
    }
    // The setting's own value is not among them: picking one does not change the list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plugin, setting.name, offers, revision])

  const options = listed === null ? [] : withValues(listed.choices, [setting.value, setting.default])
  const listing = offers && !typing && listed !== null && listed.choices.length > 0

  return (
    <form
      className="mt-2"
      onSubmit={event => {
        event.preventDefault()
        void setPluginSetting(plugin, setting.name, value)
      }}
    >
      <label className="block font-mono" htmlFor={id}>
        {setting.name}
      </label>
      {setting.description && <p className="text-muted-foreground">{setting.description}</p>}
      <div className="mt-1 flex gap-2">
        {listing ? (
          <select
            className={INPUT}
            id={id}
            onChange={event => {
              if (event.target.value === OTHER) {
                setTyping(true)

                return
              }

              setValue(event.target.value)
              void setPluginSetting(plugin, setting.name, event.target.value)
            }}
            title={value}
            value={value}
          >
            {options.map(choice => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
            <option title={t.plugins.otherHint} value={OTHER}>
              {t.plugins.other}
            </option>
          </select>
        ) : (
          <input
            className={INPUT}
            id={id}
            onChange={event => {
              setValue(event.target.value)
            }}
            value={value}
          />
        )}
        {listing ? null : (
          <button className={BUTTON} type="submit">
            {t.plugins.save}
          </button>
        )}
        {typing && (
          <button
            className={BUTTON}
            onClick={() => {
              setTyping(false)
            }}
            title={t.plugins.listHint}
            type="button"
          >
            {t.plugins.list}
          </button>
        )}
        <button
          className={BUTTON}
          onClick={() => {
            setValue(setting.default)
            void setPluginSetting(plugin, setting.name, null)
          }}
          title={t.plugins.defaultValue(setting.default)}
          type="button"
        >
          {t.plugins.reset}
        </button>
      </div>
      {listed?.why && <p className="mt-1 text-muted-foreground">{listed.why}</p>}
    </form>
  )
}

/** What a setting's list may hang on: the plugin's settings, its keys, and whether it is on. */
function revisionOf(plugin: PluginRow): string {
  return JSON.stringify([
    plugin.enabled,
    plugin.settings.map(setting => setting.value),
    plugin.secrets.map(secret => secret.stored)
  ])
}

/** The choices with each of `values` among them, the ones the list did not hold added at the end. */
export function withValues(choices: readonly SettingChoice[], values: readonly string[]): SettingChoice[] {
  const all = [...choices]

  for (const value of values) {
    if (!all.some(choice => choice.value === value)) {
      all.push({ label: value, value })
    }
  }

  return all
}

function People({ pairings }: { pairings: PairingRow[] }) {
  return (
    <section className="space-y-2" data-testid="pairings">
      <h2 className="font-semibold">{t.plugins.people}</h2>
      <p className="text-sm text-muted-foreground">{t.plugins.peopleIntro}</p>
      {pairings.length === 0 && <p className="text-sm text-muted-foreground">{t.plugins.noPeople}</p>}
      <ul className="space-y-1 text-sm">
        {pairings.map(pairing => (
          <li
            className="flex items-center justify-between gap-4"
            data-testid="pairing-row"
            key={`${pairing.channel}-${pairing.user}`}
          >
            <span className="min-w-0 truncate">
              {pairing.name} <span className="text-muted-foreground">· {pairing.channel} · </span>
              {pairing.approved ? (
                <span className="text-muted-foreground">{t.plugins.approved}</span>
              ) : (
                <span className="font-mono">{t.plugins.waits(pairing.code)}</span>
              )}
            </span>
            <span className="flex shrink-0 gap-2">
              {!pairing.approved && (
                <button
                  className={BUTTON}
                  data-testid="pairing-approve"
                  onClick={() => void approvePairing(pairing.code)}
                  type="button"
                >
                  {t.plugins.approve}
                </button>
              )}
              <button
                className={BUTTON}
                onClick={() => void forgetPairing(pairing.channel, pairing.user)}
                type="button"
              >
                {t.plugins.remove}
              </button>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

const BUTTON = 'rounded-md border border-border-strong px-2 py-1 hover:bg-muted disabled:opacity-40'
const INPUT = 'min-w-0 flex-1 rounded-md border border-border-strong bg-raised px-2 py-1'
