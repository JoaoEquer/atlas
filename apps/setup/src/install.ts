/**
 * What the Setup runs and reads (docs/ARCHITECTURE.md, The Setup): where the installer puts Atlas,
 * the command that runs the installer the Setup carries, and the marks of the installer's lines.
 * The folders are the installer's own rules (`Initialize-Paths` in install.ps1, `init_paths` in
 * install.sh), read the same way.
 */
import path from 'node:path'

export interface Installation {
  /** The environment's `atlas`, which `atlas desktop` opens the app with. */
  atlas: string
  checkout: string
  root: string
}

/** Where the installer puts Atlas, `ATLAS_INSTALL_DIR` heeded as the installer heeds it. */
export function installation(env: NodeJS.ProcessEnv, platform: NodeJS.Platform, home: string): Installation {
  if (platform === 'win32') {
    const local = env.LOCALAPPDATA || path.win32.join(home, 'AppData', 'Local')
    const root = (env.ATLAS_INSTALL_DIR || path.win32.join(local, 'atlas')).replace(/[\\/]+$/, '')
    const checkout = path.win32.join(root, 'atlas')

    return { atlas: path.win32.join(checkout, '.venv', 'Scripts', 'atlas.exe'), checkout, root }
  }

  // The shell's `${VAR:-default}`: an empty variable is an unset one.
  const data = env.XDG_DATA_HOME || path.posix.join(home, '.local', 'share')
  const root = (env.ATLAS_INSTALL_DIR || path.posix.join(data, 'atlas')).replace(/\/+$/, '')
  const checkout = path.posix.join(root, 'atlas')

  return { atlas: path.posix.join(checkout, '.venv', 'bin', 'atlas'), checkout, root }
}

/** The installer's file for this system, among the Setup's resources. */
export function installerFile(platform: NodeJS.Platform): string {
  return platform === 'win32' ? 'install.ps1' : 'install.sh'
}

export interface Command {
  args: string[]
  command: string
}

/**
 * The installer run as a file, so it owns its exit code. On Windows, Windows PowerShell by its full
 * path (a `powershell.exe` earlier on the PATH is not it) with the console's output set to UTF-8
 * first, so a user folder with an accent comes through; the path goes in single quotes, where only
 * a quote is special, and is doubled.
 */
export function installerCommand(script: string, platform: NodeJS.Platform, env: NodeJS.ProcessEnv): Command {
  if (platform !== 'win32') {
    return { args: [script], command: '/bin/bash' }
  }

  const windows = env.SystemRoot || env.SYSTEMROOT || 'C:\\Windows'
  const quoted = `'${script.replaceAll("'", "''")}'`

  return {
    args: [
      '-NoProfile',
      '-NonInteractive',
      '-ExecutionPolicy',
      'Bypass',
      '-Command',
      `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & ${quoted}; exit $LASTEXITCODE`
    ],
    command: path.win32.join(windows, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  }
}

/**
 * The environment the installer runs in: the Setup's own, less what an AppImage adds for its own
 * libraries, which would load into the system's `git` and `curl`.
 */
export function installerEnvironment(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const appDir = env.APPDIR
  const result = { ...env }

  if (!appDir) {
    return result
  }

  for (const name of ['LD_LIBRARY_PATH', 'LD_PRELOAD']) {
    const kept = (result[name] ?? '')
      .split(':')
      .filter(entry => entry && !entry.startsWith(appDir))
      .join(':')

    if (kept) {
      result[name] = kept
    } else {
      Reflect.deleteProperty(result, name)
    }
  }

  return result
}

export type Mark = 'error' | 'ok' | 'step' | 'text' | 'warning'

export interface Line {
  mark: Mark
  text: string
}

const MARKS: [string, Mark][] = [
  ['-> ', 'step'],
  ['[OK] ', 'ok'],
  ['[!] ', 'warning'],
  ['[X] ', 'error']
]

// eslint-disable-next-line no-control-regex
const COLOR = /\u001b\[[0-9;]*m/g

/** One of the installer's lines by its mark (`step()` and the others in both scripts). */
export function classify(raw: string): Line {
  const line = raw.replace(COLOR, '').trimEnd()

  for (const [prefix, mark] of MARKS) {
    if (line.startsWith(prefix)) {
      return { mark, text: line.slice(prefix.length) }
    }
  }

  return { mark: 'text', text: line }
}

/**
 * Whole lines out of the chunks a pipe gives, `\r\n` or `\n`, the last one kept until it ends. A
 * line a program redraws with `\r` (Git's "Updating files: 40%") is what it said last.
 */
export class Lines {
  private rest = ''

  push(chunk: string): string[] {
    const parts = (this.rest + chunk).split(/\r?\n/)
    this.rest = parts.pop() ?? ''

    return parts.map(redrawn)
  }

  end(): string[] {
    const last = redrawn(this.rest)
    this.rest = ''

    return last ? [last] : []
  }
}

function redrawn(line: string): string {
  return line.split('\r').findLast(part => part.trim() !== '') ?? ''
}
