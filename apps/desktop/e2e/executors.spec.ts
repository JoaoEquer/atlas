import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, readFileSync, utimesSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import { expect, type Page, test } from '@playwright/test'

import { vscodeLink } from '../electron/editor'

import { atlas, type Desktop, launch, openApp, ROOT, say } from './harness'

let desktop: Desktop | undefined

test.afterEach(async () => {
  await desktop?.close()
  desktop = undefined
})

/** Only the parent's system prompt holds the memory guidance. */
const PARENT = 'Skills come first'

const QUESTIONS = [
  {
    header: 'Runner',
    multiSelect: false,
    options: [{ label: 'Jest' }, { label: 'Vitest' }],
    question: 'Which runner?'
  }
]

/** A session Claude Code 2.1.140 keeps, in its own format (tests/plugins/data). */
const SESSION = '6f1c2a8e-4b7d-4e2a-9c3f-1a2b3c4d5e6f'
const SESSION_TITLE = 'Show cents in the cart total'
/** The wait for a session's answer when its prompt starts a Claude Code: on CI's Windows a fake one
 * starting takes seconds, and opening a session starts another to read its commands. */
const STARTED = { timeout: 30_000 }

const python =
  process.platform === 'win32'
    ? path.join(ROOT, '.venv', 'Scripts', 'python.exe')
    : path.join(ROOT, '.venv', 'bin', 'python')
const fakeClaude = JSON.stringify([python, path.join(ROOT, 'tests', 'fake_claude.py')])

/** The fake `claude`, which the Claude Code plugin Atlas ships runs: turned on as a user does in a
 * terminal, its steps read from a folder of one script per project, and the sessions it keeps
 * written where Claude Code keeps them, in the test's own Claude Code folder. */
function withClaude(scripts: string, more: Record<string, string> = {}) {
  return {
    environment: {
      CLAUDE_CONFIG_DIR: path.join(path.dirname(scripts), 'claude'),
      FAKE_CLAUDE_PROJECTS: path.join(path.dirname(scripts), 'claude', 'projects'),
      FAKE_CLAUDE_SCRIPT: scripts,
      ...more
    },
    setup: (home: string) => {
      atlas(home, 'plugins', 'set', 'claude-code', 'program', fakeClaude)
      atlas(home, 'plugins', 'enable', 'claude-code', '--yes')
    }
  }
}

/** A folder for the scripts, beside the test's home: the fake's sessions go under its parent. */
async function scriptsFolder(): Promise<string> {
  const { mkdtempSync } = await import('node:fs')
  const { tmpdir } = await import('node:os')
  const root = mkdtempSync(path.join(tmpdir(), 'atlas-claude-'))
  const scripts = path.join(root, 'scripts')
  mkdirSync(scripts)

  return scripts
}

/** Claude Code's name for a project's folder (the SDK's `_sanitize_path`). */
function projectFolder(folder: string): string {
  return folder.replace(/[^a-zA-Z0-9]/g, '-')
}

/** The session as the user left it in a terminal, in `folder`. */
function keepOldSession(claude: string, folder: string): void {
  const text = readFileSync(path.join(ROOT, 'tests', 'plugins', 'data', 'claude-session.jsonl'), 'utf8')
  const escaped = JSON.stringify('C:\\work\\shop').slice(1, -1)
  const moved = text.split(escaped).join(JSON.stringify(folder).slice(1, -1))
  const project = path.join(claude, 'projects', projectFolder(folder))
  mkdirSync(project, { recursive: true })
  writeFileSync(path.join(project, `${SESSION}.jsonl`), moved)
}

async function openClaudeCode(page: Page): Promise<void> {
  await page.getByTestId('tab-executors').click()
  const node = page.locator('[data-testid="executor-node"][data-executor="claude-code"]')
  // A click on its row opens it under the row, and closes it when it is open.
  if ((await node.getAttribute('data-open')) !== 'true') {
    await node.getByTestId('executor-card').click()
  }
  await expect(node).toHaveAttribute('data-open', 'true')
}

/** Open a folder of the list: its sessions are read only then. */
async function openFolder(page: Page, folder: string): Promise<void> {
  await page
    .getByTestId('session-folder')
    .filter({ hasText: path.basename(folder) })
    .getByTestId('session-folder-toggle')
    .click()
}

test('a task Atlas delegates opens from its line as a session, marked by Atlas, its questions inside', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts, { FAKE_CLAUDE_SESSION: 'abcdabcd-0000-4000-8000-000000000001' }))
  const { folder, model, page } = desktop
  writeFileSync(
    path.join(scripts, `${path.basename(folder)}.json`),
    JSON.stringify([
      { say: 'The test imports the wrong module.' },
      { input: { command: 'npm test' }, tool: 'Bash' },
      { questions: QUESTIONS },
      { result: 'Fixed the import; the tests pass on Vitest.' }
    ])
  )
  const tasks = [{ folder: '.', goal: 'Fix the failing test.' }]
  await model.reply(
    {
      calls: [{ arguments: JSON.stringify({ approval: 'manual', executor: 'claude-code', tasks }), name: 'delegate' }],
      when: PARENT
    },
    { text: 'Claude Code is on it.', when: PARENT },
    { text: 'Claude Code fixed it, on Vitest.', when: PARENT }
  )
  await say(page, 'Fix my failing test')

  // The tab says something waits there; the delegate line opens the session.
  await expect(page.getByTestId('tab-executors-alert')).toBeVisible()
  const delegated = page.getByTestId('tool-row').filter({ hasText: 'Fix the failing test.' })
  await expect(delegated.locator('summary')).toContainText(/Delegat\w+: Fix the failing test\./)
  await delegated.getByTestId('open-delegated').click()
  await expect(page.getByTestId('session-box')).toBeVisible()

  const approval = page.getByTestId('executor-approval-card')
  await expect(approval).toContainText('Claude Code wants to run a command')
  await expect(approval).toContainText('npm test')
  // The call it asks for waits for the user: the session read meanwhile does not cut it short
  // (found by the UI review: it read "cancelled").
  await expect(page.getByTestId('tool-row').filter({ hasText: 'npm test' })).toHaveAttribute('data-status', 'waiting')
  await approval.getByRole('button', { name: 'Allow' }).click()
  const question = page.getByTestId('executor-question-card')
  await expect(question).toContainText('Which runner?')
  await question.getByRole('button', { name: 'Vitest' }).click()
  await question.getByRole('button', { name: 'Answer' }).click()

  // The session as Claude Code kept it, and the list marks it Atlas's.
  await expect(page.getByTestId('assistant-message').last()).toContainText('The test imports the wrong module.')
  await expect(page.getByTestId('session-row').first().getByTestId('session-marks')).toContainText('by Atlas')
  await expect(page.getByTestId('tab-executors-alert')).toBeHidden()

  // Its result comes back to Atlas's conversation.
  await page.getByTestId('tab-conversations').click()
  await page.getByTestId('conversation-row').first().click()
  await expect(page.getByTestId('assistant-message').last()).toContainText('Claude Code fixed it, on Vitest.')
})

