/**
 * The Gaps client (task P6-36). Mirrors `meridian_core/schemas/gaps.py`;
 * `tests/gaps.test.tsx` fails if a field is added on one side only.
 *
 * Its own module rather than more of `api.ts`, and it uses the same error
 * shape (`ApiError`, `describeDetail`) so a refusal reads the same everywhere.
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

export function boostFromGap(
  gapId: string,
  topic: string,
  factor: number,
  days: number,
): Promise<GapActionResult> {
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
  if (has('sources')) out.push(`sources ${evidence.sources}`)
  if (has('strong_sources')) out.push(`gov/peer-reviewed ${evidence.strong_sources}`)
  if (has('passages')) out.push(`passages ${evidence.passages}`)
  if (has('passage_sources') && evidence.passage_sources)
    out.push(`in other documents ${evidence.passage_sources}`)
  if (has('newest')) out.push(`newest ${evidence.newest ?? '—'}`)
  if (has('crawl_share')) out.push(`crawl share ${pct(evidence.crawl_share)}`)
  if (has('corpus_share')) out.push(`corpus share ${pct(evidence.corpus_share)}`)
  if (has('searches_done')) out.push(`searches ${evidence.searches_done}`)
  if (has('results_queued')) out.push(`results kept ${evidence.results_queued}`)
  if (has('grade')) out.push(`grade ${evidence.grade}/3 · ${evidence.basis}`)
  if (has('topics') && evidence.topics) out.push(`topics ${evidence.topics}`)
  if (has('run')) out.push(`run ${evidence.run}`)
  return out
}

/** Which filter a gap falls under. */
export type GapGroup = 'coverage' | 'search' | 'questions' | 'other'

export function groupOf(gap: Gap): GapGroup {
  if (gap.source === 'topic-coverage' || gap.source === 'areas') return 'coverage'
  if (gap.source === 'search-yield') return 'search'
  if (gap.source === 'question-set') return 'questions'
  return 'other'
}
