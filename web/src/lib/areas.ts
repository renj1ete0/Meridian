/**
 * Areas and bridges: the wire types and the geometry of the Map screen
 * (tasks P6-30, P6-31, P6-34), kept apart from the SVG so they can be tested
 * without a browser.
 */
import { ApiError } from './api'

// --------------------------------------------------------------------------
// Wire types — mirror `meridian_core/schemas/areas.py`; tests/api.test.ts
// reads the Python to hold the field lists together.

export interface AreaBuild {
  build_id: number
  computed_at: string
  passages: number
  regions: number
  areas: number
  leaves: number
}

export interface Area {
  area_id: number
  level: number
  parent_id: number | null
  /** The three most distinctive terms joined — a description, not a chosen topic. */
  name: string
  terms: string[]
  passages: number
  sources: number
  tier_mix: Record<string, number>
  /** Passages whose topics were decided, and of those how many are about a topic (`P6-42`); null when not measured. */
  examined: number | null
  on_topic: number | null
  /** `{topic: passages}`, most first; a passage on two topics counts under both. */
  topic_mix: Record<string, number>
  newest_at: string | null
  x: number
  y: number
  children: number
  weak: boolean
  stale: boolean
  reasons: string[]
}

export interface AreaCrumb {
  area_id: number
  level: number
  name: string
}

export interface AreaLink {
  area_a: number
  area_b: number
  /** Claims in the graph whose evidence spans the two. The only kind that says a source states a link. */
  cited_claims: number
  cited_sources: number
  /** Most-similar passage pairs found across the two: near in meaning, nothing more. */
  similar_pairs: number
  similarity: number
  shared_terms: string[]
}

export interface AreasLevel {
  build: AreaBuild | null
  level: number
  parent: Area | null
  path: AreaCrumb[]
  areas: Area[]
  links: AreaLink[]
  levels: number
  passages_needed: number
  stale_after_days: number
  weak_below_sources: number
}

export interface AreaPassage {
  chunk_id: number
  source_id: number
  title: string | null
  url: string
  source_tier: string
  snippet: string
}

export interface AreaDetail {
  area: Area
  path: AreaCrumb[]
  passages: AreaPassage[]
}

export interface AreaJumpHit {
  area: Area
  path: AreaCrumb[]
  match: 'name' | 'passages'
  hits: number
}

export interface AreaJump {
  query: string
  hits: AreaJumpHit[]
}

export interface BridgeClaim {
  edge_id: number
  from_node: number
  from_name: string
  relation_type: string
  to_node: number
  to_name: string
  sources: number
  citations: number
  tiers: string[]
}

export interface BridgePassage {
  chunk_id: number
  source_id: number
  title: string | null
  source_tier: string
  snippet: string
}

export interface BridgePair {
  score: number
  a: BridgePassage
  b: BridgePassage
}

export interface Bridge {
  a: AreaCrumb
  b: AreaCrumb
  claims: BridgeClaim[]
  cited_sources: number
  similar: BridgePair[]
  shared_terms: string[]
  similarity: number
}

export const AREA_FIELDS = [
  'area_id',
  'level',
  'parent_id',
  'name',
  'terms',
  'passages',
  'sources',
  'tier_mix',
  'examined',
  'on_topic',
  'topic_mix',
  'newest_at',
  'x',
  'y',
  'children',
  'weak',
  'stale',
  'reasons',
] as const
export const AREAS_LEVEL_FIELDS = [
  'build',
  'level',
  'parent',
  'path',
  'areas',
  'links',
  'levels',
  'passages_needed',
  'stale_after_days',
  'weak_below_sources',
] as const
export const AREA_BUILD_FIELDS = ['build_id', 'computed_at', 'passages', 'regions', 'areas', 'leaves'] as const
export const AREA_LINK_FIELDS = [
  'area_a',
  'area_b',
  'cited_claims',
  'cited_sources',
  'similar_pairs',
  'similarity',
  'shared_terms',
] as const
export const BRIDGE_FIELDS = ['a', 'b', 'claims', 'cited_sources', 'similar', 'shared_terms', 'similarity'] as const
export const BRIDGE_CLAIM_FIELDS = [
  'edge_id',
  'from_node',
  'from_name',
  'relation_type',
  'to_node',
  'to_name',
  'sources',
  'citations',
  'tiers',
] as const

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T
export type AssertArea = Expect<Equal<keyof Area, (typeof AREA_FIELDS)[number]>>
export type AssertAreasLevel = Expect<Equal<keyof AreasLevel, (typeof AREAS_LEVEL_FIELDS)[number]>>
export type AssertAreaBuild = Expect<Equal<keyof AreaBuild, (typeof AREA_BUILD_FIELDS)[number]>>
export type AssertAreaLink = Expect<Equal<keyof AreaLink, (typeof AREA_LINK_FIELDS)[number]>>
export type AssertBridge = Expect<Equal<keyof Bridge, (typeof BRIDGE_FIELDS)[number]>>
export type AssertBridgeClaim = Expect<Equal<keyof BridgeClaim, (typeof BRIDGE_CLAIM_FIELDS)[number]>>

