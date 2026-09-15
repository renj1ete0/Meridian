import type { GazetteerEntityType, GazetteerRow, GazetteerState } from '../lib/api'

/**
 * The gazetteer approval queue (task P6-13, spec §5.6).
 *
 * §5.6 ends with "approve in the UI — a two-minute weekly task". This is that
 * screen, and the two minutes are the design constraint: a curator arrives with
 * a list of terms a regex proposed and leaves with each one decided.
 *
 * **Every row says whether the matcher will actually load it.** That is the
 * unusual part, and the reason it is here: an approved term whose surface form
 * another row already claims is withheld, so it can read approved and never
 * match anything in any document. Nothing else in the system reports that —
 * extraction runs, the row looks right, and the term is simply absent. The
 * server computes the verdict against the whole approved set and it is rendered
 * beside the decision that caused it.
 *
 * **Approve and reject are not coloured.** §2 of the design system: the palette
 * has no green and no red, and colour must not imply a verdict. A green approve
 * button would also make rejection read as the damaging option, when rejecting
 * a bad term is the whole point of the screen.
 */

export interface GazetteerQueueProps {
  rows: readonly GazetteerRow[]
  state: GazetteerState
  counts: { pending: number; approved: number; rejected: number }
  busy?: number | null
  onState?: (state: GazetteerState) => void
  onDecide?: (termId: number, decision: 'approve' | 'reject' | 'restore') => void
  onEdit?: (termId: number, entityType: GazetteerEntityType) => void
}

const STATES: { key: GazetteerState; label: string }[] = [
  { key: 'pending', label: 'waiting' },
  { key: 'approved', label: 'approved' },
  { key: 'rejected', label: 'turned down' },
  { key: 'all', label: 'all' },
]

/**
 * One class for every decision button, which is the point rather than tidiness.
 *
 * §2 of the design system: the palette has no green and no red, and colour must
 * not imply a verdict. Approve and turn-down therefore have to be *identical* —
 * not merely both uncoloured, because any difference in weight reads as one
 * being the safe option, and here neither is.
 */
const DECISION =
  'h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 ' +
  'font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ' +
  'disabled:opacity-50'

export const ENTITY_TYPES: GazetteerEntityType[] = [
  'agency',
  'scheme',
  'infrastructure',
  'metric',
  'concept',
]

/**
 * What the matcher does with this row, in a sentence.
 *
 * Each reason gets a different fix, which is why the reason is named rather
 * than reduced to a boolean: a collision needs one of the two rows changed,
 * ambiguity is a decision to leave the mention to the resolver, and the other
 * two are just where the row is in the queue.
 */
export function verdictOf(row: GazetteerRow): string {
  if (row.will_load && !row.withheld_reason) return 'Matches documents.'
  if (row.withheld_reason === 'unapproved') return 'Not matching yet — waiting on this decision.'
  if (row.withheld_reason === 'rejected') return 'Turned down, so it matches nothing.'
  if (row.withheld_reason === 'ambiguous') {
    return row.will_load
      ? 'The full name matches. The short forms are held back for the resolver to decide.'
      : 'Held back for the resolver, which can read the rest of the document.'
  }
  if (row.withheld_reason === 'collision') {
    const others = row.collides_with.join(', ')
    return `Another term already claims this wording (${others}), so neither is used. Change one of them.`
  }
  if (row.withheld_reason === 'no_patterns') return 'Approved, but has no wording to match on.'
  return row.will_load ? 'Matches documents.' : 'Not matching.'
}

export function GazetteerQueue({
  rows,
  state,
  counts,
  busy = null,
  onState,
  onDecide,
  onEdit,
}: GazetteerQueueProps) {
  const shown: Record<GazetteerState, number> = {
    pending: counts.pending,
    approved: counts.approved,
    rejected: counts.rejected,
    all: counts.pending + counts.approved + counts.rejected,
  }

  return (
    <section>
      <h2 className="font-sans text-[length:var(--text-heading)] font-semibold">Gazetteer</h2>
      <p className="mt-2 max-w-prose text-[length:var(--text-small)] text-text-muted">
        Terms the crawl proposed by reading acronym definitions out of documents. An approved term
        takes precedence over the statistical model wherever it matches, so it is worth being sure.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {STATES.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => onState?.(key)}
            aria-pressed={state === key}
            className={`rounded-chip border px-2 py-0.5 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
              state === key
                ? 'border-accent-graph text-accent-graph'
                : 'border-line-strong text-text-muted'
            }`}
          >
            {/* Counts are unfiltered, so a queue reading "waiting (0)" while
                forty sit under another tab cannot happen. */}
            {label} ({shown[key]})
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="mt-6 text-text-muted">
          Nothing here. The harvest files terms as it reads documents, so an empty queue means
          every definition found so far has been decided.
        </p>
      ) : (
        <ul className="mt-6 space-y-4">
          {rows.map((row) => (
            <li key={row.term.term_id} className="border border-line bg-surface p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-sans text-[length:var(--text-body)] font-semibold">
                  {row.term.canonical}
                </h3>
                <span className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                  {/* Documents, not mentions. One report repeating a definition
                      forty times has said one thing forty times. */}
                  {row.term.occurrence_count === 1
                    ? '1 document'
                    : `${row.term.occurrence_count} documents`}
                </span>
              </div>

              {row.term.aliases && row.term.aliases.length > 0 ? (
                <p className="mt-1 font-mono text-[length:var(--text-small)] text-text-muted">
                  {row.term.aliases.join(' · ')}
                </p>
              ) : null}

              <p
                className={`mt-3 text-[length:var(--text-small)] ${
                  row.withheld_reason === 'collision' || row.withheld_reason === 'no_patterns'
                    ? 'text-accent-attention'
                    : 'text-text-muted'
                }`}
              >
                {verdictOf(row)}
              </p>

              <div className="mt-4 flex flex-wrap items-center gap-3">
                <label className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                  <span className="sr-only">Type for {row.term.canonical}</span>
                  <select
                    value={row.term.entity_type}
                    disabled={busy === row.term.term_id}
                    onChange={(event) =>
                      onEdit?.(row.term.term_id, event.target.value as GazetteerEntityType)
                    }
                    className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-2 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)]"
                  >
                    {/* A harvested term arrives as `concept` because a regex
                        cannot tell an agency from a metric. Correcting that is
                        most of the work of approving one. */}
                    {ENTITY_TYPES.map((type) => (
                      <option key={type} value={type}>
                        {type}
                      </option>
                    ))}
                  </select>
                </label>

                {row.term.rejected_at ? (
                  <button
                    type="button"
                    disabled={busy === row.term.term_id}
                    onClick={() => onDecide?.(row.term.term_id, 'restore')}
                    className={DECISION}
                  >
                    Put back
                  </button>
                ) : (
                  <>
                    <button
                      type="button"
                      disabled={busy === row.term.term_id || row.term.approved}
                      onClick={() => onDecide?.(row.term.term_id, 'approve')}
                      className={DECISION}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      disabled={busy === row.term.term_id}
                      onClick={() => onDecide?.(row.term.term_id, 'reject')}
                      className={DECISION}
                    >
                      Turn down
                    </button>
                  </>
                )}

                <span className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                  {row.term.source === 'auto_acronym' ? 'found in documents' : row.term.source}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
