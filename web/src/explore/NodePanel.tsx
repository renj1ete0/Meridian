import { useState } from 'react'

import type { GraphNodeDetail, ContestedPair, Evidence } from './graph/api'
import type { NodeAttribute, NoteDraft, SearchHit } from '../lib/api'
import { readable } from '../lib/readable'
import { hrefForNode, hrefForSource, onInternalClick } from '../lib/route'
import { DAGGER } from '../ui/Contested'
import { TIER_LABEL, type SourceTier } from '../ui/Tier'
import { dayOf } from '../lib/time'

/**
 * The node panel beside the canvas (tasks P6-04, P6-01; spec §12.5; design
 * `Explore`).
 *
 * §12.5 asks for "description, attribute tags with confidence, supporting
 * chunks with source and tier, contested edges, own annotations", and the
 * artboard gives each its block, in that order, over one opaque surface —
 * opaque because this is what a reader reads (design-system.md §5): citations,
 * provenance and confidence values do not go on glass over a live graph.
 *
 * Three rules from the older panel survive the redesign because they are about
 * honesty rather than layout:
 *
 * - **Confidence is on the chip**, as a number. §7 makes it first-class, and a
 *   confidence a reader has to hover for is a claim rendered as a fact.
 * - **A tag with no evidence says so.** §2 principle 3: nothing is assertable
 *   without a citation, so an empty citation list is a data problem to show.
 * - **Nothing is invented to fill a block.** No description, no annotation, no
 *   contested pair: the block is absent or states the absence. The artboard's
 *   sample prose is sample data, not a template to pad with.
 */

export const LBL =
  'font-mono text-[9px] font-medium uppercase tracking-[0.15em] text-text-faint'

/** How many passages the panel opens with; the rest are one click away. */
export const EVIDENCE_VISIBLE = 4

export function formatConfidence(value: number | null): string {
  // Two decimals, as the artboard shows: a probability, not a word. Rounding
  // to "high" throws away the difference between 0.61 and 0.94.
  return value === null ? '—' : value.toFixed(2)
}

/** At or above this a confidence reads in the accent; below, it is faint. */
export const CONFIDENT = 0.5

export function domainOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** `YYYY-MM`. Dates stay strings (see `lib/api.ts`) — no timezone to shift them. */
export function monthOf(date: string | null): string | null {
  return date ? date.slice(0, 7) : null
}

export function excerpt(text: string, max = 240): string {
  const flat = readable(text).replace(/\s+/g, ' ').trim()
  if (flat.length <= max) return flat
  const cut = flat.slice(0, max)
  return `${cut.slice(0, cut.lastIndexOf(' ') > max * 0.6 ? cut.lastIndexOf(' ') : max)}…`
}

/** `Intervention · walkability · SG` — type, topics, jurisdiction, as known. */
export function metaLine(detail: GraphNodeDetail): string {
  const parts = [detail.entity.node_type.replaceAll('_', ' ')]
  parts.push(...detail.home_topics)
  if (detail.entity.jurisdiction) parts.push(detail.entity.jurisdiction)
  if (detail.entity.is_annotation) parts.push('yours')
  return parts.join(' · ')
}

/**
 * A plain-text citation for the node. The node's own URL is the anchor —
 * `/nodes/{id}` is addressable, which is what makes citing it possible.
 */
export function citationFor(detail: GraphNodeDetail, origin: string): string {
  const e = detail.entity
  const bits = [`${e.canonical_name} (${e.node_type.replaceAll('_', ' ')}`]
  if (e.jurisdiction) bits.push(`, ${e.jurisdiction}`)
  bits.push(`). Meridian node #${e.entity_id}. ${origin}${hrefForNode(e.entity_id)}`)
  return bits.join('')
}

function Chip({ attribute }: { attribute: NodeAttribute }) {
  const value = attribute.value ?? (attribute.value_numeric !== null ? String(attribute.value_numeric) : null)
  const strong = attribute.confidence !== null && attribute.confidence >= CONFIDENT
  return (
    <li
      className="border border-line bg-surface-raised px-2 py-1 font-mono text-[10px] text-text-muted"
      title={
        attribute.scope === 'topic_local' && attribute.topic
          ? `Only meaningful within ${attribute.topic}.`
          : 'Compared across the corpus.'
      }
    >
      {attribute.name.replaceAll('_', ' ')}
      {value ? <span className="text-text"> {value}</span> : null}{' '}
      <span className={strong ? 'text-accent-graph' : 'text-text-faint'}>
        {formatConfidence(attribute.confidence)}
      </span>
      {attribute.supporting_chunk_ids.length === 0 ? (
        <span className="text-accent-attention"> · no evidence</span>
      ) : null}
    </li>
  )
}

