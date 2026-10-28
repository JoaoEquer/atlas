import { useStore } from '@nanostores/react'
import { DropdownMenu } from 'radix-ui'
import { type DragEvent, useRef, useState } from 'react'

import { t } from '@/i18n'
import type { ExecutorRow, SessionFolderRow, SessionRow } from '@/protocol/protocol.generated'
import {
  $home,
  $openExecutors,
  $openFolders,
  $openSession,
  $read,
  $readError,
  $sessionFolders,
  $sessions,
  $used,
  $view,
  $workbench,
  $workbenchError,
  canChooseFolder,
  chooseFolder,
  deleteSession,
  folderKey,
  newSession,
  openSession,
  refreshFolders,
  refreshSessions,
  toggleExecutor,
  toggleFolder
} from '@/store/app'
import {
  $archived,
  $archivedSessions,
  $showArchived,
  archiveKey,
  setArchived,
  setSessionArchived,
  setShowArchived
} from '@/store/archive'
import { $executorOrder, inOrder, moveBlock, setExecutorOrder } from '@/store/order'
import { openSettings } from '@/store/settings'
import { byLastUse, instanceKey, lastUse } from '@/store/status'

import { AddAccount, FolderAccount, LoginForm, RemoveAccount } from './executor-accounts'
import { ExecutorAvatar } from './executor-avatar'
import { ReadFailed } from './read-failed'
import { rowDot, StatusDot, useInstancesDot, useSessionDot, useSessionInstance } from './status-dot'
import {
  Archive,
  ArchiveOff,
  Check,
  ChevronDown,
  ChevronRight,
  Folder as FolderGlyph,
  More,
  Plus,
  Refresh,
  SwitchAccount,
  Trash,
  UserCircle
} from './ui/icons'
import { SkeletonRows } from './ui/skeleton'
import { ICON_BUTTON, IconButton } from './ui/tip'
import { when } from './when'

/**
 * The executors tab (step 16f, decided with the user on 15/10/2026): one row per executor on and
 * found, as the bots' rows. A click opens it in place, a tree under its row (asked for by the user
 * on 18/10/2026, where it used to open a screen of its own):
 * its folders, the most recent first, a new session in a new folder
 * being in its menu; a folder opened lists a new session in it, then its sessions, wherever each
 * began, with its state here: working, waiting for an
 * answer, by Atlas, each as a dot (status-dot.tsx), and a row closed with the most urgent of its
 * own. A folder's sessions are read only once it is opened, and kept while it is closed. Several
 * executors open at once (asked for by the user on 20/10/2026), the last opened's tab in the side
 * panel. The executors and their accounts keep the order the user drags them into (store/order.ts),
 * a harness among the others and an account among its harness's; the folders and
 * sessions put first the last the user sent a message to or got an answer from (asked for by the
 * user on 22/10/2026, where any work moved every level). A folder archived leaves the
 * list for the executor's "Archived" (store/archive.ts), and a session for its folder's. Each other
 * account is a row of its own (step 16h), with its own folders and sessions; a session is deleted
 * there, on that account alone, after a yes (asked for by the user on 19/10/2026).
 */
