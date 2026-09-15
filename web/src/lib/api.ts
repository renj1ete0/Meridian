/**
 * Typed client over `/api/explore/*` (task P2-13, spec §12.5, §12.6).
 *
 * ## Relative URLs, and no base URL anywhere
 *
 * There is deliberately no `API_BASE` constant in this file, and nothing reads
 * an environment variable. Every request is `/api/...`, which the Vite proxy
 * forwards in development and which `cloudflared` fronts in production. A base
 * URL would be a value that has to be correct per environment, and the way that
 * fails is a build shipped pointing at someone's laptop.
 *
 * ## Keeping these types honest
 *
 * TypeScript types vanish at runtime, so nothing can compare them to the
 * pydantic DTOs directly. The chain here has two links, and both are enforced:
 *
 * 1. `tsc` ties each `interface` to its `*_FIELDS` runtime list, through the
 *    `Expect<Equal<...>>` assertions below. Add a field to the type and not the
 *    list and the build fails.
 * 2. `tests/api.test.ts` ties each `*_FIELDS` list to the pydantic model it
 *    mirrors, parsed out of `meridian_core/schemas/`. Add a field in Python and
 *    not here and the test fails.
 *
 * So the type follows the DTO transitively, and neither link can be removed
 * without something going red. This is the same device as the `SOURCE_TIER`
 * drift test in `tests/ui.test.tsx`, which exists because a tier added to
 * Postgres and not to the UI renders as a raw enum value and nobody notices.
 *
 * ## Dates stay strings
 *
 * `publication_date` arrives as `"2025-12-03"` and is kept as written. Calling
 * `new Date("2025-12-03")` parses it as UTC midnight, which in any negative
 * offset renders as the 2nd — a citation silently dated to the wrong day, on
 * some readers' machines only. A date this system publishes is a fact from a
 * document, not a moment in time, and formatting it is the display layer's job.
 */

import type { SourceTier } from '../ui/Tier'

// --------------------------------------------------------------------------
// Compile-time plumbing for link 1 of the chain above
// --------------------------------------------------------------------------

/** True only when A and B are the same type, invariantly. */
type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false

/** Fails to compile unless its argument is exactly `true`. */
type Expect<T extends true> = T

// --------------------------------------------------------------------------
// The shapes, mirroring meridian_core.schemas
// --------------------------------------------------------------------------

/** `RETENTION_TIER` in `models/source.py`. */
export type RetentionTier = 'primary' | 'background' | 'junk'

/** `OCR_TIER` in `models/source.py`. */
export type OcrTier = 'none' | 'cheap' | 'quality'

/** Which retrieval arms ran. Lexical-only is a legitimate, reported mode. */
export type PageUnit = 'page' | 'offset'

export type SearchArm = 'lexical' | 'vector'

/** Mirrors `SearchHitRead`. */
export interface SearchHit {
  chunk_id: number
  source_id: number
  text: string
  page_or_offset: number | null
  chunk_index: number

  url: string
  title: string | null
  source_tier: SourceTier
  /** `YYYY-MM-DD`, or null. Not a `Date` — see the module docstring. */
  publication_date: string | null
  language: string | null

  /**
   * What `page_or_offset` counts (`P2-18`). §5.3 makes it a page for paginated
   * documents and a character offset otherwise; before this the hit carried
   * nothing that said which, so a citation could only be labelled
   * "page/offset". `null` means the source's media type was never recorded —
   * genuinely unknown, rather than a default that mislabels one kind or the
   * other.
   */
  page_unit: PageUnit | null
  media_type: string | null

  /** The novelty gate's verdict, so a surface can say why something is absent. */
  duplicate_of: number | null

  score: number
  lexical_rank: number | null
  vector_rank: number | null
}

export const SEARCH_HIT_FIELDS = [
  'chunk_id',
  'source_id',
  'text',
  'page_or_offset',
  'chunk_index',
  'url',
  'title',
  'source_tier',
  'publication_date',
  'language',
  'page_unit',
  'media_type',
  'duplicate_of',
  'score',
  'lexical_rank',
  'vector_rank',
] as const

