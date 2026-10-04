import { Fragment } from 'react'

import type { RunRow } from '../lib/api'
import { PageHeader, ROW, TD, TDM, TH, TableCard, stamp } from './ui'

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
    <section className="flex flex-col gap-5">
      <PageHeader title="Run history">
        {active
          ? `Run ${active.run_id} is ${active.status} at ${active.stage ?? 'an unknown stage'}.`
          : 'Nothing is running.'}{' '}
        {total.toLocaleString()} in total.
      </PageHeader>

      {rows.length === 0 ? (
        <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
          No runs yet. The orchestrator starts one when it comes up, and daily after that.
        </p>
      ) : (
        <TableCard>
          <thead>
            <tr>
              <th className={TH}>Run</th>
              <th className={TH}>Started</th>
              <th className={TH}>Status</th>
              <th className={TH}>Written</th>
              <th className={TH}>Spent</th>
              <th className={TH}>Agent</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Fragment key={row.run_id}>
                <tr className={ROW} data-run={row.run_id}>
                  <td className={TDM}>{row.run_id}</td>
                  <td className={`${TDM} whitespace-nowrap text-text-muted`}>{stamp(row.started_at)}</td>
                  <td className={TD}>
                    <span className="font-mono text-[11.5px] text-text">
                      {row.status}
                      {row.stage ? ` · ${row.stage}` : ''}
                    </span>
                    <div className="text-[11.5px] text-text-faint">{STATUS_NOTES[row.status] ?? ''}</div>
                  </td>
                  <td className={`${TDM} whitespace-nowrap`}>{written(row)}</td>
                  <td className={`${TDM} whitespace-nowrap text-text-muted`}>{spent(row)}</td>
                  <td className={`${TDM} text-text-muted`}>{row.agent_id ?? '—'}</td>
                </tr>
                {row.error ? (
                  // In full, and not truncated. The reason a run deferred is
                  // the whole content of the row — "no enabled agent declares
                  // 'relation_extraction'" is a sentence somebody can act on,
                  // and its first forty characters are not.
                  <tr data-run-error={row.run_id}>
                    <td />
                    <td
                      colSpan={5}
                      className={`${TD} break-words pt-0 text-[12px] ${
                        row.status === 'failed' ? 'text-accent-attention' : 'text-text-muted'
                      }`}
                    >
                      {row.error}
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            ))}
          </tbody>
        </TableCard>
      )}
    </section>
  )
}