async function getJson<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { headers: { accept: 'application/json' }, ...init })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(0, 'The API is unreachable. Check it is running.')
  }
  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json())?.detail
    } catch {
      detail = undefined
    }
    throw new ApiError(
      response.status,
      typeof detail === 'string' && detail ? detail : `The API returned ${response.status}.`,
    )
  }
  return (await response.json()) as T
}

export function getAreas(parent: number | null, init?: RequestInit): Promise<AreasLevel> {
  return getJson<AreasLevel>(parent === null ? '/api/explore/areas' : `/api/explore/areas?parent=${parent}`, init)
}

export function getArea(areaId: number, init?: RequestInit): Promise<AreaDetail> {
  return getJson<AreaDetail>(`/api/explore/areas/${areaId}`, init)
}

export function jumpToArea(query: string, init?: RequestInit): Promise<AreaJump> {
  return getJson<AreaJump>(`/api/explore/areas/jump?q=${encodeURIComponent(query)}`, init)
}

export function getBridge(a: number, b: number, init?: RequestInit): Promise<Bridge> {
  return getJson<Bridge>(`/api/explore/bridges/${a}/${b}`, init)
}

// --------------------------------------------------------------------------
// Geometry

export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface Placed {
  area: Area
  x: number
  y: number
  r: number
}

/**
 * Circle radius per unit of √passages, so that *area* is proportional to
 * passages collected (the operator's decision: size = passages, with a key).
 * The scale is set so the largest circle on screen has radius `maxRadius`;
 * the size key is drawn with the same scale, so it stays true.
 */
export function radiusScale(passages: readonly number[], maxRadius: number): number {
  const largest = Math.max(0, ...passages)
  return largest > 0 ? maxRadius / Math.sqrt(largest) : 0
}

export function radiusOf(passages: number, scale: number): number {
  return Math.sqrt(Math.max(0, passages)) * scale
}

const NICE = [1, 2, 5]

/** The largest 1-2-5 number at or below `value`. */
export function niceBelow(value: number): number {
  if (value < 1) return 0
  const power = 10 ** Math.floor(Math.log10(value))
  let best = power
  for (const step of NICE) if (step * power <= value) best = step * power
  return best
}

/**
 * Three round values for the size key, spanning what is on screen: the
 * largest round number at or below the biggest circle, then two steps down
 * by about a factor of ten each, never below the smallest circle's order.
 */
export function keyValues(passages: readonly number[]): number[] {
  const positive = passages.filter((p) => p > 0)
  if (positive.length === 0) return []
  const top = niceBelow(Math.max(...positive))
  const floor = niceBelow(Math.min(...positive))
  const values = [top, niceBelow(top / 5), niceBelow(top / 25)].filter((v) => v >= Math.max(1, floor / 2))
  return [...new Set(values)].sort((a, b) => a - b)
}

/**
 * The largest radius that leaves room: circles together cover at most `fill`
 * of the box, and none is wider than `cap` of its shorter side. A fixed
 * radius drew four areas in a narrow canvas on top of one another.
 */
export function fitRadius(
  passages: readonly number[],
  width: number,
  height: number,
  { fill = 0.22, cap = 0.18 }: { fill?: number; cap?: number } = {},
): number {
  const total = passages.reduce((sum, p) => sum + Math.max(0, p), 0)
  const largest = Math.max(0, ...passages)
  if (total <= 0 || largest <= 0) return 0
  // Σ π (k√p)² = fill · W · H  →  k = √(fill·W·H / (π Σp)); the largest is k√max.
  const k = Math.sqrt((fill * width * height) / (Math.PI * total))
  return Math.min(k * Math.sqrt(largest), cap * Math.min(width, height))
}

/**
 * Key values that can be drawn at the canvas's scale within `maxRadius`:
 * round values from the largest that fits downwards, never below what the
 * smallest circle shows.
 */
