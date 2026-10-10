/**
 * Typed client over `/api/explore/*` and `/api/admin/*` (task P2-13, spec §12.5, §12.6).
 *
 * Requests are relative (`/api/...`), with no base URL. Each interface is tied to a
 * `*_FIELDS` list by `Expect<Equal<...>>`, and each list to its pydantic DTO by
 * `tests/api.test.ts`. Calendar dates stay strings.
 * See docs/features/web-app.md#the-api-client.
 */

import type { SourceTier } from '../ui/Tier'

// --------------------------------------------------------------------------
// Compile-time plumbing for link 1 of the chain above
// --------------------------------------------------------------------------

/** True only when A and B are the same type, invariantly. */
type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false

/** Fails to compile unless its argument is exactly `true`. */
type Expect<T extends true> = T

// --------------------------------------------------------------------------
// The shapes, mirroring meridian_core.schemas
// --------------------------------------------------------------------------

/** `RETENTION_TIER` in `models/source.py`. */
export type RetentionTier = 'primary' | 'background' | 'junk'

/** `OCR_TIER` in `models/source.py`. */
export type OcrTier = 'none' | 'cheap' | 'quality'

/** `DOC_KIND` in `models/source.py` (`B-59`): what a document is. */
export const DOC_KINDS = ['paper', 'report', 'news', 'legal', 'profile', 'listing', 'other'] as const
export type DocKind = (typeof DOC_KINDS)[number]

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
  /** Which topics the source belongs to (`P2-14`). Null means nothing examined it. */
  topic_labels: string[] | null
  /**
   * Which topics this passage itself is about (`P2-24`), best first. Null means
   * no pass has examined the passage; `[]` means one has and found none. A
   * topic filter matches on either list, so this is how a hit from a document
   * labelled with something else shows why it is in a filtered set.
   */
  passage_topics: string[] | null
  /**
   * Which places the source is about (`P2-23`): ISO 3166-1 alpha-2 for a
   * country, UN/LOCODE without its space for a city. Null means never examined.
   */
  places?: string[] | null

  /**
   * What `page_or_offset` counts (`P2-18`): a page for paginated documents, else a
   * character offset. `null` means the media type was never recorded: unknown, not a
   * default.
   */
  page_unit: PageUnit | null
  media_type: string | null

  /** The novelty gate's verdict, so a surface can say why something is absent. */
  duplicate_of: number | null

  score: number
  lexical_rank: number | null
  vector_rank: number | null
  /**
   * How old the document is, and what that did to its score (`P2-20`), published so a
   * demotion can be audited. `age_days` is null for an undated document, and `decay` is
   * then 1.
   */
  age_days: number | null
  decay: number
  score_before_decay: number
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
  'topic_labels',
  'passage_topics',
  'places',
  'page_unit',
  'media_type',
  'duplicate_of',
  'score',
  'lexical_rank',
  'vector_rank',
  'age_days',
  'decay',
  'score_before_decay',
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
  /** Every source row, junk and copies included. */
  sources: number
  /** Documents a reader can find: not junk, not a copy (`B-156`). What the landing shows. */
  kept_sources: number
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
  /** Every configured topic, most-attended first (`P6-24`). */
  topics: string[]
  /** Sources nothing has examined for topics — a topic filter excludes them. */
  sources_without_topics: number
  /** The comparison set's places, for a place filter to offer (`P2-23`). */
  places?: Place[]
  /** Sources nothing has examined for places — a place filter excludes them. */
  sources_without_places?: number
}

/** Mirrors `PlaceRead`: a stored place code and the name to show for it. */
export interface Place {
  code: string
  name: string
}

export const PLACE_FIELDS = ['code', 'name'] as const

export const CORPUS_STATS_FIELDS = [
  'as_of',
  'sources',
  'kept_sources',
  'chunks',
  'embedded_chunks',
  'duplicate_chunks',
  'searchable_chunks',
  'entities',
  'edges',
  'contested_edges',
  'new_sources',
  'new_chunks',
  'topics',
  'sources_without_topics',
  'places',
  'sources_without_places',
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
  /** When a re-crawl retired this chunk (`P1-32`). Null is the live set. */
  superseded_at: string | null
  /** Which embedding view the vector was computed from (`B-49`); null before views. */
  embedding_view: number | null
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
  'superseded_at',
  'embedding_view',
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
  /**
   * Which topics this source's content is about, best first (`P2-14`, `P2-21`).
   * Null means nothing has examined the content; `[]` means it is about none.
   */
  topic_labels: string[] | null
  /** Which queue topics caused the fetch (`P2-21`) — why it was crawled, not what it says. */
  crawled_for: string[] | null
  /** The earlier source this one copies (`B-44`); hidden from search and the map. */
  duplicate_of: number | null
  duplicate_reason: string | null
  /**
   * What kind of document this is (`B-59`). Null means nothing has classified
   * it yet; a `listing` is followed for its links and holds no passages.
   */
  doc_kind: DocKind | null
  /** When the content labeller last examined it, and under which basis (`P2-21`). */
  topics_examined_at: string | null
  topic_basis: string | null
  /** Each topic's similarity to the content: what the labels were decided from. */
  topic_scores: Record<string, number> | null
  /** Set when the labels were read from a sample of a long document (`B-89`): that sample's best score. */
  topic_sample_best: number | null
  /** Which places the content is about, most-evidenced first (`P2-23`). Null: never examined. */
  places: string[] | null
  /** When the place pass last examined it, and under which basis (`P2-23`). */
  places_examined_at: string | null
  place_basis: string | null
  /** What the places were decided from, and which signals decided each. */
  place_evidence: Record<string, unknown> | null
  /**
   * What screening concluded about this page (`P4-14`). Shown to the operator
   * on purpose: a passage from something quarantined should be visible here,
   * or a false positive never gets noticed. The MCP surface returns none.
   */
  trust_state: TrustState
  /** When the acronym harvest last read this document (`P5-02`). Null is the queue. */
  acronyms_harvested_at: string | null
  extra: Record<string, unknown> | null
  created_at: string
}

