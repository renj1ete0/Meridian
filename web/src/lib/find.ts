/**
 * What Find narrows a search by, and how that travels in the URL (`B-173`).
 *
 * One object for every filter, so the landing, the results rail, a saved view and a
 * shared link all carry the same thing. See docs/features/web-app.md#explore-and-find.
 */
import { SOURCE_TIERS, type SourceTier } from '../ui/Tier'
import type { ResultMode } from './answer'
import type { SearchParams, TopicMatch } from './api'

export interface FindFilters {
  topics: readonly string[]
  /** How several topics combine; meaningless with fewer than two. */
  match: TopicMatch
  /** Place codes, as `sources.places` stores them. */
  places: readonly string[]
  tiers: readonly SourceTier[]
  /** First and last publication year, inclusive. Null is open-ended. */
  from: number | null
  to: number | null
}

export const NO_FILTERS: FindFilters = { topics: [], match: 'any', places: [], tiers: [], from: null, to: null }

/** Years a reader may type. Outside this a typo, not a filter. */
export const EARLIEST_YEAR = 1800
export const LATEST_YEAR = 2100

/** A typed year, or null when it is empty or not a plausible year. */
export function parseYear(text: string | null | undefined): number | null {
  if (!text) return null
  const trimmed = text.trim()
  if (!/^\d{4}$/.test(trimmed)) return null
  const year = Number(trimmed)
  return year >= EARLIEST_YEAR && year <= LATEST_YEAR ? year : null
}

/** How many filters narrow the search; the topic match is a modifier, not a filter. */
export function activeCount(filters: FindFilters): number {
  return (
    filters.topics.length +
    filters.places.length +
    filters.tiers.length +
    (filters.from !== null ? 1 : 0) +
    (filters.to !== null ? 1 : 0)
  )
}

/** Whether any filter could leave out a document with no date: only the years can. */
export function dropsUndated(filters: FindFilters): boolean {
  return filters.from !== null || filters.to !== null
}

/** The filters as the search and answer routes name them. */
export function searchFilters(filters: FindFilters): Omit<SearchParams, 'q'> {
  // A reversed range is read as the reader meant it rather than refused: it is the
  // same span typed right to left.
  const [from, to] =
    filters.from !== null && filters.to !== null && filters.from > filters.to
      ? [filters.to, filters.from]
      : [filters.from, filters.to]
  return {
    topic: filters.topics,
    topic_match: filters.topics.length > 0 ? filters.match : undefined,
    place: filters.places,
    source_tier: filters.tiers,
    published_after: from !== null ? `${from}-01-01` : undefined,
    published_before: to !== null ? `${to}-12-31` : undefined,
  }
}

/**
 * `/` with the search and its filters, the inverse of {@link readFind}. `view` is set only
 * when the reader chose answer or passages, so a link opens as the sender saw it.
 */
export function findLink(query: string, filters: FindFilters = NO_FILTERS, view?: ResultMode): string {
  const params = new URLSearchParams()
  const q = query.trim()
  if (q) params.set('q', q)
  for (const topic of filters.topics) params.append('topic', topic)
  if (filters.topics.length > 0 && filters.match === 'all') params.set('topic_match', 'all')
  for (const place of filters.places) params.append('place', place)
  for (const tier of filters.tiers) params.append('tier', tier)
  if (filters.from !== null) params.set('from', String(filters.from))
  if (filters.to !== null) params.set('to', String(filters.to))
  if (view) params.set('view', view)
  const suffix = params.toString()
  return suffix ? `/?${suffix}` : '/'
}

function distinct(values: readonly string[]): string[] {
  return [...new Set(values.map((v) => v.trim()).filter(Boolean))]
}

/** What Find reads back out of its URL. Unknown tiers and implausible years are dropped. */
export function readFind(search: string): { q: string; filters: FindFilters; view: ResultMode | null } {
  const params = new URLSearchParams(search)
  const known = new Set<string>(SOURCE_TIERS)
  const view = params.get('view')
  return {
    q: params.get('q')?.trim() ?? '',
    filters: {
      topics: distinct(params.getAll('topic')),
      match: params.get('topic_match') === 'all' ? 'all' : 'any',
      places: distinct(params.getAll('place')),
      tiers: distinct(params.getAll('tier')).filter((t): t is SourceTier => known.has(t)),
      from: parseYear(params.get('from')),
      to: parseYear(params.get('to')),
    },
    view: view === 'answer' || view === 'passages' ? view : null,
  }
}

/** Whether two filter sets narrow the same way; order of chosen values does not matter. */
export function sameFilters(a: FindFilters, b: FindFilters): boolean {
  const set = (xs: readonly string[]) => [...xs].sort().join('\u0000')
  return (
    set(a.topics) === set(b.topics) &&
    (a.topics.length < 2 || a.match === b.match) &&
    set(a.places) === set(b.places) &&
    set(a.tiers) === set(b.tiers) &&
    a.from === b.from &&
    a.to === b.to
  )
}

/**
 * Filters as a saved view stores them: named as the server's `SearchFilters` names them,
 * since it validates against that model (`B-73`). Empty filters are left out.
 */
export function viewFiltersOf(filters: FindFilters): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const wire = searchFilters(filters)
  if (filters.topics.length > 0) out.topics = [...filters.topics]
  if (filters.topics.length > 1 && filters.match === 'all') out.topics_all = true
  if (filters.places.length > 0) out.places = [...filters.places]
  if (filters.tiers.length > 0) out.source_tiers = [...filters.tiers]
  if (wire.published_after) out.published_after = wire.published_after
  if (wire.published_before) out.published_before = wire.published_before
  return out
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

function yearOf(value: unknown): number | null {
  return typeof value === 'string' ? parseYear(value.slice(0, 4)) : null
}

/** A saved view's filters read back; `topic` is the spelling from before `B-73`. */
export function filtersOfView(stored: Record<string, unknown>): FindFilters {
  const known = new Set<string>(SOURCE_TIERS)
  const topics = strings(stored.topics ?? stored.topic)
  return {
    topics,
    match: stored.topics_all === true ? 'all' : 'any',
    places: strings(stored.places),
    tiers: strings(stored.source_tiers).filter((t): t is SourceTier => known.has(t)),
    from: yearOf(stored.published_after),
    to: yearOf(stored.published_before),
  }
}
