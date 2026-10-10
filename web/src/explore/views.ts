/**
 * Where a saved view opens (`B-177`): a node view on its node with its filters, a search
 * view on Find with its words and filters. One function, so the landing and the node
 * workspace's rail cannot disagree. See docs/features/web-app.md#where-you-were.
 */
import type { SavedViewRecord } from '../lib/api'
import { filtersOfView, findLink } from '../lib/find'
import { hrefForNode } from '../lib/route'
import { filtersFromRecord, toSearch } from './graph/filters'

export function hrefForView(view: Pick<SavedViewRecord, 'focus_entity_id' | 'query' | 'filters'>): string {
  if (view.focus_entity_id !== null) {
    return `${hrefForNode(view.focus_entity_id)}${toSearch({ filters: filtersFromRecord(view.filters), view: 'node-link' })}`
  }
  return findLink(view.query ?? '', filtersOfView(view.filters))
}