/** Mirrors `SourcePageRead`: the row, and what it implies for the source page (`B-156`). */
export type SourceWithPage = Source & {
  /** What its passages' `page_or_offset` counts: a citable page, or bookkeeping. Null when unknown. */
  page_unit: PageUnit | null
}

/** `SourcePageRead`'s own fields, beyond `SourceRead`'s. */
export const SOURCE_PAGE_FIELDS = ['page_unit'] as const

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
  'topic_labels',
  'crawled_for',
  'duplicate_of',
  'duplicate_reason',
  'doc_kind',
  'topics_examined_at',
  'topic_basis',
  'topic_scores',
  'topic_sample_best',
  'places',
  'places_examined_at',
  'place_basis',
  'place_evidence',
  'trust_state',
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

export const SOURCE_CHUNKS_FIELDS = ['source_id', 'chunks', 'limit', 'offset', 'has_more'] as const

// Link 1: each interface must have exactly the keys its runtime list names.
// Exported so `noUnusedLocals` does not delete the enforcement.
export type AssertSearchHit = Expect<Equal<keyof SearchHit, (typeof SEARCH_HIT_FIELDS)[number]>>
export type AssertSearchResponse = Expect<Equal<keyof SearchResponse, (typeof SEARCH_RESPONSE_FIELDS)[number]>>
export type AssertCorpusStats = Expect<Equal<keyof CorpusStats, (typeof CORPUS_STATS_FIELDS)[number]>>
export type AssertChunk = Expect<Equal<keyof Chunk, (typeof CHUNK_FIELDS)[number]>>
export type AssertSource = Expect<Equal<keyof Source, (typeof SOURCE_FIELDS)[number]>>
export type AssertSourceChunks = Expect<Equal<keyof SourceChunks, (typeof SOURCE_CHUNKS_FIELDS)[number]>>

// --------------------------------------------------------------------------
// Errors
// --------------------------------------------------------------------------

/**
 * A request the API refused, carrying a sentence a reader can act on.
 *
 * FastAPI's `detail` is a string or an array of per-field objects; both are normalised
 * here, once, so no call site renders `[object Object]`.
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

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
  /** Keep sources carrying any of these topics (`P2-14`). */
  topic?: readonly string[]
  /**
   * `all` keeps only passages whose source and own labels together carry
   * every `topic` — where topics meet (`B-72`). Omitted means the server's
   * default, `any`.
   */
  topic_match?: TopicMatch
  /** Keep sources about any of these places — stored codes (`P2-23`). */
  place?: readonly string[]
  published_after?: string
  published_before?: string
  include_duplicates?: boolean
  include_junk?: boolean
  candidates?: number
}

/**
 * Build the query string.
 *
 * List filters repeat their key (`source_tier=a&source_tier=b`), as FastAPI's
 * `list[...] | None` expects; empty arrays are omitted, meaning "no filter".
 * See docs/features/web-app.md#query-strings.
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
    // Only sent when it narrows: `any` is the server's default, and a URL that
    // always carries it would read as a choice the reader made.
    ['topic_match', params.topic_match === 'all' ? 'all' : undefined],
  ]
  for (const [key, value] of scalars) {
    if (value !== undefined) query.set(key, String(value))
  }

  for (const tier of params.source_tier ?? []) query.append('source_tier', tier)
  for (const language of params.language ?? []) query.append('language', language)
  for (const topic of params.topic ?? []) query.append('topic', topic)
  for (const place of params.place ?? []) query.append('place', place)

  return query.toString()
}

export function searchCorpus(params: SearchParams, init?: RequestInit): Promise<SearchResponse> {
  return request<SearchResponse>(`/api/explore/search?${searchQuery(params)}`, init)
}

export function corpusStats(params: { since?: string | null } = {}, init?: RequestInit): Promise<CorpusStats> {
  // Omitted entirely when absent, rather than sent empty: the API distinguishes
  // "nobody asked" from "nothing arrived", and `?since=` would collapse them.
  const suffix = params.since ? `?since=${encodeURIComponent(params.since)}` : ''
  return request<CorpusStats>(`/api/explore/stats${suffix}`, init)
}

/** Mirrors `DisplaySettingsRead` (`B-145`, ADR 0009). */
export interface DisplaySettings {
  /** An IANA zone name; every time a reader sees is shown in it. */
  display_timezone: string
  /** `GMT+8` and the like, at the moment of the request. */
  label: string
}

export const DISPLAY_SETTINGS_FIELDS = ['display_timezone', 'label'] as const

export type AssertDisplaySettings = Expect<Equal<keyof DisplaySettings, (typeof DISPLAY_SETTINGS_FIELDS)[number]>>

export function displaySettings(init?: RequestInit): Promise<DisplaySettings> {
  return request<DisplaySettings>('/api/explore/settings', init)
}

