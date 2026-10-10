// @vitest-environment jsdom
/**
 * Deciding a possible duplicate (`B-202`): both nodes side by side with their evidence, one
 * decision, and undo, since a merge is reversible.
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DuplicatesPanel } from '../src/admin/DuplicatesPanel'
import { SECTIONS } from '../src/admin/sections'
import { ApiError } from '../src/lib/api'
import type { DuplicatePair } from '../src/lib/duplicates'

afterEach(cleanup)

const side = (id: number, name: string) => ({
  entity_id: id,
  canonical_name: name,
  node_type: 'concept',
  jurisdiction: null,
  aliases: ['another spelling'],
  description: null,
  links: id === 1 ? 1 : 4,
  passages: [{ chunk_id: id * 10, source_id: id * 100, title: 'A source', text: 'What the passage says' }],
})

const PAIR: DuplicatePair = {
  notification_id: 7,
  mention: 'a name',
  created: side(1, 'made by the run'),
  candidate: side(2, 'already there'),
  created_at: '2026-10-08T00:00:00Z',
}

describe('the panel', () => {
  it('shows both nodes, linked, with their evidence and links counted', () => {
    render(<DuplicatesPanel pairs={[PAIR]} total={67} onDecide={vi.fn()} onUndo={vi.fn()} />)
    expect(screen.getByRole('link', { name: 'made by the run' }).getAttribute('href')).toBe('/nodes/1')
    expect(screen.getByRole('link', { name: 'already there' }).getAttribute('href')).toBe('/nodes/2')
    expect(screen.getByText(/1 link$/)).toBeTruthy()
    expect(screen.getByText(/4 links$/)).toBeTruthy()
    expect(screen.getAllByRole('link', { name: /What the passage says/ })[0]!.getAttribute('href')).toBe(
      '/sources/100?passage=10',
    )
    expect(screen.getByText(/67 waiting, 1 shown/)).toBeTruthy()
  })

  it('merges, says so, and undoes', async () => {
    const onDecide = vi.fn(async () => ({ notification_id: 7, decision: 'merged' as const, merge_id: 3 }))
    const onUndo = vi.fn(async () => ({ notification_id: 7, decision: 'reopened' as const, merge_id: null }))
    render(<DuplicatesPanel pairs={[PAIR]} total={1} onDecide={onDecide} onUndo={onUndo} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Same thing: merge' }))
    })
    expect(onDecide).toHaveBeenCalledWith(PAIR, 'merge')
    expect(screen.getByRole('status').textContent).toContain('Merged')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    })
    expect(onUndo).toHaveBeenCalledWith(PAIR)
    expect(screen.getByRole('button', { name: 'Same thing: merge' })).toBeTruthy()
  })

  it('shows a refusal as the server words it, and keeps the choice open', async () => {
    const onDecide = vi.fn(async () => {
      throw new ApiError(409, 'concept and organisation are different kinds of thing')
    })
    render(<DuplicatesPanel pairs={[PAIR]} total={1} onDecide={onDecide} onUndo={vi.fn()} />)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Same thing: merge' }))
    })
    expect(screen.getByRole('alert').textContent).toContain('different kinds of thing')
    expect(screen.getByRole('button', { name: 'Different: keep apart' })).toBeTruthy()
  })

  it('says when there is nothing to decide', () => {
    render(<DuplicatesPanel pairs={[]} total={0} onDecide={vi.fn()} onUndo={vi.fn()} />)
    expect(screen.getByText(/Nothing to decide/)).toBeTruthy()
  })

  it('has its own Admin section', () => {
    expect(SECTIONS.find((s) => s.key === 'duplicates')?.path).toBe('duplicates')
  })
})
