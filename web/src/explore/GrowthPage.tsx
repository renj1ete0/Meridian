import { useEffect, useMemo, useState } from 'react'

import { LABEL, Loading, PageHeader } from '../admin/ui'
import { ApiError } from '../lib/api'
import {
  RANGES,
  fetchGrowth,
  growthQuery,
  readGrowthQuery,
  seriesColour,
  topicWords,
  type Growth,
  type GrowthCount,
  type GrowthDay,
  type GrowthRange,
  type TopicGrowth,
} from '../lib/growth'
import { shortDateOf, shortDayOf, stampOf, zoneLabel } from '../lib/time'

/**
 * How the corpus grew (task `B-140`, ADRs 0005 and 0010; mock `docs/design/CorpusGrowth.dc.html`).
 *
 * Opens on 30 days, filterable by window and topic; both live in the URL. A day the crawl did
 * not run is drawn as a dashed gap, never as a zero. See docs/features/growth.md.
 */

type Load = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; body: Growth }

const RANGE_LABEL: Record<GrowthRange, string> = { '7d': '7 days', '30d': '30 days', all: 'All' }
const MULTI_COLOUR = 'var(--text-faint)'
const n = (value: number) => value.toLocaleString('en')

export function GrowthPage() {
  const initial = readGrowthQuery(window.location.search)
  const [range, setRange] = useState<GrowthRange>(initial.range)
  const [topics, setTopics] = useState<string[]>(initial.topics)
  const [load, setLoad] = useState<Load>({ status: 'loading' })

  useEffect(() => {
    window.history.replaceState({}, '', `/growth${growthQuery(range, topics)}`)
    const controller = new AbortController()
    setLoad((prev) => (prev.status === 'ready' ? prev : { status: 'loading' }))
    fetchGrowth(range, topics, { signal: controller.signal })
      .then((body) => setLoad({ status: 'ready', body }))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setLoad({
          status: 'error',
          message: cause instanceof ApiError ? cause.message : 'Growth could not be read.',
        })
      })
    return () => controller.abort()
  }, [range, topics])

  function toggle(topic: string) {
    setTopics((now) => (now.includes(topic) ? now.filter((t) => t !== topic) : [...now, topic].sort()))
  }

  return (
    <div className="mx-auto flex w-full max-w-[1320px] flex-col gap-5 px-4 pb-24 pt-8 sm:px-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <PageHeader title="How the corpus grew">
          What the crawl read and kept, day by day. Counts come from what the database holds; a day the crawl did not
          run is drawn empty, not as zero.
        </PageHeader>
        <div className="flex items-center gap-3">
          {load.status === 'ready' ? (
            <span className="font-mono text-[10.5px] text-text-faint">
              as of {stampOf(load.body.as_of)} {zoneLabel(load.body.as_of)}
            </span>
          ) : null}
          <div role="radiogroup" aria-label="Window" className="flex border border-line">
            {RANGES.map((r) => (
              <button
                key={r}
                type="button"
                role="radio"
                aria-checked={r === range}
                onClick={() => setRange(r)}
                className={`px-3 py-1.5 font-mono text-[11px] ${
                  r === range ? 'bg-surface-raised text-text' : 'text-text-faint hover:text-text'
                }`}
              >
                {RANGE_LABEL[r]}
              </button>
            ))}
          </div>
        </div>
      </div>

      {load.status === 'loading' ? <Loading what="growth" /> : null}
      {load.status === 'error' ? (
        <p role="alert" className="text-[13px] text-accent-attention">
          Growth is unavailable: {load.message}
        </p>
      ) : null}
      {load.status === 'ready' ? (
        <GrowthBody growth={load.body} chosen={topics} onToggle={toggle} onClear={() => setTopics([])} />
      ) : null}
    </div>
  )
}

