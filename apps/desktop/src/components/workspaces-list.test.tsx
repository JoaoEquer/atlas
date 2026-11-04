import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExecutorRow, WorkspaceRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import * as workspaces from '@/store/workspaces'

import { WorkspacesList } from './workspaces-list'

const CLAUDE = { name: 'claude-code' } as ExecutorRow

function row(name: string, overrides: Partial<WorkspaceRow> = {}): WorkspaceRow {
  return {
    base: 'main',
    branch: `atlas/${name}`,
    created_at: '2026-11-04T10:00:00Z',
    folder: `/home/worktrees/shop-1a2b3c/${name}`,
    id: `w-${name}`,
    name,
    ports: '',
    project_root: '/work/shop',
    setup_output: '',
    state: 'ready',
    ...overrides
  }
}

beforeEach(() => {
  app.$connection.set({ error: null, state: 'open' })
})

afterEach(() => {
  cleanup()
  app.$connection.set({ error: null, state: 'idle' })
  vi.restoreAllMocks()
})

describe('the workspaces in the executors tab', () => {
  it('shows nothing while the home has none', async () => {
    const listed = vi.spyOn(workspaces, 'listWorkspaces').mockResolvedValue([])
    render(<WorkspacesList executors={[CLAUDE]} />)

    await waitFor(() => {
      expect(listed).toHaveBeenCalled()
    })
    expect(screen.queryByTestId('workspaces')).toBeNull()
  })

  it('groups them under their project, says what each is doing, and opens a session in one', async () => {
    vi.spyOn(workspaces, 'listWorkspaces').mockResolvedValue([
      row('new-page', { state: 'preparing' }),
      row('fix-login'),
      row('old', { project_root: '/work/docs', state: 'setup_failed', setup_output: 'npm ERR! missing' })
    ])
    const opened = vi.spyOn(app, 'newSession').mockImplementation(() => undefined)
    render(<WorkspacesList executors={[CLAUDE]} />)

    const projects = await screen.findAllByTestId('workspace-project')
    expect(projects.map(project => project.dataset.project)).toEqual(['/work/docs', '/work/shop'])
    const names = screen.getAllByTestId('workspace').map(item => item.dataset.workspace)
    expect(names).toEqual(['old', 'fix-login', 'new-page'])
    expect(screen.getAllByTestId('workspace-state').map(state => state.textContent)).toEqual([
      'setup failed',
      'preparing…'
    ])
    expect(screen.getByText('npm ERR! missing')).toBeTruthy()

    const [, fixLogin] = screen.getAllByTestId('workspace-open')
    if (fixLogin) {
      fireEvent.click(fixLogin)
    }
    expect(opened).toHaveBeenCalledWith('claude-code', '/home/worktrees/shop-1a2b3c/fix-login')
  })

  it('deletes one with work not delivered only on a second yes', async () => {
    vi.spyOn(workspaces, 'listWorkspaces').mockResolvedValue([row('busy')])
    const deleted = vi
      .spyOn(workspaces, 'deleteWorkspace')
      .mockRejectedValueOnce(new Error('The workspace busy holds work not delivered'))
      .mockResolvedValueOnce(undefined)
    render(<WorkspacesList executors={[CLAUDE]} />)

    fireEvent.click(await screen.findByTestId('workspace-delete'))
    expect(screen.getByTestId('workspace-delete-confirm').textContent).toContain('atlas/busy')
    fireEvent.click(screen.getByTestId('workspace-delete-yes'))

    await waitFor(() => {
      expect(screen.getByTestId('workspace-delete-confirm').textContent).toContain('not delivered')
    })
    fireEvent.click(screen.getByTestId('workspace-delete-yes'))

    await waitFor(() => {
      expect(screen.queryByTestId('workspace-delete-confirm')).toBeNull()
    })
    expect(deleted.mock.calls).toEqual([
      ['w-busy', false],
      ['w-busy', true]
    ])
  })

  it('archives one, and brings an archived one back', async () => {
    vi.spyOn(workspaces, 'listWorkspaces').mockResolvedValue([row('kept'), row('gone', { state: 'archived' })])
    const archived = vi.spyOn(workspaces, 'archiveWorkspace').mockResolvedValue(row('kept'))
    const back = vi.spyOn(workspaces, 'unarchiveWorkspace').mockResolvedValue(row('gone'))
    render(<WorkspacesList executors={[CLAUDE]} />)

    fireEvent.click(await screen.findByTestId('workspace-archive'))
    fireEvent.click(screen.getByTestId('workspace-unarchive'))

    expect(archived).toHaveBeenCalledWith('w-kept')
    expect(back).toHaveBeenCalledWith('w-gone')
    expect(screen.getAllByTestId('workspace-open')[0]).toHaveProperty('disabled', true)
  })

  it('makes a new one, and sets how its project prepares them', async () => {
    vi.spyOn(workspaces, 'listWorkspaces').mockResolvedValue([row('first')])
    const created = vi.spyOn(workspaces, 'createWorkspace').mockResolvedValue(row('second'))
    vi.spyOn(workspaces, 'readProject').mockResolvedValue({
      ports_size: 10,
      ports_start: 0,
      root: '/work/shop',
      setup_script: ''
    })
    const saved = vi.spyOn(workspaces, 'saveProject').mockResolvedValue({
      ports_size: 10,
      ports_start: 4000,
      root: '/work/shop',
      setup_script: 'npm ci'
    })
    render(<WorkspacesList executors={[CLAUDE]} />)

    fireEvent.keyDown(await screen.findByTestId('workspace-project-menu'), { key: 'Enter' })
    fireEvent.click(screen.getByTestId('workspace-new'))
    fireEvent.change(screen.getByLabelText('Name of the workspace'), { target: { value: 'second' } })
    fireEvent.submit(screen.getByTestId('workspace-new-form'))
    await waitFor(() => {
      expect(screen.queryByTestId('workspace-new-form')).toBeNull()
    })

    fireEvent.keyDown(screen.getByTestId('workspace-project-menu'), { key: 'Enter' })
    fireEvent.click(screen.getByTestId('workspace-setup'))
    const script = screen.getByTestId('workspace-setup-script')
    await waitFor(() => {
      expect(script).toHaveProperty('disabled', false)
    })
    fireEvent.change(script, { target: { value: 'npm ci' } })
    fireEvent.change(screen.getByTestId('workspace-ports-start'), { target: { value: '4000' } })
    fireEvent.submit(screen.getByTestId('workspace-setup-form'))

    expect(created).toHaveBeenCalledWith('/work/shop', 'second')
    await waitFor(() => {
      expect(saved).toHaveBeenCalledWith('/work/shop', 'npm ci', 4000, 10)
    })
  })
})
