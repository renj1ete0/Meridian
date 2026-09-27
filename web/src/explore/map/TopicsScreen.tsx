import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { ApiError, getTopicOverlaps, type TopicOverlaps } from '../../lib/api'
import { navigate } from '../../lib/route'
import { topicLabel } from './HoverCard'
import {
  carrying,
  combinationsContaining,
  layoutTopics,
  linkKey,
  linkWidth,
  rimToRim,
  searchHref,
  selectLink,
  toggleTopic,
  topicLinks,
  topicTotals,
  type TopicLink,
} from '../../lib/topicweb'

/**
 * The Map's Topics view (task B-72): the web of topics.
 *
 * Topics are multi-label — a source carries every topic its content is about —
 * so the corpus is a web rather than a partition. A circle per topic, its area
 * proportional to the sources carrying it; a line between two topics wherever
 * sources carry both, thicker for more. Choosing circles (or a line, which
 * chooses its two ends) answers "how many sources lie in all of these", and
 * hands that intersection to Find as a search with `topic_match=all`.
 *
 * The readout lists exact combinations as bars rather than drawing a Venn
 * diagram: past three sets a Venn cannot be drawn with honest areas, and a bar
 * per combination reads the same at two topics as at six.
 */

type Load = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: TopicOverlaps }

export function TopicsScreen() {
  const [load, setLoad] = useState<Load>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    getTopicOverlaps({ signal: controller.signal })
      .then((data) => setLoad({ status: 'ready', data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setLoad({
          status: 'error',
          message: error instanceof ApiError ? error.message : 'The topic overlaps did not load.',
        })
      })
    return () => controller.abort()
  }, [])

  if (load.status === 'error') return <p className="p-6 text-text">{load.message}</p>
  if (load.status === 'loading') {
    return <p className="p-6 font-mono text-[length:var(--text-data)] text-text-muted">Counting where topics meet.</p>
  }
  return <TopicsView data={load.data} />
}

const fmt = (n: number) => n.toLocaleString('en')

