/**
 * The Map's semantic zoom (task P6-42): a finer level is laid out inside the
 * circles of the coarser one, nothing the root holds drops out of view at any
 * depth, and the split animation starts inside the parent and ends where the
 * finer level draws each child.
 */
import { describe, expect, it, vi } from 'vitest'

import {
  AreaCache,
  CHILD_SPREAD,
  ancestorAt,
  composeChild,
  easeInOut,
  enclosing,
  fitLabels,
  levelFromSearch,
  loadedTo,
  morphAt,
  placeNested,
  relax,
  rowsAt,
  splitMorph,
  wrapWords,
  zoomCaption,
  type Area,
  type AreasLevel,
  type Layout,
  type Placed,
} from '../src/lib/areas'
import { area } from './areas-fixtures'

function level(areas: Area[], over: Partial<AreasLevel> = {}): AreasLevel {
  return {
    build: null,
    level: areas[0]?.level ?? 1,
    parent: null,
    path: [],
    areas,
    links: [],
    levels: 3,
    passages_needed: 0,
    stale_after_days: 0,
    weak_below_sources: 0,
    ...over,
  }
}

/** Two roots with children, one leaf root; children two levels deep under the first. */
function tree() {
  const roots = [
    area({ area_id: 1, level: 1, children: 2, passages: 400 }),
    area({ area_id: 2, level: 1, children: 1, passages: 100 }),
    area({ area_id: 3, level: 1, children: 0, passages: 50 }),
  ]
  const under1 = [
    area({ area_id: 11, level: 2, parent_id: 1, children: 2, passages: 300, x: -0.8, y: 0.5 }),
    area({ area_id: 12, level: 2, parent_id: 1, children: 0, passages: 100, x: 0.9, y: -0.9 }),
  ]
  const under2 = [area({ area_id: 21, level: 2, parent_id: 2, children: 0, passages: 100, x: 0, y: 0 })]
  const under11 = [
    area({ area_id: 111, level: 3, parent_id: 11, children: 0, passages: 200, x: -1, y: -1 }),
    area({ area_id: 112, level: 3, parent_id: 11, children: 0, passages: 100, x: 1, y: 1 }),
  ]
  const byParent = new Map<number, AreasLevel>([
    [1, level(under1)],
    [2, level(under2)],
    [11, level(under11)],
  ])
  return { root: level(roots), byParent, all: [...roots, ...under1, ...under2, ...under11] }
}

const ids = (rows: readonly Area[]) => rows.map((a) => a.area_id)
const total = (rows: readonly Area[]) => rows.reduce((n, a) => n + a.passages, 0)

describe('composeChild', () => {
  it('keeps every child centre inside its parent, even from a square corner', () => {
    const parent = { x: 200, y: 100, r: 50 }
    for (const [cx, cy] of [
      [1, 1],
      [-1, 1],
      [1, -1],
      [-1, -1],
      [0.3, -0.2],
      [5, 0],
    ] as const) {
      const at = composeChild(parent, { x: cx, y: cy })
      expect(Math.hypot(at.x - parent.x, at.y - parent.y)).toBeLessThanOrEqual(parent.r * CHILD_SPREAD + 1e-9)
    }
  })

  it('flips y: the build grows upwards, the screen downwards', () => {
    const at = composeChild({ x: 0, y: 0, r: 10 }, { x: 0, y: 0.5 })
    expect(at.y).toBeLessThan(0)
  })
})

describe('rowsAt and loadedTo', () => {
  it('holds everything the root holds at every depth, whatever is loaded', () => {
    const { root, byParent } = tree()
    for (const depth of [1, 2, 3, 4]) {
      expect(total(rowsAt(root, byParent, depth))).toBe(total(root.areas))
    }
    // Nothing below the roots loaded: the roots stand in for their children.
    expect(ids(rowsAt(root, new Map(), 3))).toEqual([1, 2, 3])
  })

  it('descends in API order, a leaf standing in for its own absent children', () => {
    const { root, byParent } = tree()
    expect(ids(rowsAt(root, byParent, 2))).toEqual([11, 12, 21, 3])
    expect(ids(rowsAt(root, byParent, 3))).toEqual([111, 112, 12, 21, 3])
  })

  it('reports a missing level as not loaded, and a leaf as needing nothing', () => {
    const { root, byParent } = tree()
    expect(loadedTo(root, byParent, 3)).toBe(true)
    const partial = new Map(byParent)
    partial.delete(11)
    expect(loadedTo(root, partial, 2)).toBe(true)
    expect(loadedTo(root, partial, 3)).toBe(false)
    expect(loadedTo(level([area({ children: 0 })]), new Map(), 5)).toBe(true)
  })
})

