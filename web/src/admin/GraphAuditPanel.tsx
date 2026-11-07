import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../lib/api'
import {
  getAuditDraw,
  getGraphAudit,
  gradeAuditSample,
  isEdgeShown,
  isMatchShown,
  KIND_LABEL,
  type AuditDraw,
  type AuditDrawDetail,
  type AuditPassage,
  type AuditSample,
  type AuditTally,
  type AuditVerdict,
  type EdgeShown,
  type GraphAudit,
  type MatchShown,
  type MergeShown,
  type MergeSide,
} from '../lib/graphAudit'
import { readable } from '../lib/readable'
import { hrefForNode, hrefForSource, onInternalClick } from '../lib/route'
import { useEdit } from './editGate'
import {
  BUTTON_PRIMARY,
  BUTTON_ROW,
  BUTTON_SECONDARY,
  Card,
  Filters,
  LABEL,
  PageHeader,
  ROW,
  SubHeading,
  TD,
  TDM,
  TH,
  TableCard,
  stamp,
} from './ui'

/**
 * The graph audit (`P7-10`, spec §14.1): a monthly random sample of new relations, merges and
 * matches at resolution (`B-273`), graded by a person. Precision per draw is the measure over time; a grade changes nothing in
 * the graph. See docs/features/knowledge-graph.md#the-graph-audit.
 */

export interface GraphAuditPanelProps {
  /** What to show before loading anything; given, the panel does not load on opening. */
  initial?: GraphAudit
  initialDraw?: AuditDrawDetail
  /** Injected in tests; the page passes nothing and the panel loads itself. */
  load?: () => Promise<GraphAudit>
  loadDraw?: (drawId: number) => Promise<AuditDrawDetail>
  grade?: (sampleId: number, verdict: AuditVerdict) => Promise<AuditSample>
}

const pct = (value: number) => `${Math.round(value * 100)}%`

/** Precision and its interval as the table prints it, or a dash before anything is judged. */
export function precisionText(tally: AuditTally): string {
  if (tally.precision === null || tally.low === null || tally.high === null) return '—'
  return `${pct(tally.precision)} (${pct(tally.low)}–${pct(tally.high)})`
}

/** How much of a sample is graded, and what it was drawn from. */
export function gradedText(tally: AuditTally, population: number): string {
  if (tally.drawn === 0) return population === 0 ? 'none made' : `none drawn of ${population.toLocaleString()}`
  const unsure = tally.unsure ? ` · ${tally.unsure} unsure` : ''
  return `${tally.graded} of ${tally.drawn} graded${unsure} · from ${population.toLocaleString()}`
}

/** What each resolver signal is called on the page; see docs/features/knowledge-graph.md#resolution. */
const SIGNAL_LABEL: Record<string, string> = { string: 'name', embedding: 'meaning', context: 'shared passages' }
const SIGNAL_ORDER = ['string', 'embedding', 'context']

/**
 * The signals behind a resolver's score, in a fixed order: "name 0.88 · meaning 0.97". A signal
 * the resolver could not compute is absent, not zero, and is left out; null when none was kept.
 */
export function signalsText(signals: Record<string, number> | null): string | null {
  if (!signals) return null
  const keys = Object.keys(signals).sort(
    (a, b) => (SIGNAL_ORDER.indexOf(a) + 1 || 99) - (SIGNAL_ORDER.indexOf(b) + 1 || 99) || a.localeCompare(b),
  )
  if (keys.length === 0) return null
  return keys.map((k) => `${SIGNAL_LABEL[k] ?? k.replaceAll('_', ' ')} ${signals[k]!.toFixed(2)}`).join(' · ')
}

function Signals({ signals }: { signals: Record<string, number> | null }) {
  const text = signalsText(signals)
  return text ? <span className="font-mono text-[10.5px] text-text-faint">from {text}</span> : null
}

const VERDICTS: readonly { key: AuditVerdict; label: string }[] = [
  { key: 'correct', label: 'Correct' },
  { key: 'wrong', label: 'Wrong' },
  { key: 'unsure', label: 'Unsure' },
]

