import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AtlasClient } from '@/protocol/client'
import type { ExecutorRow, InstanceRow, SessionFolderRow, SessionRow } from '@/protocol/protocol.generated'
import { current, openHome, showExecutor, startApp } from '@/store/app'
import { $executorOrder } from '@/store/order'

import { ExecutorsList } from './executors-list'

const EXECUTOR: ExecutorRow = {
  account: 'someone@example.com',
  account_folders: false,
  files: false,
  account_id: '',
  accounts: true,
  base: '',
  consent: '',
  consent_again: false,
  declaration: [],
  enabled: true,
  installed: true,
  logged_in: true,
  minimum: '',
  name: 'claude-code',
  nickname: '',
  plan: '',
  plugin: 'claude-code',
  problem: '',
  supported: true,
  title: 'Claude Code',
  version: '2.1.140'
}

const folder = (path: string): SessionFolderRow => ({
  account_id: '',
  executor: 'claude-code',
  folder: path,
  updated_at: ''
})

const session = (id: string, path: string, title: string): SessionRow => ({
  by_atlas: false,
  elsewhere: '',
  executor: 'claude-code',
  folder: path,
  instance_id: '',
  session_id: id,
  started_at: '',
  state: '',
  title,
  updated_at: '',
  waiting: 0
})

/** A promise the test settles when it chooses. */
function later<T>(): { promise: Promise<T>; reject: (error: Error) => void; resolve: (value: T) => void } {
  let resolve: (value: T) => void = () => undefined
  let reject: (error: Error) => void = () => undefined
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })

  return { promise, reject, resolve }
}

/** A client that never connects, whose calls `answer` settles, each kept in `calls`. */
function listingClient(answer: (method: string, params: unknown) => Promise<unknown>, calls: [string, unknown][]) {
  const none = () => () => undefined

  return {
    call: (method: string, params: unknown) => {
      calls.push([method, params])

      return answer(method, params)
    },
    onError: none,
    onEvent: none,
    onRequest: none,
    onResync: none,
    onState: none,
    start: vi.fn(),
    stop: vi.fn(),
    watch: vi.fn()
  } as unknown as AtlasClient
}

let stop: () => void = () => undefined

afterEach(() => {
  cleanup()
  stop()
})

/** The app on a client answering `answer`, the executors tab on Claude Code's list. */
async function showClaudeCode(
  answer: (method: string, params: unknown) => Promise<unknown>,
  instances: InstanceRow[] = []
): Promise<[string, unknown][]> {
  const calls: [string, unknown][] = []
  stop = startApp({
    client: () => listingClient(answer, calls),
    shell: { focus: () => undefined, notify: () => undefined }
  })
  await openHome('work')
  current().$workbench.set({ executors: [EXECUTOR], instances })
  render(<ExecutorsList />)
  act(() => {
    showExecutor('claude-code')
  })

  return calls
}

const listed = (calls: [string, unknown][], method: string) => calls.filter(([called]) => called === method)

/** Opens a row's three-dot menu, as the keyboard does (jsdom has no pointer for Radix's), and
 * returns it. */
function openMenu(node: HTMLElement = screen.getByTestId('executor-node')): HTMLElement {
  fireEvent.keyDown(within(node).getByTestId('executor-menu'), { key: 'Enter' })

  return screen.getByTestId('executor-menu-content')
}

/** Turns on a row's archived folders and sessions in its menu, which stays open for another
 * choice, then closes it. */
function showArchived(node?: HTMLElement): void {
  fireEvent.click(within(openMenu(node)).getByTestId('executor-show-archived'))
  fireEvent.keyDown(screen.getByTestId('executor-menu-content'), { key: 'Escape' })
}