test('a session begun in a terminal is listed under its folder, opens as a conversation and goes on here', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { claude, folder, page } = desktop
  keepOldSession(claude, folder)
  writeFileSync(
    path.join(scripts, `${path.basename(folder)}.resume.json`),
    JSON.stringify([{ say: 'Running them again.' }, { sleep: 1.5 }, { questions: QUESTIONS }, { result: 'Green.' }])
  )

  await openClaudeCode(page)
  // The folder is listed closed, none of its sessions read until it opens.
  await expect(page.getByTestId('session-folder').filter({ hasText: path.basename(folder) })).toHaveAttribute(
    'data-open',
    'false'
  )
  await expect(page.getByTestId('session-row')).toHaveCount(0)
  await openFolder(page, folder)
  const old = page
    .getByTestId('session-folder')
    .filter({ hasText: path.basename(folder) })
    .getByTestId('session-row')
  await expect(old).toContainText(SESSION_TITLE)
  await old.click()

  // What the terminal session said, as a trail: the prompts, the tools with IN and OUT, the answers.
  await expect(page.getByTestId('user-message').first()).toContainText('Make the cart total show cents.')
  await expect(page.getByTestId('tool-row').filter({ hasText: 'Bash' })).toHaveAttribute('data-status', 'failed')
  await expect(page.getByTestId('assistant-message').last()).toContainText('The code rounds now; the test passes.')

  // The title bar opens the session's folder in VS Code, by VS Code's own link: kept here, not opened.
  await desktop.app.evaluate(({ shell }) => {
    const opened: string[] = []
    Object.assign(globalThis, { openedLinks: opened })
    shell.openExternal = (link: string) => {
      opened.push(link)

      return Promise.resolve()
    }
  })
  await page.getByTestId('title-open-editor').click()
  await expect
    .poll(() => desktop?.app.evaluate(() => (globalThis as unknown as { openedLinks: string[] }).openedLinks))
    .toEqual([vscodeLink(folder)])

  const box = page.getByTestId('session-box')
  await box.fill('Run the tests again.')
  await box.press('Enter')
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'working')
  // While it works, the box steers the turn, and its send is the stop while it is empty.
  await expect(box).toHaveAttribute('placeholder', 'It is working: this enters the turn running')
  // Stopping is the box's.
  await expect(page.getByTestId('stop')).toBeVisible()
  await box.fill('Use Vitest.')
  await expect(page.getByTestId('stop')).toBeHidden()
  await box.fill('')

  const question = page.getByTestId('executor-question-card')
  await expect(question).toContainText('Which runner?')
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'waiting')
  // Its dot says so too (asked for by the user on 20/10/2026).
  await expect(page.getByTestId('session-row').first().getByTestId('status-dot')).toHaveAttribute('data-dot', 'waiting')
  await question.getByRole('button', { name: 'Vitest' }).click()
  await question.getByRole('button', { name: 'Answer' }).click()

  // The turn ended: read again as Claude Code kept it, the old session and the new turn in one.
  await expect(page.getByTestId('user-message').last()).toContainText('Run the tests again.')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Running them again.', STARTED)
  await expect(page.getByTestId('user-message').first()).toContainText('Make the cart total show cents.')
  // Idle, so what is sent next starts a turn and does not steer this one.
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'idle', STARTED)
  // Ended on screen, it was seen: no dot.
  await expect(page.getByTestId('session-row').first().getByTestId('status-dot')).toHaveCount(0)

  // `/` offers the commands Claude Code takes, as it describes them, its own apart from skills.
  await box.fill('/co')
  const commands = page.getByTestId('slash-commands')
  await expect(commands.locator('[data-group="command"]')).toContainText('Clear the conversation history')
  await commands.locator('[data-command="compact"]').click()
  await expect(box).toHaveValue('/compact ')

  // One it runs itself answers with its words, and reads back as it was typed.
  await box.fill('/context')
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Context Usage', STARTED)
  await expect(page.getByTestId('user-message').last()).toContainText('/context')

  // Its permission modes in a menu by the box (step 18g), "Atlas answers" gone: automatic from the
  // start (20/10/2026); another goes at once, and automatic again only once its warning is read and
  // confirmed (17/10/2026).
  const mode = page.getByTestId('mode-select')
  await expect(mode).toHaveAttribute('data-mode', 'auto')
  await mode.click()
  await expect(page.getByTestId('mode-option')).toHaveText([/^Manual/, /^Accept edits/, /^Plan/, /^Automatic/])
  await page.getByTestId('mode-option').filter({ hasText: 'Plan' }).click()
  await expect(mode).toHaveAttribute('data-mode', 'plan')
  await mode.click()
  await page.getByTestId('mode-option').filter({ hasText: 'Automatic' }).click()
  const warning = page.getByTestId('harness-auto-warning')
  await expect(warning).toContainText('may run commands and edit files')
  await expect(mode).toHaveAttribute('data-mode', 'plan')
  await warning.getByTestId('harness-auto-confirm').click()
  await expect(warning).toBeHidden()
  await expect(mode).toHaveAttribute('data-mode', 'auto')
  await expect(page.getByText('Atlas answers')).toBeHidden()
  // Claude Code takes files (step 16i): the box has Atlas's clip.
  await expect(page.getByRole('button', { name: 'Attach files' })).toBeVisible()

  // Another turn, stopped from the box: in send's place while it works, it ends the instance.
  await box.fill('Once more.')
  await box.press('Enter')
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'working')
  await page.getByTestId('stop').click()
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', '')
  await expect(page.getByTestId('stop')).toBeHidden()
})

