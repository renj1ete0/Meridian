/**
 * The graph workspace's pure parts (tasks P6-01–P6-03): URL state, layout,
 * the published canvas styling, the scene, the trail and recent nodes.
 *
 * The styling tests transcribe design-system.md §2's "Graph canvas colours"
 * and §6's contested mark row by row, and read the token names out of
 * `tokens.css` rather than trusting the module that uses them.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { NO_FILTERS } from '../src/explore/graph/api'
import { filtersFromRecord, parseSearch, toSearch, toggle, VIEWS } from '../src/explore/graph/filters'
import { HINT_RADIUS, RING_INNER, RING_OUTER, pathLayout, radialLayout } from '../src/explore/graph/layout'
import { MAX_RECENT, readRecentNodes, recordRecentNode } from '../src/explore/graph/recent'
import { neighbourhoodScene, pathScene } from '../src/explore/graph/scene'
import {
  PALETTE_TOKENS,
  collapseEdges,
  edgeLook,
  neighbourSize,
  nodeLook,
  withAlpha,
  type CanvasPalette,
} from '../src/explore/graph/style'
import { MAX_TRAIL, nextTrail } from '../src/explore/graph/trail'
import { SOURCE_TIERS } from '../src/ui/Tier'
import { gedge, gnode, hood, path } from './graph-fixtures'

const WEB = fileURLToPath(new URL('..', import.meta.url))
const tokensCss = readFileSync(join(WEB, 'src/styles/tokens.css'), 'utf8')

/** Distinct stand-ins, so a test can tell which role a look used. */
const P = Object.fromEntries(
  Object.keys(PALETTE_TOKENS).map((role, i) => [role, `#${String(i + 10).padStart(2, '0')}0000`]),
) as unknown as CanvasPalette

// --------------------------------------------------------------------------
// URL state (P6-02)
// --------------------------------------------------------------------------

describe('the workspace URL', () => {
  it('round-trips every filter and the view', () => {
    const state = {
      filters: {
        topics: ['walk', 'bus'],
        tiers: ['press' as const, 'government' as const],
        publishedFrom: '2020-01-01',
        publishedTo: '2024-12-31',
        contestedOnly: true,
        attribute: 'density',
      },
      view: 'table' as const,
    }
    expect(parseSearch(toSearch(state))).toEqual(state)
  })

  it('is empty when nothing is set', () => {
    expect(toSearch({ filters: NO_FILTERS, view: 'node-link' })).toBe('')
  })

  it('drops a tier the database does not know', () => {
    expect(parseSearch('?tier=tabloid&tier=press').filters.tiers).toEqual(['press'])
  })

  it('drops a malformed date rather than sending it', () => {
    expect(parseSearch('?published_from=last-year').filters.publishedFrom).toBeNull()
  })

  it('drops the bound that makes a range reversed, not the whole filter set', () => {
    const f = parseSearch('?published_from=2024-01-01&published_to=2020-01-01&tier=press').filters
    expect([f.publishedFrom, f.publishedTo, f.tiers]).toEqual([null, '2020-01-01', ['press']])
  })

  it('falls back to node-link for an unknown view', () => {
    expect(parseSearch('?view=hairball').view).toBe('node-link')
    expect(VIEWS[0]).toBe('node-link')
  })

  it('reads a saved view written by the search landing', () => {
    // The landing saves `{ topic: [...] }`; the workspace must honour it.
    expect(filtersFromRecord({ topic: ['walk'] })).toEqual({ ...NO_FILTERS, topics: ['walk'] })
    expect(filtersFromRecord({ tier: SOURCE_TIERS.slice(0, 2) }).tiers).toEqual(SOURCE_TIERS.slice(0, 2))
  })

  it('toggle adds and removes, keeping order', () => {
    expect(toggle(['a', 'b'], 'c')).toEqual(['a', 'b', 'c'])
    expect(toggle(['a', 'b', 'c'], 'b')).toEqual(['a', 'c'])
  })
})

// --------------------------------------------------------------------------
// Layout (P6-01)
// --------------------------------------------------------------------------