export function keyValuesAtScale(passages: readonly number[], scale: number, maxRadius: number): number[] {
  if (scale <= 0) return []
  const positive = passages.filter((p) => p > 0)
  if (!positive.length) return []
  // The largest round value whose circle fits, then two steps down.
  const fits = Math.floor((maxRadius / scale) ** 2)
  const top = niceBelow(Math.min(Math.max(...positive), fits))
  const values = [top, niceBelow(top / 5), niceBelow(top / 25)].filter((v) => v >= 1)
  return [...new Set(values)].sort((a, b) => a - b)
}

/**
 * Place circles in a box from their stored positions, then push overlapping
 * ones apart. Deterministic: fixed iteration count, pairs in index order.
 *
 * Stored positions are in [-1, 1] and come from the build (stable across
 * rebuilds); this only spaces them so no circle hides another, keeping each
 * as near its stored place as the spacing allows.
 */
export function placeAreas(
  areas: readonly Area[],
  width: number,
  height: number,
  {
    maxRadius,
    gap = 10,
    pad = 24,
    iterations = 240,
    avoid = [],
  }: { maxRadius: number; gap?: number; pad?: number; iterations?: number; avoid?: readonly Rect[] },
): { placed: Placed[]; scale: number } {
  const scale = radiusScale(
    areas.map((a) => a.passages),
    maxRadius,
  )
  const cx = width / 2
  const cy = height / 2
  const spanX = width / 2 - pad - maxRadius * 0.5
  const spanY = height / 2 - pad - maxRadius * 0.5
  const placed: Placed[] = areas.map((area) => ({
    area,
    x: cx + area.x * Math.max(spanX, 0),
    // Screen y grows downwards; the build's y grows upwards.
    y: cy - area.y * Math.max(spanY, 0),
    r: Math.max(radiusOf(area.passages, scale), 3),
  }))
  relax(placed, { width, height, gap, pad, iterations, avoid })
  return { placed, scale }
}

/**
 * Push overlapping circles apart, keep them out of `avoid` and inside the
 * box; with `targets`, also draw each back towards its own target by `pull`
 * of the distance per step, so a group spaced apart stays a group.
 * Deterministic: fixed iteration count, pairs in index order. Mutates.
 */
export function relax(
  placed: Placed[],
  {
    width,
    height,
    gap,
    pad,
    iterations,
    avoid = [],
    targets,
    pull = 0,
  }: {
    width: number
    height: number
    gap: number
    pad: number
    iterations: number
    avoid?: readonly Rect[]
    targets?: readonly { x: number; y: number }[]
    pull?: number
  },
): void {
  for (let step = 0; step < iterations; step++) {
    let moved = false
    if (targets && pull > 0) {
      for (let i = 0; i < placed.length; i++) {
        const p = placed[i]!
        const t = targets[i]!
        p.x += (t.x - p.x) * pull
        p.y += (t.y - p.y) * pull
      }
    }
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i]!
        const b = placed[j]!
        let dx = b.x - a.x
        let dy = b.y - a.y
        const needed = a.r + b.r + gap
        // Cheap reject first: a finer level has tens of thousands of pairs.
        if (dx > needed || dx < -needed || dy > needed || dy < -needed) continue
        let distance = Math.hypot(dx, dy)
        if (distance >= needed) continue
        if (distance < 1e-6) {
          // Coincident: separate along a fixed direction chosen by index.
          const angle = (j * 2.399963) % (2 * Math.PI)
          dx = Math.cos(angle)
          dy = Math.sin(angle)
          distance = 1
        }
        const push = (needed - distance) / 2
        const ux = dx / distance
        const uy = dy / distance
        a.x -= ux * push
        a.y -= uy * push
        b.x += ux * push
        b.y += uy * push
        moved = true
      }
    }
    for (const p of placed) {
      // Out of anything drawn over the canvas (the size key), by the
      // shorter way; then back inside the box.
      for (const rect of avoid) {
        const nearestX = Math.min(rect.x + rect.w, Math.max(rect.x, p.x))
        const nearestY = Math.min(rect.y + rect.h, Math.max(rect.y, p.y))
        if (Math.hypot(p.x - nearestX, p.y - nearestY) >= p.r + gap) continue
        const right = rect.x + rect.w + p.r + gap - p.x
        const up = p.y - (rect.y - p.r - gap)
        if (right < up) p.x += right
        else p.y -= up
        moved = true
      }
      p.x = Math.min(width - pad - p.r, Math.max(pad + p.r, p.x))
      p.y = Math.min(height - pad - p.r, Math.max(pad + p.r, p.y))
    }
    if (!moved) break
  }
}

// --------------------------------------------------------------------------
// Semantic zoom: every cluster of a finer level at once, each placed where
// its parent was drawn.