/** Mirrors `SearchResponse`. */
export interface SearchResponse {
  hits: SearchHit[]
  arms: SearchArm[]
  degraded: boolean
  degraded_reason: string | null
  limit: number
  offset: number
  has_more: boolean
  candidate_pool: number
  lexical_candidates: number
  vector_candidates: number
}

export const SEARCH_RESPONSE_FIELDS = [
  'hits',
  'arms',
  'degraded',
  'degraded_reason',
  'limit',
  'offset',
  'has_more',
  'candidate_pool',
  'lexical_candidates',
  'vector_candidates',
] as const

/** Mirrors `CorpusStatsRead`. */
export interface CorpusStats {
  /** When the counts were taken (`P2-18`) — a client cannot otherwise tell a
   *  cached figure from a fresh one. ISO 8601. */
  as_of: string
  sources: number
  chunks: number
  embedded_chunks: number
  duplicate_chunks: number
  /** Deliberately distinct from `chunks`: collected-and-filtered is not uncollected. */
  searchable_chunks: number
  entities: number
  edges: number
  contested_edges: number
  /**
   * What arrived since the caller's `since` (`P6-11`). `null` means they did
   * not ask; `0` means nothing arrived, and a landing page must not show the
   * first as the second.
   */
  new_sources: number | null
  new_chunks: number | null
}

export const CORPUS_STATS_FIELDS = [
  'as_of',
  'sources',
  'chunks',
  'embedded_chunks',
  'duplicate_chunks',
  'searchable_chunks',
  'entities',
  'edges',
  'contested_edges',
  'new_sources',
  'new_chunks',
] as const

/** Mirrors `ChunkRead`. No `embedding` — a 1024-float vector is not a payload. */
export interface Chunk {
  chunk_id: number
  source_id: number
  text: string
  page_or_offset: number | null
  chunk_index: number
  novelty_checked_at: string | null
  nearest_similarity: number | null
  duplicate_of: number | null
  created_at: string
}

export const CHUNK_FIELDS = [
  'chunk_id',
  'source_id',
  'text',
  'page_or_offset',
  'chunk_index',
  'novelty_checked_at',
  'nearest_similarity',
  'duplicate_of',
  'created_at',
] as const

/** Mirrors `SourceRead`. */
export interface Source {
  source_id: number
  url: string
  archive_url: string | null
  title: string | null
  author: string | null
  publisher: string | null
  publication_date: string | null
  doi: string | null
  accessed_at: string | null
  checksum: string | null
  etag: string | null
  last_modified: string | null
  source_tier: SourceTier
  retention_tier: RetentionTier
  raw_file_path: string | null
  /** Which raw store the file went into (`P1-45`). Provenance, not a lookup. */
  raw_root: string | null
  language: string | null
  /** Which tool read the text (`P1-44`). Null on rows predating the column. */
  extractor: string | null
  text_available: boolean
  ocr_applied: boolean
  ocr_tier: OcrTier
  ocr_confidence: number | null
  /** When the acronym harvest last read this document (`P5-02`). Null is the queue. */
  acronyms_harvested_at: string | null
  extra: Record<string, unknown> | null
  created_at: string
}

export const SOURCE_FIELDS = [
  'source_id',
  'url',
  'archive_url',
  'title',
  'author',
  'publisher',
  'publication_date',
  'doi',
  'accessed_at',
  'checksum',
  'etag',
  'last_modified',
  'source_tier',
  'retention_tier',
  'raw_file_path',
  'raw_root',
  'language',
  'extractor',
  'text_available',
  'ocr_applied',
  'ocr_tier',
  'ocr_confidence',
  'acronyms_harvested_at',
  'extra',
  'created_at',
] as const

/** Mirrors `SourceChunksRead`. */
export interface SourceChunks {
  source_id: number
  chunks: Chunk[]
  limit: number
  offset: number
  has_more: boolean
}