/** Change the display zone (Admin). Refused unless it is an IANA name the server knows. */
export function setDisplayTimezone(zone: string, init?: RequestInit): Promise<DisplaySettings> {
  return request<DisplaySettings>('/api/admin/settings/display-timezone', {
    ...init,
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    body: JSON.stringify({ display_timezone: zone }),
  })
}

/** Mirrors `MapPointRead` (`P6-26`, `P6-29`). */
export interface MapPoint {
  chunk_id: number
  source_id: number
  /** All three in [-1, 1]: the widest point on each axis sits at the edge. */
  x: number
  y: number
  /** The third principal component (`P6-29`); the flat view ignores it. */
  z: number
  /** The primary topic — `topics[0]` — or null. What the point is coloured by. */
  topic: string | null
  /**
   * Every topic the source's content is about, primary first (`P2-21`). Null
   * when nothing has examined it; `[]` when it was examined and is about none.
   */
  topics: string[] | null
  title: string | null
  url: string
  snippet: string
}

export const MAP_POINT_FIELDS = [
  'chunk_id',
  'source_id',
  'x',
  'y',
  'z',
  'topic',
  'topics',
  'title',
  'url',
  'snippet',
] as const

/** Mirrors `CorpusMapRead` (`P6-26`, `P6-29`). */
export interface CorpusMap {
  as_of: string
  points: MapPoint[]
  /** How many passages matched before sampling. Above `points.length` means a sample. */
  eligible: number
  /** Share of variance each axis carries, in axis order — how much of the space the picture shows. */
  explained_variance: [number, number, number]
}

export const CORPUS_MAP_FIELDS = ['as_of', 'points', 'eligible', 'explained_variance'] as const

export type AssertMapPoint = Expect<Equal<keyof MapPoint, (typeof MAP_POINT_FIELDS)[number]>>
export type AssertCorpusMap = Expect<Equal<keyof CorpusMap, (typeof CORPUS_MAP_FIELDS)[number]>>

export function getCorpusMap(
  params: { topic?: string[]; sample?: number } = {},
  init?: RequestInit,
): Promise<CorpusMap> {
  const query = new URLSearchParams()
  if (params.sample !== undefined) query.set('sample', String(params.sample))
  for (const topic of params.topic ?? []) query.append('topic', topic)
  const suffix = query.size ? `?${query}` : ''
  return request<CorpusMap>(`/api/explore/map${suffix}`, init)
}

/** How several `topic` filters combine on `/api/explore/search` (`B-72`). */
export type TopicMatch = 'any' | 'all'

/** Mirrors `TopicOverlapRead` (`B-72`): sources carrying exactly this set of topics. */
export interface TopicOverlap {
  /** Sorted and de-duplicated. */
  topics: string[]
  sources: number
}

export const TOPIC_OVERLAP_FIELDS = ['topics', 'sources'] as const

/**
 * Mirrors `TopicOverlapsRead` (`B-72`). Exact sets, so they add up: sources
 * carrying *at least* a selection are the sum over every set containing it.
 */
export interface TopicOverlaps {
  overlaps: TopicOverlap[]
  labelled_sources: number
}

export const TOPIC_OVERLAPS_FIELDS = ['overlaps', 'labelled_sources'] as const

export type AssertTopicOverlap = Expect<Equal<keyof TopicOverlap, (typeof TOPIC_OVERLAP_FIELDS)[number]>>
export type AssertTopicOverlaps = Expect<Equal<keyof TopicOverlaps, (typeof TOPIC_OVERLAPS_FIELDS)[number]>>

export function getTopicOverlaps(init?: RequestInit): Promise<TopicOverlaps> {
  return request<TopicOverlaps>('/api/explore/topic-overlaps', init)
}

export function getSource(sourceId: number, init?: RequestInit): Promise<SourceWithPage> {
  return request<SourceWithPage>(`/api/explore/sources/${sourceId}`, init)
}

export function getSourceChunks(
  sourceId: number,
  params: { limit?: number; offset?: number; around?: number } = {},
  init?: RequestInit,
): Promise<SourceChunks> {
  const query = new URLSearchParams()
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  if (params.offset !== undefined) query.set('offset', String(params.offset))
  // A passage to open on (`B-178`): the server starts the window a few passages before it.
  if (params.around !== undefined) query.set('around', String(params.around))
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
   * Deep link into the stored raw file, with `#page=N` when the page is known. `null`
   * when this deployment does not serve raw files, so no caption carries a dead link.
   */
  raw_url: string | null
  /**
   * The caption or alt text a reader is shown (`B-156`): null when both are only the image's
   * file name. `caption` and `alt_text` stay as extracted.
   */
  reader_caption: string | null
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
  'reader_caption',
] as const

/** Mirrors `SourceFiguresRead`. */
export interface SourceFigures {
  source_id: number
  figures: FigureRef[]
  raw_available: boolean
  /** Logos, icons and controls left out of `figures` (`B-156`), counted so their absence is said. */
  furniture_hidden: number
}

export const SOURCE_FIGURES_FIELDS = ['source_id', 'figures', 'raw_available', 'furniture_hidden'] as const

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
// `/api/admin/*` — the control surface (task P6-13). The prefix is the role boundary;
// a 503 means no caller identity is configured, not an outage.
// See docs/features/web-app.md#explore-and-admin-prefixes.
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

export const GAZETTEER_ROW_FIELDS = ['term', 'will_load', 'withheld_reason', 'collides_with'] as const

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

