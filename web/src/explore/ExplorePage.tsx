import { useCallback, useEffect, useRef, useState } from 'react'

import { openSession } from '../lib/lastVisit'
import { CorpusCounts, type CorpusFigures } from './CorpusCounts'
import { EntryPoints, type EntryPointName } from './EntryPoints'
import { ResultList } from './ResultList'
import { SearchField } from './SearchField'
import { SinceLastVisit } from './SinceLastVisit'
import { WhereYouWere } from './WhereYouWere'
import {
  ApiError,
  corpusStats,
  searchCorpus,
  type CorpusStats,
  type SearchResponse,
} from '../lib/api'

/**
 * Explore, wired to the read surface (task P2-08, spec §12.5, design §8).
 *
 * **The degraded flag is rendered, not logged.** `P2-07` has no embedder, so
 * only the lexical arm runs, and every response says so. A reader who searches
 * a topic, sees nothing, and is not told that meaning-based matching was off
 * will conclude the corpus lacks the topic — which is false, costly, and
 * exactly the confusion §12.5 exists to prevent: it asks the interface to make
 * *absence* visible, and "we did not look properly" is a different absence from
 * "it is not here".
 *
 * That is why the notice is loudest when there are no hits. A degraded search
 * that returned results is a caveat; a degraded search that returned nothing is
 * a claim about the corpus that the search is not entitled to make.
 *
 * **Searching happens on submit.** Not per keystroke: each query is a fused
 * ranking over two arms and a candidate pool, and firing one per character
 * spends the Pi's budget on queries nobody finished typing. A superseded
 * request is aborted rather than left to land out of order.
 */

/** §12.5's counts, from what `/stats` actually returns. */
function figuresFrom(stats: CorpusStats): CorpusFigures {
  return {
    // Documents, not chunks. A reader asking how much is in here means sources;
    // chunk count is an artefact of how they were cut up.
    documents: stats.sources,
    nodes: stats.entities,
    edges: stats.edges,
    contested: stats.contested_edges,
  }
}

type Phase = 'idle' | 'searching' | 'done' | 'failed'

export function ExplorePage() {
  const [query, setQuery] = useState('')
  const [asked, setAsked] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [results, setResults] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const [stats, setStats] = useState<CorpusStats | null>(null)
  const [statsError, setStatsError] = useState<string | null>(null)

  const inFlight = useRef<AbortController | null>(null)

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

  const run = useCallback((text: string) => {
    const trimmed = text.trim()
    if (!trimmed) return

    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller

    setPhase('searching')
    setAsked(trimmed)
    setError(null)

    searchCorpus({ q: trimmed }, { signal: controller.signal })
      .then((response) => {
        setResults(response)
        setPhase('done')
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setError(cause instanceof ApiError ? cause.message : 'The search could not be completed.')
        setPhase('failed')
      })
  }, [])

  const unavailable: Partial<Record<EntryPointName, string>> = {
    coverage: 'Coverage scoring is not built yet.',
    // Derived rather than hardcoded: once edges exist this stops claiming they
    // do not, without anyone remembering to change it.
    ...(stats && stats.edges === 0
      ? { contested: 'No edges yet, so no source disagrees with another.' }
      : {}),
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <SearchField value={query} onChange={setQuery} onSubmit={run} />

      <div className="mt-12">
        <CorpusCounts counts={stats ? figuresFrom(stats) : null} />
        {stats ? (
          <div className="mt-3 text-center">
            <SinceLastVisit newSources={stats.new_sources} newChunks={stats.new_chunks} />
          </div>
        ) : null}
        {statsError ? (
          <p className="mt-3 text-center text-[length:var(--text-small)] text-accent-attention">
            {statsError}
          </p>
        ) : null}
      </div>

      <section className="mt-12" aria-live="polite">
        {phase === 'searching' ? (
          <p className="text-[length:var(--text-small)] text-text-muted">Searching.</p>
        ) : null}

        {phase === 'failed' && error ? (
          // §4: an error names the cause and the scope. The API's own message
          // is the most specific thing available, so it is shown rather than
          // replaced with a generic line.
          <p className="border border-accent-attention bg-surface p-4 text-[length:var(--text-small)] text-accent-attention">
            {error}
          </p>
        ) : null}

        {phase === 'done' && results ? (
          <SearchOutcome asked={asked} results={results} />
        ) : null}
      </section>

      {phase === 'idle' ? (
        <>
          <div className="mt-12">
            <EntryPoints onOpen={() => {}} unavailable={unavailable} />
          </div>
          <div className="mt-12">
            <WhereYouWere savedViews={[]} recentNodes={[]} />
          </div>
        </>
      ) : null}
    </div>
  )
}

export function SearchOutcome({ asked, results }: { asked: string; results: SearchResponse }) {
  const empty = results.hits.length === 0

  return (
    <>
      {results.degraded && results.degraded_reason ? (
        <RetrievalNotice reason={results.degraded_reason} empty={empty} />
      ) : null}

      {empty ? (
        <p className="text-[length:var(--text-small)] text-text-muted">
          Nothing matched {`"${asked}"`}.
        </p>
      ) : (
        <>
          <p className="mb-4 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
            {results.hits.length} of {results.lexical_candidates} candidates
          </p>
          <ResultList hits={results.hits} />
        </>
      )}
    </>
  )
}

/**
 * What the search did not do.
 *
 * Bordered and at attention weight when the result set is empty, because that
 * is the case where silence becomes a false claim about the corpus. With hits
 * on screen it is a caveat and reads as one.
 */
export function RetrievalNotice({ reason, empty }: { reason: string; empty: boolean }) {
  return (
    <div
      className={
        empty
          ? 'mb-4 border border-accent-attention bg-surface p-4 text-[length:var(--text-small)] text-accent-attention'
          : 'mb-4 text-[length:var(--text-small)] text-text-muted'
      }
    >
      <p>{reason}</p>
      {empty ? (
        <p className="mt-2">
          Passages about this topic that use different wording were not searched. An empty result
          here is not evidence that the corpus lacks the subject.
        </p>
      ) : null}
    </div>
  )
}