describe('the radial layout', () => {
  const dist = (p: { x: number; y: number }) => Math.hypot(p.x, p.y)

  it('puts the focus at the centre and neighbours on the ring', () => {
    const h = hood(6)
    const at = radialLayout(h.nodes, h.edges)
    expect(at.get(1)).toEqual({ x: 0, y: 0 })
    for (const n of h.nodes.slice(1)) expect(dist(at.get(n.entity_id)!)).toBeCloseTo(RING_OUTER)
  })

  it('staggers a crowded ring so adjacent labels do not collide', () => {
    const h = hood(20)
    const at = radialLayout(h.nodes, h.edges)
    const radii = h.nodes.slice(1).map((n) => dist(at.get(n.entity_id)!))
    expect(new Set(radii.map((r) => r.toFixed(2)))).toEqual(new Set([RING_OUTER.toFixed(2), RING_INNER.toFixed(2)]))
  })

  it('never stacks two neighbours on one spot, even at the cap', () => {
    const h = hood(100)
    const points = [...radialLayout(h.nodes, h.edges).values()]
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        expect(Math.hypot(points[i]!.x - points[j]!.x, points[i]!.y - points[j]!.y)).toBeGreaterThan(0.03)
      }
    }
  })

  it('is deterministic', () => {
    const h = hood(9)
    expect(radialLayout(h.nodes, h.edges)).toEqual(radialLayout(h.nodes, h.edges))
  })

  it('hangs a hint outside the neighbour it joins', () => {
    const base = hood(4)
    const hint = gnode({ entity_id: 50, role: 'hint', support: 0 })
    const nodes = [...base.nodes, hint]
    const edges = [...base.edges, gedge({ edge_id: 900, from_node: 50, to_node: 3, kind: 'hint' })]
    const at = radialLayout(nodes, edges)
    const h = at.get(50)!
    const anchor = at.get(3)!
    expect(dist(h)).toBeCloseTo(HINT_RADIUS)
    // Nearer its own neighbour than any other.
    const nearest = base.nodes
      .slice(1)
      .map((n) => ({ id: n.entity_id, d: Math.hypot(at.get(n.entity_id)!.x - h.x, at.get(n.entity_id)!.y - h.y) }))
      .sort((a, b) => a.d - b.d)[0]!
    expect(nearest.id).toBe(3)
    expect(dist(h)).toBeGreaterThan(dist(anchor))
  })

  it('places an orphaned hint somewhere other than on the focus', () => {
    const base = hood(2)
    const nodes = [...base.nodes, gnode({ entity_id: 60, role: 'hint' })]
    expect(dist(radialLayout(nodes, base.edges).get(60)!)).toBeCloseTo(HINT_RADIUS)
  })

  it('lays a path out left to right in order', () => {
    const at = pathLayout([1, 7, 9])
    expect(at.get(1)!.x).toBe(-1)
    expect(at.get(9)!.x).toBe(1)
    expect(at.get(7)!.x).toBeCloseTo(0)
  })
})

// --------------------------------------------------------------------------
// Styling — design-system.md §2 and §6 (P6-01)
// --------------------------------------------------------------------------

describe('the canvas palette comes from the token file', () => {
  it.each(Object.entries(PALETTE_TOKENS))('%s is a defined token (%s)', (_role, token) => {
    // Drift: a renamed token would give WebGL an empty string, which it draws
    // as black — invisible on this ground and failing nowhere else.
    expect(tokensCss).toMatch(new RegExp(`^\\s*${token}:\\s*#[0-9A-Fa-f]{6};`, 'm'))
  })

  it('reads dark palette entries only: the canvas has no light column', () => {
    for (const token of Object.values(PALETTE_TOKENS)) expect(token.startsWith('--dark-')).toBe(true)
  })
})

describe('node looks, per the published table', () => {
  it('focus: r 13 with a 45% ring at r 26', () => {
    const look = nodeLook(gnode({ role: 'focus' }), P, { maxSupport: 3 })
    expect([look.size, look.color, look.ring]).toEqual([13, P.focus, { radius: 26, color: P.focus, opacity: 0.45 }])
  })

  it('contested: r 8 in brass, a 60% ring, and the dagger', () => {
    const look = nodeLook(gnode({ contested: true, node_type: 'finding' }), P, { maxSupport: 3 })
    expect([look.size, look.color, look.ring?.opacity, look.dagger]).toEqual([8, P.contested, 0.6, true])
    expect(look.caption).toBe('finding · contested')
  })

  it('cross-topic: the accent at 75%, captioned', () => {
    const look = nodeLook(gnode({ cross_topic: true }), P, { maxSupport: 3 })
    expect(look.color).toBe(withAlpha(P.focus, 0.75))
    expect(look.caption).toBe('cross-topic')
  })

  it('neighbours: r 6.5 to 8 by support', () => {
    expect(neighbourSize(1, 5)).toBe(6.5)
    expect(neighbourSize(5, 5)).toBe(8)
    expect(neighbourSize(1, 1)).toBe(7)
  })

  it('hints: a small unlabelled dot', () => {
    const look = nodeLook(gnode({ role: 'hint' }), P, { maxSupport: 1 })
    expect([look.size, look.label, look.color]).toEqual([3, null, P.hint])
  })

  it('§6: brass never appears without the dagger, and the dagger never without brass', () => {
    const cases = [
      gnode({ role: 'focus' }),
      gnode({ contested: true }),
      gnode({ cross_topic: true }),
      gnode({ contested: true, cross_topic: true }),
      gnode(),
    ]
    for (const node of cases.filter((n) => n.role !== 'focus')) {
      const look = nodeLook(node, P, { maxSupport: 2 })
      const brass = look.color === P.contested || look.labelColor === P.contested
      expect(brass, node.canonical_name).toBe(look.dagger)
    }
    // The focus carries the dagger when any of its edges is contested.
    expect(nodeLook(gnode({ role: 'focus' }), P, { maxSupport: 1, focusContested: true }).dagger).toBe(true)
  })
})