export function editGazetteerTerm(termId: number, edit: GazetteerTermEdit, init?: RequestInit): Promise<GazetteerRow> {
  return request<GazetteerRow>(`/api/admin/gazetteer/${termId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(edit),
    ...init,
  })
}

export type AssertGazetteerTerm = Expect<Equal<keyof GazetteerTerm, (typeof GAZETTEER_TERM_FIELDS)[number]>>
export type AssertGazetteerRow = Expect<Equal<keyof GazetteerRow, (typeof GAZETTEER_ROW_FIELDS)[number]>>
export type AssertGazetteerQueue = Expect<Equal<keyof GazetteerQueue, (typeof GAZETTEER_QUEUE_FIELDS)[number]>>

// --------------------------------------------------------------------------
// Steering (task P6-12, spec §10)
// --------------------------------------------------------------------------

/** `TOPIC_STATUS` in `models/config.py`. */
export type TopicStatus = 'active' | 'maintenance' | 'paused' | 'archived'

/** Mirrors `TopicConfigRead`. */
export interface TopicConfig {
  topic: string
  weight: number
  floor: number
  ceiling: number
  boost_factor: number | null
  boost_expires_at: string | null
  pinned: boolean
  status: TopicStatus
  /** What the topic is about (`P2-21`) — most of what content labelling compares a page to. */
  description: string | null
}

export const TOPIC_CONFIG_FIELDS = [
  'topic',
  'weight',
  'floor',
  'ceiling',
  'boost_factor',
  'boost_expires_at',
  'pinned',
  'status',
  'description',
] as const

/** Mirrors `TopicRowRead`. */
export interface TopicRow {
  topic: TopicConfig
  effective_weight: number
  share: number
  boost_active: boolean
}

export const TOPIC_ROW_FIELDS = ['topic', 'effective_weight', 'share', 'boost_active'] as const

/** Mirrors `TopicsRead`. */
export interface Topics {
  rows: TopicRow[]
  sums_to: number
}

export const TOPICS_FIELDS = ['rows', 'sums_to'] as const

/** Mirrors `SteeringLogRead`. */
export interface SteeringEntry {
  log_id: number
  changed_at: string
  actor: string
  topic: string | null
  field: string | null
  old_value: string | null
  new_value: string | null
  reason: string | null
}

export const STEERING_ENTRY_FIELDS = [
  'log_id',
  'changed_at',
  'actor',
  'topic',
  'field',
  'old_value',
  'new_value',
  'reason',
] as const

/** Mirrors `SteeringLogPage`. */
export interface SteeringLog {
  entries: SteeringEntry[]
  limit: number
  has_more: boolean
}

export const STEERING_LOG_FIELDS = ['entries', 'limit', 'has_more'] as const

/** Mirrors `TopicEdit`. Omitted keys are left alone. */
export interface TopicEdit {
  weight?: number
  floor?: number
  ceiling?: number
  pinned?: boolean
  status?: TopicStatus
  boost_factor?: number | null
  boost_expires_at?: string | null
  /** Re-labels every source's topics when it changes (`P2-21`). Empty clears it. */
  description?: string | null
  reason?: string
}

export function getTopics(init?: RequestInit): Promise<Topics> {
  return request<Topics>('/api/admin/topics', init)
}

export function editTopic(topic: string, edit: TopicEdit, init?: RequestInit): Promise<Topics> {
  return request<Topics>(`/api/admin/topics/${encodeURIComponent(topic)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(edit),
    ...init,
  })
}

export function addTopic(body: TopicAddBody, init?: RequestInit): Promise<Topics> {
  return request<Topics>('/api/admin/topics', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    ...init,
  })
}

export function getSteeringLog(
  params: { topic?: string; limit?: number } = {},
  init?: RequestInit,
): Promise<SteeringLog> {
  const query = new URLSearchParams()
  if (params.topic) query.set('topic', params.topic)
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  const suffix = query.toString()
  return request<SteeringLog>(`/api/admin/steering-log${suffix ? `?${suffix}` : ''}`, init)
}

export type AssertTopicConfig = Expect<Equal<keyof TopicConfig, (typeof TOPIC_CONFIG_FIELDS)[number]>>
export type AssertTopicRow = Expect<Equal<keyof TopicRow, (typeof TOPIC_ROW_FIELDS)[number]>>
export type AssertTopics = Expect<Equal<keyof Topics, (typeof TOPICS_FIELDS)[number]>>
export type AssertSteeringEntry = Expect<Equal<keyof SteeringEntry, (typeof STEERING_ENTRY_FIELDS)[number]>>
export type AssertSteeringLog = Expect<Equal<keyof SteeringLog, (typeof STEERING_LOG_FIELDS)[number]>>

// --------------------------------------------------------------------------
// Fetch policy (task P6-22, spec §6.4)
// --------------------------------------------------------------------------

/** `TRUST_STATE` in `models/mixins.py` (`P4-14`). */
export type TrustState = 'unscreened' | 'cleared' | 'quarantined' | 'rejected'

/** `SEED_SOURCE` in `models/queue.py`. */
export type SeedSource = 'frontier' | 'sitemap' | 'search' | 'citation' | 'doi' | 'model' | 'user' | 'diversity'

/** `DOMAIN_STATUS` in `models/config.py`. */
export type DomainStatus = 'active' | 'blocked' | 'paused'

/** Mirrors `FetchPolicyRead`. */
export interface FetchPolicy {
  domain: string
  settings: Record<string, unknown> | null
  status: DomainStatus
  note: string | null
  consecutive_failures: number
  render_js_escalations: number
  render_js_learned_at: string | null
  seed_allowed: boolean | null
  first_seen_via: SeedSource | null
  novel_fetches: number
  trust_state: TrustState
  clean_fetches: number
  trust_decided_at: string | null
  trust_decided_by: string | null
  trust_reason: string | null
  updated_at: string | null
  updated_by: string | null
}

