import { useState } from 'react'

import type { GraphEdge, GraphNode, Neighbourhood } from './api'
import { hrefForNode, onInternalClick } from '../../lib/route'
import { DAGGER } from '../../ui/Contested'

/**
 * §12.3's table view: "what exactly do I have, sortable". The same
 * neighbourhood the canvas draws — same filters, same cap — one row per
 * neighbour, with every relation that joins it to the focus rather than the
 * one line the canvas collapses them to.
 */

export type SortKey = 'name' | 'type' | 'support' | 'degree' | 'sources' | 'newest'

export interface TableRow {
  node: GraphNode
  relations: string[]
  certainty: string[]
}

export function tableRows(hood: Neighbourhood): TableRow[] {
  const focus = hood.focus.entity_id
  const byNeighbour = new Map<number, GraphEdge[]>()
  for (const edge of hood.edges) {
    if (edge.kind !== 'focus') continue
    const other = edge.from_node === focus ? edge.to_node : edge.from_node
    byNeighbour.set(other, [...(byNeighbour.get(other) ?? []), edge])
  }
  return hood.nodes
    .filter((n) => n.role === 'neighbour')
    .map((node) => {
      const edges = byNeighbour.get(node.entity_id) ?? []
      return {
        node,
        relations: [...new Set(edges.map((e) => e.relation_type.replaceAll('_', ' ')))],
        certainty: [...new Set(edges.map((e) => e.certainty).filter((c): c is string => Boolean(c)))],
      }
    })
}

export function sortRows(rows: readonly TableRow[], key: SortKey, descending: boolean): TableRow[] {
  const value = (r: TableRow): string | number => {
    switch (key) {
      case 'name':
        return r.node.canonical_name.toLowerCase()
      case 'type':
        return r.node.node_type
      case 'support':
        return r.node.support
      case 'degree':
        return r.node.degree
      case 'sources':
        return r.node.sources
      case 'newest':
        // Undated sorts as oldest, so it never heads a "newest first" list.
        return r.node.newest ?? ''
    }
  }
  const sorted = [...rows].sort((a, b) => {
    const [x, y] = [value(a), value(b)]
    if (x < y) return -1
    if (x > y) return 1
    return a.node.entity_id - b.node.entity_id
  })
  return descending ? sorted.reverse() : sorted
}

const COLUMNS: { key: SortKey; label: string; numeric?: boolean }[] = [
  { key: 'name', label: 'Neighbour' },
  { key: 'type', label: 'Type' },
  { key: 'support', label: 'Passages', numeric: true },
  { key: 'degree', label: 'Edges', numeric: true },
  { key: 'sources', label: 'Sources', numeric: true },
  { key: 'newest', label: 'Newest' },
]

export function TableView({ hood }: { hood: Neighbourhood }) {
  const [sort, setSort] = useState<{ key: SortKey; descending: boolean }>({
    key: 'support',
    descending: true,
  })
  const rows = sortRows(tableRows(hood), sort.key, sort.descending)

  if (rows.length === 0) return null

  return (
    <div className="absolute inset-0 overflow-auto px-[18px] pb-14 pt-14">
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-line-strong">
            {COLUMNS.map((c) => (
              <th
                key={c.key}
                scope="col"
                aria-sort={sort.key === c.key ? (sort.descending ? 'descending' : 'ascending') : 'none'}
                className={`py-2 pr-4 font-mono text-[9px] font-medium uppercase tracking-[0.15em] text-text-faint ${
                  c.numeric ? 'text-right' : ''
                }`}
              >
                <button
                  type="button"
                  onClick={() =>
                    setSort((s) => ({
                      key: c.key,
                      descending: s.key === c.key ? !s.descending : c.numeric || c.key === 'newest',
                    }))
                  }
                  className="uppercase"
                >
                  {c.label}
                  {sort.key === c.key ? (sort.descending ? ' ↓' : ' ↑') : ''}
                </button>
              </th>
            ))}
            <th
              scope="col"
              className="py-2 font-mono text-[9px] font-medium uppercase tracking-[0.15em] text-text-faint"
            >
              Relation
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ node, relations, certainty }) => (
            <tr key={node.entity_id} className="border-b border-line align-baseline">
              <td className="py-2 pr-4 text-[12.5px]">
                <a
                  href={hrefForNode(node.entity_id)}
                  onClick={onInternalClick(hrefForNode(node.entity_id))}
                  className={node.contested ? 'text-accent-attention' : 'text-text'}
                >
                  {node.canonical_name}
                </a>
                {node.contested ? (
                  <sup className="font-mono text-[0.72em] text-accent-attention" aria-label="contested">
                    {DAGGER}
                  </sup>
                ) : null}
                {node.cross_topic ? (
                  <span className="ml-2 font-mono text-[9px] uppercase tracking-[0.1em] text-accent-graph">
                    cross-topic
                  </span>
                ) : null}
              </td>
              <td className="py-2 pr-4 font-mono text-[10.5px] uppercase tracking-[0.08em] text-text-faint">
                {node.node_type.replaceAll('_', ' ')}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-[12px] tabular-nums text-text-muted">
                {node.support}
              </td>
              <td className="py-2 pr-4 text-right font-mono text-[12px] tabular-nums text-text-muted">{node.degree}</td>
              <td className="py-2 pr-4 text-right font-mono text-[12px] tabular-nums text-text-muted">
                {node.sources}
              </td>
              <td className="whitespace-nowrap py-2 pr-4 font-mono text-[12px] text-text-muted">
                {node.newest?.slice(0, 7) ?? '—'}
              </td>
              <td className="py-2 font-mono text-[11px] text-text-faint">
                {relations.join(', ')}
                {certainty.length ? ` · ${certainty.join(', ')}` : ''}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
