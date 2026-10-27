/**
 * What every end-to-end test stands on: the real app (Electron, built), the real `atlas serve` it
 * starts, and the fake model server, scripted over HTTP. Only the model is fake: the server, the
 * agent, the tools and the gate are real.
 *
 * Each test gets a fresh home (`ATLAS_HOME`), a fresh work folder, and its own Electron profile, so
 * no test sees another's conversations, and none touches the user's.
 */
import { type ChildProcess, spawn, spawnSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { appendFileSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { _electron, type ElectronApplication, expect, type Locator, type Page } from '@playwright/test'

const here = path.dirname(fileURLToPath(import.meta.url))
export const DESKTOP = path.resolve(here, '..')
export const ROOT = path.resolve(DESKTOP, '..', '..')

/** A reply the fake model gives; the fields of `tests/fake_model.py`'s `FakeReply`. */
export interface Reply {
  calls?: { arguments: string; name: string }[]
  /** Seconds between streamed pieces. */
  pause?: number
  reasoning?: string
  text?: string
  /** Seconds of silence before the reply starts. */
  wait_before?: number
  /** Taken only by a request whose system prompt holds this text (a parent, or one subagent). */
  when?: string
}

export interface ModelRequest {
  /** The `Authorization` header it came with, which says the key used. */
  authorization: null | string
  body: { messages?: { content?: unknown; role: string }[] } | null
  path: string
}

export class FakeModel {
  private constructor(
    private readonly child: ChildProcess,
    readonly port: number
  ) {}

  /** `key`: the only API key it takes, as a provider does; without it, any. */
  static async start(key?: string): Promise<FakeModel> {
    const args = ['run', '--project', ROOT, 'python', path.join('tests', 'fake_model.py')]
    const child = spawn('uv', key === undefined ? args : [...args, '--key', key], {
      cwd: ROOT,
      // Its own process group on POSIX, so the stop reaches the Python under uv.
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true
    })
    const port = await new Promise<number>((resolve, reject) => {
      let output = ''
      const timer = setTimeout(() => {
        reject(new Error(`The fake model did not start.\n${output}`))
      }, 60_000)
      child.stdout.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
        const match = /FAKE_MODEL_READY port=(\d+)/.exec(output)

        if (match?.[1]) {
          clearTimeout(timer)
          resolve(Number(match[1]))
        }
      })
      child.stderr.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
      })
      child.once('exit', code => {
        clearTimeout(timer)
        reject(new Error(`The fake model stopped (${String(code)}).\n${output}`))
      })
    })

    return new FakeModel(child, port)
  }

  async reply(...replies: Reply[]): Promise<void> {
    const response = await fetch(`http://127.0.0.1:${this.port}/_fake/reply`, {
      body: JSON.stringify(replies),
      headers: { 'content-type': 'application/json' },
      method: 'POST'
    })
    expect(response.ok).toBe(true)
  }

  /** What the next transcriptions answer (OpenAI's, which the voice plugin calls). */
  async transcribe(...texts: string[]): Promise<void> {
    const response = await fetch(`http://127.0.0.1:${this.port}/_fake/transcribe`, {
      body: JSON.stringify(texts),
      headers: { 'content-type': 'application/json' },
      method: 'POST'
    })
    expect(response.ok).toBe(true)
  }

  async requests(): Promise<ModelRequest[]> {
    const response = await fetch(`http://127.0.0.1:${this.port}/_fake/requests`)
    const body = (await response.json()) as { requests: ModelRequest[] }

    return body.requests
  }

  /** Every text any request sent the model, flattened, to check what reached it. */
  async seen(): Promise<string> {
    return JSON.stringify(await this.requests())
  }

  stop(): void {
    stopTree(this.child)
  }
}

export interface Desktop {
  app: ElectronApplication
  /** Claude Code's own folder for this test (`CLAUDE_CONFIG_DIR`), so no test reaches the user's
   * sessions (step 16f). */
  claude: string
  /** Codex's own folder for this test (`CODEX_HOME`), for the same reason (step 16o). */
  codex: string
  /** The Gemini CLI's home for this test (`GEMINI_CLI_HOME`), for the same reason (step 16p). */
  gemini: string
  /** DeepSeek's harness's home for this test (`DSH_HOME`), for the same reason (step 16q). */
  dsh: string
  /** The temporary folder the agent's file tools start from. */
  folder: string
  home: string
  model: FakeModel
  page: Page
  close: () => Promise<void>
}

/** A fresh home whose model is the fake one, and the app on it. `homes` are cloned from it first,
 * so each talks to the same fake model. */
