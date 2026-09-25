/**
 * The Map screen's geometry (task P6-34): circle area is passages collected,
 * the key is drawn to the same scale, and no circle hides another.
 */
import { describe, expect, it } from 'vitest'

import {
  keyValues,
  keyValuesAtScale,
  edgeToEdge,
  fitRadius,
  labelLines,
  levelCaption,
  levelNoun,
  linkWidth,
  niceBelow,
  placeAreas,
  radiusOf,
  radiusScale,
  type AreaLink,
  type AreasLevel,
} from '../src/lib/areas'
import { area } from './areas-fixtures'

describe('circle size', () => {
  it('makes area, not radius, proportional to passages', () => {
    const scale = radiusScale([100, 400], 40)
    const small = radiusOf(100, scale)
    const large = radiusOf(400, scale)
    expect(large).toBe(40)
    // Four times the passages is four times the area: twice the radius.
    expect(large / small).toBeCloseTo(2)
    expect((Math.PI * large ** 2) / (Math.PI * small ** 2)).toBeCloseTo(4)
  })

  it('has no scale for nothing', () => {
    expect(radiusScale([], 40)).toBe(0)
    expect(radiusScale([0, 0], 40)).toBe(0)
  })

  it('keys with round values inside what is drawn', () => {
    expect(niceBelow(5050)).toBe(5000)
    expect(niceBelow(1999)).toBe(1000)
    expect(niceBelow(0.5)).toBe(0)
    const values = keyValues([422, 2672, 5050])
    expect(values.at(-1)).toBe(5000)
    expect(values.every((v) => v <= 5050)).toBe(true)
    expect([...values].sort((a, b) => a - b)).toEqual(values)
    expect(new Set(values).size).toBe(values.length)
    expect(keyValues([])).toEqual([])
  })
})

describe('placing areas', () => {
  it('keeps every circle inside the box and none overlapping', () => {
    const areas = [
      area({ area_id: 1, passages: 5000, x: 0, y: 0 }),
      area({ area_id: 2, passages: 4000, x: 0.01, y: 0 }),
      area({ area_id: 3, passages: 300, x: 0, y: 0 }),
      area({ area_id: 4, passages: 50, x: 1, y: 1 }),
    ]
    const { placed } = placeAreas(areas, 800, 600, { maxRadius: 120, gap: 8 })
    for (const p of placed) {
      expect(p.x - p.r).toBeGreaterThanOrEqual(23.9)
      expect(p.x + p.r).toBeLessThanOrEqual(776.1)
      expect(p.y - p.r).toBeGreaterThanOrEqual(23.9)
      expect(p.y + p.r).toBeLessThanOrEqual(576.1)
    }
    for (let i = 0; i < placed.length; i++)
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i]!
        const b = placed[j]!
        expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeGreaterThan(a.r + b.r)
      }
  })

  it('is deterministic, so the map does not jiggle between renders', () => {
    const areas = [area({ area_id: 1, x: 0.2 }), area({ area_id: 2, x: 0.2 }), area({ area_id: 3, x: -0.4 })]
    const first = placeAreas(areas, 600, 400, { maxRadius: 60 }).placed.map((p) => [p.x, p.y])
    const second = placeAreas(areas, 600, 400, { maxRadius: 60 }).placed.map((p) => [p.x, p.y])
    expect(first).toEqual(second)
  })

  it('puts a stored position where it says, y upwards', () => {
    const { placed } = placeAreas([area({ x: 0.5, y: 0.5, passages: 1 })], 1000, 1000, { maxRadius: 10, pad: 0 })
    expect(placed[0]!.x).toBeGreaterThan(500)
    expect(placed[0]!.y).toBeLessThan(500)
  })
})

describe('the size key at the canvas scale', () => {
  it('only offers values it can draw within its box', () => {
    const scale = radiusScale([5050], 100)
    const values = keyValuesAtScale([422, 5050], scale, 34)
    expect(values.length).toBeGreaterThan(0)
    for (const v of values) expect(radiusOf(v, scale)).toBeLessThanOrEqual(34)
  })

  it('has nothing to key without a scale', () => {
    expect(keyValuesAtScale([100], 0, 34)).toEqual([])
  })
})

