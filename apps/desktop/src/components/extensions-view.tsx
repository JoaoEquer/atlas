import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { t } from '@/i18n'
import type { McpServerRow, SkillRow } from '@/protocol/protocol.generated'
import {
  $extensionsError,
  $mcpServers,
  $plugins,
  $skills,
  $workbench,
  refreshExtensions,
  refreshPlugins
} from '@/store/app'
import { matches, mcpShelves, skillShelves } from '@/store/extensions'
import { $extensionsTab, showExtensionsTab } from '@/store/settings'

import { ExecutorOffers } from './executor-offers'
import { Badge, ExtensionCard, KindTabs, Shelf } from './extension-card'
import { McpSignIn } from './mcp-sign-in'
import { PluginsTab } from './plugins-view'

/**
 * The settings screen's Extensions (decided with the user on 28/10/2026, in place of its Plugins
 * and its Executors): what the home has of each kind the marketplace offers, in the marketplace's
 * look, a tab per kind with its count, a search, and a card each with its action. Plugins turn on
 * with their consent, as before; skills say where they came from; MCP servers say how they stand,
 * and the one that signs in with OAuth has a Sign in button, and the command that signs in from a
 * terminal (`McpSignIn`); executors turn on with their consent.
 */
export function ExtensionsSection() {
  const tab = useStore($extensionsTab)
  const plugins = useStore($plugins)
  const skills = useStore($skills)
  const mcp = useStore($mcpServers)
  const { executors } = useStore($workbench)
  const error = useStore($extensionsError)
  const [query, setQuery] = useState('')
  const counts = {
    executor: executors.length,
    mcp: mcp?.servers.length ?? 0,
    plugin: plugins.length,
    skill: skills?.length ?? 0
  }

  return (
    <div className="space-y-4" data-testid="extensions-view">
      <div className="flex flex-wrap items-center gap-2">
        <p className="min-w-0 flex-1 basis-64 text-sm text-muted-foreground">{t.extensions.intro}</p>
        <input
          aria-label={t.extensions.search}
          className="w-48 rounded-md border border-border-strong bg-transparent px-2 py-1 text-sm"
          data-testid="extensions-search"
          onChange={event => {
            setQuery(event.target.value)
          }}
          placeholder={t.extensions.search}
          type="search"
          value={query}
        />
        <button
          className={BUTTON}
          data-testid="extensions-refresh"
          onClick={() => {
            refreshPlugins()
            refreshExtensions()
          }}
          type="button"
        >
          {t.extensions.refresh}
        </button>
      </div>
      <KindTabs counts={counts} current={tab} onPick={showExtensionsTab} testId="extensions-tab" />
      {error && (tab === 'skill' || tab === 'mcp') && (
        <p className="rounded-md bg-failed/10 px-3 py-2 text-sm text-failed-text" role="alert">
          {error}
        </p>
      )}
      <div data-tab={tab} data-testid="extensions-panel" role="tabpanel">
        {tab === 'plugin' && <PluginsTab query={query} />}
        {tab === 'skill' && <SkillsTab query={query} skills={skills} />}
        {tab === 'mcp' && <McpTab query={query} />}
        {tab === 'executor' && (
          <>
            <ExecutorOffers grid query={query} />
            {executors.length === 0 && <p className="text-sm text-muted-foreground">{t.extensions.noExecutors}</p>}
          </>
        )}
      </div>
    </div>
  )
}