/** How far a child may sit from its parent's centre, in parent radii. */
export const CHILD_SPREAD = 0.72

/**
 * Where a child is drawn: its parent's centre on screen plus the child's
 * stored offset, in units of the parent's radius.
 *
 * A child's x, y are in its *parent's* frame, [-1, 1]² among its siblings
 * (the build lays out each sibling group on its own), so they mean nothing
 * on screen until composed with where the parent was drawn. The offset is
 * clamped to the unit disc, since a square's corners lie outside the circle;
 * that keeps every child's centre inside its parent's circle.
 */
export function composeChild(
  parent: { x: number; y: number; r: number },
  child: { x: number; y: number },
  spread = CHILD_SPREAD,
): { x: number; y: number } {
  const length = Math.hypot(child.x, child.y)
  const unit = length > 1 ? 1 / length : 1
  return {
    x: parent.x + child.x * unit * parent.r * spread,
    // Screen y grows downwards; the build's y grows upwards.
    y: parent.y - child.y * unit * parent.r * spread,
  }
}

export interface Layout {
  level: number
  placed: Placed[]
  /** Radius per √passage at this level, which the size key draws with. */
  scale: number
}

/**
 * The next level down, laid out from this one: each parent's children
 * composed into its circle ({@link composeChild}), sized at `shrink` of the
 * parent level's scale, then spaced apart with a pull back to where they
 * were composed. A parent with nothing under it is carried down as it is,
 * so no part of the corpus drops out of view at a finer level.
 */
export function placeNested(
  parents: Layout,
  childrenOf: ReadonlyMap<number, readonly Area[]>,
  {
    width,
    height,
    shrink,
    spread = CHILD_SPREAD,
    gap,
    pad,
    iterations = 160,
    pull = 0.08,
    avoid = [],
  }: {
    width: number
    height: number
    shrink: number
    spread?: number
    gap: number
    pad: number
    iterations?: number
    pull?: number
    avoid?: readonly Rect[]
  },
): Layout {
  const scale = parents.scale * shrink
  const placed: Placed[] = []
  const targets: { x: number; y: number }[] = []
  for (const parent of parents.placed) {
    const children = parent.area.children > 0 ? childrenOf.get(parent.area.area_id) : undefined
    if (!children?.length) {
      placed.push({ ...parent })
      targets.push({ x: parent.x, y: parent.y })
      continue
    }
    for (const child of children) {
      const at = composeChild(parent, child, spread)
      placed.push({ area: child, x: at.x, y: at.y, r: Math.max(radiusOf(child.passages, scale), 2.5) })
      targets.push(at)
    }
  }
  relax(placed, { width, height, gap, pad, iterations, avoid, targets, pull })
  return { level: parents.level + 1, placed, scale }
}

/**
 * A circle round a group: centred on the group's area-weighted centroid and
 * just large enough to hold every member. Not the smallest enclosing circle,
 * which is not worth its cost for an outline drawn at low contrast.
 */
export function enclosing(
  circles: readonly { x: number; y: number; r: number }[],
  pad = 0,
): { x: number; y: number; r: number } {
  if (!circles.length) return { x: 0, y: 0, r: 0 }
  let weight = 0
  let x = 0
  let y = 0
  for (const c of circles) {
    const w = Math.max(c.r, 0.5) ** 2
    weight += w
    x += c.x * w
    y += c.y * w
  }
  x /= weight
  y /= weight
  let r = 0
  for (const c of circles) r = Math.max(r, Math.hypot(c.x - x, c.y - y) + c.r)
  return { x, y, r: r + pad }
}

/** The ancestor of `areaId` at `level`, walking parent links; null if the chain breaks. */
export function ancestorAt(
  areaId: number,
  level: number,
  areas: ReadonlyMap<number, Pick<Area, 'level' | 'parent_id'>>,
): number | null {
  let id: number | null = areaId
  for (let guard = 0; id !== null && guard < 16; guard++) {
    const area = areas.get(id)
    if (!area) return null
    if (area.level === level) return id
    if (area.level < level) return null
    id = area.parent_id
  }
  return null
}

export interface MorphItem {
  id: number
  /** Packed inside its ancestor's circle. */
  from: { x: number; y: number; r: number }
  /** Where the finer level draws it. */
  to: { x: number; y: number; r: number }
}

/**
 * The split from a coarse level to a finer one: each fine circle starts as
 * its final group shrunk uniformly into its ancestor's circle, so on the
 * first frame the children fill the parent they came from and on the last
 * they are where the finer level draws them. Played backwards, it is the
 * merge. A fine circle with no drawn ancestor starts where it ends.
 */
