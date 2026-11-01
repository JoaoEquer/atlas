// What the site serves to agents: each page as Markdown, the llms.txt index, robots.txt and the
// published skill. Built from the same sources as the pages, so the two never differ.
import { createHash } from 'node:crypto'

import { REPOSITORY, SIDEBAR } from '../../site.config.mjs'
import { CONTENT_SIGNAL } from '../../worker/routing'

import type { Change } from './changelog'
import { catalogue, chapters, compare, faq, gate, hero, install, memory, proof, route } from './home'
import { installWays } from './install'

export interface DocPage {
  id: string
  title: string
  description: string
  body: string
}

export interface NewsItem {
  title: string
  date: Date
  source: string
  url: string
  atlas?: string | undefined
  body: string
}

/** Where a page sits in the sidebar: its group and its place there; last when the sidebar lacks it. */
export function sidebarPlace(page: { id: string }): [number, number] {
  for (const [group, entry] of SIDEBAR.entries()) {
    const item = entry.items.findIndex(listed => 'slug' in listed && listed.slug === page.id)
    if (item >= 0) {
      return [group, item]
    }
  }
  return [SIDEBAR.length, 0]
}

/** The documentation's pages in the sidebar's order. */
export function sortDocs<T extends { id: string }>(pages: T[]): T[] {
  return [...pages].sort((a, b) => {
    const [groupA, itemA] = sidebarPlace(a)
    const [groupB, itemB] = sidebarPlace(b)
    return groupA - groupB || itemA - itemB || a.id.localeCompare(b.id)
  })
}

export function twinOf(id: string): string {
  return `/${id}.md`
}

