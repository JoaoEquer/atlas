import { useStore } from '@nanostores/react'
import { DropdownMenu } from 'radix-ui'
import { useEffect } from 'react'

import { locale, t } from '@/i18n'
import type { InstanceRow } from '@/protocol/protocol.generated'
import { $connection, $machine } from '@/store/app'
import {
  $folderGit,
  $sessionContext,
  contextOf,
  folderGitOf,
  readFolderGit,
  readSessionContext,
  startWorktree
} from '@/store/environment'
import { machineName } from '@/store/machines'

import { Branch, Check, ChevronDown, Computer, Folder } from './ui/icons'
import { PILL } from './ui/pill'

const HEADING = 'text-xs font-semibold tracking-wide text-muted-foreground uppercase'

/** The last part of a folder's path, as a pill names it. */
function baseName(folder: string): string {
  return folder.split(/[\\/]/).filter(Boolean).pop() ?? folder
}

/** The folder's git, read when it is shown and whenever the folder changes. */
function useFolderGit(folder: string) {
  const { state } = useStore($connection)
  const git = folderGitOf(useStore($folderGit), folder)

  useEffect(() => {
    if (state === 'open' && folder) {
      void readFolderGit(folder)
    }
  }, [folder, state])

  return git
}

/**
 * Where an executor's session works, a pill by its box (step 18g, asked for by the user on
 * 01/11/2026): its project and the branch checked out there. Its menu says the folder whole, and
 * in a git repository offers a new worktree beside it, where a new session works apart from the
 * folder (decided with the user on 01/11/2026: a worktree is the executors', never Atlas's own
 * conversation's or a bot's); the cloud waits for Atlas Cloud.
 */
export function WherePill({ executor, folder }: { executor: string; folder: string }) {
  const git = useFolderGit(folder)
  const { state } = useStore($connection)

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild disabled={state !== 'open'}>
        <button className={`${PILL} max-w-72`} data-testid="where-select" title={folder} type="button">
          <Folder aria-hidden className="shrink-0" size={14} stroke={1.75} />
          <span className="truncate">{baseName(folder)}</span>
          {git?.repository && (
            <>
              <Branch aria-hidden className="shrink-0" size={14} stroke={1.75} />
              <span className="truncate" data-testid="where-branch">
                {git.branch}
              </span>
            </>
          )}
          <ChevronDown aria-hidden className="shrink-0 text-muted-foreground" size={12} stroke={2} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          className="z-50 w-72 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg"
          data-testid="where-menu"
          side="top"
          sideOffset={6}
        >
          <DropdownMenu.Label className="truncate px-2 pt-1 pb-0.5 text-xs text-muted-foreground" title={folder}>
            {folder}
          </DropdownMenu.Label>
          <DropdownMenu.Item className={ITEM} data-testid="where-local" disabled={!git?.worktree}>
            <Computer aria-hidden size={14} stroke={1.75} />
            <span className="flex-1">{t.environment.local}</span>
            {!git?.worktree && <Check aria-hidden size={14} stroke={2} />}
          </DropdownMenu.Item>
          {git?.repository && (
            <DropdownMenu.Item
              className={ITEM}
              data-testid="where-worktree"
              onSelect={() => {
                if (!git.worktree) {
                  void startWorktree(executor, folder)
                }
              }}
            >
              <Branch aria-hidden size={14} stroke={1.75} />
              <span className="min-w-0 flex-1">
                <span className="block">{git.worktree ? t.environment.worktree : t.environment.newWorktree}</span>
                {!git.worktree && (
                  <span className="block text-xs text-muted-foreground">
                    {t.environment.newWorktreeHint(git.branch)}
                  </span>
                )}
              </span>
              {git.worktree && <Check aria-hidden size={14} stroke={2} />}
            </DropdownMenu.Item>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

const ITEM =
  'flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-sm outline-none select-none data-disabled:opacity-60 data-highlighted:bg-muted'

/** The session's environment, in the side panel: the machine, the folder and its branch. */
export function EnvironmentSection({ folder }: { folder: string }) {
  const git = useFolderGit(folder)
  const machine = machineName(useStore($machine))

  return (
    <section className="space-y-1.5" data-testid="environment">
      <h3 className={HEADING}>{t.environment.title}</h3>
      <p className="flex items-center gap-2">
        <Computer aria-hidden className="shrink-0 text-muted-foreground" size={14} stroke={1.75} />
        <span className="truncate">{machine}</span>
      </p>
      <p className="flex items-center gap-2" title={folder}>
        <Folder aria-hidden className="shrink-0 text-muted-foreground" size={14} stroke={1.75} />
        <span className="shrink-0">{baseName(folder)}</span>
        <span className="truncate text-xs text-muted-foreground">{folder}</span>
      </p>
      {git?.repository && (
        <p className="flex items-center gap-2" data-testid="environment-branch">
          <Branch aria-hidden className="shrink-0 text-muted-foreground" size={14} stroke={1.75} />
          <span className="truncate">{git.branch}</span>
          {git.worktree && <span className="text-xs text-muted-foreground">{t.environment.worktree}</span>}
        </p>
      )}
    </section>
  )
}

/** How full the session's context is, read again each time a turn ends: its tokens against its
 * model's window, when the catalog knows the window; its harness not saying, nothing. */
export function ContextSection({ instance }: { instance: InstanceRow }) {
  const read = contextOf(useStore($sessionContext), instance.instance_id)
  const { state } = useStore($connection)

  useEffect(() => {
    if (state === 'open' && instance.state !== 'working') {
      void readSessionContext(instance.instance_id)
    }
  }, [instance.instance_id, instance.state, state])

  if (read?.tokens == null) {
    return null
  }

  const count = (value: number) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: 1, notation: 'compact' }).format(value)
  const used = read.window ? Math.min(100, Math.round((read.tokens / read.window) * 100)) : null

  return (
    <section className="space-y-1.5" data-testid="session-context">
      <h3 className={HEADING}>{t.environment.context}</h3>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span>{used === null ? t.environment.tokens(count(read.tokens)) : t.executorPanel.used(used)}</span>
        {read.window && (
          <span className="text-muted-foreground" data-testid="session-context-count">
            {count(read.tokens)} / {count(read.window)}
          </span>
        )}
      </div>
      {used !== null && (
        <div
          aria-label={t.environment.context}
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={used}
          className="h-1.5 overflow-hidden rounded-full bg-selected"
          role="progressbar"
        >
          <div className="h-full rounded-full bg-muted-foreground" style={{ width: `${String(used)}%` }} />
        </div>
      )}
    </section>
  )
}
