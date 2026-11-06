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
  area: string
  render: (place: Place) => ReactNode
}

/** The screen's part in `area` (its connection unless named), registered with a `ctx` whose calls
 *  answer as `answer` says, with what they were given. */
function part(screenModule: unknown, answer: (name: string, params: unknown) => unknown, area = 'connection') {
  const calls: string[] = []
  const given: unknown[] = []
  const found: Registered[] = []
  ;(screenModule as { register: (ctx: unknown) => void }).register({
    call: (name: string, params: unknown) => {
      calls.push(name)
      given.push(params)

      return Promise.resolve(answer(name, params))
    },
    register: (registered: Registered) => {
      found.push(registered)
    }
  })

  return { calls, given, part: found.find(item => item.area === area) as Registered }
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

  it('with no gh on the computer, Connect says it fetches gh first (05/11/2026)', async () => {
    const { part: github_ } = part(github, () => ({ account: '', installed: false, logged_in: false }))
    render(<>{github_.render({ away: () => undefined })}</>)

    expect((await screen.findByTestId('first-run-github-missing')).textContent).toContain('gh')
    expect(screen.getByTestId('first-run-github-connect')).toBeTruthy()
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

describe("the GitHub plugin's part of a workspace (step 21)", () => {
  const WORKSPACE = { base: 'main', branch: 'atlas/cart', folder: '/w/cart', id: 'w-cart', root: '/work/shop' }

  it('opens a pull request after Atlas sent the branch, then shows it with its checks', async () => {
    const pushed = vi.spyOn(host, 'request').mockResolvedValue({ said: '' })
    const made = {
      checks: [
        { name: 'test', state: 'success' },
        { name: 'lint', state: 'in_progress' }
      ],
      draft: false,
      exists: true,
      number: 7,
      state: 'open',
      title: 'Add the cart',
      url: 'https://github.com/example/shop/pull/7'
    }
    const { given, part: pr } = part(github, name => (name === 'pull_request' ? { exists: false } : made), 'workspace')
    render(<>{pr.render(WORKSPACE)}</>)

    fireEvent.click(await screen.findByTestId('github-open-pr'))
    fireEvent.change(screen.getByTestId('github-pr-title'), { target: { value: 'Add the cart' } })
    fireEvent.click(screen.getByTestId('github-pr-open'))

    expect((await screen.findByTestId('github-pr-link')).textContent).toBe('#7 Add the cart · open')
    expect(screen.getAllByTestId('github-check').map(check => check.textContent)).toEqual(['✓test', '…lint'])
    expect(pushed).toHaveBeenCalledWith('workspace.push', { id: 'w-cart' })
    expect(given.at(-1)).toEqual({
      base: 'main',
      branch: 'atlas/cart',
      draft: false,
      folder: '/w/cart',
      title: 'Add the cart'
    })
  })

  it("starts a workspace from one of the project's issues, the issue its first message", async () => {
    const start = vi.fn().mockResolvedValue(undefined)
    const issue = {
      body: '1.005 shows 1.00.',
      number: 12,
      title: 'The cart rounds wrong',
      url: 'https://github.com/example/shop/issues/12'
    }
    const { part: issues } = part(github, () => [issue], 'project')
    render(<>{issues.render({ root: '/work/shop', start })}</>)

    fireEvent.click(screen.getByTestId('github-issues-toggle'))
    expect((await screen.findByTestId('github-issue')).textContent).toContain('#12 The cart rounds wrong')
    fireEvent.click(screen.getByTestId('github-issue-start'))

    expect(start).toHaveBeenCalledWith(
      'issue-12',
      'Resolve issue #12: The cart rounds wrong\n\n1.005 shows 1.00.\n\nhttps://github.com/example/shop/issues/12'
    )
  })
})
