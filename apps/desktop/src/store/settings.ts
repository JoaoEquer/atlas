/**
 * The settings screen on screen (step 15b, docs/ARCHITECTURE.md, The settings screen): open or not,
 * and the section on its right. It opens on a section and keeps the others a click away on its
 * left. Extensions is a screen of its own since 01/11/2026 (`showExtensions`).
 */
import { atom } from 'nanostores'

import { $machinesNotice, refreshMachines, refreshThisMachine } from './machines'

export const SECTIONS = ['general', 'model', 'personalization', 'safety', 'workspace', 'machines'] as const
export type SettingsSection = (typeof SECTIONS)[number]

export const $settings = atom(false)
export const $settingsSection = atom<SettingsSection>('general')

/** Open the settings on a section. */
export function openSettings(section: SettingsSection = 'general'): void {
  $settings.set(true)
  showSection(section)
}

export function showSection(section: SettingsSection): void {
  $settingsSection.set(section)
  if (section === 'machines') {
    $machinesNotice.set(null)
    void refreshMachines()
    void refreshThisMachine()
  }
}

export function closeSettings(): void {
  $settings.set(false)
}
