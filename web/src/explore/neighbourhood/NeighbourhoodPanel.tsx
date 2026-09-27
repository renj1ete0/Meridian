import { hrefForNode, hrefForSource, onInternalClick } from '../../lib/route'
import { TierChip } from '../../ui/Tier'
import type { CitedTerm, Relation, SimilarBasis, SimilarTerm, Term, TermNeighbourhood } from './api'

/**
 * The neighbourhood panel beside Find's results (task P6-33; design canvas,
 * "Find · Term neighbourhood").
 *
 * **Two rings, drawn and listed apart.** The inner ring is what a passage
 * states a link to — solid cyan spokes, as the canvas draws an edge from the
 * focus. The outer ring is what merely reads alike — dashed, fainter, smaller
 * nodes, and a separate list whose heading says "no stated link". Nothing on
 * this panel puts the two in one list, because resemblance shown beside a
 * citation reads as a second citation.
 *
 * **Sparse is stated, not hidden.** The graph is small and most terms have no
 * stated links yet; the panel says so in words and still shows the outer ring,
 * which is what exists. When no vector was available the outer ring was not
 * measured at all, and the panel says that rather than drawing an empty ring
 * that would read as "nothing is similar".
 *
 * The ring diagram is the canvas's ground and palette in both themes, like the
 * graph workspace: it is a picture of the graph, and the graph is drawn dark.
 */

export interface NeighbourhoodPanelProps {
  state:
    { phase: 'loading' } | { phase: 'failed'; message: string } | { phase: 'done'; data: TermNeighbourhood }
  /** Chunk ids already on screen in the result list, so they are not repeated. */
  shownChunkIds?: ReadonlySet<number>
  /** The reader picked a node for a term that named none. */
  onPick?: (term: Term) => void
}

const LABEL = 'font-mono text-[9.5px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint'
const META = 'font-mono text-[10.5px] text-text-faint'
/** Rules between similar rows are dashed, like the outer ring's spokes. */
const DASHED = { borderTopStyle: 'dashed' } as const

/** How many nodes each ring draws; the lists below carry the rest. */
export const DRAWN_CITED = 5
export const DRAWN_SIMILAR = 6

export interface RingPoint {
  x: number
  y: number
  /** Degrees clockwise from straight up. */
  angle: number
}

/**
 * Points spread evenly over an arc of a ring, clockwise from `from` to `to`
 * degrees (0 = up). The top of each ring is left free for its caption, so a
 * node never sits on the word CITED or SIMILAR.
 */
export function arcPositions(
  count: number,
  radius: number,
  centre: { x: number; y: number },
  from = 40,
  to = 320,
): RingPoint[] {
  const step = (to - from) / Math.max(count, 1)
  return Array.from({ length: count }, (_, i) => {
    const angle = from + step * (i + 0.5)
    const rad = (angle * Math.PI) / 180
    return {
      // `|| 0` folds -0 into 0, so equal layouts compare equal.
      x: Math.round((centre.x + radius * Math.sin(rad)) * 10) / 10 || 0,
      y: Math.round((centre.y - radius * Math.cos(rad)) * 10) / 10 || 0,
      angle,
    }
  })
}

/** Where a node's label goes: outward from the centre, so labels do not cross spokes. */
export function labelAt(
  point: RingPoint,
  gap = 9,
): { x: number; y: number; anchor: 'start' | 'middle' | 'end' } {
  const side = Math.sin((point.angle * Math.PI) / 180)
  if (side > 0.25) return { x: point.x + gap, y: point.y + 4, anchor: 'start' }
  if (side < -0.25) return { x: point.x - gap, y: point.y + 4, anchor: 'end' }
  const below = Math.cos((point.angle * Math.PI) / 180) < 0
  return { x: point.x, y: below ? point.y + gap + 8 : point.y - gap, anchor: 'middle' }
}

/** A label short enough for the diagram; the list below has the whole name. */
export function shortLabel(name: string, max = 18): string {
  return name.length <= max ? name : `${name.slice(0, max - 1).trimEnd()}…`
}

/**
 * A name on up to two lines of about `width` characters (`B-100`), shortened
 * only past that: one line of fourteen left most names unreadable ("pavement
 * lifes…"), and the diagram has the room for a second.
 */
