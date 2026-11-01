import { useEffect, useState } from 'react'

import { BUTTON_PRIMARY, BUTTON_SECONDARY, Card, FIELD, LABEL, Loading, PageHeader, ROW } from '../admin/ui'
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
import { useMe } from '../lib/me'
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

/** The operator's groups (`B-239`): each folds below the reader's list, under its own line. */
const OPERATOR_GROUPS: readonly { key: GapGroup; label: string; about: string }[] = [
  {
    key: 'questions',
    label: 'Question set',
    about: 'Held-out questions that scored low in the newest evaluation run.',
  },
  {
    key: 'search',
    label: 'Search yield',
    about: 'How the crawl’s searches are landing. They change what is fetched, not what is known.',
  },
]

/**
 * The operator's group a link names (`?kind=`), so Back and a shared link reopen its fold
 * (`B-184`). Null for anything else, the reader's list included.
 */
export function groupFromSearch(search: string): GapGroup | null {
  const kind = new URLSearchParams(search).get('kind')
  return OPERATOR_GROUPS.some((g) => g.key === kind) ? (kind as GapGroup) : null
}

export function GapsPage() {
  const { can } = useMe()
  const operator = can('admin.view')
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  const [open, setOpenState] = useState<GapGroup | null>(() => groupFromSearch(window.location.search))

  function toggle(next: GapGroup) {
    const value = open === next ? null : next
    setOpenState(value)
    window.history.replaceState({}, '', value ? `/gaps?kind=${value}` : '/gaps')
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
  // A reader's Gaps is about evidence (`B-239`): the question set is the operator's
  // evaluation, and search yield is how the crawl is doing. Both fold below, for the operator.
  const shown = readerGaps(gaps)
  const acts = can('seeds.add') || can('topics.steer')

  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 px-4 pb-24 pt-8 sm:px-6">
      <PageHeader title="Gaps">
        Where the evidence is thin, weak, stale or unconnected, most severe first.
        {acts ? (
          <>
            {' '}
            Every action goes through the crawl queue or the steering vector, takes effect at the crawl&rsquo;s next
            claim, and is reversible in Admin.
          </>
        ) : null}
      </PageHeader>

      {load.status === 'loading' ? <Loading what="the gaps" /> : null}
      {load.status === 'error' ? (
        <p role="alert" className="text-[13px] text-accent-attention">
          The gap list is unavailable: {load.message}
        </p>
      ) : null}

      {load.status === 'ready' ? (
        <>
          <SourceLine
            sources={load.body.sources.filter((s) => isReaderSource(s.name))}
            computedAt={load.body.computed_at}
            notes={operator}
          />
          {shown.length === 0 ? (
            <Card className="px-[18px] py-6">
              <p className="text-[13px] text-text-muted">
                No gaps in the evidence from the sources that ran. Sources marked unavailable or pending above were not
                checked.
              </p>
            </Card>
          ) : (
            <Card>
              <ol aria-label="Gaps, most severe first">
                {shown.map((gap, index) => (
                  <GapRow key={gap.id} gap={gap} rank={index + 1} first={index === 0} />
                ))}
              </ol>
            </Card>
          )}

          {operator ? (
            <section aria-label="For the operator" className="mt-4 flex flex-col gap-3 border-t border-line pt-5">
              <span className={LABEL}>For the operator</span>
              <SourceLine sources={load.body.sources.filter((s) => !isReaderSource(s.name))} />
              {OPERATOR_GROUPS.map(({ key, label, about }) => {
                const rows = gaps.filter((g) => groupOf(g) === key)
                const expanded = open === key
                return (
                  <section key={key} aria-label={label} className="flex flex-col gap-2">
                    <button
                      type="button"
                      aria-expanded={expanded}
                      onClick={() => toggle(key)}
                      className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 self-start text-left"
                    >
                      <span className={`${LABEL} whitespace-nowrap`}>
                        {expanded ? '⌄' : '›'} {label} · {rows.length}
                      </span>
                      <span className="text-[12.5px] text-text-muted">{about}</span>
                    </button>
                    {expanded && rows.length > 0 ? (
                      <Card>
                        <ol aria-label={`${label}, most severe first`}>
                          {rows.map((gap, index) => (
                            <GapRow key={gap.id} gap={gap} rank={index + 1} first={index === 0} />
                          ))}
                        </ol>
                      </Card>
                    ) : null}
                  </section>
                )
              })}
            </section>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

/** The gaps a reader is shown: about the evidence, not the operator's evaluation or searches. */
export function readerGaps(gaps: readonly Gap[]): Gap[] {
  return gaps.filter((g) => groupOf(g) !== 'search' && groupOf(g) !== 'questions')
}

/** Whether a gap source speaks about the evidence (shown to readers) or the operator's work. */
export function isReaderSource(name: string): boolean {
  return !OPERATOR_SOURCES.includes(name)
}

/** The sources whose gaps are the operator's (`B-239`); `groupOf` puts them in `search` or `questions`. */
export const OPERATOR_SOURCES: readonly string[] = ['search-queries', 'search-results', 'question-set']

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
export function SourceLine({
  sources,
  computedAt,
  notes = true,
}: {
  sources: GapSource[]
  computedAt?: string
  /**
   * Whether to print each source's note. A note says how to fix what is unavailable ("run …"),
   * which is the operator's to act on (`B-239`); a reader is told only that it was not checked.
   */
  notes?: boolean
}) {
  if (sources.length === 0) return null
  return (
    <section aria-label="Gap sources" className="flex flex-col gap-1.5">
      {computedAt ? (
        <span className={LABEL}>
          Checked {stampOf(computedAt)} {zoneLabel(computedAt)}
        </span>
      ) : null}
      <ul className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[11px] text-text-muted">
        {sources.map((s) => (
          <li key={s.name} title={notes ? (s.note ?? undefined) : undefined} className="[overflow-wrap:anywhere]">
            <span className="text-text">{SOURCE_NAMES[s.name] ?? s.name}</span>{' '}
            {s.status === 'ok' ? (
              <span className="tabular-nums">{s.gaps}</span>
            ) : (
              <span className={s.status === 'unavailable' ? 'text-accent-attention' : 'text-text-faint'}>
                {s.status}
                {notes && s.note ? ` — ${s.note}` : ''}
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
  const { can } = useMe()
  const [seeding, setSeeding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<Done | null>(null)
  const flagged = gap.severity >= FLAG_AT
  // Steering is offered only to whoever may steer (`B-239`); the server refuses it otherwise.
  const seed = can('seeds.add') ? gap.actions.find((a) => a.kind === 'seed_query') : undefined
  const boost = can('topics.steer') ? gap.actions.find((a) => a.kind === 'boost_topic') : undefined
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
        {gap.kind === 'search_off_topic' && can('admin.view') ? (
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
