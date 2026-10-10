// @vitest-environment jsdom
/** Run history goes back past its first page (`B-198`): 25 of 422 was all an operator saw. */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { RunsPanel } from '../src/admin/RunsPanel'
import type { RunRow } from '../src/lib/api'

afterEach(cleanup)

function run(id: number): RunRow {
  return {
    run_id: id,
    started_at: '2026-10-08T10:00:00Z',
    finished_at: null,
    status: 'done',
    stage: 'done',
    tokens_used: 0,
    cost_usd: null,
    edges_added: 0,
    tags_added: 0,
    seeds_emitted: 0,
    agent_id: null,
    error: null,
  } as unknown as RunRow
}

describe('older runs', () => {
  it('are offered while there are more than shown, and asked for', () => {
    const onOlder = vi.fn()
    const { rerender } = render(<RunsPanel rows={[run(2)]} total={422} active={null} onOlder={onOlder} />)
    fireEvent.click(screen.getByRole('button', { name: 'Older runs (421 more)' }))
    expect(onOlder).toHaveBeenCalled()
    rerender(<RunsPanel rows={[run(2)]} total={1} active={null} onOlder={onOlder} />)
    expect(screen.queryByRole('button', { name: /Older runs/ })).toBeNull()
  })
})
