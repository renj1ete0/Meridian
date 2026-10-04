/**
 * The Map as a map (task B-93): the wheel zooms the view about the pointer,
 * a drag pans it, the level of detail follows the magnification, and lines
 * are drawn at every level rather than only the root's.
 *
 * The helpers are pure, so the properties are checked directly: the point
 * under the pointer stays put, the map never comes away from the frame's
 * edges, the level for a zoom and the zoom for a level agree, and every
 * loaded parent's lines are found — and none of an unloaded one's.
 */
import { describe, expect, it } from 'vitest'

import { wheelFactor } from '../src/explore/map/Zoom'
import {
  HOME,
  MAX_ZOOM,
  ZOOM_FOR_DEPTH,
  clampView,
  depthForZoom,
  fitLabels,
  linksAt,
  routeBetween,
  researchShare,
  shadeOf,
  onScreen,
  panBy,
  viewed,
  zoomAbout,
  zoomForDepth,
  type AreaLink,
  type AreasLevel,
  type Placed,
} from '../src/lib/areas'
import { area } from './areas-fixtures'

const W = 1000
const H = 600

function link(a: number, b: number, cited = 0): AreaLink {
  return {
    area_a: a,
    area_b: b,
    cited_claims: cited,
    cited_sources: cited,
    similar_pairs: 1,
    similarity: 0.9,
    shared_terms: [],
  }
}

function level(over: Partial<AreasLevel>): AreasLevel {
  return {
    build: { build_id: 1, computed_at: '2026-09-27T00:00:00Z', passages: 100, regions: 2, areas: 4, leaves: 4 },
    level: 1,
    parent: null,
    path: [],
    areas: [],
    links: [],
    levels: 3,
    passages_needed: 20,
    stale_after_days: 180,
    weak_below_sources: 3,
    ...over,
  }
}

describe('the level follows the zoom', () => {
  it('agrees both ways at every level there is', () => {
    for (let root = 1; root <= 3; root++) {
      for (let depth = root; depth <= 3; depth++) {
        expect(depthForZoom(zoomForDepth(depth, root), root, 3)).toBe(depth)
      }
    }
  })

  it('only ever deepens as the zoom grows, and never past the levels there are', () => {
    let last = 0
    for (let k = 1; k <= MAX_ZOOM; k += 0.05) {
      const depth = depthForZoom(k, 1, 2)
      expect(depth).toBeGreaterThanOrEqual(last)
      expect(depth).toBeLessThanOrEqual(2)
      last = depth
    }
    expect(depthForZoom(MAX_ZOOM, 3, 3)).toBe(3)
  })

  it('shows the root at the whole map, and each finer level only once zoomed', () => {
    expect(depthForZoom(1, 1, 3)).toBe(1)
    expect(depthForZoom(ZOOM_FOR_DEPTH[1]! - 0.01, 1, 3)).toBe(1)
    expect(ZOOM_FOR_DEPTH[0]).toBe(1)
  })
})

describe('zooming and panning', () => {
  it('keeps the point under the pointer where it is', () => {
    const view = zoomAbout(HOME, 2, 400, 250, W, H)
    // The base point that was under (400, 250) is still there.
    const base = { x: 400, y: 250 }
    expect(base.x * view.k + view.x).toBeCloseTo(400)
    expect(base.y * view.k + view.y).toBeCloseTo(250)
  })

  it('never zooms out past the whole map nor in past the limit', () => {
    expect(zoomAbout(HOME, 0.2, 10, 10, W, H)).toEqual(HOME)
    expect(zoomAbout(HOME, 1000, 10, 10, W, H).k).toBe(MAX_ZOOM)
  })

  it('never lets an edge of the map come away from the frame', () => {
    const zoomed = zoomAbout(HOME, 3, W / 2, H / 2, W, H)
    for (const [dx, dy] of [
      [5000, 0],
      [-5000, 0],
      [0, 5000],
      [0, -5000],
    ] as const) {
      const v = panBy(zoomed, dx, dy, W, H)
      expect(v.x).toBeLessThanOrEqual(0)
      expect(v.y).toBeLessThanOrEqual(0)
      expect(v.x + W * v.k).toBeGreaterThanOrEqual(W - 1e-9)
      expect(v.y + H * v.k).toBeGreaterThanOrEqual(H - 1e-9)
    }
  })

  it('does not pan the whole map at all', () => {
    expect(panBy(HOME, 300, -200, W, H)).toEqual(HOME)
    expect(clampView({ k: 1, x: 40, y: -40 }, W, H)).toEqual(HOME)
  })

  it('draws a circle where the view puts it, and only if any of it is on screen', () => {
    const view = { k: 2, x: -100, y: -50 }
    expect(viewed({ x: 100, y: 100, r: 10 }, view)).toEqual({ x: 100, y: 150, r: 20 })
    expect(onScreen({ x: -15, y: 10, r: 20 }, W, H)).toBe(true)
    expect(onScreen({ x: -25, y: 10, r: 20 }, W, H)).toBe(false)
    expect(onScreen({ x: 500, y: H + 19, r: 20 }, W, H)).toBe(true)
  })
})

