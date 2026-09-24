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

  for (let step = 0; step < iterations; step++) {
    let moved = false
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i]!
        const b = placed[j]!
        let dx = b.x - a.x
        let dy = b.y - a.y
        let distance = Math.hypot(dx, dy)
        const needed = a.r + b.r + gap
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
  return { placed, scale }
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

/** "Level 2 of 3 · 7 areas in this region" and the like. */
export function levelCaption(level: AreasLevel): string {
  const noun = level.level === 1 ? 'regions' : level.level === level.levels ? 'sub-areas' : 'areas'
  const count = level.areas.length
  if (level.parent === null) {
    const build = level.build
    const inside = build ? ` · ${build.areas.toLocaleString('en')} areas in ${build.regions} regions` : ''
    return `All areas · level 1 of ${level.levels}${inside}`
  }
  return `${level.parent.name} · level ${level.level} of ${level.levels} · ${count} ${count === 1 ? noun.replace(/s$/, '') : noun}`
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
