// The installers, copied from scripts/ into the build as they are, so each deploy of main serves
// main's. The Worker serves them as text and counts each download (worker/installs.ts).
import { readFileSync } from 'node:fs'

import { SCRIPTS } from '../../site.config.mjs'

export function installerScript(name: 'install.ps1' | 'install.sh'): Response {
  return new Response(readFileSync(new URL(name, SCRIPTS)), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' }
  })
}