function Passages({ passages }: { passages: readonly AuditPassage[] }) {
  if (passages.length === 0) {
    return <p className="font-mono text-[10.5px] text-text-faint">No passage kept with it.</p>
  }
  return (
    <ul className="flex flex-col gap-2">
      {passages.map((p) => (
        <li key={p.chunk_id} className="border-l border-line pl-2.5">
          <a
            href={hrefForSource(p.source_id, p.chunk_id)}
            onClick={onInternalClick(hrefForSource(p.source_id, p.chunk_id))}
            className="text-[12px] leading-[1.5] text-text-muted no-underline hover:text-text"
          >
            “{readable(p.text).trim()}…”
            <span className="block font-mono text-[10px] text-text-faint">{p.title ?? `source ${p.source_id}`}</span>
          </a>
        </li>
      ))}
    </ul>
  )
}

function NodeLink({ id, name }: { id: number; name: string }) {
  return (
    <a
      href={hrefForNode(id)}
      onClick={onInternalClick(hrefForNode(id))}
      className="font-semibold text-text hover:text-accent-graph"
    >
      {name}
    </a>
  )
}

function Side({ side, role }: { side: MergeSide; role: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className={LABEL}>{role}</span>
      <span className="text-[14px] leading-snug">
        <NodeLink id={side.entity_id} name={side.name} />
      </span>
      <span className="font-mono text-[10.5px] text-text-faint">
        {(side.node_type ?? 'unknown').replaceAll('_', ' ')}
        {side.jurisdiction ? ` · ${side.jurisdiction}` : ''}
      </span>
      {side.aliases?.length ? (
        <span className="text-[12px] text-text-muted">also: {side.aliases.slice(0, 6).join(', ')}</span>
      ) : null}
      {side.description ? <p className="text-[12.5px] leading-[1.5] text-text-muted">{side.description}</p> : null}
      <Passages passages={side.passages} />
    </div>
  )
}

function MergeBody({ shown }: { shown: MergeShown }) {
  const score = shown.score === null ? 'no score recorded' : `score ${shown.score.toFixed(2)}`
  return (
    <>
      <p className="text-[13px] text-text">
        Were these one thing? Merged {stamp(shown.merged_at)}, {score}, decided by {shown.decided_by};{' '}
        {shown.moved_edges} {shown.moved_edges === 1 ? 'link' : 'links'} moved.
      </p>
      <Signals signals={shown.signals} />
      <div className="grid gap-5 md:grid-cols-2">
        <Side side={shown.source} role="Merged away" />
        <Side side={shown.target} role="Into" />
      </div>
    </>
  )
}

function MatchBody({ shown }: { shown: MatchShown }) {
  const m = shown.mention
  return (
    <>
      <p className="text-[13px] text-text">
        Is this the same thing? A run read this name and joined it to an existing node {stamp(shown.matched_at)}, score{' '}
        {shown.score.toFixed(2)}.
      </p>
      <Signals signals={shown.signals} />
      <div className="grid gap-5 md:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-2">
          <span className={LABEL}>Name read</span>
          <span className="text-[14px] font-semibold leading-snug text-text">{m.name}</span>
          <span className="font-mono text-[10.5px] text-text-faint">
            {(m.node_type ?? 'unknown').replaceAll('_', ' ')}
            {m.jurisdiction ? ` · ${m.jurisdiction}` : ''}
          </span>
          <Passages passages={m.passages} />
        </div>
        <Side side={shown.node} role="Joined to" />
      </div>
    </>
  )
}

function EdgeBody({ sample }: { sample: AuditSample & { shown: EdgeShown } }) {
  const s = sample.shown
  const qualifiers = [
    s.stance ? `stance ${s.stance}` : null,
    s.certainty ? `certainty ${s.certainty}` : null,
    s.similarity_dimension ? `alike in ${s.similarity_dimension}` : null,
    s.disanalogy ? `unlike in ${s.disanalogy}` : null,
    s.valid_from || s.valid_to ? `held ${s.valid_from ?? '…'} to ${s.valid_to ?? '…'}` : null,
  ].filter(Boolean)
  return (
    <>
      <p className="text-[14px] leading-snug text-text">
        <NodeLink id={s.from.entity_id} name={s.from.name} />{' '}
        <span className="font-mono text-[12px] text-accent-graph">{s.relation.replaceAll('_', ' ')}</span>{' '}
        <NodeLink id={s.to.entity_id} name={s.to.name} />
      </p>
      {qualifiers.length ? (
        <span className="font-mono text-[10.5px] text-text-faint">{qualifiers.join(' · ')}</span>
      ) : null}
      <span className={LABEL}>
        Does the passage say this? {s.chunk_ids.length} {s.chunk_ids.length === 1 ? 'passage' : 'passages'} cited
      </span>
      <Passages passages={s.passages} />
    </>
  )
}

