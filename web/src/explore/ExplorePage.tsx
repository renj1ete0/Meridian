import { useCallback, useEffect, useRef, useState } from 'react'

import { focusSearch } from '../lib/hotkeys'
import { openSession } from '../lib/lastVisit'
import { navigate } from '../lib/route'
import { initialMode, rememberMode, type ResultMode } from '../lib/answer'
import { Lockup } from '../ui/Mark'
import { TIER_LABEL } from '../ui/Tier'
import { AnswerView } from './AnswerView'
import { NotesPanel } from './Annotations'
import { CorpusCounts, type CorpusFigures } from './CorpusCounts'
import { EntryPoints, type EntryPointName } from './EntryPoints'
import { FirstHour } from './FirstHour'
import { ResultList } from './ResultList'
import { SaveView } from './SaveView'
import { SearchField } from './SearchField'
import { SinceLastVisit } from './SinceLastVisit'
import { NeighbourhoodPanel, type NeighbourhoodPanelProps } from './neighbourhood/NeighbourhoodPanel'
import { getTermNeighbourhood, hasNeighbourhood } from './neighbourhood/api'
import { FiltersButton, FindRail } from './FindRail'
import { topicWords } from '../lib/growth'
import { WhereYouWere } from './WhereYouWere'
import {
  ApiError,
  corpusStats,
  getAnnotations,
  getCrawlProgress,
  getSavedViews,
  markViewOpened,
  saveView,
  searchCorpus,
  type Annotation,
  type CorpusStats,
  type CrawlProgress,
  type SavedViewRecord,
  type SearchResponse,
} from '../lib/api'
import {
  activeCount,
  filtersOfView,
  findLink,
  NO_FILTERS,
  readFind,
  searchFilters,
  viewFiltersOf,
  type FindFilters,
} from '../lib/find'

/**
 * Explore — the landing and the search results (tasks P2-08, P6-27; spec §12.5;
 * design-system.md §8; `ExploreLanding.dc.html`). The degraded-search notice is rendered,
 * and searching happens on submit. See docs/features/web-app.md#explore-and-find.
 */

/** §12.5's counts, from what `/stats` actually returns. */
function figuresFrom(stats: CorpusStats): CorpusFigures {
  return {
    // Documents, not chunks: a chunk count is an artefact of how they were cut up. And the
    // ones a reader can find, as Growth counts them, not junk and copies too (`B-156`).
    documents: stats.kept_sources,
    nodes: stats.entities,
    edges: stats.edges,
    contested: stats.contested_edges,
  }
}

type Phase = 'idle' | 'searching' | 'done' | 'failed'

/**
 * How many notes the landing screen shows. Small on purpose: this is a way back
 * into recent thinking, not the notebook — the export is the notebook, and a
 * landing page that opened with fifty of anything is one nobody reads.
 */
const NOTES_ON_LANDING = 5

/** How many saved views "where you were" lists. §8: a *short* list. */
const VIEWS_ON_LANDING = 6

/** Passages asked for at a time, as the route's default page. */
const PAGE = 20

/** What each entry card says, derived from the counts rather than written once. */
export function entryState(stats: CorpusStats | null): {
  unavailable: Partial<Record<EntryPointName, string>>
  descriptions: Partial<Record<EntryPointName, string>>
} {
  const unavailable: Partial<Record<EntryPointName, string>> = {}
  const descriptions: Partial<Record<EntryPointName, string>> = {}
  if (stats) {
    if (stats.edges === 0) {
      // Derived rather than hardcoded: once edges exist this stops claiming
      // they do not, without anyone remembering to change it.
      unavailable.contested = 'No stated links yet, so no source disagrees with another.'
    } else if (stats.contested_edges === 0) {
      descriptions.contested = `No two sources disagree across ${stats.edges.toLocaleString('en')} stated links yet. When they do, both sides are kept; neither is resolved.`
    } else {
      const n = stats.contested_edges
      descriptions.contested = `${n.toLocaleString('en')} ${n === 1 ? 'claim' : 'claims'} sources disagree about. Both sides are kept; neither is resolved.`
    }
  }
  return { unavailable, descriptions }
}

