// @vitest-environment jsdom
/**
 * Saved views and notes can be tidied and read in full (`B-180`): six views and five notes
 * showed on the landing, with no list of the rest and no way to rename or delete a view, though
 * the routes existed.
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'
import { deleteView } from '../src/lib/api'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function view(id: number, name: string) {
  return {
    view_id: id,
    name,
    query: 'kerb',
    filters: {},
    focus_entity_id: null,
    note: null,
    created_at: '2026-10-01T00:00:00Z',
    last_opened_at: null,
    new_since: null,
  }
}

function stub(views: unknown[], notes = { annotations: [], total: 0 }) {
  const calls: { url: string; method: string; body?: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ url: String(url), method, body: init?.body ? JSON.parse(String(init.body)) : undefined })
      if (String(url).includes('/api/explore/views')) return new Response(JSON.stringify({ views }))
      if (String(url).includes('/api/explore/annotations')) return new Response(JSON.stringify(notes))
      if (method === 'DELETE') return new Response(null, { status: 204 })
      if (method === 'PATCH')
        return new Response(JSON.stringify({ ...view(2, 'x'), ...JSON.parse(String(init!.body)) }))
      return new Promise<Response>(() => {})
    }),
  )
  return calls
}

async function landing() {
  await act(async () => {
    render(<ExplorePage />)
  })
}

describe('managing saved views', () => {
  it('lists every view past the six on the landing', async () => {
    stub(Array.from({ length: 8 }, (_, i) => view(i + 1, `view ${i + 1}`)))
    await landing()
    fireEvent.click(await screen.findByRole('button', { name: 'All 8 saved views →' }))
    const all = screen.getByRole('region', { name: 'Saved views' })
    expect(all.textContent).toContain('view 8')
    expect(all.textContent).toContain('All saved views · 8')
  })

  it('renames one in place', async () => {
    const calls = stub([view(2, 'old name')])
    await landing()
    fireEvent.click(await screen.findByRole('button', { name: 'manage views' }))
    fireEvent.click(screen.getByRole('button', { name: 'rename' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'New name for old name' }), { target: { value: 'new name' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'save' }))
    })
    expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ name: 'new name' })
    expect(screen.getAllByText('new name').length).toBeGreaterThan(0)
  })

  it('deletes one only after asking', async () => {
    const calls = stub([view(2, 'to go')])
    await landing()
    fireEvent.click(await screen.findByRole('button', { name: 'manage views' }))
    fireEvent.click(screen.getByRole('button', { name: 'delete' }))
    expect(calls.some((c) => c.method === 'DELETE')).toBe(false)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'yes' }))
    })
    expect(calls.find((c) => c.method === 'DELETE')!.url).toBe('/api/admin/views/2')
    expect(screen.queryByText('to go')).toBeNull()
  })

  it('treats a 204 as done, not as a malformed answer', async () => {
    stub([])
    await expect(deleteView(2)).resolves.toBeUndefined()
  })
})

describe('notes', () => {
  it('offers the rest while some are not shown, and reads up to the route’s ceiling', async () => {
    const calls = stub([], { annotations: [], total: 12 } as never)
    await landing()
    fireEvent.click(await screen.findByRole('button', { name: 'show all' }))
    const asked = calls.filter((c) => c.url.includes('/api/explore/annotations')).at(-1)!
    expect(new URL(asked.url, 'http://x').searchParams.get('limit')).toBe('100')
  })
})