export function splitMorph(
  coarse: readonly Placed[],
  fine: readonly Placed[],
  ancestorOf: (areaId: number) => number | null,
): MorphItem[] {
  const parents = new Map(coarse.map((p) => [p.area.area_id, p]))
  const groups = new Map<number, Placed[]>()
  for (const p of fine) {
    const a = ancestorOf(p.area.area_id)
    if (a === null || !parents.has(a)) continue
    const list = groups.get(a)
    if (list) list.push(p)
    else groups.set(a, [p])
  }
  const hulls = new Map([...groups].map(([a, list]) => [a, enclosing(list)]))
  return fine.map((p) => {
    const to = { x: p.x, y: p.y, r: p.r }
    const a = ancestorOf(p.area.area_id)
    const parent = a === null ? undefined : parents.get(a)
    const hull = a === null ? undefined : hulls.get(a)
    if (!parent || !hull || hull.r <= 0) return { id: p.area.area_id, from: to, to }
    const k = parent.r / hull.r
    return {
      id: p.area.area_id,
      from: { x: parent.x + (p.x - hull.x) * k, y: parent.y + (p.y - hull.y) * k, r: p.r * k },
      to,
    }
  })
}

/** Ease in and out, cubic: slow to leave, slow to arrive. */
export function easeInOut(t: number): number {
  const c = Math.min(1, Math.max(0, t))
  return c < 0.5 ? 4 * c * c * c : 1 - (-2 * c + 2) ** 3 / 2
}

/**
 * One frame of a split, at `p` from 0 (the coarse level) to 1 (the fine).
 * A merge plays `p` from 1 to 0. Parents fade as their children leave them;
 * children fade in over the first part of the way, so they do not appear on
 * top of a parent still fully drawn.
 */
export function morphAt(
  items: readonly MorphItem[],
  p: number,
): { circles: { id: number; x: number; y: number; r: number }[]; parentOpacity: number; childOpacity: number } {
  const e = Math.min(1, Math.max(0, p))
  return {
    circles: items.map(({ id, from, to }) => ({
      id,
      x: from.x + (to.x - from.x) * e,
      y: from.y + (to.y - from.y) * e,
      r: from.r + (to.r - from.r) * e,
    })),
    parentOpacity: 1 - e,
    childOpacity: Math.min(1, e / 0.35),
  }
}

/** The motion setting the reader asked the system for. */
export function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
  } catch {
    return false
  }
}

/** `?level=` in the URL: the level of detail to show, 1-based. */
export function levelFromSearch(search: string): number | null {
  const value = new URLSearchParams(search).get('level')
  const n = value ? Number(value) : NaN
  return Number.isInteger(n) && n >= 1 && n <= 9 ? n : null
}

/**
 * Every cluster at `depth` under the root level: the root's own clusters,
 * then their children, and so on, in the order the API gave them. A parent
 * whose children are not loaded, or which has none, stands in for them, so
 * the whole of what the root holds is always there.
 */
export function rowsAt(root: AreasLevel, byParent: ReadonlyMap<number, AreasLevel>, depth: number): Area[] {
  let rows = root.areas
  for (let level = root.level; level < depth; level++) {
    rows = rows.flatMap((a) => (a.children > 0 ? (byParent.get(a.area_id)?.areas ?? [a]) : [a]))
  }
  return rows
}

/** Whether every parent down to `depth` has its children loaded. */
export function loadedTo(root: AreasLevel, byParent: ReadonlyMap<number, AreasLevel>, depth: number): boolean {
  let rows = root.areas
  for (let level = root.level; level < depth; level++) {
    const next: Area[] = []
    for (const a of rows) {
      if (a.children === 0) next.push(a)
      else {
        const children = byParent.get(a.area_id)
        if (!children) return false
        next.push(...children.areas)
      }
    }
    rows = next
  }
  return true
}

/**
 * Each level fetched once: the same parent asked for twice shares one
 * request, and a failed request is forgotten so it can be tried again.
 * Requests are not cancelled when the view moves on: what was asked for is
 * likely the next thing the reader zooms to.
 */
export class AreaCache {
  private readonly pending = new Map<string, Promise<AreasLevel>>()
  private readonly fetchLevel: (parent: number | null) => Promise<AreasLevel>

  constructor(fetchLevel: (parent: number | null) => Promise<AreasLevel> = (p) => getAreas(p)) {
    this.fetchLevel = fetchLevel
  }

  get(parent: number | null): Promise<AreasLevel> {
    const key = parent === null ? 'top' : String(parent)
    let request = this.pending.get(key)
    if (!request) {
      request = this.fetchLevel(parent)
      this.pending.set(key, request)
      request.catch(() => this.pending.delete(key))
    }
    return request
  }

