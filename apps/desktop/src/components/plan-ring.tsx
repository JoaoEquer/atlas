import { useStore } from '@nanostores/react'
import { useEffect } from 'react'

import { locale, t } from '@/i18n'
import { $connection, $executorUsage, readUsageIfStale } from '@/store/app'
import { level, resetsIn, windowName } from '@/store/usage'

import { Tip } from './ui/tip'
import { useNow } from './use-now'

const RING: Record<ReturnType<typeof level>, string> = {
  full: 'text-failed',
  high: 'text-waiting',
  ok: 'text-muted-foreground'
}

const RADIUS = 6
const AROUND = 2 * Math.PI * RADIUS

/**
 * How much of its plan the session's account has used, a ring by the box (step 18g, asked for by
 * the user on 01/11/2026): the fullest of its plan's limits, and on hover each limit with when it
 * starts again. Its plugin reads the windows (`executor.usage`, the side panel's Plan usage); a
 * login with no plan limits, or a plugin that does not read them, shows no ring.
 */
export function PlanRing({ executor }: { executor: string }) {
  const read = useStore($executorUsage)[executor]
  const { state } = useStore($connection)
  const now = useNow(true, 30_000)

  useEffect(() => {
    if (state === 'open') {
      readUsageIfStale(executor)
    }
  }, [executor, state])

  const windows = read?.usage?.listed ? read.usage.windows : []
  const fullest = windows.reduce<(typeof windows)[number] | undefined>(
    (most, window) => (!most || window.used > most.used ? window : most),
    undefined
  )

  if (!fullest) {
    return null
  }

  const used = Math.round(fullest.used)
  const label = (
    <span className="block space-y-1" data-testid="plan-ring-detail">
      {windows.map(window => {
        const resets = resetsIn(window.resets_at, now, locale)

        return (
          <span className="block" key={window.id}>
            <span className="block font-medium">
              {t.planRing.used(Math.round(window.used), windowName(window, t.executorPanel.windows))}
            </span>
            {resets && <span className="block opacity-80">{t.executorPanel.resets(resets)}</span>}
          </span>
        )
      })}
    </span>
  )

  return (
    <Tip label={label}>
      <span
        aria-label={t.planRing.used(used, windowName(fullest, t.executorPanel.windows))}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={used}
        className={`inline-flex h-7 shrink-0 items-center px-1 ${RING[level(fullest.used)]}`}
        data-testid="plan-ring"
        role="meter"
        tabIndex={0}
      >
        <svg aria-hidden className="-rotate-90" height="16" viewBox="0 0 16 16" width="16">
          <circle
            className="text-selected"
            cx="8"
            cy="8"
            fill="none"
            r={RADIUS}
            stroke="currentColor"
            strokeWidth="2"
          />
          <circle
            cx="8"
            cy="8"
            fill="none"
            r={RADIUS}
            stroke="currentColor"
            strokeDasharray={AROUND}
            strokeDashoffset={AROUND * (1 - Math.min(used, 100) / 100)}
            strokeLinecap="round"
            strokeWidth="2"
          />
        </svg>
      </span>
    </Tip>
  )
}
