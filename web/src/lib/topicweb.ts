/**
 * The web of topics (task B-72): pure arithmetic behind the Map's Topics view.
 *
 * A source carries every topic its content is about, so topics overlap. The
 * API returns sources by *exact* combination, which adds up; everything here
 * is derived from those sets on the client, so choosing circles never costs a
 * request.
 */
import type { TopicMatch, TopicOverlap, TopicOverlaps } from './api'

/** Does `combination` contain every topic in `selection`? */
function covers(combination: readonly string[], selection: readonly string[]): boolean {
  return selection.every((topic) => combination.includes(topic))
}

/**
 * Sources carrying at least every topic in `selection`: the sum over each
 * exact combination that is a superset of it.
 *
 * An empty selection asks nothing of a source, so every labelled source
 * qualifies — `labelled_sources`, which is also what the sets sum to.
 */
export function carrying(data: TopicOverlaps, selection: readonly string[]): number {
  if (selection.length === 0) return data.labelled_sources
  let total = 0
  for (const overlap of data.overlaps) {
    if (covers(overlap.topics, selection)) total += overlap.sources
  }
  return total
}

export interface TopicTotal {
  topic: string
  sources: number
}

/** Each topic's sources, largest first, ties by name so the order is stable. */
export function topicTotals(overlaps: readonly TopicOverlap[]): TopicTotal[] {
  const totals = new Map<string, number>()
  for (const { topics, sources } of overlaps) {
    for (const topic of new Set(topics)) totals.set(topic, (totals.get(topic) ?? 0) + sources)
  }
  return [...totals]
    .map(([topic, sources]) => ({ topic, sources }))
    .sort((a, b) => b.sources - a.sources || a.topic.localeCompare(b.topic))
}

export interface TopicLink {
  /** `a < b`, so a pair has one key whichever way it was found. */
  a: string
  b: string
  shared: number
}

export function linkKey(link: Pick<TopicLink, 'a' | 'b'>): string {
  return `${link.a}\u0000${link.b}`
}

/** Every pair of topics that at least one source carries together. */
export function topicLinks(overlaps: readonly TopicOverlap[]): TopicLink[] {
  const shared = new Map<string, TopicLink>()
  for (const { topics, sources } of overlaps) {
    const sorted = [...new Set(topics)].sort()
    for (let i = 0; i < sorted.length; i++) {
      for (let j = i + 1; j < sorted.length; j++) {
        const link = { a: sorted[i]!, b: sorted[j]!, shared: 0 }
        const key = linkKey(link)
        const found = shared.get(key) ?? link
        found.shared += sources
        shared.set(key, found)
      }
    }
  }
  return [...shared.values()]
    .filter((link) => link.shared > 0)
    .sort((x, y) => y.shared - x.shared || linkKey(x).localeCompare(linkKey(y)))
}

/**
 * The exact combinations that contain the selection, largest first: where the
 * sources counted by `carrying` actually sit. With nothing selected, every
 * combination — the corpus's shape at a glance.
 */
export function combinationsContaining(
  overlaps: readonly TopicOverlap[],
  selection: readonly string[],
  limit = 8,
): TopicOverlap[] {
  return overlaps
    .filter((overlap) => overlap.sources > 0 && covers(overlap.topics, selection))
    .sort(
      (x, y) =>
        y.sources - x.sources ||
        x.topics.length - y.topics.length ||
        x.topics.join('\u0000').localeCompare(y.topics.join('\u0000')),
    )
    .slice(0, limit)
}

/** Toggle one topic in a selection, keeping the result sorted. */
export function toggleTopic(selection: readonly string[], topic: string): string[] {
  return selection.includes(topic) ? selection.filter((t) => t !== topic) : [...selection, topic].sort()
}

/** Clicking a link means "these two": the selection becomes exactly its ends. */
export function selectLink(link: Pick<TopicLink, 'a' | 'b'>): string[] {
  return [link.a, link.b].sort()
}

/** Find's URL for a search inside every selected topic at once. */
export function searchHref(selection: readonly string[], query = ''): string {
  const params = new URLSearchParams()
  const q = query.trim()
  if (q) params.set('q', q)
  for (const topic of selection) params.append('topic', topic)
  if (selection.length > 0) params.set('topic_match', 'all')
  const suffix = params.toString()
  return suffix ? `/?${suffix}` : '/'
}

/**
 * Find's URL for a search as run (`B-95`): the words, the topics, and whether
 * a source must carry all of them. The inverse of {@link findParams}, so a
 * search can be shared, bookmarked, and come back on Back.
 */
export function findHref(query: string, topics: readonly string[], match: TopicMatch): string {
  const params = new URLSearchParams()
  const q = query.trim()
  if (q) params.set('q', q)
  for (const topic of topics) params.append('topic', topic)
  if (topics.length > 0 && match === 'all') params.set('topic_match', 'all')
  const suffix = params.toString()
  return suffix ? `/?${suffix}` : '/'
}