  /** The children of every parent from the root's level down to `depth`. */
  async descend(root: AreasLevel, depth: number): Promise<Map<number, AreasLevel>> {
    const out = new Map<number, AreasLevel>()
    let rows = root.areas
    for (let level = root.level; level < depth; level++) {
      const parents = rows.filter((a) => a.children > 0)
      const levels = await Promise.all(parents.map((a) => this.get(a.area_id)))
      parents.forEach((a, i) => out.set(a.area_id, levels[i]!))
      rows = rows.flatMap((a) => (a.children > 0 ? (out.get(a.area_id)?.areas ?? []) : [a]))
    }
    return out
  }
}

/** "All fields · level 2 of 3 · 81 subfields" when every parent is open at once. */
export function zoomCaption(root: AreasLevel, depth: number, count: number): string {
  if (depth <= root.level) return levelCaption(root)
  const where = root.parent ? root.parent.name : 'All fields'
  return `${where} · level ${depth} of ${root.levels} · ${count.toLocaleString('en')} ${levelNoun(depth, count)}`
}

/** How a circle's name is drawn: inside it when it fits across, else below. */
export interface Label {
  lines: string[]
  fontSize: number
  lineHeight: number
  /** Baseline of the first line. */
  y: number
  inside: boolean
  /** Whether the mono caption under the name ("7 subfields inside") is drawn. */
  caption: boolean
  box: Rect
}

/** An estimate of a label's width per character, as a share of its font size. */
const CHAR_WIDTH = 0.48
/** The width of one character of the 10.5px mono caption. */
const CAPTION_CHAR = 6.4

/**
 * Words wrapped to lines of at most `maxChars` (a longer single word gets a
 * line of its own), or null when they need more than `maxLines`. With
 * `truncate`, the lines that fit are kept and the last ends in an ellipsis.
 */
export function wrapWords(name: string, maxChars: number, maxLines: number, truncate = false): string[] | null {
  const words = name.split(/\s+/).filter(Boolean)
  const lines: string[] = []
  let current = ''
  for (const word of words) {
    const next = current ? `${current} ${word}` : word
    if (next.length <= maxChars || !current) current = next
    else {
      lines.push(current)
      current = word
    }
  }
  if (current) lines.push(current)
  if (lines.length <= maxLines) return lines
  if (!truncate) return null
  const kept = lines.slice(0, maxLines)
  kept[maxLines - 1] = `${kept[maxLines - 1]!.replace(/[,&·]$/, '').trimEnd()}…`
  return kept
}

/**
 * Where a circle's label goes and how large, before any overlap is judged:
 * inside the circle, wrapped to at most three lines, when it fits across and
 * down; otherwise hung below it on at most two.
 */
export function labelOf(p: { x: number; y: number; r: number }, name: string, captionChars = 0): Label {
  const inside = insideLabel(p, name, captionChars)
  if (inside) return inside
  const caption = captionChars > 0
  const fontSize = p.r >= 22 ? 13 : 11.5
  const lineHeight = fontSize + 3
  const lines = wrapWords(name, 24, p.r >= 22 ? 2 : 1, true)!
  const longest = Math.max(...lines.map((line) => line.length))
  const width = Math.max(longest * fontSize * CHAR_WIDTH, captionChars * CAPTION_CHAR) + 8
  const y = p.y + p.r + fontSize + 1
  return {
    lines,
    fontSize,
    lineHeight,
    y,
    inside: false,
    caption,
    box: { x: p.x - width / 2, y: y - fontSize, w: width, h: lines.length * lineHeight + (caption ? 13 : 0) },
  }
}

function insideLabel(p: { x: number; y: number; r: number }, name: string, captionChars: number): Label | null {
  // The size a circle of this radius reads best at, then smaller, down to 10px.
  const first = p.r >= 60 ? 15 : p.r >= 34 ? 13 : 11
  for (const fontSize of [first, 11, 10]) {
    if (fontSize > first) continue
    const label = insideAt(p, name, captionChars, fontSize)
    if (label) return label
  }
  return null
}