describe('the executors tab lists folders first, and a folder its sessions once opened', () => {
  it('reads no session until a folder opens, each folder once, and keeps them while it is closed', async () => {
    const folders = later<unknown>()
    const shop = later<unknown>()
    const calls = await showClaudeCode(method =>
      method === 'executor.folders'
        ? folders.promise
        : method === 'executor.sessions'
          ? shop.promise
          : new Promise(() => undefined)
    )

    // The folders are being read; asked again meanwhile, the same read answers.
    expect(screen.getByTestId('folders-reading').textContent).toContain('Reading its folders…')
    act(() => {
      showExecutor('claude-code')
    })
    expect(listed(calls, 'executor.folders')).toHaveLength(1)
    await act(async () => {
      folders.resolve({ folders: [folder('C:\\work\\shop'), folder('C:\\work\\api')] })
      await folders.promise
    })

    const rows = screen.getAllByTestId('session-folder')
    expect(rows.map(row => row.getAttribute('data-folder'))).toEqual(['C:\\work\\shop', 'C:\\work\\api'])
    expect(rows.map(row => row.getAttribute('data-open'))).toEqual(['false', 'false'])
    expect(screen.queryByTestId('session-row')).toBeNull()
    expect(listed(calls, 'executor.sessions')).toEqual([])

    // Opened: only its own sessions are read, once, however often it is asked meanwhile.
    const toggle = screen.getByTitle('C:\\work\\shop')
    fireEvent.click(toggle)
    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('sessions-reading').textContent).toContain('Reading its sessions…')
    act(() => {
      showExecutor('claude-code')
    })
    expect(listed(calls, 'executor.sessions')).toEqual([
      ['executor.sessions', { executor: 'claude-code', folder: 'C:\\work\\shop' }]
    ])
    await act(async () => {
      shop.resolve({ sessions: [session('s-1', 'C:\\work\\shop', 'Show cents in the cart total')] })
      await shop.promise
    })
    expect(screen.getByTestId('session-row').textContent).toContain('Show cents in the cart total')
    expect(screen.queryByTestId('sessions-reading')).toBeNull()

    // Closed and opened again, it shows what it read with no new read.
    fireEvent.click(toggle)
    expect(screen.queryByTestId('session-row')).toBeNull()
    fireEvent.click(toggle)
    expect(screen.getByTestId('session-row').textContent).toContain('Show cents in the cart total')
    expect(listed(calls, 'executor.sessions')).toHaveLength(1)
  })

  it('says why a folder could not be read, reads it again on asking, and says when it has none', async () => {
    let failing = true
    const calls = await showClaudeCode(method => {
      if (method === 'executor.folders') {
        return Promise.resolve({ folders: [folder('C:\\work\\shop')] })
      }

      if (method === 'executor.sessions') {
        return failing
          ? Promise.reject(new Error('Claude Code could not list its sessions: OSError'))
          : Promise.resolve({ sessions: [] })
      }

      return new Promise(() => undefined)
    })

    fireEvent.click(await screen.findByTestId('session-folder-toggle'))
    expect((await screen.findByRole('alert')).textContent).toContain(
      'They could not be read: Claude Code could not list its sessions: OSError'
    )

    failing = false
    fireEvent.click(screen.getByRole('button', { name: /Read again/ }))
    expect(await screen.findByText('No sessions in this folder.')).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    expect(listed(calls, 'executor.sessions')).toHaveLength(2)
  })

  it('says the folders could not be read, and an executor with none says it has no sessions', async () => {
    let failing = true
    await showClaudeCode(method => {
      if (method === 'executor.folders') {
        return failing
          ? Promise.reject(new Error('Claude Code is not installed on this computer.'))
          : Promise.resolve({ folders: [] })
      }

      return new Promise(() => undefined)
    })

    expect((await screen.findByRole('alert')).textContent).toContain('Claude Code is not installed on this computer.')
    failing = false
    fireEvent.click(screen.getByRole('button', { name: /Read again/ }))
    expect(await screen.findByText('No sessions yet.')).toBeTruthy()
  })

  it('a closed folder says one of its sessions waits for the user, with none of them read', async () => {
    const waiting = {
      executor: 'claude-code',
      folder: 'C:\\work\\shop',
      instance_id: 'in-1',
      state: 'idle',
      waiting: 1
    }
    const calls = await showClaudeCode(
      method =>
        method === 'executor.folders'
          ? Promise.resolve({ folders: [folder('C:\\work\\shop'), folder('C:\\work\\api')] })
          : new Promise(() => undefined),
      [waiting as InstanceRow]
    )

    const shop = await screen.findByTitle('C:\\work\\shop')
    expect(within(shop).getByRole('img', { name: 'waiting for you' }).dataset.dot).toBe('waiting')
    expect(within(screen.getByTitle('C:\\work\\api')).queryByTestId('status-dot')).toBeNull()
    expect(listed(calls, 'executor.sessions')).toEqual([])
  })

  it('a closed account carries its most urgent dot on its picture; opened, its rows carry theirs', async () => {
    const instances = [
      { executor: 'claude-code', folder: 'C:\\work\\shop', instance_id: 'in-1', state: 'working', waiting: 0 },
      { executor: 'claude-code', folder: 'C:\\work\\api', instance_id: 'in-2', state: 'failed', waiting: 0 }
    ] as InstanceRow[]
    await showClaudeCode(
      method =>
        method === 'executor.folders'
          ? Promise.resolve({ folders: [folder('C:\\work\\shop'), folder('C:\\work\\api')] })
          : new Promise(() => undefined),
      instances
    )
    await screen.findAllByTestId('session-folder')

    expect(within(screen.getByTitle('C:\\work\\shop')).getByTestId('status-dot').dataset.dot).toBe('working')
    expect(within(screen.getByTitle('C:\\work\\api')).getByTestId('status-dot').dataset.dot).toBe('failed')
    const card = screen.getByTestId('executor-card')
    expect(within(card).queryByTestId('status-dot')).toBeNull()

    fireEvent.click(card)
    expect(within(card).getByRole('img', { name: 'stopped with an error' })).toBeTruthy()
  })

  it('opens under its own row; an archived folder leaves the list for Archived, and comes back', async () => {
    localStorage.clear()
    await showClaudeCode(method =>
      method === 'executor.folders'
        ? Promise.resolve({ folders: [folder('C:\\work\\shop'), folder('C:\\work\\api')] })
        : new Promise(() => undefined)
    )
    const shown = () => screen.getAllByTestId('session-folder').map(row => row.getAttribute('data-folder'))

    expect(within(screen.getByTestId('executor-node')).getByTestId('executor-tree')).toBeTruthy()
    await screen.findAllByTestId('session-folder')
    fireEvent.click(within(screen.getAllByTestId('session-folder')[0] as HTMLElement).getByTestId('archive-folder'))
    expect(shown()).toEqual(['C:\\work\\api'])
    // Archived ones are not listed at all until the row's menu says so (21/10/2026).
    expect(screen.queryByTestId('archived-folders')).toBeNull()
    showArchived()
    expect(screen.getByTestId('archived-toggle').textContent).toContain('Archived folders (1)')
    expect(screen.queryByTestId('archived-folder')).toBeNull()

    fireEvent.click(screen.getByTestId('archived-toggle'))
    fireEvent.click(screen.getByTestId('unarchive-folder'))
    expect(shown()).toEqual(['C:\\work\\shop', 'C:\\work\\api'])
    expect(screen.queryByTestId('archived-folders')).toBeNull()

    // Its row closes it again.
    fireEvent.click(screen.getByTestId('executor-card'))
    expect(screen.queryByTestId('executor-tree')).toBeNull()
  })

  it("switches account from its row's menu, and the menu says the new one", async () => {
    const calls = await showClaudeCode(method =>
      method === 'executor.account'
        ? Promise.resolve({ ...EXECUTOR, account: 'other@example.com' })
        : method === 'executor.folders'
          ? Promise.resolve({ folders: [] })
          : new Promise(() => undefined)
    )

    // The row says its version alone; who it is logged in as is in its menu.
    expect(screen.getByTestId('executor-card').textContent).toBe('Claude Code2.1.140')
    const menu = openMenu()
    expect(within(menu).getByTestId('executor-menu-account').textContent).toBe('someone@example.com')
    fireEvent.click(within(menu).getByTestId('executor-switch-account'))
    const login = screen.getByRole('textbox', { name: 'Its email (optional)' })
    fireEvent.change(login, { target: { value: 'other@example.com' } })
    fireEvent.submit(login.closest('form') as HTMLFormElement)

    await vi.waitFor(() => {
      expect(screen.queryByTestId('executor-account')).toBeNull()
    })
    expect(within(openMenu()).getByTestId('executor-menu-account').textContent).toBe('other@example.com')
    expect(listed(calls, 'executor.account')).toEqual([
      ['executor.account', { executor: 'claude-code', login: 'other@example.com' }]
    ])
  })
})