export function labelLines(name: string, width = 16): string[] {
  if (name.length <= width) return [name]
  const words = name.split(/\s+/)
  let first = ''
  let i = 0
  while (i < words.length && `${first} ${words[i]}`.trim().length <= width) first = `${first} ${words[i++]}`.trim()
  if (!first) return [shortLabel(name, width)]
  const rest = words.slice(i).join(' ')
  return rest ? [first, shortLabel(rest, width)] : [first]
}

/** `reduces →` when the anchor is the subject, `← obstructs` when it is the object. */
export function relationText(relation: Relation): string {
  const words = relation.relation_type.replace(/_/g, ' ')
  return relation.outgoing ? `${words} →` : `← ${words}`
}

export function basisText(basis: SimilarBasis): string {
  switch (basis) {
    case 'node':
      return 'Measured from the node’s name.'
    case 'term':
      return 'Measured from the term as typed.'
    default:
      return 'Not measured: no embedding was available for this term, so nothing here says what reads alike.'
  }
}

const W = 440
const H = 340
const CENTRE = { x: W / 2, y: H / 2 }
const INNER = 78
const OUTER = 130

const canvas = (token: string) => `var(--dark-${token})`

function Caption({ y, children }: { y: number; children: string }) {
  return (
    <text
      x={CENTRE.x}
      y={y}
      textAnchor="middle"
      fontFamily="IBM Plex Mono"
      fontSize="9"
      letterSpacing="1.5"
      fill={canvas('text-faint')}
    >
      {children}
    </text>
  )
}

function RingDiagram({ data }: { data: TermNeighbourhood }) {
  const cited = data.cited.slice(0, DRAWN_CITED)
  const similar = data.similar.slice(0, DRAWN_SIMILAR)
  const inner = arcPositions(cited.length, INNER, CENTRE)
  // A different arc, so a similar node does not sit directly behind a cited one.
  const outer = arcPositions(similar.length, OUTER, CENTRE, 55, 335)
  const focus = data.anchor?.canonical_name ?? data.term
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`${cited.length} stated links and ${similar.length} similar terms around ${focus}`}
      className="block h-auto w-full"
      style={{ background: canvas('ground-deep') }}
      data-role="rings"
    >
      <g fill="none" stroke={canvas('canvas-graticule')}>
        <circle cx={CENTRE.x} cy={CENTRE.y} r={INNER} />
        <circle cx={CENTRE.x} cy={CENTRE.y} r={OUTER} />
      </g>
      <Caption y={CENTRE.y - INNER + 4}>CITED</Caption>
      <Caption y={CENTRE.y - OUTER + 4}>SIMILAR</Caption>

      {similar.map((term, i) => {
        const at = outer[i]!
        const label = labelAt(at, 8)
        return (
          <g key={`s${term.entity_id}`} data-ring="similar">
            <line
              x1={CENTRE.x}
              y1={CENTRE.y}
              x2={at.x}
              y2={at.y}
              stroke={canvas('canvas-neighbour')}
              strokeOpacity="0.35"
              strokeDasharray="3 5"
            />
            <circle cx={at.x} cy={at.y} r="4.5" fill={canvas('canvas-neighbour')} fillOpacity="0.7" />
            <text
              x={label.x}
              y={label.y}
              textAnchor={label.anchor}
              fontFamily="Archivo"
              fontSize="11"
              fill={canvas('text-muted')}
              stroke={canvas('ground-deep')}
              strokeWidth="3.5"
              paintOrder="stroke"
            >
              {/* Few outer nodes leave room for longer names. */}
              {labelLines(term.canonical_name, similar.length <= 4 ? 20 : 15).map((line, n) => (
                <tspan key={n} x={label.x} dy={n === 0 ? 0 : 12}>
                  {line}
                </tspan>
              ))}
            </text>
            <title>{term.canonical_name}</title>
          </g>
        )
      })}

      {cited.map((term, i) => {
        const at = inner[i]!
        // Below the node, as the board draws the inner ring: outward would
        // run into the outer ring's nodes.
        const label = { x: at.x, y: at.y + 19, anchor: 'middle' as const }
        return (
          <g key={`c${term.entity_id}`} data-ring="cited">
            <line
              x1={CENTRE.x}
              y1={CENTRE.y}
              x2={at.x}
              y2={at.y}
              stroke={term.contested ? canvas('canvas-edge-contested') : canvas('accent-graph')}
              strokeOpacity="0.7"
              strokeWidth="1.6"
            />
            <circle
              cx={at.x}
              cy={at.y}
              r="6.5"
              fill={term.contested ? canvas('accent-attention') : canvas('canvas-neighbour')}
            />
            <text
              x={label.x}
              y={label.y}
              textAnchor={label.anchor}
              fontFamily="Archivo"
              fontSize="12"
              fill={canvas('text')}
              stroke={canvas('ground-deep')}
              strokeWidth="3.5"
              paintOrder="stroke"
            >
              {labelLines(term.canonical_name, 16).map((line, n) => (
                <tspan key={n} x={label.x} dy={n === 0 ? 0 : 13}>
                  {line}
                </tspan>
              ))}
              {term.contested ? (
                <tspan fontFamily="IBM Plex Mono" fontSize="10" dy="-4" fill={canvas('accent-attention')}>
                  †
                </tspan>
              ) : null}
            </text>
            <title>{term.canonical_name}</title>
          </g>
        )
      })}

      <circle
        cx={CENTRE.x}
        cy={CENTRE.y}
        r="22"
        fill="none"
        stroke={canvas('accent-graph')}
        strokeOpacity="0.45"
        strokeWidth="1.5"
      />
      <circle
        cx={CENTRE.x}
        cy={CENTRE.y}
        r="11"
        fill={data.anchor ? canvas('accent-graph') : 'none'}
        stroke={canvas('accent-graph')}
        strokeWidth="1.5"
      />
      {/* The focus is named in the panel's heading, not here: at this width a
          label at the centre lands on the inner ring's labels. */}
    </svg>
  )
}

