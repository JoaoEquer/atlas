import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { t } from '@/i18n'
import { PlaceContributions } from '@/plugins/view'
import { $plugins, enablePlugin, newConversation, refreshPlugins } from '@/store/app'
import type { Item } from '@/store/thread'

type Tool = Extract<Item, { kind: 'tool' }>

/**
 * A connection Atlas offered in the conversation (`offer_connection`, 05/11/2026), drawn as a card
 * where the user decides: off, the plugin's declaration and Turn on, which is the consent; on, the
 * plugin's own connection part (GitHub's Connect, Google's set-up), the same as in the first run.
 * The model offered; only the user's click turns anything on.
 */
export function ConnectionOffer({ tool }: { tool: Tool }) {
  const name = offered(tool.args)
  const plugins = useStore($plugins)
  const row = plugins.find(found => found.name === name)
  const [turning, setTurning] = useState(false)

  useEffect(() => {
    refreshPlugins()
  }, [])

  if (!name) {
    return null
  }

  const label = name === 'github' ? t.firstRun.github : name === 'google' ? t.firstRun.google : name
  const on = row !== undefined && row.enabled && row.consented

  return (
    <section
      className="space-y-2 rounded-lg border border-border p-4"
      data-connection={name}
      data-testid="connection-offer"
    >
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{t.connectionOffer.title(label)}</p>
          {row && !on && <p className="text-xs text-muted-foreground">{row.description}</p>}
        </div>
        {row && !on && (
          <button
            className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
            data-testid="connection-offer-turn-on"
            disabled={turning}
            onClick={() => {
              setTurning(true)
              void enablePlugin(row).finally(() => {
                setTurning(false)
              })
            }}
            type="button"
          >
            {t.firstRun.turnOn}
          </button>
        )}
      </div>
      {row && !on && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
          {row.declaration.map(line => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {!row && <p className="text-xs text-muted-foreground">{t.connectionOffer.missing}</p>}
      {on && (
        <PlaceContributions
          area="connection"
          place={{
            away: () => {
              newConversation()
            }
          }}
          plugin={name}
        />
      )}
    </section>
  )
}

/** The connection a call offered, from its arguments; empty when they are not readable yet. */
function offered(args: string): string {
  try {
    const parsed = JSON.parse(args) as { connection?: unknown }

    return typeof parsed.connection === 'string' ? parsed.connection : ''
  } catch {
    return ''
  }
}