export const SOURCE_CHUNKS_FIELDS = [
  'source_id',
  'chunks',
  'limit',
  'offset',
  'has_more',
] as const

// Link 1: each interface must have exactly the keys its runtime list names.
// Exported so `noUnusedLocals` does not delete the enforcement.
export type AssertSearchHit = Expect<Equal<keyof SearchHit, (typeof SEARCH_HIT_FIELDS)[number]>>
export type AssertSearchResponse = Expect<
  Equal<keyof SearchResponse, (typeof SEARCH_RESPONSE_FIELDS)[number]>
>
export type AssertCorpusStats = Expect<
  Equal<keyof CorpusStats, (typeof CORPUS_STATS_FIELDS)[number]>
>
export type AssertChunk = Expect<Equal<keyof Chunk, (typeof CHUNK_FIELDS)[number]>>
export type AssertSource = Expect<Equal<keyof Source, (typeof SOURCE_FIELDS)[number]>>
export type AssertSourceChunks = Expect<
  Equal<keyof SourceChunks, (typeof SOURCE_CHUNKS_FIELDS)[number]>
>

// --------------------------------------------------------------------------
// Errors
// --------------------------------------------------------------------------

/**
 * A request the API refused, carrying a sentence a reader can act on.
 *
 * FastAPI's `detail` is two different shapes: a string for a raised
 * `HTTPException`, and an array of per-field objects for a validation failure.
 * Rendering the second directly gives `[object Object]`, which §4 would call an
 * error that names neither its cause nor its remedy — so both are normalised
 * here, once, rather than at each call site.
 */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface ValidationIssue {
  loc?: unknown[]
  msg?: string
}

