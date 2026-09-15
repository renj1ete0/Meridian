import type { SteeringEntry, TopicRow, TopicStatus } from '../lib/api'

/**
 * Steering (task P6-12, spec §10, §10.1).
 *
 * §10's model in one line: attention is a weight vector over topics, and seeds
 * are drawn proportionally. This screen is where a person changes it, and the
 * design problem is that **the number you set is not always the number that
 * applies**. A floor lifts a starved topic, a ceiling caps a dominant one, a
 * boost multiplies until it expires, and pausing a topic removes it from the
 * pool entirely.
 *
 * So both numbers are shown. `share` is what actually gets crawled and is the
 * one in the bar; `weight` is what is stored. Showing only the stored weight
 * would be a screen that lies whenever anything interesting is happening, and
 * showing only the share would leave a person unable to see what they set.
 *
 * **Archive is not delete, and the copy says so.** §10.2: nodes, edges and tags
 * stay untouched and coming back is a status change. A confirmation dialog
 * warning about data loss would describe a system this is not.
 */

export interface TopicPanelProps {
  rows: readonly TopicRow[]
  sumsTo: number
  entries?: readonly SteeringEntry[]
  busy?: string | null
  onWeight?: (topic: string, weight: number) => void
  onStatus?: (topic: string, status: TopicStatus) => void
}

/** §10.2's four states, in the order a person moves through them. */
export const STATUSES: { key: TopicStatus; label: string; hint: string }[] = [
  { key: 'active', label: 'active', hint: 'Draws seeds.' },
  { key: 'maintenance', label: 'maintenance', hint: 'Finishes its queue, starts nothing new.' },
  { key: 'paused', label: 'paused', hint: 'Out of the pool. Its weight is kept.' },
  { key: 'archived', label: 'archived', hint: 'Out of the pool for good, until you say otherwise.' },
]

export function percent(value: number): string {
  // One decimal, because a floor of 5% and a share of 5.4% are different facts
  // and rounding to whole numbers makes a topic look exactly at its floor when
  // it is not.
  return `${(value * 100).toFixed(1)}%`
}

/**
 * Why this topic's share is not simply its weight.
 *
 * Returns null when the two agree, which is the ordinary case — a note on every
 * row would be noise, and then the rows that need one would not stand out.
 */
export function shareNote(row: TopicRow): string | null {
  if (row.topic.status !== 'active') {
    return 'Draws nothing. Its weight is kept, so putting it back costs nothing.'
  }
  if (row.boost_active) return 'Boosted. Returns to its weight when the boost expires.'
  if (row.share > row.topic.ceiling - 1e-6 && row.topic.ceiling < 1) {
    return `Held at its ceiling of ${percent(row.topic.ceiling)}.`
  }
  if (row.share < row.topic.floor + 1e-6) {
    return `Lifted to its floor of ${percent(row.topic.floor)} so it does not stall.`
  }
  return null
}

export function TopicPanel({
  rows,
  sumsTo,
  entries = [],
  busy = null,
  onWeight,
  onStatus,
}: TopicPanelProps) {
  const active = rows.filter((row) => row.topic.status === 'active')

  return (
    <section>
      <h2 className="font-sans text-[length:var(--text-heading)] font-semibold">Topics</h2>
      <p className="mt-2 max-w-prose text-[length:var(--text-small)] text-text-muted">
        Attention is split across topics, and seeds are drawn in proportion. Nothing here deletes:
        a topic taken out of the pool keeps everything it has already collected.
      </p>

      <p className="mt-3 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
        {/* Published rather than assumed. It is one number and the whole screen
            rests on it. */}
        {active.length} drawing · {percent(sumsTo)} allocated
      </p>

      <ul className="mt-6 space-y-4">
        {rows.map((row) => {
          const note = shareNote(row)
          return (
            <li key={row.topic.topic} className="border border-line bg-surface p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-sans text-[length:var(--text-body)] font-semibold">
                  {row.topic.topic}
                  {row.topic.pinned ? (
                    <span className="ml-2 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                      pinned
                    </span>
                  ) : null}
                </h3>
                <span className="font-mono text-[length:var(--text-label)] text-text-muted">
                  {percent(row.share)} of seeds · weight {row.topic.weight.toFixed(3)}
                </span>
              </div>

              {/* The share, drawn. Not a colour scale: §2 gives colour no
                  verdict, and a topic at 5% is not worse than one at 40%. */}
              <div className="mt-3 h-1.5 w-full bg-surface-raised">
                <div
                  className="h-full bg-accent-graph"
                  style={{ width: `${Math.max(row.share * 100, 0)}%` }}
                />
              </div>

              {note ? (
                <p className="mt-2 text-[length:var(--text-small)] text-text-muted">{note}</p>
              ) : null}

              <div className="mt-4 flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2">
                  <span className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                    share
                  </span>
                  <input
                    type="range"
                    min={Math.round(row.topic.floor * 100)}
                    max={Math.round(row.topic.ceiling * 100)}
                    defaultValue={Math.round(row.share * 100)}
                    disabled={busy === row.topic.topic || row.topic.status !== 'active'}
                    aria-label={`Share for ${row.topic.topic}`}
                    // On release, not on every pixel: each change renormalises
                    // the whole vector and writes an audit row per topic that
                    // moved, so a drag would write hundreds.
                    onMouseUp={(event) =>
                      onWeight?.(row.topic.topic, Number(event.currentTarget.value) / 100)
                    }
                    onKeyUp={(event) =>
                      onWeight?.(row.topic.topic, Number(event.currentTarget.value) / 100)
                    }
                  />
                </label>

                <label className="flex items-center gap-2">
                  <span className="sr-only">Status for {row.topic.topic}</span>
                  <select
                    value={row.topic.status}
                    disabled={busy === row.topic.topic}
                    onChange={(event) =>
                      onStatus?.(row.topic.topic, event.target.value as TopicStatus)
                    }
                    className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-2 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)]"
                  >
                    {STATUSES.map(({ key, label }) => (
                      <option key={key} value={key}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>

                <span className="text-[length:var(--text-small)] text-text-muted">
                  {STATUSES.find((s) => s.key === row.topic.status)?.hint}
                </span>
              </div>
            </li>
          )
        })}
      </ul>

      {entries.length > 0 ? (
        <div className="mt-10">
          <h3 className="font-sans text-[length:var(--text-body)] font-semibold">
            What changed, and why
          </h3>
          <p className="mt-1 max-w-prose text-[length:var(--text-small)] text-text-muted">
            {/* §10.1: with two writers, the alternative is opening this screen in
                a month with no idea what moved anything. */}
            Every change, including the ones that fell out of somebody steering a different topic.
          </p>
          <ul className="mt-3 space-y-2">
            {entries.map((entry) => (
              <li key={entry.log_id} className="text-[length:var(--text-small)] text-text-muted">
                <span className="font-mono">{entry.changed_at.slice(0, 16).replace('T', ' ')}</span>
                {' · '}
                {entry.topic ?? 'all'} {entry.field} {entry.old_value ?? '—'} → {entry.new_value}
                {entry.reason ? ` · ${entry.reason}` : ''}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  )
}