export function ExecutorsList() {
  const { executors } = useStore($workbench)
  const read = useStore($read).workbench
  const failed = useStore($readError).workbench
  const home = useStore($home)
  const order = useStore($executorOrder)[home ?? ''] ?? []
  const on = inOrder(
    executors.filter(row => row.enabled && row.installed && row.supported),
    row => row.name,
    order
  )
  const takeAccounts = on.filter(row => row.account_folders && !row.base)
  const harnesses = [...new Set(on.map(harnessOf))].map(name => on.filter(row => harnessOf(row) === name))
  const reorder = useReorder(home, harnesses)

  return (
    <>
      {on.length > 0 && (
        <div className="space-y-1.5 px-3 pb-2">
          <NewSessionTop executors={on} />
          {/* Above the executors' list too (asked for by the user on 19/10/2026). */}
          {takeAccounts.length > 0 && <AddAccount executors={takeAccounts} />}
        </div>
      )}
      <div className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {harnesses.map(rows => (
          <Harness key={rows[0] ? harnessOf(rows[0]) : ''} reorder={reorder} rows={rows} />
        ))}
        {!read &&
          on.length === 0 &&
          (failed ? (
            <ReadFailed error={failed} what="workbench" />
          ) : (
            <SkeletonRows count={2} label={t.executors.reading} />
          ))}
        {read && on.length === 0 && (
          <div className="space-y-1 px-2 py-1 text-sm text-muted-foreground">
            <p>{t.executors.noneOn}</p>
            <button
              className="link"
              data-testid="executors-open-settings"
              onClick={() => {
                openSettings('extensions', 'executor')
              }}
              type="button"
            >
              {t.executors.openSettings}
            </button>
          </div>
        )}
      </div>
    </>
  )
}

function harnessOf(row: ExecutorRow): string {
  return row.base || row.name
}

/** What is dragged: a harness with its accounts, or one account among its harness's. */
interface Drag {
  account: boolean
  harness: string
  names: string[]
}

type Reorder = ReturnType<typeof useReorder>

/** Dragging a harness or an account to where the user wants it, a line showing where it lands. */
function useReorder(home: null | string, harnesses: ExecutorRow[][]) {
  const [drag, setDrag] = useState<Drag | null>(null)
  const [over, setOver] = useState<{ after: boolean; target: string } | null>(null)
  const end = () => {
    setDrag(null)
    setOver(null)
  }
  const takes = (account: boolean, harness: string) =>
    drag !== null && drag.account === account && (!account || drag.harness === harness)

  return {
    drag,
    /** A drop by `names` (a harness's, or one account's): the dragged land just before or after them. */
    drop(event: DragEvent, account: boolean, harness: string, names: string[]) {
      if (!drag || !takes(account, harness)) {
        return
      }

      event.preventDefault()
      event.stopPropagation()
      const after = over?.after ?? false
      const anchor = after ? names.at(-1) : names[0]

      if (anchor) {
        const shown = harnesses.flat().map(row => row.name)
        setExecutorOrder(home, moveBlock(shown, drag.names, anchor, after))
      }

      end()
    },
    end,
    hover(event: DragEvent, account: boolean, harness: string, target: string) {
      if (!takes(account, harness)) {
        return
      }

      event.preventDefault()
      event.stopPropagation()
      event.dataTransfer.dropEffect = 'move'
      const box = event.currentTarget.getBoundingClientRect()
      const after = event.clientY > box.top + box.height / 2

      if (over?.target !== target || over.after !== after) {
        setOver({ after, target })
      }
    },
    /** The line over or under the row the dragged would land by. */
    line(target: string): string {
      if (over?.target !== target) {
        return ''
      }

      return over.after ? 'shadow-[inset_0_-2px_0_0_var(--foreground)]' : 'shadow-[inset_0_2px_0_0_var(--foreground)]'
    },
    start(event: DragEvent, next: Drag) {
      event.stopPropagation()
      event.dataTransfer.effectAllowed = 'move'
      event.dataTransfer.setData('text/plain', next.names.join(' '))
      setDrag(next)
    }
  }
}

/** A harness and its accounts: alone, its row says what it is; with others, its name and version
 * head them once, and each row says only its account (asked for by the user on 19/10/2026, where
 * every row repeated them). The harness is dragged by its heading, or by its row when alone; an
 * account by its row, among its harness's. */
