/**
 * The corpus map (tasks P6-26, P6-29).
 *
 * Neither canvas is tested by its pixels — jsdom has no WebGL and no 2D
 * context, and a screenshot test would pin pixels rather than meaning. What is
 * tested is what the picture depends on: that a colour means one topic and
 * keeps meaning it, that a dot lands where its coordinates say and the cursor
 * finds it there, that the camera frames what is shown, that the page says how
 * partial the picture is, and that a browser without WebGL still gets a map.
 *
 * @vitest-environment jsdom
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { act, cleanup, render, screen } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MapView } from '../src/explore/MapPage'
import {
  bounds,
  coloursOf,
  fitDistance,
  isDrag,
  mix,
  parseColour,
  pick,
  positionsOf,
  toViewport,
  visibilityOf,
} from '../src/explore/map/geometry'
import { cardPosition } from '../src/explore/map/HoverCard'
import { GRATICULE_MIX, paletteFrom } from '../src/explore/map/palette'
import type { CorpusMap, MapPoint } from '../src/lib/api'
import {
  AXES,
  SERIES_SLOTS,
  assignSwatches,
  caption,
  nameHash,
  nearest,
  percent,
  swatchVar,
  toScreen,
  topicCounts,
} from '../src/lib/corpusmap'
import { parseRoute } from '../src/lib/route'

// `import.meta.dirname`, not a `URL`: under jsdom the global URL is the DOM's, which refuses `file:`.
const REPO = join(import.meta.dirname, '..', '..')

function point(over: Partial<MapPoint> = {}): MapPoint {
  return {
    chunk_id: 1,
    source_id: 1,
    x: 0,
    y: 0,
    z: 0,
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
    explained_variance: [0.061, 0.034, 0.022],
    ...over,
  }
}

/** Topic names that hash to distinct slots, found rather than assumed. */
function distinctTopics(count: number): string[] {
  const found: string[] = []
  const slots = new Set<number>()
  for (let i = 0; found.length < count; i++) {
    const name = `topic-${i}`
    const slot = nameHash(name) % SERIES_SLOTS
    if (!slots.has(slot)) {
      slots.add(slot)
      found.push(name)
    }
  }
  return found
}

/** Two names that want the same slot. */
function collidingPair(): [string, string] {
  const seen = new Map<number, string>()
  for (let i = 0; ; i++) {
    const name = `topic-${i}`
    const slot = nameHash(name) % SERIES_SLOTS
    const other = seen.get(slot)
    if (other) return [other, name].sort() as [string, string]
    seen.set(slot, name)
  }
}