function insideAt(
  p: { x: number; y: number; r: number },
  name: string,
  captionChars: number,
  fontSize: number,
): Label | null {
  const lineHeight = fontSize + 3
  // A band across the middle of the circle, narrower than its diameter so
  // the outer lines do not touch the rim.
  const across = p.r * 2 * 0.84
  // A caption that would not fit across is left off rather than spilling out.
  const caption = captionChars > 0 && captionChars * CAPTION_CHAR + 6 <= across
  const maxChars = Math.floor((across - 6) / (fontSize * CHAR_WIDTH))
  if (maxChars < 5) return null
  const lines = wrapWords(name, maxChars, 3)
  if (!lines) return null
  const longest = Math.max(...lines.map((line) => line.length))
  const width = longest * fontSize * CHAR_WIDTH + 6
  const height = lines.length * lineHeight + (caption ? 13 : 0)
  if (width > across || height > p.r * 2 * 0.78) return null
  const y = p.y - height / 2 + fontSize * 0.85
  return {
    lines,
    fontSize,
    lineHeight,
    y,
    inside: true,
    caption,
    box: { x: p.x - width / 2, y: y - fontSize, w: width, h: height },
  }
}

/** The length of "7 subfields inside" or "1,234 passages", near enough to size a box. */
function captionLength(area: Area): number {
  return area.children > 0 ? String(area.children).length + 17 : area.passages.toLocaleString('en').length + 9
}

function overlaps(a: Rect, b: Rect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
}

function boxHitsCircle(box: Rect, c: { x: number; y: number; r: number }): boolean {
  const nx = Math.min(box.x + box.w, Math.max(box.x, c.x))
  const ny = Math.min(box.y + box.h, Math.max(box.y, c.y))
  return Math.hypot(c.x - nx, c.y - ny) < c.r
}

/**
 * The labels that can be drawn without one covering another: largest circle
 * first, each kept only if its box is clear of every label already kept and
 * of `reserved`, and inside the canvas's width. Where circles are dense
 * (`crowded`), a label hung below its circle must also clear every other
 * circle, or it would read as that circle's name. The rest are read by
 * hovering.
 */
export function fitLabels(
  placed: readonly Placed[],
  {
    width,
    captions,
    crowded = placed.length > 40,
    reserved = [],
  }: { width: number; captions: boolean; crowded?: boolean; reserved?: readonly Rect[] },
): Map<number, Label> {
  const order = [...placed].sort((a, b) => b.r - a.r || a.area.area_id - b.area.area_id)
  const kept = new Map<number, Label>()
  const boxes: Rect[] = [...reserved]
  for (const p of order) {
    const label = labelOf(p, p.area.name, captions ? captionLength(p.area) : 0)
    const box = label.box
    if (box.x < 0 || box.x + box.w > width) continue
    if (boxes.some((b) => overlaps(b, box))) continue
    if (crowded && !label.inside && placed.some((o) => o !== p && boxHitsCircle(box, o))) continue
    kept.set(p.area.area_id, label)
    boxes.push(box)
  }
  return kept
}

/**
 * The part of the line between two circles that lies outside both, or null
 * when they touch and there is no gap to draw in.
 */
export function edgeToEdge(
  a: { x: number; y: number; r: number },
  b: { x: number; y: number; r: number },
): [number, number, number, number] | null {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const distance = Math.hypot(dx, dy)
  if (distance <= a.r + b.r + 1) return null
  const ux = dx / distance
  const uy = dy / distance
  return [a.x + ux * a.r, a.y + uy * a.r, b.x - ux * b.r, b.y - uy * b.r]
}

/** Stroke width for a link: more independent sources, thicker, within limits. */
export function linkWidth(link: AreaLink): number {
  if (link.cited_claims > 0) return Math.min(8, 1.5 + Math.sqrt(link.cited_sources) * 1.5)
  return 1.4
}

/** A name split into at most two lines for a circle label. */
export function labelLines(name: string, maxChars = 26): string[] {
  const parts = name.split(' · ')
  const lines: string[] = []
  let current = ''
  for (const part of parts) {
    const next = current ? `${current} · ${part}` : part
    if (next.length <= maxChars || !current) current = next
    else {
      lines.push(current)
      current = part
    }
  }
  if (current) lines.push(current)
  if (lines.length <= 2) return lines
  return [lines[0]!, `${lines[1]!}…`]
}

/**
 * What a person calls a cluster at a level: a field, then the subfields
 * inside it, then the themes inside those. The API and the URL still say
 * "area"; only the words on screen changed, so old links keep working.
 */
export function levelNoun(level: number, count = 1): string {
  const noun = level <= 1 ? 'field' : level === 2 ? 'subfield' : 'theme'
  return count === 1 ? noun : `${noun}s`
}

