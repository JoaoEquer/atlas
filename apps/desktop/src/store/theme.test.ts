// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'

import type { PluginThemeRow } from '@/protocol/protocol.generated'

const SAND: PluginThemeRow = {
  plugin: 'sand',
  name: 'sand',
  label: 'Sand',
  base: 'light',
  colors: { background: '#e9e3d3', subtle: '#e2dbc9', working: '#2d63a8' },
  labels: { 'pt-BR': 'Areia' }
}

/** A fresh copy of the store, as the window opens with what the storage holds. */
async function opened(stored: Record<string, string> = {}) {
  localStorage.clear()
  for (const [key, value] of Object.entries(stored)) {
    localStorage.setItem(key, value)
  }
  document.documentElement.removeAttribute('style')
  document.documentElement.classList.remove('dark')
  const { vi } = await import('vitest')
  vi.resetModules()

  return import('./theme')
}

describe('a theme a plugin brings', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('repaints the colors it names, on the page root, and keeps its base look', async () => {
    const theme = await opened()
    theme.startTheme()
    theme.setPluginThemes([SAND])
    theme.setTheme('plugin:sand:sand')

    const root = document.documentElement
    expect(root.style.getPropertyValue('--subtle')).toBe('#e2dbc9')
    expect(root.style.getPropertyValue('--working')).toBe('#2d63a8')
    expect(root.classList.contains('dark')).toBe(false)
    expect(theme.$theme.get()).toBe('light')
  })

  it('takes its colors off when another look is chosen', async () => {
    const theme = await opened()
    theme.setPluginThemes([SAND])
    theme.setTheme('plugin:sand:sand')
    theme.setTheme('dark')

    expect(document.documentElement.style.getPropertyValue('--subtle')).toBe('')
    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })

  it('is painted as the system look, and kept, while its plugin is off', async () => {
    const theme = await opened({ 'atlas.theme': 'plugin:sand:sand' })
    theme.setPluginThemes([])

    expect(document.documentElement.style.getPropertyValue('--subtle')).toBe('')
    expect(theme.$themeChoice.get()).toBe('plugin:sand:sand')
    expect(localStorage.getItem('atlas.theme')).toBe('plugin:sand:sand')

    theme.setPluginThemes([SAND])
    expect(document.documentElement.style.getPropertyValue('--subtle')).toBe('#e2dbc9')
  })

  it('is painted at once from the last time, before the server says what is on', async () => {
    const theme = await opened({ 'atlas.theme': 'plugin:sand:sand', 'atlas.theme.row': JSON.stringify(SAND) })
    theme.startTheme()

    expect(document.documentElement.style.getPropertyValue('--background')).toBe('#e9e3d3')
  })

  it('paints only a hex color for a named color of the app, whatever the server sends', async () => {
    const theme = await opened()
    theme.setPluginThemes([
      {
        ...SAND,
        colors: {
          subtle: 'url(https://x.test/a.png)',
          muted: '#e2dbc9',
          '--evil': '#fff',
          'motion-fast': '#fff'
        }
      }
    ])
    theme.setTheme('plugin:sand:sand')

    const style = document.documentElement.style
    expect(style.getPropertyValue('--subtle')).toBe('')
    expect(style.getPropertyValue('--muted')).toBe('#e2dbc9')
    expect(style.getPropertyValue('--motion-fast')).toBe('')
    expect(style.getPropertyValue('--evil')).toBe('')
  })

  it('sits on the base the plugin names: a dark theme turns the dark class on', async () => {
    const theme = await opened()
    theme.setPluginThemes([{ ...SAND, name: 'dusk', base: 'dark' }])
    theme.setTheme('plugin:sand:dusk')

    expect(document.documentElement.classList.contains('dark')).toBe(true)
  })
})