/** The page below the header, for one loaded answer. Exported for tests. */
export function GrowthBody({
  growth,
  chosen,
  onToggle,
  onClear,
}: {
  growth: Growth
  chosen: readonly string[]
  onToggle?: (topic: string) => void
  onClear?: () => void
}) {
  const offered = growth.all_topics.filter((t) => t.passages.total > 0 || chosen.includes(t.topic))
  const shown = growth.all_topics.filter((t) => growth.topics.includes(t.topic))
  const span = growth.days === null ? 'since the start' : `in ${growth.days} days`

  return (
    <>
      <TopicFilter offered={offered} chosen={chosen} onToggle={onToggle} onClear={onClear} />

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile
          label="Passages on a topic"
          count={growth.passages}
          note={windowNote(growth.passages, span, growth.first_day)}
        />
        <Tile label="Sources kept" count={growth.sources} note={windowNote(growth.sources, span, growth.first_day)} />
        <Tile label="Sites" count={growth.sites} note={windowNote(growth.sites, `new ${span}`, growth.first_day)} />
        <Tile
          label="Knowledge graph"
          count={growth.concepts}
          unit="concepts"
          note={`${n(growth.links.total)} links · ${windowNote(growth.concepts, span, growth.first_day)}`}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <DailyChart days={growth.daily} topics={shown} />
        <ByTopic topics={shown} span={span} />
      </div>

      <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_420px]">
        <NewSites days={growth.daily} />
        <MapPanel growth={growth} />
      </div>
    </>
  )
}

function TopicFilter({
  offered,
  chosen,
  onToggle,
  onClear,
}: {
  offered: readonly TopicGrowth[]
  chosen: readonly string[]
  onToggle?: (topic: string) => void
  onClear?: () => void
}) {
  if (offered.length === 0) return null
  return (
    <div role="group" aria-label="Topics" className="flex flex-wrap items-center gap-2">
      <span className={LABEL}>Topics</span>
      <button
        type="button"
        aria-pressed={chosen.length === 0}
        onClick={onClear}
        className={`border px-2.5 py-1 text-[12px] ${
          chosen.length === 0 ? 'border-line-strong bg-surface-raised text-text' : 'border-line text-text-muted'
        }`}
      >
        All
      </button>
      {offered.map((t) => {
        const on = chosen.includes(t.topic)
        return (
          <button
            key={t.topic}
            type="button"
            aria-pressed={on}
            onClick={() => onToggle?.(t.topic)}
            className={`flex items-center gap-1.5 border px-2.5 py-1 text-[12px] ${
              on ? 'border-line-strong bg-surface-raised text-text' : 'border-line text-text-muted'
            }`}
          >
            <span aria-hidden className="block h-2 w-2 rounded-[2px]" style={{ background: seriesColour(t.series) }} />
            {topicWords(t.topic)}
          </button>
        )
      })}
    </div>
  )
}

/**
 * What a tile's window added (`B-187`). On a corpus younger than the window every count is new,
 * and "+91,850 in 30 days" beside 91,850 says the same number twice; it says since when instead.
 * The first day is a calendar date, formatted without converting it (`shortDateOf`).
 */
export function windowNote(count: GrowthCount, span: string, firstDay: string | null): string {
  if (count.total > 0 && count.in_window === count.total && firstDay) return `all since ${shortDateOf(firstDay)}`
  return `+${n(count.in_window)} ${span}`
}

function Tile({ label, count, note, unit }: { label: string; count: GrowthCount; note: string; unit?: string }) {
  return (
    <div className="flex flex-col gap-2 border border-line bg-surface px-[18px] py-4">
      <span className={LABEL}>{label}</span>
      <span className="font-mono text-[27px] leading-none tracking-[-0.02em] text-text tabular-nums">
        {n(count.total)}
        {unit ? <span className="ml-2 text-[15px] text-text-muted">{unit}</span> : null}
      </span>
      <span className="font-mono text-[10.5px] text-text-muted">{note}</span>
    </div>
  )
}