export const FETCH_POLICY_FIELDS = [
  'domain',
  'settings',
  'status',
  'note',
  'consecutive_failures',
  'render_js_escalations',
  'render_js_learned_at',
  'seed_allowed',
  'first_seen_via',
  'novel_fetches',
  'trust_state',
  'clean_fetches',
  'trust_decided_at',
  'trust_decided_by',
  'trust_reason',
  'updated_at',
  'updated_by',
] as const

/** Mirrors `FetchPolicyRowRead`. */
export interface FetchPolicyRow {
  policy: FetchPolicy
  resolved: Record<string, unknown>
  overridden: string[]
}

export const FETCH_POLICY_ROW_FIELDS = ['policy', 'resolved', 'overridden'] as const

/** Mirrors `FetchPolicyPage`. */
export interface FetchPolicyPage {
  rows: FetchPolicyRow[]
  limit: number
  offset: number
  has_more: boolean
  active: number
  paused: number
  blocked: number
}

export const FETCH_POLICY_PAGE_FIELDS = ['rows', 'limit', 'offset', 'has_more', 'active', 'paused', 'blocked'] as const

export function getFetchPolicy(
  params: { status?: DomainStatus; q?: string; limit?: number; offset?: number } = {},
  init?: RequestInit,
): Promise<FetchPolicyPage> {
  const query = new URLSearchParams()
  if (params.status) query.set('status', params.status)
  if (params.q) query.set('q', params.q)
  if (params.limit !== undefined) query.set('limit', String(params.limit))
  // P6-28: Admin pages through thousands of domains.
  if (params.offset) query.set('offset', String(params.offset))
  const suffix = query.toString()
  return request<FetchPolicyPage>(`/api/admin/fetch-policy${suffix ? `?${suffix}` : ''}`, init)
}

export function editFetchPolicy(
  domain: string,
  edit: { settings?: Record<string, unknown>; status?: DomainStatus; note?: string; confirm?: boolean },
  init?: RequestInit,
): Promise<FetchPolicyRow> {
  return request<FetchPolicyRow>(`/api/admin/fetch-policy/${encodeURIComponent(domain)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(edit),
    ...init,
  })
}

export function actOnFetchPolicy(
  domain: string,
  action: 'unblock' | 'forget-render',
  init?: RequestInit,
): Promise<FetchPolicyRow> {
  return request<FetchPolicyRow>(`/api/admin/fetch-policy/${encodeURIComponent(domain)}/${action}`, {
    method: 'POST',
    ...init,
  })
}

export type AssertFetchPolicy = Expect<Equal<keyof FetchPolicy, (typeof FETCH_POLICY_FIELDS)[number]>>
export type AssertFetchPolicyRow = Expect<Equal<keyof FetchPolicyRow, (typeof FETCH_POLICY_ROW_FIELDS)[number]>>
export type AssertFetchPolicyPage = Expect<Equal<keyof FetchPolicyPage, (typeof FETCH_POLICY_PAGE_FIELDS)[number]>>

// --------------------------------------------------------------------------
// Annotations (task P6-05, spec §12.5)
// --------------------------------------------------------------------------
//
// Reads on `/api/explore`, writes on `/api/admin`. No `produced_by` is ever sent: the
// server assigns authorship. See docs/features/web-app.md#explore-and-admin-prefixes.

/** Mirrors `AnnotationTarget` — a node a note is about, named rather than numbered. */
export interface AnnotationTarget {
  entity_id: number
  canonical_name: string
  node_type: string
}

export const ANNOTATION_TARGET_FIELDS = ['entity_id', 'canonical_name', 'node_type'] as const

/** Mirrors `AnnotationRead`. */
export interface Annotation {
  entity_id: number
  title: string
  body: string | null
  about: AnnotationTarget[]
  supporting_chunk_ids: number[]
  topic_labels: string[] | null
  /** Always the reserved human author, carried so the layer renders as distinct. */
  produced_by: string
  /** When the text was last the author's — moved by a rewrite, unlike `created_at`. */
  produced_at: string | null
  created_at: string
}

export const ANNOTATION_FIELDS = [
  'entity_id',
  'title',
  'body',
  'about',
  'supporting_chunk_ids',
  'topic_labels',
  'produced_by',
  'produced_at',
  'created_at',
] as const

/** Mirrors `AnnotationsRead`. */
export interface Annotations {
  annotations: Annotation[]
  /** How many match the filter, not how many came back. */
  total: number
}

export const ANNOTATIONS_FIELDS = ['annotations', 'total'] as const

export interface AnnotationParams {
  /** Narrow to the notes attached to one node. */
  about?: number
  limit?: number
  offset?: number
}

export function getAnnotations(params: AnnotationParams = {}, init?: RequestInit): Promise<Annotations> {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const suffix = search.toString()
  return request<Annotations>(`/api/explore/annotations${suffix ? `?${suffix}` : ''}`, init)
}

/** What a reader writes. The absent `produced_by` is the point — see above. */
export interface NoteDraft {
  title: string
  body?: string | null
  about?: readonly number[]
  supporting_chunk_ids?: readonly number[]
  topic_labels?: readonly string[] | null
}

export function writeAnnotation(draft: NoteDraft, init?: RequestInit): Promise<Annotation> {
  return request<Annotation>('/api/admin/annotations', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(draft),
    ...init,
  })
}

/**
 * Rewrite a note. Fields left out are left alone; `about`, when given, replaces the
 * note's nodes rather than adding to them.
 */
export function rewriteAnnotation(
  entityId: number,
  change: Partial<NoteDraft>,
  init?: RequestInit,
): Promise<Annotation> {
  return request<Annotation>(`/api/admin/annotations/${entityId}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(change),
    ...init,
  })
}

