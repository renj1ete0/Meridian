import { useEffect, useState } from 'react'

import { Loading } from '../admin/ui'
import {
  ApiError,
  getWhatsNew,
  type DocKind,
  type NewBasis,
  type NewFinding,
  type NewSource,
  type NewTopic,
  type WhatsNew,
} from '../lib/api'
import { KIND_LABEL } from '../lib/find'
import { topicWords } from '../lib/growth'
import { readPreviousVisit } from '../lib/lastVisit'
import { ago } from '../lib/status'
import { hrefForNode, hrefForSource, navigate, onInternalClick } from '../lib/route'
import { dateOf, shortDayOf, stampOf, zoneLabel } from '../lib/time'
import { topicFindHref } from './TopicCards'

/**
 * What's new (task `B-237`, `WhatsNew.dc.html`): sources new since the reader's last visit, or
 * in a chosen window, by topic and kind, each with its best passage; then what the graph
 * learned, each with its citation. See docs/features/whats-new.md.
 */

/** The window a reader picks: since their last visit, or the last week or month. */
export type NewWindow = 'last' | '7d' | '30d'

const WINDOW_DAYS: Record<Exclude<NewWindow, 'last'>, number> = { '7d': 7, '30d': 30 }

const WINDOW_LABEL: Record<NewWindow, string> = { last: 'Since last visit', '7d': '7 days', '30d': '30 days' }

/** What the page's URL holds: the window, the basis, and an exact instant when linked with one. */
export interface NewQuery {
  window: NewWindow
  basis: NewBasis
  /** An instant from the link (the landing passes the one it counted from). */
  since: string | null
}

/** One document of each kind, as a row's chip names it. */
export const KIND_ONE: Readonly<Record<DocKind, string>> = {
  news: 'News',
  press_release: 'Press release',
  opinion: 'Opinion',
  report: 'Report',
  paper: 'Paper',
  legal: 'Legal text',
  profile: 'Organisation page',
  listing: 'Listing',
  other: 'Other',
}

/** Read `/new?window=&basis=&since=`; anything unknown falls back to the defaults. */
export function readNewQuery(search: string): NewQuery {
  const params = new URLSearchParams(search)
  const window = params.get('window')
  const since = params.get('since')
  return {
    window: window === '7d' || window === '30d' ? window : 'last',
    basis: params.get('basis') === 'published' ? 'published' : 'added',
    since: since && !Number.isNaN(Date.parse(since)) ? since : null,
  }
}

/** The inverse of {@link readNewQuery}; defaults are left out so the plain link is `/new`. */
export function newHref(query: NewQuery): string {
  const params = new URLSearchParams()
  if (query.window !== 'last') params.set('window', query.window)
  if (query.window === 'last' && query.since) params.set('since', query.since)
  if (query.basis !== 'added') params.set('basis', query.basis)
  const suffix = params.toString()
  return suffix ? `/new?${suffix}` : '/new'
}

/**
 * The instant to ask from. Since-last-visit with no visit recorded falls back to a week, and
 * says so (`fellBack`), rather than showing everything or nothing.
 */
export function sinceFor(
  query: NewQuery,
  previousVisit: string | null,
  now: Date = new Date(),
): { since: string; fellBack: boolean } {
  if (query.window !== 'last') {
    return { since: new Date(now.getTime() - WINDOW_DAYS[query.window] * 86_400_000).toISOString(), fellBack: false }
  }
  const since = query.since ?? previousVisit
  if (since) return { since, fellBack: false }
  return { since: new Date(now.getTime() - 7 * 86_400_000).toISOString(), fellBack: true }
}

/** The sentence under the title: what is listed, and from when. */
export function windowLine(query: NewQuery, since: string, fellBack: boolean, now: Date = new Date()): string {
  const what = query.basis === 'published' ? 'Sources dated' : 'Sources added'
  const at = `${stampOf(since)} ${zoneLabel(since)}`
  if (query.window !== 'last') return `${what} in the last ${WINDOW_DAYS[query.window]} days, since ${at}`
  if (fellBack) return `No earlier visit is recorded in this browser, so: ${what.toLowerCase()} in the last 7 days`
  return `${what} since you were last here, ${ago(since, now)} · ${at}`
}