describe('links and labels', () => {
  const link = (over: Partial<AreaLink>): AreaLink => ({
    area_a: 1,
    area_b: 2,
    cited_claims: 0,
    cited_sources: 0,
    similar_pairs: 3,
    similarity: 0.8,
    shared_terms: [],
    ...over,
  })

  it('thickens a cited link with its independent sources, and caps it', () => {
    expect(linkWidth(link({ cited_claims: 2, cited_sources: 9 }))).toBeGreaterThan(
      linkWidth(link({ cited_claims: 2, cited_sources: 1 })),
    )
    expect(linkWidth(link({ cited_claims: 50, cited_sources: 400 }))).toBe(8)
    expect(linkWidth(link({}))).toBeLessThan(linkWidth(link({ cited_claims: 1, cited_sources: 1 })))
  })

  it('wraps a name over at most two lines', () => {
    expect(labelLines('alpha · beta')).toEqual(['alpha · beta'])
    const lines = labelLines('a very long first term · a second long term · a third long term', 20)
    expect(lines.length).toBe(2)
    expect(lines[1]!.endsWith('…')).toBe(true)
  })

  it('captions a level with what it holds', () => {
    const base: AreasLevel = {
      build: { build_id: 1, computed_at: '', passages: 10, regions: 8, areas: 30, leaves: 150 },
      level: 1,
      parent: null,
      path: [],
      areas: [area(), area({ area_id: 2 })],
      links: [],
      levels: 3,
      passages_needed: 20,
      stale_after_days: 180,
      weak_below_sources: 3,
    }
    expect(levelCaption(base)).toBe('All fields · level 1 of 3 · 30 subfields in 8 fields')
    expect(levelCaption({ ...base, level: 2, parent: area({ name: 'x · y' }) })).toBe(
      'x · y · level 2 of 3 · 2 subfields',
    )
    expect(levelCaption({ ...base, level: 3, parent: area(), areas: [area()] })).toMatch(/ · 1 theme$/)
    expect(levelCaption({ ...base, level: 3, parent: area() })).toMatch(/ · 2 themes$/)
    // One of each counts in the singular, the old geography words are gone.
    const single = levelCaption({ ...base, build: { ...base.build!, regions: 1, areas: 1 } })
    expect(single).toBe('All fields · level 1 of 3 · 1 subfield in 1 field')
    expect(single).not.toMatch(/area|region/)
  })

  it('names each level as a person would: field, subfield, theme', () => {
    expect([1, 2, 3].map((n) => levelNoun(n))).toEqual(['field', 'subfield', 'theme'])
    expect([1, 2, 3].map((n) => levelNoun(n, 0))).toEqual(['fields', 'subfields', 'themes'])
    expect(levelNoun(4, 5)).toBe('themes')
  })
})

describe('lines between circles', () => {
  it('run edge to edge, outside both circles', () => {
    const segment = edgeToEdge({ x: 0, y: 0, r: 10 }, { x: 100, y: 0, r: 20 })
    expect(segment).toEqual([10, 0, 80, 0])
  })

  it('are not drawn when the circles touch', () => {
    expect(edgeToEdge({ x: 0, y: 0, r: 10 }, { x: 25, y: 0, r: 15 })).toBeNull()
  })

  it('fit the box: circles together cover no more than the fill', () => {
    const passages = [5000, 3000, 1000, 400]
    const r = fitRadius(passages, 800, 600, { fill: 0.25, cap: 1 })
    const scale = radiusScale(passages, r)
    const covered = passages.reduce((sum, p) => sum + Math.PI * radiusOf(p, scale) ** 2, 0)
    expect(covered).toBeLessThanOrEqual(0.25 * 800 * 600 + 1)
    expect(fitRadius(passages, 800, 600, { fill: 1, cap: 0.1 })).toBe(60)
  })
})

describe('keeping clear of the key', () => {
  it('moves a circle out of an area the key covers', () => {
    const { placed } = placeAreas([area({ x: -1, y: -1, passages: 100 })], 800, 600, {
      maxRadius: 30,
      avoid: [{ x: 0, y: 450, w: 230, h: 150 }],
    })
    const p = placed[0]!
    const nearestX = Math.min(230, Math.max(0, p.x))
    const nearestY = Math.min(600, Math.max(450, p.y))
    expect(Math.hypot(p.x - nearestX, p.y - nearestY)).toBeGreaterThanOrEqual(p.r)
  })
})