test('an answer Claude Code keeps a moment after its turn ends stays on screen', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { claude, folder, page } = desktop
  keepOldSession(claude, folder)
  writeFileSync(
    path.join(scripts, `${path.basename(folder)}.resume.json`),
    JSON.stringify([{ say: 'Reading the roadmap.' }, { late: 1, say: 'The brief, whole.' }, { result: 'The brief.' }])
  )

  await openClaudeCode(page)
  await openFolder(page, folder)
  await page
    .getByTestId('session-folder')
    .filter({ hasText: path.basename(folder) })
    .getByTestId('session-row')
    .click()
  await expect(page.getByTestId('assistant-message').last()).toContainText('The code rounds now; the test passes.')
  const box = page.getByTestId('session-box')
  await box.fill('Write the brief.')
  await box.press('Enter')

  // The turn ended before its answer reached the file (found on 16/10/2026): read before it, the
  // screen lost the answer until the session was opened again.
  const kept = path.join(claude, 'projects', projectFolder(folder), `${SESSION}.jsonl`)
  await expect.poll(() => readFileSync(kept, 'utf8')).toContain('The brief, whole.')
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'idle', STARTED)
  await expect(page.getByTestId('assistant-message').last()).toContainText('The brief, whole.', STARTED)
  await expect(page.getByTestId('user-message').first()).toContainText('Make the cart total show cents.')
})

test('the model and effort under the box start the session on them, and switch it mid-session', async () => {
  const scripts = await scriptsFolder()
  const log = path.join(path.dirname(scripts), 'claude.log')
  desktop = await launch(withClaude(scripts, { FAKE_CLAUDE_LOG: log }))
  const { claude, folder, page } = desktop
  keepOldSession(claude, folder)
  writeFileSync(
    path.join(scripts, `${path.basename(folder)}.resume.json`),
    JSON.stringify([{ say: 'On it.' }, { result: 'Done.' }])
  )
  const received = () =>
    readFileSync(log, 'utf8')
      .split('\n')
      .filter(Boolean)
      .map(line => JSON.parse(line) as { argv?: string[]; request?: Record<string, unknown> })
  const session = () => received().filter(entry => entry.argv?.includes(`--resume=${SESSION}`))

  await openClaudeCode(page)
  await openFolder(page, folder)
  await page
    .getByTestId('session-folder')
    .filter({ hasText: path.basename(folder) })
    .getByTestId('session-row')
    .click()

  // What Claude Code's `/model` lists, its default first; the effort lists what the model takes,
  // on Atlas's default until another is chosen, marked in the list.
  const model = page.getByTestId('session-model')
  const effort = page.getByTestId('session-effort')
  await expect(model).toHaveValue('default')
  await expect(effort).toHaveValue('medium')
  await model.selectOption('sonnet')
  await expect(effort.locator('option')).toHaveText(['Low', 'Medium · default', 'High'])
  await effort.selectOption('high')

  // Chosen before any instance runs: the instance its first prompt starts runs on them.
  const box = page.getByTestId('session-box')
  await box.fill('Go.')
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('On it.', STARTED)
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'idle')
  const argv = session()[0]?.argv ?? []
  expect(argv.slice(argv.indexOf('--model'), argv.indexOf('--model') + 2)).toEqual(['--model', 'sonnet'])
  expect(argv.slice(argv.indexOf('--effort'), argv.indexOf('--effort') + 2)).toEqual(['--effort', 'high'])

  // Mid-session, on the same process: a model that takes no effort hides it.
  await model.selectOption('haiku')
  await expect(effort).toBeHidden()
  await expect
    .poll(() => received().map(entry => entry.request))
    .toContainEqual({ model: 'haiku', subtype: 'set_model' })
  await box.fill('Again.')
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('On it.', STARTED)
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'idle')
  await expect(page.getByTestId('session-ran-as')).toBeHidden()
  expect(session()).toHaveLength(1)
})

test('a session open in VS Code warns first, and goes on in a copy or opens to read only', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { claude, folder, page } = desktop
  keepOldSession(claude, folder)
  // Claude Code's registry of its running processes: this one, as if it were VS Code's.
  mkdirSync(path.join(claude, 'sessions'))
  writeFileSync(
    path.join(claude, 'sessions', `${String(process.pid)}.json`),
    JSON.stringify({ entrypoint: 'claude-vscode', kind: 'interactive', pid: process.pid, sessionId: SESSION })
  )
  writeFileSync(
    path.join(scripts, `${path.basename(folder)}.resume.json`),
    JSON.stringify([{ say: 'Going on in the copy.' }, { result: 'Done.' }])
  )

  await openClaudeCode(page)
  await openFolder(page, folder)
  // The copy will carry its title too: the original is known by its id.
  const old = page.locator(`[data-testid="session-row"][data-session="${SESSION}"]`)
  await expect(old).toContainText(SESSION_TITLE)
  await expect(old.getByTestId('session-marks')).toContainText('open in VS Code')
  await old.click()

  const warning = page.getByTestId('session-elsewhere')
  await expect(warning).toContainText('This session is open in VS Code.')
  await expect(page.getByTestId('session-box')).toBeHidden()
  await warning.getByRole('button', { name: 'Continue in a copy' }).click()
  await expect(page.getByTestId('session-copy-note')).toBeVisible()
  const box = page.getByTestId('session-box')
  await box.fill('Go on in a copy.')
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Going on in the copy.', STARTED)

  // The copy is a session of its own; the one VS Code holds is as it was.
  await expect(page.getByTestId('session-row')).toHaveCount(2)
  await old.click()
  await page.getByTestId('session-elsewhere').getByRole('button', { name: 'Read only' }).click()
  await expect(page.getByTestId('session-read-only')).toBeVisible()
  await expect(page.getByTestId('user-message')).not.toContainText(['Go on in a copy.'])
})

