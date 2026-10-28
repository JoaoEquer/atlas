import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { t } from '@/i18n'
import type { McpServerRow } from '@/protocol/protocol.generated'
import { $remote, signInToMcp } from '@/store/app'

import { Check, Copy } from './ui/icons'
import { IconButton } from './ui/tip'

/**
 * How to sign in to an MCP server that signs in with OAuth (`server.login`): a Sign in button
 * that opens the server's page in the browser, and the command that does the same from a
 * terminal. The browser comes back to an address Atlas listens on, on the home's machine, so a
 * home on another machine shows the command only.
 */
export function McpSignIn({ server }: { server: McpServerRow }) {
  const remote = useStore($remote)
  const [waiting, setWaiting] = useState(false)
  const [error, setError] = useState<null | string>(null)
  const [copied, setCopied] = useState(false)
  const out = server.state === 'left_out'

  // The sign-in ended: the server connected, or was left out saying why.
  useEffect(() => {
    setWaiting(false)
  }, [server.state, server.problem])

  if (!server.login) {
    return null
  }

  const command = server.login

  return (
    <div className="space-y-2 rounded-md bg-subtle p-2 text-xs" data-testid="mcp-sign-in">
      {!remote && (
        <div className="flex flex-wrap items-center gap-2">
          <button
            className={`${BUTTON} ${out ? 'bg-primary text-primary-foreground hover:bg-primary/90' : ''}`}
            data-testid="mcp-sign-in-button"
            disabled={waiting}
            onClick={() => {
              setError(null)
              setWaiting(true)
              void signInToMcp(server.name).then(why => {
                if (why !== null) {
                  setWaiting(false)
                  setError(why)
                }
              })
            }}
            type="button"
          >
            {out ? t.extensions.signInButton : t.extensions.signInAgainButton}
          </button>
          {waiting && (
            <span className="text-muted-foreground" data-testid="mcp-sign-in-waiting" role="status">
              {t.extensions.signInWaiting}
            </span>
          )}
        </div>
      )}
      {error && (
        <p className="text-failed-text select-text" data-testid="mcp-sign-in-error" role="alert">
          {error}
        </p>
      )}
      <p className={out && remote ? 'text-waiting-text' : 'text-muted-foreground'}>
        {remote ? (out ? t.extensions.signIn : t.extensions.signInAgain) : t.extensions.signInOrTerminal}
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate font-mono select-text" data-testid="mcp-login" title={command}>
          {command}
        </code>
        <IconButton
          icon={copied ? Check : Copy}
          label={copied ? t.extensions.copied : t.extensions.copy}
          onClick={() => {
            void navigator.clipboard.writeText(command).then(() => {
              setCopied(true)
            })
          }}
          testId="mcp-login-copy"
        />
      </div>
    </div>
  )
}

const BUTTON = 'rounded-md border border-border-strong px-2 py-1 text-sm hover:bg-muted disabled:opacity-40'