function SampleCard({
  sample,
  onGrade,
}: {
  sample: AuditSample
  onGrade: (sample: AuditSample, verdict: AuditVerdict) => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const edit = useEdit()

  async function choose(verdict: AuditVerdict) {
    setBusy(true)
    setFailed(null)
    try {
      await onGrade(sample, verdict)
    } catch (cause) {
      setFailed(cause instanceof ApiError ? cause.message : 'That did not go through.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="flex flex-col gap-3 px-[18px] py-4">
      <span className={LABEL}>
        {sample.position + 1}. {KIND_LABEL[sample.kind].replace(/^./, (c) => c.toUpperCase())}
      </span>
      {isEdgeShown(sample) ? (
        <EdgeBody sample={sample} />
      ) : isMatchShown(sample) ? (
        <MatchBody shown={sample.shown} />
      ) : (
        <MergeBody shown={sample.shown as MergeShown} />
      )}
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={`Grade item ${sample.position + 1}`}>
        {VERDICTS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            aria-pressed={sample.grade === key}
            className={sample.grade === key ? BUTTON_PRIMARY : BUTTON_SECONDARY}
            {...edit(busy)}
            onClick={() => void choose(key)}
          >
            {label}
          </button>
        ))}
        {sample.grade ? (
          <span role="status" className="font-mono text-[11px] text-accent-graph">
            Graded {sample.grade}
            {sample.graded_at ? ` ${stamp(sample.graded_at)}` : ''}.
          </span>
        ) : (
          <span className="font-mono text-[10.5px] text-text-faint">a grade changes nothing in the graph</span>
        )}
        {failed ? (
          <p role="alert" className="w-full font-mono text-[11px] text-accent-attention">
            Refused: {failed}
          </p>
        ) : null}
      </div>
    </Card>
  )
}

function DrawsTable({
  draws,
  selected,
  onSelect,
}: {
  draws: readonly AuditDraw[]
  selected: number | null
  onSelect: (drawId: number) => void
}) {
  return (
    <TableCard>
      <thead>
        <tr>
          <th className={TH}>Drawn</th>
          <th className={TH}>Window</th>
          <th className={TH}>Relations</th>
          <th className={TH}>Merges</th>
          <th className={TH}>Matches</th>
          <th className={TH}>
            <span className="sr-only">Open</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {draws.map((d) => (
          <tr key={d.draw_id} className={ROW} data-draw={d.draw_id} aria-current={d.draw_id === selected || undefined}>
            <td className={`${TDM} whitespace-nowrap`}>{stamp(d.drawn_at)}</td>
            <td className={`${TDM} whitespace-nowrap text-text-muted`}>
              {d.window_start.slice(0, 10)} – {d.window_end.slice(0, 10)}
            </td>
            <td className={TD}>
              <span className="font-mono text-[11.5px] tabular-nums">{precisionText(d.edges)}</span>
              <div className="text-[11.5px] text-text-faint">{gradedText(d.edges, d.edge_population)}</div>
            </td>
            <td className={TD}>
              <span className="font-mono text-[11.5px] tabular-nums">{precisionText(d.merges)}</span>
              <div className="text-[11.5px] text-text-faint">
                {gradedText(d.merges, d.merge_population)}
                {d.merges_reversed ? ` · ${d.merges_reversed} reversed already` : ''}
              </div>
            </td>
            <td className={TD}>
              <span className="font-mono text-[11.5px] tabular-nums">{precisionText(d.matches)}</span>
              <div className="text-[11.5px] text-text-faint">{gradedText(d.matches, d.match_population)}</div>
            </td>
            <td className={TD}>
              <button type="button" data-view className={BUTTON_ROW} onClick={() => onSelect(d.draw_id)}>
                {d.draw_id === selected ? 'Open' : 'Grade'}
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </TableCard>
  )
}

function ByProducer({ draw }: { draw: AuditDraw }) {
  const rows = draw.by_producer.filter((p) => p.tally.graded > 0)
  if (rows.length === 0) return null
  return (
    <>
      <SubHeading>By who wrote it</SubHeading>
      <TableCard>
        <thead>
          <tr>
            <th className={TH}>Kind</th>
            <th className={TH}>Agent</th>
            <th className={TH}>Model</th>
            <th className={TH}>Precision</th>
            <th className={TH}>Graded</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={`${p.kind}-${p.produced_by}-${p.model}`} className={ROW}>
              <td className={TD}>{KIND_LABEL[p.kind]}</td>
              <td className={`${TDM} text-text-muted`}>{p.produced_by ?? '—'}</td>
              <td className={`${TDM} text-text-muted`}>{p.model ?? '—'}</td>
              <td className={TDM}>{precisionText(p.tally)}</td>
              <td className={TDM}>
                {p.tally.graded} of {p.tally.drawn}
              </td>
            </tr>
          ))}
        </tbody>
      </TableCard>
    </>
  )
}

type Show = 'ungraded' | 'all'

export function GraphAuditPanel({
  initial,
  initialDraw,
  load = getGraphAudit,
  loadDraw = getAuditDraw,
  grade = gradeAuditSample,
}: GraphAuditPanelProps) {
  const [audit, setAudit] = useState<GraphAudit | null>(initial ?? null)
  const [selected, setSelected] = useState<number | null>(
    initialDraw?.draw.draw_id ?? initial?.draws[0]?.draw_id ?? null,
  )
  const [detail, setDetail] = useState<AuditDrawDetail | null>(initialDraw ?? null)
  const [show, setShow] = useState<Show>('ungraded')
  const [error, setError] = useState<string | null>(null)
  // Graded while this page is open: kept under "To grade" so a slip can be corrected.
  const [gradedHere, setGradedHere] = useState<ReadonlySet<number>>(new Set())

  const refresh = useCallback(
    () =>
      load()
        .then((body) => {
          setAudit(body)
          setSelected((current) => current ?? body.draws[0]?.draw_id ?? null)
        })
        .catch(() => setError('The graph audit could not be loaded.')),
    [load],
  )

  useEffect(() => {
    if (!initial) void refresh()
  }, [initial, refresh])

  useEffect(() => {
    if (selected === null || detail?.draw.draw_id === selected) return
    let live = true
    loadDraw(selected)
      .then((body) => {
        if (live) setDetail(body)
      })
      .catch(() => setError('That draw could not be loaded.'))
    return () => {
      live = false
    }
    // `detail` is left out on purpose: a grade replaces it, and that must not reload the draw.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadDraw, selected])

  async function onGrade(sample: AuditSample, verdict: AuditVerdict) {
    const graded = await grade(sample.sample_id, verdict)
    setGradedHere((seen) => new Set(seen).add(graded.sample_id))
    setDetail((d) => (d ? { ...d, samples: d.samples.map((s) => (s.sample_id === graded.sample_id ? graded : s)) } : d))
    void refresh()
  }

  if (audit === null) {
    return error ? (
      <p role="alert" className="text-[12.5px] text-accent-attention">
        {error}
      </p>
    ) : (
      <p className="text-[12.5px] text-text-muted">Loading the graph audit.</p>
    )
  }

  const current = audit.draws.find((d) => d.draw_id === selected) ?? null
  const samples = detail && detail.draw.draw_id === selected ? detail.samples : []
  const waiting = samples.filter((s) => s.grade === null)
  const listed = show === 'ungraded' ? samples.filter((s) => s.grade === null || gradedHere.has(s.sample_id)) : samples

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Graph audit">
        A random sample of the relations, merges and name matches made each month, for a person to grade: how many are
        wrong is the number anything written from the graph rests on. A grade changes nothing in the graph.{' '}
        {audit.ungraded.toLocaleString()} waiting to be graded.
      </PageHeader>
      {error ? (
        <p role="alert" className="font-mono text-[11px] text-accent-attention">
          {error}
        </p>
      ) : null}

      {audit.draws.length === 0 ? (
        <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
          Nothing drawn yet. The scheduler draws once a month, from what was written since the last draw.
        </p>
      ) : (
        <>
          <SubHeading>Precision over time</SubHeading>
          <DrawsTable draws={audit.draws} selected={selected} onSelect={setSelected} />
          {current ? (
            <>
              <ByProducer draw={current} />
              <SubHeading
                action={
                  <Filters<Show>
                    options={[
                      { key: 'ungraded', label: 'To grade', count: waiting.length },
                      { key: 'all', label: 'All', count: samples.length },
                    ]}
                    value={show}
                    onChange={setShow}
                  />
                }
              >
                Drawn {stamp(current.drawn_at)}
              </SubHeading>
              {listed.length === 0 ? (
                <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
                  {samples.length === 0 ? 'Loading this draw.' : 'Every item in this draw is graded.'}
                </p>
              ) : (
                listed.map((sample) => <SampleCard key={sample.sample_id} sample={sample} onGrade={onGrade} />)
              )}
            </>
          ) : null}
        </>
      )}
    </section>
  )
}