export function ExplorePage() {
  const [query, setQuery] = useState('')
  const [asked, setAsked] = useState('')
  // Filters narrow the *next* search rather than re-filtering the last one's
  // results: fusion ranks a candidate pool, so a filter applied afterwards
  // would show the top 20 of an unfiltered ranking with most of them removed,
  // which looks like a topic with almost nothing in it (`P6-24`).
  const [filters, setFilters] = useState<FindFilters>(NO_FILTERS)
  // The rail below `lg`, where it folds behind a button.
  const [railOpen, setRailOpen] = useState(false)

  const [views, setViews] = useState<readonly SavedViewRecord[]>([])
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedCount, setSavedCount] = useState(0)
  const [phase, setPhase] = useState<Phase>('idle')
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The next page of passages (`B-174`): idle, reading, or the reason it failed.
  const [more, setMore] = useState<{ phase: 'idle' | 'loading' } | { phase: 'failed'; message: string }>({
    phase: 'idle',
  })
  // Answer (grouped by country) or passages (the ranked list). Decided per
  // search: the reader's last choice, else whether the text reads as a question.
  const [mode, setMode] = useState<ResultMode>('passages')
  // Whether the reader picked the tab for this search, so a shared link opens on it.
  const modeChosen = useRef(false)

  const [stats, setStats] = useState<CorpusStats | null>(null)
  const [statsError, setStatsError] = useState<string | null>(null)
  const [progress, setProgress] = useState<CrawlProgress | null>(null)

  // The reader's own layer (`P6-05`). Loaded beside the stats rather than
  // behind a click: §12.5's argument for building the affordance early is that
  // an unseen one does not get used, and that applies to reading them back.
  const [notes, setNotes] = useState<readonly Annotation[]>([])
  const [noteCount, setNoteCount] = useState(0)

  const inFlight = useRef<AbortController | null>(null)

  // The neighbourhood beside the results (`P6-33`): the term as asked, or a
  // node the reader picked from the candidates when the term named none.
  const [hood, setHood] = useState<NeighbourhoodPanelProps['state'] | null>(null)
  const [picked, setPicked] = useState<number | null>(null)
  useEffect(() => {
    if (!asked) {
      setHood(null)
      return
    }
    const controller = new AbortController()
    setHood({ phase: 'loading' })
    getTermNeighbourhood(picked !== null ? { entityId: picked } : { q: asked }, {
      signal: controller.signal,
    })
      .then((data) => setHood({ phase: 'done', data }))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        // The panel is beside the results, not in front of them: its failure
        // is named in the panel and the results stand.
        setHood({
          phase: 'failed',
          message:
            cause instanceof ApiError
              ? `The neighbourhood could not be read: ${cause.message}`
              : 'The neighbourhood could not be read.',
        })
      })
    return () => controller.abort()
  }, [asked, picked])

  const showHood = hood !== null && (hood.phase === 'failed' || (hood.phase === 'done' && hasNeighbourhood(hood.data)))

  // Read once, and the stamp advances immediately. Writing it later — on
  // unmount, or after the fetch — is how the delta ends up always zero: the
  // second render reads a stamp the first one just wrote. `P6-11`.
  const [since] = useState<string | null>(() => openSession())

  useEffect(() => {
    const controller = new AbortController()
    corpusStats({ since }, { signal: controller.signal })
      .then(setStats)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        // Counts that cannot be fetched stay null, so `CorpusCounts` renders em
        // dashes rather than zeros — the distinction it exists to preserve.
        setStatsError(cause instanceof ApiError ? cause.message : 'The corpus counts are unavailable.')
      })
    return () => controller.abort()
    // `since` is captured once and never changes, so this runs on mount only.
  }, [since])

  // An empty corpus shows what the crawl is doing instead of three entry
  // points into nothing (`B-09`). Asked for only when it is empty.
  const empty = stats !== null && stats.sources === 0
  useEffect(() => {
    if (!empty) return
    const controller = new AbortController()
    getCrawlProgress({ signal: controller.signal })
      .then(setProgress)
      .catch(() => setProgress(null))
    return () => controller.abort()
  }, [empty])

  useEffect(() => {
    const controller = new AbortController()
    getSavedViews({ signal: controller.signal })
      .then((body) => setViews(body.views))
      .catch(() => {
        // Saved views are a convenience, not the corpus. A landing page that
        // failed to render because this list could not be fetched would spend
        // the whole screen on the least important thing on it.
        setViews([])
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    getAnnotations({ limit: NOTES_ON_LANDING }, { signal: controller.signal })
      .then((body) => {
        setNotes(body.annotations)
        // The total, not the page. A reader with four hundred notes is owed the
        // number, and a panel that reports its own length cannot tell them.
        setNoteCount(body.total)
      })
      .catch(() => {
        // Same reasoning as the views above, one step stronger: this panel is
        // the reader's own writing, and a landing page that refused to render
        // because it could not be listed would take the corpus down with it.
        setNotes([])
        setNoteCount(0)
      })
    return () => controller.abort()
  }, [])

  const run = useCallback(
    (text: string, within: FindFilters = NO_FILTERS, history: 'push' | 'keep' = 'push', view?: ResultMode) => {
      const trimmed = text.trim()
      if (!trimmed) return

      // The search in the URL (`B-95`), so it can be shared and Back returns to
      // it. A new question is a new entry; the same one re-filtered replaces it,
      // or every filter toggled would be a step of Back.
      if (history === 'push') {
        const href = findLink(trimmed, within, view)
        const current = `${window.location.pathname}${window.location.search}`
        if (href !== current) {
          const same = readFind(window.location.search).q === trimmed
          window.history[same ? 'replaceState' : 'pushState']({}, '', href)
        }
      }

      inFlight.current?.abort()
      const controller = new AbortController()
      inFlight.current = controller

      setPhase('searching')
      setMore({ phase: 'idle' })
      setMode(view ?? initialMode(trimmed))
      setAsked(trimmed)
      setPicked(null)
      setError(null)

      searchCorpus({ q: trimmed, ...searchFilters(within) }, { signal: controller.signal })
        .then((response) => {
          setResults(response)
          setPhase('done')
        })
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === 'AbortError') return
          setError(cause instanceof ApiError ? cause.message : 'The search could not be completed.')
          setPhase('failed')
        })
    },
    [],
  )

  // `/?q=…` opens with that search run, narrowed by whatever filters the link names.
  // See docs/features/web-app.md#explore-and-find.
  useEffect(() => {
    const linked = readFind(window.location.search)
    setFilters(linked.filters)
    if (!linked.q) {
      if (activeCount(linked.filters) > 0) focusSearch()
      return
    }
    setQuery(linked.q)
    run(linked.q, linked.filters, 'keep', linked.view ?? undefined)
  }, [run])

  // Back and Forward between searches run the search the URL now names.
  useEffect(() => {
    const onPop = () => {
      if (window.location.pathname !== '/') return
      const linked = readFind(window.location.search)
      setFilters(linked.filters)
      if (linked.q) {
        setQuery(linked.q)
        run(linked.q, linked.filters, 'keep', linked.view ?? undefined)
      } else {
        inFlight.current?.abort()
        setQuery('')
        setAsked('')
        setResults(null)
        setPhase('idle')
      }
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [run])

  /** The next page of the same search, appended (`B-174`). */
  function loadMore() {
    if (!results || !results.has_more || more.phase === 'loading') return
    const offset = results.hits.length
    const controller = new AbortController()
    inFlight.current = controller
    setMore({ phase: 'loading' })
    searchCorpus(
      // Never past the pool the ranking saw: the route refuses a window beyond it.
      { q: asked, ...searchFilters(filters), offset, limit: Math.min(PAGE, results.candidate_pool - offset) },
      { signal: controller.signal },
    )
      .then((page) => {
        // Keyed by passage: a ranking that shifted between requests must not show one twice.
        const seen = new Set(results.hits.map((hit) => hit.chunk_id))
        setResults({ ...page, hits: [...results.hits, ...page.hits.filter((hit) => !seen.has(hit.chunk_id))] })
        setMore({ phase: 'idle' })
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setMore({
          phase: 'failed',
          message: cause instanceof ApiError ? cause.message : 'The next passages could not be read.',
        })
      })
  }

  /**
   * One country from the answer, as passages (`B-175`): the same words, narrowed to it. A new
   * history entry, so Back returns to the answer it came from.
   */
  function readPlace(code: string) {
    const next = { ...filters, places: [code] }
    setFilters(next)
    modeChosen.current = true
    window.history.pushState({}, '', findLink(asked, next, 'passages'))
    run(asked, next, 'keep', 'passages')
  }

  /** A filter changed: kept for the next search, and the current one re-run with it. */
  function narrow(next: FindFilters) {
    setFilters(next)
    if (asked) run(asked, next, 'push', modeChosen.current ? mode : undefined)
    else window.history.replaceState({}, '', findLink('', next))
  }

  function openView(view: SavedViewRecord) {
    // Recorded as a write, and failing to record it must not stop the view from
    // opening: the ordering of a list is not worth refusing somebody the thing they clicked.
    void markViewOpened(view.view_id).catch(() => {})
    const saved = filtersOfView(view.filters)
    setFilters(saved)
    setQuery(view.query ?? '')
    if (view.query) run(view.query, saved)
  }

  function clear() {
    // Back to the overview keeps the filters: they are set up for the next search.
    const href = findLink('', filters)
    if (`${window.location.pathname}${window.location.search}` !== href) window.history.pushState({}, '', href)
    inFlight.current?.abort()
    setQuery('')
    setAsked('')
    setPicked(null)
    setResults(null)
    setError(null)
    setPhase('idle')
  }

  const idle = phase === 'idle'
  const { unavailable, descriptions } = entryState(stats)

  const field = (
    <SearchField
      value={query}
      onChange={setQuery}
      onSubmit={(text) => {
        modeChosen.current = false
        run(text, filters)
      }}
      size={idle ? 'large' : 'regular'}
    />
  )

  const rail = (
    <FindRail
      topics={stats?.topics ?? []}
      places={stats?.places ?? []}
      filters={filters}
      onChange={narrow}
      unexaminedTopics={(stats?.sources_without_topics ?? 0) > 0}
      unexaminedPlaces={(stats?.sources_without_places ?? 0) > 0}
      views={views}
      onOpenView={openView}
    />
  )
  const placeNames = Object.fromEntries((stats?.places ?? []).map((p) => [p.code, p.name]))

  if (!idle) {
    const shown = new Set(results?.hits.map((hit) => hit.chunk_id) ?? [])
    return (
      <div className="mx-auto w-full max-w-[1440px] px-4 pb-24 pt-8">
        {/* The rail beside everything from `lg` (design `Explore`); below it, folded
            behind a button so the results stay the first thing on a phone. The field
            comes first in the document, so it is first in tab order too. */}
        <div className="grid grid-cols-1 items-start gap-x-8 gap-y-6 lg:grid-cols-[220px_minmax(0,1fr)]">
          <div className="flex max-w-[912px] flex-col gap-3 lg:col-start-2 lg:row-start-1">{field}</div>

          <aside
            id="find-rail"
            className={`${railOpen ? 'block' : 'hidden'} border border-line bg-surface p-4 lg:sticky lg:top-[70px] lg:block lg:border-0 lg:bg-transparent lg:p-0 lg:pt-1 lg:max-h-[calc(100vh-90px)] lg:overflow-y-auto lg:col-start-1 lg:row-span-2 lg:row-start-1`}
          >
            {rail}
          </aside>

          {/* Results and the neighbourhood side by side (`P6-33`); stacked,
              results first, below the width where both fit. The panel appears
              once it has something to show; while it loads, or when it holds
              nothing, the results keep the width. */}
          <div
            className={`grid grid-cols-1 items-start gap-6 lg:col-start-2 lg:row-start-2 ${
              showHood ? 'xl:grid-cols-[minmax(0,1fr)_360px]' : 'max-w-[1100px]'
            }`}
          >
            <section aria-live="polite">
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <ModeSwitch
                  mode={mode}
                  onChange={(next) => {
                    setMode(next)
                    rememberMode(next)
                    modeChosen.current = true
                    window.history.replaceState({}, '', findLink(asked, filters, next))
                  }}
                />
                <FiltersButton open={railOpen} count={activeCount(filters)} onClick={() => setRailOpen(!railOpen)} />
              </div>

              {mode === 'answer' && asked ? (
                <AnswerView question={asked} filters={filters} onReadPlace={readPlace} />
              ) : null}

              {mode === 'passages' && phase === 'searching' ? (
                <p className="font-mono text-[10.5px] text-text-faint">Searching.</p>
              ) : null}

              {mode === 'passages' && phase === 'failed' && error ? (
                // §4: an error names the cause and the scope. The API's own message
                // is the most specific thing available, so it is shown rather than
                // replaced with a generic line.
                <p className="border border-line-strong bg-surface p-4 text-[13px] text-text">{error}</p>
              ) : null}

              {mode === 'passages' && phase === 'done' && results ? (
                <SearchOutcome
                  asked={asked}
                  results={results}
                  footer={<MorePassages results={results} state={more} onMore={loadMore} />}
                  aside={
                    <SaveView
                      key={savedCount}
                      query={asked}
                      filters={viewFiltersOf(filters)}
                      busy={saving}
                      error={saveError}
                      onSave={(name, text, stored) => {
                        setSaving(true)
                        setSaveError(null)
                        saveView({ name, query: text, filters: stored })
                          .then((view) => {
                            setViews((current) => [view, ...current])
                            // Remounted closed: the view is in the rail now, which says it took.
                            setSavedCount((n) => n + 1)
                          })
                          .catch((cause: unknown) => {
                            setSaveError(cause instanceof ApiError ? cause.message : 'That view was not saved.')
                          })
                          .finally(() => setSaving(false))
                      }}
                    />
                  }
                />
              ) : null}
            </section>
            {showHood && hood ? (
              <NeighbourhoodPanel state={hood} shownChunkIds={shown} onPick={(term) => setPicked(term.entity_id)} />
            ) : null}
          </div>
        </div>

        <p className="mt-8 lg:pl-[252px]">
          <button type="button" onClick={clear} className="font-mono text-[10.5px] text-accent-graph hover:underline">
            ← back to the overview
          </button>
        </p>
      </div>
    )
  }

  return (
    <>
      <Geometry />
      <div className="relative mx-auto flex w-full max-w-[812px] flex-col gap-[46px] px-4 pb-24 pt-12 sm:pt-24">
        <div className="flex flex-col items-center gap-[26px]">
          <Lockup size={34} wordmarkSize={26} gap={14} />
          <div className="flex w-full flex-col gap-3">
            {field}
            <ActiveFilters filters={filters} placeNames={placeNames} onClear={() => narrow(NO_FILTERS)} />
          </div>
        </div>

        <div className="flex flex-col gap-3">
          <CorpusCounts counts={stats ? figuresFrom(stats) : null} />
          {statsError ? <p className="font-mono text-[10.5px] text-text-muted">{statsError}</p> : null}
        </div>

        {empty ? (
          progress ? (
            <FirstHour progress={progress} />
          ) : (
            <p className="text-[13px] text-text-muted">
              Nothing to search yet, and the crawl's progress could not be read.
            </p>
          )
        ) : (
          <EntryPoints
            actions={{
              search: () => focusSearch(),
              coverage: () => navigate('/gaps'),
              contested: () => navigate('/contested'),
            }}
            unavailable={unavailable}
            descriptions={descriptions}
          />
        )}

        <WhereYouWere
          delta={
            stats ? <SinceLastVisit newSources={stats.new_sources} newChunks={stats.new_chunks} since={since} /> : null
          }
          savedViews={views.slice(0, VIEWS_ON_LANDING).map((view) => ({
            id: String(view.view_id),
            name: view.name,
            at: view.last_opened_at ?? view.created_at,
            fresh: view.new_since,
          }))}
          recentNodes={[]}
          onOpenView={(id) => {
            const view = views.find((v) => String(v.view_id) === id)
            if (view) openView(view)
          }}
        />

        <NotesPanel notes={notes} total={noteCount} />
      </div>
    </>
  )
}

/**
 * The landing's background: the mark's circle-and-meridian, scaled up, cropped and fixed
 * (§8). Decoration only: hidden from assistive technology and never corpus data.
 */
export function Geometry() {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      data-role="geometry"
      viewBox="0 0 1440 900"
      preserveAspectRatio="xMidYMin slice"
      fill="none"
      className="pointer-events-none fixed inset-0 h-full w-full text-line"
    >
      <g stroke="currentColor" strokeWidth="1.2" opacity="0.55">
        <circle cx="720" cy="1230" r="700" />
        <ellipse cx="720" cy="1230" rx="258" ry="700" />
        <path d="M720 530 L253 686 M720 530 L1187 686" strokeWidth="1" />
      </g>
      <g stroke="currentColor" strokeWidth="1.2" opacity="0.4">
        <circle cx="188" cy="-190" r="420" />
        <ellipse cx="188" cy="-190" rx="155" ry="420" />
      </g>
      <g fill="currentColor" opacity="0.7">
        <circle cx="720" cy="530" r="7" />
        <circle cx="253" cy="686" r="5" />
        <circle cx="1187" cy="686" r="5" />
        <circle cx="188" cy="230" r="5" opacity="0.8" />
      </g>
    </svg>
  )
}

