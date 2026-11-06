/**
 * An executor session's folder opened in VS Code (asked for by the user on 18/10/2026), to read the
 * project's code beside the session. By VS Code's own link, `vscode://file/{full path}`, which its
 * docs give for a file or a project ("Opening VS Code with URLs") and its installer registers on
 * Windows, macOS and Linux: the system hands the link to VS Code, so no shell runs and no `code`
 * needs to be on the PATH. An open folder's window comes forward, as `code <folder>` does.
 */
import { statSync } from 'node:fs'
import path from 'node:path'

/** VS Code's link to a folder: forward slashes, each name escaped, a drive's colon kept. */
export function vscodeLink(folder: string): string {
  const names = folder
    .replaceAll('\\', '/')
    .split('/')
    .map(name => (/^[A-Za-z]:$/.test(name) ? name : encodeURIComponent(name)))
  const joined = names.join('/')

  return `vscode://file${joined.startsWith('/') ? '' : '/'}${joined}`
}

/**
 * Open `folder` in VS Code with `open` (Electron's `shell.openExternal`): `''` once handed over,
 * else why not. Only a folder of this machine, given in full, goes.
 */
export async function openInEditor(folder: unknown, open: (link: string) => Promise<void>): Promise<string> {
  const refused = notAFolder(folder)

  if (refused !== null || typeof folder !== 'string') {
    return refused ?? ''
  }

  try {
    await open(vscodeLink(folder))
  } catch (error) {
    return `VS Code did not open: ${error instanceof Error ? error.message : String(error)}`
  }

  return ''
}

/**
 * Open `folder` in the system's file manager with `open` (Electron's `shell.openPath`, which says
 * why not, or nothing): the title bar's "Open the folder" (step 18g). The same folders as VS Code.
 */
export async function openFolder(folder: unknown, open: (folder: string) => Promise<string>): Promise<string> {
  const refused = notAFolder(folder)

  if (refused !== null || typeof folder !== 'string') {
    return refused ?? ''
  }

  const failed = await open(folder)

  return failed ? `The folder did not open: ${failed}` : ''
}

/** Why `folder` is not a folder of this machine given in full; `null` when it is. */
function notAFolder(folder: unknown): null | string {
  if (typeof folder !== 'string' || !path.isAbsolute(folder)) {
    return 'Not a full path to a folder.'
  }

  try {
    return statSync(folder).isDirectory() ? null : `${folder} is not a folder.`
  } catch {
    return `${folder} is not on this machine.`
  }
}
