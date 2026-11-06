import { useStore } from '@nanostores/react'
import { atom } from 'nanostores'
import { DropdownMenu } from 'radix-ui'
import { type KeyboardEvent, useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import type { ConversationModel, ModelOption, ProviderOption } from '@/protocol/protocol.generated'
import { $modelOptions, $models, $selected, DRAFT, setModel } from '@/store/app'

import { Check, ChevronDown, ChevronRight, Search } from './ui/icons'
import { PILL } from './ui/pill'

/** From how many models the menu has a search on top. */
const SEARCH_FROM = 9

/** The menu open: a turn's "Change model" opens it from the thread. */
export const $modelMenu = atom(false)

/**
 * The conversation's model and effort, one pill in the composer (docs/ARCHITECTURE.md, Daily use)
 * that says both, "Fake High". Its menu (step 18f, drawn on 01/11/2026 from what the user
 * brought): the models of the conversation's provider first, numbered for the keys 1 to 9, the
 * default marked and the current one checked; then the effort, for a model that reasons, and each
 * other provider with a key, each in a menu of its own; a search on top once there are many. A
 * pick is for this conversation only and applies from its next turn, so nothing is disabled while
 * a turn runs. Prices stay hidden, and the pill shows what runs, never "Default": the effort is
 * Atlas's default when none was picked (asked for by the user on 17/10/2026).
 */
export function ModelPicker({ connected }: { connected: boolean }) {
  const selected = useStore($selected)
  const models = useStore($models)
  const options = useStore($modelOptions)
  const open = useStore($modelMenu)
  const [query, setQuery] = useState('')
  const search = useRef<HTMLInputElement>(null)

  // Opened with a search, the typing goes to it, once the menu has taken the focus it takes.
  useEffect(() => {
    if (open) {
      const later = setTimeout(() => search.current?.focus(), 0)

      return () => {
        clearTimeout(later)
      }
    }
  }, [open])

  if (!options) {
    return null
  }

  const current: ConversationModel = models[selected ?? DRAFT] ?? options.default

  if (!current.provider || !current.model) {
    return <span className="text-xs text-muted-foreground">{t.composer.noModel}</span>
  }

  const providers = withCurrent(options.providers, current.provider, current.model)
  const here = providers.find(p => p.id === current.provider)
  const others = providers.filter(p => p !== here)
  const info = here?.models.find(m => m.id === current.model)
  const efforts = info?.reasoning === false ? [] : (info?.efforts ?? [])
  // The server says the effort a conversation runs on, Atlas's default when it picked none.
  const effort = current.effort ?? info?.default_effort ?? null
  const all = providers.flatMap(provider => provider.models.map(model => ({ model, provider })))
  const words = query.trim().toLowerCase()
  const hits = words
    ? all.filter(({ model, provider }) => `${model.name} ${model.id} ${provider.name}`.toLowerCase().includes(words))
    : null

  const pick = (provider: string, model: string) => {
    const next = providers.find(p => p.id === provider)?.models.find(m => m.id === model)
    // An effort the new model does not take goes back to Atlas's default for it.
    const kept = effort && next?.efforts?.includes(effort) ? effort : null
    void setModel(provider, model, kept)
  }
  const item = (provider: ProviderOption, model: ModelOption, number?: number) => {
    const chosen = provider.id === current.provider && model.id === current.model
    const isDefault = provider.id === options.default.provider && model.id === options.default.model

    return (
      <DropdownMenu.Item
        className={ITEM}
        data-current={chosen ? 'true' : undefined}
        data-testid="model-option"
        key={`${provider.id}\u0000${model.id}`}
        onSelect={() => {
          pick(provider.id, model.id)
        }}
      >
        <span className="min-w-0 truncate">{model.name}</span>
        {hits && <span className="truncate text-xs text-muted-foreground">{provider.name}</span>}
        {isDefault && <span className={BADGE}>{t.composer.defaultBadge}</span>}
        <span className="ml-auto flex shrink-0 items-center text-xs text-muted-foreground">
          {chosen ? <Check aria-hidden size={14} stroke={2} /> : number !== undefined && number <= 9 && String(number)}
        </span>
      </DropdownMenu.Item>
    )
  }
  // The keys 1 to 9 pick the provider's models, as their numbers say; the search keeps its own.
  const onKey = (event: KeyboardEvent) => {
    if (event.target === search.current || !/^[1-9]$/.test(event.key)) {
      return
    }
    const model = here?.models[Number(event.key) - 1]

    if (here && model) {
      event.preventDefault()
      pick(here.id, model.id)
      $modelMenu.set(false)
    }
  }

  return (
    <DropdownMenu.Root
      onOpenChange={next => {
        $modelMenu.set(next)
        setQuery('')
      }}
      open={open && connected}
    >
      <DropdownMenu.Trigger asChild disabled={!connected}>
        <button
          aria-label={t.composer.model}
          className={`${PILL} max-w-64`}
          data-testid="model-select"
          title={t.composer.model}
          type="button"
        >
          <span className="truncate text-foreground" data-testid="model-name">
            {info?.name ?? current.model}
          </span>
          {efforts.length > 0 && effort && (
            <span className="shrink-0" data-testid="model-effort">
              {t.composer.effortLevel(effort)}
            </span>
          )}
          <ChevronDown aria-hidden className="shrink-0 text-muted-foreground" size={12} stroke={2} />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          className={MENU}
          data-testid="model-menu"
          onCloseAutoFocus={event => {
            // Back to the box, to write on the model just picked.
            event.preventDefault()
            document.querySelector<HTMLTextAreaElement>('[data-testid="composer"] textarea')?.focus()
          }}
          onKeyDown={onKey}
          side="top"
          sideOffset={6}
        >
          {all.length >= SEARCH_FROM && (
            <div className="mb-1 flex items-center gap-2 rounded-md border border-border px-2">
              <Search aria-hidden className="shrink-0 text-muted-foreground" size={14} stroke={1.75} />
              <input
                aria-label={t.composer.searchModels}
                className="h-7 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                data-testid="model-search"
                onChange={event => {
                  setQuery(event.target.value)
                }}
                onKeyDown={event => {
                  // The menu's own keys find items by their first letters: here they are words.
                  if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp' && event.key !== 'Escape') {
                    event.stopPropagation()
                  }
                }}
                placeholder={t.composer.searchModels}
                ref={search}
                value={query}
              />
            </div>
          )}
          {hits ? (
            hits.length > 0 ? (
              hits.map(({ model, provider }) => item(provider, model))
            ) : (
              <p className="px-2 py-1.5 text-sm text-muted-foreground">{t.composer.noModelMatch}</p>
            )
          ) : (
            <>
              {here && (
                <>
                  <DropdownMenu.Label className={LABEL}>{here.name}</DropdownMenu.Label>
                  {here.models.map((model, index) => item(here, model, index + 1))}
                </>
              )}
              {efforts.length > 0 && (
                <>
                  <DropdownMenu.Separator className={SEPARATOR} />
                  <DropdownMenu.Sub>
                    <DropdownMenu.SubTrigger className={ITEM} data-testid="effort-menu">
                      <span>{t.composer.effortTitle}</span>
                      <span className="ml-auto text-xs text-muted-foreground">
                        {effort ? t.composer.effortLevel(effort) : ''}
                      </span>
                      <ChevronRight aria-hidden className="text-muted-foreground" size={14} stroke={1.75} />
                    </DropdownMenu.SubTrigger>
                    <DropdownMenu.Portal>
                      <DropdownMenu.SubContent className={MENU} sideOffset={4}>
                        <DropdownMenu.RadioGroup
                          onValueChange={level => {
                            void setModel(current.provider ?? '', current.model ?? '', level)
                          }}
                          value={effort ?? ''}
                        >
                          {efforts.map(level => (
                            <DropdownMenu.RadioItem
                              className={ITEM}
                              data-testid="effort-option"
                              key={level}
                              value={level}
                            >
                              <span>{t.composer.effortLevel(level)}</span>
                              {level === info?.default_effort && (
                                <span className={BADGE}>{t.composer.defaultBadge}</span>
                              )}
                              <DropdownMenu.ItemIndicator className="ml-auto">
                                <Check aria-hidden size={14} stroke={2} />
                              </DropdownMenu.ItemIndicator>
                            </DropdownMenu.RadioItem>
                          ))}
                        </DropdownMenu.RadioGroup>
                      </DropdownMenu.SubContent>
                    </DropdownMenu.Portal>
                  </DropdownMenu.Sub>
                </>
              )}
              {others.length > 0 && <DropdownMenu.Separator className={SEPARATOR} />}
              {others.map(provider => (
                <DropdownMenu.Sub key={provider.id}>
                  <DropdownMenu.SubTrigger className={ITEM} data-testid="provider-menu">
                    <span className="truncate">{provider.name}</span>
                    <ChevronRight aria-hidden className="ml-auto text-muted-foreground" size={14} stroke={1.75} />
                  </DropdownMenu.SubTrigger>
                  <DropdownMenu.Portal>
                    <DropdownMenu.SubContent className={`${MENU} max-h-80 overflow-y-auto`} sideOffset={4}>
                      {provider.models.map(model => item(provider, model))}
                    </DropdownMenu.SubContent>
                  </DropdownMenu.Portal>
                </DropdownMenu.Sub>
              ))}
            </>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

const MENU = 'z-50 w-64 rounded-lg border border-border bg-raised p-1 text-foreground shadow-lg'

const ITEM =
  'flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-sm outline-none select-none data-disabled:opacity-40 data-highlighted:bg-muted data-[state=open]:bg-muted'

const LABEL = 'px-2 pt-1 pb-0.5 text-xs text-muted-foreground'

const SEPARATOR = 'my-1 h-px bg-selected'

const BADGE = 'shrink-0 rounded border border-border-strong px-1 text-[10px] leading-4 text-muted-foreground'

/** The offer, with the conversation's model in it even when the curated list lacks it (a model
 * named in config.toml, or one the list no longer carries). */
export function withCurrent(providers: ProviderOption[], provider: string, model: string): ProviderOption[] {
  const found = providers.find(p => p.id === provider)

  if (found?.models.some(m => m.id === model)) {
    return providers
  }

  const extra: ModelOption = { default_effort: null, efforts: null, id: model, name: model, reasoning: null }

  if (found) {
    return providers.map(p => (p === found ? { ...p, models: [...p.models, extra] } : p))
  }

  return [...providers, { id: provider, models: [extra], name: provider }]
}