/** Points the documentation's own links at their Markdown twins. */
export function linkTwins(markdown: string, site: URL): string {
  return markdown.replace(/\]\((\/docs(?:\/[^)\s#]*?)?)\/?(#[^)\s]*)?\)/g, (_match, path: string, hash?: string) => {
    const id = path.replace(/^\//, '').replace(/\/$/, '')
    return `](${new URL(twinOf(id), site).toString()}${hash ?? ''})`
  })
}

export function docMarkdown(page: DocPage, site: URL): string {
  return `# ${page.title}\n\n> ${page.description}\n\n${linkTwins(page.body, site).trim()}\n`
}

export function homeMarkdown(site: URL): string {
  const link = (path: string) => new URL(path, site).toString()
  const lines: string[] = [
    '# Atlas',
    '',
    `> ${hero.lede}`,
    '',
    hero.title,
    '',
    ...proof.map(item => `- **${item.label}.** ${item.detail}.`),
    ''
  ]
  for (const chapter of chapters) {
    lines.push(`## ${chapter.title}`, '', ...chapter.body.flatMap(text => [text, '']))
    if (chapter.id === 'route') {
      lines.push(
        `For example: ${route.stops.map(stop => `${stop.name} · ${stop.account}`).join(' → ')}.`,
        '',
        route.handoff,
        ''
      )
    }
    if (chapter.id === 'memory') {
      lines.push(...memory.entries.map(entry => `- ${entry.kind}: ${entry.text}`), '', memory.caption, '')
    }
  }
  lines.push(`## ${compare.title}`, '', compare.body, '')
  lines.push(
    ...compare.rows.map(row => `- **${row.moment}.** ${compare.alone}: ${row.alone} ${compare.atlas}: ${row.atlas}`),
    ''
  )
  lines.push(`## ${gate.title}`, '', gate.body, '')
  lines.push(...gate.commands.map(item => `- \`${item.command}\`: ${item.verdict}. ${item.detail}`), '')
  lines.push(`## ${catalogue.title}`, '')
  lines.push(catalogue.body, '')
  lines.push(
    ...catalogue.items.map(
      item => `- **[${item.name}](${link(twinOf(item.href.replace(/^\/|\/$/g, '')))})**: ${item.text}`
    ),
    ''
  )
  lines.push(`## ${install.title}`, '')
  for (const way of installWays) {
    lines.push(`${way.label}, ${way.where.toLowerCase()}:`, '', '```', ...way.lines, '```', '')
    if (way.after) {
      lines.push(way.after, '')
    }
    if (way.downloads) {
      lines.push(`Or download the Setup: ${way.downloads.map(d => `[${d.label}](${d.href})`).join(', ')}.`, '')
    }
  }
  lines.push(...install.steps.map((step, k) => `${String(k + 1)}. ${step}`), '', install.update, '')
  lines.push(install.needs, '')
  lines.push(`## ${faq.title}`, '')
  for (const item of faq.items) {
    lines.push(`### ${item.question}`, '', item.answer, '')
  }
  lines.push(
    '## More',
    '',
    `- [Documentation](${link('/docs.md')})`,
    `- [News](${link('/news.md')})`,
    `- [Changelog](${link('/changelog.md')})`,
    `- [Source code](${REPOSITORY})`,
    ''
  )
  return lines.join('\n')
}

export function newsMarkdown(items: NewsItem[]): string {
  const lines = [
    '# News',
    '',
    '> Short summaries, in our words, of what changes in the models and tools Atlas works with. Each one links to the full story where it was published.',
    ''
  ]
  if (items.length === 0) {
    lines.push('No summaries yet.', '')
  }
  for (const item of items) {
    lines.push(`## ${item.title}`, '', `${item.source}, ${item.date.toISOString().slice(0, 10)}: ${item.url}`, '')
    lines.push(item.body.trim(), '')
    if (item.atlas) {
      lines.push(`For Atlas: ${item.atlas}`, '')
    }
  }
  return lines.join('\n')
}

export function changelogMarkdown(list: Change[]): string {
  const lines = [
    '# Changelog',
    '',
    '> Every change that reaches the Update button in Atlas, newest first. Press Update in the app, or run `atlas update`, to get them.',
    ''
  ]
  let day = ''
  for (const change of list) {
    if (change.date !== day) {
      day = change.date
      lines.push(`## ${day}`, '')
    }
    lines.push(`- ${change.subject} (${change.sha.slice(0, 7)})`)
  }
  lines.push('')
  return lines.join('\n')
}

export function llmsTxt(pages: DocPage[], site: URL): string {
  const link = (path: string) => new URL(path, site).toString()
  return [
    '# Atlas',
    '',
    `> ${hero.lede}`,
    '',
    'Every page of this site is also served as Markdown: add `.md` to its path, or send `Accept: text/markdown`. The documentation follows the latest Atlas on the main branch.',
    '',
    '## Docs',
    '',
    ...sortDocs(pages).map(page => `- [${page.title}](${link(twinOf(page.id))}): ${page.description}`),
    '',
    '## Site',
    '',
    `- [Home](${link('/index.md')}): what Atlas does, in short, and how to install it`,
    `- [News](${link('/news.md')}): summaries of what changes in the models and tools Atlas works with`,
    `- [Changelog](${link('/changelog.md')}): every change that reaches the app's Update button`,
    '',
    '## Optional',
    '',
    `- [Complete documentation](${link('/llms-full.txt')}): every page of the documentation in one file`,
    `- [Source code](${REPOSITORY}): the repository, with the architecture and the contributor guides`,
    ''
  ].join('\n')
}

export function llmsFull(pages: DocPage[], site: URL): string {
  return sortDocs(pages)
    .map(page => docMarkdown(page, site))
    .join('\n---\n\n')
}

/** The bots of AI companies, named so that the rules for them are explicit. */
export const AI_BOTS = [
  'GPTBot',
  'OAI-SearchBot',
  'ChatGPT-User',
  'ClaudeBot',
  'Claude-SearchBot',
  'Claude-User',
  'Google-Extended',
  'PerplexityBot',
  'Perplexity-User',
  'Applebot-Extended',
  'CCBot',
  'Meta-ExternalAgent',
  'Amazonbot',
  'DuckAssistBot',
  'MistralAI-User'
]

export function robotsTxt(site: URL): string {
  return [
    '# The Atlas website. Agents are welcome: every page has a Markdown twin (add .md to its path,',
    '# or send Accept: text/markdown), and /llms.txt lists them.',
    '',
    'User-agent: *',
    `Content-Signal: ${CONTENT_SIGNAL}`,
    'Allow: /',
    '',
    ...AI_BOTS.map(bot => `User-agent: ${bot}`),
    'Allow: /',
    '',
    `Sitemap: ${new URL('/sitemap-index.xml', site).toString()}`,
    ''
  ].join('\n')
}

export const SKILL_NAME = 'atlas'

export function skillMarkdown(site: URL): string {
  const link = (path: string) => new URL(path, site).toString()
  return [
    '---',
    `name: ${SKILL_NAME}`,
    'description: Answers questions about Atlas, the open-source personal agent and desktop app that runs Claude Code, Codex, the Gemini CLI and DeepSeek on several accounts and machines. Use it when someone asks how to install, set up or use Atlas, its executors, accounts, routes, machines, memory or approval gate.',
    '---',
    '',
    '# Atlas',
    '',
    "Atlas's documentation is published as Markdown. Read it before answering, and answer from it rather than from memory: Atlas changes often.",
    '',
    `1. Start with ${link('/llms.txt')}: it lists every page with a line on what it covers.`,
    `2. Read the pages that match the question. Each one is Markdown at its path with \`.md\` added, for example ${link('/docs/install.md')}.`,
    `3. When the question spans several topics, read ${link('/llms-full.txt')}, the whole documentation in one file.`,
    '',
    'Give commands exactly as the documentation writes them. For what changed recently, read the changelog at ' +
      `${link('/changelog.md')}. For how the code works inside, the architecture is at ${REPOSITORY}/blob/main/docs/ARCHITECTURE.md.`,
    ''
  ].join('\n')
}

export function sha256(text: string): string {
  return createHash('sha256').update(text, 'utf8').digest('hex')
}

export function skillsIndex(site: URL): string {
  const skill = skillMarkdown(site)
  return `${JSON.stringify(
    {
      $schema: 'https://schemas.agentskills.io/discovery/0.2.0/schema.json',
      skills: [
        {
          name: SKILL_NAME,
          type: 'skill-md',
          description:
            'Answers questions about Atlas, the open-source personal agent that runs your coding agents on several accounts and machines, from its Markdown documentation.',
          url: new URL(`/.well-known/agent-skills/${SKILL_NAME}/SKILL.md`, site).toString(),
          digest: `sha256:${sha256(skill)}`
        }
      ]
    },
    null,
    2
  )}\n`
}
