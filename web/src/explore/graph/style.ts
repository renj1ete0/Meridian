/**
 * How each node and edge is drawn — design-system.md §2 "Graph canvas colours" and §6
 * (task P6-01). Colours come from `palette.ts`; the brass tint never appears without the
 * dagger.
 */

import type { GraphEdge, GraphNode } from './api'

/** The roles the canvas uses, one per row of the published table. */
export interface CanvasPalette {
  ground: string
  graticule: string
  meridian: string
  focus: string
  neighbour: string
  contested: string
  hint: string
  edgeFocus: string
  edgeBetween: string
  edgeContested: string
  edgeHint: string
  label: string
  labelFocus: string
  caption: string
}

/**
 * Where each role comes from in `tokens.css`. Dark palette entries in both
 * themes: §2's canvas table has no light column, and the canvas stays a dark
 * ground whatever the chrome around it is.
 */
export const PALETTE_TOKENS: Record<keyof CanvasPalette, string> = {
  ground: '--dark-ground-deep',
  graticule: '--dark-canvas-graticule',
  meridian: '--dark-canvas-meridian',
  focus: '--dark-accent-graph',
  neighbour: '--dark-canvas-neighbour',
  contested: '--dark-accent-attention',
  hint: '--dark-canvas-hint',
  edgeFocus: '--dark-canvas-edge',
  edgeBetween: '--dark-accent-graph-deep',
  edgeContested: '--dark-canvas-edge-contested',
  edgeHint: '--dark-canvas-edge-hint',
  label: '--dark-canvas-label',
  labelFocus: '--dark-text',
  caption: '--dark-canvas-caption',
}

/** `#RRGGBB` at an opacity, as `rgba()`. Anything else is returned unchanged. */
export function withAlpha(colour: string, alpha: number): string {
  const match = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(colour.trim())
  if (!match) return colour
  const [r, g, b] = match.slice(1).map((h) => parseInt(h, 16))
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export interface NodeLook {
  /** Radius in screen pixels, from the published table. */
  size: number
  color: string
  /** A halo ring around the node: focus and contested only. */
  ring: { radius: number; color: string; opacity: number } | null
  label: string | null
  /** Mono caption under the label, uppercase, e.g. `FINDING · CONTESTED`. */
  caption: string | null
  labelColor: string
  /** §6: a trailing † on the label. */
  dagger: boolean
  zIndex: number
}

function typeLabel(nodeType: string): string {
  return nodeType.replaceAll('_', ' ')
}

/** Neighbour radius: 6.5 to 8 by support, the published range. */
export function neighbourSize(support: number, maxSupport: number): number {
  if (maxSupport <= 1) return 7
  return 6.5 + 1.5 * Math.min(1, (support - 1) / (maxSupport - 1))
}

export function nodeLook(
  node: GraphNode,
  palette: CanvasPalette,
  options: { maxSupport: number; focusContested?: boolean; dimmed?: boolean },
): NodeLook {
  const dim = (c: string) => (options.dimmed ? withAlpha(c, 0.3) : c)

  if (node.role === 'focus') {
    return {
      size: 13,
      color: dim(palette.focus),
      ring: { radius: 26, color: palette.focus, opacity: 0.45 },
      label: node.canonical_name,
      caption: null,
      labelColor: palette.labelFocus,
      dagger: Boolean(options.focusContested),
      zIndex: 3,
    }
  }
  if (node.role === 'hint') {
    return {
      size: 3,
      color: dim(palette.hint),
      ring: null,
      label: null,
      caption: null,
      labelColor: palette.label,
      dagger: false,
      zIndex: 0,
    }
  }
  if (node.contested) {
    return {
      size: 8,
      color: dim(palette.contested),
      ring: { radius: 14, color: palette.contested, opacity: 0.6 },
      label: node.canonical_name,
      caption: `${typeLabel(node.node_type)} · contested`,
      labelColor: palette.contested,
      dagger: true,
      zIndex: 2,
    }
  }
  if (node.cross_topic) {
    return {
      size: 7.5,
      color: dim(withAlpha(palette.focus, 0.75)),
      ring: null,
      label: node.canonical_name,
      caption: 'cross-topic',
      labelColor: palette.focus,
      dagger: false,
      zIndex: 1,
    }
  }
  return {
    size: neighbourSize(node.support, options.maxSupport),
    color: dim(palette.neighbour),
    ring: null,
    label: node.canonical_name,
    caption: null,
    labelColor: palette.label,
    dagger: false,
    zIndex: 1,
  }
}

export interface EdgeLook {
  size: number
  color: string
  /** Drawn dashed, which WebGL lines cannot do — the overlay draws these. */
  dashed: boolean
  zIndex: number
}

export function edgeLook(
  edge: GraphEdge,
  palette: CanvasPalette,
  options: { crossTopic?: boolean; dimmed?: boolean; onPath?: boolean } = {},
): EdgeLook {
  const dim = (c: string) => (options.dimmed ? withAlpha(c, 0.25) : c)
  if (options.onPath) {
    return {
      size: 2.2,
      color: edge.contested ? palette.edgeContested : palette.focus,
      dashed: false,
      zIndex: 3,
    }
  }
  if (edge.contested) {
    return { size: 1.8, color: dim(palette.edgeContested), dashed: false, zIndex: 2 }
  }
  if (edge.kind === 'hint') {
    return { size: 1, color: dim(palette.edgeHint), dashed: false, zIndex: 0 }
  }
  if (edge.kind === 'between') {
    return { size: 1.2, color: dim(palette.edgeBetween), dashed: false, zIndex: 0 }
  }
  return {
    size: 1.6,
    color: dim(palette.edgeFocus),
    dashed: Boolean(options.crossTopic),
    zIndex: 1,
  }
}

/**
 * One drawn line per pair of nodes.
 *
 * Two entities can be joined by several edges (different relations, different
 * sources). WebGL draws parallel edges on top of each other, so the pair is
 * drawn once, and a contested edge wins — it is the one §9 says a reader must
 * not miss. The table view lists every edge.
 */
export function collapseEdges(edges: readonly GraphEdge[]): GraphEdge[] {
  const byPair = new Map<string, GraphEdge>()
  for (const edge of edges) {
    const key = [edge.from_node, edge.to_node].sort((a, b) => a - b).join(':')
    const held = byPair.get(key)
    if (
      !held ||
      (edge.contested && !held.contested) ||
      (edge.contested === held.contested && edge.support > held.support)
    ) {
      byPair.set(key, edge)
    }
  }
  return [...byPair.values()]
}
