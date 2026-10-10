/**
 * Possible duplicates, as Admin decides them (`B-202`). Mirrors `schemas/duplicates.py`;
 * `tests/api.test.ts` reads the Python to hold the field lists, and the `Expect<Equal<…>>`
 * lines hold the interfaces to the lists. See docs/features/knowledge-graph.md#deciding-a-possible-duplicate.
 */
import { request } from './api'

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

export interface DuplicatePassage {
  chunk_id: number
  source_id: number
  title: string | null
  text: string
}

export interface DuplicateSide {
  entity_id: number
  canonical_name: string
  node_type: string
  jurisdiction: string | null
  aliases: string[]
  description: string | null
  links: number
  passages: DuplicatePassage[]
}

export interface DuplicatePair {
  notification_id: number
  mention: string
  created: DuplicateSide
  candidate: DuplicateSide
  created_at: string
}

export interface Duplicates {
  pairs: DuplicatePair[]
  total: number
}

export interface DuplicateDecided {
  notification_id: number
  decision: 'merged' | 'kept apart' | 'reopened'
  merge_id: number | null
}

export const DUPLICATE_PASSAGE_FIELDS = ['chunk_id', 'source_id', 'title', 'text'] as const
export const DUPLICATE_SIDE_FIELDS = [
  'entity_id',
  'canonical_name',
  'node_type',
  'jurisdiction',
  'aliases',
  'description',
  'links',
  'passages',
] as const
export const DUPLICATE_PAIR_FIELDS = ['notification_id', 'mention', 'created', 'candidate', 'created_at'] as const
export const DUPLICATES_FIELDS = ['pairs', 'total'] as const
export const DUPLICATE_DECIDED_FIELDS = ['notification_id', 'decision', 'merge_id'] as const

export type AssertDuplicatePassage = Expect<Equal<keyof DuplicatePassage, (typeof DUPLICATE_PASSAGE_FIELDS)[number]>>
export type AssertDuplicateSide = Expect<Equal<keyof DuplicateSide, (typeof DUPLICATE_SIDE_FIELDS)[number]>>
export type AssertDuplicatePair = Expect<Equal<keyof DuplicatePair, (typeof DUPLICATE_PAIR_FIELDS)[number]>>
export type AssertDuplicates = Expect<Equal<keyof Duplicates, (typeof DUPLICATES_FIELDS)[number]>>
export type AssertDuplicateDecided = Expect<Equal<keyof DuplicateDecided, (typeof DUPLICATE_DECIDED_FIELDS)[number]>>

export function getDuplicates(limit = 20, init?: RequestInit): Promise<Duplicates> {
  return request<Duplicates>(`/api/admin/duplicates?limit=${limit}`, init)
}

export function decideDuplicate(notificationId: number, decision: 'merge' | 'keep'): Promise<DuplicateDecided> {
  return request<DuplicateDecided>(`/api/admin/duplicates/${notificationId}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ decision }),
  })
}

export function undoDuplicate(notificationId: number): Promise<DuplicateDecided> {
  return request<DuplicateDecided>(`/api/admin/duplicates/${notificationId}/undo`, { method: 'POST' })
}
