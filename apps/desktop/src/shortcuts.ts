/** Whether the window runs on macOS, where a shortcut is held with ⌘ rather than Ctrl. */
export const MAC = typeof navigator !== 'undefined' && navigator.userAgent.includes('Mac')

/** A shortcut's keys as the system's own menus write them: ⌘B on a Mac, Ctrl+B elsewhere. */
export function shortcut(key: string): string {
  return MAC ? `⌘${key}` : `Ctrl+${key}`
}

/** The same with Shift held too: ⇧⌘B on a Mac, Ctrl+Shift+B elsewhere. */
export function shiftShortcut(key: string, mac = MAC): string {
  return mac ? `⇧⌘${key}` : `Ctrl+Shift+${key}`
}