describe("each harness's mark (asked for by the user on 21/10/2026)", () => {
  it("gives Codex a mark of its own, not Claude Code's", async () => {
    await showClaudeCode(() => new Promise(() => undefined))
    act(() => {
      current().$workbench.set({
        executors: [EXECUTOR, { ...EXECUTOR, name: 'codex', plugin: 'codex', title: 'Codex' }],
        instances: []
      })
    })

    const [claude, codex] = screen.getAllByTestId('executor-avatar') as [HTMLElement, HTMLElement]
    expect([claude.dataset.plugin, codex.dataset.plugin].sort()).toEqual(['claude-code', 'codex'])
    expect(claude.className).not.toBe(codex.className)
  })

  it('gives the Gemini CLI one too (step 16p)', async () => {
    await showClaudeCode(() => new Promise(() => undefined))
    act(() => {
      current().$workbench.set({
        executors: [EXECUTOR, { ...EXECUTOR, name: 'gemini-cli', plugin: 'gemini-cli', title: 'Gemini CLI' }],
        instances: []
      })
    })

    const marks = screen.getAllByTestId('executor-avatar')
    const gemini = marks.find(mark => mark.dataset.plugin === 'gemini-cli')
    expect(gemini?.className).toContain('bg-[#1a73e8]')
  })

  it('gives DeepSeek one too (step 16q)', async () => {
    await showClaudeCode(() => new Promise(() => undefined))
    act(() => {
      current().$workbench.set({
        executors: [EXECUTOR, { ...EXECUTOR, name: 'deepseek', plugin: 'deepseek', title: 'DeepSeek' }],
        instances: []
      })
    })

    const marks = screen.getAllByTestId('executor-avatar')
    const deepseek = marks.find(mark => mark.dataset.plugin === 'deepseek')
    expect(deepseek?.className).toContain('bg-[#4d6bfe]')
  })
})

