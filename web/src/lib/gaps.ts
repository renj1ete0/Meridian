/**
 * The Gaps client (task P6-36). Mirrors `meridian_core/schemas/gaps.py`;
 * `tests/gaps.test.tsx` fails if a field is added on one side only.
 */
import { ApiError, describeDetail } from './api'

export type GapActionKind = 'seed_query' | 'boost_topic' | 'open_search'

export interface GapAction {
  kind: GapActionKind
  label: string
  topic: string | null
  query: string | null
  factor: number | null
  days: number | null
}

export interface Gap {
  id: string
  source: string
  kind: string
  subject: string
  title: string
  reason: string
  severity: number
  evidence: Record<string, string | number | null>
  actions: GapAction[]
}

export interface GapSource {
  name: string
  status: 'ok' | 'unavailable' | 'pending'
  note: string | null
  gaps: number
}

export interface Gaps {
  gaps: Gap[]
  sources: GapSource[]
  computed_at: string
}

export interface GapActionResult {
  kind: 'seed_query' | 'boost_topic'
  topic: string
  detail: string
  undo: string
  task_id: number | null
  expires_at: string | null
}

export const GAP_FIELDS = [
  'id',
  'source',
  'kind',
  'subject',
  'title',
  'reason',
  'severity',
  'evidence',
  'actions',
] as const
export const GAP_ACTION_FIELDS = ['kind', 'label', 'topic', 'query', 'factor', 'days'] as const
export const GAP_SOURCE_FIELDS = ['name', 'status', 'note', 'gaps'] as const
export const GAPS_FIELDS = ['gaps', 'sources', 'computed_at'] as const
export const GAP_RESULT_FIELDS = ['kind', 'topic', 'detail', 'undo', 'task_id', 'expires_at'] as const

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { ...init, headers: { accept: 'application/json', ...init?.headers } })
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
    throw new ApiError(response.status, describeDetail(detail, response.status))
  }
  return (await response.json()) as T
}

export function getGaps(init?: RequestInit): Promise<Gaps> {
  return call<Gaps>('/api/explore/gaps', init)
}

function post<T>(path: string, body: unknown): Promise<T> {
  return call<T>(path, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function seedFromGap(gapId: string, topic: string, query: string): Promise<GapActionResult> {
  return post('/api/admin/gaps/seed', { gap_id: gapId, topic, query })
}

export function boostFromGap(gapId: string, topic: string, factor: number, days: number): Promise<GapActionResult> {
  return post('/api/admin/gaps/boost', { gap_id: gapId, topic, factor, days })
}

/** The Find link for a gap: Explore with the search already run. */
export function findHref(query: string): string {
  return `/?q=${encodeURIComponent(query)}`
}

/** Evidence as the row prints it: known keys in a fixed order, units named. */
export function evidenceLine(evidence: Gap['evidence']): string[] {
  const pct = (v: unknown) => (typeof v === 'number' ? `${(v * 100).toFixed(v < 0.01 && v > 0 ? 1 : 0)}%` : '—')
  const out: string[] = []
  const has = (k: string) => Object.prototype.hasOwnProperty.call(evidence, k)
  if (has('place')) out.push(`place ${evidence.place}`)
  if (has('sources')) out.push(`sources ${evidence.sources}`)
  if (has('strong_sources')) out.push(`gov/peer-reviewed ${evidence.strong_sources}`)
  if (has('passages')) out.push(`passages ${evidence.passages}`)
  if (has('strong_passages')) out.push(`gov/peer-reviewed passages ${evidence.strong_passages}`)
  if (has('passage_sources') && evidence.passage_sources) out.push(`in other documents ${evidence.passage_sources}`)
  if (has('topic_sources')) out.push(`in the topic ${evidence.topic_sources}`)
  if (has('newest')) out.push(`newest ${evidence.newest ?? '—'}`)
  if (has('crawl_share')) out.push(`crawl share ${pct(evidence.crawl_share)}`)
  if (has('corpus_share')) out.push(`corpus share ${pct(evidence.corpus_share)}`)
  if (has('failed')) out.push(`failed ${evidence.failed}`)
  if (has('searches_done')) out.push(`searches ${evidence.searches_done}`)
  if (has('results')) out.push(`results ${evidence.results}`)
  if (has('results_queued')) out.push(`results kept ${evidence.results_queued}`)
  if (has('examined')) out.push(`read ${evidence.examined}`)
  if (has('on_topic')) out.push(`on topic ${evidence.on_topic}`)
  if (has('on_topic_share')) out.push(`on-topic share ${pct(evidence.on_topic_share)}`)
  if (has('task_ids') && evidence.task_ids) out.push(`tasks ${evidence.task_ids}`)
  if (has('hops')) out.push(`hops ${evidence.hops ?? 'none'}`)
  if (has('similar_hops') && evidence.similar_hops !== null) out.push(`by resemblance ${evidence.similar_hops}`)
  if (has('max_depth')) out.push(`within ${evidence.max_depth}`)
  if (has('grade')) out.push(`grade ${evidence.grade}/3 · ${evidence.basis}`)
  if (has('topics') && evidence.topics) out.push(`topics ${evidence.topics}`)
  if (has('run')) out.push(`run ${evidence.run}`)
  return out
}

/**
 * Where a field gap is drawn on the Map (`P6-42`): the level holding it, which
 * the Map opens by its parent. Null for any other gap, or evidence without ids.
 */
export function mapHrefOf(evidence: Gap['evidence']): string | null {
  const area = evidence.area_id
  if (typeof area !== 'number') return null
  const parent = evidence.parent_id
  return typeof parent === 'number' ? `/map?area=${parent}` : '/map'
}

/** Which filter a gap falls under. */
export type GapGroup = 'coverage' | 'search' | 'questions' | 'other'

export function groupOf(gap: Gap): GapGroup {
  // Place coverage is coverage by another axis (`P2-23`), and routes are
  // coverage between topics: what the corpus cannot connect (`P6-36`).
  if (
    gap.source === 'topic-coverage' ||
    gap.source === 'place-coverage' ||
    gap.source === 'areas' ||
    gap.source === 'routes'
  )
    return 'coverage'
  if (gap.source === 'search-queries' || gap.source === 'search-results') return 'search'
  if (gap.source === 'question-set') return 'questions'
  return 'other'
}
