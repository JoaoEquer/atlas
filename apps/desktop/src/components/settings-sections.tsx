import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useState } from 'react'

import { chosenLocale, locale, type LocaleChoice, setLocale, systemLocale, t } from '@/i18n'
import type { ConversationModel, ProviderOption, SettingChange, SettingRow } from '@/protocol/protocol.generated'
import {
  $home,
  $keyProviders,
  $modelOptions,
  $proposals,
  $remote,
  readSettings,
  serverVersion,
  showProposals,
  writeSettings
} from '@/store/app'
import { closeSettings } from '@/store/settings'
import { $pluginThemes, $themeChoice, setTheme, type ThemeChoice, themeKey } from '@/store/theme'
import { $update, $updateOpen } from '@/store/updates'

import { Providers } from './key-screen'
import { MemoryEntries } from './memory-entries'
import { withCurrent } from './model-picker'
import { SoulSection } from './soul-view'

/**
 * The settings screen's sections (step 15b, docs/ARCHITECTURE.md, The settings screen): a change
 * saves itself, with no Save button, and the server applies it without a
 * restart; a text saves when the field is left. What an `ATLAS_*` variable sets shows locked,
 * naming it, since it wins over the file.
 */

type Rows = Record<string, SettingRow>
type Status = { kind: 'failed'; text: string } | { kind: 'saved' } | { kind: 'saving' }

const id = (section: string, key: string) => `${section}.${key}`

/** The editable settings of the open home, read again when it changes, and their saving. */
function useSettings() {
  const home = useStore($home)
  const [rows, setRows] = useState<null | Rows>(null)
  const [status, setStatus] = useState<Record<string, Status>>({})
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    let live = true
    setRows(null)
    setError(null)
    readSettings().then(
      read => {
        if (live) {
          setRows(Object.fromEntries(read.settings.map(row => [id(row.section, row.key), row])))
        }
      },
      (failure: unknown) => {
        if (live) {
          setError(failure instanceof Error ? failure.message : String(failure))
        }
      }
    )

    return () => {
      live = false
    }
  }, [home])

  const save = async (field: string, changes: SettingChange[]): Promise<boolean> => {
    setStatus(before => ({ ...before, [field]: { kind: 'saving' } }))

    try {
      const written = await writeSettings(changes)
      setRows(Object.fromEntries(written.settings.map(row => [id(row.section, row.key), row])))
      setStatus(before => ({ ...before, [field]: { kind: 'saved' } }))

      return true
    } catch (failure) {
      const text = failure instanceof Error ? failure.message : String(failure)
      setStatus(before => ({ ...before, [field]: { kind: 'failed', text } }))

      return false
    }
  }

  return { error, rows, save, status }
}