export async function launch(
  options: {
    config?: string
    environment?: Record<string, string>
    homes?: string[]
    lang?: string
    models?: string
    /** A first run: the home names no model and its vault holds no key, and OpenRouter is the
     * fake model, which takes only this key. The app opens on the key screen. */
    openRouterKey?: string
    /** What the user did in a terminal before opening the app, such as turning a plugin on. */
    setup?: (home: string, model: FakeModel) => void
    /** More of Chromium's switches, such as a fake microphone. */
    chromium?: string[]
  } = {}
): Promise<Desktop> {
  const root = mkdtempSync(path.join(tmpdir(), 'atlas-e2e-'))
  const home = path.join(root, 'home')
  const folder = path.join(root, 'work')
  await mkdir(home, { recursive: true })
  await mkdir(folder, { recursive: true })
  const model = await FakeModel.start(options.openRouterKey)

  if (options.openRouterKey === undefined) {
    pointAtFakeModel(home, model, options.models)
    writeConfig(home, { model: 'fake-model', provider: 'vllm' }, folder)
  } else {
    writeFileSync(
      path.join(home, 'models.toml'),
      `[providers.openrouter]\nbase_url = ${toml(`http://127.0.0.1:${String(model.port)}/v1`)}\n`
    )
    writeFileSync(path.join(home, 'config.toml'), `[workspace]\nfolder = ${toml(folder)}\n`)
  }

  if (options.config) {
    appendFileSync(
      path.join(home, 'config.toml'),
      `
${options.config}
`
    )
  }

  options.setup?.(home, model)

  for (const name of options.homes ?? []) {
    atlas(home, 'home', 'create', name, '--clone')
  }

  const claude = options.environment?.CLAUDE_CONFIG_DIR ?? path.join(root, 'claude')
  // Codex will not start on a folder that is not there.
  const codex = options.environment?.CODEX_HOME ?? path.join(root, 'codex')
  await mkdir(codex, { recursive: true })
  const gemini = options.environment?.GEMINI_CLI_HOME ?? path.join(root, 'gemini')
  const dsh = options.environment?.DSH_HOME ?? path.join(root, 'dsh')
  const environment = {
    CLAUDE_CONFIG_DIR: claude,
    CODEX_HOME: codex,
    DSH_HOME: dsh,
    GEMINI_CLI_HOME: gemini,
    ...options.environment
  }
  const { app, page } = await openApp(root, home, environment, options.lang, options.chromium)

  return {
    app,
    claude,
    close: async () => {
      // A test may have closed the app already (the lifecycle tests do).
      await app.close().catch(() => undefined)
      model.stop()
      rmSync(root, { force: true, maxRetries: 10, recursive: true, retryDelay: 500 })
    },
    codex,
    dsh,
    folder,
    gemini,
    home,
    model,
    page
  }
}

/**
 * `atlas serve` started by hand, as a container or another machine runs it, with a token of its
 * own: the app reaches it through `ATLAS_DESKTOP_URL` and `ATLAS_DESKTOP_TOKEN` and starts nothing
 * (docs/ARCHITECTURE.md, Desktop app and Container).
 */
export class HandServer {
  private constructor(
    private readonly child: ChildProcess,
    readonly port: number
  ) {}

  /** On `port`, or one the system picks; a port just freed may take a moment to bind again. */
  static async start(home: string, token: string, port = 0): Promise<HandServer> {
    for (let attempt = 1; ; attempt++) {
      try {
        return await HandServer.once(home, token, port)
      } catch (error) {
        if (attempt === 10) {
          throw error
        }

        await new Promise(resolve => setTimeout(resolve, 1_000))
      }
    }
  }

  private static async once(home: string, token: string, port: number): Promise<HandServer> {
    const child = spawn(
      'uv',
      ['run', '--project', ROOT, 'atlas', 'serve', '--host', '127.0.0.1', '--port', String(port)],
      {
        cwd: ROOT,
        detached: process.platform !== 'win32',
        env: cleanEnvironment({ ATLAS_HOME: home, ATLAS_SERVER_TOKEN: token }),
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: true
      }
    )
    const found = await new Promise<number>((resolve, reject) => {
      let output = ''
      const timer = setTimeout(() => {
        reject(new Error(`atlas serve did not start.\n${output}`))
      }, 90_000)
      child.stdout.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
        const match = /ATLAS_SERVER_READY port=(\d+)/.exec(output)

        if (match?.[1]) {
          clearTimeout(timer)
          resolve(Number(match[1]))
        }
      })
      child.stderr.on('data', (chunk: Buffer) => {
        output += chunk.toString('utf8')
      })
      child.once('exit', code => {
        clearTimeout(timer)
        reject(new Error(`atlas serve stopped (${String(code)}).\n${output}`))
      })
    })

    return new HandServer(child, found)
  }

  get url(): string {
    return `ws://127.0.0.1:${String(this.port)}/ws`
  }

  stop(): void {
    stopTree(this.child)
  }
}

export interface RemoteDesktop extends Desktop {
  /** The server the app reaches, which a test may stop and start again on the same port. */
  server: HandServer
  startServer: () => Promise<void>
}

