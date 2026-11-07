import type { Icon } from '@tabler/icons-react'
import type { ReactNode } from 'react'

import { t } from '@/i18n'

import { BotsList } from './bots-list'
import { ExecutorsList } from './executors-list'
import { Conversations } from './sidebar'
import { Messages, Robot, Terminal } from './ui/icons'
import { Wordmark } from './wordmark'

/**
 * The start screen, what a new tab opens on (asked for by the user on 01/11/2026): three cards side
 * by side, Atlas on the left, the bots in the middle and the executors on the right. Each has its
 * name set in characters on top, as the website's title, then its name and what it is for, then
 * the sidebar's own list: a new conversation, bot or session first, then the rows to open. What is
 * chosen there opens in the tab, which leaves the start screen for it.
 */
export function StartScreen() {
  return (
    <div className="@container min-h-0 flex-1 overflow-y-auto" data-testid="start-screen">
      <div className="mx-auto grid h-full max-w-6xl grid-cols-1 gap-4 p-6 @3xl:grid-cols-3">
        <Card hint={t.start.atlas} icon={Messages} testId="start-atlas" title="Atlas">
          <Conversations bare />
        </Card>
        <Card hint={t.start.bots} icon={Robot} testId="start-bots" title={t.bots.tab}>
          <BotsList />
        </Card>
        <Card hint={t.start.executors} icon={Terminal} testId="start-executors" title={t.executors.tab}>
          <ExecutorsList />
        </Card>
      </div>
    </div>
  )
}

type CardProps = {
  children: ReactNode
  hint: string
  icon: Icon
  testId: string
  title: string
}

function Card({ children, hint, icon: Glyph, testId, title }: CardProps) {
  return (
    <section
      aria-label={title}
      className="flex min-h-[28rem] min-w-0 flex-col overflow-hidden rounded-lg border border-border bg-subtle"
      data-testid={testId}
    >
      <div className="border-b border-border bg-background px-4 py-5">
        <Wordmark cellH={7} cellW={4} ratio={0.38} word={title} />
      </div>
      <header className="px-4 pt-3 pb-3">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <Glyph aria-hidden className="shrink-0 text-muted-foreground" size={16} stroke={1.75} />
          {title}
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">{children}</div>
    </section>
  )
}
