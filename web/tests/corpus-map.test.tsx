/**
 * The corpus map (task P6-26).
 *
 * The canvas itself is not tested here — jsdom has no 2D context, and a
 * screenshot test would pin pixels rather than meaning. What is tested is what
 * the picture depends on: that a colour means one topic and keeps meaning it,
 * that the plot is not stretched, that hovering finds the dot under the cursor,
 * and that the page tells a reader how partial the picture is.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { MapView } from '../src/explore/MapPage'
import type { CorpusMap, MapPoint } from '../src/lib/api'
import {
  SERIES_SLOTS,
  assignSwatches,
  nearest,
  percent,
  swatchVar,
  toScreen,
  topicCounts,
} from '../src/lib/corpusmap'
import { parseRoute } from '../src/lib/route'

function point(over: Partial<MapPoint> = {}): MapPoint {
  return {
    chunk_id: 1,
    source_id: 1,
    x: 0,
    y: 0,
    topic: 'transit',
    title: 'A title',
    url: 'https://example.test/a',
    snippet: 'A passage.',
    ...over,
  }
}

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z#0-9]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function map(over: Partial<CorpusMap> = {}): CorpusMap {
  return {
    as_of: '2026-09-23T12:00:00Z',
    points: [point(), point({ chunk_id: 2, topic: 'housing' }), point({ chunk_id: 3, topic: null })],
    eligible: 3,
    explained_variance: [0.061, 0.034],
    ...over,
  }
}

describe('a colour means one topic, and keeps meaning it', () => {
  it('assigns by name, so the largest topic does not take the first colour', () => {
    const points = [
      ...Array.from({ length: 50 }, (_, i) => point({ chunk_id: i, topic: 'zoning' })),
      point({ chunk_id: 99, topic: 'access' }),
    ]
    const swatches = assignSwatches(points)
    expect(swatches.get('access')).toEqual({ kind: 'series', slot: 1 })
    expect(swatches.get('zoning')).toEqual({ kind: 'series', slot: 2 })
  })

  it('does not repaint the survivors when a topic disappears from view', () => {
    // Hiding a topic filters what is drawn, not what was assigned. A second
    // assignment over the reduced set is what would shift colours, and the
    // page never makes one — this pins why it must not.
    const all = [point({ topic: 'a' }), point({ topic: 'b' }), point({ topic: 'c' })]
    const full = assignSwatches(all)
    expect(full.get('c')).toEqual({ kind: 'series', slot: 3 })
    expect(assignSwatches(all.filter((p) => p.topic !== 'a')).get('c')).not.toEqual(full.get('c'))
  })

  it('folds a ninth topic into Other rather than inventing a hue', () => {
    const topics = Array.from({ length: SERIES_SLOTS + 2 }, (_, i) => `t${String(i).padStart(2, '0')}`)
    const swatches = assignSwatches(topics.map((topic, i) => point({ chunk_id: i, topic })))

    const slots = [...swatches.values()].filter((s) => s.kind === 'series')
    expect(slots).toHaveLength(SERIES_SLOTS)
    expect(swatches.get('t09')).toEqual({ kind: 'other' })
    expect(swatchVar({ kind: 'other' })).toBe('--text-faint')
  })

  it('gives unlabelled sources their own neutral entry, not a series colour', () => {
    const swatches = assignSwatches([point({ topic: null })])
    expect(swatches.get(null)).toEqual({ kind: 'unlabelled' })
    expect(swatchVar(swatches.get(null)!)).toBe('--text-faint')
  })

  it('only ever names series roles the token file defines', () => {
    for (let slot = 1; slot <= SERIES_SLOTS; slot++) {
      expect(swatchVar({ kind: 'series', slot })).toBe(`--series-${slot}`)
    }
  })
})

describe('geometry', () => {
  it('keeps the plot square in a wide frame, so neither axis is stretched', () => {
    const [left] = toScreen({ x: -1, y: 0 }, 1000, 400, 0)
    const [right] = toScreen({ x: 1, y: 0 }, 1000, 400, 0)
    const [, top] = toScreen({ x: 0, y: 1 }, 1000, 400, 0)
    const [, bottom] = toScreen({ x: 0, y: -1 }, 1000, 400, 0)
    expect(right - left).toBe(bottom - top)
  })

  it('puts positive y at the top of the screen', () => {
    const [, up] = toScreen({ x: 0, y: 1 }, 200, 200, 10)
    const [, down] = toScreen({ x: 0, y: -1 }, 200, 200, 10)
    expect(up).toBeLessThan(down)
  })

  it('finds the dot under the cursor, and nothing when the cursor is in space', () => {
    const points = [point({ chunk_id: 1, x: -0.5 }), point({ chunk_id: 2, x: 0.5 })]
    const project = (p: MapPoint) => toScreen(p, 200, 200, 0)
    const [x, y] = project(points[1]!)

    expect(nearest(points, [x + 3, y - 3], project, 8)?.chunk_id).toBe(2)
    expect(nearest(points, [x + 30, y], project, 8)).toBeNull()
  })

  it('prefers the dot drawn last when two overlap, because it is the one on top', () => {
    const points = [point({ chunk_id: 1 }), point({ chunk_id: 2 })]
    expect(nearest(points, [100, 100], (p) => toScreen(p, 200, 200, 0), 8)?.chunk_id).toBe(2)
  })
})

describe('the page is honest about how partial the picture is', () => {
  it('says how much of the variation the axes carry', () => {
    const body = text(renderToStaticMarkup(<MapView map={map()} />))
    expect(body).toContain('axes carry 6.1% and 3.4% of the variation')
  })

  it('says when the map is a sample, and of how much', () => {
    const body = text(renderToStaticMarkup(<MapView map={map({ eligible: 12_000 })} />))
    expect(body).toContain('3 of 12,000 passages, sampled')
  })

  it('does not call a complete map a sample', () => {
    const body = text(renderToStaticMarkup(<MapView map={map()} />))
    expect(body).toContain('3 passages')
    expect(body).not.toContain('sampled')
  })

  it('says why an empty map is empty instead of drawing a blank frame', () => {
    const body = text(renderToStaticMarkup(<MapView map={map({ points: [], eligible: 0 })} />))
    expect(body).toContain('Nothing to draw yet')
    expect(body).not.toContain('Table view')
  })

  it('names every topic in the legend and the table, so colour is never the only cue', () => {
    const markup = renderToStaticMarkup(<MapView map={map()} />)
    const body = text(markup)
    for (const name of ['transit', 'housing', 'Unlabelled']) {
      expect(body.split(name).length - 1, name).toBeGreaterThanOrEqual(2)
    }
    expect(markup).toContain('aria-label="Topics"')
  })
})

describe('the table view', () => {
  it('orders by size and breaks ties by name, so it is stable', () => {
    const rows = topicCounts([
      point({ topic: 'b' }),
      point({ topic: 'a' }),
      point({ topic: 'c' }),
      point({ topic: 'c' }),
    ])
    expect(rows).toEqual([
      { topic: 'c', count: 2 },
      { topic: 'a', count: 1 },
      { topic: 'b', count: 1 },
    ])
  })

  it('formats a share to one decimal', () => {
    expect(percent(0.0614)).toBe('6.1%')
  })
})

describe('the route', () => {
  it('is a real URL', () => {
    expect(parseRoute('/map')).toEqual({ name: 'map' })
    expect(parseRoute('/map/')).toEqual({ name: 'map' })
  })

  it('does not swallow a longer path that merely starts with it', () => {
    expect(parseRoute('/maps')).toEqual({ name: 'explore' })
    expect(parseRoute('/map/5')).toEqual({ name: 'explore' })
  })
})