/** A fresh home served by hand, the fake model behind it, and the app pointed at it. */
export async function launchRemote(): Promise<RemoteDesktop> {
  const root = mkdtempSync(path.join(tmpdir(), 'atlas-e2e-'))
  const home = path.join(root, 'home')
  const folder = path.join(root, 'work')
  await mkdir(home, { recursive: true })
  await mkdir(folder, { recursive: true })
  const model = await FakeModel.start()
  pointAtFakeModel(home, model)
  writeConfig(home, { model: 'fake-model', provider: 'vllm' }, folder)
  const token = randomBytes(24).toString('base64url')
  const first = await HandServer.start(home, token)
  const { app, page } = await openApp(root, home, { ATLAS_DESKTOP_TOKEN: token, ATLAS_DESKTOP_URL: first.url })
  const desktop: RemoteDesktop = {
    app,
    claude: path.join(root, 'claude'),
    codex: path.join(root, 'codex'),
    gemini: path.join(root, 'gemini'),
    dsh: path.join(root, 'dsh'),
    close: async () => {
      await app.close().catch(() => undefined)
      desktop.server.stop()
      model.stop()
      rmSync(root, { force: true, maxRetries: 10, recursive: true, retryDelay: 500 })
    },
    folder,
    home,
    model,
    page,
    server: first,
    startServer: async () => {
      desktop.server = await HandServer.start(home, token, first.port)
    }
  }

  return desktop
}

const toml = (value: string) => JSON.stringify(value)

/** An `atlas` command on a home, as a user runs it in a terminal. */
export function atlas(home: string, ...args: string[]): string {
  const done = spawnSync('uv', ['run', '--project', ROOT, 'atlas', ...args], {
    cwd: ROOT,
    encoding: 'utf8',
    env: cleanEnvironment({ ATLAS_HOME: home }),
    windowsHide: true
  })

  if (done.status !== 0) {
    throw new Error(`atlas ${args.join(' ')} failed (${String(done.status)}):\n${done.stdout}${done.stderr}`)
  }

  return done.stdout
}

/** Conversations put into the home before the app opens (`tests/seed_conversations.py`): what was
 * said in each and its answer, oldest first, a minute apart; `title`, a title model's. */
export function seedConversations(
  home: string,
  conversations: { answer: string; said: string; title?: string }[]
): void {
  const file = path.join(path.dirname(home), 'conversations.json')
  writeFileSync(file, JSON.stringify(conversations))
  const done = spawnSync(
    'uv',
    ['run', '--project', ROOT, 'python', path.join('tests', 'seed_conversations.py'), home, file],
    { cwd: ROOT, encoding: 'utf8', env: cleanEnvironment({}), windowsHide: true }
  )

  if (done.status !== 0) {
    throw new Error(`The conversations were not seeded (${String(done.status)}):\n${done.stdout}${done.stderr}`)
  }
}

/** The home's settings: the conversation's model, and the folder the file tools start from. */
export function writeConfig(home: string, chosen: { model: string; provider: string }, folder: string): void {
  writeFileSync(
    path.join(home, 'config.toml'),
    `[model]\nprovider = ${toml(chosen.provider)}\nname = ${toml(chosen.model)}\n\n` +
      `[workspace]\nfolder = ${toml(folder)}\n`
  )
}

/** Point the home's vLLM provider at the fake model server; `more` is more of models.toml. */
export function pointAtFakeModel(home: string, model: FakeModel, more = ''): void {
  writeFileSync(
    path.join(home, 'models.toml'),
    `[providers.vllm]\nbase_url = ${toml(`http://127.0.0.1:${String(model.port)}/v1`)}\n\n${more}`
  )
}

/** The real app on a home, connected. `root` holds its Electron profile; `extra` is more of the
 * app's environment, such as the address of a server it does not start; `lang` is the language the
 * window gets as the system's: English unless a test asks, so the texts the tests look for hold on
 * a machine set to another language. */
export async function openApp(
  root: string,
  home: string,
  extra: Record<string, string> = {},
  lang = 'en-US',
  chromium: string[] = []
): Promise<{ app: ElectronApplication; page: Page }> {
  const app = await _electron.launch({
    // On Linux CI Chromium's own sandbox needs privileges the runner lacks, so --no-sandbox is
    // passed there. The window keeps context isolation and no Node either way.
    args: [
      '.',
      '--atlas-e2e',
      `--lang=${lang}`,
      `--user-data-dir=${path.join(root, 'profile')}`,
      ...chromium,
      ...(process.platform === 'linux' ? ['--no-sandbox'] : [])
    ],
    cwd: DESKTOP,
    env: cleanEnvironment({ ATLAS_HOME: home, ...extra })
  })
  const page = await app.firstWindow()

  // What the window and the server said, printed when a test asks for it (ATLAS_E2E_DEBUG=1).
  if (process.env.ATLAS_E2E_DEBUG) {
    page.on('console', message => {
      process.stdout.write(`[window] ${message.text()}\n`)
    })
    page.on('pageerror', error => {
      process.stdout.write(`[window error] ${error.message}\n`)
    })
    app.process().stderr?.on('data', (chunk: Buffer) => {
      process.stdout.write(chunk.toString('utf8'))
    })
  }

  await expect(page.locator('[data-connection="open"]')).toBeVisible({ timeout: 90_000 })

  return { app, page }
}