describe("an executor's other accounts (step 16h, redone on 19/10/2026)", () => {
  const WORK: ExecutorRow = {
    ...EXECUTOR,
    account: 'w@example.com',
    account_id: 'ab12cd34',
    base: 'claude-code',
    name: 'claude-code@ab12cd34',
    nickname: 'work',
    title: 'Claude Code · work'
  }
  const ACCOUNTS = {
    accounts: [
      {
        account_id: '',
        executor: 'claude-code',
        folder: 'C:\\Users\\me\\.claude',
        logged_in: true,
        login: 'someone@example.com',
        name: 'claude-code',
        nickname: ''
      },
      {
        account_id: 'ab12cd34',
        executor: 'claude-code',
        folder: 'C:\\Users\\me\\.claude-work',
        logged_in: true,
        login: 'w@example.com',
        name: 'claude-code@ab12cd34',
        nickname: 'work'
      }
    ],
    can_add: true,
    compact: false,
    executor: 'claude-code',
    failover: true
  }
  const answer = (method: string): Promise<unknown> =>
    method === 'executor.accounts'
      ? Promise.resolve(ACCOUNTS)
      : method === 'executor.folders'
        ? Promise.resolve({ folders: [{ ...folder('C:\\work\\api'), account_id: 'ab12cd34' }] })
        : method === 'executor.sessions'
          ? Promise.resolve({ sessions: [session('s-1', 'C:\\work\\api', 'Fix the API')] })
          : method === 'workbench.list'
            ? Promise.resolve({
                executors: [{ ...EXECUTOR, account_folders: true }, WORK],
                instances: [],
                requests: []
              })
            : method === 'executor.failover'
              ? Promise.resolve({
                  ...ACCOUNTS,
                  accounts: [ACCOUNTS.accounts[1], ACCOUNTS.accounts[0]],
                  compact: true
                })
              : method === 'executor.add_account'
                ? Promise.resolve({ ...ACCOUNTS.accounts[1], account_id: 'ef56ab78', nickname: 'side' })
                : ['executor.folder_account', 'executor.remove_account', 'executor.delete_session'].includes(method)
                  ? Promise.resolve({})
                  : new Promise(() => undefined)

  async function showAccounts(name = 'claude-code'): Promise<[string, unknown][]> {
    const calls: [string, unknown][] = []
    stop = startApp({
      client: () => listingClient(answer, calls),
      shell: { focus: () => undefined, notify: () => undefined }
    })
    await openHome('work')
    current().$workbench.set({ executors: [{ ...EXECUTOR, account_folders: true }, WORK], instances: [] })
    render(<ExecutorsList />)
    act(() => {
      showExecutor(name)
    })
    await screen.findAllByTestId('session-folder')

    return calls
  }

  it('shows each account as an executor of its own, and a folder starts on the one it names', async () => {
    const calls = await showAccounts()

    // The harness and its version head its accounts once; each row says only its account.
    expect(screen.getByTestId('harness-heading').textContent).toBe('Claude Code2.1.140')
    const rows = screen.getAllByTestId('executor-card')
    expect(rows.map(row => row.textContent)).toEqual(['Main account', 'work'])
    // Claude Code's mark, not Codex's.
    expect(rows.map(row => within(row).getByTestId('executor-avatar').dataset.plugin)).toEqual([
      'claude-code',
      'claude-code'
    ])
    const chosen = await screen.findByTestId<HTMLSelectElement>('folder-account')
    expect(chosen.value).toBe('ab12cd34')
    fireEvent.change(chosen, { target: { value: '' } })

    await vi.waitFor(() => {
      expect(listed(calls, 'executor.folder_account')).toEqual([
        ['executor.folder_account', { account_id: '', executor: 'claude-code', folder: 'C:\\work\\api' }]
      ])
    })
  })

  it('opens another account without closing the first (asked for by the user on 20/10/2026)', async () => {
    await showAccounts()

    const [main, work] = screen.getAllByTestId('executor-card') as [HTMLElement, HTMLElement]
    fireEvent.click(work)
    expect(screen.getAllByTestId('executor-tree')).toHaveLength(2)
    expect(main.getAttribute('aria-expanded')).toBe('true')

    fireEvent.click(main)
    expect(screen.getAllByTestId('executor-tree')).toHaveLength(1)
    expect(work.getAttribute('aria-expanded')).toBe('true')
  })

  it("starts a new session from the tab's first button, on the executor chosen, in the folder typed", async () => {
    await showAccounts()
    const started = vi.spyOn(current(), 'newSession').mockImplementation(() => undefined)

    // The tab's first button, as a new conversation is in its own (the UI review, 26/10/2026).
    const top = screen.getByTestId('new-session-top')
    expect(top.compareDocumentPosition(screen.getByTestId('add-account'))).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    fireEvent.click(top)
    fireEvent.change(screen.getByTestId('new-session-executor'), { target: { value: 'claude-code@ab12cd34' } })
    const form = screen.getByTestId('new-session-form')
    fireEvent.change(within(form).getByRole('textbox'), { target: { value: '/work/shop' } })
    fireEvent.submit(form)

    expect(started).toHaveBeenCalledWith('claude-code@ab12cd34', '/work/shop')
  })

  it('adds one by its nickname alone, and reads the executors again for its row', async () => {
    const calls = await showAccounts()

    // Above the executors' list, not inside the first one's tree (asked for by the user on 19/10/2026).
    const add = screen.getByTestId('add-account')
    expect(add.closest('[data-testid="executor-node"]')).toBeNull()
    expect(add.compareDocumentPosition(screen.getAllByTestId('executor-node')[0] as HTMLElement)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING
    )
    fireEvent.click(add)
    const form = screen.getByTestId('add-account-form')
    expect(form.textContent).toContain('a folder of its own beside yours')
    fireEvent.change(within(form).getByTestId('account-nickname'), { target: { value: ' side ' } })
    fireEvent.submit(form)

    await vi.waitFor(() => {
      expect(screen.queryByTestId('add-account-form')).toBeNull()
    })
    expect(listed(calls, 'executor.add_account')).toEqual([
      ['executor.add_account', { executor: 'claude-code', nickname: 'side' }]
    ])
    expect(listed(calls, 'workbench.list').length).toBeGreaterThan(0)
  })

  it("removes one from its row's menu only once the user confirms, saying its folder stays", async () => {
    const calls = await showAccounts('claude-code@ab12cd34')
    const [main, work] = screen.getAllByTestId('executor-node') as [HTMLElement, HTMLElement]

    // The main account has none to remove.
    expect(within(openMenu(main)).queryByTestId('remove-account')).toBeNull()
    fireEvent.keyDown(screen.getByTestId('executor-menu-content'), { key: 'Escape' })
    fireEvent.click(within(openMenu(work)).getByTestId('remove-account'))
    expect(listed(calls, 'executor.remove_account')).toEqual([])
    const asked = screen.getByTestId('remove-account-confirm')
    expect(asked.textContent).toContain('.claude-work) stays as it is')
    fireEvent.click(within(asked).getByRole('button', { name: 'Remove work' }))

    await vi.waitFor(() => {
      expect(listed(calls, 'executor.remove_account')).toEqual([
        ['executor.remove_account', { account_id: 'ab12cd34', executor: 'claude-code' }]
      ])
    })
  })

  it('archives a session out of its folder and back, and deletes one only after a yes', async () => {
    localStorage.clear()
    const calls = await showAccounts('claude-code@ab12cd34')

    fireEvent.click(screen.getByTestId('session-folder-toggle'))
    await screen.findByTestId('session-row')
    fireEvent.click(screen.getByTestId('archive-session'))
    expect(screen.queryByTestId('session-row')).toBeNull()
    expect(screen.queryByTestId('archived-sessions')).toBeNull()
    showArchived(screen.getAllByTestId('executor-node')[1])
    fireEvent.click(screen.getByTestId('archived-sessions-toggle'))
    fireEvent.click(screen.getByTestId('unarchive-session'))
    expect(screen.getByTestId('session-row').textContent).toContain('Fix the API')

    fireEvent.click(screen.getByTestId('delete-session'))
    expect(listed(calls, 'executor.delete_session')).toEqual([])
    const asked = screen.getByTestId('delete-session-confirm')
    expect(asked.textContent).toContain('the other accounts keep theirs')
    fireEvent.click(within(asked).getByRole('button', { name: 'Delete' }))

    await vi.waitFor(() => {
      expect(listed(calls, 'executor.delete_session')).toEqual([
        ['executor.delete_session', { executor: 'claude-code', folder: 'C:\\work\\api', session_id: 's-1' }]
      ])
    })
  })
})