/** The summary line's figures, as one string for a screen reader and a test. */
export function summaryOf(body: WhatsNew): string {
  const total = body.topics.reduce((sum, topic) => sum + topic.total, 0)
  const withNew = body.topics.filter((topic) => topic.total > 0).length
  const parts = [`+${total.toLocaleString('en')} sources on ${withNew} topic${withNew === 1 ? '' : 's'}`]
  if (body.elsewhere > 0) parts.push(`${body.elsewhere.toLocaleString('en')} on none of them`)
  parts.push(`${body.findings_total.toLocaleString('en')} new finding${body.findings_total === 1 ? '' : 's'}`)
  return parts.join(' · ')
}

type Load = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; body: WhatsNew }

const LABEL = 'font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint'
const COUNT = 'whitespace-nowrap font-mono text-[10.5px] tabular-nums text-text-faint'
const ACTION = 'font-mono text-[10.5px] text-accent-graph hover:underline'
const CHIP =
  'inline-flex shrink-0 items-center border border-line px-1.5 py-px font-mono text-[9px] uppercase ' +
  'leading-[1.5] tracking-[0.1em] text-text-faint'

export function WhatsNewPage() {
  const [query, setQuery] = useState<NewQuery>(() => readNewQuery(window.location.search))
  const [previous] = useState<string | null>(readPreviousVisit)
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  // Computed once per choice, so a re-render does not move the window and refetch.
  const [{ since, fellBack }, setWindow] = useState(() => sinceFor(query, previous))

  useEffect(() => {
    const onPop = () => {
      const next = readNewQuery(window.location.search)
      setQuery(next)
      setWindow(sinceFor(next, previous))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [previous])

  useEffect(() => {
    const controller = new AbortController()
    setLoad({ status: 'loading' })
    getWhatsNew({ since, basis: query.basis }, { signal: controller.signal })
      .then((body) => setLoad({ status: 'ready', body }))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setLoad({ status: 'error', message: cause instanceof ApiError ? cause.message : 'It could not be read.' })
      })
    return () => controller.abort()
  }, [since, query.basis])

  function choose(next: NewQuery) {
    // A chosen window replaces the linked instant; "since last visit" goes back to the stamp.
    const cleaned = { ...next, since: next.window === 'last' ? query.since : null }
    navigate(newHref(cleaned))
  }

  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-5 px-4 pb-24 pt-8 sm:px-6">
      {/* Not `PageHeader`: its actions never shrink, and these two controls must wrap on a phone. */}
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
        <div className="flex min-w-0 flex-col gap-1.5">
          <h1 className="text-[25px] font-semibold leading-[1.15] tracking-[var(--tracking-display)] text-text">
            What's new
          </h1>
          <p className="max-w-[74ch] text-[13px] leading-[1.55] text-text-muted">
            {windowLine(query, load.status === 'ready' ? load.body.since : since, fellBack)}
          </p>
        </div>
        <div className="flex min-w-0 flex-wrap gap-3">
          <Segmented
            label="Window"
            options={(['last', '7d', '30d'] as const).map((w) => ({ value: w, label: WINDOW_LABEL[w] }))}
            value={query.window}
            onChange={(window) => choose({ ...query, window })}
          />
          <Segmented
            label="Measured by"
            options={[
              { value: 'added', label: 'Added' },
              { value: 'published', label: 'Published' },
            ]}
            value={query.basis}
            onChange={(basis) => choose({ ...query, basis })}
          />
        </div>
      </header>

      {load.status === 'loading' ? <Loading what="what is new" /> : null}
      {load.status === 'error' ? (
        <p role="alert" className="text-[13px] text-accent-attention">
          What is new is unavailable: {load.message}
        </p>
      ) : null}
      {load.status === 'ready' ? <Answer body={load.body} /> : null}
    </div>
  )
}

