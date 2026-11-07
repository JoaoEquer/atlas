/** What the Setup's window and its main process say to each other, through the preload. */
import type { Line } from './install'
import type { Language } from './words'

export const CHANNELS = {
  close: 'setup:close',
  end: 'setup:end',
  install: 'setup:install',
  line: 'setup:line',
  log: 'setup:log',
  open: 'setup:open',
  state: 'setup:state'
} as const

export interface SetupState {
  /** Whether Atlas is installed where the installer puts it. */
  installed: boolean
  language: Language
  root: string
}

/** How a run of the installer ended. */
export interface Ended {
  code: number
  /** Its last `[X]` line, or why it could not start. */
  error: string
  /** Every `[!]` line, kept for the end. */
  warnings: string[]
}

export interface SetupApi {
  close(): void
  install(): void
  onEnd(listener: (ended: Ended) => void): void
  onLine(listener: (line: Line) => void): void
  open(): void
  openLog(): void
  state(): Promise<SetupState>
}