describe('a colour means one topic, and keeps meaning it', () => {
  it('follows the name, not the size: a topic is the same colour however large it grows', () => {
    const [small, large] = distinctTopics(2) as [string, string]
    const before = assignSwatches([point({ topic: small }), point({ topic: large })])
    const after = assignSwatches([
      point({ topic: small }),
      ...Array.from({ length: 50 }, (_, i) => point({ chunk_id: i + 10, topic: large })),
    ])
    expect(after.get(large)).toEqual(before.get(large))
    expect(after.get(small)).toEqual(before.get(small))
  })

  it('does not repaint the others when a new topic arrives, unless it collides', () => {
    // Alphabetical slots — the first map's rule — failed exactly this: a name
    // sorting first pushed every colour after it along by one.
    const topics = distinctTopics(4)
    const three = assignSwatches(topics.slice(1).map((topic, i) => point({ chunk_id: i, topic })))
    const four = assignSwatches(topics.map((topic, i) => point({ chunk_id: i, topic })))
    for (const topic of topics.slice(1)) expect(four.get(topic), topic).toEqual(three.get(topic))
  })

  it('resolves a collision without two topics sharing a colour', () => {
    const [first, second] = collidingPair()
    const swatches = assignSwatches([point({ topic: second }), point({ topic: first })])
    const a = swatches.get(first)
    const b = swatches.get(second)
    expect(a?.kind).toBe('series')
    expect(b?.kind).toBe('series')
    expect(a).not.toEqual(b)
    // The alphabetically first keeps its preferred slot, whatever order the points arrive in.
    expect(a).toEqual({ kind: 'series', slot: (nameHash(first) % SERIES_SLOTS) + 1 })
  })

  it('folds a ninth topic into Other rather than inventing a hue', () => {
    const topics = Array.from({ length: SERIES_SLOTS + 2 }, (_, i) => `t${String(i).padStart(2, '0')}`)
    const swatches = assignSwatches(topics.map((topic, i) => point({ chunk_id: i, topic })))

    const series = [...swatches.values()].filter((s) => s.kind === 'series')
    expect(series).toHaveLength(SERIES_SLOTS)
    expect(new Set(series.map((s) => JSON.stringify(s))).size).toBe(SERIES_SLOTS)
    expect(swatches.get('t09')).toEqual({ kind: 'other' })
    expect(swatchVar({ kind: 'other' })).toBe('--text-faint')
  })

  it('gives unlabelled sources their own neutral entry, not a series colour', () => {
    const swatches = assignSwatches([point({ topic: null })])
    expect(swatches.get(null)).toEqual({ kind: 'unlabelled' })
    expect(swatchVar(swatches.get(null)!)).toBe('--text-faint')
  })

  it('only ever names series roles the token file defines', () => {
    const tokens = readFileSync(join(REPO, 'web/src/styles/tokens.css'), 'utf8')
    for (let slot = 1; slot <= SERIES_SLOTS; slot++) {
      const role = swatchVar({ kind: 'series', slot })
      expect(tokens, role).toContain(`${role}: var(`)
    }
  })
})

describe('the canvas palette comes from the tokens, read at draw time', () => {
  // The real dark palette, read from the token file — which is also what keeps
  // colour literals out of this test.
  const css = readFileSync(join(REPO, 'web/src/styles/tokens.css'), 'utf8')
  const dark = (name: string) => new RegExp(`--dark-${name}: (#[0-9A-Fa-f]{6});`).exec(css)![1]!
  const TOKENS: Record<string, string> = {
    '--ground-deep': dark('ground-deep'),
    '--line': dark('line'),
    '--line-strong': dark('line-strong'),
    '--text-faint': dark('text-faint'),
    '--series-1': dark('series-1'),
  }
  const channels = (hex: string) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)

  it('parses the forms getComputedStyle hands back', () => {
    expect(parseColour(dark('text'))).toEqual(channels(dark('text')))
    expect(parseColour(` ${dark('text').toLowerCase()} `)).toEqual(channels(dark('text')))
    expect(parseColour('rgb(255, 0, 51)')).toEqual([1, 0, 0.2])
    expect(parseColour('rgba(255 255 255 / 0.5)')).toEqual([1, 1, 1])
  })

  it('calls an unreadable colour unreadable rather than black', () => {
    // Black on `ground.deep` is a dot that silently vanished.
    for (const bad of ['', 'var(--series-1)', 'teal', dark('text').slice(0, 6)]) {
      expect(parseColour(bad), bad).toBeNull()
    }
  })

  it('draws each topic in its series token, and a series with no token in the neutral', () => {
    const swatches = new Map([
      ['a', { kind: 'series', slot: 1 } as const],
      ['b', { kind: 'series', slot: 2 } as const],
      [null, { kind: 'unlabelled' } as const],
    ])
    const palette = paletteFrom((property) => TOKENS[property] ?? '', swatches)!
    expect(palette.topics.get('a')).toEqual(channels(dark('series-1')))
    expect(palette.topics.get('b')).toEqual(palette.neutral) // --series-2 unset here
    expect(palette.topics.get(null)).toEqual(channels(dark('text-faint')))
  })

  it('places the graticule between the ground and the hairline, quieter than both', () => {
    const palette = paletteFrom((property) => TOKENS[property] ?? '', new Map())!
    const ground = channels(dark('ground-deep')) as [number, number, number]
    const line = channels(dark('line')) as [number, number, number]
    expect(palette.graticule).toEqual(mix(ground, line, GRATICULE_MIX))
    palette.graticule.forEach((channel, i) => {
      expect(channel).toBeGreaterThan(ground[i]!)
      expect(channel).toBeLessThan(line[i]!)
    })
  })

  it('refuses to draw without its ground rather than guessing one', () => {
    expect(paletteFrom(() => '', new Map())).toBeNull()
  })
})

