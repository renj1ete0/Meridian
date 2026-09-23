/**
 * Recently focused nodes, for the landing's "where you were" (§8, task P6-01).
 *
 * Per viewer and per browser, like the since-last-visit stamp: two people on
 * one Meridian have different answers to "where was I". Stored in the shape
 * `WhereYouWere` renders, so the landing can pass `readRecentNodes()` straight
 * through. Every access is guarded — `localStorage` throws outright in a
 * private window, and this runs on every refocus.
 */

import type { RecentNode } from '../WhereYouWere'
import { NODE_GLYPHS } from '../../ui/icons'

const KEY = 'meridian.recentNodes'
export const MAX_RECENT = 8

function glyphFor(nodeType: string): RecentNode['nodeType'] {
  return (nodeType in NODE_GLYPHS ? nodeType : 'concept') as RecentNode['nodeType']
}

export function readRecentNodes(): RecentNode[] {
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? '[]')
    if (!Array.isArray(parsed)) return []
    return parsed.filter(
      (n): n is RecentNode =>
        typeof n === 'object' &&
        n !== null &&
        typeof (n as RecentNode).id === 'string' &&
        typeof (n as RecentNode).name === 'string',
    )
  } catch {
    return []
  }
}

/** Most recent first, one entry per node, capped. */
export function recordRecentNode(node: {
  entity_id: number
  canonical_name: string
  node_type: string
  contested?: boolean
}): RecentNode[] {
  const entry: RecentNode = {
    id: String(node.entity_id),
    name: node.canonical_name,
    nodeType: glyphFor(node.node_type),
    contested: Boolean(node.contested),
  }
  const next = [entry, ...readRecentNodes().filter((n) => n.id !== entry.id)].slice(0, MAX_RECENT)
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next))
  } catch {
    /* Not remembering where somebody was is not an error worth showing. */
  }
  return next
}
