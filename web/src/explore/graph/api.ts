/**
 * Typed client over `/api/explore/graph/*` (tasks P6-01, P6-02, P6-03).
 *
 * Kept beside the workspace rather than in `lib/api.ts` because nothing else
 * reads these routes, and it follows the same two-link drift chain that file
 * documents: `tsc` ties each interface to its `*_FIELDS` list through the
 * `Expect<Equal<...>>` lines below, and `tests/graph-api.test.ts` ties each
 * list to the pydantic class in `meridian_core/schemas/graphview.py`.
 */

import {
  ApiError,
  describeDetail,
  type Annotation,
  type Entity,
  type NodeAttribute,
  type SearchHit,
} from '../../lib/api'
import type { SourceTier } from '../../ui/Tier'

type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
    throw new ApiError(response.status, describeDetail(detail, response.status))
  }
  return (await response.json()) as T
}

// --------------------------------------------------------------------------
// The neighbourhood
// --------------------------------------------------------------------------

export type NodeRole = 'focus' | 'neighbour' | 'hint'
export type EdgeKind = 'focus' | 'between' | 'hint'

/** Mirrors `GraphNodeRead`. */
export interface GraphNode {
  entity_id: number
  canonical_name: string
  node_type: string
  jurisdiction: string | null
  is_annotation: boolean
  role: NodeRole
  home_topics: string[]
  topics: string[]
  contested: boolean
  cross_topic: boolean
  support: number
  degree: number
  sources: number
  newest: string | null
}

export const GRAPH_NODE_FIELDS = [
  'entity_id',
  'canonical_name',
  'node_type',
  'jurisdiction',
  'is_annotation',
  'role',
  'home_topics',
  'topics',
  'contested',
  'cross_topic',
  'support',
  'degree',
  'sources',
  'newest',
] as const

/** Mirrors `GraphEdgeRead`. */
export interface GraphEdge {
  edge_id: number
  from_node: number
  to_node: number
  relation_type: string
  kind: EdgeKind
  confidence: number | null
  stance: string | null
  certainty: string | null
  support: number
  contested: boolean
  contested_with: number[]
}

export const GRAPH_EDGE_FIELDS = [
  'edge_id',
  'from_node',
  'to_node',
  'relation_type',
  'kind',
  'confidence',
  'stance',
  'certainty',
  'support',
  'contested',
  'contested_with',
] as const

/** Mirrors `GraphFilters`. */
export interface GraphFiltersRead {
  topics: string[]
  tiers: SourceTier[]
  published_from: string | null
  published_to: string | null
  contested_only: boolean
  attribute: string | null
}

export const GRAPH_FILTERS_FIELDS = [
  'topics',
  'tiers',
  'published_from',
  'published_to',
  'contested_only',
  'attribute',
] as const

/** Mirrors `FacetCount`. */
export interface FacetCount {
  value: string
  count: number
}

export const FACET_COUNT_FIELDS = ['value', 'count'] as const

/** Mirrors `GraphFacetsRead`. */
export interface GraphFacets {
  topics: FacetCount[]
  tiers: FacetCount[]
  attributes: FacetCount[]
  contested: number
  published_min: string | null
  published_max: string | null
}

export const GRAPH_FACETS_FIELDS = [
  'topics',
  'tiers',
  'attributes',
  'contested',
  'published_min',
  'published_max',
] as const

/** Mirrors `NeighbourhoodRead`. */
export interface Neighbourhood {
  focus: GraphNode
  focus_contested: boolean
  redirects_to: number | null
  nodes: GraphNode[]
  edges: GraphEdge[]
  total: number
  shown: number
  unfiltered: number
  limit: number
  ranked_by: 'support'
  filters: GraphFiltersRead
  facets: GraphFacets
}

export const NEIGHBOURHOOD_FIELDS = [
  'focus',
  'focus_contested',
  'redirects_to',
  'nodes',
  'edges',
  'total',
  'shown',
  'unfiltered',
  'limit',
  'ranked_by',
  'filters',
  'facets',
] as const

// --------------------------------------------------------------------------
// The node panel
// --------------------------------------------------------------------------