const SHOP = 'C:\\work\\shop'

describe('the executors tab, as the user asked for it on 22/10/2026', () => {
  it("a session's dot follows its instance as its events tell it, not as its folder was read", async () => {
    await showClaudeCode(method =>
      method === 'executor.folders'
        ? Promise.resolve({ folders: [folder(SHOP)] })
        : method === 'executor.sessions'
          ? Promise.resolve({ sessions: [session('s-1', SHOP, 'Show cents in the cart total')] })
          : new Promise(() => undefined)
    )
    fireEvent.click(await screen.findByTitle(SHOP))
    const row = await screen.findByTestId('session-row')
    expect(within(row).queryByTestId('status-dot')).toBeNull()

    const working = {
      executor: 'claude-code',
      folder: SHOP,
      instance_id: 'in-1',
      session_id: 's-1',
      state: 'working',
      waiting: 0
    } as InstanceRow
    act(() => {
      current().$workbench.set({ executors: [EXECUTOR], instances: [working] })
    })
    expect(within(row).getByTestId('status-dot').dataset.dot).toBe('working')
    expect(row.dataset.state).toBe('working')

    act(() => {
      current().$workbench.set({ executors: [EXECUTOR], instances: [] })
    })
    expect(within(row).queryByTestId('status-dot')).toBeNull()
  })

  it('a folder opened offers a new session in it, always; a new folder is in the row menu', async () => {
    await showClaudeCode(method =>
      method === 'executor.folders'
        ? Promise.resolve({ folders: [folder(SHOP)] })
        : method === 'executor.sessions'
          ? Promise.resolve({ sessions: [] })
          : new Promise(() => undefined)
    )
    const toggle = await screen.findByTitle(SHOP)
    expect(screen.queryByTestId('folder-new-session')).toBeNull()
    expect(screen.queryByTestId('new-session')).toBeNull()

    fireEvent.click(toggle)
    fireEvent.click(screen.getByTestId('folder-new-session'))
    expect(current().$openSession.get()).toMatchObject({
      executor: 'claude-code',
      folder: SHOP,
      sessionId: ''
    })

    fireEvent.click(within(openMenu()).getByTestId('new-session'))
    expect(screen.getByTestId('new-session-form')).toBeTruthy()
  })
})

