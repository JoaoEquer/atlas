/**
 * The settings screen on screen (step 15b, docs/ARCHITECTURE.md, The settings screen): open or not,
 * the section on its right, and the tab of its Extensions. It opens on a section and keeps the
 * others a click away on its left.
 */
import { atom } from 'nanostores'

import { detectExecutors, refreshExtensions, refreshPlugins } from './app'
import type { ExtensionKind } from './extensions'
import { $machinesNotice, refreshMachines, refreshThisMachine } from './machines'

export const SECTIONS = [
  'general',
  'model',
  'personalization',
  'safety',
  'workspace',
  'extensions',
  'machines'
] as const
export type SettingsSection = (typeof SECTIONS)[number]

export const $settings = atom(false)
export const $settingsSection = atom<SettingsSection>('general')
/** The Extensions section's tab: its plugins, skills, MCP servers or executors. */
export const $extensionsTab = atom<ExtensionKind>('plugin')

/** Open the settings on a section; on Extensions, on `tab` when one is given. */
export function openSettings(section: SettingsSection = 'general', tab?: ExtensionKind): void {
  $settings.set(true)
  if (tab) {
    $extensionsTab.set(tab)
  }

  showSection(section)
}

export function showSection(section: SettingsSection): void {
  $settingsSection.set(section)
  if (section === 'machines') {
    $machinesNotice.set(null)
    void refreshMachines()
    void refreshThisMachine()
  } else if (section === 'extensions') {
    // Every tab's count shows at once, so all are read; the harnesses are looked for on their tab.
    refreshPlugins()
    refreshExtensions()
    if ($extensionsTab.get() === 'executor') {
      void detectExecutors()
    }
  }
}

export function showExtensionsTab(tab: ExtensionKind): void {
  $extensionsTab.set(tab)
  if (tab === 'executor') {
    void detectExecutors()
  }
}

export function closeSettings(): void {
  $settings.set(false)
}
