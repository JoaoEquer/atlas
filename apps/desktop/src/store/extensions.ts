/**
 * The settings screen's Extensions as pure rules: its tabs, the marketplace's kinds in the
 * marketplace's order, and the search over each tab's cards.
 */
import type { McpServerRow, SkillRow } from '@/protocol/protocol.generated'

import { KINDS, type MarketKind } from './marketplace'

export type ExtensionKind = MarketKind

/** The tabs, in the marketplace's order. */
export const EXTENSION_KINDS: readonly ExtensionKind[] = KINDS

/** Whether every word of the search is in one of the texts, case aside; an empty search matches. */
export function matches(query: string, ...texts: readonly string[]): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  const all = texts.join('\n').toLowerCase()

  return words.every(word => all.includes(word))
}

/** The skills by shelf: the Atlas team's, the home's own, then the plugins'. */
export function skillShelves(skills: readonly SkillRow[]): Record<SkillRow['source'], SkillRow[]> {
  return {
    atlas: skills.filter(skill => skill.source === 'atlas'),
    home: skills.filter(skill => skill.source === 'home'),
    plugin: skills.filter(skill => skill.source === 'plugin')
  }
}

/** A plugin's MCP servers that sign in, for its card: the server names it as
 * `plugin_<plugin>_<server>` (`atlas.plugins.server_name`). */
export function pluginSignIns(servers: readonly McpServerRow[], plugin: string): McpServerRow[] {
  return servers.filter(server => server.plugin && server.login && server.name.startsWith(`plugin_${plugin}_`))
}

/** The MCP servers by shelf: the home's `mcp.json`, then the plugins'. */
export function mcpShelves(servers: readonly McpServerRow[]): { file: McpServerRow[]; plugins: McpServerRow[] } {
  return {
    file: servers.filter(server => !server.plugin),
    plugins: servers.filter(server => server.plugin)
  }
}