export function Heading({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="space-y-1">
      <h3 className="text-sm font-semibold">{children}</h3>
      {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
    </div>
  )
}

function StatusLine({ status, variable }: { status?: Status; variable?: null | string }) {
  if (variable) {
    return (
      <p className="text-xs text-waiting-text" data-testid="setting-locked">
        {t.settings.locked(variable)}
      </p>
    )
  }

  if (!status) {
    return null
  }

  if (status.kind === 'failed') {
    return (
      <p className="text-xs text-failed-text" data-testid="setting-error" role="alert">
        {status.text}
      </p>
    )
  }

  return (
    <p className="text-xs text-muted-foreground" data-testid="setting-status" role="status">
      {status.kind === 'saving' ? t.settings.saving : t.settings.saved}
    </p>
  )
}

function Failure({ error }: { error: null | string }) {
  return (
    error && (
      <p className="rounded-md bg-failed/10 px-3 py-2 text-sm text-failed-text" role="alert">
        {error}
      </p>
    )
  )
}

/** A setting edited as text, saved when the field is left (and on Enter, for one line). */
function TextSetting({
  children,
  hint,
  label,
  lines = false,
  row,
  save,
  status
}: {
  children?: ReactNode
  hint: ReactNode
  label: string
  lines?: boolean
  row: SettingRow
  save: (field: string, changes: SettingChange[]) => Promise<boolean>
  status?: Status
}) {
  const field = id(row.section, row.key)
  const [draft, setDraft] = useState(row.value ?? '')

  useEffect(() => {
    setDraft(row.value ?? '')
  }, [row.value])

  const commit = (value: string) => {
    if (value.trim() === (row.value ?? '').trim()) {
      return
    }
    void save(field, [{ key: row.key, section: row.section, value: value.trim() ? value : null }])
  }

  const props = {
    'aria-label': label,
    className: 'w-full rounded-md border border-border-strong bg-raised px-2 py-1.5 text-sm disabled:opacity-60',
    'data-testid': `setting-${field}`,
    disabled: row.variable !== null,
    onBlur: () => {
      commit(draft)
    },
    placeholder: row.default,
    spellCheck: false,
    value: draft
  }

  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium">{label}</span>
      <span className="block text-sm text-muted-foreground">{hint}</span>
      <span className="flex gap-2">
        {lines ? (
          <textarea
            {...props}
            className={`${props.className} min-h-20 font-mono`}
            onChange={event => {
              setDraft(event.target.value)
            }}
          />
        ) : (
          <input
            {...props}
            onChange={event => {
              setDraft(event.target.value)
            }}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                commit(draft)
              }
            }}
          />
        )}
        {children}
      </span>
      <StatusLine status={status} variable={row.variable} />
    </label>
  )
}

// -- General ------------------------------------------------------------------------------------

export function GeneralSection() {
  const theme = useStore($themeChoice)
  const pluginThemes = useStore($pluginThemes)
  const update = useStore($update)
  const language = chosenLocale()
  const [looking, setLooking] = useState(false)
  const [looked, setLooked] = useState(false)
  const version = serverVersion()

  const look = () => {
    setLooking(true)
    window.atlas.updates
      .check()
      .then(found => {
        if (found) {
          $update.set(found)
        }
      })
      .catch(() => undefined)
      .finally(() => {
        setLooking(false)
        setLooked(true)
      })
  }

  return (
    <div className="space-y-6">
      <Choice
        current={language}
        hint={t.settings.languageHint}
        label={t.settings.language}
        onChange={next => {
          setLocale(next as LocaleChoice, () => {
            window.location.reload()
          })
        }}
        options={[
          ['system', `${t.settings.languageSystem} (${t.settings.languages[systemLocale] ?? systemLocale})`],
          ['pt-BR', t.settings.languages['pt-BR'] ?? 'Português'],
          ['en', t.settings.languages.en ?? 'English']
        ]}
        testId="setting-language"
      />
      <Choice
        current={theme}
        hint={t.settings.themeHint}
        label={t.settings.theme}
        onChange={next => {
          setTheme(next as ThemeChoice)
        }}
        options={[
          ['system', t.settings.themeSystem],
          ['light', t.settings.themeLight],
          ['dark', t.settings.themeDark],
          ...pluginThemes.map((row): [string, string] => [themeKey(row), row.labels[locale] ?? row.label])
        ]}
        testId="setting-theme"
      />
      <section className="space-y-2" data-testid="settings-about">
        <Heading>{t.settings.about}</Heading>
        <p className="text-sm">{version ? t.settings.version(version) : '…'}</p>
        {update?.supported === false ? (
          <p className="text-sm text-muted-foreground">{t.settings.noUpdates}</p>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <button className={SECONDARY} data-testid="settings-look" disabled={looking} onClick={look} type="button">
              {looking ? t.settings.checking : t.settings.checkUpdate}
            </button>
            {update?.available ? (
              <>
                <span className="text-sm">{t.settings.updateAvailable(update.behind)}</span>
                <button
                  className={PRIMARY}
                  onClick={() => {
                    closeSettings()
                    $updateOpen.set(true)
                  }}
                  type="button"
                >
                  {t.settings.update}
                </button>
              </>
            ) : (
              looked &&
              update && (
                <span className="text-sm text-muted-foreground" role="status">
                  {update.error || t.settings.upToDate}
                </span>
              )
            )}
          </div>
        )}
      </section>
    </div>
  )
}

