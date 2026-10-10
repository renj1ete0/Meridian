// @vitest-environment jsdom
/**
 * The workspace's adjacency matrix (§12.3: "which clusters are dense? where
 * are the gaps?").
 *
 * The view's whole value is that an empty cell *means* something — a pair the
 * graph has no relation for — so most of this file pins what the matrix
 * counts and what it refuses to count: hint edges, edges to nodes the cap or a
 * filter removed, and the same edge listed twice. A matrix that quietly
 * counted any of those would draw a density that is not there.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { GraphCanvasProps } from '../src/explore/graph/GraphCanvas'
import {
  MatrixView,
  SHADE_CEILING,
  SHADE_FLOOR,
  adjacency,
  cellSize,
  matrixLine,
  pairKey,
  shade,
} from '../src/explore/graph/MatrixView'
import { BUILT_VIEWS, VIEW_LABEL } from '../src/explore/graph/filters'
import { NodePage } from '../src/explore/NodePage'
import { DAGGER } from '../src/ui/Contested'
import { detail, gedge, gnode, hood } from './graph-fixtures'

vi.mock('../src/explore/graph/GraphCanvas', () => ({
  GraphCanvas: (props: GraphCanvasProps) => <div data-testid="canvas">{props.scene.nodes.length}</div>,
}))

/**
 * Focus 1; neighbours 2, 3, 4; a hint 9 beyond neighbour 2.
 *
 * - 1–2: two relations, one each way.
 * - 1–3: one, contested.
 * - 1–4: one.
 * - 2–3: one "between" edge.
 * - 2–9: a hint edge, which the matrix must not count.
 * - 3–7: an edge to a node the API did not return (capped out).
 */
