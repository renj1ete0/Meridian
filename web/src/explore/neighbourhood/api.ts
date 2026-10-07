/**
 * Typed client over `/api/explore/neighbourhood` (task P6-33), with the same two-link
 * drift chain as `graph/api.ts` (`tests/neighbourhood.test.tsx`). Cited and similar stay
 * separate lists of separate types.
 */

import { ApiError, describeDetail, type SearchHit } from '../../lib/api'

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

/** Mirrors `TermRead`. */
export interface Term {
  entity_id: number
  canonical_name: string
  node_type: string
}
export const TERM_FIELDS = ['entity_id', 'canonical_name', 'node_type'] as const

/** Mirrors `RelationRead`. */
export interface Relation {
  relation_type: string
  /** The anchor is the stated subject: anchor → other. */
  outgoing: boolean
}
export const RELATION_FIELDS = ['relation_type', 'outgoing'] as const

/** Mirrors `CitedTermRead`: inner ring, a passage states the link. */
export interface CitedTerm extends Term {
  support: number
  relations: Relation[]
  contested: boolean
}
export const CITED_TERM_FIELDS = [...TERM_FIELDS, 'support', 'relations', 'contested'] as const

/** Mirrors `SimilarTermRead`: outer ring, near in meaning, no stated link. */
export interface SimilarTerm extends Term {
  similarity: number
}
export const SIMILAR_TERM_FIELDS = [...TERM_FIELDS, 'similarity'] as const

/** Mirrors `SimilarPassageRead`. */
export interface SimilarPassage {
  hit: SearchHit
  similarity: number
}
export const SIMILAR_PASSAGE_FIELDS = ['hit', 'similarity'] as const

export type SimilarBasis = 'node' | 'term' | 'none'

/** Mirrors `TermNeighbourhoodRead`. */
export interface TermNeighbourhood {
  term: string
  anchor: Term | null
  candidates: Term[]
  cited: CitedTerm[]
  cited_total: number
  similar: SimilarTerm[]
  similar_total: number
  passages: SimilarPassage[]
  similar_basis: SimilarBasis
  similar_floor: number
  passage_floor: number
}
/**
 * Whether a neighbourhood holds anything a reader can follow. A question
 * rarely names a node and often has nothing near it; a panel saying so
 * beside every such search takes a quarter of the page to report an absence.
 */
export function hasNeighbourhood(data: TermNeighbourhood): boolean {
  return (
    data.anchor !== null ||
    data.candidates.length > 0 ||
    data.cited.length > 0 ||
    data.similar.length > 0 ||
    data.passages.length > 0
  )
}

export const TERM_NEIGHBOURHOOD_FIELDS = [
  'term',
  'anchor',
  'candidates',
  'cited',
  'cited_total',
  'similar',
  'similar_total',
  'passages',
  'similar_basis',
  'similar_floor',
  'passage_floor',
] as const

export type AssertTerm = Expect<Equal<keyof Term, (typeof TERM_FIELDS)[number]>>
export type AssertRelation = Expect<Equal<keyof Relation, (typeof RELATION_FIELDS)[number]>>
export type AssertCited = Expect<Equal<keyof CitedTerm, (typeof CITED_TERM_FIELDS)[number]>>
export type AssertSimilar = Expect<Equal<keyof SimilarTerm, (typeof SIMILAR_TERM_FIELDS)[number]>>
export type AssertPassage = Expect<Equal<keyof SimilarPassage, (typeof SIMILAR_PASSAGE_FIELDS)[number]>>
export type AssertNeighbourhood = Expect<Equal<keyof TermNeighbourhood, (typeof TERM_NEIGHBOURHOOD_FIELDS)[number]>>

/** The query string for a term, or for a node the reader picked. */
export function neighbourhoodQuery(target: { q?: string; entityId?: number }): string {
  const params = new URLSearchParams()
  if (target.q && target.q.trim()) params.set('q', target.q.trim())
  if (target.entityId !== undefined) params.set('entity_id', String(target.entityId))
  return params.toString()
}

export async function getTermNeighbourhood(
  target: { q?: string; entityId?: number },
  init?: RequestInit,
): Promise<TermNeighbourhood> {
  let response: Response
  try {
    response = await fetch(`/api/explore/neighbourhood?${neighbourhoodQuery(target)}`, {
      headers: { accept: 'application/json' },
      ...init,
    })
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
  return (await response.json()) as TermNeighbourhood
}
