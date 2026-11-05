import { useStore } from '@nanostores/react'
import { type ReactNode, useEffect, useState } from 'react'

import { t } from '@/i18n'
import type { RoutineSave } from '@/protocol/protocol.generated'
import { $bots, $modelOptions, $routineForm, $workbench, closeRoutineForm, saveRoutine } from '@/store/app'

/** Who runs a routine's prompt: Atlas's model, a bot, or an outside executor in a folder. */
type Runner = 'atlas' | 'bot' | 'executor'

const EFFORTS = ['low', 'medium', 'high']

/**
 * A prompt routine made or changed from the Routines screen (step 25), in place of "making one
 * stays with the conversation and the terminal" (11/10/2026): its prompt, when it runs (a schedule,
 * and a probe that wakes it), who runs it (Atlas, a bot, or an executor in a folder, in the mode
 * chosen here), its model and effort, and Run now. A template's Add opens it filled in.
 */
export function RoutineFormHost() {
  const draft = useStore($routineForm)

  // A key per routine, so a new form never starts with the last one's fields.
  return draft ? <Form draft={draft} key={draft.routine_id ?? `new:${draft.name}`} /> : null
}

function Form({ draft }: { draft: RoutineSave }) {
  const changing = Boolean(draft.routine_id)
  const [name, setName] = useState(draft.name)
  const [prompt, setPrompt] = useState(draft.prompt)
  const [schedule, setSchedule] = useState(draft.schedule)
  const [probe, setProbe] = useState(draft.probe ?? '')
  const [runner, setRunner] = useState<Runner>(draft.executor ? 'executor' : draft.bot ? 'bot' : 'atlas')
  const [bot, setBot] = useState(draft.bot ?? '')
  const [executor, setExecutor] = useState(draft.executor?.name ?? '')
  const [folder, setFolder] = useState(draft.executor?.folder ?? '')
  const [auto, setAuto] = useState(draft.executor?.auto ?? false)
  const [workspace, setWorkspace] = useState(draft.executor?.workspace ?? false)
  const [pullRequest, setPullRequest] = useState(draft.executor?.draft ?? false)
  const [model, setModel] = useState(draft.model ?? '')
  const [effort, setEffort] = useState(draft.effort ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const bots = useStore($bots)
  const executors = useStore($workbench).executors.filter(found => found.installed)
  const models = useStore($modelOptions)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeRoutineForm()
      }
    }

    window.addEventListener('keydown', onKey)

    return () => {
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const save = (runNow: boolean) => {
    const saved: RoutineSave = {
      bot: runner === 'bot' ? bot : '',
      effort: runner === 'executor' ? '' : effort,
      executor:
        runner === 'executor'
          ? { auto, draft: workspace && pullRequest, folder: folder.trim(), name: executor, workspace }
          : null,
      model: runner === 'executor' ? '' : model,
      name: name.trim(),
      probe: probe.trim(),
      prompt: prompt.trim(),
      routine_id: draft.routine_id ?? '',
      schedule: schedule.trim()
    }
    setBusy(true)
    setError('')
    saveRoutine(saved, runNow)
      .catch((failure: unknown) => {
        setError(failure instanceof Error ? failure.message : String(failure))
      })
      .finally(() => {
        setBusy(false)
      })
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim p-4"
      data-testid="routine-form"
      onMouseDown={event => {
        if (event.target === event.currentTarget) {
          closeRoutineForm()
        }
      }}
      role="presentation"
    >
      <form
        aria-label={changing ? t.routineForm.editTitle : t.routineForm.newTitle}
        className="flex max-h-full w-full max-w-xl flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-raised p-5 shadow-xl"
        onSubmit={event => {
          event.preventDefault()
          save(false)
        }}
        role="dialog"
      >
        <h2 className="text-base font-semibold">{changing ? t.routineForm.editTitle : t.routineForm.newTitle}</h2>
        <Field label={t.routineForm.name}>
          <input
            autoFocus={!changing}
            className={INPUT}
            data-testid="routine-form-name"
            onChange={event => {
              setName(event.target.value)
            }}
            value={name}
          />
        </Field>
        <Field label={t.routineForm.prompt}>
          <textarea
            className={`${INPUT} min-h-24`}
            data-testid="routine-form-prompt"
            onChange={event => {
              setPrompt(event.target.value)
            }}
            value={prompt}
          />
        </Field>
        <Field hint={t.routineForm.scheduleHint} label={t.routineForm.schedule}>
          <input
            className={INPUT}
            data-testid="routine-form-schedule"
            onChange={event => {
              setSchedule(event.target.value)
            }}
            placeholder="every day at 9am"
            value={schedule}
          />
        </Field>
        <Field hint={t.routineForm.probeHint} label={t.routineForm.probe}>
          <input
            className={`${INPUT} font-mono`}
            data-testid="routine-form-probe"
            onChange={event => {
              setProbe(event.target.value)
            }}
            value={probe}
          />
        </Field>
        <Field label={t.routineForm.runner}>
          <select
            className={INPUT}
            data-testid="routine-form-runner"
            onChange={event => {
              setRunner(event.target.value as Runner)
            }}
            value={runner}
          >
            <option value="atlas">{t.routineForm.runners.atlas}</option>
            {bots.length > 0 && <option value="bot">{t.routineForm.runners.bot}</option>}
            {executors.length > 0 && <option value="executor">{t.routineForm.runners.executor}</option>}
          </select>
        </Field>
        {runner === 'bot' && (
          <Field label={t.routineForm.bot}>
            <select
              className={INPUT}
              data-testid="routine-form-bot"
              onChange={event => {
                setBot(event.target.value)
              }}
              value={bot}
            >
              <option value="" />
              {bots.map(found => (
                <option key={found.name} value={found.name}>
                  {found.title || found.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        {runner === 'executor' && (
          <>
            <Field label={t.routineForm.executor}>
              <select
                className={INPUT}
                data-testid="routine-form-executor"
                onChange={event => {
                  setExecutor(event.target.value)
                }}
                value={executor}
              >
                <option value="" />
                {executors.map(found => (
                  <option key={found.name} value={found.name}>
                    {found.title}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={t.routineForm.folder}>
              <input
                className={`${INPUT} font-mono`}
                data-testid="routine-form-folder"
                onChange={event => {
                  setFolder(event.target.value)
                }}
                placeholder="/home/me/code/shop"
                value={folder}
              />
            </Field>
            <Check checked={auto} label={t.routineForm.auto} onChange={setAuto} testId="routine-form-auto" />
            <Check
              checked={workspace}
              label={t.routineForm.workspace}
              onChange={setWorkspace}
              testId="routine-form-workspace"
            />
            {workspace && (
              <Check
                checked={pullRequest}
                label={t.routineForm.draft}
                onChange={setPullRequest}
                testId="routine-form-draft"
              />
            )}
            <p className="text-xs text-muted-foreground">{t.routineForm.unwatched}</p>
          </>
        )}
        {runner !== 'executor' && (
          <div className="flex gap-3">
            <Field label={t.routineForm.model}>
              <select
                className={INPUT}
                data-testid="routine-form-model"
                onChange={event => {
                  setModel(event.target.value)
                }}
                value={model}
              >
                <option value="">{t.routineForm.modelOfHome}</option>
                {models?.providers.map(provider => (
                  <optgroup key={provider.id} label={provider.name}>
                    {provider.models.map(option => (
                      <option key={option.id} value={`${provider.id}/${option.id}`}>
                        {option.name}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </Field>
            <Field label={t.routineForm.effort}>
              <select
                className={INPUT}
                data-testid="routine-form-effort"
                disabled={!model}
                onChange={event => {
                  setEffort(event.target.value)
                }}
                value={effort}
              >
                <option value="">{t.routineForm.effortOfHome}</option>
                {EFFORTS.map(level => (
                  <option key={level} value={level}>
                    {t.routineForm.efforts[level as keyof typeof t.routineForm.efforts]}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        )}
        {error && (
          <p className="text-sm text-failed-text" data-testid="routine-form-error" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-1">
          <button className={BUTTON} onClick={closeRoutineForm} type="button">
            {t.routineForm.cancel}
          </button>
          <button
            className={BUTTON}
            data-testid="routine-form-run"
            disabled={busy}
            onClick={() => {
              save(true)
            }}
            type="button"
          >
            {t.routineForm.saveAndRun}
          </button>
          <button
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            data-testid="routine-form-save"
            disabled={busy}
            type="submit"
          >
            {t.routineForm.save}
          </button>
        </div>
      </form>
    </div>
  )
}

function Field({ children, hint, label }: { children: ReactNode; hint?: string; label: string }) {
  return (
    <label className="block flex-1 space-y-1 text-sm">
      <span className="flex items-baseline justify-between gap-2">
        <span className="font-medium">{label}</span>
        {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
      </span>
      {children}
    </label>
  )
}

function Check({
  checked,
  label,
  onChange,
  testId
}: {
  checked: boolean
  label: string
  onChange: (checked: boolean) => void
  testId: string
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input
        checked={checked}
        data-testid={testId}
        onChange={event => {
          onChange(event.target.checked)
        }}
        type="checkbox"
      />
      {label}
    </label>
  )
}

const INPUT = 'w-full rounded-md border border-border-strong bg-background px-2 py-1.5 text-sm disabled:opacity-60'
const BUTTON = 'rounded-md border border-border-strong px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-40'