describe('placeNested', () => {
  const box = { width: 1200, height: 800, gap: 2, pad: 8 }

  function parents(): Layout {
    const { root } = tree()
    return {
      level: 1,
      scale: 5,
      placed: [
        { area: root.areas[0]!, x: 300, y: 300, r: 100 },
        { area: root.areas[1]!, x: 700, y: 300, r: 50 },
        { area: root.areas[2]!, x: 900, y: 600, r: 35 },
      ],
    }
  }

  it('carries a parent with no children down, so no part of the corpus vanishes', () => {
    const { byParent } = tree()
    const childrenOf = new Map([...byParent].map(([k, v]) => [k, v.areas]))
    const next = placeNested(parents(), childrenOf, { ...box, shrink: 0.6 })
    expect(next.level).toBe(2)
    expect(ids(next.placed.map((p) => p.area)).sort((a, b) => a - b)).toEqual([3, 11, 12, 21])
    expect(total(next.placed.map((p) => p.area))).toBe(total(parents().placed.map((p) => p.area)))
  })

  it('keeps each child near the parent it came from', () => {
    const { byParent } = tree()
    const childrenOf = new Map([...byParent].map(([k, v]) => [k, v.areas]))
    const from = parents()
    const next = placeNested(from, childrenOf, { ...box, shrink: 0.6 })
    for (const p of next.placed) {
      const parent = from.placed.find((q) => q.area.area_id === (p.area.parent_id ?? p.area.area_id))!
      // Spacing may push a child out a little, never across the map.
      expect(Math.hypot(p.x - parent.x, p.y - parent.y)).toBeLessThan(parent.r * 1.5)
    }
  })

  it('is deterministic', () => {
    const { byParent } = tree()
    const childrenOf = new Map([...byParent].map(([k, v]) => [k, v.areas]))
    const a = placeNested(parents(), childrenOf, { ...box, shrink: 0.6 })
    const b = placeNested(parents(), childrenOf, { ...box, shrink: 0.6 })
    expect(a.placed.map(({ x, y, r }) => [x, y, r])).toEqual(b.placed.map(({ x, y, r }) => [x, y, r]))
  })
})

describe('relax', () => {
  it('separates circles stacked on one point and keeps them inside the box', () => {
    const placed: Placed[] = [1, 2, 3, 4].map((id) => ({ area: area({ area_id: id }), x: 100, y: 100, r: 20 }))
    relax(placed, { width: 400, height: 300, gap: 2, pad: 4, iterations: 200 })
    for (let i = 0; i < placed.length; i++) {
      const a = placed[i]!
      expect(a.x - a.r).toBeGreaterThanOrEqual(4 - 1e-6)
      expect(a.y + a.r).toBeLessThanOrEqual(300 - 4 + 1e-6)
      for (let j = i + 1; j < placed.length; j++) {
        const b = placed[j]!
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThanOrEqual(a.r + b.r - 0.5)
      }
    }
  })
})

describe('enclosing', () => {
  it('contains every member circle, and is empty for no circles', () => {
    const circles = [
      { x: 0, y: 0, r: 5 },
      { x: 40, y: 10, r: 12 },
      { x: -20, y: 30, r: 3 },
    ]
    const hull = enclosing(circles, 2)
    for (const c of circles) expect(Math.hypot(c.x - hull.x, c.y - hull.y) + c.r).toBeLessThanOrEqual(hull.r)
    expect(enclosing([])).toEqual({ x: 0, y: 0, r: 0 })
  })
})

describe('ancestorAt', () => {
  const { all } = tree()
  const byId = new Map(all.map((a) => [a.area_id, a]))

  it('walks up to the level asked for', () => {
    expect(ancestorAt(111, 1, byId)).toBe(1)
    expect(ancestorAt(111, 2, byId)).toBe(11)
    expect(ancestorAt(111, 3, byId)).toBe(111)
  })

  it('refuses a broken chain, a finer level than the area, and a cycle', () => {
    expect(ancestorAt(999, 1, byId)).toBeNull()
    expect(ancestorAt(11, 3, byId)).toBeNull()
    const orphan = new Map(byId)
    orphan.delete(11)
    expect(ancestorAt(111, 1, orphan)).toBeNull()
    const cycle = new Map([
      [1, { level: 2, parent_id: 2 }],
      [2, { level: 2, parent_id: 1 }],
    ])
    expect(ancestorAt(1, 1, cycle)).toBeNull()
  })
})

