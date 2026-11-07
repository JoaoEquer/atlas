import { DropdownMenu } from 'radix-ui'
import type { KeyboardEvent } from 'react'

import { t } from '@/i18n'

import { Check, ChevronDown } from './ui/icons'
import { PILL } from './ui/pill'

/**
 * The permission mode an executor's session runs in, a pill by its box (step 18g, asked for by the
 * user on 01/11/2026): each mode its harness lists (SDK version 28), numbered for the keys 1 to 9,
 * with what it does, its default marked and the current one checked. Claude Code's are its default,
 * which asks, edits taken without asking, a plan before any change, and its automatic mode; never
 * the one that checks nothing. A harness with only its automatic mode keeps the box's Auto switch.
 */
export function ModeMenu({
  current,
  disabled,
  modes,
  onPick
}: {
  current: string
  disabled: boolean
  modes: readonly string[]
  onPick: (mode: string) => void
}) {
  const onKey = (event: KeyboardEvent) => {
    const mode = /^[1-9]$/.test(event.key) ? modes[Number(event.key) - 1] : undefined

    if (mode) {
      event.preventDefault()
      onPick(mode)
    }
  }

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button className={PILL} data-mode={current} data-testid="mode-select" title={t.modes.title} type="button">
          <span className={current === modes[0] ? '' : 'text-foreground'}>{name(current)}</span>
          <ChevronDown aria-hidden className="text-muted-foreground" size={12} stroke={2} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          className="z-50 w-72 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg"
          data-testid="mode-menu"
          onKeyDown={onKey}
          side="top"
          sideOffset={6}
        >
          <DropdownMenu.Label className="px-2 pt-1 pb-0.5 text-xs text-muted-foreground">
            {t.modes.title}
          </DropdownMenu.Label>
          {modes.map((mode, index) => (
            <DropdownMenu.Item
              className="flex cursor-default items-start gap-2 rounded px-2 py-1.5 text-sm outline-none select-none data-highlighted:bg-muted"
              data-current={mode === current ? 'true' : undefined}
              data-testid="mode-option"
              key={mode}
              onSelect={() => {
                onPick(mode)
              }}
            >
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  {name(mode)}
                  {index === 0 && (
                    <span className="rounded border border-border-strong px-1 text-[10px] leading-4 text-muted-foreground">
                      {t.composer.defaultBadge}
                    </span>
                  )}
                </span>
                {t.modes.hints[mode] && (
                  <span className="block text-xs text-muted-foreground">{t.modes.hints[mode]}</span>
                )}
              </span>
              <span className="flex w-4 shrink-0 justify-end pt-0.5 text-xs text-muted-foreground">
                {mode === current ? <Check aria-hidden size={14} stroke={2} /> : index < 9 && String(index + 1)}
              </span>
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

/** A mode as the screen says it; one the app does not know, as its harness names it. */
function name(mode: string): string {
  return t.modes.names[mode] ?? mode
}