describe('edge looks, per the published table', () => {
  it.each([
    ['focus', false, 1.6, 'edgeFocus'],
    ['between', false, 1.2, 'edgeBetween'],
    ['hint', false, 1, 'edgeHint'],
    ['focus', true, 1.8, 'edgeContested'],
  ] as const)('%s, contested %s: %s wide', (kind, contested, size, role) => {
    const look = edgeLook(gedge({ kind, contested }), P)
    expect([look.size, look.color]).toEqual([size, P[role]])
  })

  it('a cross-topic edge from the focus is dashed', () => {
    expect(edgeLook(gedge(), P, { crossTopic: true }).dashed).toBe(true)
    expect(edgeLook(gedge(), P).dashed).toBe(false)
  })

  it('draws one line per pair and lets a contested edge win', () => {
    const plain = gedge({ edge_id: 1, support: 9 })
    const contested = gedge({ edge_id: 2, from_node: 2, to_node: 1, contested: true, support: 1 })
    expect(collapseEdges([plain, contested]).map((e) => e.edge_id)).toEqual([2])
  })

  it('withAlpha converts a token hex and leaves anything else alone', () => {
    expect(withAlpha('#' + '49D0DA', 0.75)).toBe('rgba(73, 208, 218, 0.75)')
    expect(withAlpha('', 0.5)).toBe('')
  })
})

describe('scenes', () => {
  it('dashes the focus edge to a cross-topic neighbour', () => {
    const h = hood(2)
    h.nodes[1] = { ...h.nodes[1]!, cross_topic: true }
    const scene = neighbourhoodScene(h, P)
    const dashed = scene.edges.filter((e) => e.look.dashed).map((e) => e.target)
    expect(dashed).toEqual([h.nodes[1]!.entity_id])
  })

  it('draws the meridian for a neighbourhood and not for a path', () => {
    expect(neighbourhoodScene(hood(2), P).meridian).toBe(true)
    expect(pathScene(path(), P).meridian).toBe(false)
  })

  it('a path labels its edges and colours every edge on it', () => {
    const scene = pathScene(path(), P)
    expect(scene.edgeLabels).toBe(true)
    expect(scene.edges.every((e) => e.look.color === P.focus)).toBe(true)
    expect(scene.nodes.map((n) => n.id)).toEqual([1, 7, 9])
  })
})

// --------------------------------------------------------------------------
// Trail and recent nodes
// --------------------------------------------------------------------------

describe('the breadcrumb trail', () => {
  it('appends a new node', () => {
    expect(nextTrail([{ id: 1, name: 'a' }], { id: 2, name: 'b' }).map((c) => c.id)).toEqual([1, 2])
  })

  it('cuts back to a node already on it, rather than looping', () => {
    const trail = [1, 2, 3].map((id) => ({ id, name: String(id) }))
    expect(nextTrail(trail, { id: 2, name: '2' }).map((c) => c.id)).toEqual([1, 2])
  })

  it('is capped', () => {
    let trail: { id: number; name: string }[] = []
    for (let id = 0; id < MAX_TRAIL + 5; id++) trail = nextTrail(trail, { id, name: '' })
    expect(trail).toHaveLength(MAX_TRAIL)
    expect(trail.at(-1)!.id).toBe(MAX_TRAIL + 4)
  })
})

describe('recent nodes for the landing', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function memoryStorage() {
    const data = new Map<string, string>()
    return {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    }
  }

  it('keeps one entry per node, most recent first, capped', () => {
    vi.stubGlobal('window', { localStorage: memoryStorage() })
    for (let id = 1; id <= MAX_RECENT + 2; id++) {
      recordRecentNode({ entity_id: id, canonical_name: `n${id}`, node_type: 'place' })
    }
    recordRecentNode({ entity_id: 5, canonical_name: 'n5', node_type: 'place', contested: true })
    const recent = readRecentNodes()
    expect(recent).toHaveLength(MAX_RECENT)
    expect(recent[0]).toEqual({ id: '5', name: 'n5', nodeType: 'place', contested: true })
    expect(recent.filter((n) => n.id === '5')).toHaveLength(1)
  })

  it('maps a type with no glyph to the generic one', () => {
    vi.stubGlobal('window', { localStorage: memoryStorage() })
    recordRecentNode({ entity_id: 1, canonical_name: 'x', node_type: 'failure_mode' })
    expect(readRecentNodes()[0]!.nodeType).toBe('concept')
  })

  it('survives storage that throws, as a private window does', () => {
    vi.stubGlobal('window', {
      get localStorage(): Storage {
        throw new Error('SecurityError')
      },
    })
    expect(() => recordRecentNode({ entity_id: 1, canonical_name: 'x', node_type: 'place' })).not.toThrow()
    expect(readRecentNodes()).toEqual([])
  })
})
