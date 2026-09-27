// @vitest-environment jsdom
/**
 * The graph workspace at `/nodes/{id}` (tasks P6-01, P6-02, P6-03; §12.2,
 * §12.3).
 *
 * The canvas itself is WebGL and jsdom has none, so it is replaced by a list
 * of buttons that call the same `onNodeClick` Sigma would. Everything around
 * it — requests, the URL, the status line, the view switcher, path mode, the
 * honest states — is the real component.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GraphCanvasProps } from '../src/explore/graph/GraphCanvas'
import { CEILING, NodePage, PAGE, cardPosition, expandBlocked, statusLine } from '../src/explore/NodePage'
import { BUILT_VIEWS, VIEWS, VIEW_LABEL } from '../src/explore/graph/filters'
import { detail, gnode, hood, path } from './graph-fixtures'

vi.mock('../src/explore/graph/GraphCanvas', () => ({
  GraphCanvas: (props: GraphCanvasProps) => (
    <div data-testid="canvas">
      {props.scene.nodes.map((n) => (
        <button key={n.id} type="button" onClick={() => props.onNodeClick?.(n.id)}>
          canvas {n.node.canonical_name}
        </button>
      ))}
    </div>
  ),
}))

type Route = (url: string, init?: RequestInit) => { status?: number; body: unknown } | undefined

let calls: string[] = []

function serve(route: Route) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push(url)
      const found = route(url, init) ?? defaults(url)
      return new Response(JSON.stringify(found.body), { status: found.status ?? 200 })
    }),
  )
}

function defaults(url: string): { status?: number; body: unknown } {
  if (url.startsWith('/api/explore/views')) return { body: { views: [] } }
  if (url.includes('/neighbourhood')) return { body: hood(3) }
  if (url.startsWith('/api/explore/graph/nodes/')) return { body: detail() }
  return { status: 404, body: { detail: `unrouted ${url}` } }
}

beforeEach(() => {
  window.history.replaceState({}, '', '/nodes/1')
  window.localStorage.clear()
  window.sessionStorage.clear()
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('status and expansion', () => {
  it('says how much of the neighbourhood is shown, and why', () => {
    expect(statusLine(hood(3, { shown: 11, total: 34, unfiltered: 34 }))).toBe(
      'depth 1 · 11 of 34 neighbours shown · ranked by supporting passages',
    )
    expect(statusLine(hood(3, { shown: 2, total: 2, unfiltered: 12 }))).toContain('10 filtered out')
  })

  it('blocks Expand with a reason', () => {
    expect(expandBlocked(hood(3), PAGE)).toBe('All 3 neighbours are shown.')
    expect(expandBlocked(hood(3, { shown: 100, total: 140 }), CEILING)).toMatch(/stops at 100/)
    expect(expandBlocked(hood(3, { shown: 30, total: 40 }), PAGE)).toBeNull()
  })

  it('keeps the hover card inside the canvas', () => {
    const node = { id: 1, x: 0, y: 0, look: {} as never, node: gnode() }
    expect(cardPosition({ node, x: 100, y: 100, width: 800, height: 600 }).left).toBe(118)
    expect(cardPosition({ node, x: 700, y: 100, width: 800, height: 600 }).left).toBe(700 - 18 - 230)
    expect(cardPosition({ node, x: 100, y: 590, width: 800, height: 600 }).top).toBe(600 - 90 - 12)
  })
})

describe('the workspace', () => {
  it('loads the neighbourhood and the panel for the node in the URL', async () => {
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    expect(await screen.findByText('depth 1 · 3 of 3 neighbours shown · ranked by supporting passages')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Focus' })).toBeTruthy()
    expect(calls).toContain('/api/explore/graph/nodes/1/neighbourhood?limit=30')
    expect(calls).toContain('/api/explore/graph/nodes/1')
  })

  it('applies a rail filter as a request and writes it to the URL', async () => {
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    fireEvent.click(screen.getByLabelText(/^Press/))
    await waitFor(() => expect(calls.some((c) => c.includes('tier=press'))).toBe(true))
    expect(window.location.search).toBe('?tier=press')
  })

  it('reads its filters from the URL it was opened with', async () => {
    window.history.replaceState({}, '', '/nodes/1?contested_only=true&view=table')
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    await waitFor(() => expect(calls.some((c) => c.includes('contested_only=true'))).toBe(true))
    expect(screen.getByRole('tab', { name: 'Table' }).getAttribute('aria-selected')).toBe('true')
  })

  it('shows the table view over the same neighbourhood', async () => {
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    fireEvent.click(screen.getByRole('tab', { name: 'Table' }))
    expect(screen.getByRole('columnheader', { name: /Passages/ })).toBeTruthy()
    expect(screen.getAllByRole('row')).toHaveLength(4)
  })

  // Derived from the switcher's own lists, so a view that is built but still
  // listed as unbuilt (or the reverse) fails here instead of being skipped.
  const UNBUILT = VIEWS.filter((v) => !BUILT_VIEWS.has(v)).map((v) => VIEW_LABEL[v])

  it('offers exactly the built views as tabs, and no dead one', async () => {
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    const tabs = screen.getAllByRole('tab').map((t) => t.textContent)
    expect(tabs).toEqual([...BUILT_VIEWS].map((v) => VIEW_LABEL[v]))
    for (const view of UNBUILT) expect(screen.queryByRole('tab', { name: view })).toBeNull()
  })

  it.each(VIEWS.filter((v) => !BUILT_VIEWS.has(v)))('an old link to %s says it is not built rather than faking it', async (view) => {
    window.history.replaceState({}, '', `/nodes/1?view=${view}`)
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    expect(await screen.findByText(`${VIEW_LABEL[view]} is not built yet.`)).toBeTruthy()
    expect(screen.queryByTestId('canvas')).toBeNull()
  })

  it('refocuses by navigating, keeping the filters', async () => {
    window.history.replaceState({}, '', '/nodes/1?tier=press')
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    fireEvent.click(await screen.findByText('canvas N3'))
    expect(window.location.pathname + window.location.search).toBe('/nodes/3?tier=press')
  })

  it('Expand asks for the next page', async () => {
    serve((url) => (url.includes('/neighbourhood') ? { body: hood(3, { shown: 3, total: 40 }) } : undefined))
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 40 neighbours/)
    fireEvent.click(screen.getByText('Expand neighbours'))
    await waitFor(() => expect(calls).toContain('/api/explore/graph/nodes/1/neighbourhood?limit=60'))
  })

  it('says when no neighbour survives the filters, and offers to clear them', async () => {
    window.history.replaceState({}, '', '/nodes/1?tier=informal')
    const empty = hood(0, { unfiltered: 5, total: 0, shown: 0 })
    serve((url) => (url.includes('/neighbourhood') ? { body: empty } : undefined))
    render(<NodePage entityId={1} />)
    expect(await screen.findByText('No neighbour survives these filters. 5 without them.')).toBeTruthy()
    fireEvent.click(screen.getByText('Clear filters'))
    expect(window.location.search).toBe('')
  })

  it('says when nothing is connected at all', async () => {
    serve((url) => (url.includes('/neighbourhood') ? { body: hood(0) } : undefined))
    render(<NodePage entityId={1} />)
    expect(await screen.findByText(/Nothing is connected to Focus yet/)).toBeTruthy()
  })

  it('shows the API sentence for a node that does not exist', async () => {
    serve((url) =>
      url.startsWith('/api/explore/graph/nodes/') ? { status: 404, body: { detail: 'No entity 1.' } } : undefined,
    )
    render(<NodePage entityId={1} />)
    expect((await screen.findAllByText('No entity 1.')).length).toBeGreaterThan(0)
  })

  it('follows a merged node to where it went, replacing the URL', async () => {
    serve((url) => (url.includes('/nodes/1/neighbourhood') ? { body: hood(1, { redirects_to: 8 }) } : undefined))
    render(<NodePage entityId={1} />)
    await waitFor(() => expect(window.location.pathname).toBe('/nodes/8'))
  })

  it('records the focus for the landing and the trail', async () => {
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    expect(JSON.parse(window.localStorage.getItem('meridian.recentNodes')!)[0].id).toBe('1')
    expect(JSON.parse(window.sessionStorage.getItem('meridian.graphTrail')!)).toEqual([{ id: 1, name: 'Focus' }])
  })
})

describe('path mode (P6-03)', () => {
  it('picks a second node on the canvas and draws the route', async () => {
    serve((url) => (url.startsWith('/api/explore/graph/path') ? { body: path() } : undefined))
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    fireEvent.click(screen.getByText('Path from…'))
    expect(screen.getByText(/Path from Focus: click a concept/)).toBeTruthy()
    fireEvent.click(screen.getByText('canvas N3'))
    expect(await screen.findByText('path · 2 hops · Focus › Middle › Far')).toBeTruthy()
    expect(calls).toContain('/api/explore/graph/path?source=1&target=3')
    // Picking did not refocus.
    expect(window.location.pathname).toBe('/nodes/1')
    // The canvas now shows the route, not the neighbourhood.
    expect(screen.getByText('canvas Middle')).toBeTruthy()
    fireEvent.click(screen.getByText('Leave path'))
    expect(screen.queryByText('canvas Middle')).toBeNull()
  })

  it('says a missing route is a gap, not an error', async () => {
    serve((url) =>
      url.startsWith('/api/explore/graph/path')
        ? { body: path({ found: false, hops: null, nodes: [], edges: [] }) }
        : undefined,
    )
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    fireEvent.click(screen.getByText('Path from…'))
    fireEvent.click(screen.getByText('canvas N2'))
    expect(await screen.findByText(/No route within 4 hops/)).toBeTruthy()
  })

  it('Escape leaves path mode', async () => {
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    fireEvent.click(screen.getByText('Path from…'))
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    })
    expect(screen.getByText('Path from…')).toBeTruthy()
  })
})