/**
 * The test's environment without what the caller's own Atlas or keys would leak in: credentials and
 * Atlas's own runtime variables are stripped, since a spec run from an agent's terminal would
 * inherit its auto mode.
 */
export function cleanEnvironment(extra: Record<string, string>): Record<string, string> {
  const kept: Record<string, string> = {}

  for (const [name, value] of Object.entries(process.env)) {
    // ELECTRON_RUN_AS_NODE comes from an editor built on Electron (VS Code sets it for its own
    // children), and makes the app start as plain Node.
    if (
      value === undefined ||
      name.startsWith('ATLAS_') ||
      name === 'ELECTRON_RUN_AS_NODE' ||
      /_(API_KEY|TOKEN|SECRET)$/.test(name)
    ) {
      continue
    }

    kept[name] = value
  }

  return { ...kept, ...extra }
}

export function stopTree(child: ChildProcess): void {
  if (child.pid === undefined || child.exitCode !== null) {
    return
  }

  // uv starts the .venv's launcher, which starts Python: the whole tree goes, before the test moves on.
  if (process.platform === 'win32') {
    spawnSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { windowsHide: true })
  } else {
    process.kill(-child.pid, 'SIGTERM')
  }
}

/** Send a message from the box, to Atlas or to the bot whose conversation is on screen. */
/** The home on screen, its button at the foot of the sidebar; `data-home` is its name. */
export function homeButton(page: Page): Locator {
  return page.getByTestId('home-button')
}

/** Open the settings screen from the gear, on one of its sections (step 15b). */
export async function openSettings(page: Page, section: string): Promise<Locator> {
  const settings = page.getByTestId('settings')
  if (!(await settings.isVisible())) {
    await page.getByTestId('settings-open').click()
  }
  await settings.getByTestId(`settings-nav-${section}`).click()
  await expect(settings.getByTestId('settings-section')).toHaveAttribute('data-section', section)

  return settings
}

/** Open the home's menu: the homes, a new one, the home's dialog. */
export async function openHomeMenu(page: Page): Promise<Locator> {
  await homeButton(page).click()
  const menu = page.getByTestId('home-menu')
  await expect(menu).toBeVisible()

  return menu
}

/** Open another home from the home's menu. */
export async function switchHome(page: Page, name: string): Promise<void> {
  const menu = await openHomeMenu(page)
  await menu.locator(`[data-testid="home-option"][data-home="${name}"]`).click()
}

/** The homes the home's menu lists, the menu closed again. */
export async function listedHomes(page: Page): Promise<string[]> {
  const menu = await openHomeMenu(page)
  const names = await menu
    .getByTestId('home-option')
    .evaluateAll(rows => rows.map(row => row.getAttribute('data-home')))
  await page.keyboard.press('Escape')
  await expect(menu).toBeHidden()

  return names.map(name => name ?? '')
}

/** "New home", from the home's menu. */
export async function newHomeFromMenu(page: Page): Promise<void> {
  const menu = await openHomeMenu(page)
  await menu.getByTestId('home-new').click()
}

/** The home's own dialog (its picture, a backup, an import), opened from its menu. */
export async function editHome(page: Page): Promise<Locator> {
  const menu = await openHomeMenu(page)
  await menu.getByTestId('home-profile').click()
  const dialog = page.getByTestId('home-profile-dialog')
  await expect(dialog).toBeVisible()

  return dialog
}

/** Turn "Auto" off for the conversation on screen, on since 20/10/2026: what is flagged asks. */
export async function manual(page: Page): Promise<void> {
  const auto = page.getByRole('button', { name: 'Auto mode' })
  await expect(auto).toHaveAttribute('aria-pressed', 'true')
  await auto.click()
  await expect(auto).toHaveAttribute('aria-pressed', 'false')
}

export async function say(page: Page, text: string, to = 'Atlas'): Promise<void> {
  const box = page.getByRole('textbox', { name: `Message ${to}` })
  await box.fill(text)
  await box.press('Enter')
}

/** Whether a process is still running; `kill(pid, 0)` only asks, on Windows too (Node checks). */
export function running(pid: number): boolean {
  try {
    process.kill(pid, 0)

    return true
  } catch {
    return false
  }
}