test('an executor has its connectors beside, and both sides of the window hide and come back, the same on every screen', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { page } = desktop

  await page.setViewportSize({ height: 820, width: 1080 })
  // Hidden, the panel's strip is on every screen, the conversations' too.
  await expect(page.getByTestId('side-panel')).toBeHidden()
  await expect(page.getByTestId('panel-show')).toBeVisible()
  // Choosing an executor leaves the panel hidden; opened, it is on the executor's tab.
  await openClaudeCode(page)
  await expect(page.getByTestId('side-panel')).toBeHidden()
  await page.getByTestId('panel-show').click()
  // The panel leaves room for the conversation, and the sidebar control stays inside its rail.
  const nav = page.getByRole('navigation', { name: 'Conversations' })
  const hideSidebar = page.getByTestId('sidebar-hide')
  const navRight = await nav.evaluate(element => element.getBoundingClientRect().right)
  const toggleRight = await hideSidebar.evaluate(element => element.getBoundingClientRect().right)
  const composerWidth = await page.getByTestId('composer').evaluate(element => element.getBoundingClientRect().width)
  expect(toggleRight).toBeLessThanOrEqual(navRight)
  expect(composerWidth).toBeGreaterThanOrEqual(380)

  // At the minimum app width, the side panel overlays instead of squeezing the composer: squeezed,
  // it would have the window less both 288px rails.
  await page.setViewportSize({ height: 820, width: 720 })
  const narrowComposerWidth = await page
    .getByTestId('composer')
    .evaluate(element => element.getBoundingClientRect().width)
  expect(narrowComposerWidth).toBeGreaterThanOrEqual(400)

  // The executor's tab, its MCP servers read with no prompt: those down behind a click, a
  // plugin's under the plugin (22/10/2026).
  const panel = page.getByTestId('executor-panel')
  await expect(page.getByTestId('executor-tab')).toHaveAttribute('aria-selected', 'true')
  await expect(panel).toContainText('someone@example.com')
  const servers = panel.getByTestId('executor-servers')
  const rows = servers.getByTestId('connector-row')
  await expect(rows).toHaveCount(2)
  await expect(servers.getByTestId('connector-group').first()).toContainText('Yours')
  await expect(rows.filter({ hasText: 'files' })).toContainText('2 tools')
  await servers.getByTestId('servers-down').click()
  await expect(rows).toHaveCount(3)
  const account = servers.getByTestId('connector-group').filter({ hasText: 'From the account' })
  await expect(account.getByTestId('connector-row')).toHaveCount(2)
  await expect(panel).toContainText('done in Claude Code itself (/mcp)')
  const marketing = panel.getByTestId('plugin-row').filter({ hasText: 'marketing' })
  await expect(marketing.locator('summary [title="0 of 1 MCP server"]')).toBeVisible()
  await marketing.locator('summary').click()
  await expect(marketing.getByTestId('connector-row')).toHaveAttribute('data-status', 'needs-auth')
  const design = panel.getByTestId('plugin-row').filter({ hasText: 'design' })
  await design.locator('summary').click()
  await expect(design.getByTestId('connector-row')).toContainText('No URL configured for this server')

  // Its skills, read in the folder with no prompt.
  await expect(panel.getByTestId('executor-skills').getByTestId('skill-row')).toHaveText('review')

  // Read again, on asking.
  await servers.getByTestId('connectors-refresh').click()
  await expect(rows).toHaveCount(3)

  // The panel hides to a strip, and comes back.
  await page.getByTestId('panel-hide').click()
  await expect(panel).toBeHidden()
  await page.getByTestId('panel-show').click()
  await expect(panel).toBeVisible()

  // The sidebar hides to a strip, by its button and by Ctrl+B, and comes back.
  await page.getByTestId('sidebar-hide').click()
  await expect(page.getByTestId('tab-executors')).toBeHidden()
  await page.getByTestId('sidebar-show').click()
  await expect(page.getByTestId('tab-executors')).toBeVisible()
  await page.keyboard.press('Control+b')
  await expect(page.getByTestId('tab-executors')).toBeHidden()
  await page.keyboard.press('Control+b')
  await expect(page.getByTestId('tab-executors')).toBeVisible()

  // Closed in the sidebar, the panel keeps its tab (asked for by the user on 22/10/2026: every click
  // on it shows its plan); on another tab of the sidebar it leaves it and stays open.
  await page.getByTestId('executor-card').filter({ hasText: 'Claude Code' }).click()
  await expect(page.getByTestId('executor-tab')).toBeVisible()
  await page.getByTestId('tab-bots').click()
  await expect(page.getByTestId('executor-tab')).toBeHidden()
  await expect(page.getByTestId('side-panel')).toBeVisible()
  // Hidden there, it stays hidden when an executor is chosen again.
  await page.getByTestId('panel-hide').click()
  await openClaudeCode(page)
  await expect(page.getByTestId('side-panel')).toBeHidden()
  await expect(page.getByTestId('panel-show')).toBeVisible()
})