export function SourceLine({ hit, certainty }: { hit: SearchHit; certainty?: string | null }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <a
        href={hrefForSource(hit.source_id)}
        onClick={onInternalClick(hrefForSource(hit.source_id))}
        className="font-mono text-[10px] text-accent-graph"
      >
        {domainOf(hit.url)}
      </a>
      <span className="border border-line px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.1em] text-text-faint">
        {TIER_LABEL[hit.source_tier as SourceTier] ?? hit.source_tier}
      </span>
      {hit.publication_date ? (
        <span className="font-mono text-[10px] text-text-faint">{monthOf(hit.publication_date)}</span>
      ) : (
        <span className="font-mono text-[10px] text-text-faint">undated</span>
      )}
      {certainty ? (
        <span
          className={`ml-auto font-mono text-[10px] ${
            certainty === 'hedged' ? 'text-accent-attention' : 'text-text-faint'
          }`}
        >
          {certainty}
        </span>
      ) : null}
    </div>
  )
}

function Passage({ evidence }: { evidence: Evidence }) {
  return (
    <div className="flex flex-col gap-1.5">
      <blockquote className="font-mono text-[11.5px] italic leading-[1.6] text-text-muted">
        “{excerpt(evidence.hit.text)}”
      </blockquote>
      <SourceLine hit={evidence.hit} certainty={evidence.certainty} />
    </div>
  )
}

function otherSide(pair: ContestedPair, here: number) {
  const t = pair.theirs
  return t.from_entity_id === here
    ? { id: t.to_entity_id, name: t.to_name }
    : { id: t.from_entity_id, name: t.from_name }
}

