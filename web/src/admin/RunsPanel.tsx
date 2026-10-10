import { Fragment } from 'react'

import type { RunRow } from '../lib/api'
import { PageHeader, ROW, TD, TDM, TH, TableCard, stamp } from './ui'

/**
 * Synthesis run history (task P6-23, spec §11.9, §11.10, §13.4): why each run stopped,
 * in full, and counters rather than a verdict. See docs/features/web-app.md#run-history.
 */

export interface RunsPanelProps {
  rows: readonly RunRow[]
  total: number
  active: RunRow | null
  /** Read the next page of older runs; absent when there is none to read. */
  onOlder?: () => void
  loadingOlder?: boolean
}

/**
 * A run's status in the words the state machine uses, plus what it means. `deferred` reads
 * like an error and is a scheduled retry.
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
  const n = (count: number, one: string) => `${count} ${count === 1 ? one : `${one}s`}`
  return `${n(row.edges_added, 'edge')} · ${n(row.tags_added, 'tag')} · ${n(row.seeds_emitted, 'seed')}`
}

/** Status and stage, without saying the same word twice ("done · done"). */
export function statusLine(row: RunRow): string {
  return row.stage && row.stage !== row.status ? `${row.status} · ${row.stage}` : row.status
}

export function spent(row: RunRow): string {
  const tokens = `${row.tokens_used.toLocaleString()} tokens`
  return row.cost_usd === null ? tokens : `${tokens} · $${row.cost_usd.toFixed(2)}`
}

export function RunsPanel({ rows, total, active, onOlder, loadingOlder = false }: RunsPanelProps) {
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
                    <span className="font-mono text-[11.5px] text-text">{statusLine(row)}</span>
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
      {onOlder && rows.length < total ? (
        <button
          type="button"
          onClick={onOlder}
          disabled={loadingOlder}
          className="self-start border border-line-strong bg-surface-raised px-3 py-1.5 font-sans text-[12.5px] text-text/85 hover:text-text disabled:opacity-50"
        >
          {loadingOlder ? 'Reading…' : `Older runs (${(total - rows.length).toLocaleString()} more)`}
        </button>
      ) : null}
    </section>
  )
}