test('both sides open at the same width, and their edges drag them wider or narrower, the conversation keeping its room', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { page } = desktop
  await page.setViewportSize({ height: 820, width: 1400 })
  await openClaudeCode(page)
  await page.getByTestId('panel-show').click()

  const nav = page.getByRole('navigation', { name: 'Conversations' })
  const panel = page.getByTestId('side-panel')
  const width = (where: typeof nav) => where.evaluate(element => element.getBoundingClientRect().width)
  const conversation = () => page.locator('.main-pane').evaluate(element => element.getBoundingClientRect().width)
  /**
   * Drag an edge `by` pixels to the right, a step at a time, as a hand does, stopping at the
   * window's border: a busy renderer merges the moves of one frame into the last, and a last move
   * past the border was dropped with the ones before it.
   */
  const drag = async (edge: string, by: number) => {
    const box = await page.getByTestId(edge).boundingBox()
    const view = page.viewportSize()

    if (!box || !view) {
      throw new Error(`${edge} is not on screen`)
    }

    const x = box.x + box.width / 2
    const y = box.y + box.height / 2
    await page.mouse.move(x, y)
    await page.mouse.down()
    await page.mouse.move(Math.min(view.width - 1, Math.max(1, x + by)), y, { steps: 8 })
    await page.mouse.up()
  }

  // Widths are polled: a rail takes its width a frame after the pointer moves, and on a busy
  // machine that frame came after the read.
  // The same width on both sides.
  await expect.poll(() => width(nav)).toBe(288)
  await expect.poll(() => width(panel)).toBe(288)

  // The sidebar's edge drags right to widen it, the panel's left; a terminal or frame under the
  // pointer does not take the drag away.
  await drag('sidebar-edge', 100)
  await expect.poll(() => width(nav)).toBe(388)
  await drag('panel-edge', -120)
  await expect.poll(() => width(panel)).toBe(408)
  await expect(page.getByTestId('composer')).toBeVisible()

  // Dragged too far, a rail stops where the conversation keeps its minimum, and at its own.
  await drag('panel-edge', -900)
  await expect.poll(conversation).toBeGreaterThanOrEqual(383)
  await expect.poll(() => width(panel)).toBeLessThanOrEqual(640)
  await drag('sidebar-edge', -900)
  await expect.poll(() => width(nav)).toBe(224)

  // A double click puts an edge back; a width dragged stays for next time, in the window's storage.
  await page.getByTestId('sidebar-edge').dblclick()
  await expect.poll(() => width(nav)).toBe(288)
  const kept = await page.evaluate(() => [
    localStorage.getItem('atlas.sidebarWidth'),
    localStorage.getItem('atlas.panelWidth')
  ])
  expect(kept[0]).toBeNull()
  expect(Number(kept[1])).toBe(await width(panel))

  // A narrower window narrows both rails before the conversation.
  await page.getByTestId('panel-edge').dblclick()
  await drag('sidebar-edge', 200)
  await drag('panel-edge', -200)
  await page.setViewportSize({ height: 820, width: 1000 })
  await expect.poll(conversation).toBeGreaterThanOrEqual(383)
  await expect.poll(() => width(nav)).toBeGreaterThanOrEqual(224)
  await expect.poll(() => width(panel)).toBeGreaterThanOrEqual(224)
  await expect.poll(() => width(nav)).toBeLessThan(488)
})

test('a first run offers the harnesses installed here, turned on with consent right there', async () => {
  // Installed, its plugin off: Atlas looks for it at every start all the same.
  desktop = await launch({
    openRouterKey: 'sk-or-v1-good',
    setup: home => {
      atlas(home, 'plugins', 'set', 'claude-code', 'program', fakeClaude)
    }
  })
  const { page } = desktop
  // The Executors step, after the Model step skipped.
  await page.getByTestId('first-run-skip').click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'executors')
  // Codex is offered beside it, found here or not: this test is about Claude Code's.
  const offer = page.getByTestId('executor-offer').filter({ hasText: 'Claude Code' })
  await expect(offer).toContainText('Claude Code 2.1.140 · someone@example.com')
  await offer.getByRole('button', { name: 'Turn on' }).click()
  const consent = offer.getByTestId('executor-consent')
  await expect(consent).toContainText('Adds the executor claude-code')
  await consent.getByRole('button', { name: 'Turn it on' }).click()
  await expect(offer).toContainText('on')
  await expect(offer.getByRole('button', { name: 'Turn on' })).toBeHidden()

  // In the executors tab it is there to open.
  await page.getByTestId('first-run-leave').click()
  await page.getByTestId('tab-executors').click()
  await expect(page.getByTestId('executor-card')).toContainText('Claude Code')
})

test('a harness not found here is shown greyed out, with why, and nothing to turn on', async () => {
  desktop = await launch({
    openRouterKey: 'sk-or-v1-good',
    setup: home => {
      atlas(home, 'plugins', 'set', 'claude-code', 'program', '["no-such-claude"]')
    }
  })
  const { page } = desktop
  await page.getByTestId('first-run-skip').click()
  // Codex is offered beside it, found here or not: this test is about Claude Code's.
  const offer = page.getByTestId('executor-offer').filter({ hasText: 'Claude Code' })
  // The why in full (18/10/2026), where it used to say only that it was not found.
  await expect(offer.getByTestId('executor-problem')).toContainText('Claude Code could not start')
  await expect(offer).toHaveAttribute('data-usable', 'false')
  await expect(offer.getByRole('button', { name: 'Turn on' })).toBeHidden()
})