function NodeLink({ term, className }: { term: Term; className: string }) {
  return (
    <a
      href={hrefForNode(term.entity_id)}
      onClick={onInternalClick(hrefForNode(term.entity_id))}
      className={className}
    >
      {term.canonical_name}
    </a>
  )
}

function CitedList({ cited, total }: { cited: CitedTerm[]; total: number }) {
  return (
    <ul className="flex flex-col" data-list="cited">
      {cited.map((term) => (
        <li key={term.entity_id} className="flex flex-col gap-0.5 border-t border-line py-2">
          <div className="flex items-baseline justify-between gap-3">
            <NodeLink
              term={term}
              className="min-w-0 text-[13px] font-medium leading-snug text-text hover:text-accent-graph"
            />
            <span className={`${META} shrink-0 tabular-nums`}>
              {term.support} {term.support === 1 ? 'passage' : 'passages'}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            {term.relations.map((relation) => (
              <span
                key={`${relation.relation_type}${relation.outgoing}`}
                className="font-mono text-[10.5px] text-accent-graph"
              >
                {relationText(relation)}
              </span>
            ))}
            {term.contested ? (
              <span className="border border-accent-attention-deep px-1 font-mono text-[9.5px] text-accent-attention">
                †&nbsp;Contested
              </span>
            ) : null}
          </div>
        </li>
      ))}
      {total > cited.length ? (
        <li className={`${META} border-t border-line pt-2`}>
          {cited.length} of {total} shown, most passages first
        </li>
      ) : null}
    </ul>
  )
}

function SimilarList({ similar, total }: { similar: SimilarTerm[]; total: number }) {
  return (
    <ul className="flex flex-col" data-list="similar">
      {similar.map((term) => (
        <li
          key={term.entity_id}
          className="flex items-baseline justify-between gap-3 border-t border-line py-1.5"
          style={DASHED}
        >
          <NodeLink
            term={term}
            className="min-w-0 text-[13px] leading-snug text-text-muted hover:text-accent-graph"
          />
          <span className={`${META} shrink-0 tabular-nums`}>{term.similarity.toFixed(2)}</span>
        </li>
      ))}
      {total > similar.length ? (
        <li className={`${META} border-t border-line pt-2`} style={DASHED}>
          {similar.length} of {total} shown
        </li>
      ) : null}
    </ul>
  )
}

export function NeighbourhoodPanel({ state, shownChunkIds, onPick }: NeighbourhoodPanelProps) {
  return (
    <aside
      aria-label="Neighbourhood"
      className="flex flex-col gap-4 border border-line bg-surface p-4"
      data-role="neighbourhood"
    >
      <span className={LABEL}>Neighbourhood</span>
      {state.phase === 'loading' ? <p className={META}>Reading the neighbourhood.</p> : null}
      {state.phase === 'failed' ? (
        <p className="text-[13px] leading-[1.55] text-text-muted">{state.message}</p>
      ) : null}
      {state.phase === 'done' ? (
        <Body data={state.data} shownChunkIds={shownChunkIds} onPick={onPick} />
      ) : null}
    </aside>
  )
}

