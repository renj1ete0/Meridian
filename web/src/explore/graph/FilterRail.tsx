import { useState, type ReactNode } from 'react'
import './graph.css'

import type { FacetCount, GraphFacets, GraphFilterState } from './api'
import { toggle } from './filters'
import { NodeSearchBox } from './NodeSearchBox'
import type { SavedViewRecord } from '../../lib/api'
import { SOURCE_TIERS, TIER_LABEL, type SourceTier } from '../../ui/Tier'

/**
 * The left rail: §12.2's live filters, saved views, and the legend (task P6-02, design
 * `Explore`). Every count is of this focus's neighbours, before any filter
 * (`GraphFacetsRead`). See docs/features/knowledge-graph.md#the-graph-workspace.
 */

const LBL = 'font-mono text-[9px] font-medium uppercase tracking-[0.15em] text-text-faint'
const COUNT = 'font-mono text-[10.5px] tabular-nums text-text-faint'

function Check({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`flex h-[13px] w-[13px] shrink-0 items-center justify-center border ${
        on ? 'border-accent-graph bg-accent-graph-deep' : 'border-line-strong bg-surface-raised'
      }`}
    >
      {on ? (
        <svg width="9" height="9" viewBox="0 0 12 12" fill="none">
          <path
            d="M2 6.4 L4.8 9 L10 3"
            stroke="currentColor"
            strokeWidth="1.9"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-text"
          />
        </svg>
      ) : null}
    </span>
  )
}

export function Row({
  label,
  count,
  on,
  onToggle,
  countClass = COUNT,
}: {
  label: ReactNode
  count: number | null
  on: boolean
  onToggle: () => void
  countClass?: string
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-2.5 text-[12.5px] text-text-muted">
      <span className="flex min-w-0 items-center gap-[9px]">
        <input type="checkbox" checked={on} onChange={onToggle} className="sr-only" />
        <Check on={on} />
        <span className="truncate">{label}</span>
      </span>
      {count !== null ? <span className={countClass}>{count}</span> : null}
    </label>
  )
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-[11px]">
      <h2 className={LBL}>{title}</h2>
      {children}
    </section>
  )
}

export const RULE = <div className="h-px shrink-0 bg-line" />

export function countOf(facets: readonly FacetCount[], value: string): number {
  return facets.find((f) => f.value === value)?.count ?? 0
}

/** Years spanned by the evidence, as the slider's range. */
export function yearSpan(facets: GraphFacets): [number, number] | null {
  if (!facets.published_min || !facets.published_max) return null
  return [Number(facets.published_min.slice(0, 4)), Number(facets.published_max.slice(0, 4))]
}

function YearRange({
  span,
  filters,
  onChange,
}: {
  span: [number, number] | null
  filters: GraphFilterState
  onChange: (next: GraphFilterState) => void
}) {
  if (!span) {
    return <p className="text-[12px] text-text-faint">No passage here carries a date.</p>
  }
  const [min, max] = span
  const from = filters.publishedFrom ? Math.max(min, Number(filters.publishedFrom.slice(0, 4))) : min
  const to = filters.publishedTo ? Math.min(max, Number(filters.publishedTo.slice(0, 4))) : max
  const width = Math.max(max - min, 1)
  const left = ((from - min) / width) * 100
  const right = ((max - to) / width) * 100

  function set(nextFrom: number, nextTo: number) {
    onChange({
      ...filters,
      // The whole span means no bound: an undated passage would otherwise be
      // excluded by a filter the reader never set.
      publishedFrom: nextFrom > min ? `${nextFrom}-01-01` : null,
      publishedTo: nextTo < max ? `${nextTo}-12-31` : null,
    })
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="relative h-[11px]">
        <div className="absolute inset-x-0 top-1 h-[3px] bg-surface-raised" />
        <div className="absolute top-1 h-[3px] bg-accent-graph-deep" style={{ left: `${left}%`, right: `${right}%` }} />
        {min === max ? null : (
          <>
            <input
              type="range"
              aria-label="Published from"
              min={min}
              max={max}
              value={from}
              onChange={(e) => set(Math.min(Number(e.target.value), to), to)}
              className="year-thumb pointer-events-none absolute inset-0 h-[11px] w-full appearance-none bg-transparent"
            />
            <input
              type="range"
              aria-label="Published to"
              min={min}
              max={max}
              value={to}
              onChange={(e) => set(from, Math.max(Number(e.target.value), from))}
              className="year-thumb pointer-events-none absolute inset-0 h-[11px] w-full appearance-none bg-transparent"
            />
          </>
        )}
      </div>
    </div>
  )
}

export interface FilterRailProps {
  facets: GraphFacets | null
  filters: GraphFilterState
  onChange: (next: GraphFilterState) => void
  views: readonly SavedViewRecord[] | null
  onOpenView: (view: SavedViewRecord) => void
  onSaveView?: (name: string) => void
  saveState?: { busy: boolean; error: string | null; saved: string | null }
  onPickNode: (entityId: number) => void
}