test('the first run opened again brings the sessions of a window, and archives the older ones', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { claude, folder, page } = desktop
  // The home's one-time offer of "Bring your work" decided first, with nothing old to offer: the
  // old folder below would otherwise open it under the test's clicks.
  await page.waitForFunction(() => localStorage.getItem('atlas.firstRun') !== null, undefined, STARTED)
  keepOldSession(claude, folder)
  // A second folder, last touched two months ago.
  const old = path.join(path.dirname(folder), 'old-shop')
  const text = readFileSync(path.join(claude, 'projects', projectFolder(folder), `${SESSION}.jsonl`), 'utf8')
  const project = path.join(claude, 'projects', projectFolder(old))
  mkdirSync(project, { recursive: true })
  const file = path.join(project, '0a1b2c3d-0000-4000-8000-000000000002.jsonl')
  writeFileSync(file, text.split(JSON.stringify(folder).slice(1, -1)).join(JSON.stringify(old).slice(1, -1)))
  const twoMonths = (Date.now() - 60 * 24 * 60 * 60 * 1000) / 1000
  utimesSync(file, twoMonths, twoMonths)

  await page.getByTestId('settings-open').click()
  await page.getByTestId('settings-first-run-open').click()
  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'model')
  await page.getByTestId('first-run-step-work').click()

  const work = page.getByTestId('first-run-work')
  await expect(work.getByTestId('first-run-executor')).toHaveText('Claude Code')
  await expect(work.getByTestId('first-run-counts')).toHaveText('1 Claude Code', STARTED)
  await work.getByTestId('first-run-window').selectOption('90')
  await expect(work.getByTestId('first-run-counts')).toHaveText('2 Claude Code', STARTED)
  await work.getByTestId('first-run-window').selectOption('30')
  await work.getByRole('button', { name: 'Bring 1 session' }).click()
  await expect(page.getByTestId('first-run')).toBeHidden()

  // The list keeps the folder of the window; the old one waits under Archived, untouched.
  await openClaudeCode(page)
  await expect(page.getByTestId('session-folder')).toHaveCount(1)
  await expect(page.getByTestId('session-folder')).toHaveAttribute('data-folder', folder)
  expect(existsSync(file)).toBe(true)
})

test('a home that had its key before is offered "Bring your work" once, alone, for its old folders', async () => {
  const scripts = await scriptsFolder()
  // A folder whose only session was last touched two months ago, there before Atlas opens.
  const claude = path.join(path.dirname(scripts), 'claude')
  const old = path.join(path.dirname(scripts), 'old-shop')
  keepOldSession(claude, old)
  const file = path.join(claude, 'projects', projectFolder(old), `${SESSION}.jsonl`)
  const twoMonths = (Date.now() - 60 * 24 * 60 * 60 * 1000) / 1000
  utimesSync(file, twoMonths, twoMonths)
  desktop = await launch(withClaude(scripts))
  const { page } = desktop

  await expect(page.getByTestId('first-run')).toHaveAttribute('data-step', 'work', STARTED)
  await expect(page.getByTestId('first-run-step-model')).toHaveAttribute('data-mark', 'done')
  await page.getByTestId('first-run-leave').click()
  await expect(page.getByTestId('first-run')).toBeHidden()

  // Once: a reload does not ask again.
  await page.reload()
  await expect(page.getByTestId('tab-executors')).toBeVisible(STARTED)
  await expect(page.getByTestId('first-run')).toBeHidden()
})

test('after an update that changed its plugin, Claude Code waits for consent again, says so, and comes back', async () => {
  const scripts = await scriptsFolder()
  const claude = withClaude(scripts)
  desktop = await launch(claude)
  const { codex, dsh, folder, gemini, home, page } = desktop
  keepOldSession(desktop.claude, folder)
  await openClaudeCode(page)
  await openFolder(page, folder)
  await page.getByTestId('session-row').filter({ hasText: SESSION_TITLE }).click()
  await expect(page.getByTestId('user-message').first()).toContainText('Make the cart total show cents.')
  await desktop.app.close()

  // The update: the consent kept was given to what an earlier version of the plugin declared.
  const updated = spawnSync(
    python,
    [
      '-c',
      'import sqlite3, sys; db = sqlite3.connect(sys.argv[1]); ' +
        `db.execute("UPDATE plugins_state SET consent = 'earlier' WHERE name = 'claude-code'"); db.commit()`,
      path.join(home, 'atlas.db')
    ],
    { encoding: 'utf8', windowsHide: true }
  )
  expect(updated.status, updated.stderr).toBe(0)

  const reopened = await openApp(path.dirname(home), home, {
    CODEX_HOME: codex,
    DSH_HOME: dsh,
    GEMINI_CLI_HOME: gemini,
    ...claude.environment
  })

  try {
    const again = reopened.page
    // The session's tab comes back saying why it cannot go on, where it used to read forever.
    const notice = again.getByTestId('session-not-running')
    await expect(notice).toContainText('Claude Code waits for your consent again after an update', STARTED)
    // And the sidebar keeps Claude Code, saying the same, where it used to drop it.
    await again.getByTestId('tab-executors').click()
    // The sidebar's, not the tab's own.
    const waiting = again.locator(
      '[data-testid="executor-consent-again"][data-executor="claude-code"]:not([data-testid="session-not-running"] *)'
    )
    await expect(waiting).toBeVisible()

    await waiting.getByTestId('executor-review-consent').click()
    const row = again.locator('[data-testid="plugin-row"][data-plugin="claude-code"]')
    await expect(row.getByTestId('plugin-consent-again')).toBeVisible()
    const consent = row.getByTestId('plugin-consent')
    await expect(consent).toContainText('Adds the executor claude-code')
    await consent.getByTestId('plugin-consent-yes').click()
    await expect(row.getByTestId('plugin-consent-again')).toBeHidden()

    // On again: the sidebar lists it as before, and its session's tab reads the session.
    await expect(waiting).toBeHidden(STARTED)
    await expect(again.locator('[data-testid="executor-node"][data-executor="claude-code"]')).toBeVisible()
    await again.getByTestId('tab').filter({ hasText: SESSION_TITLE }).click()
    await expect(again.getByTestId('session-not-running')).toBeHidden()
    await expect(again.getByTestId('user-message').first()).toContainText('Make the cart total show cents.', STARTED)
  } finally {
    await reopened.app.close().catch(() => undefined)
  }
})

