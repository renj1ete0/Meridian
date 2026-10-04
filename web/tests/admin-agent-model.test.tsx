/**
 * Choosing an agent's model from Admin (tasks P6-06, P6-07).
 *
 * A local server names its models however it was started, so the row pointing
 * at it has to be able to follow without a release. What matters: the string
 * sent is the one typed, a string the server would refuse is stopped before it
 * is sent, and a refused save keeps what was typed rather than pretending.
 *
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AgentsPanel, modelProblem } from '../src/admin/AgentsPanel'
import { editAgent, type AgentRow } from '../src/lib/api'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function agent(over: Partial<AgentRow> = {}): AgentRow {
  return {
    agent_id: 'local-chat',
    provider: 'openai_compatible',
    model: '${LOCAL_CHAT_MODEL}',
    task_types: ['chat'],
    quality_tier: 2,
    cost_tier: 'free',
    availability: 'opportunistic',
    enabled: false,
    fallback_agent_id: null,
    route_order: null,
    endpoint: '${LOCAL_CHAT_LLM_URL}',
    api_key_env_var: null,
    key_present: false,
    blocked_by: [],
    ...over,
  }
}

describe('the model field', () => {
  it('sends exactly the string typed, and closes once saved', async () => {
    const onModel = vi.fn(async () => true)
    render(<AgentsPanel rows={[agent()]} unserved={[]} onModel={onModel} />)

    fireEvent.click(screen.getByRole('button', { name: 'Change the model for local-chat' }))
    const input = screen.getByRole('textbox', { name: 'Model for local-chat' }) as HTMLInputElement
    // It starts from the stored value, so a small change is a small edit.
    expect(input.value).toBe('${LOCAL_CHAT_MODEL}')
    fireEvent.change(input, { target: { value: 'qwen2.5:7b-instruct' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    expect(onModel).toHaveBeenCalledWith('local-chat', 'qwen2.5:7b-instruct')
    await waitFor(() => expect(screen.queryByRole('textbox')).toBeNull())
  })

  it('keeps what was typed when the server refuses it', async () => {
    const onModel = vi.fn(async () => false)
    render(<AgentsPanel rows={[agent()]} unserved={[]} onModel={onModel} />)

    fireEvent.click(screen.getByRole('button', { name: 'Change the model for local-chat' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'llama3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(onModel).toHaveBeenCalled())
    expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('llama3')
  })

  it('does not send a string the server would refuse', () => {
    const onModel = vi.fn(async () => true)
    render(<AgentsPanel rows={[agent()]} unserved={[]} onModel={onModel} />)

    fireEvent.click(screen.getByRole('button', { name: 'Change the model for local-chat' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: ' llama3' } })
    const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.submit(save.closest('form')!)

    expect(onModel).not.toHaveBeenCalled()
    expect(screen.getByText('No spaces at either end.')).toBeTruthy()
  })

  it('cancels back to the stored value, unchanged', () => {
    const onModel = vi.fn(async () => true)
    render(<AgentsPanel rows={[agent()]} unserved={[]} onModel={onModel} />)

    fireEvent.click(screen.getByRole('button', { name: 'Change the model for local-chat' }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'other' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onModel).not.toHaveBeenCalled()
    expect(screen.getByText('${LOCAL_CHAT_MODEL}')).toBeTruthy()
  })

  it('offers no edit where nothing can save it', () => {
    render(<AgentsPanel rows={[agent()]} unserved={[]} />)

    expect(screen.queryByRole('button', { name: /Change the model/ })).toBeNull()
  })

  it('mirrors the server rule for a model string', () => {
    // `AgentEdit.model`: 1–200 characters, no whitespace at either end.
    expect(modelProblem('')).not.toBeNull()
    expect(modelProblem('   ')).not.toBeNull()
    expect(modelProblem('m ')).not.toBeNull()
    expect(modelProblem('x'.repeat(201))).not.toBeNull()
    expect(modelProblem('x'.repeat(200))).toBeNull()
    expect(modelProblem('org/model name:q4')).toBeNull()
  })
})

describe('editAgent', () => {
  it('sends only the fields being changed', async () => {
    const bodies: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        bodies.push(JSON.parse(String(init?.body)))
        return new Response(JSON.stringify({ rows: [], unserved_tasks: [] }), { status: 200 })
      }),
    )

    await editAgent('local-chat', { model: 'llama3' })
    await editAgent('local-chat', { enabled: true })

    // An `enabled: undefined` beside a model edit would be dropped by JSON,
    // but a `false` default would silently switch the agent off.
    expect(bodies).toEqual([{ model: 'llama3' }, { enabled: true }])
  })
})
