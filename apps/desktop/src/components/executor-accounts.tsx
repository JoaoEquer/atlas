import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { ExecutorRow } from '@/protocol/protocol.generated'
import { $workbenchError, switchExecutorAccount } from '@/store/app'

/*
 * An executor's login (step 16h): its row's menu has it, which the harness itself does on the
 * user's browser; Atlas never sees a credential. Its other accounts, their adding, removal and
 * order, are the accounts plugin's since step 22: its screen shows them under the executor.
 */

/** The switch to another login, opened from the row's menu: the user's sessions on it go on with
 * the new one, never while one works. The email is optional, the browser's login choosing the
 * account. */
export function LoginForm({ executor, onDone }: { executor: ExecutorRow; onDone: () => void }) {
  const [login, setLogin] = useState('')
  const error = useStore($workbenchError)[`account:${executor.name}`]

  return (
    <div className="space-y-1 px-2 pb-1 text-xs" data-testid="executor-account">
      <form
        className="space-y-1"
        onSubmit={event => {
          event.preventDefault()
          void switchExecutorAccount(executor.name, login).then(done => {
            if (done) {
              setLogin('')
              onDone()
            }
          })
        }}
      >
        <p className="text-soft">{t.accounts.loginNote(executor.title)}</p>
        <div className="flex gap-1">
          <input
            aria-label={t.accounts.login}
            autoFocus
            className="min-w-0 flex-1 rounded border border-border-strong px-2 py-1 bg-raised"
            onChange={event => {
              setLogin(event.target.value)
            }}
            placeholder={t.accounts.login}
            value={login}
          />
          <button className="rounded bg-primary px-2 py-1 text-primary-foreground" type="submit">
            {t.accounts.logIn}
          </button>
          <button className="rounded px-2 py-1 text-muted-foreground" onClick={onDone} type="button">
            {t.accounts.cancel}
          </button>
        </div>
      </form>
      {error && (
        <p className="text-failed-text" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
