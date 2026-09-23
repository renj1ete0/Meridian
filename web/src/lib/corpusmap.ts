/**
 * The arithmetic behind the corpus map (task P6-26), kept apart from the canvas
 * so it can be tested without one.
 */
import type { MapPoint } from './api'

/** Eight published series colours; a ninth topic is "Other", never a new hue. */
export const SERIES_SLOTS = 8

/** A colour assignment: a series slot, "other" past the eighth, or unlabelled. */
export type Swatch = { kind: 'series'; slot: number } | { kind: 'other' } | { kind: 'unlabelled' }

/**
 * Give each topic a colour by its *name*, not its size or its rank.
 *
 * Alphabetical over the topics the map contains. Ranking by count would be the
 * obvious choice and the wrong one: the crawl changes which topic is largest,
 * and a reader who learned "blue is that topic" would find it orange the next
 * morning. Hiding a topic in the legend does not call this again, so the
 * survivors keep their colours.
 */
export function assignSwatches(points: readonly MapPoint[]): Map<string | null, Swatch> {
  const topics = [...new Set(points.map((p) => p.topic).filter((t): t is string => t !== null))]
  topics.sort((a, b) => a.localeCompare(b))

  const swatches = new Map<string | null, Swatch>()
  topics.forEach((topic, index) => {
    swatches.set(topic, index < SERIES_SLOTS ? { kind: 'series', slot: index + 1 } : { kind: 'other' })
  })
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

/**
 * Map [-1, 1] coordinates onto a square plot inside a ``width × height`` box.
 *
 * Square, not stretched to the box: PCA's axes are in the same units, and
 * stretching one would make distances along it look larger than they are.
 * ``y`` is flipped so up on the screen is positive, as on any plot.
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
