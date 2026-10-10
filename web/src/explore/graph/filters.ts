/**
 * The workspace's filter state, its URL and its saved-view form (task P6-02). The URL
 * uses the API's parameter names; parsing drops what the API would refuse.
 * See docs/features/knowledge-graph.md#the-graph-workspace.
 */

import { SOURCE_TIERS, type SourceTier } from '../../ui/Tier'
import { NO_FILTERS, filtersToRecord, type GraphFilterState } from './api'

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

/** The views §12.3 lists. Node-link, Table and Matrix are built. */
export const VIEWS = ['node-link', 'table', 'matrix', 'timeline', 'coverage'] as const
export type GraphView = (typeof VIEWS)[number]

export const VIEW_LABEL: Record<GraphView, string> = {
  'node-link': 'Node-link',
  table: 'Table',
  matrix: 'Matrix',
  timeline: 'Timeline',
  coverage: 'Coverage',
}

export const BUILT_VIEWS: ReadonlySet<GraphView> = new Set(['node-link', 'table', 'matrix'])

function isTier(value: unknown): value is SourceTier {
  return typeof value === 'string' && (SOURCE_TIERS as readonly string[]).includes(value)
}

function isDate(value: unknown): value is string {
  return typeof value === 'string' && ISO_DATE.test(value)
}

function strings(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((v): v is string => typeof v === 'string' && v !== '')
  return typeof value === 'string' && value !== '' ? [value] : []
}

/** Filters from anything shaped like a saved view's `filters` record. */
export function filtersFromRecord(record: Record<string, unknown>): GraphFilterState {
  const from = isDate(record.published_from) ? record.published_from : null
  const to = isDate(record.published_to) ? record.published_to : null
  return {
    // `topics`/`tiers` as a node view stores them (`B-193`), `topic`/`tier` as the URL does.
    topics: [...new Set(strings(record.topics ?? record.topic))],
    tiers: [...new Set(strings(record.tiers ?? record.tier).filter(isTier))],
    // A reversed range would be refused by the API; drop the bound that
    // makes it reversed rather than the whole filter set.
    publishedFrom: from && to && from > to ? null : from,
    publishedTo: to,
    contestedOnly: record.contested_only === true || record.contested_only === 'true',
    attribute: typeof record.attribute === 'string' && record.attribute ? record.attribute : null,
  }
}

export interface WorkspaceUrlState {
  filters: GraphFilterState
  view: GraphView
}

export function parseSearch(search: string): WorkspaceUrlState {
  const params = new URLSearchParams(search)
  const record: Record<string, unknown> = {
    topic: params.getAll('topic'),
    tier: params.getAll('tier'),
    published_from: params.get('published_from'),
    published_to: params.get('published_to'),
    contested_only: params.get('contested_only'),
    attribute: params.get('attribute'),
  }
  const view = params.get('view')
  return {
    filters: filtersFromRecord(record),
    view: (VIEWS as readonly string[]).includes(view ?? '') ? (view as GraphView) : 'node-link',
  }
}

export function toSearch(state: WorkspaceUrlState): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filtersToRecord(state.filters))) {
    if (Array.isArray(value)) for (const v of value) params.append(key, String(v))
    else params.set(key, String(value))
  }
  if (state.view !== 'node-link') params.set('view', state.view)
  const out = params.toString()
  return out ? `?${out}` : ''
}

export function hasFilters(filters: GraphFilterState): boolean {
  return Object.keys(filtersToRecord(filters)).length > 0
}

export { NO_FILTERS }

/** Toggle one value in a list, keeping the order it was first chosen in. */
export function toggle<T>(list: readonly T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}
