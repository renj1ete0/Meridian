import { useState } from 'react'

import type { GraphEdge, GraphNode, Neighbourhood } from './api'
import { DAGGER } from '../../ui/Contested'

/**
 * §12.3's adjacency matrix: the canvas's neighbourhood (same filters, same cap) as a
 * symmetric square of the focus and its shown neighbours, without second-hop hints.
 * See docs/features/knowledge-graph.md#the-graph-workspace.
 */

export interface MatrixCell {
  /** The pair, lower id first. */
  pair: [number, number]
  edges: GraphEdge[]
  /** Distinct passages behind these edges, summed per edge. */
  support: number
  contested: boolean
}

export interface Matrix {
  /** Focus first, then the neighbours in the order the API ranked them. */
  nodes: GraphNode[]
  /** Keyed by `pairKey`. Only pairs with at least one edge are present. */
  cells: Map<string, MatrixCell>
  /** Off-diagonal pairs with at least one edge. */
  joined: number
  /** Off-diagonal pairs there could be: n(n−1)/2. */
  possible: number
  /** Joined pairs with at least one contested edge. */
  contested: number
  /** Edges drawn in the matrix, so a reader can reconcile it with the canvas. */
  edges: number
  /** The most edges any one cell holds; what the shading is scaled to. */
  maxEdges: number
}

export function pairKey(a: number, b: number): string {
  return a <= b ? `${a}:${b}` : `${b}:${a}`
}

export function adjacency(hood: Neighbourhood): Matrix {
  const focus = hood.nodes.find((n) => n.role === 'focus') ?? hood.focus
  const nodes = [focus, ...hood.nodes.filter((n) => n.role === 'neighbour')]
  const members = new Set(nodes.map((n) => n.entity_id))

  const cells = new Map<string, MatrixCell>()
  let edges = 0
  for (const edge of hood.edges) {
    if (edge.kind === 'hint') continue
    // An edge whose far end is not a shown node belongs to a neighbour the cap
    // or a filter removed. Counting it would put weight on a row that is not
    // there.
    if (!members.has(edge.from_node) || !members.has(edge.to_node)) continue
    const key = pairKey(edge.from_node, edge.to_node)
    const held = cells.get(key)
    const pair: [number, number] =
      edge.from_node <= edge.to_node ? [edge.from_node, edge.to_node] : [edge.to_node, edge.from_node]
    const cell = held ?? { pair, edges: [], support: 0, contested: false }
    // The API lists a focus edge once per neighbour and a between edge once;
    // the same edge id twice would be a double count, not a second relation.
    if (cell.edges.some((e) => e.edge_id === edge.edge_id)) continue
    cell.edges.push(edge)
    cell.support += edge.support
    cell.contested ||= edge.contested
    cells.set(key, cell)
    edges += 1
  }

  let joined = 0
  let contested = 0
  let maxEdges = 0
  for (const cell of cells.values()) {
    maxEdges = Math.max(maxEdges, cell.edges.length)
    if (cell.pair[0] === cell.pair[1]) continue
    joined += 1
    if (cell.contested) contested += 1
  }

  const n = nodes.length
  return { nodes, cells, joined, possible: (n * (n - 1)) / 2, contested, edges, maxEdges }
}

/** The status line under the matrix: what it holds, and what it leaves out. */
export function matrixLine(matrix: Matrix, hood: Neighbourhood): string {
  const parts = [
    `${matrix.nodes.length} node${matrix.nodes.length === 1 ? '' : 's'}`,
    `${matrix.joined} of ${matrix.possible} pairs joined`,
  ]
  if (matrix.contested) parts.push(`${matrix.contested} contested`)
  if (hood.shown < hood.total) parts.push(`${hood.shown} of ${hood.total} neighbours shown`)
  return parts.join(' · ')
}

/** Cell size by node count: legible at the default cap, still a square at the ceiling. */
export function cellSize(count: number): number {
  if (count <= 32) return 24
  if (count <= 64) return 16
  return 11
}

function relation(edge: GraphEdge): string {
  return edge.relation_type.replaceAll('_', ' ')
}

export interface MatrixViewProps {
  hood: Neighbourhood
  /** Refocus on a node, keeping the filters and the view. */
  onPick: (entityId: number) => void
  /** The URL a row label links to, so it can be opened in a new tab. */
  hrefFor: (entityId: number) => string
}

