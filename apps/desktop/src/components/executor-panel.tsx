import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { locale, t } from '@/i18n'
import type { ExecutorConnector, ExecutorRow, SlashCommandRow, UsageWindowRow } from '@/protocol/protocol.generated'
import {
  $commandReads,
  $executorConnectors,
  $executorUsage,
  $openSession,
  $slashCommands,
  $view,
  $workbench,
  currentFolder,
  readPanelCommands,
  refreshExecutorConnectors,
  refreshExecutorUsage,
  slashKey
} from '@/store/app'
import { level, resetsIn, windowName } from '@/store/usage'

import { ContextSection, EnvironmentSection } from './environment'
import { baseName } from './executors-list'
import { useNow } from './use-now'

/** The origins in the order Claude Code's `/mcp` lists them: the user's own first, the account's
 * hosted ones last. A plugin's servers are listed under its plugin instead. */
const ORIGINS = ['user', 'project', 'local', 'managed', 'account']

/** A server in one of these is down until the user acts in the harness: hidden unless asked,
 * outside an opened plugin (asked for by the user on 22/10/2026). */
const DOWN = new Set(['disabled', 'failed', 'needs-auth'])

/** The skills listed before the rest is asked for. */
const SKILLS_SHOWN = 8

const DOTS: Record<string, string> = {
  connected: 'bg-muted-foreground',
  disabled: 'bg-faint',
  failed: 'bg-failed',
  'needs-auth': 'bg-waiting',
  pending: 'bg-working motion-safe:animate-pulse'
}

const BARS: Record<ReturnType<typeof level>, string> = {
  full: 'bg-failed',
  high: 'bg-waiting',
  ok: 'bg-muted-foreground'
}

const HEADING = 'text-xs font-semibold tracking-wide text-muted-foreground uppercase'
const REFRESH = 'rounded px-2 py-0.5 text-xs text-muted-foreground hover:bg-selected disabled:opacity-50'

/** One of the harness's plugins as the panel lists it: the skills and MCP servers it brings. */
interface PluginGroup {
  name: string
  servers: ExecutorConnector[]
  skills: SlashCommandRow[]
}

/** The plugin a skill comes from: Claude Code names a plugin's skills after it (`marketing:seo`). */
export function pluginOf(skill: SlashCommandRow): string {
  const colon = skill.name.indexOf(':')

  return colon > 0 ? skill.name.slice(0, colon) : ''
}

/** The harness's plugins, by name, from what its skills and servers say. */
export function pluginGroups(skills: SlashCommandRow[], servers: ExecutorConnector[]): PluginGroup[] {
  const groups = new Map<string, PluginGroup>()
  const group = (name: string): PluginGroup => {
    const found = groups.get(name) ?? { name, servers: [], skills: [] }
    groups.set(name, found)

    return found
  }

  for (const skill of skills) {
    const plugin = pluginOf(skill)

    if (plugin) {
      group(plugin).skills.push(skill)
    }
  }

  for (const server of servers) {
    if (server.origin === 'plugin') {
      group(server.plugin || server.origin).servers.push(server)
    }
  }

  return [...groups.values()].sort((a, b) => a.name.localeCompare(b.name))
}

/**
 * The side panel's executor tab (asked for by the user on 16/10/2026): what the executor chosen in
 * the sidebar has. Its plan's usage first (asked for by the user on 18/10/2026), a bar per window
 * as Claude Code's own `/usage` lists them, always shown; then what it has, in the words Atlas
 * uses for its own (asked for by the user on 22/10/2026): its skills, its MCP servers with those
 * down behind a click, and its plugins, each closed to a line, with the skills and servers it
 * brings. Only shown: a server's login stays in the harness.
 */
