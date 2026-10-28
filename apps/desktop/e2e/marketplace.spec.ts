/**
 * The marketplace end to end (step 16s): the real app, the real `atlas serve`, and the fake GitHub
 * (tests/fake_github.py) serving git repositories made here as GitHub serves archives and files.
 */
import { type ChildProcess, spawn, spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { expect, test } from '@playwright/test'

import { type Desktop, launch, ROOT, say, stopTree } from './harness'

/** tests/fake_github.py in a process of its own. */
class FakeGitHub {
  private constructor(
    private readonly child: ChildProcess,
    readonly url: string
  ) {}

  static async start(): Promise<FakeGitHub> {
    const child = spawn('uv', ['run', '--project', ROOT, 'python', 'tests/fake_github.py'], {
      cwd: ROOT,
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
    const port = await new Promise<number>((resolve, reject) => {
      let output = ''
      const timer = setTimeout(() => {
        reject(new Error(`The fake GitHub did not start.\n${output}`))
      }, 60_000)
      child.stdout.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
        const match = /FAKE_GITHUB_READY port=(\d+)/.exec(output)

        if (match?.[1]) {
          clearTimeout(timer)
          resolve(Number(match[1]))
        }
      })
      child.stderr.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
      })
    })

    return new FakeGitHub(child, `http://127.0.0.1:${String(port)}`)
  }

  /** Serve the git repository in `folder` as `owner/repo`. */
  async serve(name: string, folder: string): Promise<void> {
    const response = await fetch(`${this.url}/_fake/repo`, {
      body: JSON.stringify({ folder, name }),
      headers: { 'content-type': 'application/json' },
      method: 'POST'
    })
    expect(response.ok).toBe(true)
  }

  /** The marketplace's settings pointing at this fake. */
  config(repository: string): string {
    return [
      '[marketplace]',
      `repository = ${JSON.stringify(repository)}`,
      `archive_url = ${JSON.stringify(`${this.url}/archive/{repo}/{ref}`)}`,
      `raw_url = ${JSON.stringify(`${this.url}/raw/{repo}/{ref}/{path}`)}`
    ].join('\n')
  }

  stop(): void {
    stopTree(this.child)
  }
}

/** A git repository holding `files`, committed; its folder and commit. */
function repository(root: string, name: string, files: Record<string, string>): { folder: string; sha: string } {
  const folder = path.join(root, name.replace('/', '--'))

  for (const [file, text] of Object.entries(files)) {
    mkdirSync(path.dirname(path.join(folder, file)), { recursive: true })
    writeFileSync(path.join(folder, file), text)
  }

  const git = (...args: string[]) => {
    const done = spawnSync('git', ['-c', 'user.name=Test', '-c', 'user.email=test@example.com', ...args], {
      cwd: folder,
      encoding: 'utf8',
      windowsHide: true
    })

    if (done.status !== 0) {
      throw new Error(`git ${args.join(' ')} failed:\n${done.stderr}`)
    }

    return done.stdout.trim()
  }
  git('init', '-q', '-b', 'main')
  git('add', '-A')
  git('commit', '-q', '-m', 'First')

  return { folder, sha: git('rev-parse', 'HEAD') }
}

const MARKETPLACE = 'atlas-test/marketplace'
const PDF = `---
name: pdf
description: Read PDF files.
metadata:
  requires:
    bins: [atlas-e2e-no-such-program]
    mcp: [docs]
---
# Reading PDFs

Open the file with the docs server, then read it page by page.
`
const VIDEO = JSON.stringify({ description: 'Cut videos.', name: 'video', version: '1.0.0' })

let desktop: Desktop | undefined
let github: FakeGitHub | undefined
let scratch = ''

test.afterEach(async () => {
  await desktop?.close()
  github?.stop()
  rmSync(scratch, { force: true, maxRetries: 10, recursive: true, retryDelay: 500 })
  desktop = undefined
  github = undefined
})

