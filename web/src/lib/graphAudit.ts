/**
 * The graph audit, as Admin grades it (`P7-10`). Mirrors `schemas/graphaudit.py`;
 * `tests/api.test.ts` reads the Python to hold the field lists, and the `Expect<Equal<…>>`
 * lines hold the interfaces to the lists. See docs/features/knowledge-graph.md#the-graph-audit.
 */
import { request } from './api'

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

export type AuditKind = 'edge' | 'merge' | 'match'
export type AuditVerdict = 'correct' | 'wrong' | 'unsure'

export interface AuditTally {
  drawn: number
  graded: number
  correct: number
  wrong: number
  unsure: number
  precision: number | null
  low: number | null
  high: number | null
}

export interface AuditProducer {
  kind: AuditKind
  produced_by: string | null
  model: string | null
  tally: AuditTally
}

export interface AuditDraw {
  draw_id: number
  drawn_at: string
  window_start: string
  window_end: string
  seed: string
  drawn_by: string
  edge_population: number
  merge_population: number
  match_population: number
  merges_reversed: number
  edges: AuditTally
  merges: AuditTally
  matches: AuditTally
  by_producer: AuditProducer[]
}

/** A passage kept with a sampled item, cut to what the page shows. */
export interface AuditPassage {
  chunk_id: number
  source_id: number
  title: string | null
  text: string
}

export interface AuditNode {
  entity_id: number
  name: string
  node_type: string | null
  jurisdiction?: string | null
}

/** What a sampled relation shows (`_edge_shown`). */
export interface EdgeShown {
  from: AuditNode
  relation: string
  to: AuditNode
  stance: string | null
  certainty: string | null
  similarity_dimension: string | null
  disanalogy: string | null
  valid_from: string | null
  valid_to: string | null
  chunk_ids: number[]
  passages: AuditPassage[]
}

export interface MergeSide extends AuditNode {
  description: string | null
  passages: AuditPassage[]
  aliases?: string[]
}

/** What a sampled merge shows (`_merge_shown`). */
export interface MergeShown {
  source: MergeSide
  target: MergeSide
  score: number | null
  signals: Record<string, number> | null
  decided_by: string
  merged_at: string
  moved_edges: number
}

/** The mention side of a match: the name as the model wrote it, and where it was read. */
export interface MatchMention {
  name: string
  node_type: string | null
  jurisdiction: string | null
  passages: AuditPassage[]
}

/** What a sampled match at resolution shows (`_match_shown`, `B-273`). */
export interface MatchShown {
  mention: MatchMention
  /** The node as the resolver saw it, before the mention joined it. */
  node: MergeSide
  score: number
  signals: Record<string, number>
  matched_at: string
  run_id: number | null
}

export interface AuditSample {
  sample_id: number
  draw_id: number
  kind: AuditKind
  item_id: number
  position: number
  shown: EdgeShown | MergeShown | MatchShown
  grade: AuditVerdict | null
  note: string | null
  graded_by: string | null
  graded_at: string | null
  /** For a match a person has since split off (`B-274`), the node it became. */
  split_entity_id: number | null
}

/** What splitting a matched mention off did (`B-274`). */
export interface MatchSplit {
  match_id: number
  entity_id: number
  split_entity_id: number
  alias_removed: boolean
  chunks_moved: number
}

export interface GraphAudit {
  draws: AuditDraw[]
  ungraded: number
}

export interface AuditDrawDetail {
  draw: AuditDraw
  samples: AuditSample[]
}

export const AUDIT_TALLY_FIELDS = ['drawn', 'graded', 'correct', 'wrong', 'unsure', 'precision', 'low', 'high'] as const
export const AUDIT_PRODUCER_FIELDS = ['kind', 'produced_by', 'model', 'tally'] as const
export const AUDIT_DRAW_FIELDS = [
  'draw_id',
  'drawn_at',
  'window_start',
  'window_end',
  'seed',
  'drawn_by',
  'edge_population',
  'merge_population',
  'match_population',
  'merges_reversed',
  'edges',
  'merges',
  'matches',
  'by_producer',
] as const
export const AUDIT_SAMPLE_FIELDS = [
  'sample_id',
  'draw_id',
  'kind',
  'item_id',
  'position',
  'shown',
  'grade',
  'note',
  'graded_by',
  'graded_at',
  'split_entity_id',
] as const
export const MATCH_SPLIT_FIELDS = ['match_id', 'entity_id', 'split_entity_id', 'alias_removed', 'chunks_moved'] as const
export const GRAPH_AUDIT_FIELDS = ['draws', 'ungraded'] as const
export const AUDIT_DRAW_DETAIL_FIELDS = ['draw', 'samples'] as const

