/**
 * Where each node sits on the canvas (task P6-01).
 *
 * **Radial and deterministic, not force-directed.** §12.2 draws one focus and
 * its depth-1 neighbours, so the geometry already has a centre: the focus. A
 * force layout would spend its iterations rediscovering that, would place the
 * same neighbourhood differently on every visit, and would pull in a second
 * dependency. Here the focus is at the origin, neighbours are on a ring in rank
 * order, and second-hop hints sit just outside the neighbour they hang from —
 * so the same data draws the same picture twice, and "strongest first" reads
 * clockwise from the top.
 *
 * Units are abstract: Sigma fits the bounding box to the viewport. The
 * graticule behind the graph (design `Explore`) is drawn in the same units, so
 * it scales and pans with the nodes.
 */

import type { GraphEdge, GraphNode } from './api'

export interface Point {
  x: number
  y: number
}

/** The neighbour ring. Alternating radii keep adjacent labels apart. */
export const RING_OUTER = 1
export const RING_INNER = 0.74
/** Beyond this many neighbours the ring alternates between two radii. */
export const STAGGER_FROM = 9
/** Where hints sit, and the meridian circle drawn behind everything. */
export const HINT_RADIUS = 1.42
export const MERIDIAN_RADIUS = 1.22
/** The meridian ellipse's width as a share of its height, as in the mock (122 / 330). */
export const MERIDIAN_ASPECT = 122 / 330

/** Start just right of twelve o'clock, so the top neighbour's label clears the focus's. */
const START = -Math.PI / 2 + 0.35

/**
 * Positions for a neighbourhood: focus, ranked neighbours, then hints.
 *
 * `nodes` is taken in the order the API returns it, which is rank order.
 */
export function radialLayout(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): Map<number, Point> {
  const out = new Map<number, Point>()
  const focus = nodes.find((n) => n.role === 'focus')
  if (!focus) return out
  out.set(focus.entity_id, { x: 0, y: 0 })

  const neighbours = nodes.filter((n) => n.role === 'neighbour')
  const angle = new Map<number, number>()
  const step = (2 * Math.PI) / Math.max(neighbours.length, 1)
  neighbours.forEach((node, index) => {
    const theta = START + index * step
    const radius = neighbours.length >= STAGGER_FROM && index % 2 === 1 ? RING_INNER : RING_OUTER
    angle.set(node.entity_id, theta)
    out.set(node.entity_id, { x: radius * Math.cos(theta), y: radius * Math.sin(theta) })
  })

  // Each hint hangs from the first shown neighbour it is joined to.
  const hints = nodes.filter((n) => n.role === 'hint')
  const via = new Map<number, number>()
  for (const edge of edges) {
    if (edge.kind !== 'hint') continue
    for (const [hint, anchor] of [
      [edge.to_node, edge.from_node],
      [edge.from_node, edge.to_node],
    ] as const) {
      if (!via.has(hint) && angle.has(anchor) && !angle.has(hint)) via.set(hint, anchor)
    }
  }
  const fanned = new Map<number, number[]>()
  for (const hint of hints) {
    const anchor = via.get(hint.entity_id)
    if (anchor === undefined) continue
    fanned.set(anchor, [...(fanned.get(anchor) ?? []), hint.entity_id])
  }
  const spread = Math.min(0.32, step * 0.4)
  for (const [anchor, ids] of fanned) {
    const theta = angle.get(anchor)!
    ids.forEach((id, j) => {
      const offset = (j - (ids.length - 1) / 2) * spread
      out.set(id, {
        x: HINT_RADIUS * Math.cos(theta + offset),
        y: HINT_RADIUS * Math.sin(theta + offset),
      })
    })
  }
  // A hint whose anchor is not shown still needs a place; park it on the
  // outer ring rather than at the origin on top of the focus.
  hints.forEach((hint, index) => {
    if (!out.has(hint.entity_id)) {
      const theta = START + (index + 0.5) * ((2 * Math.PI) / Math.max(hints.length, 1))
      out.set(hint.entity_id, { x: HINT_RADIUS * Math.cos(theta), y: HINT_RADIUS * Math.sin(theta) })
    }
  })
  return out
}

/**
 * A path, left to right on a shallow arc (task P6-03).
 *
 * A route is a sequence, and a line reads as one. The arc keeps the labels of
 * a long route from sitting on the straight edges between them.
 */
export function pathLayout(ids: readonly number[]): Map<number, Point> {
  const out = new Map<number, Point>()
  const last = Math.max(ids.length - 1, 1)
  ids.forEach((id, index) => {
    const t = index / last
    out.set(id, { x: -1 + 2 * t, y: -0.28 * Math.sin(Math.PI * t) })
  })
  return out
}