export function FilterRail({
  facets,
  filters,
  onChange,
  views,
  onOpenView,
  onSaveView,
  saveState,
  onPickNode,
}: FilterRailProps) {
  const [naming, setNaming] = useState<string | null>(null)
  const span = facets ? yearSpan(facets) : null
  // A topic ticked in the URL but absent from this neighbourhood still shows,
  // so it can be unticked — otherwise the filter is on and invisible.
  const topics = facets
    ? [
        ...facets.topics,
        ...filters.topics
          .filter((t) => !facets.topics.some((f) => f.value === t))
          .map((value) => ({ value, count: 0 })),
      ]
    : []

  return (
    <nav
      aria-label="Graph filters"
      className="order-3 flex w-full shrink-0 flex-col gap-5 border-t border-line bg-ground px-4 py-[18px] lg:order-none lg:h-full lg:min-h-0 lg:w-[236px] lg:overflow-y-auto lg:border-r lg:border-t-0"
    >
      <NodeSearchBox onPick={onPickNode} placeholder="Find a node" />

      <Section title="Topic">
        {topics.length === 0 ? (
          <p className="text-[12px] text-text-faint">No topic labels on this neighbourhood's sources.</p>
        ) : (
          topics.map((f) => (
            <Row
              key={f.value}
              label={f.value.replaceAll('-', ' ')}
              count={f.count}
              on={filters.topics.includes(f.value)}
              onToggle={() => onChange({ ...filters, topics: toggle(filters.topics, f.value) })}
            />
          ))
        )}
      </Section>

      {RULE}

      <Section title="Source tier">
        {SOURCE_TIERS.map((tier: SourceTier) => (
          <Row
            key={tier}
            label={TIER_LABEL[tier]}
            count={facets ? countOf(facets.tiers, tier) : null}
            on={filters.tiers.includes(tier)}
            onToggle={() => onChange({ ...filters, tiers: toggle(filters.tiers, tier) })}
          />
        ))}
      </Section>

      {facets && facets.attributes.length > 0 ? (
        <>
          {RULE}
          <Section title="Attribute">
            {facets.attributes.map((f) => (
              <Row
                key={f.value}
                label={f.value.replaceAll('_', ' ')}
                count={f.count}
                on={filters.attribute === f.value}
                onToggle={() =>
                  onChange({
                    ...filters,
                    attribute: filters.attribute === f.value ? null : f.value,
                  })
                }
              />
            ))}
          </Section>
        </>
      ) : null}

      {RULE}

      <section className="flex flex-col gap-3">
        <div className="flex items-baseline justify-between">
          <h2 className={LBL}>Published</h2>
          {span ? (
            <span className="font-mono text-[10.5px] text-text-muted">
              {filters.publishedFrom?.slice(0, 4) ?? span[0]} – {filters.publishedTo?.slice(0, 4) ?? span[1]}
            </span>
          ) : null}
        </div>
        <YearRange span={span} filters={filters} onChange={onChange} />
      </section>

      {RULE}

      <Row
        label="Contested only"
        count={facets ? facets.contested : null}
        on={filters.contestedOnly}
        onToggle={() => onChange({ ...filters, contestedOnly: !filters.contestedOnly })}
        countClass="font-mono text-[10.5px] tabular-nums text-accent-attention"
      />

      {RULE}

      <Section title="Saved views">
        {views === null ? (
          <p className="text-[12px] text-text-faint">Loading.</p>
        ) : views.length === 0 ? (
          <p className="text-[12px] text-text-faint">None saved.</p>
        ) : (
          views.slice(0, 6).map((view) => (
            <button
              key={view.view_id}
              type="button"
              onClick={() => onOpenView(view)}
              className="truncate text-left text-[12.5px] text-text-muted hover:text-text"
              title={
                view.focus_entity_id
                  ? 'Opens its focus node with its filters'
                  : 'A search view: opens on the search page'
              }
            >
              {view.name}
            </button>
          ))
        )}
        {onSaveView ? (
          naming === null ? (
            <button
              type="button"
              onClick={() => setNaming('')}
              className="self-start font-mono text-[10.5px] text-accent-graph"
            >
              Save this view
            </button>
          ) : (
            <form
              className="flex gap-1.5"
              onSubmit={(e) => {
                e.preventDefault()
                if (naming.trim()) onSaveView(naming.trim())
                setNaming(null)
              }}
            >
              <input
                aria-label="Name for this view"
                placeholder="Name this view"
                autoFocus
                value={naming}
                onChange={(e) => setNaming(e.target.value)}
                className="h-7 min-w-0 flex-1 border border-line-strong bg-surface-raised px-2 text-[12px] text-text"
              />
              <button type="submit" className="font-mono text-[10.5px] text-accent-graph">
                Save
              </button>
            </form>
          )
        ) : null}
        {saveState?.error ? (
          <p className="text-[11.5px] text-accent-attention">{saveState.error}</p>
        ) : saveState?.saved ? (
          <p className="text-[11.5px] text-text-faint">Saved as “{saveState.saved}”.</p>
        ) : null}
      </Section>

      <section className="mt-auto flex flex-col gap-2 pt-2">
        <h2 className={LBL}>Legend</h2>
        <ul className="font-mono text-[10px] leading-[1.8] text-text-faint">
          <li>
            <span className="mr-1.5 inline-block h-[7px] w-[7px] rounded-full bg-accent-graph" />
            focus
          </li>
          <li>
            <span className="mr-1.5 inline-block h-[7px] w-[7px] rounded-full bg-accent-attention" />
            contested †
          </li>
          <li>
            <span
              className="mr-1.5 inline-block h-[7px] w-[7px] rounded-full"
              style={{ background: 'var(--dark-canvas-neighbour)' }}
            />
            neighbour, depth 1
          </li>
          <li>
            <span className="mr-1.5 inline-block h-[7px] w-[7px] rounded-full bg-accent-graph/75" />
            cross-topic
          </li>
        </ul>
      </section>
    </nav>
  )
}
