import { defineConfig } from '@playwright/test'

/**
 * End to end: the real app (Electron), the real `atlas serve`, and the fake model server. Each
 * test owns its app, its home, its Electron profile and its fake model on a port of its own, so
 * the files run side by side. What holds a key of the whole system (the dictation's global
 * shortcut, which one app at a time may register) runs after them, alone.
 */
const ALONE = ['**/voice.spec.ts']

export default defineConfig({
  // A slower machine, as the CI's Windows is, takes longer than Playwright's 5 s for a screen to
  // settle; a wait that times out says no more at 10 s than at 5.
  expect: { timeout: 10_000 },
  forbidOnly: Boolean(process.env.CI),
  fullyParallel: false,
  projects: [
    { name: 'app', testIgnore: ALONE },
    { name: 'alone', testMatch: ALONE, dependencies: ['app'] }
  ],
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  retries: 0,
  testDir: './e2e',
  timeout: 120_000,
  use: { screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  // Each worker runs an app, a server and a fake model: the CI's machines have four cores.
  workers: process.env.CI ? 2 : 4
})
