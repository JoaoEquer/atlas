// Bundle the main process and the preload (CommonJS: a sandboxed preload cannot be a module) and
// the window's script into out/, beside its page and the app's icon. Type checking is tsc's job.
import { copyFileSync, mkdirSync } from 'node:fs'

import { build } from 'esbuild'

const node = {
  bundle: true,
  external: ['electron'],
  format: 'cjs',
  logLevel: 'warning',
  platform: 'node',
  target: 'node22'
}

mkdirSync('out', { recursive: true })
await build({ ...node, entryPoints: ['src/main.ts'], outfile: 'out/main.cjs' })
await build({ ...node, entryPoints: ['src/preload.ts'], outfile: 'out/preload.cjs' })
await build({
  bundle: true,
  entryPoints: ['src/page.ts'],
  format: 'iife',
  logLevel: 'warning',
  outfile: 'out/page.js',
  platform: 'browser',
  target: 'chrome140'
})
copyFileSync('src/index.html', 'out/index.html')
copyFileSync('src/page.css', 'out/page.css')
copyFileSync('../desktop/resources/icon.svg', 'out/icon.svg')
