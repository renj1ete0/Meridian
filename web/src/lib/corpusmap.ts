/**
 * The arithmetic behind the corpus map (tasks P6-26, P6-29), kept apart from
 * the canvas and from WebGL so it can be tested without either.
 */
import type { CorpusMap, MapPoint } from './api'

/** Eight published series colours; a ninth topic is "Other", never a new hue. */
export const SERIES_SLOTS = 8

/**
 * How many principal components the map carries. Mirrors
 * `meridian_core.corpusmap.COMPONENTS` and the arity of `explained_variance`;
 * a drift test reads the Python to hold the two together.
 */
export const AXES = 3

/** A colour assignment: a series slot, "other" past the eighth, or unlabelled. */
export type Swatch = { kind: 'series'; slot: number } | { kind: 'other' } | { kind: 'unlabelled' }

/** FNV-1a over UTF-16 code units: small, fast, and the same in every browser. */
export function nameHash(name: string): number {
  let hash = 0x811c9dc5
  for (let i = 0; i < name.length; i++) {
    hash ^= name.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return hash >>> 0
}

/**
 * Give each topic a colour by its *name*, never by its size or rank.
 *
 * Each name hashes to a preferred slot, and keeps it unless an earlier name
 * (alphabetically) already holds it, in which case it takes the next free one.
 * The first map assigned slots by alphabetical position, which is rank by
 * another name: a new topic sorting first moved every colour after it, and a
 * reader who learned "blue is that topic" found it orange the next morning.
 * Hashed, a newcomer can displace at most the topics it collides with.
 *
 * Hiding a topic in the legend does not call this again, so the survivors keep
 * their colours.
 */
export function assignSwatches(points: readonly MapPoint[]): Map<string | null, Swatch> {
  const topics = [...new Set(points.map((p) => p.topic).filter((t): t is string => t !== null))]
  topics.sort((a, b) => a.localeCompare(b))

  const taken = new Set<number>()
  const swatches = new Map<string | null, Swatch>()
  for (const topic of topics) {
    if (taken.size === SERIES_SLOTS) {
      swatches.set(topic, { kind: 'other' })
      continue
    }
    let slot = nameHash(topic) % SERIES_SLOTS
    while (taken.has(slot)) slot = (slot + 1) % SERIES_SLOTS
    taken.add(slot)
    swatches.set(topic, { kind: 'series', slot: slot + 1 })
  }
  if (points.some((p) => p.topic === null)) swatches.set(null, { kind: 'unlabelled' })
  return swatches
}

/** The CSS custom property a swatch draws with. Read at draw time, so a theme switch repaints. */
export function swatchVar(swatch: Swatch): string {
  if (swatch.kind === 'series') return `--series-${swatch.slot}`
  return '--text-faint'
}

/** How many points each topic contributes, largest first — the table view's rows. */
export function topicCounts(points: readonly MapPoint[]): { topic: string | null; count: number }[] {
  const counts = new Map<string | null, number>()
  for (const point of points) counts.set(point.topic, (counts.get(point.topic) ?? 0) + 1)
  return [...counts]
    .map(([topic, count]) => ({ topic, count }))
    .sort((a, b) => b.count - a.count || String(a.topic).localeCompare(String(b.topic)))
}

/** The legend's order: by name, unlabelled last — the order colours were given in. */
export function legendOrder(topics: Iterable<string | null>): (string | null)[] {
  return [...topics].sort((a, b) => {
    if (a === null) return 1
    if (b === null) return -1
    return a.localeCompare(b)
  })
}

/**
 * Map [-1, 1] coordinates onto a square plot inside a ``width × height`` box.
 *
 * Square, not stretched to the box: the axes are scaled alike, and stretching
 * one would make distances along it look larger than they are. ``y`` is
 * flipped so up on the screen is positive, as on any plot.
 */
export function toScreen(
  point: Pick<MapPoint, 'x' | 'y'>,
  width: number,
  height: number,
  padding: number,
): [number, number] {
  const side = Math.max(0, Math.min(width, height) - 2 * padding)
  const left = (width - side) / 2
  const top = (height - side) / 2
  return [left + ((point.x + 1) / 2) * side, top + ((1 - point.y) / 2) * side]
}

/**
 * The visible point nearest a cursor, within ``radius`` pixels, or null.
 *
 * A hit target larger than the dot: dots are a few pixels across, and a hover
 * that needed pixel accuracy would feel broken. The last-drawn point wins a tie,
 * because it is the one on top.
 */
export function nearest(
  points: readonly MapPoint[],
  cursor: [number, number],
  project: (p: MapPoint) => [number, number],
  radius: number,
): MapPoint | null {
  let best: MapPoint | null = null
  let bestDistance = radius * radius
  for (const point of points) {
    const [x, y] = project(point)
    const distance = (x - cursor[0]) ** 2 + (y - cursor[1]) ** 2
    if (distance <= bestDistance) {
      best = point
      bestDistance = distance
    }
  }
  return best
}

/** "3.1%" — the share of variance an axis carries. */
export function percent(share: number): string {
  return `${(share * 100).toFixed(1)}%`
}

/**
 * The honest caption: how much of the corpus is drawn, and how much of the
 * space the axes the reader is looking at can show. The flat view names two
 * shares, not three, because it draws two — quoting the third there would
 * claim structure the picture does not contain.
 */
export function caption(map: Pick<CorpusMap, 'points' | 'eligible' | 'explained_variance'>, axes: 2 | 3): string {
  const drawn = map.points.length
  const sampled = drawn < map.eligible
  const count = sampled
    ? `${drawn.toLocaleString('en')} of ${map.eligible.toLocaleString('en')} passages, sampled`
    : `${drawn.toLocaleString('en')} passages`
  const shares = map.explained_variance.slice(0, axes).map(percent)
  return `${count} · ${axes === 3 ? 'three' : 'two'} axes carry ${shares.join(', ')} of the variation`
}