function SkillsTab({ query, skills }: { query: string; skills: null | SkillRow[] }) {
  if (skills === null) {
    return <p className="text-sm text-muted-foreground">{t.extensions.reading}</p>
  }

  const shown = skills.filter(skill => matches(query, skill.name, skill.description, skill.plugin))
  const shelves = skillShelves(shown)

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t.extensions.skillsIntro}</p>
      {skills.length === 0 && <p className="text-sm text-muted-foreground">{t.extensions.noSkills}</p>}
      {skills.length > 0 && shown.length === 0 && (
        <p className="text-sm text-muted-foreground">{t.extensions.noMatch}</p>
      )}
      <Shelf count={shelves.atlas.length} title={t.marketplace.official}>
        {shelves.atlas.map(skill => (
          <Skill key={skill.name} skill={skill} />
        ))}
      </Shelf>
      <Shelf count={shelves.home.length} title={t.extensions.yours}>
        {shelves.home.map(skill => (
          <Skill key={skill.name} skill={skill} />
        ))}
      </Shelf>
      <Shelf count={shelves.plugin.length} title={t.extensions.fromPlugins}>
        {shelves.plugin.map(skill => (
          <Skill key={skill.name} skill={skill} />
        ))}
      </Shelf>
    </div>
  )
}

function Skill({ skill }: { skill: SkillRow }) {
  return (
    <ExtensionCard
      action={
        skill.user_invocable ? (
          <span className="shrink-0 font-mono text-xs text-muted-foreground">/{skill.name}</span>
        ) : undefined
      }
      badge={skill.source === 'atlas' ? <Badge>{t.marketplace.officialBadge}</Badge> : undefined}
      data={{ 'data-name': skill.name, 'data-source': skill.source, 'data-testid': 'skill-card' }}
      footer={skill.user_invocable ? undefined : t.extensions.modelOnly}
      kind="skill"
      subtitle={t.extensions.skillSource(skill.source, skill.plugin)}
      summary={skill.description}
      title={skill.name}
    />
  )
}

function McpTab({ query }: { query: string }) {
  const mcp = useStore($mcpServers)

  if (mcp === null) {
    return <p className="text-sm text-muted-foreground">{t.extensions.reading}</p>
  }

  const shown = mcp.servers.filter(server => matches(query, server.name, server.transport))
  const shelves = mcpShelves(shown)

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">{t.extensions.mcpIntro}</p>
      {mcp.problem && (
        <p
          className="rounded-md bg-waiting/10 px-3 py-2 text-sm text-waiting-text select-text"
          data-testid="mcp-problem"
          role="status"
        >
          {mcp.problem}
        </p>
      )}
      {mcp.servers.length === 0 && <p className="text-sm text-muted-foreground">{t.extensions.noMcp}</p>}
      {mcp.servers.length > 0 && shown.length === 0 && (
        <p className="text-sm text-muted-foreground">{t.extensions.noMatch}</p>
      )}
      <Shelf count={shelves.file.length} title={t.extensions.inMcpFile}>
        {shelves.file.map(server => (
          <McpServer key={server.name} server={server} />
        ))}
      </Shelf>
      <Shelf count={shelves.plugins.length} title={t.extensions.fromPlugins}>
        {shelves.plugins.map(server => (
          <McpServer key={server.name} server={server} />
        ))}
      </Shelf>
    </div>
  )
}

function McpServer({ server }: { server: McpServerRow }) {
  const out = server.state === 'left_out'

  return (
    <ExtensionCard
      action={
        <span
          className={`shrink-0 text-xs ${out ? 'text-waiting-text' : 'text-muted-foreground'}`}
          data-testid="mcp-state"
        >
          {t.extensions.mcpState(server.state, server.tools)}
        </span>
      }
      data={{ 'data-name': server.name, 'data-state': server.state, 'data-testid': 'mcp-card' }}
      footer={server.plugin ? t.extensions.fromPlugin : t.extensions.inMcpFile}
      kind="mcp"
      subtitle={server.transport}
      summary={t.extensions.mcpSummary(server.transport)}
      title={server.name}
    >
      {server.problem && (
        <p
          className={`text-sm break-words select-text ${out ? 'text-waiting-text' : 'text-muted-foreground'}`}
          data-testid="mcp-card-problem"
        >
          {server.problem}
        </p>
      )}
      <McpSignIn server={server} />
    </ExtensionCard>
  )
}

const BUTTON = 'rounded-md border border-border-strong px-2 py-1 text-sm hover:bg-muted disabled:opacity-40'