test('another account is added by its nickname, logs in in its own folder, and a session goes on there in a copy', async () => {
  const scripts = await scriptsFolder()
  // The user's home, beside which the other account's folder is made: the test's own.
  const user = path.join(path.dirname(scripts), 'user')
  mkdirSync(user)
  // Other accounts are the accounts plugin's (step 22), turned on as a user does in a terminal.
  const claudeOn = withClaude(scripts, { HOME: user, USERPROFILE: user })
  desktop = await launch({
    ...claudeOn,
    setup: (home: string) => {
      claudeOn.setup(home)
      atlas(home, 'plugins', 'enable', 'accounts', '--yes')
    }
  })
  const { claude, folder, page } = desktop
  keepOldSession(claude, folder)
  writeFileSync(
    path.join(scripts, `${path.basename(folder)}.resume.json`),
    JSON.stringify([{ account: true }, { result: 'Done.' }])
  )

  await openClaudeCode(page)
  // The plugin's part, atop Claude Code's tree once opened.
  await page.locator('[data-testid="executor-node"][data-executor="claude-code"]').getByTestId('add-account').click()
  const form = page.getByTestId('add-account-form')
  await form.getByTestId('account-nickname').fill('work')
  await form.getByRole('button', { name: 'Add' }).click()
  const work = page.locator('[data-testid="executor-node"][data-executor^="claude-code@"]')
  await expect(page.getByTestId('harness-heading')).toContainText('Claude Code')
  await expect(work.getByTestId('executor-card')).toContainText('work')
  await expect(work.getByTestId('executor-card')).toContainText('not logged in')

  // Claude Code logs it in itself, in its own folder; the email is the browser's choice.
  // Its row's menu has the login, and says who it is logged in as.
  await work.getByTestId('executor-card').click()
  await work.getByTestId('executor-menu').click()
  await page.getByTestId('executor-switch-account').click()
  await work.locator('form').getByRole('button', { name: 'Log in' }).click()
  await expect(work.getByTestId('executor-card')).not.toContainText('not logged in', STARTED)
  await work.getByTestId('executor-menu').click()
  await expect(page.getByTestId('executor-menu-account')).toHaveText('other@example.com')
  await page.keyboard.press('Escape')

  // The old session goes on on the main account first.
  await openClaudeCode(page)
  await openFolder(page, folder)
  await page.getByTestId('session-row').filter({ hasText: SESSION_TITLE }).click()
  const box = page.getByTestId('session-box')
  await box.fill('Go.')
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Running on own.', STARTED)
  const original = path.join(claude, 'projects', projectFolder(folder), `${SESSION}.jsonl`)
  // The account is chosen with the route, from one pill by the box.
  await page.getByTestId('session-route').click()
  // The route is a path: a stop added says whether it can take over, and a copy compacts first.
  const route = page.getByTestId('session-route-editor')
  await route.getByTestId('session-route-others').selectOption({ label: 'Claude Code · work' })
  await expect(route.getByTestId('session-route-name')).toHaveText(['1. Claude Code · work'], STARTED)
  await expect(route.getByTestId('session-route-ready')).toContainText('ready')
  await expect(route.getByTestId('session-route-compact')).toBeChecked()
  const workAccount = page.getByTestId('session-account').getByRole('radio', { name: 'work' })
  await expect(workAccount).toBeEnabled(STARTED)
  const before = readFileSync(original, 'utf8')

  // Switched, after the user read what changes: a copy on the other account, the original untouched.
  await workAccount.click()
  const note = page.getByTestId('account-switch')
  await expect(note).toContainText('Go on on work (other@example.com)?', STARTED)
  await expect(note.getByTestId('account-switch-missing')).toContainText('files')
  await note.getByTestId('account-switch-confirm').click()
  await expect(page.getByTestId('session-route')).toContainText('work', STARTED)
  expect(readFileSync(original, 'utf8')).toBe(before)
  await box.fill('Again.')
  await box.press('Enter')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Running on .claude-work.', STARTED)
  const copies = path.join(user, '.claude-work', 'projects', projectFolder(folder))
  expect(readdirSync(copies).filter(name => name.endsWith('.jsonl'))).toHaveLength(1)
  // Atlas deletes no session (decided with the user on 03/11/2026): the original stays on its account.
  expect(readFileSync(original, 'utf8')).toBe(before)
})

test('a file pasted or picked in a session goes to Claude Code as the SDK sends it, and stays on screen reopened', async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { claude, folder, page } = desktop
  keepOldSession(claude, folder)
  writeFileSync(
    path.join(scripts, `${path.basename(folder)}.resume.json`),
    JSON.stringify([{ say: 'The total is cut off.' }, { result: 'Seen.' }])
  )

  await openClaudeCode(page)
  await openFolder(page, folder)
  await page
    .getByTestId('session-folder')
    .filter({ hasText: path.basename(folder) })
    .getByTestId('session-row')
    .click()
  await page.getByTestId('attachment-picker').setInputFiles([
    {
      buffer: Buffer.from(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==',
        'base64'
      ),
      mimeType: 'image/png',
      name: 'cart.png'
    },
    { buffer: Buffer.from('%PDF-1.7\n% the spec\n'), mimeType: 'application/pdf', name: 'spec.pdf' }
  ])
  await expect(page.getByTestId('composer-attachments').getByTestId('attachment-chip')).toHaveCount(2)
  const box = page.getByTestId('session-box')
  await box.fill('What is wrong in the cart?')
  await expect(page.getByRole('button', { name: 'Send' })).toBeEnabled()
  await box.press('Enter')

  // Claude Code got the image as an `image` block and both files by their paths.
  const kept = path.join(claude, 'projects', projectFolder(folder), `${SESSION}.jsonl`)
  await expect.poll(() => readFileSync(kept, 'utf8'), STARTED).toContain('The total is cut off.')
  const sent = readFileSync(kept, 'utf8')
  expect(sent).toContain('"type": "image"')
  expect(sent).toContain('<atlas-attachments>')
  expect(sent).toMatch(/cart\.png \(image\/png, shown above\)/)
  expect(sent).toMatch(/spec\.pdf \(application\/pdf\)/)

  // Read back from Claude Code's own file once the turn ends: the chips above the prompt, the note
  // not among its words.
  await expect(page.getByTestId('session-row').first()).toHaveAttribute('data-state', 'idle', STARTED)
  await expect(page.getByTestId('assistant-message').last()).toContainText('The total is cut off.', STARTED)
  const prompt = page.getByTestId('user-message').last()
  await expect(prompt).toContainText('What is wrong in the cart?')
  await expect(prompt).not.toContainText('atlas-attachments')
  await expect(prompt.getByTestId('attachment-chip')).toHaveCount(2)
})