function Harness({ reorder, rows }: { reorder: Reorder; rows: ExecutorRow[] }) {
  const own = rows.find(row => !row.base) ?? rows[0]

  if (!own) {
    return null
  }

  const harness = harnessOf(own)
  const names = rows.map(row => row.name)
  const dragged = reorder.drag?.harness === harness && !reorder.drag.account

  return (
    <div
      className={`rounded-md ${rows.length > 1 ? 'pt-1' : ''} ${dragged ? 'opacity-50' : ''} ${reorder.line(harness)}`}
      data-harness={harness}
      data-testid="harness"
      onDragOver={event => {
        reorder.hover(event, false, harness, harness)
      }}
      onDrop={event => {
        reorder.drop(event, false, harness, names)
      }}
    >
      {rows.length === 1 ? (
        <ExecutorNode drag={{ account: false, harness, names }} reorder={reorder} row={own} />
      ) : (
        <>
          <p
            className="flex cursor-grab gap-1 px-2 pb-0.5 text-xs text-muted-foreground"
            data-testid="harness-heading"
            draggable
            onDragEnd={reorder.end}
            onDragStart={event => {
              reorder.start(event, { account: false, harness, names })
            }}
          >
            <span className="truncate font-medium">{own.title}</span>
            {own.version && <span className="shrink-0">{own.version}</span>}
          </p>
          {rows.map(row => (
            <ExecutorNode
              account
              drag={{ account: true, harness, names: [row.name] }}
              key={row.name}
              reorder={reorder}
              row={row}
            />
          ))}
        </>
      )}
    </div>
  )
}