export type AssertAnnotationTarget = Expect<Equal<keyof AnnotationTarget, (typeof ANNOTATION_TARGET_FIELDS)[number]>>
export type AssertAnnotation = Expect<Equal<keyof Annotation, (typeof ANNOTATION_FIELDS)[number]>>
export type AssertAnnotations = Expect<Equal<keyof Annotations, (typeof ANNOTATIONS_FIELDS)[number]>>

// --------------------------------------------------------------------------
// The node detail panel (task P6-04, spec §12.5)
// --------------------------------------------------------------------------

/** Mirrors `NodeAttributeRead`. */
export interface NodeAttribute {
  value_id: number
  name: string
  scope: string
  topic: string | null
  value: string | null
  value_numeric: number | null
  confidence: number | null
  quality_tier: number | null
  supporting_chunk_ids: number[]
}

export const NODE_ATTRIBUTE_FIELDS = [
  'value_id',
  'name',
  'scope',
  'topic',
  'value',
  'value_numeric',
  'confidence',
  'quality_tier',
  'supporting_chunk_ids',
] as const

/** Mirrors `EntityRead`. */
export interface Entity {
  entity_id: number
  canonical_name: string
  node_type: string
  jurisdiction: string | null
  aliases: string[] | null
  topic_labels: string[] | null
  description: string | null
  confidence: string | null
  merged_from: number[] | null
  redirects_to: number | null
  is_annotation: boolean
  /** What a hand-written node was drawn from; empty for everything derived (`P6-05`). */
  supporting_chunk_ids: number[]
  produced_by: string | null
  model: string | null
  quality_tier: number | null
  produced_at: string | null
  schema_version: number
  created_at: string
}

export const ENTITY_FIELDS = [
  'entity_id',
  'canonical_name',
  'node_type',
  'jurisdiction',
  'aliases',
  'topic_labels',
  'description',
  'confidence',
  'merged_from',
  'redirects_to',
  'is_annotation',
  'supporting_chunk_ids',
  'produced_by',
  'model',
  'quality_tier',
  'produced_at',
  'schema_version',
  'created_at',
] as const

/** Mirrors `NodeDetailRead`. */
export interface NodeDetail {
  entity: Entity
  attributes: NodeAttribute[]
  supporting: SearchHit[]
  contested_edges: number
  /** §12.5 ends the panel with "own annotations". The recent few (`P6-05`). */
  annotations: Annotation[]
}

export const NODE_DETAIL_FIELDS = ['entity', 'attributes', 'supporting', 'contested_edges', 'annotations'] as const

export function getNode(entityId: number, init?: RequestInit): Promise<NodeDetail> {
  return request<NodeDetail>(`/api/explore/nodes/${entityId}`, init)
}

export type AssertNodeAttribute = Expect<Equal<keyof NodeAttribute, (typeof NODE_ATTRIBUTE_FIELDS)[number]>>
export type AssertEntity = Expect<Equal<keyof Entity, (typeof ENTITY_FIELDS)[number]>>
export type AssertNodeDetail = Expect<Equal<keyof NodeDetail, (typeof NODE_DETAIL_FIELDS)[number]>>

// --------------------------------------------------------------------------
// Saved views (task P6-09, spec §12.5)
// --------------------------------------------------------------------------
//
// Reads on `/api/explore`, writes on `/api/admin`: shared state a guest can open but not
// add to. See docs/features/web-app.md#explore-and-admin-prefixes.

/** Mirrors `SavedViewRead`. */
export interface SavedViewRecord {
  view_id: number
  name: string
  query: string | null
  filters: Record<string, unknown>
  focus_entity_id: number | null
  note: string | null
  last_opened_at: string | null
  created_at: string
  /** Sources new since last opened that match it (`P6-43`), capped; null when not counted. */
  new_since: number | null
}

export const SAVED_VIEW_FIELDS = [
  'view_id',
  'name',
  'query',
  'filters',
  'focus_entity_id',
  'note',
  'last_opened_at',
  'created_at',
  'new_since',
] as const

/** Mirrors `SavedViewsRead`. */
export interface SavedViews {
  views: SavedViewRecord[]
}

export const SAVED_VIEWS_FIELDS = ['views'] as const

export function getSavedViews(init?: RequestInit): Promise<SavedViews> {
  return request<SavedViews>('/api/explore/views', init)
}

export function saveView(
  body: { name: string; query?: string | null; filters?: Record<string, unknown>; note?: string },
  init?: RequestInit,
): Promise<SavedViewRecord> {
  return request<SavedViewRecord>('/api/admin/views', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    ...init,
  })
}

export function markViewOpened(viewId: number, init?: RequestInit): Promise<SavedViewRecord> {
  return request<SavedViewRecord>(`/api/admin/views/${viewId}/opened`, {
    method: 'POST',
    ...init,
  })
}

export type AssertSavedView = Expect<Equal<keyof SavedViewRecord, (typeof SAVED_VIEW_FIELDS)[number]>>
export type AssertSavedViews = Expect<Equal<keyof SavedViews, (typeof SAVED_VIEWS_FIELDS)[number]>>