/** Pages kept per day, stacked by topic, with "two or more" on top. Exported for tests. */
export function DailyChart({ days, topics }: { days: readonly GrowthDay[]; topics: readonly TopicGrowth[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const [table, setTable] = useState(false)
  const totals = days.map((d) => topics.reduce((sum, t) => sum + (d.by_topic[t.topic] ?? 0), 0) + d.multi)
  const top = Math.max(20, Math.ceil(Math.max(0, ...totals) / 20) * 20)
  const W = 760
  const H = 230
  const step = days.length ? W / days.length : W
  const bar = Math.max(2, step - Math.min(6, step * 0.25))
  const y = (v: number) => H - (v / top) * H
  const ticks = [0, top / 4, top / 2, (3 * top) / 4, top].map(Math.round)
  const labelEvery = Math.max(1, Math.ceil(days.length / 5))

  return (
    <section
      aria-label="Pages kept on a topic, per day"
      className="flex min-w-0 flex-col gap-3 border border-line bg-surface px-5 py-4"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[14px] font-semibold text-text">Pages kept on a topic, per day</h2>
        <button type="button" onClick={() => setTable((t) => !t)} className="font-mono text-[10.5px] text-accent-graph">
          {table ? 'Chart view' : 'Table view'}
        </button>
      </div>
      <Legend topics={topics} />
      {table ? (
        <DailyTable days={days} topics={topics} />
      ) : (
        <div className="relative overflow-x-auto">
          <svg
            role="img"
            aria-label={`Pages kept on a topic over ${days.length} days`}
            viewBox={`-36 -6 ${W + 44} ${H + 30}`}
            className="block w-full min-w-[480px]"
            onMouseLeave={() => setHover(null)}
          >
            {ticks.map((t) => (
              <g key={t}>
                <line x1={0} x2={W} y1={y(t)} y2={y(t)} stroke="var(--line)" strokeWidth={1} />
                <text
                  x={-8}
                  y={y(t) + 3}
                  textAnchor="end"
                  fontSize={9.5}
                  fill="var(--text-faint)"
                  className="font-mono"
                >
                  {t}
                </text>
              </g>
            ))}
            {days.map((d, i) => {
              const x = i * step + (step - bar) / 2
              if (!d.crawled) {
                return (
                  <line
                    key={d.day}
                    x1={x}
                    x2={x + bar}
                    y1={H - 0.5}
                    y2={H - 0.5}
                    stroke="var(--line-strong)"
                    strokeDasharray="2 2"
                  />
                )
              }
              const segments = [
                ...topics.map((t) => ({ v: d.by_topic[t.topic] ?? 0, colour: seriesColour(t.series) })),
                { v: d.multi, colour: MULTI_COLOUR },
              ].filter((s) => s.v > 0)
              let acc = 0
              return (
                <g key={d.day}>
                  {segments.map((s, k) => {
                    const h = (s.v / top) * H
                    const y0 = H - (acc / top) * H - h
                    acc += s.v
                    return (
                      <rect
                        key={k}
                        x={x}
                        y={y0}
                        width={bar}
                        height={Math.max(h - (k > 0 ? 2 : 0), 0.5)}
                        rx={k === segments.length - 1 ? 2 : 0}
                        fill={s.colour}
                      />
                    )
                  })}
                  <rect
                    x={i * step}
                    y={0}
                    width={step}
                    height={H}
                    fill="transparent"
                    onMouseEnter={() => setHover(i)}
                    onFocus={() => setHover(i)}
                  />
                </g>
              )
            })}
            {hover !== null ? (
              <line
                x1={hover * step + step / 2}
                x2={hover * step + step / 2}
                y1={0}
                y2={H}
                stroke="var(--line-strong)"
              />
            ) : null}
            {days.map((d, i) =>
              i % labelEvery === 0 && i < days.length - Math.ceil(labelEvery / 2) ? (
                <text
                  key={d.day}
                  x={i * step + step / 2}
                  y={H + 16}
                  textAnchor="middle"
                  fontSize={9.5}
                  fill="var(--text-faint)"
                  className="font-mono"
                >
                  {shortDateOf(d.day)}
                </text>
              ) : null,
            )}
            {days.length ? (
              <text
                x={(days.length - 1) * step + step / 2}
                y={H + 16}
                textAnchor="middle"
                fontSize={9.5}
                fill="var(--text-muted)"
                className="font-mono"
              >
                today
              </text>
            ) : null}
          </svg>
          {hover !== null && days[hover] ? (
            <DayTip day={days[hover]} total={totals[hover] ?? 0} topics={topics} left={(hover / days.length) * 100} />
          ) : null}
        </div>
      )}
    </section>
  )
}

function Legend({ topics }: { topics: readonly TopicGrowth[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1">
      {topics.map((t) => (
        <li key={t.topic} className="flex items-center gap-1.5 text-[11.5px] text-text-muted">
          <span aria-hidden className="block h-2 w-2 rounded-[2px]" style={{ background: seriesColour(t.series) }} />
          {topicWords(t.topic)}
        </li>
      ))}
      <li className="flex items-center gap-1.5 text-[11.5px] text-text-muted">
        <span aria-hidden className="block h-2 w-2 rounded-[2px]" style={{ background: MULTI_COLOUR }} />
        Two or more
      </li>
    </ul>
  )
}

function DayTip({
  day,
  total,
  topics,
  left,
}: {
  day: GrowthDay
  total: number
  topics: readonly TopicGrowth[]
  left: number
}) {
  return (
    <div
      role="status"
      className="pointer-events-none absolute top-0 flex w-44 flex-col gap-1.5 border border-line-strong bg-surface-raised px-3 py-2.5 text-[11.5px] text-text-muted"
      style={{ left: `clamp(0px, calc(${left}% - 11rem), calc(100% - 11rem))` }}
    >
      <span className="font-mono text-[10px] text-text">
        {shortDateOf(day.day)} · {n(total)} pages
      </span>
      {topics.map((t) => (
        <span key={t.topic} className="flex items-center gap-2">
          <span aria-hidden className="block h-2 w-2 rounded-[2px]" style={{ background: seriesColour(t.series) }} />
          <span className="grow">{topicWords(t.topic)}</span>
          <span className="font-mono text-text">{n(day.by_topic[t.topic] ?? 0)}</span>
        </span>
      ))}
      <span className="flex items-center gap-2">
        <span aria-hidden className="block h-2 w-2 rounded-[2px]" style={{ background: MULTI_COLOUR }} />
        <span className="grow">Two or more</span>
        <span className="font-mono text-text">{n(day.multi)}</span>
      </span>
    </div>
  )
}

function DailyTable({ days, topics }: { days: readonly GrowthDay[]; topics: readonly TopicGrowth[] }) {
  return (
    <div className="max-h-[280px] overflow-auto">
      <table className="w-full border-collapse font-mono text-[11.5px] tabular-nums">
        <thead>
          <tr>
            <th className={`${LABEL} pb-2 text-left`}>Day</th>
            {topics.map((t) => (
              <th key={t.topic} className={`${LABEL} pb-2 text-right`}>
                {topicWords(t.topic)}
              </th>
            ))}
            <th className={`${LABEL} pb-2 text-right`}>Two or more</th>
            <th className={`${LABEL} pb-2 text-right`}>New sites</th>
          </tr>
        </thead>
        <tbody>
          {days.map((d) => (
            <tr key={d.day} className="border-t border-line text-text">
              <td className="py-1.5">
                {d.day}
                {d.crawled ? '' : ' · not crawled'}
              </td>
              {topics.map((t) => (
                <td key={t.topic} className="text-right">
                  {d.crawled ? n(d.by_topic[t.topic] ?? 0) : '—'}
                </td>
              ))}
              <td className="text-right">{d.crawled ? n(d.multi) : '—'}</td>
              <td className="text-right">{n(d.new_sites)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Sparkline({ values, colour }: { values: readonly number[]; colour: string }) {
  const cumulative = useMemo(() => {
    let sum = 0
    return values.map((v) => (sum += v))
  }, [values])
  const W = 110
  const H = 22
  const peak = Math.max(1, ...cumulative)
  const points = cumulative
    .map((v, i) => `${((i / Math.max(1, cumulative.length - 1)) * W).toFixed(1)},${(H - (v / peak) * H).toFixed(1)}`)
    .join(' ')
  return (
    <svg width={W} height={H} aria-hidden className="block overflow-visible">
      <polyline
        points={points}
        fill="none"
        stroke={colour}
        strokeWidth={1.6}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}

function ByTopic({ topics, span }: { topics: readonly TopicGrowth[]; span: string }) {
  return (
    <section aria-label="By topic" className="flex min-w-0 flex-col gap-3 border border-line bg-surface px-5 py-4">
      <h2 className="text-[14px] font-semibold text-text">By topic</h2>
      <table className="w-full border-collapse">
        <thead>
          <tr className="border-b border-line">
            <th className={`${LABEL} pb-2 text-left`}>Topic</th>
            <th className={`${LABEL} pb-2 text-right`}>Passages</th>
            <th className={`${LABEL} pb-2 text-right`}>New</th>
            <th className={`${LABEL} pb-2 pl-4 text-left`}>Pages, cumulative</th>
          </tr>
        </thead>
        <tbody>
          {topics.map((t) => (
            <tr key={t.topic}>
              <td className="py-2">
                <span className="flex items-center gap-2 text-[12.5px] text-text">
                  <span
                    aria-hidden
                    className="block h-2 w-2 rounded-[2px]"
                    style={{ background: seriesColour(t.series) }}
                  />
                  {topicWords(t.topic)}
                </span>
              </td>
              <td className="text-right font-mono text-[11.5px] text-text tabular-nums">{n(t.passages.total)}</td>
              <td className="text-right font-mono text-[11.5px] text-text-muted tabular-nums">
                +{n(t.passages.in_window)}
              </td>
              <td className="pl-4">
                <Sparkline values={t.daily} colour={seriesColour(t.series)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11.5px] leading-normal text-text-faint">
        New passages {span}. A passage inside a document about another topic counts for its own topic; a page about two
        topics counts once in the chart, under &ldquo;two or more&rdquo;.
      </p>
    </section>
  )
}

function NewSites({ days }: { days: readonly GrowthDay[] }) {
  const peak = Math.max(1, ...days.map((d) => d.new_sites))
  const W = 800
  const H = 40
  const step = days.length ? W / days.length : W
  return (
    <section
      aria-label="New sites per day"
      className="flex min-w-0 flex-col gap-3 border border-line bg-surface px-5 py-4"
    >
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[14px] font-semibold text-text">New sites per day</h2>
        <span className="font-mono text-[10.5px] text-text-faint">first page kept from a host</span>
      </div>
      <svg
        role="img"
        aria-label="New sites per day"
        viewBox={`0 0 ${W} ${H + 2}`}
        className="block h-[42px] w-full"
        preserveAspectRatio="none"
      >
        {days.map((d, i) => {
          const h = (d.new_sites / peak) * H
          return (
            <rect
              key={d.day}
              x={i * step + step * 0.1}
              y={H - h}
              width={step * 0.8}
              height={Math.max(h, 0.5)}
              rx={1.5}
              fill="var(--accent-graph)"
            >
              <title>{`${d.day}: ${d.new_sites} new`}</title>
            </rect>
          )
        })}
      </svg>
    </section>
  )
}

function MapPanel({ growth }: { growth: Growth }) {
  const now = growth.map_now
  return (
    <section aria-label="The map" className="flex min-w-0 flex-col gap-3 border border-line bg-surface px-5 py-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-[14px] font-semibold text-text">The map</h2>
        {growth.map_history_from ? (
          <span className="font-mono text-[10.5px] text-text-faint">
            history from {shortDayOf(growth.map_history_from)}
          </span>
        ) : null}
      </div>
      {now ? (
        <div className="flex gap-6">
          <Mini value={now.regions} label="regions" />
          <Mini value={now.areas} label="areas" extra={now.weak_areas ? `${now.weak_areas} weak` : null} />
          <Mini value={now.sub_areas} label="sub-areas" />
        </div>
      ) : (
        <p className="text-[12.5px] text-text-muted">No map has been built yet.</p>
      )}
    </section>
  )
}

function Mini({ value, label, extra }: { value: number; label: string; extra?: string | null }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="font-mono text-[20px] leading-none text-text tabular-nums">{n(value)}</span>
      <span className="font-mono text-[10.5px] text-text-muted">
        {label}
        {extra ? <span className="text-accent-attention"> · {extra}</span> : null}
      </span>
    </div>
  )
}
