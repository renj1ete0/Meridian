/**
 * Corpus growth on the wire (task `B-140`, ADRs 0005 and 0010). Mirrors
 * `meridian_core/schemas/growth.py`; `tests/api.test.ts` holds the field lists to it.
 */
import { request } from './api'

/** True only when A and B are the same type, invariantly. */
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false

/** Fails to compile unless its argument is exactly `true`. */
type Expect<T extends true> = T

/** The windows the page offers, as the URL spells them. */
export const RANGES = ['7d', '30d', 'all'] as const
export type GrowthRange = (typeof RANGES)[number]
export const DEFAULT_RANGE: GrowthRange = '30d'

/** Mirrors `GrowthCount`. */
export interface GrowthCount {
  total: number
  in_window: number
}
export const GROWTH_COUNT_FIELDS = ['total', 'in_window'] as const
export type AssertGrowthCount = Expect<Equal<keyof GrowthCount, (typeof GROWTH_COUNT_FIELDS)[number]>>

/** Mirrors `GrowthDay`: one calendar day in the display zone. */
export interface GrowthDay {
  day: string
  /** False: the crawl did not run that day. Drawn as a gap, never as zero. */
  crawled: boolean
  by_topic: Record<string, number>
  multi: number
  new_sites: number
}
export const GROWTH_DAY_FIELDS = ['day', 'crawled', 'by_topic', 'multi', 'new_sites'] as const
export type AssertGrowthDay = Expect<Equal<keyof GrowthDay, (typeof GROWTH_DAY_FIELDS)[number]>>

/** Mirrors `TopicGrowth`. */
export interface TopicGrowth {
  topic: string
  /** Stable colour slot; never the topic's rank. */
  series: number
  passages: GrowthCount
  daily: number[]
}
export const TOPIC_GROWTH_FIELDS = ['topic', 'series', 'passages', 'daily'] as const
export type AssertTopicGrowth = Expect<Equal<keyof TopicGrowth, (typeof TOPIC_GROWTH_FIELDS)[number]>>

/** Mirrors `MapSize`. */
export interface MapSize {
  computed_at: string
  regions: number
  areas: number
  sub_areas: number
  weak_areas: number
}
export const MAP_SIZE_FIELDS = ['computed_at', 'regions', 'areas', 'sub_areas', 'weak_areas'] as const
export type AssertMapSize = Expect<Equal<keyof MapSize, (typeof MAP_SIZE_FIELDS)[number]>>

/** Mirrors `GrowthRead`. */
export interface Growth {
  as_of: string
  zone: string
  days: number | null
  first_day: string | null
  topics: string[]
  all_topics: TopicGrowth[]
  passages: GrowthCount
  sources: GrowthCount
  sites: GrowthCount
  concepts: GrowthCount
  links: GrowthCount
  daily: GrowthDay[]
  map_now: MapSize | null
  map_history: MapSize[]
  map_history_from: string | null
}
export const GROWTH_FIELDS = [
  'as_of',
  'zone',
  'days',
  'first_day',
  'topics',
  'all_topics',
  'passages',
  'sources',
  'sites',
  'concepts',
  'links',
  'daily',
  'map_now',
  'map_history',
  'map_history_from',
] as const
export type AssertGrowth = Expect<Equal<keyof Growth, (typeof GROWTH_FIELDS)[number]>>

/** Fetch one window, narrowed to `topics` when any are given. */
export function fetchGrowth(range: GrowthRange, topics: readonly string[], init?: RequestInit): Promise<Growth> {
  const params = new URLSearchParams({ range })
  for (const topic of topics) params.append('topic', topic)
  return request<Growth>(`/api/explore/growth?${params}`, init)
}

/** The page's state as the URL holds it, so a filtered view can be linked (ADR 0010). */
export function readGrowthQuery(search: string): { range: GrowthRange; topics: string[] } {
  const params = new URLSearchParams(search)
  const range = params.get('range')
  return {
    range: (RANGES as readonly string[]).includes(range ?? '') ? (range as GrowthRange) : DEFAULT_RANGE,
    topics: params.getAll('topic').filter(Boolean),
  }
}

export function growthQuery(range: GrowthRange, topics: readonly string[]): string {
  const params = new URLSearchParams()
  if (range !== DEFAULT_RANGE) params.set('range', range)
  for (const topic of topics) params.append('topic', topic)
  const text = params.toString()
  return text ? `?${text}` : ''
}

/** The colour of a topic: its series slot, or the neutral "other" past the eighth (§2 series). */
export function seriesColour(series: number): string {
  return series >= 0 && series < 8 ? `var(--series-${series + 1})` : 'var(--text-faint)'
}