describe('the wheel', () => {
  it('zooms in towards the reader and out away, a pinch more strongly', () => {
    expect(wheelFactor(-100, 0, false)).toBeGreaterThan(1)
    expect(wheelFactor(100, 0, false)).toBeLessThan(1)
    expect(wheelFactor(-10, 0, true)).toBeGreaterThan(wheelFactor(-10, 0, false))
  })

  it('reads line and page deltas as more travel than pixels, and caps a fling', () => {
    expect(wheelFactor(-3, 1, false)).toBeGreaterThan(wheelFactor(-3, 0, false))
    expect(wheelFactor(-100000, 0, false)).toBe(wheelFactor(-300, 0, false))
  })
})

describe('lines at every level', () => {
  const root = level({
    areas: [area({ area_id: 1, children: 2 }), area({ area_id: 2, children: 2 }), area({ area_id: 3, children: 0 })],
    links: [link(1, 2, 1)],
  })
  const byParent = new Map([
    [1, level({ level: 2, areas: [area({ area_id: 11 }), area({ area_id: 12 })], links: [link(11, 12)] })],
  ])

  it('is the root’s own lines at the root’s level', () => {
    expect(linksAt(root, byParent, 1)).toEqual(root.links)
  })

  it('is every loaded parent’s lines between its children below it, and none of the root’s', () => {
    const both = new Map(byParent)
    both.set(2, level({ level: 2, areas: [area({ area_id: 21 }), area({ area_id: 22 })], links: [link(21, 22, 2)] }))
    const found = linksAt(root, both, 2).map((l) => `${l.area_a}-${l.area_b}`)
    expect(found.sort()).toEqual(['11-12', '21-22'])
  })

  it('finds nothing for a parent not yet loaded, rather than failing', () => {
    expect(linksAt(root, byParent, 2).map((l) => l.area_a)).toEqual([11])
  })
})

describe('names inside a circle', () => {
  it('names a mid-sized circle whose one long word would not fit at the usual sizes', () => {
    const name = 'Transportation & Automotive Engineering'
    const p: Placed = { area: area({ area_id: 1, name }), x: 200, y: 200, r: 34 }
    // A neighbour close below, so a name hung under the circle is refused.
    const below: Placed = { area: area({ area_id: 2, name: 'x' }), x: 200, y: 262, r: 26 }
    const labels = fitLabels([p, below], { width: W, captions: false, crowded: true })
    const label = labels.get(1)
    expect(label?.inside).toBe(true)
    // The start of the name, cut where it had to be, and inside the rim.
    expect(label!.lines[0]).toMatch(/^Transport\S*…?$/)
    expect(label!.lines.join(' ')).toContain('…')
    expect(label!.box.w).toBeLessThanOrEqual(p.r * 2)
  })
})

describe('a route between fields (P6-39)', () => {
  const L = (a: number, b: number, cited = 0, sources = cited) => ({ ...link(a, b, cited), cited_sources: sources })

  it('takes the fewest hops, then the fewest by resemblance only', () => {
    const links = [L(1, 2), L(2, 4), L(1, 3, 1), L(3, 4, 2), L(1, 5), L(5, 6), L(6, 4, 3)]
    const route = routeBetween(links, 1, 4)!
    expect(route.map((h) => [h.area_a, h.area_b])).toEqual([
      [1, 3],
      [3, 4],
    ])
  })

  it('never prefers a longer route of claims to a shorter one of resemblance', () => {
    const links = [L(1, 4), L(1, 2, 5), L(2, 3, 5), L(3, 4, 5)]
    expect(routeBetween(links, 1, 4)!.length).toBe(1)
  })

  it('among equal routes, the one with more sources behind its claims', () => {
    const links = [L(1, 2, 1, 1), L(2, 4, 1, 1), L(1, 3, 1, 9), L(3, 4, 1, 9)]
    expect(routeBetween(links, 1, 4)!.map((h) => h.area_b)).toEqual([3, 4])
  })

  it('orients each hop along the way, whichever way the link was stored', () => {
    const route = routeBetween([L(2, 1, 1), L(3, 2, 1)], 1, 3)!
    expect(route.map((h) => [h.area_a, h.area_b])).toEqual([
      [1, 2],
      [2, 3],
    ])
  })

  it('says null when nothing joins them, and nothing to walk from a place to itself', () => {
    expect(routeBetween([L(1, 2), L(3, 4)], 1, 4)).toBeNull()
    expect(routeBetween([], 7, 7)).toEqual([])
  })
})

describe('fill by research share (P6-42)', () => {
  it('is the peer-reviewed share of counted passages, government not included', () => {
    expect(researchShare({ tier_mix: { government: 60, peer_reviewed: 30, informal: 10 } })).toBeCloseTo(0.3)
    expect(researchShare({ tier_mix: { government: 100 } })).toBe(0)
  })

  it('refuses to guess with nothing counted', () => {
    expect(researchShare({ tier_mix: {} })).toBeNull()
  })

  it('switches what the fill reads without touching the topic share', () => {
    const a = area({ area_id: 1, examined: 10, on_topic: 9, tier_mix: { peer_reviewed: 1, government: 9 } })
    expect(shadeOf(a, 'topic')).toBeCloseTo(0.9)
    expect(shadeOf(a, 'research')).toBeCloseTo(0.1)
  })
})