describe('3D geometry, without WebGL', () => {
  // A plain orthographic "camera": x and y pass straight through to NDC, z
  // becomes depth. Column-major, as three.js stores it.
  const IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]

  it('puts a point where its coordinates say, with up on the screen positive', () => {
    expect(toViewport(IDENTITY, 0, 0, 0, 200, 100)).toEqual([100, 50, 0])
    expect(toViewport(IDENTITY, 1, 1, 0, 200, 100)).toEqual([200, 0, 0])
    expect(toViewport(IDENTITY, -1, -1, 0.5, 200, 100)).toEqual([0, 100, 0.5])
  })

  it('never places a point that is behind the camera or past the clip planes', () => {
    // A perspective-style matrix whose w is −z: anything at z ≥ 0 is behind.
    const perspective = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, -1, 0, 0, 0, 0]
    expect(toViewport(perspective, 0, 0, 1, 100, 100)).toBeNull()
    expect(toViewport(IDENTITY, 0, 0, 1.5, 100, 100)).toBeNull()
  })

  it('picks the dot under the cursor, within a radius in pixels', () => {
    const points = [point({ x: -0.5 }), point({ x: 0.5 })]
    const positions = positionsOf(points)
    const visible = visibilityOf(points, new Set())
    // x = 0.5 lands at 150 of 200.
    expect(pick(positions, visible, IDENTITY, 200, 200, [153, 97], 7)).toBe(1)
    expect(pick(positions, visible, IDENTITY, 200, 200, [180, 100], 7)).toBe(-1)
  })

  it('never picks a dot the legend has hidden', () => {
    const points = [point({ topic: 'shown', x: 0.5 }), point({ topic: 'hidden', x: 0.5 })]
    const visible = visibilityOf(points, new Set(['shown']))
    expect([...visible]).toEqual([0, 1])
    expect(pick(positionsOf(points), visible, IDENTITY, 200, 200, [150, 100], 7)).toBe(1)
    expect(pick(positionsOf(points), visibilityOf(points, new Set(['shown', 'hidden'])), IDENTITY, 200, 200, [150, 100], 7)).toBe(-1)
  })

  it('prefers the dot in front when two sit on the same spot', () => {
    const points = [point({ chunk_id: 1, z: 0.4 }), point({ chunk_id: 2, z: -0.4 }), point({ chunk_id: 3, z: 0.1 })]
    expect(pick(positionsOf(points), visibilityOf(points, new Set()), IDENTITY, 200, 200, [100, 100], 7)).toBe(1)
  })

  it('prefers the dot nearer the cursor over the one nearer the camera', () => {
    const points = [point({ x: 0.04, z: -0.9 }), point({ x: 0.01, z: 0.9 })]
    // 0.01 → 101px, 0.04 → 104px; the cursor at 101 is on the far dot.
    expect(pick(positionsOf(points), visibilityOf(points, new Set()), IDENTITY, 200, 200, [101, 100], 7)).toBe(1)
  })

  it('frames the visible points about their own centre', () => {
    const points = [point({ topic: 'a', x: 0.8 }), point({ topic: 'a', x: 1 }), point({ topic: 'b', x: -1 })]
    const box = bounds(positionsOf(points), visibilityOf(points, new Set(['b'])))!
    expect(box.centre[0]).toBeCloseTo(0.9)
    expect(box.radius).toBeCloseTo(0.1)
    expect(bounds(positionsOf(points), visibilityOf(points, new Set(['a', 'b'])))).toBeNull()
  })

  it('backs the camera off far enough for the sphere to fit the narrower field of view', () => {
    for (const aspect of [0.5, 1, 2.5]) {
      const distance = fitDistance(1, 30, aspect, 1)
      const vertical = (15 * Math.PI) / 180
      const horizontal = Math.atan(Math.tan(vertical) * aspect)
      // The sphere's angular radius from there equals the tighter half-angle.
      expect(Math.asin(1 / distance)).toBeCloseTo(Math.min(vertical, horizontal), 6)
    }
    expect(fitDistance(2, 30, 1)).toBeCloseTo(2 * fitDistance(1, 30, 1))
  })

  it('lays positions and colours out three floats a point, in point order', () => {
    const points = [point({ x: 0.1, y: 0.2, z: 0.3, topic: 'a' }), point({ x: -1, y: 1, z: 0, topic: 'zz' })]
    expect([...positionsOf(points)].map((v) => Number(v.toFixed(3)))).toEqual([0.1, 0.2, 0.3, -1, 1, 0])
    const colours = coloursOf(points, new Map([['a', [1, 0, 0]]]), [0.5, 0.5, 0.5])
    expect([...colours]).toEqual([1, 0, 0, 0.5, 0.5, 0.5])
  })

  it('tells a click from a drag', () => {
    expect(isDrag([10, 10], [12, 11])).toBe(false)
    expect(isDrag([10, 10], [30, 10])).toBe(true)
  })
})

