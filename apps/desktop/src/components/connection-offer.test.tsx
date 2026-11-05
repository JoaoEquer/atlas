import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { t } from '@/i18n'
import type { PluginRow } from '@/protocol/protocol.generated'
import * as app from '@/store/app'
import type { Item } from '@/store/thread'

import { ConnectionOffer } from './connection-offer'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  app.$plugins.set([])
})

function offer(connection: string): Extract<Item, { kind: 'tool' }> {
  return {
    args: JSON.stringify({ connection }),
    key: 'k1',
    kind: 'tool',
    name: 'offer_connection',
    progress: [],
    result: '',
    seconds: null,
    spillPath: null,
    startedAt: null,
    status: 'completed',
    toolId: 't1'
  }
}

describe('a connection Atlas offered in the conversation (05/11/2026)', () => {
  it('shows what the plugin declares, and only the user turns it on', () => {
    vi.spyOn(app, 'refreshPlugins').mockImplementation(() => undefined)
    const enabled = vi.spyOn(app, 'enablePlugin').mockResolvedValue(true)
    const google = {
      consent: 'abc',
      consented: false,
      declaration: ['Reads your email and calendar through gws.'],
      description: 'Gmail and Calendar, through gws.',
      enabled: false,
      name: 'google'
    } as unknown as PluginRow
    app.$plugins.set([google])
    render(<ConnectionOffer tool={offer('google')} />)

    expect(screen.getByText(t.connectionOffer.title(t.firstRun.google))).toBeTruthy()
    expect(screen.getByText('Reads your email and calendar through gws.')).toBeTruthy()
    expect(enabled).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('connection-offer-turn-on'))
    expect(enabled).toHaveBeenCalledWith(google)
  })

  it('says so when the plugin is not in this Atlas', () => {
    vi.spyOn(app, 'refreshPlugins').mockImplementation(() => undefined)
    render(<ConnectionOffer tool={offer('github')} />)

    expect(screen.getByText(t.connectionOffer.missing)).toBeTruthy()
  })
})
