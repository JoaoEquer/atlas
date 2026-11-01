// The run against a real script, through the same command the Setup builds: PowerShell on Windows,
// bash elsewhere.
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { describe, expect, it } from 'vitest'

import type { Line } from './install'
import { installerCommand } from './install'
import { runInstaller } from './run'

const WINDOWS = process.platform === 'win32'

/** A stand-in for the installer, written in its marks, that ends with `code`. */
function standIn(folder: string, code: number): string {
  if (WINDOWS) {
    const file = path.join(folder, 'install.ps1')
    // With a BOM, so Windows PowerShell reads the accent as UTF-8; the real installer is ASCII, and
    // its accents come from the paths it is given.
    writeFileSync(
      file,
      '﻿' +
        [
          'Write-Host "-> Installing uv"',
          'Write-Host "[OK] uv"',
          'Write-Host "[!] The sandbox needs root"',
          'Write-Host "Atlas está em C:\\Usuários\\Ana"',
          `if (${code}) { Write-Host "[X] Atlas is open. Close it"; exit ${code} }`,
          '$global:LASTEXITCODE = 0'
        ].join('\r\n')
    )

    return file
  }

  const file = path.join(folder, 'install.sh')
  writeFileSync(
    file,
    [
      "printf '%s\\n' '-> Installing uv' '[OK] uv'",
      "printf '%s\\n' '[!] The sandbox needs root' >&2",
      "printf '%s\\n' 'Atlas está em /home/Ana'",
      `if [ ${code} != 0 ]; then printf '%s\\n' '[X] Atlas is open. Close it' >&2; exit ${code}; fi`
    ].join('\n')
  )

  return file
}

describe('runInstaller', () => {
  it('reads each line by its mark, keeps the warnings, and logs it all', async () => {
    const folder = mkdtempSync(path.join(os.tmpdir(), 'atlas-setup-'))
    const log = path.join(folder, 'logs', 'setup.log')
    const lines: Line[] = []

    const ended = await runInstaller({
      command: installerCommand(standIn(folder, 0), process.platform, process.env),
      env: process.env,
      log,
      onLine: line => lines.push(line)
    }).ended

    expect(ended).toEqual({ code: 0, error: '', warnings: ['The sandbox needs root'] })
    expect(lines).toContainEqual({ mark: 'step', text: 'Installing uv' })
    expect(lines).toContainEqual({ mark: 'text', text: expect.stringContaining('Atlas está em') as string })
    expect(readFileSync(log, 'utf8')).toContain('The installer ended with code 0.')
  }, 60_000)

  it("ends with the installer's code and its last error", async () => {
    const folder = mkdtempSync(path.join(os.tmpdir(), 'atlas-setup-'))

    const ended = await runInstaller({
      command: installerCommand(standIn(folder, 3), process.platform, process.env),
      env: process.env,
      log: path.join(folder, 'setup.log'),
      onLine: () => undefined
    }).ended

    expect(ended.code).toBe(3)
    expect(ended.error).toBe('Atlas is open. Close it')
  }, 60_000)

  it('says when the installer cannot start', async () => {
    const folder = mkdtempSync(path.join(os.tmpdir(), 'atlas-setup-'))

    const ended = await runInstaller({
      command: { args: [], command: path.join(folder, 'missing') },
      env: process.env,
      log: path.join(folder, 'setup.log'),
      onLine: () => undefined
    }).ended

    expect(ended.code).toBe(-1)
    expect(ended.error).toContain('The installer could not start')
  })
})
