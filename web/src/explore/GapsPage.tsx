import { useEffect, useState } from 'react'

import { BUTTON_PRIMARY, BUTTON_SECONDARY, Card, FIELD, Filters, LABEL, Loading, PageHeader, ROW } from '../admin/ui'
import { ApiError } from '../lib/api'
import {
  boostFromGap,
  evidenceLine,
  findHref,
  getGaps,
  groupOf,
  mapHrefOf,
  seedFromGap,
  type Gap,
  type GapAction,
  type GapGroup,
  type GapActionResult,
  type Gaps,
  type GapSource,
} from '../lib/gaps'
import { onInternalClick } from '../lib/route'
import { stampOf, zoneLabel } from '../lib/time'

/**
 * Gaps (task P6-36): one ranked list of what the corpus cannot yet answer, each row a
 * finding in numbers with a logged, reversible action. See docs/features/web-app.md#gaps
 * and docs/features/gaps.md.
 */

type Load = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; body: Gaps }

/** Severity at or above this is flagged in brass with the dagger (§6). */
export const FLAG_AT = 0.6

const GROUPS: readonly { key: GapGroup | null; label: string }[] = [
  { key: null, label: 'All' },
  { key: 'coverage', label: 'Coverage' },
  { key: 'search', label: 'Searches' },
  { key: 'questions', label: 'Question set' },
]

/** The group a link names (`?kind=`), so Back and a shared link keep the tab (`B-184`). */
export function groupFromSearch(search: string): GapGroup | null {
  const kind = new URLSearchParams(search).get('kind')
  return GROUPS.some((g) => g.key === kind) ? (kind as GapGroup) : null
}

export function GapsPage() {
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  const [group, setGroupState] = useState<GapGroup | null>(() => groupFromSearch(window.location.search))
  const [yieldOpen, setYieldOpen] = useState(false)

  function setGroup(next: GapGroup | null) {
    setGroupState(next)
    window.history.replaceState({}, '', next ? `/gaps?kind=${next}` : '/gaps')
  }

  useEffect(() => {
    const controller = new AbortController()
    getGaps({ signal: controller.signal })
      .then((body) => setLoad({ status: 'ready', body }))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setLoad({
          status: 'error',
          message: cause instanceof ApiError ? cause.message : 'The gap list could not be read.',
        })
      })
    return () => controller.abort()
  }, [])

  const gaps = load.status === 'ready' ? load.body.gaps : []
  // "All" leads with what a reader can ask about; how the crawl's searches are doing is the
  // operator's, and folds below (`B-184`). It ranked first by severity and hid the questions.
  const [reader, searches] = group === null ? splitForReaders(gaps) : [gaps.filter((g) => groupOf(g) === group), []]
  const shown = reader

  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 px-4 pb-24 pt-8 sm:px-6">
      <PageHeader title="Gaps">
        What the corpus cannot yet answer, most severe first. Every action goes through the crawl queue or the steering
        vector, takes effect at the crawl&rsquo;s next claim, and is reversible in Admin.
      </PageHeader>

      {load.status === 'loading' ? <Loading what="the gaps" /> : null}
      {load.status === 'error' ? (
        <p role="alert" className="text-[13px] text-accent-attention">
          The gap list is unavailable: {load.message}
        </p>
      ) : null}

      {load.status === 'ready' ? (
        <>
          <SourceLine sources={load.body.sources} computedAt={load.body.computed_at} />
          <Filters
            options={GROUPS.map((g) => ({
              ...g,
              count: g.key === null ? gaps.length : gaps.filter((x) => groupOf(x) === g.key).length,
            }))}
            value={group}
            onChange={setGroup}
          />
          {shown.length === 0 && searches.length === 0 ? (
            <Card className="px-[18px] py-6">
              <p className="text-[13px] text-text-muted">
                {gaps.length === 0
                  ? 'No gaps from the sources that ran. Sources marked unavailable or pending above were not checked.'
                  : 'No gaps of this kind.'}
              </p>
            </Card>
          ) : (
            <>
              {shown.length > 0 ? (
                <Card>
                  <ol aria-label="Gaps, most severe first">
                    {shown.map((gap, index) => (
                      <GapRow key={gap.id} gap={gap} rank={index + 1} first={index === 0} />
                    ))}
                  </ol>
                </Card>
              ) : null}
              {searches.length > 0 ? (
                <section aria-label="Search yield" className="flex flex-col gap-2">
                  <button
                    type="button"
                    aria-expanded={yieldOpen}
                    onClick={() => setYieldOpen(!yieldOpen)}
                    className="flex items-baseline gap-2 self-start text-left"
                  >
                    <span className={LABEL}>
                      {yieldOpen ? '⌄' : '›'} Search yield · {searches.length}
                    </span>
                    <span className="text-[12.5px] text-text-muted">
                      How the crawl&rsquo;s searches are landing. They change what is fetched, not what is known.
                    </span>
                  </button>
                  {yieldOpen ? (
                    <Card>
                      <ol aria-label="Search yield, most severe first">
                        {searches.map((gap, index) => (
                          <GapRow key={gap.id} gap={gap} rank={index + 1} first={index === 0} />
                        ))}
                      </ol>
                    </Card>
                  ) : null}
                </section>
              ) : null}
            </>
          )}
        </>
      ) : null}
    </div>
  )
}