function ExecutorNode({
  account = false,
  drag,
  reorder,
  row
}: {
  account?: boolean
  drag: Drag
  reorder: Reorder
  row: ExecutorRow
}) {
  const dragged = account && reorder.drag?.account === true && reorder.drag.names[0] === row.name
  const open = useStore($openExecutors)[row.name] === true
  const dot = useInstancesDot(instance => instance.executor === row.name)
  const [doing, setDoing] = useState<'' | 'login' | 'new' | 'remove'>('')
  const Chevron = open ? ChevronDown : ChevronRight
  const title = account ? row.nickname || t.accounts.mainAccount : row.title
  const detail = [account ? '' : row.version, row.logged_in ? '' : t.executors.notLoggedIn].filter(Boolean).join(' · ')
  const done = () => {
    setDoing('')
  }

  return (
    <section
      className={`rounded-md ${account ? reorder.line(row.name) : ''} ${dragged ? 'opacity-50' : ''}`}
      data-executor={row.name}
      data-open={open}
      data-testid="executor-node"
      onDragOver={event => {
        if (account) {
          reorder.hover(event, true, drag.harness, row.name)
        }
      }}
      onDrop={event => {
        if (account) {
          reorder.drop(event, true, drag.harness, [row.name])
        }
      }}
    >
      <div
        className="group flex items-center rounded-md hover:bg-hover"
        data-testid="executor-drag"
        draggable
        onDragEnd={reorder.end}
        onDragStart={event => {
          reorder.start(event, drag)
        }}
      >
        <button
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left"
          data-executor={row.name}
          data-testid="executor-card"
          onClick={() => {
            toggleExecutor(row.name)
          }}
          type="button"
        >
          <Chevron aria-hidden className="shrink-0 text-muted-foreground" size={14} stroke={1.75} />
          <span className="relative shrink-0">
            <ExecutorAvatar row={row} />
            {!open && <StatusDot dot={dot} ring />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{title}</span>
            {detail && (
              <span
                className={`block truncate text-xs ${row.logged_in ? 'text-muted-foreground' : 'text-waiting-text'}`}
              >
                {detail}
              </span>
            )}
          </span>
        </button>
        <ExecutorMenu
          onLogin={() => {
            setDoing('login')
          }}
          onNewSession={() => {
            setDoing('new')
          }}
          onRemove={() => {
            setDoing('remove')
          }}
          row={row}
          title={title}
        />
      </div>
      {doing === 'new' && (
        <div className="px-2 pb-1">
          <NewSession executor={row.name} onDone={done} />
        </div>
      )}
      {doing === 'login' && <LoginForm executor={row} onDone={done} />}
      {doing === 'remove' && <RemoveAccount executor={row} onDone={done} />}
      {open && <ExecutorTree executor={row} />}
    </section>
  )
}

/** A row's menu, behind its three dots (asked for by the user on 21/10/2026, to keep the sidebar
 * clean): who it is logged in as, a new session (asked for by the user on 22/10/2026, where it was a
 * row of its tree), the switch to another login, whether its archived folders and sessions are
 * listed (off until turned on), and an account's removal, in red. */
function ExecutorMenu({
  onLogin,
  onNewSession,
  onRemove,
  row,
  title
}: {
  onLogin: () => void
  onNewSession: () => void
  onRemove: () => void
  row: ExecutorRow
  title: string
}) {
  const home = useStore($home)
  const shown = useStore($showArchived)[archiveKey(home, row.name)] === true
  // The login's field takes the focus the menu would give back to its dots.
  const typing = useRef(false)

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          aria-label={t.executors.menu(title)}
          className={`${ICON_BUTTON} mr-1 opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100`}
          data-testid="executor-menu"
          type="button"
        >
          <More aria-hidden size={16} stroke={1.75} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          className="z-50 w-64 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg"
          data-testid="executor-menu-content"
          onCloseAutoFocus={event => {
            if (typing.current) {
              typing.current = false
              event.preventDefault()
            }
          }}
          sideOffset={4}
        >
          <DropdownMenu.Label
            className={`flex items-center gap-2 px-2 py-1.5 text-xs ${row.logged_in ? 'text-muted-foreground' : 'text-waiting-text'}`}
            data-testid="executor-menu-account"
          >
            <UserCircle aria-hidden className="shrink-0" size={15} stroke={1.75} />
            <span className="min-w-0 flex-1 truncate">{row.logged_in ? row.account : t.executors.notLoggedIn}</span>
          </DropdownMenu.Label>
          <DropdownMenu.Separator className={SEPARATOR} />
          <DropdownMenu.Item
            className={MENU_ITEM}
            data-testid="new-session"
            onSelect={() => {
              typing.current = true
              onNewSession()
            }}
          >
            <Plus aria-hidden size={15} stroke={1.75} />
            {t.executors.newSession}
          </DropdownMenu.Item>
          <DropdownMenu.Item
            className={MENU_ITEM}
            data-testid="executor-switch-account"
            onSelect={() => {
              typing.current = true
              onLogin()
            }}
          >
            <SwitchAccount aria-hidden size={15} stroke={1.75} />
            {row.logged_in ? t.executors.account : t.executors.logIn}
          </DropdownMenu.Item>
          <DropdownMenu.CheckboxItem
            checked={shown}
            className={MENU_ITEM}
            data-testid="executor-show-archived"
            onCheckedChange={checked => {
              setShowArchived(home, row.name, checked)
            }}
            onSelect={event => {
              event.preventDefault()
            }}
          >
            <Archive aria-hidden size={15} stroke={1.75} />
            <span className="min-w-0 flex-1">{t.executors.showArchived}</span>
            <DropdownMenu.ItemIndicator>
              <Check aria-hidden size={14} stroke={2} />
            </DropdownMenu.ItemIndicator>
          </DropdownMenu.CheckboxItem>
          {row.base && (
            <>
              <DropdownMenu.Separator className={SEPARATOR} />
              <DropdownMenu.Item
                className={`${MENU_ITEM} text-failed-text data-highlighted:bg-failed/10`}
                data-testid="remove-account"
                onSelect={onRemove}
              >
                <Trash aria-hidden size={15} stroke={1.75} />
                {t.accounts.remove(row.nickname)}
              </DropdownMenu.Item>
            </>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

const MENU_ITEM =
  'flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-sm outline-none select-none data-disabled:opacity-40 data-highlighted:bg-muted'
const SEPARATOR = 'my-1 h-px bg-selected'

/** What an executor opened has, under its row. */
function ExecutorTree({ executor }: { executor: ExecutorRow }) {
  const read = useStore($sessionFolders)[executor.name]
  const home = useStore($home)
  const archived = useStore($archived)[archiveKey(home, executor.name)] ?? []
  const shown = useStore($showArchived)[archiveKey(home, executor.name)] === true
  const [showArchived, setShowArchived] = useState(false)
  const used = useStore($used)
  const folders = read?.folders ?? null
  const kept = folders?.filter(row => !archived.includes(row.folder)) ?? null
  const listed = kept && byLastUse(kept, row => lastUse(row.updated_at, used[folderKey(executor.name, row.folder)]))
  const hidden = folders?.filter(row => archived.includes(row.folder)) ?? []

  return (
    <div className="ml-3 space-y-1 border-l border-border pt-1 pl-2" data-testid="executor-tree">
      {folders === null && !read?.error && (
        <p className="px-2 py-1 text-xs text-muted-foreground" data-testid="folders-reading" role="status">
          {t.executors.readingFolders}
        </p>
      )}
      {read?.error && (
        <Failed
          error={read.error}
          onAgain={() => {
            void refreshFolders(executor.name)
          }}
        />
      )}
      {folders?.length === 0 && <p className="px-2 py-1 text-sm text-muted-foreground">{t.executors.empty}</p>}
      {listed?.map(row => (
        <Folder executor={executor} key={row.folder} row={row} />
      ))}
      {shown && hidden.length > 0 && (
        <div data-testid="archived-folders">
          <button
            aria-expanded={showArchived}
            className="flex items-center gap-1 px-2 pt-1 text-xs text-muted-foreground hover:text-foreground"
            data-testid="archived-toggle"
            onClick={() => {
              setShowArchived(!showArchived)
            }}
            type="button"
          >
            {showArchived ? (
              <ChevronDown aria-hidden size={12} stroke={1.75} />
            ) : (
              <ChevronRight aria-hidden size={12} stroke={1.75} />
            )}
            <Archive aria-hidden size={13} stroke={1.75} />
            {t.executors.archivedFolders(hidden.length)}
          </button>
          {showArchived &&
            hidden.map(row => (
              <div
                className="group flex items-center gap-2 px-2 py-0.5 text-xs text-muted-foreground"
                data-folder={row.folder}
                data-testid="archived-folder"
                key={row.folder}
              >
                <span className="min-w-0 flex-1 truncate" title={row.folder}>
                  {baseName(row.folder)}
                </span>
                <IconButton
                  icon={ArchiveOff}
                  label={t.executors.unarchive(baseName(row.folder))}
                  onClick={() => {
                    setArchived(home, executor.name, row.folder, false)
                  }}
                  testId="unarchive-folder"
                />
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

/** A folder of the list: its name, when it last changed and, closed, whether something of it
 * waits or works; opened, its sessions, read the first time. */
function Folder({ executor: node, row }: { executor: ExecutorRow; row: SessionFolderRow }) {
  const executor = node.name
  const key = folderKey(executor, row.folder)
  const open = useStore($openFolders)[key] === true
  const read = useStore($sessions)[key]
  const dot = useInstancesDot(instance => instance.executor === executor && instance.folder === row.folder)
  const home = useStore($home)
  const archived = useStore($archivedSessions)[archiveKey(home, executor)] ?? []
  const shown = useStore($showArchived)[archiveKey(home, executor)] === true
  const used = useStore($used)
  const [showArchived, setShowArchived] = useState(false)
  const all = read?.sessions ?? null
  const kept = all?.filter(found => !archived.includes(found.session_id)) ?? null
  const sessions = kept && byLastUse(kept, found => lastUse(found.updated_at, used[instanceKey(found.instance_id)]))
  const hidden = all?.filter(found => found.session_id && archived.includes(found.session_id)) ?? []
  const Chevron = open ? ChevronDown : ChevronRight

  return (
    <section data-folder={row.folder} data-open={open} data-testid="session-folder">
      <div className="group relative flex items-center gap-1 rounded-md px-2 py-1 hover:bg-muted">
        <button
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left text-sm text-soft"
          data-testid="session-folder-toggle"
          onClick={() => {
            toggleFolder(executor, row.folder)
          }}
          title={row.folder}
          type="button"
        >
          <Chevron aria-hidden className="shrink-0 text-muted-foreground" size={12} stroke={1.75} />
          <FolderGlyph aria-hidden className="shrink-0 text-muted-foreground" size={15} stroke={1.75} />
          <span className="truncate font-medium">{baseName(row.folder)}</span>
          {!open && <StatusDot dot={dot} />}
        </button>
        {row.account_id && <FolderAccount account={row.account_id} executor={node} folder={row.folder} />}
        <span className={`shrink-0 text-xs text-muted-foreground ${HIDE_ON_HOVER}`}>{when(row.updated_at)}</span>
        <span className={`${ROW_ACTIONS} top-1/2 -translate-y-1/2 bg-muted`}>
          {!row.account_id && <FolderAccount account={row.account_id} executor={node} folder={row.folder} />}
          <IconButton
            icon={Archive}
            label={t.executors.archive(baseName(row.folder))}
            onClick={() => {
              setArchived(home, executor, row.folder, true)
            }}
            testId="archive-folder"
          />
        </span>
      </div>
      {open && (
        <div className="ml-3.5 border-l border-border pl-1.5" data-testid="folder-sessions">
          {/* A new conversation in the folder, its first row and always there (asked for by the user
              on 22/10/2026: on the folder's row, on hover, it was taken for a new folder's). */}
          <button
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
            data-testid="folder-new-session"
            onClick={() => {
              newSession(executor, row.folder)
            }}
            title={t.executors.newSessionIn(baseName(row.folder))}
            type="button"
          >
            <span aria-hidden className="grid h-2 w-2 shrink-0 place-items-center">
              <Plus className="shrink-0" size={13} stroke={2} />
            </span>
            {t.executors.newSession}
          </button>
          {sessions === null && !read?.error && (
            <p className="px-2 py-1 text-xs text-muted-foreground" data-testid="sessions-reading" role="status">
              {t.executors.readingSessions}
            </p>
          )}
          {read?.error && (
            <Failed
              error={read.error}
              onAgain={() => {
                void refreshSessions(executor, row.folder)
              }}
            />
          )}
          {sessions?.length === 0 && (
            <p className="px-2 py-1 text-sm text-muted-foreground">{t.executors.emptyFolder}</p>
          )}
          <ul>
            {sessions?.map(session => (
              <li key={session.session_id || session.instance_id}>
                <SessionItem executor={executor} row={session} />
              </li>
            ))}
          </ul>
          {shown && hidden.length > 0 && (
            <div data-testid="archived-sessions">
              <button
                aria-expanded={showArchived}
                className="flex items-center gap-1 px-2 pt-1 text-xs text-muted-foreground hover:text-foreground"
                data-testid="archived-sessions-toggle"
                onClick={() => {
                  setShowArchived(!showArchived)
                }}
                type="button"
              >
                {showArchived ? (
                  <ChevronDown aria-hidden size={12} stroke={1.75} />
                ) : (
                  <ChevronRight aria-hidden size={12} stroke={1.75} />
                )}
                <Archive aria-hidden size={13} stroke={1.75} />
                {t.executors.archivedSessions(hidden.length)}
              </button>
              {showArchived &&
                hidden.map(session => (
                  <div
                    className="group flex items-center gap-2 px-2 py-0.5 text-xs text-muted-foreground"
                    data-session={session.session_id}
                    data-testid="archived-session"
                    key={session.session_id}
                  >
                    <span className="min-w-0 flex-1 truncate" title={session.title}>
                      {session.title || t.executors.untitled}
                    </span>
                    <IconButton
                      icon={ArchiveOff}
                      label={t.executors.unarchiveSession(session.title || t.executors.untitled)}
                      onClick={() => {
                        setSessionArchived(home, executor, session.session_id, false)
                      }}
                      testId="unarchive-session"
                    />
                  </div>
                ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

/** Why a list could not be read, and a way to read it again. */
function Failed({ error, onAgain }: { error: string; onAgain: () => void }) {
  return (
    <div className="space-y-1 px-2 py-1">
      <p className="text-xs text-failed-text" role="alert">
        {t.executors.failed(error)}
      </p>
      <button
        className="flex items-center gap-1 rounded px-2 py-0.5 text-xs text-muted-foreground hover:bg-selected"
        onClick={onAgain}
        type="button"
      >
        <Refresh aria-hidden size={13} stroke={1.75} />
        {t.executors.readAgain}
      </button>
    </div>
  )
}

/** A session of the list, and what is done to it from there: archive it, or delete it on its
 * account after a yes; neither while an instance works on it. */
function SessionItem({ executor, row }: { executor: string; row: SessionRow }) {
  const home = useStore($home)
  const [asking, setAsking] = useState(false)
  const error = useStore($workbenchError)[`delete:${row.executor}`]
  const title = row.title || t.executors.untitled
  const live = useSessionInstance(row)?.state
  const busy = live === 'working' || live === 'starting'
  const current = useIsOpen(row)

  return (
    <div className="group" data-testid="session-item">
      <div className="relative">
        <SessionButton actions={Boolean(row.session_id) && !busy} row={row} />
        {row.session_id && !busy && (
          <span className={`${ROW_ACTIONS} top-1 ${current ? 'bg-selected' : 'bg-muted'}`}>
            <IconButton
              icon={Archive}
              label={t.executors.archiveSession(title)}
              onClick={() => {
                setSessionArchived(home, executor, row.session_id, true)
              }}
              testId="archive-session"
            />
            <IconButton
              icon={Trash}
              label={t.executors.deleteSession(title)}
              onClick={() => {
                setAsking(true)
              }}
              testId="delete-session"
            />
          </span>
        )}
      </div>
      {asking && (
        <div className="space-y-1 px-2 pb-1 text-xs" data-testid="delete-session-confirm">
          <p className="text-soft">{t.executors.deleteSessionNote}</p>
          <div className="flex gap-2">
            <button
              className="rounded bg-danger px-2 py-0.5 text-white"
              onClick={() => {
                void deleteSession(row).then(done => {
                  if (done) {
                    setAsking(false)
                  }
                })
              }}
              type="button"
            >
              {t.executors.deleteSessionYes}
            </button>
            <button
              className="rounded px-2 py-0.5 text-muted-foreground"
              onClick={() => {
                setAsking(false)
              }}
              type="button"
            >
              {t.executors.cancel}
            </button>
          </div>
        </div>
      )}
      {error && asking && (
        <p className="px-2 text-xs text-failed-text" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

/** Whether the session shown is this one. */
function useIsOpen(row: SessionRow): boolean {
  const open = useStore($openSession)
  const view = useStore($view)

  return (
    view === 'session' &&
    open !== null &&
    (row.session_id ? open.sessionId === row.session_id : open.instanceId === row.instance_id)
  )
}

/** A row's actions, over its time while the pointer or the keyboard is on the row, so every time in
 * the list ends at the same edge. */
const ROW_ACTIONS =
  'pointer-events-none absolute right-1 flex rounded-md opacity-0 group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100'
const HIDE_ON_HOVER = 'group-focus-within:invisible group-hover:invisible'

function SessionButton({ actions, row }: { actions: boolean; row: SessionRow }) {
  const dot = useSessionDot(row)
  const live = useSessionInstance(row)?.state ?? ''
  const current = useIsOpen(row)
  const marks = [
    row.by_atlas ? t.executors.byAtlas : '',
    row.elsewhere ? t.executors.elsewhere(t.executors.places[row.elsewhere] ?? row.elsewhere) : ''
  ].filter(Boolean)

  return (
    <button
      aria-current={current ? 'page' : undefined}
      className={`w-full rounded-md px-2 py-1.5 text-left ${current ? 'bg-selected' : 'group-hover:bg-muted'}`}
      data-session={row.session_id}
      data-state={dot === 'waiting' ? dot : live}
      data-testid="session-row"
      onClick={() => void openSession(row)}
      title={row.title}
      type="button"
    >
      <span className="flex items-center gap-2">
        <StatusDot dot={rowDot(dot)} slot />
        <span className={`min-w-0 flex-1 truncate text-sm ${dot === 'unread' ? 'font-semibold' : ''}`}>
          {row.title || t.executors.untitled}
        </span>
        <span className={`shrink-0 text-xs text-muted-foreground ${actions ? HIDE_ON_HOVER : ''}`}>
          {when(row.updated_at)}
        </span>
      </span>
      {marks.length > 0 && (
        <span className="block truncate pl-4 text-xs text-muted-foreground" data-testid="session-marks">
          {marks.join(' · ')}
        </span>
      )}
    </button>
  )
}

/** The tab's own first button, as the conversations' and the bots' are (the UI review,
 * 26/10/2026: adding an account held that place, a rarer act, and a new session hid in each
 * executor's menu): a new session, on the executor chosen when several are on, in a folder typed or
 * picked. Adding an account comes next, above the list too. */
function NewSessionTop({ executors }: { executors: ExecutorRow[] }) {
  const [opened, setOpened] = useState(false)
  const [picked, setPicked] = useState('')
  const executor = executors.find(row => row.name === picked)?.name ?? executors[0]?.name ?? ''

  if (!opened) {
    return (
      <button
        className="flex w-full items-center gap-2 rounded-md border border-border-strong px-2 py-1.5 text-left text-sm font-medium hover:bg-muted"
        data-testid="new-session-top"
        onClick={() => {
          setOpened(true)
        }}
        type="button"
      >
        <Plus aria-hidden size={16} stroke={1.75} />
        {t.executors.newSession}
      </button>
    )
  }

  return (
    <div className="space-y-2">
      {executors.length > 1 && (
        <select
          aria-label={t.executors.which}
          className="w-full rounded border border-border-strong bg-raised px-2 py-1 text-xs"
          data-testid="new-session-executor"
          onChange={event => {
            setPicked(event.target.value)
          }}
          value={executor}
        >
          {executors.map(row => (
            <option key={row.name} value={row.name}>
              {row.title}
            </option>
          ))}
        </select>
      )}
      <NewSession
        executor={executor}
        onDone={() => {
          setOpened(false)
        }}
      />
    </div>
  )
}

/** "New session": a folder typed, or chosen in the system's dialog when the app started its own
 * server (a folder of another machine is the server's, not this one's). */
function NewSession({ executor, onDone }: { executor: string; onDone: () => void }) {
  const [folder, setFolder] = useState('')

  const start = (chosen: string) => {
    if (chosen.trim()) {
      newSession(executor, chosen.trim())
      onDone()
    }
  }

  return (
    <form
      className="space-y-2 rounded-md border border-dashed border-border-strong p-2 text-xs"
      data-testid="new-session-form"
      onSubmit={event => {
        event.preventDefault()
        start(folder)
      }}
    >
      <input
        aria-label={t.executors.folder}
        autoFocus
        className="w-full rounded border border-border-strong px-2 py-1 bg-raised"
        onChange={event => {
          setFolder(event.target.value)
        }}
        placeholder={t.executors.folderPlaceholder}
        value={folder}
      />
      <div className="flex gap-2">
        <button className="rounded bg-primary px-2 py-1 font-medium text-primary-foreground" type="submit">
          {t.executors.start}
        </button>
        {canChooseFolder() && (
          <button
            className="rounded border border-border-strong px-2 py-1"
            onClick={() => {
              void chooseFolder().then(chosen => {
                if (chosen) {
                  start(chosen)
                }
              })
            }}
            type="button"
          >
            {t.executors.choose}
          </button>
        )}
        <button className="rounded px-2 py-1 text-muted-foreground" onClick={onDone} type="button">
          {t.executors.cancel}
        </button>
      </div>
    </form>
  )
}

export function baseName(folder: string): string {
  return folder.split(/[\\/]/).filter(Boolean).pop() ?? folder
}