// --------------------------------------------------------------------------
// The first run (task B-07)

export const QUEUE_TASK_FIELDS = [
  'task_id',
  'url_or_query',
  'task_type',
  'status',
  'priority',
  'topic',
  'seed_source',
  'attempts',
  'next_attempt_at',
  'claimed_at',
  'claimed_by',
  'fetched_at',
  'error',
  'created_at',
] as const

export interface QueueTask {
  task_id: number
  url_or_query: string
  task_type: 'url' | 'query'
  status: string
  priority: number
  topic: string | null
  seed_source: string
  attempts: number
  claimed_by: string | null
  created_at: string
}

export interface FirstRun {
  is_first_run: boolean
  sources: number
  pending_seeds: readonly QueueTask[]
  seeds_in_flight: number
}

export interface SeedCreate {
  url_or_query: string
  task_type?: 'url' | 'query'
  topic?: string | null
  priority?: number
  /** Why, for the steering audit (`B-55`). */
  reason?: string | null
}

export function getFirstRun(init?: RequestInit): Promise<FirstRun> {
  return request<FirstRun>('/api/admin/first-run', init)
}

export function addSeed(seed: SeedCreate, init?: RequestInit): Promise<QueueTask> {
  return request<QueueTask>('/api/admin/seeds', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(seed),
    ...init,
  })
}

export async function removeSeed(taskId: number, init?: RequestInit): Promise<void> {
  // 204, so there is no body to parse and `request` would fail trying. The
  // error path still has to match the rest of the client, or a refused delete
  // would surface as "unreachable".
  const response = await fetch(`/api/admin/seeds/${taskId}`, { method: 'DELETE', ...init })
  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json())?.detail
    } catch {
      detail = undefined
    }
    throw new ApiError(response.status, describeDetail(detail, response.status))
  }
}

// --------------------------------------------------------------------------
// What the crawl is doing (task B-09)

export const CRAWL_PROGRESS_FIELDS = [
  'as_of',
  'queue',
  'recent_domains',
  'attempts_last_hour',
  'successes_last_hour',
  'liveness',
] as const

/** Mirrors `CrawlProgressRead`. */
export interface CrawlProgress {
  as_of: string
  queue: Record<string, number>
  recent_domains: readonly string[]
  attempts_last_hour: number
  successes_last_hour: number
  /** Whether the crawl is still fetching (`B-156`): the pill's crawl half. Null when not computed. */
  liveness: Liveness | null
}

export function getCrawlProgress(init?: RequestInit): Promise<CrawlProgress> {
  return request<CrawlProgress>('/api/explore/progress', init)
}

// --------------------------------------------------------------------------
// Whether a long crawl is still alive (task P6-25)

/** `LivenessState` in `schemas/enums.py`. A verdict computed at read time, not
 * a column, so there is no CHECK constraint to drift against — the states are
 * the four branches of `crawlhealth.judge`. */
export type LivenessState = 'crawling' | 'stalled' | 'waiting' | 'idle'

export const HOUR_BUCKET_FIELDS = ['start', 'succeeded', 'failed'] as const

/** Mirrors `HourBucketRead`. `start` is the bucket's first instant; each is
 * exactly one hour, and they run oldest first. */
export interface HourBucket {
  start: string
  succeeded: number
  failed: number
}

export const OUTCOME_COUNT_FIELDS = ['outcome', 'count'] as const

/** Mirrors `OutcomeCountRead`. `outcome` is kept a string: the value set is
 * `FETCH_OUTCOME`, read off the model server-side, and the screen shows it as
 * written rather than keeping a second copy of the list here. */
export interface OutcomeCount {
  outcome: string
  count: number
}

export const DOMAIN_COUNT_FIELDS = ['domain', 'attempts', 'succeeded'] as const

/** Mirrors `DomainCountRead`. */
export interface DomainCount {
  domain: string
  attempts: number
  succeeded: number
}

export const LIVENESS_FIELDS = ['state', 'last_attempt_at', 'quiet_seconds', 'ready', 'pending'] as const

/** Mirrors `LivenessRead`. */
export interface Liveness {
  state: LivenessState
  last_attempt_at: string | null
  quiet_seconds: number | null
  ready: number
  pending: number
}

export const CRAWL_HEALTH_FIELDS = [
  'as_of',
  'stall_after_seconds',
  'hours',
  'outcomes',
  'queue',
  'embedding_backlog',
  'top_domains',
  'liveness',
] as const

/** Mirrors `CrawlHealthRead`. */
export interface CrawlHealth {
  as_of: string
  stall_after_seconds: number
  hours: readonly HourBucket[]
  outcomes: readonly OutcomeCount[]
  queue: Record<string, number>
  embedding_backlog: number
  top_domains: readonly DomainCount[]
  liveness: Liveness
}

export type AssertHourBucket = Expect<Equal<keyof HourBucket, (typeof HOUR_BUCKET_FIELDS)[number]>>
export type AssertOutcomeCount = Expect<Equal<keyof OutcomeCount, (typeof OUTCOME_COUNT_FIELDS)[number]>>
export type AssertDomainCount = Expect<Equal<keyof DomainCount, (typeof DOMAIN_COUNT_FIELDS)[number]>>
export type AssertLiveness = Expect<Equal<keyof Liveness, (typeof LIVENESS_FIELDS)[number]>>
export type AssertCrawlHealth = Expect<Equal<keyof CrawlHealth, (typeof CRAWL_HEALTH_FIELDS)[number]>>

