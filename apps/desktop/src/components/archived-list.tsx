import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { ConversationRow } from '@/protocol/protocol.generated'
import {
  $archived,
  $selected,
  $view,
  keepConversation,
  readArchived,
  selectConversation,
  toggleArchived,
  unarchiveConversation
} from '@/store/app'

import { ReadFailed } from './read-failed'
import { Archive, ArchiveOff, ChevronDown, ChevronRight } from './ui/icons'
import { InlineError } from './ui/inline-error'
import { SkeletonRows } from './ui/skeleton'
import { IconButton } from './ui/tip'
import { when } from './when'

/**
 * The archived conversations, under the list (step 9e): a toggle that says how many, and once
 * open, each one, the latest activity first, a page at a time. A click opens one as a row of the
 * list does; its button brings it back to the list, as a new message in it does.
 */
export function ArchivedConversations() {
  const archived = useStore($archived)
  const selected = useStore($selected)
  const view = useStore($view)

  if (archived.count === 0 && archived.rows.length === 0) {
    return null
  }

  return (
    <div className="shrink-0 border-t border-border px-2 pt-1 pb-2" data-testid="archived-conversations">
      <button
        aria-expanded={archived.open}
        className="flex items-center gap-1 px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
        data-testid="archived-toggle"
        onClick={toggleArchived}
        type="button"
      >
        {archived.open ? (
          <ChevronDown aria-hidden size={12} stroke={1.75} />
        ) : (
          <ChevronRight aria-hidden size={12} stroke={1.75} />
        )}
        <Archive aria-hidden size={13} stroke={1.75} />
        {t.conversations.archived(archived.count)}
      </button>
      {archived.open && (
        <ul className="max-h-60 overflow-y-auto" data-testid="archived-list">
          {archived.rows.map(row => (
            <li key={row.id}>
              <ArchivedRow current={row.id === selected && view === 'conversation'} row={row} />
            </li>
          ))}
          {archived.reading && (
            <li>
              <SkeletonRows count={1} label={t.conversations.readingArchived} />
            </li>
          )}
          {archived.error && (
            <li className="px-1">
              <ReadFailed
                error={archived.error}
                onAgain={() => {
                  void readArchived()
                }}
                what="conversations"
              />
            </li>
          )}
          {archived.cursor && !archived.reading && !archived.error && (
            <li>
              <button
                className="px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
                data-testid="archived-more"
                onClick={() => {
                  void readArchived(true)
                }}
                type="button"
              >
                {t.conversations.showMore}
              </button>
            </li>
          )}
        </ul>
      )}
    </div>
  )
}

/** An archived conversation: its title and its time, and over the time, on hover or from the
 * keyboard, the button that brings it back to the list. */
function ArchivedRow({ current, row }: { current: boolean; row: ConversationRow }) {
  const [error, setError] = useState('')
  const title = row.title || t.conversations.untitled

  return (
    <div className="group" data-testid="archived-conversation">
      <div className="relative">
        <button
          aria-current={current ? 'page' : undefined}
          className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground ${
            current ? 'bg-selected font-medium text-foreground' : 'group-hover:bg-hover'
          }`}
          data-testid="archived-row"
          onClick={() => void selectConversation(row.id)}
          onDoubleClick={() => {
            keepConversation(row.id)
          }}
          title={row.title}
          type="button"
        >
          <span className="min-w-0 flex-1 truncate">{title}</span>
          <span className={`shrink-0 text-xs ${HIDE_ON_HOVER}`}>{when(row.updated_at)}</span>
        </button>
        <span className={`${ROW_ACTIONS} top-1/2 -translate-y-1/2`}>
          <IconButton
            icon={ArchiveOff}
            label={t.conversations.unarchive(title)}
            onClick={() => {
              setError('')
              unarchiveConversation(row.id).catch((failure: unknown) => {
                setError(failure instanceof Error ? failure.message : String(failure))
              })
            }}
            testId="unarchive-conversation"
          />
        </span>
      </div>
      {error && (
        <div className="mt-1">
          <InlineError testId="unarchive-error">{t.conversations.notUnarchived(error)}</InlineError>
        </div>
      )}
    </div>
  )
}

/** A row's button, over its time while the pointer or the keyboard is on the row. */
const ROW_ACTIONS =
  'pointer-events-none absolute right-1 flex rounded-md opacity-0 group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100'
const HIDE_ON_HOVER = 'group-focus-within:invisible group-hover:invisible'