describe('the split animation', () => {
  const coarse: Placed[] = [{ area: area({ area_id: 1 }), x: 100, y: 100, r: 50 }]
  const fine: Placed[] = [
    { area: area({ area_id: 11, parent_id: 1, level: 2 }), x: 60, y: 90, r: 20 },
    { area: area({ area_id: 12, parent_id: 1, level: 2 }), x: 150, y: 120, r: 25 },
    { area: area({ area_id: 99, parent_id: 7, level: 2 }), x: 400, y: 400, r: 10 },
  ]
  const ancestor = (id: number) => (id === 99 ? 7 : 1)
  const items = splitMorph(coarse, fine, ancestor)

  it('starts every child inside the circle it came from', () => {
    for (const item of items.filter((i) => i.id !== 99)) {
      expect(Math.hypot(item.from.x - 100, item.from.y - 100) + item.from.r).toBeLessThanOrEqual(50 + 1e-6)
    }
  })

  it('ends exactly where the finer level draws each child', () => {
    const last = morphAt(items, 1)
    expect(last.circles.map(({ x, y, r }) => [x, y, r])).toEqual(fine.map(({ x, y, r }) => [x, y, r]))
    expect(last.parentOpacity).toBe(0)
    expect(last.childOpacity).toBe(1)
  })

  it('leaves a child with no drawn ancestor where it is', () => {
    const orphan = items.find((i) => i.id === 99)!
    expect(orphan.from).toEqual(orphan.to)
  })

  it('clamps progress outside 0..1', () => {
    expect(morphAt(items, -3)).toEqual(morphAt(items, 0))
    expect(morphAt(items, 7)).toEqual(morphAt(items, 1))
  })

  it('eases monotonically from 0 to 1', () => {
    let previous = -1
    for (let t = -0.2; t <= 1.2; t += 0.05) {
      const e = easeInOut(t)
      expect(e).toBeGreaterThanOrEqual(previous)
      previous = e
    }
    expect(easeInOut(0)).toBe(0)
    expect(easeInOut(1)).toBe(1)
  })
})

describe('levelFromSearch', () => {
  it('reads a level from the URL', () => {
    expect(levelFromSearch('?level=2')).toBe(2)
  })

  it.each(['', '?level=', '?level=0', '?level=-1', '?level=1.5', '?level=abc', '?level=10', '?level=2e0x'])(
    'refuses %j',
    (search) => {
      expect(levelFromSearch(search)).toBeNull()
    },
  )
})

describe('AreaCache', () => {
  it('asks for each level once, however many callers want it', async () => {
    const { root, byParent } = tree()
    const fetchLevel = vi.fn(async (parent: number | null) => (parent === null ? root : byParent.get(parent)!))
    const cache = new AreaCache(fetchLevel)
    const [a, b] = await Promise.all([cache.get(1), cache.get(1)])
    expect(a).toBe(b)
    await cache.get(null)
    await cache.get(null)
    expect(fetchLevel).toHaveBeenCalledTimes(2)
  })

  it('forgets a failed request so it can be tried again', async () => {
    const { byParent } = tree()
    let fail = true
    const fetchLevel = vi.fn(async (parent: number | null) => {
      if (fail) throw new Error('offline')
      return byParent.get(parent!)!
    })
    const cache = new AreaCache(fetchLevel)
    await expect(cache.get(1)).rejects.toThrow('offline')
    fail = false
    await expect(cache.get(1)).resolves.toBe(byParent.get(1))
    expect(fetchLevel).toHaveBeenCalledTimes(2)
  })

  it('descends to exactly the parents that have children', async () => {
    const { root, byParent } = tree()
    const fetchLevel = vi.fn(async (parent: number | null) => byParent.get(parent!)!)
    const out = await new AreaCache(fetchLevel).descend(root, 3)
    expect([...out.keys()].sort((a, b) => a - b)).toEqual([1, 2, 11])
    expect(loadedTo(root, out, 3)).toBe(true)
    expect(fetchLevel.mock.calls.map(([p]) => p)).not.toContain(3)
  })
})

describe('zoomCaption', () => {
  it('names the depth against the levels there are', () => {
    const { root } = tree()
    expect(zoomCaption(root, 2, 4)).toMatch(/^All fields · level 2 of 3 · 4 /)
  })
})

describe('labels', () => {
  it('wraps to the width, refuses what will not fit, and truncates only when asked', () => {
    expect(wrapWords('urban transport and public health', 16, 3)).toEqual(['urban transport', 'and public', 'health'])
    // A single word longer than the width is kept whole rather than lost.
    expect(wrapWords('interdisciplinarity', 5, 1)).toEqual(['interdisciplinarity'])
    expect(wrapWords('one two three four five six', 5, 2)).toBeNull()
    const cut = wrapWords('one two three four five six', 5, 2, true)!
    expect(cut).toHaveLength(2)
    expect(cut[1]!.endsWith('…')).toBe(true)
  })

  it('never keeps two labels whose boxes overlap, nor one off the edge', () => {
    const placed: Placed[] = Array.from({ length: 30 }, (_, i) => ({
      area: area({ area_id: i + 1, name: `Field number ${i + 1} of the corpus` }),
      x: 40 + (i % 6) * 30,
      y: 40 + Math.floor(i / 6) * 30,
      r: 12,
    }))
    const labels = [...fitLabels(placed, { width: 300, captions: true }).values()]
    expect(labels.length).toBeGreaterThan(0)
    for (let i = 0; i < labels.length; i++) {
      const a = labels[i]!.box
      expect(a.x).toBeGreaterThanOrEqual(0)
      expect(a.x + a.w).toBeLessThanOrEqual(300)
      for (let j = i + 1; j < labels.length; j++) {
        const b = labels[j]!.box
        const overlap = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
        expect(overlap).toBe(false)
      }
    }
  })
})