/** Mirrors `EvidenceRead`. */
export interface Evidence {
  hit: SearchHit
  via: 'attribute' | 'edge' | 'node'
  relation_type: string | null
  other_entity_id: number | null
  other_name: string | null
  certainty: string | null
  stance: string | null
}

export const EVIDENCE_FIELDS = [
  'hit',
  'via',
  'relation_type',
  'other_entity_id',
  'other_name',
  'certainty',
  'stance',
] as const

/** Mirrors `ContestedSideRead`. */
export interface ContestedSide {
  edge_id: number
  relation_type: string
  from_entity_id: number
  from_name: string
  to_entity_id: number
  to_name: string
  certainty: string | null
  stance: string | null
  evidence: SearchHit | null
}

export const CONTESTED_SIDE_FIELDS = [
  'edge_id',
  'relation_type',
  'from_entity_id',
  'from_name',
  'to_entity_id',
  'to_name',
  'certainty',
  'stance',
  'evidence',
] as const

/** Mirrors `ContestedPairRead`. */
export interface ContestedPair {
  ours: ContestedSide
  theirs: ContestedSide
}

export const CONTESTED_PAIR_FIELDS = ['ours', 'theirs'] as const

/** Mirrors `GraphNodeDetailRead`. */
export interface GraphNodeDetail {
  entity: Entity
  home_topics: string[]
  contested: boolean
  attributes: NodeAttribute[]
  evidence: Evidence[]
  evidence_total: number
  contested_with: ContestedPair[]
  annotations: Annotation[]
}

export const GRAPH_NODE_DETAIL_FIELDS = [
  'entity',
  'home_topics',
  'contested',
  'attributes',
  'evidence',
  'evidence_total',
  'contested_with',
  'annotations',
] as const

// --------------------------------------------------------------------------
// Search and path
// --------------------------------------------------------------------------

/** Mirrors `NodeMatchRead`. */
export interface NodeMatch {
  entity_id: number
  canonical_name: string
  node_type: string
  jurisdiction: string | null
  is_annotation: boolean
  matched_alias: string | null
  degree: number
}

export const NODE_MATCH_FIELDS = [
  'entity_id',
  'canonical_name',
  'node_type',
  'jurisdiction',
  'is_annotation',
  'matched_alias',
  'degree',
] as const

/** Mirrors `NodeSearchRead`. */
export interface NodeSearch {
  query: string
  matches: NodeMatch[]
}

export const NODE_SEARCH_FIELDS = ['query', 'matches'] as const