test("the box's new worktree is a workspace in the home, listed under its project and deleted from there", async () => {
  const scripts = await scriptsFolder()
  desktop = await launch(withClaude(scripts))
  const { claude, folder, home, page } = desktop
  keepOldSession(claude, folder)
  writeFileSync(path.join(folder, 'README.md'), 'The shop.\n')
  const identity = {
    ...process.env,
    GIT_AUTHOR_EMAIL: 'ana@example.com',
    GIT_AUTHOR_NAME: 'Ana',
    GIT_COMMITTER_EMAIL: 'ana@example.com',
    GIT_COMMITTER_NAME: 'Ana'
  }
  for (const step of [
    ['init', '--quiet', '--initial-branch', 'main'],
    ['add', '--all'],
    ['commit', '--quiet', '--message', 'The shop']
  ]) {
    spawnSync('git', ['-C', folder, ...step], { env: identity, windowsHide: true })
  }

  await openClaudeCode(page)
  await openFolder(page, folder)
  await page
    .getByTestId('session-folder')
    .filter({ hasText: path.basename(folder) })
    .getByTestId('folder-new-session')
    .click()
  await page.getByTestId('where-select').click()
  await page.getByTestId('where-worktree').click()

  // Made in the home's worktrees folder, on a branch of its own, and listed under its project.
  const workspace = page.getByTestId('workspace')
  await expect(workspace).toHaveCount(1)
  await expect(page.getByTestId('workspace-project')).toContainText(path.basename(folder))
  await expect(page.getByTestId('where-branch')).toHaveText(/^atlas\//)
  const made = readdirSync(path.join(home, 'worktrees'))
  expect(made).toHaveLength(1)

  // Nothing done in it: deleted on the first yes, its folder gone.
  await workspace.hover()
  await workspace.getByTestId('workspace-delete').click()
  await page.getByTestId('workspace-delete-yes').click()
  await expect(workspace).toHaveCount(0)
  expect(readdirSync(path.join(home, 'worktrees', made[0] ?? ''))).toEqual([])
})

test("a comment on a line of a workspace's changes reaches its session as a message", async () => {
  const scripts = await scriptsFolder()
  const log = path.join(path.dirname(scripts), 'claude.log')
  desktop = await launch(withClaude(scripts, { FAKE_CLAUDE_LOG: log }))
  const { claude, folder, home, page } = desktop
  keepOldSession(claude, folder)
  writeFileSync(path.join(folder, 'README.md'), 'The shop.\n')
  const identity = {
    ...process.env,
    GIT_AUTHOR_EMAIL: 'ana@example.com',
    GIT_AUTHOR_NAME: 'Ana',
    GIT_COMMITTER_EMAIL: 'ana@example.com',
    GIT_COMMITTER_NAME: 'Ana'
  }
  for (const step of [
    ['init', '--quiet', '--initial-branch', 'main'],
    ['add', '--all'],
    ['commit', '--quiet', '--message', 'The shop']
  ]) {
    spawnSync('git', ['-C', folder, ...step], { env: identity, windowsHide: true })
  }

  await openClaudeCode(page)
  await openFolder(page, folder)
  await page
    .getByTestId('session-folder')
    .filter({ hasText: path.basename(folder) })
    .getByTestId('folder-new-session')
    .click()
  await page.getByTestId('where-select').click()
  await page.getByTestId('where-worktree').click()
  await expect(page.getByTestId('workspace')).toHaveCount(1)

  // What the agent would have done: a line changed in the workspace, never in the project.
  const [project] = readdirSync(path.join(home, 'worktrees'))
  const [name] = readdirSync(path.join(home, 'worktrees', project ?? ''))
  writeFileSync(path.join(home, 'worktrees', project ?? '', name ?? '', 'README.md'), 'The shop, open late.\n')

  if (await page.getByTestId('panel-show').isVisible()) {
    await page.getByTestId('panel-show').click()
  }
  await page.getByTestId('changes-tab').click()
  const added = page.locator('[data-testid="diff-line"][data-kind="plus"]')
  await expect(added).toHaveText(/The shop, open late\./)
  await added.click()
  await page.getByTestId('comment-text').fill('Say until when.')
  await page.getByTestId('comment-add').click()
  await page.getByTestId('review-send').click()

  await expect(page.getByTestId('review-comments')).toBeHidden()
  const prompts = () =>
    readFileSync(log, 'utf8')
      .split('\n')
      .filter(Boolean)
      .map(line => JSON.parse(line) as { message?: { content?: unknown }; type?: string })
      .filter(entry => entry.type === 'user')
      .map(entry => String(entry.message?.content))
  await expect
    .poll(prompts, STARTED)
    .toContain('Review comments on your changes:\n\nREADME.md:1\n> The shop, open late.\nSay until when.')
  expect(readFileSync(path.join(folder, 'README.md'), 'utf8')).toBe('The shop.\n')
})
