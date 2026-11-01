/**
 * The answer page's wire types and the small decisions around it.
 *
 * Mirrors `meridian_core/schemas/answer.py`; `tests/api.test.ts` reads the
 * Python to hold the field lists together, and the `Expect<Equal<…>>` lines
 * below hold the interfaces to the lists.
 */
import type { SourceTier } from '../ui/Tier'
import { addSeed, request, searchQuery, type DocKind, type QueueTask, type TopicMatch } from './api'

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

/** Mirrors `AnswerItemRead`. */
export interface AnswerItem {
  source_id: number
  chunk_id: number
  title: string | null
  url: string
  /** The site it came from: what "different publishers" is counted by. */
  publisher: string
  source_tier: SourceTier
  /** `YYYY-MM-DD`, kept as written. */
  publication_date: string | null
  text: string
  score: number
  /** How many of this source's passages matched. */
  passages: number
}

export const ANSWER_ITEM_FIELDS = [
  'source_id',
  'chunk_id',
  'title',
  'url',
  'publisher',
  'source_tier',
  'publication_date',
  'text',
  'score',
  'passages',
] as const

export type Coverage = 'strong' | 'thin'

/** Mirrors `AnswerGroupRead`. `code` is null for sources about no place. */
export interface AnswerGroup {
  code: string | null
  name: string
  sources: number
  publishers: number
  tier_mix: Record<string, number>
  newest: string | null
  coverage: Coverage
  items: AnswerItem[]
  unexamined: number
  /** Unplaced only: sources about several countries whose matching passages name none. */
  several_places: number
}

export const ANSWER_GROUP_FIELDS = [
  'code',
  'name',
  'sources',
  'publishers',
  'tier_mix',
  'newest',
  'coverage',
  'items',
  'unexamined',
  'several_places',
] as const

/** Mirrors `AnswerPlaceRead`: a country the question names. */
export interface AnswerPlace {
  code: string
  name: string
}

export const ANSWER_PLACE_FIELDS = ['code', 'name'] as const

/**
 * How the answer is arranged (`B-241`): by country when the question or its filters name a
 * place or it asks about places, else one list.
 */
export const GROUPED_BY = ['place', 'none'] as const
export type GroupedBy = (typeof GROUPED_BY)[number]

/** Mirrors `AnswerRead`. */
export interface Answer {
  query: string
  grouped_by: GroupedBy
  /** Why it is arranged so, in a sentence the page shows. */
  grouping_reason: string
  places_named: AnswerPlace[]
  /** By country. When `grouped_by` is `none`, a tally of where the sources are about. */
  groups: AnswerGroup[]
  unplaced: AnswerGroup | null
  /** The whole pool as one list, when `grouped_by` is `none`. */
  ungrouped: AnswerGroup | null
  coverage_rule: string
  strong_min_publishers: number
  strong_needs_tiers: SourceTier[]
  topics: string[]
  passages_considered: number
  sources_considered: number
  candidate_pool: number
  arms: Array<'lexical' | 'vector'>
  degraded: boolean
  degraded_reason: string | null
  /** Whether a cross-encoder re-ordered the pool's top before grouping (`B-167`). */
  reranked: boolean
}

export const ANSWER_FIELDS = [
  'query',
  'grouped_by',
  'grouping_reason',
  'places_named',
  'groups',
  'unplaced',
  'ungrouped',
  'coverage_rule',
  'strong_min_publishers',
  'strong_needs_tiers',
  'topics',
  'passages_considered',
  'sources_considered',
  'candidate_pool',
  'arms',
  'degraded',
  'degraded_reason',
  'reranked',
] as const

export type AssertAnswerItem = Expect<Equal<keyof AnswerItem, (typeof ANSWER_ITEM_FIELDS)[number]>>
export type AssertAnswerGroup = Expect<Equal<keyof AnswerGroup, (typeof ANSWER_GROUP_FIELDS)[number]>>
export type AssertAnswerPlace = Expect<Equal<keyof AnswerPlace, (typeof ANSWER_PLACE_FIELDS)[number]>>
export type AssertAnswer = Expect<Equal<keyof Answer, (typeof ANSWER_FIELDS)[number]>>

/**
 * A group's coverage in a reader's words: "well-sourced", else what it lacks first. The wire
 * keeps `strong` and `thin`; a "strong" beside a country read as a verdict on it (`B-241`).
 */
export function coverageWord(
  group: Pick<AnswerGroup, 'coverage' | 'sources' | 'publishers'>,
  answer: Pick<Answer, 'strong_min_publishers'>,
): string {
  if (group.coverage === 'strong') return 'well-sourced'
  if (group.sources < answer.strong_min_publishers) return 'few sources'
  if (group.publishers < answer.strong_min_publishers) return 'few publishers'
  return 'no primary source'
}

export interface AnswerParams {
  q: string
  topic?: readonly string[]
  topic_match?: TopicMatch
  place?: readonly string[]
  source_tier?: readonly SourceTier[]
  doc_kind?: readonly DocKind[]
  site?: readonly string[]
  published_after?: string
  published_before?: string
}

export function getAnswer(params: AnswerParams, init?: RequestInit): Promise<Answer> {
  return request<Answer>(`/api/explore/answer?${searchQuery(params)}`, init)
}

// --------------------------------------------------------------------------
// Answer or passages
// --------------------------------------------------------------------------

export type ResultMode = 'answer' | 'passages'

const MODE_KEY = 'meridian.find.mode'

const QUESTION_WORDS =
  /^(what|which|how|why|who|where|when|is|are|does|do|did|can|could|should|would|will|has|have|compare)\b/i

/** Whether text reads as a question rather than a few search words. */
export function looksLikeQuestion(text: string): boolean {
  const trimmed = text.trim()
  return trimmed.endsWith('?') || QUESTION_WORDS.test(trimmed)
}

/** The mode a new search opens in: the reader's last choice, else a guess from the text. */
export function initialMode(text: string, storage: Pick<Storage, 'getItem'> | null = safeStorage()): ResultMode {
  try {
    const stored = storage?.getItem(MODE_KEY)
    if (stored === 'answer' || stored === 'passages') return stored
  } catch {
    // Storage that throws is storage that is not there.
  }
  return looksLikeQuestion(text) ? 'answer' : 'passages'
}

export function rememberMode(mode: ResultMode, storage: Pick<Storage, 'setItem'> | null = safeStorage()): void {
  try {
    storage?.setItem(MODE_KEY, mode)
  } catch {
    // A choice not remembered is a default next time, not an error now.
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

// --------------------------------------------------------------------------
// Find more
// --------------------------------------------------------------------------

/** The search a "find more" queues: the question with the place's name after it. */
export function findMoreQuery(question: string, place: string): string {
  return `${question.trim()} ${place.trim()}`.trim()
}

/**
 * Queue a search for more about one place. An operator's seed: a `query` task
 * filed under the reader's first topic, else the topic most matching sources
 * carry, so what it fetches is labelled where the reader will look for it.
 */
export function queueFindMore(
  question: string,
  place: string,
  topic: string | null,
  init?: RequestInit,
): Promise<QueueTask> {
  return addSeed(
    {
      url_or_query: findMoreQuery(question, place),
      task_type: 'query',
      topic,
      reason: `Find more from an answer: ${place || 'the whole question'}`,
    },
    init,
  )
}
