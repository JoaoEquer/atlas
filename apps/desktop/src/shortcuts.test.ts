import { describe, expect, it } from 'vitest'

import { shiftShortcut } from './shortcuts'

describe('a shortcut held with Shift, as the system writes it', () => {
  it('is ⇧⌘ on a Mac and Ctrl+Shift elsewhere', () => {
    expect(shiftShortcut('B', true)).toBe('⇧⌘B')
    expect(shiftShortcut('B', false)).toBe('Ctrl+Shift+B')
  })
})