describe('2D geometry', () => {
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

  it('keeps the hover card inside the frame near its edges', () => {
    const frame = { width: 600, height: 400 }
    const card = { width: 280, height: 150 }
    const nearCorner = cardPosition([590, 390], frame, card)
    expect(nearCorner.left + card.width).toBeLessThanOrEqual(frame.width)
    expect(nearCorner.top + card.height).toBeLessThanOrEqual(frame.height)
    expect(cardPosition([10, 10], frame, card)).toEqual({ left: 24, top: 24 })
  })
})

describe('the page is honest about how partial the picture is', () => {
  it('names the share of every axis it draws, and only those', () => {
    expect(caption(map(), 3)).toContain('three axes carry 6.1%, 3.4%, 2.2% of the variation')
    // The flat view draws two, so quoting the third would claim what it does not show.
    expect(caption(map(), 2)).toContain('two axes carry 6.1%, 3.4% of the variation')
    expect(caption(map(), 2)).not.toContain('2.2%')
  })

  it('says when the map is a sample, and of how much', () => {
    expect(caption(map({ eligible: 12_000 }), 3)).toContain('3 of 12,000 passages, sampled')
  })

  it('does not call a complete map a sample', () => {
    expect(caption(map(), 3)).toMatch(/^3 passages ·/)
    expect(caption(map(), 3)).not.toContain('sampled')
  })

  it('carries the three-axis caption onto the page when WebGL is there', () => {
    const body = text(renderToStaticMarkup(<MapView map={map()} webgl />))
    expect(body).toContain('three axes carry 6.1%, 3.4%, 2.2%')
    for (const axis of ['PC1', 'PC2', 'PC3']) expect(body).toContain(axis)
  })

  it('says why the map is flat when WebGL is not there, and quotes two axes', () => {
    const body = text(renderToStaticMarkup(<MapView map={map()} webgl={false} />))
    expect(body).toContain('no WebGL')
    expect(body).toContain('two axes carry 6.1%, 3.4%')
    expect(body).not.toContain('PC3')
  })

  it('says why an empty map is empty instead of drawing a blank frame', () => {
    const body = text(renderToStaticMarkup(<MapView map={map({ points: [], eligible: 0 })} webgl />))
    expect(body).toContain('Nothing to draw yet')
    expect(body).not.toContain('Table')
  })

  it('names every topic in the legend and the table, so colour is never the only cue', () => {
    const markup = renderToStaticMarkup(<MapView map={map()} webgl />)
    const body = text(markup)
    for (const name of ['transit', 'housing', 'Unlabelled']) {
      expect(body.split(name).length - 1, name).toBeGreaterThanOrEqual(2)
    }
    expect(markup).toContain('aria-label="Topics"')
    expect(markup).toContain('aria-label="Table view"')
  })

  it('offers 3D first, as the default view', () => {
    const markup = renderToStaticMarkup(<MapView map={map()} webgl />)
    expect(markup).toMatch(/aria-pressed="true"[^>]*>3D</)
  })
})

