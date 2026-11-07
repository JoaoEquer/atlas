import { describe, expect, it } from 'vitest'

import { classify, installation, installerCommand, installerEnvironment, Lines } from './install'

describe('installation', () => {
  it('is where install.ps1 puts it on Windows', () => {
    const found = installation({ LOCALAPPDATA: 'C:\\Users\\Ana\\AppData\\Local' }, 'win32', 'C:\\Users\\Ana')

    expect(found).toEqual({
      atlas: 'C:\\Users\\Ana\\AppData\\Local\\atlas\\atlas\\.venv\\Scripts\\atlas.exe',
      checkout: 'C:\\Users\\Ana\\AppData\\Local\\atlas\\atlas',
      root: 'C:\\Users\\Ana\\AppData\\Local\\atlas'
    })
  })

  it('is where install.sh puts it elsewhere, XDG_DATA_HOME heeded', () => {
    expect(installation({}, 'linux', '/home/ana').checkout).toBe('/home/ana/.local/share/atlas/atlas')
    expect(installation({ XDG_DATA_HOME: '/data' }, 'darwin', '/Users/ana').atlas).toBe(
      '/data/atlas/atlas/.venv/bin/atlas'
    )
  })

  it('heeds ATLAS_INSTALL_DIR, its trailing slash dropped, and reads an empty one as unset', () => {
    expect(installation({ ATLAS_INSTALL_DIR: '/opt/atlas/' }, 'linux', '/home/ana').root).toBe('/opt/atlas')
    expect(installation({ ATLAS_INSTALL_DIR: 'D:\\atlas\\' }, 'win32', 'C:\\Users\\Ana').root).toBe('D:\\atlas')
    expect(installation({ ATLAS_INSTALL_DIR: '' }, 'linux', '/home/ana').root).toBe('/home/ana/.local/share/atlas')
  })
})

describe('installerCommand', () => {
  it('runs install.sh with bash', () => {
    expect(installerCommand('/tmp/x/install.sh', 'linux', {})).toEqual({
      args: ['/tmp/x/install.sh'],
      command: '/bin/bash'
    })
  })

  it('runs install.ps1 with Windows PowerShell by its full path, in UTF-8, owning the exit code', () => {
    const { args, command } = installerCommand("C:\\Temp\\O'Brien\\install.ps1", 'win32', { SystemRoot: 'C:\\WINDOWS' })

    expect(command).toBe('C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe')
    expect(args.slice(0, 5)).toEqual(['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command'])
    expect(args[5]).toBe(
      "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & 'C:\\Temp\\O''Brien\\install.ps1'; exit $LASTEXITCODE"
    )
  })
})

describe('installerEnvironment', () => {
  it("drops an AppImage's own libraries, keeping the user's", () => {
    const env = installerEnvironment({
      APPDIR: '/tmp/.mount_Atlas',
      LD_LIBRARY_PATH: '/tmp/.mount_Atlas/usr/lib:/opt/lib',
      LD_PRELOAD: '/tmp/.mount_Atlas/usr/lib/x.so',
      PATH: '/usr/bin'
    })

    expect(env.LD_LIBRARY_PATH).toBe('/opt/lib')
    expect('LD_PRELOAD' in env).toBe(false)
    expect(env.PATH).toBe('/usr/bin')
  })

  it('leaves everything outside an AppImage', () => {
    expect(installerEnvironment({ LD_LIBRARY_PATH: '/a' })).toEqual({ LD_LIBRARY_PATH: '/a' })
  })
})

describe('classify', () => {
  it("reads the installer's marks", () => {
    expect(classify('-> Installing uv 0.12.5')).toEqual({ mark: 'step', text: 'Installing uv 0.12.5' })
    expect(classify('[OK] Atlas is installed in /x')).toEqual({ mark: 'ok', text: 'Atlas is installed in /x' })
    expect(classify('[!] The app may not open until its sandbox is set')).toMatchObject({ mark: 'warning' })
    expect(classify('[X] Atlas is open (Atlas). Close it')).toEqual({
      mark: 'error',
      text: 'Atlas is open (Atlas). Close it'
    })
    expect(classify('Resolved 81 packages')).toEqual({ mark: 'text', text: 'Resolved 81 packages' })
  })

  it('drops colors and trailing spaces', () => {
    expect(classify('\u001b[36m-> Cloning\u001b[0m  ')).toEqual({ mark: 'step', text: 'Cloning' })
  })
})

describe('Lines', () => {
  it('gives whole lines across chunks, and the last one at the end', () => {
    const lines = new Lines()

    expect(lines.push('-> One\r\n-> Tw')).toEqual(['-> One'])
    expect(lines.push('o\nthree')).toEqual(['-> Two'])
    expect(lines.end()).toEqual(['three'])
    expect(lines.end()).toEqual([])
  })

  it('keeps what a redrawn line said last', () => {
    const lines = new Lines()

    expect(lines.push('Updating files:   0% (1/958)\rUpdating files: 100% (958/958), done.\r\n')).toEqual([
      'Updating files: 100% (958/958), done.'
    ])
    expect(lines.push('50%\r')).toEqual([])
    expect(lines.end()).toEqual(['50%'])
  })
})