function Body({
  data,
  shownChunkIds,
  onPick,
}: {
  data: TermNeighbourhood
  shownChunkIds?: ReadonlySet<number>
  onPick?: (term: Term) => void
}) {
  const passages = data.passages.filter((p) => !shownChunkIds?.has(p.hit.chunk_id))
  const measured = data.similar_basis !== 'none'
  return (
    <>
      <div className="flex flex-col gap-1">
        {data.anchor ? (
          <>
            <NodeLink
              term={data.anchor}
              className="text-[15px] font-semibold leading-snug text-text hover:text-accent-graph"
            />
            <span className={META}>{data.anchor.node_type.replace(/_/g, ' ')}</span>
          </>
        ) : (
          <p className="text-[13px] leading-[1.55] text-text-muted">
            No node is named “{data.term}”, so nothing can be stated about it yet.
          </p>
        )}
      </div>

      {!data.anchor && data.candidates.length > 0 ? (
        <div className="flex flex-col gap-2">
          <span className={LABEL}>
            {/\s/.test(data.term.trim()) ? 'Nodes its words name' : 'Nodes with that in their name'}
          </span>
          <div className="flex flex-wrap gap-1.5">
            {data.candidates.map((term) => (
              <button
                key={term.entity_id}
                type="button"
                onClick={() => onPick?.(term)}
                className="rounded-[3px] border border-line-strong bg-surface-raised px-2 py-0.5 text-[12px] text-text hover:border-accent-graph"
              >
                {term.canonical_name}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <p className={META}>inner ring: stated in a passage · outer ring: near in meaning, no stated link</p>
      {/* Two empty circles would be a picture of nothing; the lists below
          say in words that both rings are empty. */}
      {data.cited.length + data.similar.length > 0 ? <RingDiagram data={data} /> : null}

      <section className="flex flex-col gap-1" aria-label="Stated in a passage">
        <h3 className={LABEL}>
          Stated in a passage
          {data.cited_total > 0 ? ` · ${data.cited_total}` : ''}
        </h3>
        {data.cited.length > 0 ? (
          <CitedList cited={data.cited} total={data.cited_total} />
        ) : (
          <p className="text-[13px] leading-[1.55] text-text-muted">
            {data.anchor
              ? data.similar.length > 0
                ? 'No passage states a link to this yet. What reads alike is below.'
                : 'No passage states a link to this yet.'
              : 'Nothing to list: links are stated between nodes.'}
          </p>
        )}
      </section>

      <section className="flex flex-col gap-1" aria-label="Near in meaning">
        <h3 className={LABEL}>
          Near in meaning, no stated link
          {data.similar_total > 0 ? ` · ${data.similar_total}` : ''}
        </h3>
        {measured && data.similar.length > 0 ? (
          <SimilarList similar={data.similar} total={data.similar_total} />
        ) : null}
        {measured && data.similar.length === 0 ? (
          <p className="text-[13px] leading-[1.55] text-text-muted">
            No node reads alike above {data.similar_floor.toFixed(2)}.
          </p>
        ) : null}
        <p className={META}>{basisText(data.similar_basis)}</p>
      </section>

      {measured && passages.length > 0 ? (
        <section className="flex flex-col gap-1" aria-label="Passages near in meaning">
          <h3 className={LABEL}>Passages near in meaning, not in the results</h3>
          <ul className="flex flex-col">
            {passages.map(({ hit, similarity }) => (
              <li
                key={hit.chunk_id}
                className="flex flex-col gap-1.5 border-t border-line py-2.5"
                style={DASHED}
              >
                <p className="line-clamp-3 text-[12.5px] italic leading-[1.55] text-text/85">{hit.text}</p>
                <div className="flex flex-wrap items-center gap-2">
                  <TierChip tier={hit.source_tier} />
                  <span className={META}>{hit.publication_date ?? 'no date'}</span>
                  <span className={`${META} tabular-nums`}>{similarity.toFixed(2)}</span>
                  <a
                    href={hrefForSource(hit.source_id)}
                    onClick={onInternalClick(hrefForSource(hit.source_id))}
                    className="font-mono text-[10.5px] text-accent-graph hover:underline"
                  >
                    source →
                  </a>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  )
}
