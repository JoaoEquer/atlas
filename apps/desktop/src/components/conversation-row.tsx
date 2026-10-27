import { DropdownMenu } from 'radix-ui'
import { useRef, useState } from 'react'

import { t } from '@/i18n'
import type { ConversationRow } from '@/protocol/protocol.generated'
import { keepConversation, renameConversation, selectConversation } from '@/store/app'

import { rowDot, StatusDot, useConversationDot } from './status-dot'
import { More, Pencil } from './ui/icons'
import { InlineError } from './ui/inline-error'
import { ICON_BUTTON } from './ui/tip'
import { when } from './when'

/** The longest title the server keeps (`TITLE_CHARS`, conversations/model.py). */
const NAME_CHARS = 80

/**
 * A conversation's row: its dot, and its title in bold while what its turn ended with is unseen.
 * Its menu, from the dots over its time or a right click, renames it in place, as F2 on the row
 * does (step 9e).
 */
export function Conversation({ current, row }: { current: boolean; row: ConversationRow }) {
  const dot = useConversationDot(row.id)
  const [menu, setMenu] = useState(false)
  const [naming, setNaming] = useState(false)
  // The name's field takes the focus the menu would give back to its dots, and gives it back to the
  // row as it closes, unless the user left it for somewhere else.
  const typing = useRef(false)
  const [refocus, setRefocus] = useState(false)
  const title = row.title || t.conversations.untitled

  if (naming) {
    return (
      <RenameField
        onDone={focused => {
          setRefocus(focused)
          setNaming(false)
        }}
        row={row}
        title={title}
      />
    )
  }

  return (
    <div
      className="group relative"
      onContextMenu={event => {
        event.preventDefault()
        setMenu(true)
      }}
    >
      <button
        aria-current={current ? 'page' : undefined}
        autoFocus={refocus}
        className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
          current ? 'bg-selected font-medium' : 'group-hover:bg-hover'
        }`}
        data-testid="conversation-row"
        data-unread={dot === 'unread' || undefined}
        onClick={() => void selectConversation(row.id)}
        // A click opens it in the passing tab; a double click keeps that tab.
        onDoubleClick={() => {
          keepConversation(row.id)
        }}
        onKeyDown={event => {
          if (event.key === 'F2') {
            event.preventDefault()
            setNaming(true)
          }
        }}
        title={row.title}
        type="button"
      >
        <StatusDot dot={rowDot(dot)} slot />
        <span
          className={`min-w-0 flex-1 truncate ${dot === 'unread' ? 'font-semibold' : ''}`}
          data-testid="conversation-title"
        >
          {title}
          {row.source !== 'app' && (
            <span className="ml-1 text-xs font-normal text-muted-foreground" data-testid="conversation-via">
              {t.conversations.via(row.source)}
            </span>
          )}
        </span>
        {/* Its menu's dots take its place while the pointer or the keyboard is on the row. */}
        <span
          className={`shrink-0 text-xs font-normal text-muted-foreground ${menu ? 'invisible' : HIDE_ON_HOVER}`}
          data-testid="conversation-when"
        >
          {when(row.updated_at)}
        </span>
      </button>
      <DropdownMenu.Root onOpenChange={setMenu} open={menu}>
        <DropdownMenu.Trigger asChild>
          <button
            aria-label={t.conversations.menu(title)}
            className={`${ICON_BUTTON} absolute top-1/2 right-1 -translate-y-1/2 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100`}
            data-testid="conversation-menu"
            type="button"
          >
            <More aria-hidden size={16} stroke={1.75} />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Content
            align="end"
            className="z-50 w-48 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg"
            data-testid="conversation-menu-content"
            onCloseAutoFocus={event => {
              if (typing.current) {
                typing.current = false
                event.preventDefault()
              }
            }}
            sideOffset={4}
          >
            <DropdownMenu.Item
              className={MENU_ITEM}
              data-testid="conversation-rename"
              onSelect={() => {
                typing.current = true
                setNaming(true)
              }}
            >
              <Pencil aria-hidden size={15} stroke={1.75} />
              <span className="min-w-0 flex-1">{t.conversations.rename}</span>
              <kbd aria-hidden className="font-sans text-xs text-muted-foreground">
                F2
              </kbd>
            </DropdownMenu.Item>
          </DropdownMenu.Content>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>
    </div>
  )
}

/**
 * A conversation's name in its row's place, while the user gives it a new one: Enter, or leaving
 * the field, keeps the name, and Esc the old one. A refusal keeps the field open, saying why.
 * `onDone` says whether the field still had the focus, for the row to take it back.
 */
function RenameField({
  onDone,
  row,
  title
}: {
  onDone: (focused: boolean) => void
  row: ConversationRow
  title: string
}) {
  const [text, setText] = useState(row.title)
  const [error, setError] = useState('')
  const field = useRef<HTMLInputElement>(null)
  // Enter, then the blur as the field goes, would save twice; Esc, then that blur, once.
  const settled = useRef(false)
  const saving = useRef(false)

  const done = () => {
    settled.current = true
    onDone(document.activeElement === field.current)
  }

  const save = async () => {
    if (settled.current || saving.current) {
      return
    }

    if (!text.trim() || text.trim() === row.title) {
      done()

      return
    }

    saving.current = true

    try {
      await renameConversation(row.id, text)
      done()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      saving.current = false
    }
  }

  // Its words where the title's were, past the dot's place and inside the border.
  return (
    <div className="py-px">
      <input
        aria-label={t.conversations.nameField(title)}
        autoFocus
        className="w-full rounded-md border border-border bg-background py-1 pr-2 pl-[23px] text-sm"
        data-testid="conversation-name-field"
        maxLength={NAME_CHARS}
        onBlur={() => void save()}
        onChange={event => {
          setText(event.target.value)
          setError('')
        }}
        onFocus={event => {
          event.target.select()
        }}
        onKeyDown={event => {
          if (event.key === 'Enter') {
            event.preventDefault()
            void save()
          } else if (event.key === 'Escape') {
            // The field's Esc, not the window's.
            event.preventDefault()
            event.stopPropagation()
            done()
          }
        }}
        ref={field}
        value={text}
      />
      {error && (
        <div className="mt-1">
          <InlineError testId="conversation-name-error">{t.conversations.notRenamed(error)}</InlineError>
        </div>
      )}
    </div>
  )
}

const HIDE_ON_HOVER = 'group-focus-within:invisible group-hover:invisible'
const MENU_ITEM =
  'flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-sm outline-none select-none data-disabled:opacity-40 data-highlighted:bg-muted'
