import type { RunRow } from '../lib/api'

/**
 * Synthesis run history (task P6-23, spec §11.9, §11.10, §13.4).
 *
 * §11.10 keeps the orchestrator's state in a plain table so a crash resumes
 * rather than restarts. This screen reads that table, and the reason it was
 * held open until now is that it could not have been designed against an empty
 * one: what a run row needs to show is decided by what runs actually do, and
 * until `P4-16` they did nothing.
 *
 * What they do turns out to be mostly **stop for reasons**, so that is what
 * leads. §13.4 makes deferral ordinary — a provider that is down, a budget that
 * is unset, a registry with nothing enabled — and a deferred run is not a
 * failure, it is a run waiting for a condition that is usually one line to fix.
 * The reason is shown in full, because it names the condition.
 *
 * **Counters, not a verdict.** §11.9 compares cost and volume per run week on
 * week. A column that said "successful" would hide the run that finished
 * having written nothing, which is the common case while stages are unbuilt
 * and the interesting case once they are not.
 */

export interface RunsPanelProps {
  rows: readonly RunRow[]
  total: number
  active: RunRow | null
}

/** A run's status in the words the state machine uses, plus what it means.
 *
 * `deferred` is the one worth explaining: it reads like an error and is a
 * scheduled retry, and somebody who assumes the first will go looking for an
 * outage that is not there.
 */
export const STATUS_NOTES: Record<string, string> = {
  running: 'In flight.',
  deferred: 'Stopped on a condition and kept its place. The next cycle resumes it.',
  done: 'Finished.',
  failed: 'Stopped on something unexpected.',
}

/** Written work, as one line. Zero is a real answer and is shown as one —
 * a run that wrote nothing is a fact about the corpus, not a gap in the row. */
export function written(row: RunRow): string {
  return `${row.edges_added} edges · ${row.tags_added} tags · ${row.seeds_emitted} seeds`
}

export function spent(row: RunRow): string {
  const tokens = `${row.tokens_used.toLocaleString()} tokens`
  return row.cost_usd === null ? tokens : `${tokens} · $${row.cost_usd.toFixed(2)}`
}

export function RunsPanel({ rows, total, active }: RunsPanelProps) {
  return (
    <section>
      <h2 className="mb-2 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
        Runs
      </h2>

      <p className="mb-6 text-[length:var(--text-small)] text-text-muted">
        {active
          ? `Run ${active.run_id} is ${active.status} at ${active.stage ?? 'an unknown stage'}.`
          : 'Nothing is running.'}{' '}
        {total} in total.
      </p>

      {rows.length === 0 ? (
        <p className="text-[length:var(--text-small)] text-text-muted">
          No runs yet. The orchestrator starts one when it comes up, and daily after that.
        </p>
      ) : null}

      <ul className="space-y-4">
        {rows.map((row) => (
          <li key={row.run_id} className="border border-line bg-surface p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-mono text-[length:var(--text-body)] text-text">
                run {row.run_id}
              </span>
              <span className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                {row.status}
                {row.stage ? ` · ${row.stage}` : ''}
              </span>
            </div>

            <p className="mt-1 text-[length:var(--text-small)] text-text-muted">
              {STATUS_NOTES[row.status] ?? ''}
            </p>

            <p className="mt-2 text-[length:var(--text-small)] text-text">{written(row)}</p>
            <p className="mt-1 text-[length:var(--text-small)] text-text-muted">
              {spent(row)}
              {row.agent_id ? ` · ${row.agent_id}` : ''}
            </p>

            {row.error ? (
              // In full, and not truncated. The reason a run deferred is the
              // whole content of the row — "no enabled agent declares
              // 'relation_extraction'" is a sentence somebody can act on, and
              // its first forty characters are not.
              <p className="mt-2 text-[length:var(--text-small)] text-accent-attention">
                {row.error}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  )
}
