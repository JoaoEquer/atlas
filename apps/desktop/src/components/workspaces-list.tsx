import { useStore } from '@nanostores/react'
import { DropdownMenu } from 'radix-ui'
import { useEffect, useState } from 'react'

import { t } from '@/i18n'
import type { ExecutorRow, WorkspaceRow } from '@/protocol/protocol.generated'
import { $connection, $workspacesSeen, newSession } from '@/store/app'
import {
  $workspaceRows,
  archiveWorkspace,
  byProject,
  createWorkspace,
  deleteWorkspace,
  listWorkspaces,
  readProject,
  saveProject,
  unarchiveWorkspace
} from '@/store/workspaces'

import { Archive, ArchiveOff, Branch, ChevronDown, ChevronRight, Folder, More, Plus, Settings, Trash } from './ui/icons'
import { IconButton } from './ui/tip'

/**
 * The home's workspaces in the executors tab (step 21, A1): each under its project, as git groups
 * them, with what it is doing (its setup script running, failed, archived). A click starts a
 * session of the first executor on in it; its own buttons archive it, bring it back or delete it,
 * the last after a yes, and a second, louder one when it holds work not delivered. A project's
 * menu makes a new workspace and sets how one is prepared: its setup script and ports. Nothing
 * shows while the home has none: the box's "New worktree" makes the first.
 */
export function WorkspacesList({ executors }: { executors: ExecutorRow[] }) {
  const rows = useWorkspaceRows()
  const [open, setOpen] = useState(true)

  if (rows.length === 0) {
    return null
  }

  const Chevron = open ? ChevronDown : ChevronRight

  return (
    <section className="space-y-1 pb-2" data-testid="workspaces">
      <button
        aria-expanded={open}
        className="flex items-center gap-1 px-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase hover:text-foreground"
        data-testid="workspaces-toggle"
        onClick={() => {
          setOpen(!open)
        }}
        type="button"
      >
        <Chevron aria-hidden size={12} stroke={2} />
        {t.workspaces.title}
      </button>
      {open &&
        byProject(rows).map(([root, workspaces]) => (
          <Project executor={executors[0]?.name ?? ''} key={root} root={root} workspaces={workspaces} />
        ))}
    </section>
  )
}

/** The home's workspaces, read while the connection is open and again whenever one changed. */
export function useWorkspaceRows(): WorkspaceRow[] {
  const seen = useStore($workspacesSeen)
  const { state } = useStore($connection)
  const rows = useStore($workspaceRows)

  useEffect(() => {
    if (state !== 'open') {
      return undefined
    }

    let live = true
    listWorkspaces().then(
      found => {
        if (live) {
          $workspaceRows.set(found)
        }
      },
      () => undefined
    )

    return () => {
      live = false
    }
  }, [seen, state])

  return rows
}

function Project({ executor, root, workspaces }: { executor: string; root: string; workspaces: WorkspaceRow[] }) {
  const [doing, setDoing] = useState<'' | 'new' | 'setup'>('')
  const done = () => {
    setDoing('')
  }

  return (
    <div data-project={root} data-testid="workspace-project">
      <div className="group flex items-center rounded-md hover:bg-muted">
        <p className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1 text-sm text-soft" title={root}>
          <Folder aria-hidden className="shrink-0 text-muted-foreground" size={15} stroke={1.75} />
          <span className="truncate font-medium">{baseName(root)}</span>
        </p>
        <ProjectMenu
          name={baseName(root)}
          onNew={() => {
            setDoing('new')
          }}
          onSetup={() => {
            setDoing('setup')
          }}
        />
      </div>
      <div className="ml-3.5 border-l border-border pl-1.5">
        {doing === 'new' && <NewWorkspace onDone={done} root={root} />}
        {doing === 'setup' && <ProjectSetup onDone={done} root={root} />}
        {workspaces.map(row => (
          <Workspace executor={executor} key={row.id} row={row} />
        ))}
      </div>
    </div>
  )
}