/**
 * Answer or passages. Two tabs over the same search: the answer groups the
 * evidence by country and states its coverage; passages is the ranked list.
 */
export function ModeSwitch({ mode, onChange }: { mode: ResultMode; onChange: (mode: ResultMode) => void }) {
  const tabs: ReadonlyArray<[ResultMode, string]> = [
    ['answer', 'Answer'],
    ['passages', 'Passages'],
  ]
  return (
    <div role="tablist" aria-label="Show results as" className="mb-4 inline-flex border border-line">
      {tabs.map(([value, label]) => (
        <button
          key={value}
          type="button"
          role="tab"
          aria-selected={mode === value}
          onClick={() => onChange(value)}
          className={`px-3 py-[5px] font-mono text-[11px] ${
            mode === value ? 'bg-accent-graph/10 text-accent-graph' : 'text-text-faint hover:text-text-muted'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

/**
 * What the search found, per arm, in a reader's words (`P6-44`): "lexical" and
 * "vector" are how it was done, not what it means. Counted per arm, so a
 * search that only one arm answered does not read as "20 of 0". An arm stops at the
 * candidate pool, so a count that reached it is "at least", written `100+`.
 */
export function summaryLine(results: SearchResponse): string {
  const shown = results.hits.length
  const count = (n: number) => `${n.toLocaleString('en')}${n >= results.candidate_pool ? '+' : ''}`
  const parts = [`${shown} ${shown === 1 ? 'passage' : 'passages'} shown`]
  if (results.arms.includes('lexical')) parts.push(`${count(results.lexical_candidates)} matched the words`)
  if (results.arms.includes('vector')) parts.push(`${count(results.vector_candidates)} near in meaning`)
  return parts.join(' · ')
}

export function SearchOutcome({
  asked,
  results,
  aside,
  footer,
}: {
  asked: string
  results: SearchResponse
  /** Controls set at the right of the summary line — saving the view. */
  aside?: React.ReactNode
  /** Below the list: the way to the next page. */
  footer?: React.ReactNode
}) {
  const empty = results.hits.length === 0

  return (
    <>
      {results.degraded && results.degraded_reason ? (
        <RetrievalNotice reason={results.degraded_reason} empty={empty} />
      ) : null}

      {empty ? (
        <p className="text-[13.5px] text-text-muted">Nothing matched {`"${asked}"`}.</p>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
            <p className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
              {summaryLine(results)}
            </p>
            {aside}
          </div>
          <ResultList hits={results.hits} />
          {footer}
        </>
      )}
    </>
  )
}

/**
 * The foot of the passage list (`B-174`): more while the ranking has more, and, once a
 * reader has paged to its end, a line saying the end is the ranking's, not the corpus's.
 */
export function MorePassages({
  results,
  state,
  onMore,
}: {
  results: SearchResponse
  state: { phase: 'idle' | 'loading' } | { phase: 'failed'; message: string }
  onMore: () => void
}) {
  if (results.hits.length === 0) return null
  if (!results.has_more) {
    // Only said after paging: on a first page that ends early, the summary line already
    // counts what matched.
    return results.hits.length > PAGE ? (
      <p className="mt-3 font-mono text-[10.5px] leading-[1.5] text-text-faint">
        The end of what this search ranked. Other wording, or narrower filters, reach passages it did not.
      </p>
    ) : null
  }
  return (
    <div className="mt-3 flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={onMore}
        disabled={state.phase === 'loading'}
        className="h-8 border border-line-strong bg-surface-raised px-3 font-sans text-[12.5px] text-text/85 hover:text-text disabled:opacity-50"
      >
        {state.phase === 'loading' ? 'Reading…' : 'More passages'}
      </button>
      {state.phase === 'failed' ? (
        <span className="font-mono text-[10.5px] text-text-muted">{state.message}</span>
      ) : null}
    </div>
  )
}

/**
 * What the search did not do: bordered and at full ink when the result set is empty,
 * a mono caveat line above the hits otherwise.
 */
export function RetrievalNotice({ reason, empty }: { reason: string; empty: boolean }) {
  return (
    <div
      data-weight={empty ? 'notice' : 'caveat'}
      className={
        empty
          ? 'mb-4 border border-line-strong bg-surface p-4 text-[13px] leading-[1.55] text-text'
          : 'mb-3 font-mono text-[10.5px] leading-[1.5] text-text-faint'
      }
    >
      <p>{reason}</p>
      {empty ? (
        <p className="mt-2 text-text-muted">
          Passages about this topic that use different wording were not searched. An empty result here is not evidence
          that the corpus lacks the subject.
        </p>
      ) : null}
    </div>
  )
}

/**
 * The filters a search will be narrowed by, on the landing, where the rail is not shown:
 * one quiet line, and only when a link or a saved view set some (`B-173`).
 */
export function ActiveFilters({
  filters,
  placeNames,
  onClear,
}: {
  filters: FindFilters
  placeNames: Readonly<Record<string, string>>
  onClear: () => void
}) {
  if (activeCount(filters) === 0) return null
  const parts = [
    ...filters.topics.map(topicWords),
    ...filters.places.map((code) => placeNames[code] ?? code),
    ...filters.tiers.map((tier) => TIER_LABEL[tier].toLowerCase()),
  ]
  if (filters.from !== null || filters.to !== null) {
    parts.push(`published ${filters.from ?? '…'}–${filters.to ?? '…'}`)
  }
  const joiner = filters.match === 'all' && filters.topics.length > 1 ? ' + ' : ' · '
  return (
    <p className="flex flex-wrap items-baseline gap-x-3 font-mono text-[10.5px] text-text-muted">
      <span>Searching within: {parts.join(joiner)}</span>
      <button type="button" onClick={onClear} className="text-accent-graph hover:underline">
        clear
      </button>
    </p>
  )
}