/** Gaps a reader asks about, then the crawl's search diagnostics; each keeps its severity order. */
export function splitForReaders(gaps: readonly Gap[]): [Gap[], Gap[]] {
  return [gaps.filter((g) => groupOf(g) !== 'search'), gaps.filter((g) => groupOf(g) === 'search')]
}

/** Where an action's result can be undone, by the kind of action (`B-184`). */
export const UNDO_HREF: Record<string, string> = {
  seed_query: '/admin/seeds',
  boost_topic: '/admin/boosts',
}

/**
 * Engine syntax at the start of a seed search (`!news`, `!science`): kept on the search, kept
 * out of the words a reader edits (`B-184`).
 */
export function splitBangs(query: string): { bangs: string; words: string } {
  const match = /^((?:![a-z]+\s+)+)/i.exec(query)
  return match ? { bangs: match[1]!.trim(), words: query.slice(match[1]!.length) } : { bangs: '', words: query }
}

export const SOURCE_NAMES: Record<string, string> = {
  'topic-coverage': 'topic coverage',
  'place-coverage': 'place coverage',
  'search-queries': 'search queries',
  'search-results': 'search results',
  'question-set': 'question set',
  areas: 'fields',
  routes: 'routes',
}

/** Which sources ran, so an empty list is never read as "no gaps anywhere". */
export function SourceLine({ sources, computedAt }: { sources: GapSource[]; computedAt: string }) {
  return (
    <section aria-label="Gap sources" className="flex flex-col gap-1.5">
      <span className={LABEL}>
        Checked {stampOf(computedAt)} {zoneLabel(computedAt)}
      </span>
      <ul className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[11px] text-text-muted">
        {sources.map((s) => (
          <li key={s.name} title={s.note ?? undefined}>
            <span className="text-text">{SOURCE_NAMES[s.name] ?? s.name}</span>{' '}
            {s.status === 'ok' ? (
              <span className="tabular-nums">{s.gaps}</span>
            ) : (
              <span className={s.status === 'unavailable' ? 'text-accent-attention' : 'text-text-faint'}>
                {s.status}
                {s.note ? ` — ${s.note}` : ''}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

type Done = { ok: true; result: GapActionResult } | { ok: false; message: string }

export function GapRow({ gap, rank, first }: { gap: Gap; rank: number; first?: boolean }) {
  const [seeding, setSeeding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<Done | null>(null)
  const flagged = gap.severity >= FLAG_AT
  const seed = gap.actions.find((a) => a.kind === 'seed_query')
  const boost = gap.actions.find((a) => a.kind === 'boost_topic')
  const open = gap.actions.find((a) => a.kind === 'open_search')

  async function act(run: () => Promise<GapActionResult>) {
    setBusy(true)
    try {
      setDone({ ok: true, result: await run() })
      setSeeding(false)
    } catch (cause) {
      setDone({ ok: false, message: cause instanceof ApiError ? cause.message : 'The action failed.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <li
      className={`grid grid-cols-[2.25rem_minmax(0,1fr)] gap-x-3 px-[18px] py-4 sm:grid-cols-[2.25rem_minmax(0,1fr)_auto] ${first ? '' : ROW}`}
    >
      <span className="pt-[1px] font-mono text-[11px] tabular-nums text-text-faint">
        {String(rank).padStart(2, '0')}
      </span>
      <div className="flex min-w-0 flex-col gap-1.5">
        <span
          className={`font-mono text-[9.5px] uppercase tracking-[var(--tracking-label)] ${
            flagged ? 'text-accent-attention' : 'text-text-faint'
          }`}
        >
          {gap.subject} · {gap.kind.replaceAll('_', ' ')}
          {flagged ? <span aria-label="flagged"> †</span> : null}
        </span>
        <h2 className="text-[15px] font-semibold leading-[1.35] text-text">{gap.title}</h2>
        <p className="max-w-[78ch] text-[13px] leading-[1.55] text-text-muted">{gap.reason}</p>
        <p className="font-mono text-[11px] text-text-faint">{evidenceLine(gap.evidence).join(' · ')}</p>

        {seeding && seed ? (
          <SeedForm
            action={seed}
            busy={busy}
            onCancel={() => setSeeding(false)}
            onSubmit={(q) => act(() => seedFromGap(gap.id, seed.topic ?? gap.subject, q))}
          />
        ) : null}
        {done ? <Outcome done={done} /> : null}
      </div>

      <div className="col-start-2 mt-3 flex flex-wrap items-start gap-2 sm:col-start-3 sm:mt-0 sm:justify-end">
        {seed && !seeding ? (
          <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => setSeeding(true)}>
            {seed.label}
          </button>
        ) : null}
        {boost ? (
          <button
            type="button"
            className={BUTTON_SECONDARY}
            disabled={busy}
            onClick={() =>
              act(() => boostFromGap(gap.id, boost.topic ?? gap.subject, boost.factor ?? 2, boost.days ?? 7))
            }
          >
            {boost.label}
          </button>
        ) : null}
        {open?.query ? (
          <a href={findHref(open.query)} onClick={onInternalClick(findHref(open.query))} className={BUTTON_SECONDARY}>
            {open.label}
          </a>
        ) : null}
        {gap.kind === 'search_off_topic' ? (
          // The reason says a description would steer these searches; this is where it is set.
          <a href="/admin/topics" onClick={onInternalClick('/admin/topics')} className={BUTTON_SECONDARY}>
            Edit the topic&rsquo;s description
          </a>
        ) : null}
        {mapHrefOf(gap.evidence) ? (
          <a
            href={mapHrefOf(gap.evidence)!}
            onClick={onInternalClick(mapHrefOf(gap.evidence)!)}
            className={BUTTON_SECONDARY}
          >
            See it on the Map
          </a>
        ) : null}
      </div>
    </li>
  )
}

function SeedForm({
  action,
  busy,
  onSubmit,
  onCancel,
}: {
  action: GapAction
  busy: boolean
  onSubmit: (query: string) => void
  onCancel: () => void
}) {
  const { bangs, words } = splitBangs(action.query ?? '')
  const [query, setQuery] = useState(words)
  return (
    <form
      className="mt-2 flex max-w-[640px] flex-col gap-2 border border-line bg-surface-raised p-3"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit(bangs ? `${bangs} ${query.trim()}` : query)
      }}
    >
      <label className="flex flex-col gap-1.5 text-[12.5px] text-text-muted">
        <span>
          Words to search for, queued for {action.topic}
          {bangs ? (
            <span className="ml-2 font-mono text-[10.5px] text-text-faint">
              as a {bangs.replaceAll('!', '').split(/\s+/).join(', ')} search
            </span>
          ) : null}
        </span>
        <input className={FIELD} value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
      </label>
      <div className="flex gap-2">
        <button type="submit" className={BUTTON_PRIMARY} disabled={busy || query.trim().length < 3}>
          Queue this search
        </button>
        <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>
          Cancel
        </button>
      </div>
      <span className="font-mono text-[10.5px] text-text-faint">
        queued as a search seed · logged in the steering audit · removable in Admin while pending
      </span>
    </form>
  )
}

function Outcome({ done }: { done: Done }) {
  if (!done.ok) {
    return (
      <p role="alert" className="font-mono text-[11px] text-accent-attention">
        Refused: {done.message}
      </p>
    )
  }
  const undo = UNDO_HREF[done.result.kind]
  return (
    <p role="status" className="font-mono text-[11px] text-accent-graph">
      {done.result.detail} — {done.result.undo}
      {undo ? (
        <>
          {' '}
          <a href={undo} onClick={onInternalClick(undo)} className="underline">
            open it
          </a>
        </>
      ) : null}
    </p>
  )
}