export function MatrixView({ hood, onPick, hrefFor }: MatrixViewProps) {
  const matrix = adjacency(hood)
  const [active, setActive] = useState<[number, number] | null>(null)
  const size = cellSize(matrix.nodes.length)
  const showCounts = size >= 16
  const byId = new Map(matrix.nodes.map((n) => [n.entity_id, n]))
  const focusId = matrix.nodes[0]?.entity_id

  // `active` holds row and column *indices*; the cell map is keyed by ids.
  const activeRow = active ? matrix.nodes[active[0]] : undefined
  const activeCol = active ? matrix.nodes[active[1]] : undefined
  const activeCell =
    activeRow && activeCol ? matrix.cells.get(pairKey(activeRow.entity_id, activeCol.entity_id)) : undefined

  function label(node: GraphNode, vertical: boolean) {
    const isFocus = node.entity_id === focusId
    const contested = isFocus ? hood.focus_contested : node.contested
    return (
      <a
        href={hrefFor(node.entity_id)}
        onClick={(event) => {
          if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
          event.preventDefault()
          onPick(node.entity_id)
        }}
        title={node.canonical_name}
        className={`block truncate text-[12px] leading-none ${
          isFocus
            ? 'font-semibold text-accent-graph'
            : contested
              ? 'text-accent-attention'
              : 'text-text-muted hover:text-text'
        } ${vertical ? 'max-h-[150px]' : 'max-w-[200px]'}`}
      >
        {node.canonical_name}
        {contested ? (
          <sup className="font-mono text-[0.72em] text-accent-attention" aria-label="contested">
            {DAGGER}
          </sup>
        ) : null}
      </a>
    )
  }

  return (
    <div className="absolute inset-0" data-view="matrix">
      <div className="absolute inset-0 overflow-auto px-[18px] pb-14 pt-16">
        <table className="border-collapse" aria-label="Adjacency matrix" onMouseLeave={() => setActive(null)}>
          <thead>
            <tr>
              {/* The corner is the one empty space the square leaves, and the
                  key belongs beside what it explains. */}
              <td className="max-w-[200px] pb-3 pr-3 text-left align-bottom">
                <Key maxEdges={matrix.maxEdges} />
              </td>
              {matrix.nodes.map((node) => (
                <th
                  key={node.entity_id}
                  scope="col"
                  className="h-[160px] p-0 align-bottom font-normal"
                  style={{ width: size, minWidth: size }}
                >
                  <div className="flex h-full items-end justify-center pb-2 [writing-mode:vertical-rl] rotate-180">
                    {label(node, true)}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.nodes.map((row, r) => (
              <tr key={row.entity_id}>
                <th scope="row" className="py-0 pl-0 pr-3 text-right font-normal" style={{ height: size }}>
                  {label(row, false)}
                </th>
                {matrix.nodes.map((col, c) => {
                  const cell = matrix.cells.get(pairKey(row.entity_id, col.entity_id))
                  const crossed = active !== null && (active[0] === r || active[1] === c)
                  return (
                    <td
                      key={col.entity_id}
                      className={`border border-line/60 p-0 text-center ${
                        r === c ? 'bg-surface/60' : crossed ? 'bg-surface-raised/50' : ''
                      }`}
                      style={{ width: size, height: size, minWidth: size }}
                    >
                      {cell && r !== c ? (
                        <MatrixCellButton
                          cell={cell}
                          size={size}
                          maxEdges={matrix.maxEdges}
                          showCount={showCounts}
                          label={`${row.canonical_name} and ${col.canonical_name}: ${cell.edges.length} relation${
                            cell.edges.length === 1 ? '' : 's'
                          }${cell.contested ? ', contested' : ''}`}
                          onActive={() => setActive([r, c])}
                        />
                      ) : null}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Over the canvas's corner, as the node-link hover card is: the matrix
          scrolls under it rather than being pushed aside by it. */}
      <div aria-live="polite" className="pointer-events-none absolute bottom-4 right-[18px] w-[300px]">
        {activeCell ? <Readout cell={activeCell} byId={byId} /> : null}
      </div>
    </div>
  )
}

/**
 * Shading, as a share of the accent: more relations, a stronger fill. Never
 * below a floor that still reads as filled against the canvas, so a pair with
 * one relation is not mistaken for a gap.
 */
export const SHADE_FLOOR = 38
export const SHADE_CEILING = 88

export function shade(count: number, maxEdges: number): number {
  if (count <= 0 || maxEdges <= 0) return 0
  if (maxEdges === 1) return SHADE_FLOOR
  return Math.round(SHADE_FLOOR + ((SHADE_CEILING - SHADE_FLOOR) * (count - 1)) / (maxEdges - 1))
}

function MatrixCellButton({
  cell,
  size,
  maxEdges,
  showCount,
  label,
  onActive,
}: {
  cell: MatrixCell
  size: number
  maxEdges: number
  showCount: boolean
  label: string
  onActive: () => void
}) {
  const count = cell.edges.length
  // §6: the brass tint never appears without the dagger. A contested cell
  // carries both, so the reading survives the colour being stripped.
  const style = cell.contested
    ? undefined
    : { backgroundColor: `color-mix(in srgb, var(--accent-graph) ${shade(count, maxEdges)}%, transparent)` }
  return (
    <button
      type="button"
      aria-label={label}
      data-contested={cell.contested ? '' : undefined}
      onMouseEnter={onActive}
      onFocus={onActive}
      onClick={onActive}
      style={{ width: size, height: size, ...style }}
      className={`flex items-center justify-center font-mono text-[10px] leading-none tabular-nums ${
        cell.contested
          ? 'border border-accent-attention bg-accent-attention-deep text-accent-attention'
          : shade(count, maxEdges) >= 60
            ? 'text-ground-deep'
            : 'text-text'
      }`}
    >
      {showCount ? count : null}
      {cell.contested ? (
        <sup className="text-[0.8em]" aria-hidden="true">
          {DAGGER}
        </sup>
      ) : null}
    </button>
  )
}

function Readout({ cell, byId }: { cell: MatrixCell; byId: Map<number, GraphNode> }) {
  const name = (id: number) => byId.get(id)?.canonical_name ?? `#${id}`
  return (
    <div className="flex flex-col gap-2.5 border border-text/15 bg-surface/90 px-[13px] py-[11px] backdrop-blur-[10px]">
      <p className="font-mono text-[9px] font-medium uppercase tracking-[0.15em] text-text-faint">
        {cell.edges.length} relation{cell.edges.length === 1 ? '' : 's'} · {cell.support} passage
        {cell.support === 1 ? '' : 's'}
      </p>
      <ul className="flex flex-col gap-2.5">
        {cell.edges.map((edge) => (
          <li key={edge.edge_id} className="text-[12.5px] leading-[1.45]">
            <span className="text-text">{name(edge.from_node)}</span>{' '}
            <span className={edge.contested ? 'text-accent-attention' : 'text-accent-graph'}>
              {relation(edge)}
              {edge.contested ? (
                <sup className="font-mono text-[0.72em]" aria-label="contested">
                  {DAGGER}
                </sup>
              ) : null}
            </span>{' '}
            <span className="text-text">{name(edge.to_node)}</span>
            <span className="mt-0.5 block font-mono text-[10.5px] text-text-faint">
              {edge.support} passage{edge.support === 1 ? '' : 's'}
              {edge.certainty ? ` · ${edge.certainty}` : ''}
              {edge.stance ? ` · ${edge.stance}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Key({ maxEdges }: { maxEdges: number }) {
  const steps = [...new Set([1, Math.ceil(maxEdges / 2), maxEdges])].filter((n) => n >= 1)
  return (
    <div className="flex flex-col gap-2 font-mono text-[10.5px] text-text-faint">
      <p className="font-sans text-[11.5px] leading-[1.45] text-text-faint">
        A filled cell is a pair the graph relates. An empty one is a gap.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        {steps.map((n) => (
          <span key={n} className="flex items-center gap-1">
            <span
              aria-hidden="true"
              className="inline-block h-3 w-3"
              style={{ backgroundColor: `color-mix(in srgb, var(--accent-graph) ${shade(n, maxEdges)}%, transparent)` }}
            />
            {n}
          </span>
        ))}
        <span>relation{maxEdges === 1 ? '' : 's'}</span>
      </div>
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="inline-block h-3 w-3 border border-accent-attention bg-accent-attention-deep"
        />
        <span>
          contested<sup className="text-accent-attention">{DAGGER}</sup>
        </span>
      </div>
    </div>
  )
}
