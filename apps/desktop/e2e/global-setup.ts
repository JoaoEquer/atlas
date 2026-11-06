import { createRequire } from 'node:module'

/**
 * Electron's package fetches its binary the first time its path is asked for. Asked by several
 * workers at once, the fetches ran over each other and the first app of each worker failed to
 * start (`ETXTBSY` on Linux, "Process failed to launch" on Windows), so it is asked once, here,
 * before any worker.
 */
export default function globalSetup(): void {
  createRequire(import.meta.url)('electron')
}