/** "Engineering · level 2 of 3 · 7 subfields" and the like. */
export function levelCaption(level: AreasLevel): string {
  const count = level.areas.length
  if (level.parent === null) {
    const build = level.build
    const inside = build
      ? ` · ${build.areas.toLocaleString('en')} ${levelNoun(2, build.areas)} in ${build.regions} ${levelNoun(1, build.regions)}`
      : ''
    return `All fields · level 1 of ${level.levels}${inside}`
  }
  return `${level.parent.name} · level ${level.level} of ${level.levels} · ${count} ${levelNoun(level.level, count)}`
}

// --------------------------------------------------------------------------
// Steering from the map (P6-35) — mirrors the steering schemas in areas.py

export interface AreaTopicShare {
  topic: string | null
  passages: number
}

export interface AreaSteering {
  area_id: number
  /** The topic holding at least `dominant_share` of the area, or null: then nothing weights it. */
  topic: string | null
  dominant_share: number
  topics: AreaTopicShare[]
  more_factor: number
  less_factor: number
  boost_days: number
  /** What "more" queues as a search. */
  search: string
}

export interface MapSteerResult {
  action: string
  area_id: number | null
  topic: string | null
  boost_factor: number | null
  boost_expires_at: string | null
  seed_task_ids: number[]
  view_id: number | null
  message: string
  undo: string
}

export type SteerAction = 'more' | 'less' | 'watch'

export const AREA_STEERING_FIELDS = [
  'area_id',
  'topic',
  'dominant_share',
  'topics',
  'more_factor',
  'less_factor',
  'boost_days',
  'search',
] as const
export const MAP_STEER_FIELDS = [
  'action',
  'area_id',
  'topic',
  'boost_factor',
  'boost_expires_at',
  'seed_task_ids',
  'view_id',
  'message',
  'undo',
] as const
export type AssertAreaSteering = Expect<Equal<keyof AreaSteering, (typeof AREA_STEERING_FIELDS)[number]>>
export type AssertMapSteer = Expect<Equal<keyof MapSteerResult, (typeof MAP_STEER_FIELDS)[number]>>

export function getAreaSteering(areaId: number, init?: RequestInit): Promise<AreaSteering> {
  return getJson<AreaSteering>(`/api/explore/areas/${areaId}/steering`, init)
}

function postJson<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  return getJson<T>(path, {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify(body),
    ...init,
  })
}

export function steerArea(areaId: number, action: SteerAction, init?: RequestInit): Promise<MapSteerResult> {
  return postJson<MapSteerResult>(`/api/admin/map/areas/${areaId}/steer`, { action }, init)
}

export function suggestSearch(text: string, topic: string | null, init?: RequestInit): Promise<MapSteerResult> {
  return postJson<MapSteerResult>('/api/admin/map/suggest', { text, topic }, init)
}

/** A topic name from an area's terms: lower-case words joined by hyphens, as topics are named. */
export function topicNameFrom(terms: readonly string[]): string {
  return terms
    .slice(0, 2)
    .join(' ')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
}

// --------------------------------------------------------------------------
// How much of a field is about the topics (task P6-42).

/**
 * The share of a field's examined passages that are about at least one topic,
 * in [0, 1]; null when the build did not measure it or nothing was examined.
 * A passage never examined is neither on nor off topic, so it is left out of
 * both sides rather than counted as off.
 */
export function onTopicShare(area: Pick<Area, 'examined' | 'on_topic'>): number | null {
  const { examined, on_topic } = area
  if (examined == null || on_topic == null || examined <= 0) return null
  return Math.min(1, Math.max(0, on_topic / examined))
}

/** Fill opacity of a field's circle: faint when little of it is on a topic, full when most is. */
export const FILL_FAINT = 0.06
export const FILL_FULL = 0.42

export function shareFill(share: number | null): number {
  if (share === null) return 0.3
  return FILL_FAINT + (FILL_FULL - FILL_FAINT) * Math.sqrt(share)
}

/** Below this share a field's name is drawn muted: it reads as background, not a subject. */
export const OFF_TOPIC_BELOW = 0.1

/** "6% on a topic", or null when not measured. Under 1% but not none reads "under 1%". */
export function shareText(area: Pick<Area, 'examined' | 'on_topic'>): string | null {
  const share = onTopicShare(area)
  if (share === null) return null
  if (share > 0 && share < 0.01) return 'under 1% on a topic'
  return `${Math.round(share * 100)}% on a topic`
}

/** The topics a field holds, most first, each with its share of the examined passages. */
export function topicShares(area: Pick<Area, 'examined' | 'topic_mix'>, limit = 4): { topic: string; share: number }[] {
  const examined = area.examined ?? 0
  if (examined <= 0 || !area.topic_mix) return []
  return Object.entries(area.topic_mix)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([topic, n]) => ({ topic, share: Math.min(1, n / examined) }))
}