/** What Find reads back out of its URL: `q`, repeated `topic`, `topic_match`. */
export function findParams(search: string): { q: string; topics: string[]; match: TopicMatch } {
  const params = new URLSearchParams(search)
  const topics = [
    ...new Set(
      params
        .getAll('topic')
        .map((t) => t.trim())
        .filter(Boolean),
    ),
  ]
  return {
    q: params.get('q')?.trim() ?? '',
    topics,
    match: params.get('topic_match') === 'all' ? 'all' : 'any',
  }
}

// --------------------------------------------------------------------------
// Layout

export interface PlacedTopic extends TopicTotal {
  x: number
  y: number
  r: number
  /** Where the name sits; the count goes on the line below it. */
  label: { x: number; y: number; anchor: 'start' | 'middle' | 'end' }
}

/** Rough width of a label in Archivo at 12.5px — enough to leave it room. */
export function labelWidth(text: string): number {
  return Math.min(150, Math.ceil(text.length * 6.9) + 4)
}

/** Two lines of label under or over a circle: the name and the count. */
const LINES = 34

/** Room between neighbouring circles, so even the shortest link is a visible line to press. */
const GAP = 44

/**
 * Circles on a ring, largest at twelve o'clock and clockwise from there.
 *
 * Area proportional to sources (radius ∝ √sources), with a floor so a topic
 * holding one source is still something a finger can hit. A ring rather than a
 * force layout: it is stable between loads, and with a handful of topics every
 * pair is a visible chord.
 *
 * **Labels sit outside the ring** where there is width for them, so the
 * chords — which all run inside it — never cross a name. On a narrow screen
 * there is no room at the sides, and each name goes under its circle instead.
 */
export function layoutTopics(
  totals: readonly TopicTotal[],
  width: number,
  height: number,
  { minR = 14, below = width < 520 }: { minR?: number; below?: boolean } = {},
): PlacedTopic[] {
  const n = totals.length
  if (n === 0) return []
  const cx = width / 2
  const max = Math.max(1, ...totals.map((t) => t.sources))
  const widest = Math.max(...totals.map((t) => labelWidth(t.topic)))

  const padX = below ? 0 : widest + 10
  const padTop = below ? 4 : LINES
  const halfW = Math.max(0, width / 2 - padX)
  const halfH = Math.max(0, (height - padTop - LINES) / 2)
  const cy = padTop + halfH

  if (n === 1) {
    const r = Math.max(minR, Math.min(halfW, halfH) - 8)
    return [{ ...totals[0]!, x: cx, y: cy, r, label: { x: cx, y: cy + r + 15, anchor: 'middle' } }]
  }

  // The ring is an ellipse filling the box. Neighbours on it must not
  // overlap, so the chord between two of them is at least two of the largest
  // radius plus room for a link — measured on the shorter axis, which is conservative.
  let maxR = Math.max(minR, Math.min(Math.min(halfW, halfH) * 0.34, 84))
  // Under a circle, a name reaches half its width to either side.
  const rx = () => Math.max(0, below ? width / 2 - Math.max(maxR, widest / 2 + 2) : halfW - maxR)
  const ry = () => Math.max(0, halfH - maxR)
  const chord = () => 2 * Math.min(rx(), ry()) * Math.sin(Math.PI / n)
  // Tighter on a phone: sizes that still differ matter more than long links.
  const room = below ? GAP / 2 : GAP
  while (maxR > minR && chord() < 2 * maxR + room) maxR -= 1

  return totals.map((total, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / n
    const cos = Math.cos(angle)
    const sin = Math.sin(angle)
    const r = Math.max(minR, maxR * Math.sqrt(total.sources / max))
    const x = cx + rx() * cos
    const y = cy + ry() * sin
    let label: PlacedTopic['label']
    if (below) label = { x, y: y + r + 15, anchor: 'middle' }
    else if (cos > 0.35) label = { x: x + r + 8, y: y + 2, anchor: 'start' }
    else if (cos < -0.35) label = { x: x - r - 8, y: y + 2, anchor: 'end' }
    else if (sin < 0) label = { x, y: y - r - 20, anchor: 'middle' }
    else label = { x, y: y + r + 15, anchor: 'middle' }
    return { ...total, x, y, r, label }
  })
}

/** Where a link between two circles starts and ends: at their rims, not their centres. */
export function rimToRim(
  a: Pick<PlacedTopic, 'x' | 'y' | 'r'>,
  b: Pick<PlacedTopic, 'x' | 'y' | 'r'>,
): { x1: number; y1: number; x2: number; y2: number } {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const d = Math.hypot(dx, dy) || 1
  return {
    x1: a.x + (dx / d) * a.r,
    y1: a.y + (dy / d) * a.r,
    x2: b.x - (dx / d) * b.r,
    y2: b.y - (dy / d) * b.r,
  }
}

/** Link stroke width: 1.5 for the thinnest overlap, up to 10 for the largest. */
export function linkWidth(shared: number, largest: number): number {
  if (largest <= 0) return 1.5
  return 1.5 + 8.5 * Math.sqrt(shared / largest)
}