export function describeDetail(detail: unknown, status: number): string {
  if (typeof detail === 'string' && detail.length > 0) return detail

  if (Array.isArray(detail)) {
    const issues = (detail as ValidationIssue[])
      .map((issue) => {
        const field = Array.isArray(issue.loc) ? issue.loc.filter((p) => p !== 'query').join('.') : ''
        return field ? `${field}: ${issue.msg ?? 'rejected'}` : (issue.msg ?? 'rejected')
      })
      .filter(Boolean)
    if (issues.length > 0) return issues.join('; ')
  }

  return `The API returned ${status}.`
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { headers: { accept: 'application/json' }, ...init })
  } catch (cause) {
    // A network failure is not an HTTP status. Naming it separately matters:
    // "the API is unreachable" and "the API refused this" want different
    // responses from the reader, and a single "request failed" hides which.
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

// --------------------------------------------------------------------------
// Routes
// --------------------------------------------------------------------------

export interface SearchParams {
  q: string
  limit?: number
  offset?: number
  source_tier?: readonly SourceTier[]
  language?: readonly string[]
  published_after?: string
  published_before?: string
  include_duplicates?: boolean
  include_junk?: boolean
  candidates?: number
}

/**
 * Build the query string.
 *
 * Repeated keys for list filters, which is what FastAPI's `list[...] | None`
 * expects — `source_tier=government&source_tier=press`, not a comma-joined
 * value, which arrives as one tier named "government,press" and is rejected as
 * an unknown literal.
 *
 * Empty arrays are omitted rather than sent, because "no tier filter" and "a
 * filter matching no tiers" are different requests and only the first is what a
 * cleared filter control means.
 */
export function searchQuery(params: SearchParams): string {
  const query = new URLSearchParams()
  query.set('q', params.q)

  const scalars: Array<[string, string | number | boolean | undefined]> = [
    ['limit', params.limit],
    ['offset', params.offset],
    ['published_after', params.published_after],
    ['published_before', params.published_before],
    ['include_duplicates', params.include_duplicates],
    ['include_junk', params.include_junk],
    ['candidates', params.candidates],
  ]
  for (const [key, value] of scalars) {
    if (value !== undefined) query.set(key, String(value))
  }

  for (const tier of params.source_tier ?? []) query.append('source_tier', tier)
  for (const language of params.language ?? []) query.append('language', language)

  return query.toString()
}

export function searchCorpus(params: SearchParams, init?: RequestInit): Promise<SearchResponse> {
  return request<SearchResponse>(`/api/explore/search?${searchQuery(params)}`, init)
}

export function corpusStats(
  params: { since?: string | null } = {},
  init?: RequestInit,
): Promise<CorpusStats> {
  // Omitted entirely when absent, rather than sent empty: the API distinguishes
  // "nobody asked" from "nothing arrived", and `?since=` would collapse them.
  const suffix = params.since ? `?since=${encodeURIComponent(params.since)}` : ''
  return request<CorpusStats>(`/api/explore/stats${suffix}`, init)
}

export function getSource(sourceId: number, init?: RequestInit): Promise<Source> {
  return request<Source>(`/api/explore/sources/${sourceId}`, init)
}

export function getSourceChunks(
  sourceId: number,
  params: { limit?: number; offset?: number } = {},
  init?: RequestInit,
): Promise<SourceChunks> {
  const query = new URLSearchParams()
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  const suffix = query.toString() ? `?${query}` : ''
  return request<SourceChunks>(`/api/explore/sources/${sourceId}/chunks${suffix}`, init)
}

export function getChunk(chunkId: number, init?: RequestInit): Promise<Chunk> {
  return request<Chunk>(`/api/explore/chunks/${chunkId}`, init)
}


/** Mirrors `FigureRefRead`. */
export interface FigureRef {
  figure_id: number
  source_id: number
  caption: string | null
  alt_text: string | null
  image_url: string | null
  page: number | null
  source_title: string | null
  source_url: string | null
  /**
   * Deep link into the stored raw file, with `#page=N` when the page is known.
   * `null` when this deployment does not serve raw files — a caption with a
   * dead link is worse than a caption alone, because the reader spends a click
   * finding out.
   */
  raw_url: string | null
}

export const FIGURE_REF_FIELDS = [
  'figure_id',
  'source_id',
  'caption',
  'alt_text',
  'image_url',
  'page',
  'source_title',
  'source_url',
  'raw_url',
] as const

/** Mirrors `SourceFiguresRead`. */
export interface SourceFigures {
  source_id: number
  figures: FigureRef[]
  raw_available: boolean
}

export const SOURCE_FIGURES_FIELDS = ['source_id', 'figures', 'raw_available'] as const

export async function getSourceFigures(id: number, init?: RequestInit): Promise<SourceFigures> {
  return request<SourceFigures>(`/api/explore/sources/${id}/figures`, init)
}


/** Mirrors `NotificationRead`. */
export interface Notification {
  notification_id: number
  notification_type: string
  title: string
  body: string | null
  payload: Record<string, unknown> | null
  surface: string | null
  read_at: string | null
  created_at: string
}

export const NOTIFICATION_FIELDS = [
  'notification_id',
  'notification_type',
  'title',
  'body',
  'payload',
  'surface',
  'read_at',
  'created_at',
] as const

/** Mirrors `NotificationsRead`. */
export interface Notifications {
  notifications: Notification[]
  counts_by_type: Record<string, number>
  unread: number
}

export const NOTIFICATIONS_FIELDS = ['notifications', 'counts_by_type', 'unread'] as const

export async function getNotifications(
  params: { notification_type?: string[]; limit?: number } = {},
  init?: RequestInit,
): Promise<Notifications> {
  const query = new URLSearchParams()
  for (const type of params.notification_type ?? []) query.append('notification_type', type)
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  const suffix = query.toString()
  return request<Notifications>(`/api/explore/notifications${suffix ? `?${suffix}` : ''}`, init)
}

// --------------------------------------------------------------------------
// `/api/admin/*` — the control surface (task P6-13)
//
// Separate from the explore client above only by prefix, because that prefix is
// the role boundary (§12.6): everything below writes through `meridian_rw`, and
// the API refuses the lot unless callers are identified. A 503 here is not an
// outage — it is an instance that has not been told who is allowed to change
// things, and the message says so.
// --------------------------------------------------------------------------

/** `GAZETTEER_ENTITY_TYPE` in `models/gazetteer.py`. */
export type GazetteerEntityType = 'agency' | 'scheme' | 'infrastructure' | 'metric' | 'concept'

/** `GAZETTEER_SOURCE` in `models/gazetteer.py`. */
export type GazetteerSource = 'manual' | 'auto_acronym' | 'model_proposed'

/** Mirrors `GazetteerTermRead`. */
export interface GazetteerTerm {
  term_id: number
  canonical: string
  aliases: string[] | null
  entity_type: GazetteerEntityType
  jurisdiction: string | null
  ambiguous: boolean
  topic_labels: string[] | null
  source: GazetteerSource
  approved: boolean
  occurrence_count: number
  rejected_at: string | null
  created_at: string
}

export const GAZETTEER_TERM_FIELDS = [
  'term_id',
  'canonical',
  'aliases',
  'entity_type',
  'jurisdiction',
  'ambiguous',
  'topic_labels',
  'source',
  'approved',
  'occurrence_count',
  'rejected_at',
  'created_at',
] as const

/** Mirrors `GazetteerRowRead`. */
export interface GazetteerRow {
  term: GazetteerTerm
  will_load: boolean
  withheld_reason: string | null
  collides_with: number[]
}

export const GAZETTEER_ROW_FIELDS = [
  'term',
  'will_load',
  'withheld_reason',
  'collides_with',
] as const

/** Mirrors `GazetteerQueueRead`. */
export interface GazetteerQueue {
  rows: GazetteerRow[]
  limit: number
  offset: number
  has_more: boolean
  pending: number
  approved: number
  rejected: number
}

export const GAZETTEER_QUEUE_FIELDS = [
  'rows',
  'limit',
  'offset',
  'has_more',
  'pending',
  'approved',
  'rejected',
] as const

export type GazetteerState = 'pending' | 'approved' | 'rejected' | 'all'

/** Mirrors `GazetteerTermEdit`. Omitted keys are left alone; `null` clears. */
export interface GazetteerTermEdit {
  canonical?: string
  aliases?: string[] | null
  entity_type?: GazetteerEntityType
  jurisdiction?: string | null
  ambiguous?: boolean
}

export function getGazetteerQueue(
  params: { state?: GazetteerState; limit?: number; offset?: number } = {},
  init?: RequestInit,
): Promise<GazetteerQueue> {
  const query = new URLSearchParams()
  if (params.state) query.set('state', params.state)
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  const suffix = query.toString()
  return request<GazetteerQueue>(`/api/admin/gazetteer${suffix ? `?${suffix}` : ''}`, init)
}

/** `approve` | `reject` | `restore` — the three verdicts, as their own routes. */
export function decideGazetteerTerm(
  termId: number,
  decision: 'approve' | 'reject' | 'restore',
  init?: RequestInit,
): Promise<GazetteerRow> {
  return request<GazetteerRow>(`/api/admin/gazetteer/${termId}/${decision}`, {
    method: 'POST',
    ...init,
  })
}

export function editGazetteerTerm(
  termId: number,
  edit: GazetteerTermEdit,
  init?: RequestInit,
): Promise<GazetteerRow> {
  return request<GazetteerRow>(`/api/admin/gazetteer/${termId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(edit),
    ...init,
  })
}

export type AssertGazetteerTerm = Expect<
  Equal<keyof GazetteerTerm, (typeof GAZETTEER_TERM_FIELDS)[number]>
>
export type AssertGazetteerRow = Expect<
  Equal<keyof GazetteerRow, (typeof GAZETTEER_ROW_FIELDS)[number]>
>
export type AssertGazetteerQueue = Expect<
  Equal<keyof GazetteerQueue, (typeof GAZETTEER_QUEUE_FIELDS)[number]>
>
