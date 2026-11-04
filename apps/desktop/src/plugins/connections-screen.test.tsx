/**
 * The GitHub and Google Workspace plugins' screens (step 26), the files Atlas ships, as the app
 * loads them: their parts of the first run's Connections, with the plugins' calls faked, so what
 * they ask for is checked without `gh` or `gws`.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $connection } from '@/store/app'

// @ts-expect-error -- the plugins' screens are plain JavaScript, loaded uncompiled by the app.
import github from '../../../../plugins/github/desktop/plugin.js'
// @ts-expect-error -- the same.
import google from '../../../../plugins/google/desktop/plugin.js'

import type { Place } from './registry'
import { host } from './sdk'

interface Registered {
  render: (place: Place) => ReactNode
}

/** The screen's part, registered with a `ctx` whose calls answer as `answer` says. */
function part(screenModule: unknown, answer: (name: string) => unknown) {
  const calls: string[] = []
  let found: Registered | undefined
  ;(screenModule as { register: (ctx: unknown) => void }).register({
    call: (name: string) => {
      calls.push(name)

      return Promise.resolve(answer(name))
    },
    register: (registered: Registered) => {
      found = registered
    }
  })

  return { calls, part: found as Registered }
}

beforeEach(() => {
  $connection.set({ error: null, state: 'open' })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('the GitHub plugin', () => {
  it("connects through gh's code in the browser, and says who once the browser said yes", async () => {
    let logged = false
    const opened = vi.spyOn(window, 'open').mockReturnValue(null)
    const { calls, part: github_ } = part(github, name =>
      name === 'log_in'
        ? { code: 'AB12-CD34', url: 'https://github.com/login/device' }
        : { account: logged ? 'example-user' : '', installed: true, logged_in: logged, waiting: !logged }
    )
    render(<>{github_.render({ away: () => undefined })}</>)

    fireEvent.click(await screen.findByTestId('first-run-github-connect'))
    expect((await screen.findByTestId('first-run-github-code')).textContent).toBe('AB12-CD34')
    expect(opened).toHaveBeenCalledWith('https://github.com/login/device')
    // The browser said yes: the screen reads the status again while the login waits.
    logged = true
    const connected = await screen.findByTestId('first-run-github-connected', undefined, { timeout: 6000 })
    expect(connected.textContent).toBe('✓ Connected as example-user')
    expect(calls.filter(name => name === 'status').length).toBeGreaterThan(1)
  })

  it('says gh is missing, and offers no Connect without it', async () => {
    const { part: github_ } = part(github, () => ({ account: '', installed: false, logged_in: false }))
    render(<>{github_.render({ away: () => undefined })}</>)

    expect(await screen.findByTestId('first-run-github-missing')).toBeTruthy()
    expect(screen.queryByTestId('first-run-github-connect')).toBeNull()
  })
})

describe('the Google Workspace plugin', () => {
  it('sets gws up in a conversation with Atlas, the first run a band away', async () => {
    const away = vi.fn()
    const sent = vi.spyOn(host, 'send').mockResolvedValue()
    const { part: google_ } = part(google, () => ({ account: '', installed: true, logged_in: false }))
    render(<>{google_.render({ away })}</>)

    fireEvent.click(await screen.findByTestId('first-run-google-set-up'))

    expect(away).toHaveBeenCalled()
    expect(sent.mock.calls[0]?.[0]).toContain('gws auth status')
  })

  it('says who it is logged in as', async () => {
    const { part: google_ } = part(google, () => ({ account: 'me@example.com', installed: true, logged_in: true }))
    render(<>{google_.render({ away: () => undefined })}</>)

    expect((await screen.findByTestId('first-run-google-connected')).textContent).toBe('✓ Connected as me@example.com')
  })
})
