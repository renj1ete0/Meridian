// @vitest-environment jsdom
/**
 * "Where you were" leads back (`B-177`): recent nodes were never listed (the landing passed
 * an empty list though the node page records them), a saved node view was a dead click on
 * the landing, and a saved search view opened an empty landing from the node workspace.
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'
import { readRecentNodes, recordRecentNode } from '../src/explore/graph/recent'
import { hrefForView } from '../src/explore/views'
import { readFind } from '../src/lib/find'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.localStorage.clear()
  window.history.pushState({}, '', '/')
})

const VIEW = {
  view_id: 3,
  name: 'Kerb geometry',
  query: null,
  filters: { tier: ['government'] } as Record<string, unknown>,
  focus_entity_id: 95 as number | null,
  note: null,
  created_at: '2026-10-01T00:00:00Z',
  last_opened_at: null,
  new_since: null,
}

function landing(views: unknown[] = []) {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(String(url))
      if (String(url).includes('/api/explore/views')) return new Response(JSON.stringify({ views }))
      return new Promise<Response>(() => {})
    }),
  )
  return calls
}

describe('where a saved view opens', () => {
  it('a node view on its node, with its filters', () => {
    expect(hrefForView(VIEW)).toBe('/nodes/95?tier=government')
  })

  it('a search view on Find, with its words and every filter', () => {
    const href = hrefForView({ ...VIEW, focus_entity_id: null, query: 'kerb', filters: { places: ['JP'] } })
    const back = readFind(new URL(href, 'http://x').search)
    expect(back.q).toBe('kerb')
    expect(back.filters.places).toEqual(['JP'])
  })
})

describe('the landing', () => {
  it('lists the nodes this browser opened, with when, and opens them', async () => {
    recordRecentNode({ entity_id: 401, canonical_name: 'walkability of the built environment', node_type: 'concept' })
    expect(readRecentNodes()[0]!.at).toBeTruthy()
    landing()
    await act(async () => {
      render(<ExplorePage />)
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /walkability of the built environment/ }))
    })
    expect(window.location.pathname).toBe('/nodes/401')
  })

  it('opens a saved node view on its node instead of doing nothing', async () => {
    const calls = landing([VIEW])
    await act(async () => {
      render(<ExplorePage />)
    })
    await act(async () => {
      fireEvent.click(await screen.findByRole('button', { name: /Kerb geometry/ }))
    })
    expect(window.location.pathname).toBe('/nodes/95')
    expect(calls.some((u) => u.includes('/api/explore/search'))).toBe(false)
  })
})