function ContestedBlock({ pair, here }: { pair: ContestedPair; here: number }) {
  const [comparing, setComparing] = useState(false)
  const other = otherSide(pair, here)
  const theirs = pair.theirs.evidence
  const ours = pair.ours.evidence
  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-[13px] leading-[1.55] text-text">
        <a
          href={hrefForNode(other.id)}
          onClick={onInternalClick(hrefForNode(other.id))}
          className="text-text underline decoration-line-strong underline-offset-2"
        >
          {other.name}
        </a>
        <span className="text-text-muted">
          {' '}
          — {pair.theirs.relation_type.replaceAll('_', ' ')}
          {pair.theirs.stance ? `, ${pair.theirs.stance}` : ''}
          {theirs ? `: “${excerpt(theirs.text, 140)}”` : '.'}
        </span>
      </p>
      <div className="flex items-center gap-2">
        {theirs ? (
          <span className="font-mono text-[10px] text-text-faint">
            {domainOf(theirs.url)} · {(TIER_LABEL[theirs.source_tier as SourceTier] ?? theirs.source_tier).toLowerCase()}
            {theirs.publication_date ? ` · ${monthOf(theirs.publication_date)}` : ''}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => setComparing((v) => !v)}
          aria-expanded={comparing}
          className="ml-auto font-mono text-[10.5px] text-accent-attention"
        >
          {comparing ? 'Close ←' : 'Compare →'}
        </button>
      </div>
      {comparing ? (
        // §9: both edges are kept, and both are shown. Side by side, this
        // node's evidence first, because the reader arrived from here.
        <div className="grid grid-cols-2 gap-3 border-t border-accent-attention-deep pt-3">
          {[
            { side: pair.ours, hit: ours, label: 'This concept' },
            { side: pair.theirs, hit: theirs, label: other.name },
          ].map(({ side, hit, label }) => (
            <div key={side.edge_id} className="flex min-w-0 flex-col gap-1.5">
              <span className={LBL}>{label}</span>
              <span className="font-mono text-[10px] text-text-faint">
                {side.relation_type.replaceAll('_', ' ')}
                {side.certainty ? ` · ${side.certainty}` : ''}
              </span>
              {hit ? (
                <>
                  <blockquote className="font-mono text-[11px] italic leading-[1.55] text-text-muted">
                    “{excerpt(hit.text, 200)}”
                  </blockquote>
                  <SourceLine hit={hit} />
                </>
              ) : (
                <span className="text-[12px] text-text-faint">No passage backs this link.</span>
              )}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

export interface NodePanelProps {
  detail: GraphNodeDetail
  /** Expand neighbours: shows the next batch past the cap. */
  onExpand?: () => void
  /** Why Expand is unavailable, when it is — e.g. every neighbour is shown. */
  expandBlocked?: string | null
  onWrite?: (draft: NoteDraft) => void
  writing?: boolean
  writeError?: string | null
  origin?: string
}

export function NodePanel({
  detail,
  onExpand,
  expandBlocked = null,
  onWrite,
  writing = false,
  writeError = null,
  origin = typeof window === 'undefined' ? '' : window.location.origin,
}: NodePanelProps) {
  const [allEvidence, setAllEvidence] = useState(false)
  const [composing, setComposing] = useState(false)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [cited, setCited] = useState<'idle' | 'copied' | 'failed'>('idle')
  const entity = detail.entity
  const evidence = allEvidence ? detail.evidence : detail.evidence.slice(0, EVIDENCE_VISIBLE)
  const latest = detail.annotations[0] ?? null

  function cite() {
    const text = citationFor(detail, origin)
    const done = (state: 'copied' | 'failed') => {
      setCited(state)
      window.setTimeout(() => setCited('idle'), 2000)
    }
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(
        () => done('copied'),
        () => done('failed'),
      )
    } else {
      done('failed')
    }
  }

  return (
    <aside
      aria-label={`Node: ${entity.canonical_name}`}
      className="order-2 flex w-full shrink-0 flex-col border-t border-line bg-surface lg:order-none lg:h-full lg:min-h-0 lg:w-[384px] lg:border-l lg:border-t-0"
    >
      <div className="min-h-0 flex-1 overflow-y-auto">
        <header className="relative flex flex-col gap-3 border-b border-line px-5 pb-4 pt-5">
          {detail.contested ? (
            // §6: the detail-panel badge — leading dagger, nbsp, the word.
            <span className="absolute right-5 top-5 border border-accent-attention-deep bg-accent-attention-deep/40 px-2 py-1 font-mono text-[9.5px] uppercase tracking-[0.1em] text-accent-attention">
              {DAGGER}
              {'\u00a0 '}Contested
            </span>
          ) : null}
          <div className="flex min-w-0 flex-col gap-1.5">
            <h1
              className={`text-[21px] font-semibold leading-[1.15] tracking-[-0.006em] text-text ${
                detail.contested ? 'pr-[112px]' : ''
              }`}
            >
              {entity.canonical_name}
            </h1>
            <p className="font-mono text-[9.5px] uppercase tracking-[0.15em] text-text-faint">
              {metaLine(detail)}
            </p>
            {entity.aliases && entity.aliases.length > 0 ? (
              <p className="font-mono text-[10.5px] text-text-faint">
                also {entity.aliases.join(' · ')}
              </p>
            ) : null}
          </div>
          {entity.description ? (
            <p className="text-[13.5px] leading-[1.6] text-text-muted">{entity.description}</p>
          ) : null}
        </header>

        <section className="flex flex-col gap-3 border-b border-line px-5 py-4">
          <h2 className={LBL}>Properties</h2>
          {detail.attributes.length === 0 ? (
            <p className="text-[12.5px] text-text-faint">
              None recorded yet. Properties are read from sources over time, so a concept can
              have none.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {detail.attributes.map((a) => (
                <Chip key={a.value_id} attribute={a} />
              ))}
            </ul>
          )}
        </section>

        <section className="flex flex-col gap-3 border-b border-line px-5 py-4">
          <div className="flex items-baseline justify-between">
            <h2 className={LBL}>Passages that mention it</h2>
            <span className="font-mono text-[10px] text-text-faint">{detail.evidence_total}</span>
          </div>
          {detail.evidence.length === 0 ? (
            <p className="text-[12.5px] text-text-faint">
              No passage cites this concept yet, and it has no links or properties to carry
              evidence either.
            </p>
          ) : (
            <>
              {evidence.map((e, i) => (
                <div key={e.hit.chunk_id} className="flex flex-col gap-3">
                  {i > 0 ? <div className="h-px bg-line" /> : null}
                  <Passage evidence={e} />
                </div>
              ))}
              {detail.evidence.length > EVIDENCE_VISIBLE ? (
                <button
                  type="button"
                  onClick={() => setAllEvidence((v) => !v)}
                  className="self-start font-mono text-[10.5px] text-accent-graph"
                >
                  {allEvidence ? 'Show fewer' : `Show all ${detail.evidence.length}`}
                </button>
              ) : null}
              {detail.evidence_total > detail.evidence.length ? (
                <p className="font-mono text-[10px] text-text-faint">
                  {detail.evidence_total - detail.evidence.length} more not carried by this panel.
                </p>
              ) : null}
            </>
          )}
        </section>

        {detail.contested_with.length > 0 ? (
          <section className="flex flex-col gap-2.5 border-b border-line bg-accent-attention-deep/15 px-5 py-4">
            <h2 className={`${LBL} !text-accent-attention`}>Contested with</h2>
            {detail.contested_with.map((pair) => (
              <ContestedBlock
                key={`${pair.ours.edge_id}:${pair.theirs.edge_id}`}
                pair={pair}
                here={entity.entity_id}
              />
            ))}
          </section>
        ) : null}

        <section className="flex flex-col gap-2.5 px-5 py-3.5">
          <div className="flex items-baseline justify-between">
            <h2 className={LBL}>My note</h2>
            {latest?.produced_at ? (
              <span className="font-mono text-[10px] text-text-faint">{dayOf(latest.produced_at)}</span>
            ) : null}
          </div>
          {latest ? (
            <div className="border border-line bg-ground px-3 py-2.5 text-[12.5px] leading-[1.55] text-text-muted">
              <p className="font-semibold text-text">{latest.title}</p>
              {latest.body ? <p className="mt-1 whitespace-pre-line">{latest.body}</p> : null}
              {detail.annotations.length > 1 ? (
                <p className="mt-2 font-mono text-[10px] text-text-faint">
                  {detail.annotations.length - 1} earlier note
                  {detail.annotations.length === 2 ? '' : 's'} on this concept.
                </p>
              ) : null}
            </div>
          ) : !composing ? (
            <p className="text-[12.5px] text-text-faint">
              No note on this concept. Yours is the one layer here nothing else can write.
            </p>
          ) : null}
          {composing ? (
            <form
              className="flex flex-col gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                const trimmed = title.trim()
                if (!trimmed) return
                onWrite?.({
                  title: trimmed,
                  body: body.trim() || null,
                  about: [entity.entity_id],
                  supporting_chunk_ids: [],
                })
                setTitle('')
                setBody('')
                setComposing(false)
              }}
            >
              <input
                aria-label="Title for this note"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="What is this?"
                disabled={writing}
                className="h-8 border border-line-strong bg-ground px-2 text-[12.5px] text-text"
              />
              <textarea
                aria-label="The note itself"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={3}
                disabled={writing}
                className="border border-line-strong bg-ground px-2 py-1 text-[12.5px] text-text"
              />
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={writing || !title.trim()}
                  className="border border-line-strong bg-surface-raised px-3 py-1.5 text-[12px] text-text"
                >
                  Keep it
                </button>
                <button
                  type="button"
                  onClick={() => setComposing(false)}
                  className="px-2 py-1.5 text-[12px] text-text-faint"
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : null}
          {writeError ? <p className="text-[12px] text-accent-attention">{writeError}</p> : null}
        </section>
      </div>

      {/* Right padding keeps the actions clear of the question toggle, which
          sits over this corner (`B-125`). */}
      <footer className="flex flex-wrap items-center gap-2 border-t border-line py-3.5 pl-5 pr-[72px]">
        <button
          type="button"
          onClick={onExpand}
          disabled={!onExpand || Boolean(expandBlocked)}
          title={expandBlocked ?? undefined}
          className="whitespace-nowrap border border-accent-graph bg-accent-graph px-3 py-2 text-[12.5px] font-medium text-surface disabled:opacity-50"
        >
          Expand neighbours
        </button>
        <button
          type="button"
          onClick={() => setComposing(true)}
          disabled={!onWrite}
          className="border border-line-strong bg-surface-raised px-3 py-2 text-[12.5px] text-text-muted"
        >
          Annotate
        </button>
        <button
          type="button"
          onClick={cite}
          className="border border-line-strong bg-surface-raised px-3 py-2 text-[12.5px] text-text-muted"
        >
          {cited === 'copied' ? 'Copied' : cited === 'failed' ? 'Not copied' : 'Cite'}
        </button>
        <span className="ml-auto font-mono text-[10px] text-text-faint">node #{entity.entity_id}</span>
      </footer>
    </aside>
  )
}