/** Read-only, and under `/api/explore` for that reason — see the route. */
export function getCrawlHealth(init?: RequestInit): Promise<CrawlHealth> {
  return request<CrawlHealth>('/api/explore/crawl-health', init)
}

// ---------------------------------------------------------------------------
// The agent registry and run history (task P6-23, spec §11.3, §11.10)
// ---------------------------------------------------------------------------

/** Mirrors `AgentRowRead`. No key: §11.11 keeps them out of the database, and
 * a screen that echoed one would undo that from the other end. `key_present`
 * is the server's answer about its own environment. */
export interface AgentRow {
  agent_id: string
  provider: string
  model: string | null
  task_types: string[] | null
  quality_tier: number | null
  cost_tier: string | null
  availability: string | null
  enabled: boolean
  fallback_agent_id: string | null
  /** Explicit place in the routing order, lowest first; null routes by quality (`B-137`). */
  route_order: number | null
  endpoint: string | null
  api_key_env_var: string | null
  key_present: boolean
  blocked_by: string[]
}

export const AGENT_ROW_FIELDS = [
  'agent_id',
  'provider',
  'model',
  'task_types',
  'quality_tier',
  'cost_tier',
  'availability',
  'enabled',
  'fallback_agent_id',
  'route_order',
  'endpoint',
  'api_key_env_var',
  'key_present',
  'blocked_by',
] as const

/** Mirrors `AgentsRead`. */
export interface Agents {
  rows: AgentRow[]
  unserved_tasks: string[]
}

/** Mirrors `RunRowRead`. */
export interface RunRow {
  run_id: number
  started_at: string | null
  completed_at: string | null
  stage: string | null
  status: string
  agent_id: string | null
  tokens_used: number
  cost_usd: number | null
  edges_added: number
  tags_added: number
  seeds_emitted: number
  last_chunk_id: number | null
  heartbeat_at: string | null
  error: string | null
}

export const RUN_ROW_FIELDS = [
  'run_id',
  'started_at',
  'completed_at',
  'stage',
  'status',
  'agent_id',
  'tokens_used',
  'cost_usd',
  'edges_added',
  'tags_added',
  'seeds_emitted',
  'last_chunk_id',
  'heartbeat_at',
  'error',
] as const

/** Mirrors `RunsRead`. */
export interface Runs {
  rows: RunRow[]
  total: number
  active: RunRow | null
}

export function getAgents(init?: RequestInit): Promise<Agents> {
  return request<Agents>('/api/admin/agents', init)
}

/** What Admin may change on an agent: whether it is on, and which model it
 * asks for. Mirrors `AgentEdit`; every other column stays config. */
export interface AgentChange {
  enabled?: boolean
  model?: string
}

/** Change one agent. Returns the whole registry, because `unserved_tasks` is
 * computed across rows — disabling the only agent that declares a task type
 * changes a fact about every other row's screen. */
export function editAgent(agentId: string, change: AgentChange, init?: RequestInit): Promise<Agents> {
  return request<Agents>(`/api/admin/agents/${encodeURIComponent(agentId)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(change),
    ...init,
  })
}

export function getRuns(params: { limit?: number; offset?: number } = {}, init?: RequestInit): Promise<Runs> {
  const query = new URLSearchParams()
  if (params.limit) query.set('limit', String(params.limit))
  // Older runs, a page at a time (`B-198`).
  if (params.offset) query.set('offset', String(params.offset))
  const suffix = query.toString()
  return request<Runs>(`/api/admin/runs${suffix ? `?${suffix}` : ''}`, init)
}

// --------------------------------------------------------------------------
// Admin as designed (task P6-28): previews and bulk decisions
// --------------------------------------------------------------------------

/** Mirrors `TopicAdd`. */
export interface TopicAddBody {
  topic: string
  floor?: number
  ceiling?: number
  description?: string | null
  reason?: string
}

/**
 * What adding this topic would leave the vector as — the write, rolled back on
 * the server, so the dialog's arithmetic is the arithmetic that commits.
 * Refused with the write's own 422 sentence.
 */
export function previewAddTopic(body: TopicAddBody, init?: RequestInit): Promise<Topics> {
  return request<Topics>('/api/admin/topics/preview', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    ...init,
  })
}

/** What this steering change would leave the vector as, committed nowhere. */
export function previewEditTopic(topic: string, edit: TopicEdit, init?: RequestInit): Promise<Topics> {
  return request<Topics>(`/api/admin/topics/${encodeURIComponent(topic)}/preview`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(edit),
    ...init,
  })
}

/** `GAZETTEER_BULK_MAX` in `schemas/admin.py`: one bulk decision is at most a page. */
export const GAZETTEER_BULK_MAX = 200

/** Mirrors `GazetteerBulkRead`. */
export interface GazetteerBulk {
  rows: GazetteerRow[]
}

export const GAZETTEER_BULK_FIELDS = ['rows'] as const

export type AssertGazetteerBulk = Expect<Equal<keyof GazetteerBulk, (typeof GAZETTEER_BULK_FIELDS)[number]>>

/** One verdict for up to a page of terms, all or none (404 names unknown ids). */
export function decideGazetteerTerms(
  termIds: readonly number[],
  decision: 'approve' | 'reject' | 'restore',
  init?: RequestInit,
): Promise<GazetteerBulk> {
  return request<GazetteerBulk>('/api/admin/gazetteer/decide', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ term_ids: termIds, decision }),
    ...init,
  })
}
