/**
 * One run of the installer: its lines read by their marks as they come, each written to the log,
 * and how it ended. It is stopped with its whole tree, as the app stops its server (the desktop's
 * electron/server.ts): `taskkill /T /F` on Windows, the process group elsewhere.
 */
import { execFileSync, spawn } from 'node:child_process'
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

import type { Ended } from './bridge'
import { classify, type Command, type Line, Lines } from './install'

export interface RunOptions {
  command: Command
  env: NodeJS.ProcessEnv
  /** The file every line is written to, started anew. */
  log: string
  onLine: (line: Line) => void
}

export interface Running {
  ended: Promise<Ended>
  stop(): void
}

export function runInstaller({ command, env, log, onLine }: RunOptions): Running {
  mkdirSync(path.dirname(log), { recursive: true })
  writeFileSync(log, `${command.command} ${command.args.join(' ')}\n`)
  const warnings: string[] = []
  let error = ''

  const take = (raw: string) => {
    appendFileSync(log, `${raw}\n`)
    const line = classify(raw)

    if (line.mark === 'warning') {
      warnings.push(line.text)
    } else if (line.mark === 'error') {
      error = line.text
    }

    onLine(line)
  }

  const child = spawn(command.command, command.args, {
    // Its own process group on POSIX, so a stop reaches what the installer started.
    detached: process.platform !== 'win32',
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true
  })

  const ended = new Promise<Ended>(resolve => {
    const out = new Lines()
    const errors = new Lines()
    child.stdout.setEncoding('utf8').on('data', (chunk: string) => {
      out.push(chunk).forEach(take)
    })
    child.stderr.setEncoding('utf8').on('data', (chunk: string) => {
      errors.push(chunk).forEach(take)
    })
    child.once('error', thrown => {
      const said = `The installer could not start (${command.command}): ${thrown.message}`
      appendFileSync(log, `${said}\n`)
      resolve({ code: -1, error: said, warnings })
    })
    child.once('close', (code, signal) => {
      out.end().forEach(take)
      errors.end().forEach(take)
      const result = code ?? -1
      appendFileSync(log, `The installer ended with code ${result}${signal ? ` (${signal})` : ''}.\n`)
      resolve({
        code: result,
        error: result === 0 ? '' : error || `The installer ended with code ${result}.`,
        warnings
      })
    })
  })

  return {
    ended,
    stop: () => {
      if (!child.pid || child.exitCode !== null) {
        return
      }

      try {
        if (process.platform === 'win32') {
          execFileSync('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore', windowsHide: true })
        } else {
          process.kill(-child.pid, 'SIGTERM')
        }
      } catch {
        // Already gone.
      }
    }
  }
}