/** One choice of a few, as a segmented control. */
function Choice({
  current,
  hint,
  label,
  onChange,
  options,
  testId
}: {
  current: string
  hint: string
  label: string
  onChange: (value: string) => void
  options: [string, string][]
  testId: string
}) {
  return (
    <fieldset className="space-y-1.5" data-testid={testId}>
      <legend className="text-sm font-medium">{label}</legend>
      <p className="text-sm text-muted-foreground">{hint}</p>
      <div className="inline-flex rounded-md bg-selected p-0.5" role="radiogroup">
        {options.map(([value, text]) => (
          <button
            aria-checked={value === current}
            className="rounded px-3 py-1 text-sm aria-checked:bg-background aria-checked:shadow-sm"
            data-value={value}
            key={value}
            onClick={() => {
              if (value !== current) {
                onChange(value)
              }
            }}
            role="radio"
            type="button"
          >
            {text}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

// -- Model --------------------------------------------------------------------------------------

const SEPARATOR = '\u0000'
const AUTOMATIC = ''

export function ModelSection() {
  const { error, rows, save, status } = useSettings()
  const options = useStore($modelOptions)
  const keys = useStore($keyProviders)

  return (
    <div className="space-y-6">
      <Failure error={error} />
      {rows && options && (
        <DefaultModel
          fallback={options.default}
          options={options.providers}
          rows={rows}
          save={save}
          status={status.model}
        />
      )}
      <section className="space-y-3">
        <Heading hint={t.settings.keysHint}>{t.settings.keys}</Heading>
        {keys && <Providers providers={keys} />}
      </section>
    </div>
  )
}

function DefaultModel({
  fallback,
  options,
  rows,
  save,
  status
}: {
  /** What a conversation that picks nothing answers with now: names the provider's default. */
  fallback: ConversationModel
  options: ProviderOption[]
  rows: Rows
  save: (field: string, changes: SettingChange[]) => Promise<boolean>
  status?: Status
}) {
  const provider = rows['model.provider']?.value ?? null
  const model = rows['model.name']?.value ?? null
  const effort = rows['model.effort']?.value ?? null
  const variable = ['model.provider', 'model.name', 'model.effort']
    .map(field => rows[field]?.variable)
    .find(name => name)
  // The model that answers: the one named, or the provider's default the server resolved.
  const shown = model ?? (fallback.provider === provider ? fallback.model : null)
  const providers = provider && shown ? withCurrent(options, provider, shown) : options
  const info = providers.find(p => p.id === provider)?.models.find(m => m.id === shown)
  const efforts = info?.reasoning === false ? [] : (info?.efforts ?? [])
  const write = (changes: SettingChange[]) => void save('model', changes)

  return (
    <section className="space-y-3" data-testid="default-model">
      <Heading hint={t.settings.defaultModelHint}>{t.settings.defaultModel}</Heading>
      <div className="flex flex-wrap gap-2">
        <select
          aria-label={t.settings.model}
          className={SELECT}
          data-testid="setting-default-model"
          disabled={Boolean(variable)}
          onChange={event => {
            const [next = '', name = ''] = event.target.value.split(SEPARATOR)
            const kept =
              effort &&
              providers
                .find(p => p.id === next)
                ?.models.find(m => m.id === name)
                ?.efforts?.includes(effort)
            write([
              { key: 'provider', section: 'model', value: next || null },
              { key: 'name', section: 'model', value: name || null },
              { key: 'effort', section: 'model', value: kept ? effort : null }
            ])
          }}
          value={provider ? `${provider}${SEPARATOR}${model ?? ''}` : AUTOMATIC}
        >
          <option value={AUTOMATIC}>{t.settings.automatic}</option>
          {providers.map(each => (
            <optgroup key={each.id} label={each.name}>
              {provider === each.id && model === null && (
                <option value={`${each.id}${SEPARATOR}`}>
                  {t.composer.defaulted(info?.name ?? shown ?? each.name)}
                </option>
              )}
              {each.models.map(choice => (
                <option key={choice.id} value={`${each.id}${SEPARATOR}${choice.id}`}>
                  {choice.name}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {provider && efforts.length > 0 && (
          <select
            aria-label={t.settings.effort}
            className={SELECT}
            data-testid="setting-default-effort"
            disabled={Boolean(variable)}
            onChange={event => {
              write([{ key: 'effort', section: 'model', value: event.target.value || null }])
            }}
            value={effort ?? ''}
          >
            <option value="">{t.settings.effortDefault}</option>
            {efforts.map(level => (
              <option key={level} value={level}>
                {t.composer.effortLevel(level)}
              </option>
            ))}
          </select>
        )}
      </div>
      <StatusLine status={status} variable={variable} />
    </section>
  )
}

// -- Personalization ----------------------------------------------------------------------------

export function PersonalizationSection() {
  const proposals = useStore($proposals)

  return (
    <div className="space-y-8">
      <p className="text-sm text-muted-foreground">{t.settings.appliesNext}</p>
      <SoulSection />
      <MemoryEntries hint={t.settings.aboutYouHint} target="user" title={t.settings.aboutYou} />
      <MemoryEntries hint={t.settings.memoryHint} target="memory" title={t.settings.memory} />
      {proposals.length > 0 && (
        <button
          className="link text-sm"
          data-testid="settings-proposals"
          onClick={() => {
            closeSettings()
            showProposals()
          }}
          type="button"
        >
          {t.settings.proposals(proposals.length)}
        </button>
      )}
    </div>
  )
}

// -- Safety and Workspace -----------------------------------------------------------------------

export function SafetySection() {
  const { error, rows, save, status } = useSettings()
  const field = (key: string) => rows?.[id('trust', key)]

  return (
    <div className="space-y-6">
      <Failure error={error} />
      {(
        [
          ['deny', t.settings.deny, t.settings.denyHint],
          ['ask', t.settings.ask, t.settings.askHint],
          ['ask_skip', t.settings.askSkip, t.settings.askSkipHint]
        ] as const
      ).map(([key, label, hint]) => {
        const row = field(key)

        return (
          row && (
            <TextSetting
              hint={hint}
              key={key}
              label={label}
              lines
              row={row}
              save={save}
              status={status[id('trust', key)]}
            />
          )
        )
      })}
      {field('approval_timeout') && (
        <TextSetting
          hint={t.settings.approvalTimeoutHint}
          label={t.settings.approvalTimeout}
          row={field('approval_timeout') as SettingRow}
          save={save}
          status={status['trust.approval_timeout']}
        />
      )}
    </div>
  )
}

export function WorkspaceSection() {
  const { error, rows, save, status } = useSettings()
  const remote = useStore($remote)
  const folder = rows?.['workspace.folder']
  const zone = rows?.['time.zone']

  return (
    <div className="space-y-6">
      <Failure error={error} />
      {folder && (
        <TextSetting
          hint={
            <>
              {t.settings.folderHint} <code>{folder.default}</code>
            </>
          }
          label={t.settings.folder}
          row={folder}
          save={save}
          status={status['workspace.folder']}
        >
          {!remote && (
            <button
              className={SECONDARY}
              disabled={folder.variable !== null}
              onClick={() => {
                void window.atlas.chooseFolder().then(chosen => {
                  if (chosen) {
                    void save('workspace.folder', [{ key: 'folder', section: 'workspace', value: chosen }])
                  }
                })
              }}
              type="button"
            >
              {t.settings.choose}
            </button>
          )}
        </TextSetting>
      )}
      {zone && (
        <TextSetting
          hint={t.settings.zoneHint}
          label={t.settings.zone}
          row={zone}
          save={save}
          status={status['time.zone']}
        />
      )}
    </div>
  )
}

const SELECT = 'rounded-md border border-border-strong bg-raised px-2 py-1.5 text-sm disabled:opacity-60'
const PRIMARY =
  'shrink-0 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-40'
const SECONDARY =
  'shrink-0 rounded-md border border-border-strong px-3 py-1.5 text-sm text-soft hover:bg-muted disabled:opacity-40'