function mixed() {
  const base = hood(3)
  const hint = gnode({ entity_id: 9, canonical_name: 'Beyond', role: 'hint', support: 0 })
  // The API sets a neighbour's `contested` when an edge to the focus is.
  const nodes = base.nodes.map((n) => (n.entity_id === 3 ? { ...n, contested: true } : n))
  return {
    ...base,
    nodes: [...nodes, hint],
    edges: [
      gedge({ edge_id: 10, from_node: 1, to_node: 2, relation_type: 'reduces', support: 3 }),
      gedge({ edge_id: 11, from_node: 2, to_node: 1, relation_type: 'depends_on', support: 2 }),
      gedge({ edge_id: 12, from_node: 1, to_node: 3, contested: true, contested_with: [99] }),
      gedge({ edge_id: 13, from_node: 4, to_node: 1 }),
      gedge({ edge_id: 14, from_node: 2, to_node: 3, kind: 'between', relation_type: 'part_of' }),
      gedge({ edge_id: 15, from_node: 2, to_node: 9, kind: 'hint' }),
      gedge({ edge_id: 16, from_node: 3, to_node: 7, kind: 'between' }),
    ],
  }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('what the matrix counts', () => {
  it('is the focus and its shown neighbours, focus first, in the ranked order', () => {
    const m = adjacency(mixed())
    expect(m.nodes.map((n) => n.entity_id)).toEqual([1, 2, 3, 4])
  })

  it('leaves second-hop hints out, rows and edges both', () => {
    const m = adjacency(mixed())
    expect(m.nodes.some((n) => n.role === 'hint')).toBe(false)
    expect([...m.cells.keys()].some((k) => k.split(':').includes('9'))).toBe(false)
  })

  it('counts a pair in both directions in one cell', () => {
    const m = adjacency(mixed())
    const cell = m.cells.get(pairKey(2, 1))!
    expect(cell.edges.map((e) => e.edge_id)).toEqual([10, 11])
    expect(cell.support).toBe(5)
    expect(m.cells.get(pairKey(1, 2))).toBe(cell)
  })

  it('counts between-neighbour edges, which the table cannot show', () => {
    expect(adjacency(mixed()).cells.get(pairKey(3, 2))?.edges).toHaveLength(1)
  })

  it('drops an edge whose far end the API did not return', () => {
    const m = adjacency(mixed())
    expect(m.cells.has(pairKey(3, 7))).toBe(false)
    // 10, 11, 12, 13, 14 — not the hint (15) and not the capped-out end (16).
    expect(m.edges).toBe(5)
  })

  it('does not double count one edge listed twice', () => {
    const h = mixed()
    const doubled = { ...h, edges: [...h.edges, h.edges[0]!] }
    expect(adjacency(doubled).cells.get(pairKey(1, 2))?.edges).toHaveLength(2)
    expect(adjacency(doubled).edges).toBe(5)
  })

  it('reports joined, possible and contested pairs', () => {
    const m = adjacency(mixed())
    expect(m.possible).toBe(6) // 4 nodes: 4·3/2
    expect(m.joined).toBe(4) // 1–2, 1–3, 1–4, 2–3; 2–4, 3–4 are gaps
    expect(m.contested).toBe(1)
    expect(m.maxEdges).toBe(2)
  })

  it('says what it holds, and when the cap is hiding neighbours', () => {
    const h = { ...mixed(), total: 10, shown: 3 }
    expect(matrixLine(adjacency(h), h)).toBe('4 nodes · 4 of 6 pairs joined · 1 contested · 3 of 10 neighbours shown')
    const all = mixed()
    expect(matrixLine(adjacency(all), all)).toBe('4 nodes · 4 of 6 pairs joined · 1 contested')
  })

  it('is only the focus when nothing survives the filters', () => {
    const m = adjacency(hood(0))
    expect(m.nodes).toHaveLength(1)
    expect(m.possible).toBe(0)
    expect(m.joined).toBe(0)
    expect(m.cells.size).toBe(0)
  })
})

describe('shading and size', () => {
  it('never shades a relation below the floor, and never shades a gap', () => {
    expect(shade(0, 5)).toBe(0)
    expect(shade(1, 1)).toBe(SHADE_FLOOR)
    expect(shade(1, 5)).toBe(SHADE_FLOOR)
    expect(shade(5, 5)).toBe(SHADE_CEILING)
    const steps = [1, 2, 3, 4, 5].map((n) => shade(n, 5))
    expect([...steps].sort((a, b) => a - b)).toEqual(steps)
    expect(new Set(steps).size).toBe(steps.length)
  })

  it('shrinks cells as the neighbourhood grows toward the ceiling', () => {
    expect(cellSize(31)).toBeGreaterThan(cellSize(61))
    expect(cellSize(61)).toBeGreaterThan(cellSize(101))
  })
})

describe('the view', () => {
  function show(h = mixed()) {
    const onPick = vi.fn()
    render(<MatrixView hood={h} onPick={onPick} hrefFor={(id) => `/nodes/${id}?view=matrix`} />)
    return onPick
  }

  it('draws one row and one column per node, and a button only where a relation is', () => {
    show()
    const table = screen.getByRole('table', { name: 'Adjacency matrix' })
    expect(within(table).getAllByRole('rowheader')).toHaveLength(4)
    expect(within(table).getAllByRole('columnheader')).toHaveLength(4)
    // Four joined pairs, drawn on both sides of the diagonal.
    expect(within(table).getAllByRole('button')).toHaveLength(8)
    expect(screen.getAllByRole('button', { name: /^N2 and Focus: 2 relations$/ })).toHaveLength(1)
    expect(screen.queryByRole('button', { name: /^N3 and N4/ })).toBeNull()
  })

  it('marks a contested pair with the dagger as well as the brass', () => {
    // §6: strip the colour and the reading must survive.
    show()
    const cell = screen.getByRole('button', { name: 'Focus and N3: 1 relation, contested' })
    expect(cell.textContent).toContain(DAGGER)
    expect(cell.className).toContain('text-accent-attention')
    for (const plain of screen.getAllByRole('button').filter((b) => !b.hasAttribute('data-contested'))) {
      expect(plain.textContent).not.toContain(DAGGER)
      expect(plain.className).not.toContain('accent-attention')
    }
  })

  it('spells out each relation of a pair, with its direction, on hover', () => {
    show()
    fireEvent.mouseEnter(screen.getByRole('button', { name: /^Focus and N2/ }))
    const readout = screen.getByText(/2 relations · 5 passages/).parentElement!
    expect(readout.textContent).toContain('Focus reduces N2')
    expect(readout.textContent).toContain('N2 depends on Focus')
  })

  it('refocuses from a label, but leaves a modified click to the browser', () => {
    const onPick = show()
    const [label] = screen.getAllByRole('link', { name: /^N3/ })
    // The node is contested, so its label carries the dagger too.
    expect(label!.textContent).toBe(`N3${DAGGER}`)
    fireEvent.click(label!, { button: 0 })
    expect(onPick).toHaveBeenCalledWith(3)
    onPick.mockClear()
    fireEvent.click(label!, { button: 0, metaKey: true })
    expect(onPick).not.toHaveBeenCalled()
    expect(label!.getAttribute('href')).toBe('/nodes/3?view=matrix')
  })
})

// --------------------------------------------------------------------------
// In the workspace

let calls: string[] = []

function serve(route: (url: string) => { status?: number; body: unknown } | undefined) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      calls.push(url)
      const found = route(url) ?? defaults(url)
      return new Response(JSON.stringify(found.body), { status: found.status ?? 200 })
    }),
  )
}