function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (value: T) => void
}) {
  return (
    <div role="group" aria-label={label} className="flex border border-line">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={`px-2.5 py-[5px] font-mono text-[10.5px] ${
            option.value === value ? 'bg-surface-raised text-text' : 'text-text-muted hover:text-text'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

function Answer({ body }: { body: WhatsNew }) {
  if (body.topics.length === 0) {
    return <p className="text-[13px] text-text-muted">No topics are configured, so there is nothing to group by.</p>
  }
  return (
    <>
      <p className="font-mono text-[11.5px] tabular-nums text-text-muted" data-summary>
        {summaryOf(body)}
      </p>
      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="flex min-w-0 flex-col gap-4">
          {body.topics.map((topic) => (
            <TopicSection key={topic.topic} topic={topic} basis={body.basis} />
          ))}
        </div>
        <Findings findings={body.findings} total={body.findings_total} />
      </div>
    </>
  )
}

function TopicSection({ topic, basis }: { topic: NewTopic; basis: NewBasis }) {
  const name = topicWords(topic.topic)
  if (topic.total === 0) {
    return (
      <section
        aria-label={name}
        data-topic={topic.topic}
        className="flex items-baseline justify-between gap-3 border border-line bg-surface px-5 py-4"
      >
        <h2 className="text-[15px] font-semibold text-text-muted first-letter:uppercase">{name}</h2>
        <span className={COUNT}>nothing new</span>
      </section>
    )
  }
  const listed = topic.sources.length
  const find = topicFindHref(topic.topic)
  const unlisted = topic.by_kind.some((k) => k.doc_kind === 'listing' && k.sources > 0)
  return (
    <section
      aria-label={name}
      data-topic={topic.topic}
      className="flex flex-col gap-3 border border-line bg-surface px-5 py-[18px]"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[15px] font-semibold leading-tight tracking-[-0.004em] text-text first-letter:uppercase">
          {name}
        </h2>
        <span className={COUNT}>
          +{topic.total.toLocaleString('en')} source{topic.total === 1 ? '' : 's'}
        </span>
      </div>
      <ul aria-label="By kind" className="flex flex-wrap gap-1.5">
        {topic.by_kind.map((kind) => (
          <li
            key={kind.doc_kind ?? 'none'}
            className="border border-line px-1.5 py-px font-mono text-[10px] tabular-nums text-text-muted"
          >
            {kind.doc_kind ? KIND_LABEL[kind.doc_kind] : 'Unclassified'} {kind.sources.toLocaleString('en')}
          </li>
        ))}
      </ul>
      {listed > 0 ? (
        <ul>
          {topic.sources.map((source) => (
            <SourceRow key={source.source_id} source={source} basis={basis} />
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap items-baseline justify-between gap-x-[18px] gap-y-1">
        <span className={COUNT}>
          {listed < topic.total ? `${listed} of ${topic.total.toLocaleString('en')} listed` : ''}
          {listed < topic.total && unlisted ? ' · listings are counted, not listed' : ''}
        </span>
        <a href={find} onClick={onInternalClick(find)} className={ACTION}>
          Find in this topic →
        </a>
      </div>
    </section>
  )
}

/** A topic as a reader reads it mid-line: words, first letter capital. */
export function topicName(topic: string): string {
  const words = topicWords(topic)
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The mono line under a title: site, publisher, the date, and the source's other topics. */
export function metaOf(source: NewSource, basis: NewBasis): string {
  const parts: string[] = []
  if (source.site) parts.push(source.site)
  if (source.publisher && source.publisher !== source.site) parts.push(source.publisher)
  parts.push(source.published ? dateOf(source.published) : 'undated')
  // By publication date, when it arrived is the other half of "new".
  if (basis === 'published' || !source.published) parts.push(`added ${shortDayOf(source.added)}`)
  if (source.other_topics.length > 0) parts.push(`also ${source.other_topics.map(topicName).join(', ')}`)
  return parts.join(' · ')
}

function SourceRow({ source, basis }: { source: NewSource; basis: NewBasis }) {
  const href = hrefForSource(source.source_id, source.excerpt?.chunk_id)
  return (
    <li className="flex flex-col gap-1 border-t border-line/60 py-2.5" data-source={source.source_id}>
      <div className="flex min-w-0 items-baseline gap-2">
        {source.doc_kind ? <span className={CHIP}>{KIND_ONE[source.doc_kind]}</span> : null}
        <a
          href={href}
          onClick={onInternalClick(href)}
          className="min-w-0 text-[13.5px] font-medium text-text hover:underline [overflow-wrap:anywhere]"
        >
          {source.title ?? 'Untitled source'}
        </a>
      </div>
      <p className="font-mono text-[10.5px] text-text-faint [overflow-wrap:anywhere]">{metaOf(source, basis)}</p>
      {source.excerpt ? (
        <p className="text-[12.5px] leading-[1.5] text-text-muted">{source.excerpt.text}</p>
      ) : (
        <p className="text-[12px] text-text-faint">No passage of it is readable yet.</p>
      )}
    </li>
  )
}

/** A finding as a sentence: "subject relation object", the relation's underscores spaced. */
export function claimOf(finding: NewFinding): string {
  const relation = finding.relation.replaceAll('_', ' ')
  return finding.object ? `${finding.subject} ${relation} ${finding.object}` : `${finding.subject} ${relation}`
}

function Findings({ findings, total }: { findings: NewFinding[]; total: number }) {
  return (
    <section
      aria-labelledby="findings-label"
      className="flex flex-col gap-3 border border-line bg-surface px-5 py-[18px]"
    >
      <h2 id="findings-label" className={LABEL}>
        New findings · {total.toLocaleString('en')}
      </h2>
      {findings.length === 0 ? (
        <p className="text-[12.5px] leading-[1.5] text-text-muted">
          The knowledge graph gained no cited link or tag in this window.
        </p>
      ) : (
        <ul>
          {findings.map((finding, i) => (
            <FindingRow key={`${finding.kind}-${finding.finding_id}`} finding={finding} first={i === 0} />
          ))}
        </ul>
      )}
      {findings.length > 0 && findings.length < total ? (
        <span className={COUNT}>
          {findings.length} of {total.toLocaleString('en')} listed
        </span>
      ) : null}
    </section>
  )
}

function FindingRow({ finding, first }: { finding: NewFinding; first: boolean }) {
  const cite = hrefForSource(finding.citation.source_id, finding.citation.chunk_id)
  const node = hrefForNode(finding.subject_id)
  const other = finding.object_id !== null ? hrefForNode(finding.object_id) : null
  const relation = finding.relation.replaceAll('_', ' ')
  return (
    <li
      className={`flex flex-col gap-1 py-2.5 ${first ? '' : 'border-t border-line/60'}`}
      data-finding={`${finding.kind}-${finding.finding_id}`}
      aria-label={claimOf(finding)}
    >
      <p className="text-[13px] text-text [overflow-wrap:anywhere]">
        <a href={node} onClick={onInternalClick(node)} className="hover:underline">
          {finding.subject}
        </a>{' '}
        <span className="font-mono text-[11px] text-text-faint">{relation}</span>{' '}
        {other ? (
          <a href={other} onClick={onInternalClick(other)} className="hover:underline">
            {finding.object}
          </a>
        ) : (
          <span>{finding.object}</span>
        )}
      </p>
      <p className="font-mono text-[10.5px] text-text-faint [overflow-wrap:anywhere]">
        {finding.topic ? `${topicName(finding.topic)} · ` : ''}
        {finding.kind === 'tag' ? 'tag · ' : ''}from{' '}
        <a href={cite} onClick={onInternalClick(cite)} className="text-text/80 hover:text-text hover:underline">
          {finding.citation.title ?? 'Untitled source'}
        </a>
      </p>
      <p className="text-[12px] leading-[1.5] text-text-muted">“{finding.citation.text}”</p>
    </li>
  )
}