function ProjectMenu({ name, onNew, onSetup }: { name: string; onNew: () => void; onSetup: () => void }) {
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          aria-label={t.workspaces.projectMenu(name)}
          className="mr-1 rounded p-1 text-muted-foreground opacity-0 group-focus-within:opacity-100 group-hover:opacity-100 hover:bg-selected data-[state=open]:opacity-100"
          data-testid="workspace-project-menu"
          type="button"
        >
          <More aria-hidden size={16} stroke={1.75} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          className="z-50 w-60 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg"
          onCloseAutoFocus={event => {
            // The form's field takes the focus the menu would give back to its dots.
            event.preventDefault()
          }}
          sideOffset={4}
        >
          <DropdownMenu.Item className={MENU_ITEM} data-testid="workspace-new" onSelect={onNew}>
            <Plus aria-hidden size={15} stroke={1.75} />
            {t.workspaces.new}
          </DropdownMenu.Item>
          <DropdownMenu.Item className={MENU_ITEM} data-testid="workspace-setup" onSelect={onSetup}>
            <Settings aria-hidden size={15} stroke={1.75} />
            {t.workspaces.setup}
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

/** The last part of a folder's path, as a row names it. */
function baseName(folder: string): string {
  return folder.split(/[\\/]/).filter(Boolean).pop() ?? folder
}

const MENU_ITEM =
  'flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-sm outline-none select-none data-highlighted:bg-muted'
const FIELD = 'w-full rounded border border-border-strong bg-raised px-2 py-1'

/** A new workspace of the project, by the name the user gives it; its branch is `atlas/<name>`. */
function NewWorkspace({ onDone, root }: { onDone: () => void; root: string }) {
  const [name, setName] = useState('')
  const [error, setError] = useState('')

  return (
    <form
      className="my-1 space-y-2 rounded-md border border-dashed border-border-strong p-2 text-xs"
      data-testid="workspace-new-form"
      onSubmit={event => {
        event.preventDefault()

        if (name.trim()) {
          createWorkspace(root, name.trim()).then(onDone, (failed: unknown) => {
            setError(failed instanceof Error ? failed.message : String(failed))
          })
        }
      }}
    >
      <input
        aria-label={t.workspaces.name}
        autoFocus
        className={FIELD}
        onChange={event => {
          setName(event.target.value)
        }}
        placeholder={t.workspaces.namePlaceholder}
        value={name}
      />
      {error && (
        <p className="text-failed-text" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button className="rounded bg-primary px-2 py-1 font-medium text-primary-foreground" type="submit">
          {t.workspaces.create}
        </button>
        <button className="rounded px-2 py-1 text-muted-foreground" onClick={onDone} type="button">
          {t.workspaces.cancel}
        </button>
      </div>
    </form>
  )
}

/** How the project prepares a new workspace: the command run in its folder once it is made, and
 *  the first of the ports its workspaces get, each its own block. */
function ProjectSetup({ onDone, root }: { onDone: () => void; root: string }) {
  const [script, setScript] = useState<null | string>(null)
  const [start, setStart] = useState('')
  const [size, setSize] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    readProject(root).then(
      project => {
        setScript(project.setup_script)
        setStart(project.ports_start ? String(project.ports_start) : '')
        setSize(String(project.ports_size))
      },
      (failed: unknown) => {
        setError(failed instanceof Error ? failed.message : String(failed))
      }
    )
  }, [root])

  return (
    <form
      className="my-1 space-y-2 rounded-md border border-dashed border-border-strong p-2 text-xs"
      data-testid="workspace-setup-form"
      onSubmit={event => {
        event.preventDefault()
        saveProject(root, script ?? '', Number(start) || 0, Number(size) || 0).then(onDone, (failed: unknown) => {
          setError(failed instanceof Error ? failed.message : String(failed))
        })
      }}
    >
      <label className="block space-y-1">
        <span className="text-soft">{t.workspaces.script}</span>
        <textarea
          className={`${FIELD} font-mono`}
          data-testid="workspace-setup-script"
          disabled={script === null}
          onChange={event => {
            setScript(event.target.value)
          }}
          placeholder={t.workspaces.scriptPlaceholder}
          rows={2}
          value={script ?? ''}
        />
      </label>
      <p className="text-muted-foreground">{t.workspaces.scriptHint}</p>
      <div className="flex gap-2">
        <label className="flex-1 space-y-1">
          <span className="text-soft">{t.workspaces.portsStart}</span>
          <input
            className={FIELD}
            data-testid="workspace-ports-start"
            inputMode="numeric"
            onChange={event => {
              setStart(event.target.value)
            }}
            placeholder="4000"
            value={start}
          />
        </label>
        <label className="flex-1 space-y-1">
          <span className="text-soft">{t.workspaces.portsSize}</span>
          <input
            className={FIELD}
            inputMode="numeric"
            onChange={event => {
              setSize(event.target.value)
            }}
            value={size}
          />
        </label>
      </div>
      {error && (
        <p className="text-failed-text" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <button
          className="rounded bg-primary px-2 py-1 font-medium text-primary-foreground"
          disabled={script === null}
          type="submit"
        >
          {t.workspaces.save}
        </button>
        <button className="rounded px-2 py-1 text-muted-foreground" onClick={onDone} type="button">
          {t.workspaces.cancel}
        </button>
      </div>
    </form>
  )
}

function Workspace({ executor, row }: { executor: string; row: WorkspaceRow }) {
  const [asking, setAsking] = useState(false)
  const [refused, setRefused] = useState('')
  const [error, setError] = useState('')
  const archived = row.state === 'archived'
  const failed = (reason: unknown) => {
    setError(reason instanceof Error ? reason.message : String(reason))
  }
  const remove = (force: boolean) => {
    deleteWorkspace(row.id, force).then(
      () => {
        setAsking(false)
      },
      (reason: unknown) => {
        if (force) {
          failed(reason)
        } else {
          // Work not delivered: said, and deleted only on a second yes.
          setRefused(reason instanceof Error ? reason.message : String(reason))
        }
      }
    )
  }

  return (
    <div className="group" data-state={row.state} data-testid="workspace" data-workspace={row.name}>
      <div className="relative flex items-center rounded-md group-hover:bg-muted">
        <button
          className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1.5 text-left text-sm disabled:cursor-default"
          data-testid="workspace-open"
          disabled={archived || !executor}
          onClick={() => {
            newSession(executor, row.folder)
          }}
          title={row.ports ? `${row.folder}\n${t.workspaces.ports(row.ports)}` : row.folder}
          type="button"
        >
          <Branch aria-hidden className="shrink-0 text-muted-foreground" size={14} stroke={1.75} />
          <span className={`min-w-0 flex-1 truncate ${archived ? 'text-muted-foreground' : ''}`}>{row.name}</span>
          {row.state !== 'ready' && (
            <span
              className={`shrink-0 text-xs ${row.state === 'setup_failed' ? 'text-failed-text' : 'text-muted-foreground'}`}
              data-testid="workspace-state"
            >
              {t.workspaces.states[row.state] ?? row.state}
            </span>
          )}
        </button>
        <span className="pointer-events-none absolute right-1 flex rounded-md bg-muted opacity-0 group-focus-within:pointer-events-auto group-focus-within:opacity-100 group-hover:pointer-events-auto group-hover:opacity-100">
          {archived ? (
            <IconButton
              icon={ArchiveOff}
              label={t.workspaces.unarchive(row.name)}
              onClick={() => {
                unarchiveWorkspace(row.id).catch(failed)
              }}
              testId="workspace-unarchive"
            />
          ) : (
            <IconButton
              icon={Archive}
              label={t.workspaces.archive(row.name)}
              onClick={() => {
                archiveWorkspace(row.id).catch(failed)
              }}
              testId="workspace-archive"
            />
          )}
          <IconButton
            icon={Trash}
            label={t.workspaces.delete(row.name)}
            onClick={() => {
              setAsking(true)
              setRefused('')
            }}
            testId="workspace-delete"
          />
        </span>
      </div>
      {row.state === 'setup_failed' && row.setup_output && (
        <pre className="mx-2 max-h-24 overflow-auto font-mono text-xs break-words whitespace-pre-wrap text-failed-text select-text">
          {row.setup_output.trim()}
        </pre>
      )}
      {asking && (
        <div className="space-y-1 px-2 pb-1 text-xs" data-testid="workspace-delete-confirm">
          <p className={refused ? 'text-waiting-text' : 'text-soft'}>
            {refused ? t.workspaces.undelivered : t.workspaces.deleteNote(row.branch)}
          </p>
          <div className="flex gap-2">
            <button
              className="rounded bg-danger px-2 py-0.5 text-white"
              data-testid="workspace-delete-yes"
              onClick={() => {
                remove(refused !== '')
              }}
              type="button"
            >
              {refused ? t.workspaces.deleteAnyway : t.workspaces.deleteYes}
            </button>
            <button
              className="rounded px-2 py-0.5 text-muted-foreground"
              onClick={() => {
                setAsking(false)
              }}
              type="button"
            >
              {t.workspaces.cancel}
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="px-2 text-xs text-failed-text" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