/** Mirrors `PathRead`. */
export interface GraphPath {
  source: number
  target: number
  max_depth: number
  found: boolean
  hops: number | null
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export const GRAPH_PATH_FIELDS = [
  'source',
  'target',
  'max_depth',
  'found',
  'hops',
  'nodes',
  'edges',
] as const

export type AssertGraphNode = Expect<Equal<keyof GraphNode, (typeof GRAPH_NODE_FIELDS)[number]>>
export type AssertGraphEdge = Expect<Equal<keyof GraphEdge, (typeof GRAPH_EDGE_FIELDS)[number]>>
export type AssertGraphFilters = Expect<
  Equal<keyof GraphFiltersRead, (typeof GRAPH_FILTERS_FIELDS)[number]>
>
export type AssertFacetCount = Expect<Equal<keyof FacetCount, (typeof FACET_COUNT_FIELDS)[number]>>
export type AssertGraphFacets = Expect<
  Equal<keyof GraphFacets, (typeof GRAPH_FACETS_FIELDS)[number]>
>
export type AssertNeighbourhood = Expect<
  Equal<keyof Neighbourhood, (typeof NEIGHBOURHOOD_FIELDS)[number]>
>
export type AssertEvidence = Expect<Equal<keyof Evidence, (typeof EVIDENCE_FIELDS)[number]>>
export type AssertContestedSide = Expect<
  Equal<keyof ContestedSide, (typeof CONTESTED_SIDE_FIELDS)[number]>
>
export type AssertContestedPair = Expect<
  Equal<keyof ContestedPair, (typeof CONTESTED_PAIR_FIELDS)[number]>
>
export type AssertGraphNodeDetail = Expect<
  Equal<keyof GraphNodeDetail, (typeof GRAPH_NODE_DETAIL_FIELDS)[number]>
>
export type AssertNodeMatch = Expect<Equal<keyof NodeMatch, (typeof NODE_MATCH_FIELDS)[number]>>
export type AssertNodeSearch = Expect<Equal<keyof NodeSearch, (typeof NODE_SEARCH_FIELDS)[number]>>
export type AssertGraphPath = Expect<Equal<keyof GraphPath, (typeof GRAPH_PATH_FIELDS)[number]>>

// --------------------------------------------------------------------------
// Requests
// --------------------------------------------------------------------------

/** What the rail edits. Dates are ISO `YYYY-MM-DD` strings, never `Date`s. */
export interface GraphFilterState {
  topics: string[]
  tiers: SourceTier[]
  publishedFrom: string | null
  publishedTo: string | null
  contestedOnly: boolean
  attribute: string | null
}

export const NO_FILTERS: GraphFilterState = {
  topics: [],
  tiers: [],
  publishedFrom: null,
  publishedTo: null,
  contestedOnly: false,
  attribute: null,
}

/**
 * The neighbourhood's query string. Repeated keys for lists, as FastAPI's
 * `list[...]` expects; empty and false values omitted, because "no tier
 * filter" and "a filter matching no tiers" are different requests.
 */
export function neighbourhoodQuery(filters: GraphFilterState, limit?: number): string {
  const query = new URLSearchParams()
  for (const topic of filters.topics) query.append('topic', topic)
  for (const tier of filters.tiers) query.append('tier', tier)
  if (filters.publishedFrom) query.set('published_from', filters.publishedFrom)
  if (filters.publishedTo) query.set('published_to', filters.publishedTo)
  if (filters.contestedOnly) query.set('contested_only', 'true')
  if (filters.attribute) query.set('attribute', filters.attribute)
  if (limit !== undefined) query.set('limit', String(limit))
  return query.toString()
}

export function getNeighbourhood(
  entityId: number,
  filters: GraphFilterState,
  limit?: number,
  init?: RequestInit,
): Promise<Neighbourhood> {
  const query = neighbourhoodQuery(filters, limit)
  return request<Neighbourhood>(
    `/api/explore/graph/nodes/${entityId}/neighbourhood${query ? `?${query}` : ''}`,
    init,
  )
}

export function getGraphNode(entityId: number, init?: RequestInit): Promise<GraphNodeDetail> {
  return request<GraphNodeDetail>(`/api/explore/graph/nodes/${entityId}`, init)
}

export function searchNodes(q: string, limit = 8, init?: RequestInit): Promise<NodeSearch> {
  const query = new URLSearchParams({ q, limit: String(limit) })
  return request<NodeSearch>(`/api/explore/graph/search?${query}`, init)
}

export function getPath(
  source: number,
  target: number,
  maxDepth?: number,
  init?: RequestInit,
): Promise<GraphPath> {
  const query = new URLSearchParams({ source: String(source), target: String(target) })
  if (maxDepth !== undefined) query.set('max_depth', String(maxDepth))
  return request<GraphPath>(`/api/explore/graph/path?${query}`, init)
}

/**
 * Save the workspace as a view: its focus node and its filters (§12.5, `P6-09`).
 *
 * The filter keys are the neighbourhood route's own parameter names, so a view
 * saved here reopens by passing them straight back — and `topic` is the key the
 * search landing already reads, so a view saved here filters there too.
 */
export function saveGraphView(
  name: string,
  focus: number,
  filters: GraphFilterState,
  init?: RequestInit,
): Promise<{ view_id: number }> {
  return request<{ view_id: number }>('/api/admin/views', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      name,
      focus_entity_id: focus,
      filters: filtersToRecord(filters),
    }),
    ...init,
  })
}

/** The filters as a saved view stores them. Empty values are left out. */
export function filtersToRecord(filters: GraphFilterState): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  if (filters.topics.length) out.topic = filters.topics
  if (filters.tiers.length) out.tier = filters.tiers
  if (filters.publishedFrom) out.published_from = filters.publishedFrom
  if (filters.publishedTo) out.published_to = filters.publishedTo
  if (filters.contestedOnly) out.contested_only = true
  if (filters.attribute) out.attribute = filters.attribute
  return out
}
