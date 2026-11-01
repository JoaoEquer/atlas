import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { ModelOption, ProviderOption } from '@/protocol/protocol.generated'
import * as app from '@/store/app'

import { $modelMenu, ModelPicker } from './model-picker'

function model(id: string, name: string): ModelOption {
  return { default_effort: 'low', efforts: ['low', 'high'], id, name, reasoning: true }
}

const OPENAI: ProviderOption = {
  id: 'openai',
  models: Array.from({ length: 7 }, (_, index) => model(`gpt-${String(index)}`, `GPT ${String(index)}`)),
  name: 'OpenAI'
}
const LOCAL: ProviderOption = { id: 'vllm', models: [model('fake', 'Fake'), model('mini', 'Mini')], name: 'vLLM' }

function offer(): void {
  app.$modelOptions.set({
    default: { effort: null, model: 'gpt-0', picked: false, provider: 'openai' },
    providers: [OPENAI, LOCAL]
  })
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  $modelMenu.set(false)
  app.$modelOptions.set(null)
})

describe('the model pill', () => {
  it('says the model and the effort it runs on', () => {
    offer()
    render(<ModelPicker connected />)

    expect(screen.getByTestId('model-name').textContent).toBe('GPT 0')
    expect(screen.getByTestId('model-effort').textContent).toBe('Low')
  })

  it('lists the provider of the conversation, the others each in a menu of their own', () => {
    offer()
    $modelMenu.set(true)
    render(<ModelPicker connected />)

    expect(screen.getAllByTestId('model-option').map(row => row.textContent)).toEqual([
      'GPT 0Default',
      'GPT 12',
      'GPT 23',
      'GPT 34',
      'GPT 45',
      'GPT 56',
      'GPT 67'
    ])
    expect(screen.getAllByTestId('provider-menu').map(row => row.textContent)).toEqual(['vLLM'])
  })

  it('searches every provider once there are many, and picks what was found', async () => {
    const pick = vi.spyOn(app, 'setModel').mockResolvedValue(undefined)
    offer()
    $modelMenu.set(true)
    render(<ModelPicker connected />)

    const search = screen.getByTestId('model-search')
    await waitFor(() => {
      expect(document.activeElement).toBe(search)
    })
    fireEvent.change(search, { target: { value: 'min' } })
    expect(screen.getAllByTestId('model-option').map(row => row.textContent)).toEqual(['MinivLLM'])
    fireEvent.click(screen.getByTestId('model-option'))

    // The effort it ran on goes with it, the new model taking it.
    expect(pick).toHaveBeenCalledWith('vllm', 'mini', 'low')
  })

  it('picks a model by its number', () => {
    const pick = vi.spyOn(app, 'setModel').mockResolvedValue(undefined)
    offer()
    $modelMenu.set(true)
    render(<ModelPicker connected />)
    fireEvent.keyDown(screen.getByTestId('model-menu'), { key: '3' })

    expect(pick).toHaveBeenCalledWith('openai', 'gpt-2', 'low')
    expect($modelMenu.get()).toBe(false)
  })
})