export function ExecutorPanel({ executor }: { executor: ExecutorRow }) {
  // Read so the skills follow the folder on screen.
  useStore($view)
  // The session on screen, when it is this executor's (on any of its accounts): its environment
  // and its context come first (step 18g).
  const open = useStore($openSession)
  const { instances } = useStore($workbench)
  const family = (name: string) => name.split('@')[0]
  const session = open && family(open.executor) === family(executor.name) ? open : null
  const instance = session ? instances.find(row => row.instance_id === session.instanceId) : undefined
  const folder = currentFolder()
  const commands = useStore($slashCommands)[slashKey(executor.name, folder)]
  const skills = (commands ?? []).filter(command => command.group === 'skill')
  const connectors = useStore($executorConnectors)[executor.name]?.connectors ?? []
  const plugins = pluginGroups(skills, connectors)

  useEffect(() => {
    readPanelCommands(executor.name, folder)
  }, [executor.name, folder])

  return (
    <div className="space-y-4 p-4 text-sm" data-executor={executor.name} data-testid="executor-panel">
      <header>
        <p className="font-semibold">{executor.title}</p>
        <p className="text-xs text-muted-foreground">
          {[executor.version, executor.account, executor.plan].filter(Boolean).join(' · ')}
        </p>
      </header>
      {session && (
        <>
          <EnvironmentSection folder={session.folder} />
          {instance && <ContextSection instance={instance} />}
        </>
      )}
      <PlanUsage executor={executor} />
      <SkillsSection executor={executor} folder={folder} skills={skills.filter(skill => !pluginOf(skill))} />
      <ServersSection executor={executor} />
      {plugins.length > 0 && (
        <section className="space-y-1" data-testid="executor-plugins">
          <h3 className={HEADING}>
            {t.executorPanel.plugins}
            <span className="ml-1 font-normal">{plugins.length}</span>
          </h3>
          <ul>
            {plugins.map(plugin => (
              <PluginRow key={plugin.name} plugin={plugin} />
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

/** The skills of the harness, the user's and the folder's project's; a plugin's are under it. */
function SkillsSection({
  executor,
  folder,
  skills
}: {
  executor: ExecutorRow
  folder: string
  skills: SlashCommandRow[]
}) {
  const key = slashKey(executor.name, folder)
  const read = useStore($commandReads)[key]
  const listed = useStore($slashCommands)[key] !== undefined
  const [all, setAll] = useState(false)
  const shown = all ? skills : skills.slice(0, SKILLS_SHOWN)

  return (
    <section className="space-y-1" data-testid="executor-skills">
      <div className="flex items-center justify-between gap-2">
        <h3 className={HEADING}>
          {t.executorPanel.skills}
          {skills.length > 0 && <span className="ml-1 font-normal">{skills.length}</span>}
        </h3>
        <button
          className={REFRESH}
          data-testid="skills-refresh"
          disabled={read?.reading}
          onClick={() => {
            readPanelCommands(executor.name, folder, true)
          }}
          type="button"
        >
          ↻ {t.executorPanel.refresh}
        </button>
      </div>
      {read?.reading && !listed && (
        <p className="text-xs text-muted-foreground" role="status">
          {t.executorPanel.skillsReading}
        </p>
      )}
      {read?.error && (
        <p className="text-xs text-failed-text" role="alert">
          {t.executorPanel.failed(read.error)}
        </p>
      )}
      {listed && skills.length === 0 && <p className="text-xs text-muted-foreground">{t.executorPanel.skillsEmpty}</p>}
      {shown.length > 0 && (
        <ul>
          {shown.map(skill => (
            <SkillRow key={skill.name} skill={skill} />
          ))}
        </ul>
      )}
      {!all && skills.length > SKILLS_SHOWN && (
        <MoreButton
          label={t.executorPanel.moreSkills(skills.length - SKILLS_SHOWN)}
          onClick={() => {
            setAll(true)
          }}
        />
      )}
      {listed && <p className="px-1 text-xs text-muted-foreground">{t.executorPanel.skillsFolder(baseName(folder))}</p>}
    </section>
  )
}

function SkillRow({ skill }: { skill: SlashCommandRow }) {
  return (
    <li className="rounded px-1 py-0.5" data-testid="skill-row" title={skill.description || undefined}>
      <span className="block truncate">/{skill.name}</span>
      {skill.description && <span className="block truncate text-xs text-muted-foreground">{skill.description}</span>}
    </li>
  )
}

/** The MCP servers that are no plugin's, by origin: those down only when asked. */
function ServersSection({ executor }: { executor: ExecutorRow }) {
  const read = useStore($executorConnectors)[executor.name]
  const [showDown, setShowDown] = useState(false)
  const servers = (read?.connectors ?? []).filter(row => row.origin !== 'plugin')
  const up = servers.filter(row => !DOWN.has(row.status))
  const down = servers.length - up.length
  const shown = showDown ? servers : up
  const groups = ORIGINS.map(origin => [origin, shown.filter(row => row.origin === origin)] as const).filter(
    ([, rows]) => rows.length > 0
  )

  return (
    <section className="space-y-1" data-testid="executor-servers">
      <div className="flex items-center justify-between gap-2">
        <h3 className={HEADING}>
          {t.executorPanel.mcp}
          {servers.length > 0 && (
            <span className="ml-1 font-normal">{t.executorPanel.active(up.length, servers.length)}</span>
          )}
        </h3>
        <button
          className={REFRESH}
          data-testid="connectors-refresh"
          disabled={read?.reading}
          onClick={() => void refreshExecutorConnectors(executor.name)}
          type="button"
        >
          ↻ {t.executorPanel.refresh}
        </button>
      </div>
      {read?.reading && (
        <p className="text-xs text-muted-foreground" data-testid="connectors-reading" role="status">
          {t.executorPanel.reading}
        </p>
      )}
      {read?.error && (
        <p className="text-xs text-failed-text" role="alert">
          {t.executorPanel.failed(read.error)}
        </p>
      )}
      {read && !read.listed && <p className="text-xs text-muted-foreground">{t.executorPanel.none(executor.title)}</p>}
      {read && read.listed && !read.reading && !read.error && servers.length === 0 && (
        <p className="text-xs text-muted-foreground">{t.executorPanel.empty}</p>
      )}
      {up.length === 0 && down > 0 && !showDown && (
        <p className="px-1 text-xs text-muted-foreground">{t.executorPanel.noneActive}</p>
      )}
      {groups.map(([origin, rows]) => (
        <div data-origin={origin} data-testid="connector-group" key={origin}>
          <p className="px-1 pt-1 text-xs text-muted-foreground">{t.executorPanel.origins[origin] ?? origin}</p>
          <ul>
            {rows.map(row => (
              <ConnectorRow key={`${row.plugin}:${row.name}`} row={row} />
            ))}
          </ul>
        </div>
      ))}
      {down > 0 && (
        <MoreButton
          label={showDown ? t.executorPanel.hideInactive : t.executorPanel.showInactive(down)}
          onClick={() => {
            setShowDown(!showDown)
          }}
          testId="servers-down"
        />
      )}
      {read?.listed && read.connectors.length > 0 && (
        <p className="pt-1 text-xs text-muted-foreground">
          {t.executorPanel.outsideProjects} {t.executorPanel.loginThere(executor.title)}
        </p>
      )}
    </section>
  )
}

/** A plugin, closed to a line with what it brings; opened, its skills and all of its servers. */
function PluginRow({ plugin }: { plugin: PluginGroup }) {
  const up = plugin.servers.filter(row => !DOWN.has(row.status)).length
  const summary = [
    plugin.skills.length > 0 ? t.executorPanel.pluginSkills(plugin.skills.length) : '',
    plugin.servers.length > 0 ? t.executorPanel.pluginServers(up, plugin.servers.length) : ''
  ].filter(Boolean)

  return (
    <li data-plugin={plugin.name} data-testid="plugin-row">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-baseline gap-2 rounded px-1 py-1 hover:bg-selected">
          <span aria-hidden className="text-xs text-muted-foreground transition-transform group-open:rotate-90">
            ›
          </span>
          <span className="min-w-0 flex-1 truncate">{plugin.name}</span>
          <span className="shrink-0 text-xs text-muted-foreground">{summary.join(' · ')}</span>
        </summary>
        <div className="pb-1 pl-4">
          {plugin.skills.length > 0 && (
            <ul>
              {plugin.skills.map(skill => (
                <SkillRow key={skill.name} skill={skill} />
              ))}
            </ul>
          )}
          {plugin.servers.length > 0 && (
            <ul>
              {plugin.servers.map(row => (
                <ConnectorRow key={`${row.plugin}:${row.name}`} row={row} />
              ))}
            </ul>
          )}
        </div>
      </details>
    </li>
  )
}

function MoreButton({ label, onClick, testId }: { label: string; onClick: () => void; testId?: string }) {
  return (
    <button
      className="rounded px-1 py-0.5 text-left text-xs text-muted-foreground hover:bg-selected hover:text-foreground"
      data-testid={testId}
      onClick={onClick}
      type="button"
    >
      {label}
    </button>
  )
}

function ConnectorRow({ row }: { row: ExecutorConnector }) {
  const detail = row.error || (row.status === 'connected' && row.tools > 0 ? t.executorPanel.tools(row.tools) : '')

  return (
    <li
      className="flex items-start gap-2 rounded px-1 py-1"
      data-status={row.status}
      data-testid="connector-row"
      title={row.error || undefined}
    >
      <span aria-hidden className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${DOTS[row.status] ?? 'bg-faint'}`} />
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-2">
          <span className="truncate">{row.name}</span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {t.executorPanel.statuses[row.status] ?? row.status}
          </span>
        </span>
        {detail && <span className="block truncate text-xs text-muted-foreground">{detail}</span>}
      </span>
    </li>
  )
}

/** The plan's limits the account has used, a bar per window, with when each starts again. */
function PlanUsage({ executor }: { executor: ExecutorRow }) {
  const read = useStore($executorUsage)[executor.name]
  const now = useNow(true, 30_000)
  const usage = read?.usage

  if (usage && !usage.listed) {
    return null
  }

  return (
    <section className="space-y-2" data-testid="plan-usage">
      <div className="flex items-center justify-between gap-2">
        <h3 className={HEADING}>
          {t.executorPanel.usage}
          {usage?.plan && <span className="ml-1 font-normal normal-case">{usage.plan}</span>}
        </h3>
        <button
          className={REFRESH}
          data-testid="usage-refresh"
          disabled={read?.reading}
          onClick={() => void refreshExecutorUsage(executor.name)}
          type="button"
        >
          ↻ {t.executorPanel.refresh}
        </button>
      </div>
      {read?.reading && !usage && (
        <p className="text-xs text-muted-foreground" data-testid="usage-reading" role="status">
          {t.executorPanel.usageReading}
        </p>
      )}
      {read?.error && (
        <p className="text-xs text-failed-text" role="alert">
          {t.executorPanel.failed(read.error)}
        </p>
      )}
      {usage?.unavailable && <p className="text-xs text-muted-foreground">{t.executorPanel.usageNone}</p>}
      {usage?.extra_usage && (
        <p className="text-xs text-waiting-text" data-testid="usage-extra">
          {t.executorPanel.usageExtra}
        </p>
      )}
      {usage && usage.windows.length > 0 && (
        <ul className="space-y-2">
          {usage.windows.map(window => (
            <UsageBar key={window.id} now={now} window={window} />
          ))}
        </ul>
      )}
    </section>
  )
}

function UsageBar({ now, window }: { now: number; window: UsageWindowRow }) {
  const used = Math.round(window.used)
  const resets = resetsIn(window.resets_at, now, locale)

  return (
    <li data-testid="usage-window" data-window={window.id}>
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="truncate">{windowName(window, t.executorPanel.windows)}</span>
        <span className="shrink-0 text-muted-foreground">{t.executorPanel.used(used)}</span>
      </div>
      <div
        aria-label={windowName(window, t.executorPanel.windows)}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={used}
        className="mt-1 h-1.5 overflow-hidden rounded-full bg-selected"
        role="progressbar"
      >
        <div className={`h-full rounded-full ${BARS[level(window.used)]}`} style={{ width: `${String(used)}%` }} />
      </div>
      {resets && <p className="mt-0.5 text-xs text-muted-foreground">{t.executorPanel.resets(resets)}</p>}
    </li>
  )
}