test('the marketplace lists each kind, installs a skill with its server and a plugin, the model gets both, and one is made from it', async () => {
  scratch = mkdtempSync(path.join(tmpdir(), 'atlas-e2e-market-'))
  github = await FakeGitHub.start()
  const tools = repository(scratch, 'someone/tools', {
    'pdf/SKILL.md': PDF,
    'plugins/video/.claude-plugin/plugin.json': VIDEO,
    'plugins/video/README.md': '# Video\n\nCuts videos for you.\n',
    'plugins/video/skills/cut/SKILL.md': '---\nname: cut\ndescription: Cut a video.\n---\nCut it.\n'
  })
  const item = (name: string, kind: string, extra: string) =>
    `name: ${name}\nkind: ${kind}\ntitle: ${name.toUpperCase()}\nsummary: The ${name} item.\n` +
    `author: someone\nlicense: MIT\n${extra}`
  const store = repository(scratch, MARKETPLACE, {
    // Two secrets, and only one is stored: the server keeps waiting, so npx never runs a package.
    'items/docs.yaml': item(
      'docs',
      'mcp',
      'server:\n  command: npx\n  args: ["-y", "docs-mcp@1.2.3"]\n' +
        '  env:\n    KEY: "${DOCS_KEY}"\n    REGION: "${DOCS_REGION}"\n' +
        'secrets:\n  - name: DOCS_KEY\n    description: The docs key.\n' +
        '  - name: DOCS_REGION\n    description: Where the docs live.\n'
    ),
    'items/pdf.yaml': item('pdf', 'skill', `repo: someone/tools\nsha: ${tools.sha}\npath: pdf\n`),
    'items/video.yaml': item('video', 'plugin', `repo: someone/tools\nsha: ${tools.sha}\npath: plugins/video\n`),
    'marketplace.yaml': 'format: 1\nname: Test marketplace\ndescription: For the tests.\n'
  })
  await github.serve('someone/tools', tools.folder)
  await github.serve(MARKETPLACE, store.folder)
  desktop = await launch({ config: github.config(MARKETPLACE) })
  const { home, model, page } = desktop

  await page.getByTestId('marketplace-entry').click()
  const view = page.getByTestId('marketplace-view')
  const card = (name: string) => view.locator(`[data-testid="marketplace-card"][data-name="${name}"]`)

  // Plugins first: the Atlas team's, then the community's.
  await expect(card('video')).toBeVisible()
  await expect(card('telegram')).toContainText('Official')
  await view.getByTestId('marketplace-tab-executor').click()
  await expect(card('claude-code')).toContainText('Claude Code')

  // A skill's page: its own SKILL.md, and what it needs here.
  await view.getByTestId('marketplace-tab-skill').click()
  await card('pdf').click()
  const itemPage = view.getByTestId('marketplace-page')
  await expect(itemPage.getByTestId('marketplace-readme')).toContainText('read it page by page')
  const needs = itemPage.getByTestId('marketplace-need')
  await expect(needs.first()).toHaveAttribute('data-state', 'missing')
  await expect(needs.nth(1)).toHaveAttribute('data-state', 'brought')

  // Installed, with the server it needs.
  await itemPage.getByTestId('marketplace-action').click()
  await expect(itemPage.getByTestId('marketplace-action')).toHaveAttribute('data-action', 'installed')
  await expect(itemPage).toContainText('docs')
  expect(existsSync(path.join(home, 'skills', 'pdf', 'SKILL.md'))).toBe(true)
  const servers = JSON.parse(readFileSync(path.join(home, 'mcp.json'), 'utf8')) as {
    mcpServers: Record<string, unknown>
  }
  expect(Object.keys(servers.mcpServers)).toEqual(['docs'])

  // The server's secret, stored from its page and never shown again.
  await itemPage.getByText('All items').click()
  await view.getByTestId('marketplace-tab-mcp').click()
  await card('docs').click()
  // What goes into mcp.json, exactly as it was reviewed.
  await expect(itemPage.getByTestId('marketplace-entry-json')).toContainText('docs-mcp@1.2.3')
  const key = itemPage.getByTestId('marketplace-secret').filter({ hasText: 'DOCS_KEY' })
  await key.getByTestId('marketplace-secret-input').fill('k-123')
  await key.getByTestId('marketplace-secret-save').click()
  await expect(key).toHaveAttribute('data-stored', 'true')
  await expect(key.getByTestId('marketplace-secret-input')).toHaveCount(0)

  // A plugin arrives off; turning it on is the Plugins screen's consent, and its card follows.
  await itemPage.getByText('All items').click()
  await view.getByTestId('marketplace-tab-plugin').click()
  await card('video').getByTestId('marketplace-action').click()
  await expect(card('video').getByTestId('marketplace-action')).toHaveAttribute('data-action', 'turn-on')
  await card('video').getByTestId('marketplace-action').click()
  const row = page.locator('[data-testid="plugin-row"][data-plugin="video"]')
  await expect(row.getByTestId('plugin-state')).toHaveText('off')
  await row.getByTestId('plugin-enable').click()
  await expect(row.getByTestId('plugin-consent')).toContainText('video:cut')
  await row.getByTestId('plugin-consent-yes').click()
  await expect(row.getByTestId('plugin-state')).toHaveText('running')

  // The settings' Extensions show what came, a tab per kind: the skill installed, the plugin's
  // skill once it is on, and the MCP server, left out while it waits for its second secret.
  const settings = page.getByTestId('settings')
  await settings.getByTestId('extensions-tab-skill').click()
  const skill = (name: string) => settings.locator(`[data-testid="skill-card"][data-name="${name}"]`)
  await expect(skill('pdf')).toHaveAttribute('data-source', 'home')
  await expect(skill('video:cut')).toHaveAttribute('data-source', 'plugin')
  await settings.getByTestId('extensions-tab-mcp').click()
  const docs = settings.locator('[data-testid="mcp-card"][data-name="docs"]')
  await expect(docs).toHaveAttribute('data-state', 'left_out')
  await expect(docs.getByTestId('mcp-card-problem')).toContainText('DOCS_REGION')
  await expect(docs.getByTestId('mcp-sign-in')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(card('video').getByTestId('marketplace-action')).toHaveAttribute('data-action', 'on')

  // At work: the model is offered the skill from the marketplace and the plugin's.
  await page.getByRole('button', { name: 'New conversation' }).click()
  await model.reply({ text: 'Ready.' })
  await say(page, 'What can you do?')
  await expect(page.getByTestId('assistant-message')).toContainText('Ready.')
  const seen = await model.seen()
  expect(seen).toContain('- pdf: Read PDF files.')
  expect(seen).toContain('- video:cut: Cut a video.')

  // Making one: a new conversation with the skill that writes plugins in its box, sent as it is.
  await page.getByTestId('marketplace-entry').click()
  await view.getByTestId('marketplace-make').click()
  const box = page.getByRole('textbox', { name: 'Message Atlas' })
  await expect(box).toHaveValue('/write-plugin ')
  await box.press('End')
  await box.pressSequentially('a tool that says hello')
  await model.reply({ text: 'Which folder?' })
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Which folder?')
  expect(await model.seen()).toContain('atlas plugins new <name> --folder <folder>')
})