/** The keys each `shown` carries, held to the Python that writes them by `tests/api.test.ts`. */
export const EDGE_SHOWN_FIELDS = [
  'from',
  'relation',
  'to',
  'stance',
  'certainty',
  'similarity_dimension',
  'disanalogy',
  'valid_from',
  'valid_to',
  'chunk_ids',
  'passages',
] as const
export const MERGE_SHOWN_FIELDS = [
  'source',
  'target',
  'score',
  'signals',
  'decided_by',
  'merged_at',
  'moved_edges',
] as const

export const MATCH_SHOWN_FIELDS = ['mention', 'node', 'score', 'signals', 'matched_at', 'run_id'] as const

export type AssertEdgeShown = Expect<Equal<keyof EdgeShown, (typeof EDGE_SHOWN_FIELDS)[number]>>
export type AssertMergeShown = Expect<Equal<keyof MergeShown, (typeof MERGE_SHOWN_FIELDS)[number]>>
export type AssertMatchShown = Expect<Equal<keyof MatchShown, (typeof MATCH_SHOWN_FIELDS)[number]>>
export type AssertAuditTally = Expect<Equal<keyof AuditTally, (typeof AUDIT_TALLY_FIELDS)[number]>>
export type AssertAuditProducer = Expect<Equal<keyof AuditProducer, (typeof AUDIT_PRODUCER_FIELDS)[number]>>
export type AssertAuditDraw = Expect<Equal<keyof AuditDraw, (typeof AUDIT_DRAW_FIELDS)[number]>>
export type AssertAuditSample = Expect<Equal<keyof AuditSample, (typeof AUDIT_SAMPLE_FIELDS)[number]>>
export type AssertMatchSplit = Expect<Equal<keyof MatchSplit, (typeof MATCH_SPLIT_FIELDS)[number]>>
export type AssertGraphAudit = Expect<Equal<keyof GraphAudit, (typeof GRAPH_AUDIT_FIELDS)[number]>>
export type AssertAuditDrawDetail = Expect<Equal<keyof AuditDrawDetail, (typeof AUDIT_DRAW_DETAIL_FIELDS)[number]>>

/** Whether a sample's `shown` is a relation's, by its kind. */
export function isEdgeShown(sample: AuditSample): sample is AuditSample & { shown: EdgeShown } {
  return sample.kind === 'edge'
}

/** Whether a sample's `shown` is a match at resolution's, by its kind. */
export function isMatchShown(sample: AuditSample): sample is AuditSample & { shown: MatchShown } {
  return sample.kind === 'match'
}

/** What a kind is called on the page. */
export const KIND_LABEL: Record<AuditKind, string> = { edge: 'relation', merge: 'merge', match: 'match' }

export function getGraphAudit(init?: RequestInit): Promise<GraphAudit> {
  return request<GraphAudit>('/api/admin/graph-audit', init)
}

export function getAuditDraw(drawId: number, init?: RequestInit): Promise<AuditDrawDetail> {
  return request<AuditDrawDetail>(`/api/admin/graph-audit/${drawId}`, init)
}

/** Split a matched mention back off into a node of its own; needs `duplicates.decide`. */
export function splitMatch(matchId: number): Promise<MatchSplit> {
  return request<MatchSplit>(`/api/admin/graph-audit/matches/${matchId}/split`, { method: 'POST' })
}

export function gradeAuditSample(sampleId: number, grade: AuditVerdict, note?: string): Promise<AuditSample> {
  return request<AuditSample>(`/api/admin/graph-audit/samples/${sampleId}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(note ? { grade, note } : { grade }),
  })
}