/** Everything but the fetch, so a test renders exactly what the page does. */
export function TopicsView({
  data,
  initial = [],
  onSearch = navigate,
}: {
  data: TopicOverlaps
  initial?: string[]
  /** Where "Search where these meet" goes; the router, unless a test says otherwise. */
  onSearch?: (href: string) => void
}) {
  const [selection, setSelection] = useState<string[]>(initial)
  const [query, setQuery] = useState('')

  const totals = useMemo(() => topicTotals(data.overlaps), [data])
  const links = useMemo(() => topicLinks(data.overlaps), [data])
  const count = carrying(data, selection)
  const combos = useMemo(() => combinationsContaining(data.overlaps, selection, Infinity), [data, selection])

  if (totals.length === 0) {
    return (
      <div className="p-6">
        <p className="max-w-[60ch] text-[length:var(--text-body)] text-text">
          No source carries a topic yet, so there is no web to draw.
        </p>
        <p className="mt-2 max-w-[60ch] text-[length:var(--text-small)] text-text-muted">
          Sources are labelled by content as they are read; this view fills in once some are.
        </p>
      </div>
    )
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="grid grid-cols-1 lg:min-h-full lg:grid-cols-[minmax(0,1fr)_380px]">
        <section aria-label="Topic web" className="min-w-0 bg-ground px-4 py-4 sm:px-6">
          <p className="max-w-[70ch] text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
            Circle area is the sources carrying a topic; a line joins topics that sources carry together, thicker
            for more. Choose circles, or a line, to see what lies in all of them.
          </p>
          <Web
            totals={totals}
            links={links}
            selection={selection}
            onToggle={(topic) => setSelection((s) => toggleTopic(s, topic))}
            onLink={(link) => setSelection(selectLink(link))}
          />
        </section>

        <aside
          aria-label="Where the selection meets"
          className="flex min-w-0 flex-col border-t border-line bg-surface lg:border-l lg:border-t-0"
        >
          <Readout
            data={data}
            selection={selection}
            count={count}
            query={query}
            onQuery={setQuery}
            onRemove={(topic) => setSelection((s) => s.filter((t) => t !== topic))}
            onClear={() => setSelection([])}
            onSearch={() => onSearch(searchHref(selection, query))}
          />
          <Breakdown
            combos={combos}
            selection={selection}
            count={count}
            onPick={(topics) => setSelection([...topics])}
          />
        </aside>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------------
// The web

/** The drawing's width follows its box, so labels stay at reading size on a phone. */
function useWidth(fallback: number) {
  const ref = useRef<HTMLDivElement | null>(null)
  const [width, setWidth] = useState(fallback)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const measure = () => {
      const w = element.getBoundingClientRect().width
      if (w > 0) setWidth(w)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

function activate(handler: () => void) {
  return (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      handler()
    }
  }
}

export function Web({
  totals,
  links,
  selection,
  onToggle,
  onLink,
}: {
  totals: ReturnType<typeof topicTotals>
  links: TopicLink[]
  selection: readonly string[]
  onToggle: (topic: string) => void
  onLink: (link: TopicLink) => void
}) {
  const [ref, width] = useWidth(640)
  // Square-ish on a phone, where names go under their circles; wider than
  // tall on a desk, where they sit beside them.
  const narrow = width < 520
  const height = Math.round(narrow ? width * 1.3 : Math.min(Math.max(width * 0.62, 380), 600))
  const placed = useMemo(
    () => layoutTopics(totals, width, height, { minR: narrow ? 9 : 14, below: narrow }),
    [totals, width, height],
  )
  const at = new Map(placed.map((p) => [p.topic, p]))
  const largest = links[0]?.shared ?? 0
  const chosen = new Set(selection)
  const any = chosen.size > 0

  // Thin lines last, so a thick one never hides a thin one's hit area.
  const drawn = [...links].reverse()

  return (
    <div ref={ref} className="mt-3 w-full">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="group"
        aria-label="Topics and the sources they share"
        className="block max-w-full select-none"
      >
        <g data-role="links">
          {drawn.map((link) => {
            const a = at.get(link.a)
            const b = at.get(link.b)
            if (!a || !b) return null
            const on = chosen.has(link.a) && chosen.has(link.b)
            const w = linkWidth(link.shared, largest)
            const ends = rimToRim(a, b)
            const label = `${topicLabel(link.a)} and ${topicLabel(link.b)}: ${fmt(link.shared)} ${link.shared === 1 ? 'source carries' : 'sources carry'} both`
            return (
              <g
                key={linkKey(link)}
                role="button"
                tabIndex={0}
                aria-pressed={on}
                aria-label={label}
                data-link={linkKey(link)}
                onClick={() => onLink(link)}
                onKeyDown={activate(() => onLink(link))}
                className="group cursor-pointer outline-none"
              >
                <title>{label}</title>
                <line
                  {...ends}
                  strokeLinecap="round"
                  strokeWidth={w}
                  className={
                    on
                      ? 'stroke-accent-graph'
                      : `stroke-line-strong group-hover:stroke-text-faint group-focus-visible:stroke-text-faint ${any ? 'opacity-45' : 'opacity-80'}`
                  }
                />
                {/* The hit area: a line two pixels wide is not something a finger can press. */}
                <line
                  {...ends}
                  strokeWidth={Math.max(16, w + 10)}
                  stroke="transparent"
                  strokeLinecap="round"
                />
              </g>
            )
          })}
        </g>

        <g data-role="topics">
          {placed.map((p) => {
            const on = chosen.has(p.topic)
            const label = `${topicLabel(p.topic)}: ${fmt(p.sources)} ${p.sources === 1 ? 'source' : 'sources'}`
            return (
              <g
                key={p.topic}
                role="button"
                tabIndex={0}
                aria-pressed={on}
                aria-label={label}
                data-topic={p.topic}
                onClick={() => onToggle(p.topic)}
                onKeyDown={activate(() => onToggle(p.topic))}
                className="group cursor-pointer outline-none"
              >
                <title>{label}</title>
                <circle
                  cx={p.x}
                  cy={p.y}
                  r={p.r}
                  strokeWidth={on ? 2 : 1.2}
                  className={
                    on
                      ? 'fill-accent-graph/25 stroke-accent-graph'
                      : 'fill-surface-raised stroke-line-strong group-hover:stroke-text-faint group-focus-visible:stroke-text'
                  }
                />
                <text
                  x={p.label.x}
                  y={p.label.y}
                  textAnchor={p.label.anchor}
                  className={`font-sans text-[12.5px] ${on ? 'fill-text' : 'fill-text-muted'}`}
                  style={{ paintOrder: 'stroke', stroke: 'var(--ground)', strokeWidth: 3.5 }}
                >
                  {topicLabel(p.topic)}
                </text>
                <text
                  x={p.label.x}
                  y={p.label.y + 14}
                  textAnchor={p.label.anchor}
                  className="fill-text-faint font-mono text-[10.5px]"
                  style={{ paintOrder: 'stroke', stroke: 'var(--ground)', strokeWidth: 3.5 }}
                >
                  {fmt(p.sources)}
                </text>
              </g>
            )
          })}
        </g>
      </svg>
    </div>
  )
}

// --------------------------------------------------------------------------
// The readout

const LABEL = 'font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint'

/** "N sources carry all of: A, B" — or, with nothing chosen, what the whole is. */
export function readoutLine(count: number, selection: readonly string[]): string {
  if (selection.length === 0) return `${count === 1 ? 'labelled source' : 'labelled sources'}`
  const noun = count === 1 ? 'source carries' : 'sources carry'
  if (selection.length === 1) return `${noun} ${selection[0]}`
  return `${noun} all of: ${selection.join(', ')}`
}

function Readout({
  data,
  selection,
  count,
  query,
  onQuery,
  onRemove,
  onClear,
  onSearch,
}: {
  data: TopicOverlaps
  selection: readonly string[]
  count: number
  query: string
  onQuery: (q: string) => void
  onRemove: (topic: string) => void
  onClear: () => void
  onSearch: () => void
}) {
  const none = selection.length > 0 && count === 0
  return (
    <section className="flex flex-col gap-3 border-b border-line px-5 py-4" aria-live="polite">
      <div className="flex items-center justify-between gap-3">
        <h2 className={LABEL}>Selection</h2>
        {selection.length > 0 ? (
          <button type="button" onClick={onClear} className="font-mono text-[10.5px] text-accent-graph hover:underline">
            clear
          </button>
        ) : null}
      </div>

      {selection.length > 0 ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Chosen topics">
          {selection.map((topic) => (
            <li key={topic}>
              <button
                type="button"
                onClick={() => onRemove(topic)}
                aria-label={`Remove ${topicLabel(topic)}`}
                className="rounded-[var(--radius-chip)] border border-accent-graph/70 bg-accent-graph/10 px-2 py-[3px] font-mono text-[10.5px] leading-[1.4] text-accent-graph hover:bg-accent-graph/20"
              >
                {topicLabel(topic)} <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <p data-role="readout" className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono text-[29px] leading-[1.15] text-text">{fmt(count)}</span>
        {' '}
        <span className="text-[length:var(--text-body)] text-text">{readoutLine(count, selection)}</span>
      </p>

      {selection.length === 0 ? (
        <p className="text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
          Choose circles, or the line between two, to see how many sources lie in all of them.
        </p>
      ) : none ? (
        <p className="text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
          No source is labelled with all of these. A passage can still carry a topic its document does not, so a
          search here may find some.
        </p>
      ) : (
        <p className="text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
          {data.labelled_sources > 0
            ? `${Math.round((count / data.labelled_sources) * 1000) / 10}% of ${fmt(data.labelled_sources)} labelled sources.`
            : null}
        </p>
      )}

      <form
        className="flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault()
          if (selection.length > 0) onSearch()
        }}
      >
        <label className="flex flex-col gap-1.5">
          <span className={LABEL}>Search for (optional)</span>
          <input
            type="search"
            value={query}
            onChange={(event) => onQuery(event.target.value)}
            placeholder="words to look for inside the selection"
            className="h-9 w-full border border-line bg-surface-raised px-3 text-[13px] text-text placeholder:text-text-faint focus:border-line-strong"
          />
        </label>
        <button
          type="submit"
          disabled={selection.length === 0}
          className="h-9 bg-accent-graph px-4 text-[13px] font-semibold text-ground-deep hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Search where these meet
        </button>
        <p className="font-mono text-[10px] leading-[1.5] text-text-faint">
          Opens Find narrowed to every chosen topic at once. Counts here are document labels; the search also
          matches a passage by its own labels.
        </p>
      </form>
    </section>
  )
}

/**
 * The exact combinations that make up the count, largest first — an
 * UpSet-style bar list. Chosen topics in full ink, the rest muted, so a row
 * reads as "these, plus what else they come with".
 */
const SHOWN = 8

function Breakdown({
  combos,
  selection,
  count,
  onPick,
}: {
  combos: ReturnType<typeof combinationsContaining>
  selection: readonly string[]
  count: number
  onPick: (topics: readonly string[]) => void
}) {
  if (combos.length === 0) return null
  const all = combos
  combos = combos.slice(0, SHOWN)
  const widest = Math.max(...combos.map((c) => c.sources))
  const chosen = new Set(selection)
  return (
    <section className="flex flex-col gap-3 px-5 py-4">
      <h2 className={LABEL}>{selection.length === 0 ? 'Largest combinations' : 'Where they sit'}</h2>
      <ul className="flex flex-col gap-2.5" aria-label="Exact topic combinations">
        {combos.map((combo) => {
          const key = combo.topics.join('\u0000')
          const exact = combo.topics.length === selection.length
          return (
            <li key={key}>
              <button
                type="button"
                onClick={() => onPick(combo.topics)}
                title="Choose exactly this combination"
                className="flex w-full flex-col gap-1 text-left hover:bg-surface-raised/60"
              >
                <span className="flex w-full items-baseline justify-between gap-3">
                  <span className="min-w-0 text-[12.5px] leading-[1.45]">
                    {combo.topics.map((topic, i) => (
                      <span key={topic}>
                        {i > 0 ? <span className="text-text-faint"> + </span> : null}
                        <span className={chosen.has(topic) ? 'text-text' : 'text-text-muted'}>{topicLabel(topic)}</span>
                      </span>
                    ))}
                    {exact && selection.length > 0 ? (
                      <span className="font-mono text-[10px] text-text-faint"> · only these</span>
                    ) : null}
                  </span>
                  <span className="shrink-0 font-mono text-[11px] text-text">{fmt(combo.sources)}</span>
                </span>
                <span className="block h-1.5 w-full bg-surface-raised">
                  <span
                    className="block h-full bg-accent-graph"
                    style={{ width: `${(combo.sources / widest) * 100}%` }}
                  />
                </span>
              </button>
            </li>
          )
        })}
      </ul>
      {selection.length > 0 && count > 0 ? (
        <p className="font-mono text-[10px] leading-[1.5] text-text-faint">
          Each source is counted once, under its exact set of topics
          {all.length > combos.length
            ? `; the ${fmt(all.length)} combinations add up to ${fmt(count)}, and the largest ${combos.length} are shown.`
            : `, so these rows add up to ${fmt(count)}.`}
        </p>
      ) : null}
    </section>
  )
}