describe('the executors and their accounts, in the order the user drags them into', () => {
  const WORK: ExecutorRow = {
    ...EXECUTOR,
    account_id: 'ab12cd34',
    base: 'claude-code',
    name: 'claude-code@ab12cd34',
    nickname: 'work'
  }
  const CODEX: ExecutorRow = { ...EXECUTOR, name: 'codex', plugin: 'codex', title: 'Codex', version: '0.163.0' }

  afterEach(() => {
    $executorOrder.set({})
    localStorage.clear()
  })

  /** Drags `from` onto `to`, on its upper half or its lower one. */
  function drag(from: HTMLElement, to: HTMLElement, lower: boolean): void {
    const dataTransfer = { dropEffect: '', effectAllowed: '', setData: vi.fn() }
    fireEvent.dragStart(from, { dataTransfer })
    fireEvent.dragOver(to, { clientY: lower ? 10 : -10, dataTransfer })
    fireEvent.drop(to, { dataTransfer })
    fireEvent.dragEnd(from, { dataTransfer })
  }

  const names = () => screen.getAllByTestId('executor-node').map(node => node.dataset.executor)

  it('moves a harness whole among the others, an account only among its own, and keeps it per home', async () => {
    stop = startApp({
      client: () => listingClient(() => new Promise(() => undefined), []),
      shell: { focus: () => undefined, notify: () => undefined }
    })
    await openHome('work')
    current().$workbench.set({ executors: [EXECUTOR, WORK, CODEX], instances: [] })
    render(<ExecutorsList />)
    expect(names()).toEqual(['claude-code', 'claude-code@ab12cd34', 'codex'])

    // Codex, alone, by its row: above Claude Code's heading, its accounts following that heading.
    const [claude, codex] = screen.getAllByTestId('harness') as [HTMLElement, HTMLElement]
    drag(within(codex).getByTestId('executor-drag'), claude, false)
    expect(names()).toEqual(['codex', 'claude-code', 'claude-code@ab12cd34'])

    // An account under the other: dropped on Codex, it stays among Claude Code's.
    const work = () => screen.getAllByTestId('executor-node')[2] as HTMLElement
    drag(within(work()).getByTestId('executor-drag'), screen.getAllByTestId('harness')[0] as HTMLElement, false)
    expect(names()).toEqual(['codex', 'claude-code', 'claude-code@ab12cd34'])
    drag(within(work()).getByTestId('executor-drag'), screen.getAllByTestId('executor-node')[1] as HTMLElement, false)
    expect(names()).toEqual(['codex', 'claude-code@ab12cd34', 'claude-code'])

    // Claude Code by its heading, back above Codex.
    drag(screen.getByTestId('harness-heading'), screen.getAllByTestId('harness')[0] as HTMLElement, false)
    expect(names()).toEqual(['claude-code@ab12cd34', 'claude-code', 'codex'])
    expect($executorOrder.get()).toEqual({ work: ['claude-code@ab12cd34', 'claude-code', 'codex'] })
  })
})

describe('an executor not logged in (asked for by the user on 01/11/2026)', () => {
  it('offers its login on its row, not only in its menu, and hides it while the login is open', async () => {
    await showClaudeCode(() => new Promise(() => undefined))
    act(() => {
      current().$workbench.set({ executors: [{ ...EXECUTOR, logged_in: false }], instances: [] })
    })

    fireEvent.click(screen.getByTestId('executor-log-in'))
    expect(screen.getByTestId('executor-account')).toBeTruthy()
    expect(screen.queryByTestId('executor-log-in')).toBeNull()
  })

  it('says nothing of it once logged in', async () => {
    await showClaudeCode(() => new Promise(() => undefined))

    expect(screen.queryByTestId('executor-log-in')).toBeNull()
  })
})
