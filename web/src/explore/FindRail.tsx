import { useEffect, useState } from 'react'

import { activeCount, dropsUndated, NO_FILTERS, parseYear, type FindFilters } from '../lib/find'
import type { SavedViewRecord } from '../lib/api'
import { SOURCE_TIERS, TIER_LABEL } from '../ui/Tier'
import { Row, RULE, Section } from './graph/FilterRail'
import { topicWords } from '../lib/growth'

/**
 * Find's left rail (`B-173`, design `Explore`): what the search is narrowed by (topic,
 * place, source type, years) and the saved views. Every change re-runs the search.
 * See docs/features/web-app.md#the-filter-rail.
 */

const NOTE = 'font-mono text-[10.5px] leading-[1.5] text-text-faint'

/** Places shown before "more": the list grows with the gazetteer, the rail does not. */
const PLACES_SHOWN = 8

function toggled<T>(list: readonly T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

export interface FindRailProps {
  topics: readonly string[]
  places: ReadonlyArray<{ code: string; name: string }>
  filters: FindFilters
  onChange: (next: FindFilters) => void
  /** Some sources were never examined for topics, or places: a filter leaves them out. */
  unexaminedTopics?: boolean
  unexaminedPlaces?: boolean
  views: readonly SavedViewRecord[]
  onOpenView: (view: SavedViewRecord) => void
}

/** A year box that commits on Enter or leaving it, so typing "20" does not search. */
function YearBox({
  label,
  value,
  onCommit,
}: {
  label: string
  value: number | null
  onCommit: (year: number | null) => void
}) {
  const [text, setText] = useState(value === null ? '' : String(value))
  useEffect(() => setText(value === null ? '' : String(value)), [value])
  const commit = () => {
    const year = parseYear(text)
    // An unreadable year is put back rather than searched for: a filter the reader
    // cannot see applied is worse than one that did not take.
    if (year === null && text.trim() !== '') {
      setText(value === null ? '' : String(value))
      return
    }
    if (year !== value) onCommit(year)
  }
  return (
    <label className="flex flex-col gap-1">
      <span className="font-mono text-[9px] uppercase tracking-[0.12em] text-text-faint">{label}</span>
      <input
        inputMode="numeric"
        placeholder="any"
        aria-label={`Published ${label}`}
        value={text}
        maxLength={4}
        onChange={(e) => setText(e.target.value.replace(/\D/g, ''))}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
        }}
        className="h-7 w-full border border-line-strong bg-surface-raised px-2 font-mono text-[12px] tabular-nums text-text placeholder:text-text-faint focus:border-accent-graph/70 focus:outline-none"
      />
    </label>
  )
}

export function FindRail({
  topics,
  places,
  filters,
  onChange,
  unexaminedTopics = false,
  unexaminedPlaces = false,
  views,
  onOpenView,
}: FindRailProps) {
  const [allPlaces, setAllPlaces] = useState(false)
  // A chosen place stays in view even past the fold, so it can be unticked.
  const shownPlaces =
    allPlaces || places.length <= PLACES_SHOWN
      ? places
      : places.filter((p, i) => i < PLACES_SHOWN || filters.places.includes(p.code))
  const hidden = places.length - shownPlaces.length
  const active = activeCount(filters)

  return (
    <nav aria-label="Search filters" className="flex flex-col gap-5">
      {/* The field above already says what filters do; the rail only offers the way out. */}
      {active > 0 ? (
        <button
          type="button"
          onClick={() => onChange(NO_FILTERS)}
          className="self-start font-mono text-[10.5px] text-accent-graph hover:underline"
        >
          clear {active}
        </button>
      ) : null}

      {topics.length > 0 ? (
        <Section title="Topic">
          {topics.map((topic) => (
            <Row
              key={topic}
              label={topicWords(topic)}
              count={null}
              on={filters.topics.includes(topic)}
              onToggle={() => onChange({ ...filters, topics: toggled(filters.topics, topic) })}
            />
          ))}
          {filters.topics.length > 1 ? (
            <Row
              label="all at once"
              count={null}
              on={filters.match === 'all'}
              onToggle={() => onChange({ ...filters, match: filters.match === 'all' ? 'any' : 'all' })}
            />
          ) : null}
          {filters.topics.length > 0 && unexaminedTopics ? (
            <p className={NOTE}>Documents not yet examined for topics are left out.</p>
          ) : null}
        </Section>
      ) : null}

      {places.length > 0 ? (
        <>
          {RULE}
          <Section title="Place">
            {shownPlaces.map((place) => (
              <Row
                key={place.code}
                label={place.name}
                count={null}
                on={filters.places.includes(place.code)}
                onToggle={() => onChange({ ...filters, places: toggled(filters.places, place.code) })}
              />
            ))}
            {hidden > 0 || allPlaces ? (
              <button
                type="button"
                onClick={() => setAllPlaces(!allPlaces)}
                className="self-start font-mono text-[10.5px] text-accent-graph hover:underline"
              >
                {allPlaces ? 'fewer' : `${hidden} more`}
              </button>
            ) : null}
            {filters.places.length > 0 && unexaminedPlaces ? (
              <p className={NOTE}>A document counts for a place only when it names it often enough to be about it.</p>
            ) : null}
          </Section>
        </>
      ) : null}

      {RULE}

      <Section title="Source type">
        {SOURCE_TIERS.map((tier) => (
          <Row
            key={tier}
            label={TIER_LABEL[tier]}
            count={null}
            on={filters.tiers.includes(tier)}
            onToggle={() => onChange({ ...filters, tiers: toggled(filters.tiers, tier) })}
          />
        ))}
      </Section>

      {RULE}

      <Section title="Published">
        <div className="grid grid-cols-2 gap-2">
          <YearBox label="from" value={filters.from} onCommit={(from) => onChange({ ...filters, from })} />
          <YearBox label="to" value={filters.to} onCommit={(to) => onChange({ ...filters, to })} />
        </div>
        {dropsUndated(filters) ? (
          <p className={NOTE}>Documents with no date are left out while a year is set.</p>
        ) : null}
      </Section>

      {views.length > 0 ? (
        <>
          {RULE}
          <Section title="Saved views">
            {views.slice(0, 6).map((view) => (
              <button
                key={view.view_id}
                type="button"
                onClick={() => onOpenView(view)}
                className="truncate text-left text-[12.5px] text-text-muted hover:text-text"
              >
                {view.name}
              </button>
            ))}
          </Section>
        </>
      ) : null}
    </nav>
  )
}

/** The rail's toggle below the width where it sits beside the results. */
export function FiltersButton({ open, count, onClick }: { open: boolean; count: number; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls="find-rail"
      onClick={onClick}
      className={`h-8 border px-3 font-mono text-[11px] lg:hidden ${
        count > 0 || open
          ? 'border-accent-graph/70 bg-accent-graph/10 text-accent-graph'
          : 'border-line-strong text-text-muted hover:text-text'
      }`}
    >
      Filters{count > 0 ? ` · ${count}` : ''}
    </button>
  )
}
