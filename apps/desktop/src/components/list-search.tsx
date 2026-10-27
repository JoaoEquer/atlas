import { useStore } from '@nanostores/react'
import { Fragment, useRef } from 'react'

import { t } from '@/i18n'
import type { SearchHit } from '@/protocol/protocol.generated'
import {
  $search,
  $selected,
  $view,
  clearSearch,
  keepConversation,
  searchAgain,
  selectConversation,
  typeSearch
} from '@/store/app'

import { ReadFailed } from './read-failed'
import { Close, Search } from './ui/icons'
import { SkeletonRows } from './ui/skeleton'
import { IconButton } from './ui/tip'
import { when } from './when'

/** The list's search field (step 9e): what is typed is searched after a pause in the typing; Enter
 * opens the first conversation found, and Esc clears it. */
export function SearchField() {
  const { hits, query, text } = useStore($search)
  const field = useRef<HTMLInputElement>(null)
  const first = query === text.trim() ? hits[0] : undefined

  return (
    <div className="px-3 pb-2">
      {/* The field is the edge, so the keyboard's halo goes round it as round every field. */}
      <div className="relative">
        <Search
          aria-hidden
          className="pointer-events-none absolute top-1/2 left-2 -translate-y-1/2 text-muted-foreground"
          size={14}
          stroke={1.75}
        />
        <input
          aria-label={t.conversations.search.field}
          className="w-full rounded-md border border-border bg-background py-1 pr-8 pl-7 text-sm placeholder:text-muted-foreground"
          data-testid="conversation-search"
          onChange={event => {
            typeSearch(event.target.value)
          }}
          onKeyDown={event => {
            if (event.key === 'Escape' && text) {
              event.preventDefault()
              event.stopPropagation()
              clearSearch()
            } else if (event.key === 'Enter' && !event.nativeEvent.isComposing && first) {
              event.preventDefault()
              void selectConversation(first.conversation.id)
            }
          }}
          placeholder={t.conversations.search.field}
          ref={field}
          spellCheck={false}
          value={text}
        />
        {text && (
          <span className="absolute top-1/2 right-0.5 flex -translate-y-1/2">
            <IconButton
              icon={Close}
              label={t.conversations.search.clear}
              onClick={() => {
                clearSearch()
                field.current?.focus()
              }}
              testId="conversation-search-clear"
            />
          </span>
        )}
      </div>
    </div>
  )
}

/** What the search found, in the list's place while the field holds words (step 9e): each
 * conversation with where it matched, the words of its title or a message, and a click opens it. */
export function SearchResults() {
  const { error, hits, more, query, reading, text } = useStore($search)
  const selected = useStore($selected)
  const view = useStore($view)
  const asked = text.trim()
  const done = !reading && error === null && query === asked

  return (
    <ul
      aria-label={t.conversations.search.results}
      className="flex-1 overflow-y-auto px-2 pb-3"
      data-testid="search-results"
    >
      <li aria-live="polite" className="px-2 pb-1 text-xs text-muted-foreground" data-testid="search-status">
        {done &&
          (hits.length > 0 ? t.conversations.search.found(hits.length, more) : t.conversations.search.none(asked))}
      </li>
      {error !== null && (
        <li className="px-1">
          <ReadFailed error={error} onAgain={searchAgain} what="conversations" />
        </li>
      )}
      {error === null && !done && hits.length === 0 && (
        <li>
          <SkeletonRows count={3} label={t.conversations.search.searching} />
        </li>
      )}
      {hits.map(hit => (
        <li key={hit.conversation.id}>
          <Hit current={hit.conversation.id === selected && view === 'conversation'} hit={hit} />
        </li>
      ))}
    </ul>
  )
}

/** A conversation the search found: its title, the words that matched in bold, and, when a message
 * matched rather than the title, who said it and the words around the match. */
function Hit({ current, hit }: { current: boolean; hit: SearchHit }) {
  const { conversation } = hit

  return (
    <button
      aria-current={current ? 'page' : undefined}
      className={`flex w-full flex-col gap-0.5 rounded-md px-2 py-1.5 text-left text-sm ${
        current ? 'bg-selected' : 'hover:bg-hover'
      }`}
      data-testid="search-hit"
      onClick={() => void selectConversation(conversation.id)}
      // As a row: a click opens it in the passing tab, a double click keeps that tab.
      onDoubleClick={() => {
        keepConversation(conversation.id)
      }}
      title={conversation.title}
      type="button"
    >
      <span className="flex w-full items-center gap-2">
        <span className="min-w-0 flex-1 truncate" data-testid="search-hit-title">
          <Pieces pieces={hit.title} />
        </span>
        <span className="shrink-0 text-xs text-muted-foreground">{when(conversation.updated_at)}</span>
      </span>
      {hit.snippet.length > 0 && (
        <span className="line-clamp-2 text-xs text-muted-foreground" data-testid="search-hit-snippet">
          {hit.said_by === 'user' ? t.conversations.search.you : t.conversations.search.atlas}:{' '}
          <Pieces pieces={hit.snippet} />
        </span>
      )}
    </button>
  )
}

/** Text in pieces, the odd ones what matched. */
function Pieces({ pieces }: { pieces: string[] }) {
  return (
    <>
      {pieces.map((piece, index) =>
        index % 2 === 1 ? (
          <mark className="bg-transparent font-semibold text-foreground" data-testid="search-match" key={index}>
            {piece}
          </mark>
        ) : (
          <Fragment key={index}>{piece}</Fragment>
        )
      )}
    </>
  )
}
