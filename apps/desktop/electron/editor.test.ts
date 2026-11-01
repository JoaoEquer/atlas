import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { openFolder, openInEditor, vscodeLink } from './editor'

const made: string[] = []

afterEach(() => {
  for (const folder of made.splice(0)) {
    rmSync(folder, { force: true, recursive: true })
  }
})

describe("VS Code's link", () => {
  it('keeps a Windows drive and escapes the names', () => {
    expect(vscodeLink('C:\\Users\\someone\\My Project #2')).toBe('vscode://file/C:/Users/someone/My%20Project%20%232')
  })

  it('takes a path that starts at the root', () => {
    expect(vscodeLink('/home/someone/projeto-ação')).toBe('vscode://file/home/someone/projeto-a%C3%A7%C3%A3o')
  })
})

describe('opening a folder in VS Code', () => {
  it('hands over the link of a folder of this machine', async () => {
    const folder = mkdtempSync(path.join(tmpdir(), 'atlas editor '))
    made.push(folder)
    const opened: string[] = []

    const error = await openInEditor(folder, link => {
      opened.push(link)

      return Promise.resolve()
    })

    expect(error).toBe('')
    expect(opened).toEqual([vscodeLink(folder)])
  })

  it('opens nothing for what is not a folder here', async () => {
    const folder = mkdtempSync(path.join(tmpdir(), 'atlas-editor-'))
    made.push(folder)
    const file = path.join(folder, 'notes.txt')
    writeFileSync(file, 'x')
    const opened: string[] = []
    const open = (link: string) => {
      opened.push(link)

      return Promise.resolve()
    }

    expect(await openInEditor(file, open)).toContain('is not a folder')
    expect(await openInEditor(path.join(folder, 'gone'), open)).toContain('is not on this machine')
    expect(await openInEditor('relative/folder', open)).toContain('Not a full path')
    expect(await openInEditor(42, open)).toContain('Not a full path')
    expect(opened).toEqual([])
  })

  it('says why when the system cannot open the link', async () => {
    const folder = mkdtempSync(path.join(tmpdir(), 'atlas-editor-'))
    made.push(folder)

    const error = await openInEditor(folder, () => Promise.reject(new Error('no program for vscode:')))

    expect(error).toBe('VS Code did not open: no program for vscode:')
  })
})

describe("a session's folder in the file manager", () => {
  it('opens a folder of this machine, and says why the system could not', async () => {
    const folder = mkdtempSync(path.join(tmpdir(), 'atlas-folder-'))
    made.push(folder)
    const opened: string[] = []

    expect(
      await openFolder(folder, found => {
        opened.push(found)

        return Promise.resolve('')
      })
    ).toBe('')
    expect(opened).toEqual([folder])
    expect(await openFolder(folder, () => Promise.resolve('No application'))).toBe(
      'The folder did not open: No application'
    )
    expect(await openFolder('relative/folder', () => Promise.resolve(''))).toContain('Not a full path')
  })
})