function defaults(url: string): { status?: number; body: unknown } {
  if (url.startsWith('/api/explore/views')) return { body: { views: [] } }
  if (url.includes('/neighbourhood')) return { body: mixed() }
  if (url.startsWith('/api/explore/graph/nodes/')) return { body: detail() }
  return { status: 404, body: { detail: `unrouted ${url}` } }
}

describe('the matrix in the workspace', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/nodes/1?view=matrix')
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it('opens from the URL and says what it holds', async () => {
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    expect(await screen.findByRole('table', { name: 'Adjacency matrix' })).toBeTruthy()
    expect(screen.getByText('4 nodes · 4 of 6 pairs joined · 1 contested')).toBeTruthy()
    expect(screen.queryByTestId('canvas')).toBeNull()
  })

  it('follows the filter rail: a narrower neighbourhood is a smaller matrix', async () => {
    const narrow = hood(1, { total: 1, shown: 1, unfiltered: 3 })
    serve((url) => (url.includes('/neighbourhood') && url.includes('tier=') ? { body: narrow } : undefined))
    render(<NodePage entityId={1} />)
    await screen.findByRole('table', { name: 'Adjacency matrix' })
    expect(screen.getAllByRole('rowheader')).toHaveLength(4)

    await act(async () => {
      fireEvent.click(screen.getByRole('checkbox', { name: /Government/ }))
    })
    await screen.findByText('2 nodes · 1 of 1 pairs joined')
    expect(screen.getAllByRole('rowheader')).toHaveLength(2)
    expect(calls.some((c) => c.includes('/neighbourhood') && c.includes('tier=government'))).toBe(true)
    expect(window.location.search).toContain('view=matrix')
  })

  it('shows the honest empty state, not an empty square, when nothing survives', async () => {
    window.history.replaceState({}, '', '/nodes/1?view=matrix&tier=informal')
    const empty = hood(0, { unfiltered: 5, total: 0, shown: 0 })
    serve((url) => (url.includes('/neighbourhood') ? { body: empty } : undefined))
    render(<NodePage entityId={1} />)
    expect(await screen.findByText('No neighbour survives these filters. 5 without them.')).toBeTruthy()
    expect(screen.queryByRole('table', { name: 'Adjacency matrix' })).toBeNull()
  })

  it.each([...BUILT_VIEWS].map((v) => VIEW_LABEL[v]))('%s, a built view, never says it is unbuilt', async (label) => {
    window.history.replaceState({}, '', '/nodes/1')
    serve(() => undefined)
    render(<NodePage entityId={1} />)
    await screen.findByText(/3 of 3 neighbours/)
    fireEvent.click(screen.getByRole('tab', { name: label }))
    expect(screen.queryByText(/is not built yet/)).toBeNull()
  })
})

describe('the table names its two scopes (B-208)', () => {
  it('says which counts are about this link and which are the neighbour’s own', async () => {
    const { readFileSync } = await import('node:fs')
    const { join } = await import('node:path')
    const source = readFileSync(join(__dirname, '..', 'src', 'explore', 'graph', 'TableView.tsx'), 'utf8')
    for (const label of ['Passages here', 'Its links', 'Its sources']) expect(source).toContain(`label: '${label}'`)
  })
})