describe('a browser without WebGL still gets a map', () => {
  const css = readFileSync(join(REPO, 'web/src/styles/tokens.css'), 'utf8')
  const dark = (name: string) => new RegExp(`--dark-${name}: (#[0-9A-Fa-f]{6});`).exec(css)![1]!
  const PALETTE = Object.fromEntries(
    ['ground-deep', 'line', 'line-strong', 'text-faint', 'series-1'].map((name) => [`--${name}`, dark(name)]),
  )

  beforeEach(() => {
    // jsdom loads no stylesheet and has no ResizeObserver; the page needs a
    // palette to draw with and a size to draw at.
    for (const [name, value] of Object.entries(PALETTE)) document.documentElement.style.setProperty(name, value)
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(private readonly callback: ResizeObserverCallback) {}
        observe() {
          this.callback([{ contentRect: { width: 800, height: 600 } } as ResizeObserverEntry], this as never)
        }
        disconnect() {}
      },
    )
    // jsdom's canvas has no contexts at all; say so quietly.
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    document.documentElement.removeAttribute('style')
  })

  it('falls back to the flat view when the 3D renderer cannot start', async () => {
    // Told WebGL exists, so the 3D scene mounts and tries — and three.js
    // cannot get a context here. The page must notice and draw flat, not
    // leave an empty dark rectangle.
    vi.spyOn(console, 'error').mockImplementation(() => {})
    await act(async () => {
      render(<MapView map={map()} webgl />)
    })
    // The 3D view is loaded on demand, so the failure arrives a tick later.
    expect(await screen.findByText(/no WebGL/, {}, { timeout: 5000 })).toBeTruthy()
    expect(screen.getByRole('img', { name: /flat scatter of 3 passages/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: '3D' }).hasAttribute('disabled')).toBe(true)
  })

  it('asks the browser when nobody told it, and draws flat on a no', async () => {
    await act(async () => {
      render(<MapView map={map()} />)
    })
    expect(screen.getByText(/no WebGL/)).toBeTruthy()
    expect(screen.getByRole('img', { name: /flat scatter/ })).toBeTruthy()
  })
})

describe('drift against the API', () => {
  const core = readFileSync(join(REPO, 'packages/meridian_core/meridian_core/corpusmap.py'), 'utf8')
  const schema = readFileSync(join(REPO, 'packages/meridian_core/meridian_core/schemas/corpusmap.py'), 'utf8')

  it('draws as many axes as the projection computes', () => {
    const components = /^COMPONENTS = (\d+)$/m.exec(core)
    expect(components, 'COMPONENTS in corpusmap.py').not.toBeNull()
    expect(Number(components![1])).toBe(AXES)
  })

  it('expects as many variance shares as the wire shape sends', () => {
    const tuple = /explained_variance: tuple\[([^\]]+)\]/.exec(schema)
    expect(tuple, 'explained_variance in the schema').not.toBeNull()
    expect(tuple![1]!.split(',').map((s) => s.trim())).toEqual(Array(AXES).fill('float'))
    // And the client's tuple type agrees, checked by the compiler.
    const shares: CorpusMap['explained_variance'] = [0, 0, 0]
    expect(shares).toHaveLength(AXES)
  })

  it('carries a coordinate field for every axis on each point', () => {
    const fields = /class MapPointRead\(BaseModel\):([\s\S]*?)\nclass /.exec(schema)![1]!
    for (const axis of ['x', 'y', 'z'].slice(0, AXES)) expect(fields).toMatch(new RegExp(`^\\s+${axis}: float$`, 'm'))
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
