import { useEffect, useState } from 'react'

import {
  activeCount,
  dropsUndated,
  FIND_KINDS,
  KIND_LABEL,
  NO_FILTERS,
  parseYear,
  siteKey,
  type FindFilters,
} from '../lib/find'
import { listSites, type DocKind, type SavedViewRecord, type SiteCount } from '../lib/api'
import { SOURCE_TIERS, TIER_LABEL } from '../ui/Tier'
import { Row, RULE, Section } from './graph/FilterRail'
import { topicWords } from '../lib/growth'

/**
 * Find's left rail (`B-173`, design `Explore`): what the search is narrowed by (topic,
 * place, source type, kind, site, years) and the saved views. Every change re-runs the search.
 * See docs/features/web-app.md#the-filter-rail.
 */

const NOTE = 'font-mono text-[10.5px] leading-[1.5] text-text-faint'

/** Places shown before "more": the list grows with the gazetteer, the rail does not. */
const PLACES_SHOWN = 8
/** Sites offered without typing: the most-held, for the kinds chosen. */
const SITES_SHOWN = 6
/** Typed characters before the site box asks for suggestions, and how long it waits. */
const SITE_QUERY_MIN = 2
const SITE_QUERY_WAIT_MS = 200

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
  /** Where the site list comes from; the API unless a test says otherwise. */
  loadSites?: typeof listSites
}

/**
 * Sites (`B-254`): the most-held for the kinds chosen, with counts, and a box that finds any
 * other by part of its name or takes a pasted address. A chosen site stays listed to untick.
 */
function SiteSection({
  kinds,
  chosen,
  onChange,
  loadSites,
}: {
  kinds: readonly DocKind[]
  chosen: readonly string[]
  onChange: (sites: string[]) => void
  loadSites: typeof listSites
}) {
  const [top, setTop] = useState<SiteCount[]>([])
  const [text, setText] = useState('')
  const [suggested, setSuggested] = useState<SiteCount[]>([])
  const [refused, setRefused] = useState<string | null>(null)
  const kindKey = kinds.join(',')

  useEffect(() => {
    const abort = new AbortController()
    loadSites({ doc_kind: kinds, limit: SITES_SHOWN }, { signal: abort.signal })
      .then((r) => setTop(r.sites))
      // A rail without suggestions still filters by what is typed.
      .catch(() => setTop([]))
    return () => abort.abort()
    // `kindKey` stands for `kinds`, whose identity changes on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kindKey, loadSites])

  useEffect(() => {
    const needle = text.trim()
    if (needle.length < SITE_QUERY_MIN) {
      setSuggested([])
      return
    }
    const abort = new AbortController()
    const timer = setTimeout(() => {
      loadSites({ doc_kind: kinds, q: needle, limit: 8 }, { signal: abort.signal })
        .then((r) => setSuggested(r.sites))
        .catch(() => setSuggested([]))
    }, SITE_QUERY_WAIT_MS)
    return () => {
      clearTimeout(timer)
      abort.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- as above
  }, [text, kindKey, loadSites])

  const counts = new Map(top.map((s) => [s.site, s.sources]))
  const rows = [...chosen, ...top.map((s) => s.site).filter((s) => !chosen.includes(s))].slice(
    0,
    Math.max(SITES_SHOWN, chosen.length),
  )

  const add = () => {
    const key = siteKey(text)
    if (key === null) {
      setRefused(text.trim())
      return
    }
    setRefused(null)
    setText('')
    if (!chosen.includes(key)) onChange([...chosen, key])
  }

  return (
    <Section title="Site">
      {rows.map((site) => (
        <Row
          key={site}
          label={site}
          count={counts.get(site) ?? null}
          on={chosen.includes(site)}
          onToggle={() => onChange(toggled(chosen, site))}
        />
      ))}
      <input
        aria-label="Find a site"
        placeholder="find a site"
        list="find-site-suggestions"
        value={text}
        onChange={(e) => {
          setText(e.target.value)
          setRefused(null)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault()
            add()
          }
        }}
        className="h-7 w-full border border-line-strong bg-surface-raised px-2 font-mono text-[12px] text-text placeholder:text-text-faint focus:border-accent-graph/70 focus:outline-none"
      />
      <datalist id="find-site-suggestions">
        {suggested.map((s) => (
          <option key={s.site} value={s.site}>
            {s.sources} sources
          </option>
        ))}
      </datalist>
      {refused ? (
        <p role="alert" className={NOTE}>
          Not a site: {refused}. Type a name like example.org.
        </p>
      ) : null}
    </Section>
  )
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
  loadSites = listSites,
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

      <Section title="Kind">
        {FIND_KINDS.map((kind) => (
          <Row
            key={kind}
            label={KIND_LABEL[kind]}
            count={null}
            on={filters.kinds.includes(kind)}
            onToggle={() => onChange({ ...filters, kinds: toggled(filters.kinds, kind) })}
          />
        ))}
        {filters.kinds.length > 0 ? <p className={NOTE}>Documents not yet sorted by kind are left out.</p> : null}
      </Section>

      {RULE}

      <SiteSection
        kinds={filters.kinds}
        chosen={filters.sites}
        onChange={(sites) => onChange({ ...filters, sites })}
        loadSites={loadSites}
      />

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
