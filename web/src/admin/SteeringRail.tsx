import type { RunRow, SteeringEntry } from '../lib/api'
import { LABEL, stamp } from './ui'

/**
 * The right rail on the steering pages (task P6-28, spec §10.1, §11.10;
 * `AdminLight`).
 *
 * Two answers kept beside the controls that produce them. **Steering audit**
 * is §10.1's log: with two writers, the alternative is opening this screen in a
 * month with no idea what moved anything — and most of what moved a weight is
 * a change somebody made to a *different* topic, so those lines are shown too.
 * **Last runs** is what the weights were steering, in counters rather than a
 * verdict.
 */

const AUTO_REASON = /^(changed|added) through admin/

/** A stored value as the rail prints it: two decimals, or three when two would
 * make a change look like no change. */
function number(value: string | null, other: string | null): string {
  if (value === null) return '—'
  const n = Number(value)
  if (!Number.isFinite(n)) return value
  const o = Number(other)
  const places = Number.isFinite(o) && n.toFixed(2) === o.toFixed(2) && n !== o ? 3 : 2
  return n.toFixed(places)
}

/** One log row as one line of the audit. */
export function auditLine(entry: SteeringEntry): string {
  const topic = entry.topic ?? 'all'
  const { field, old_value: old, new_value: next } = entry
  switch (field) {
    case 'weight':
      return `${topic} ${number(old, next)} → ${number(next, old)}`
    case 'floor':
    case 'ceiling':
      return `${topic} ${field} ${number(old, next)} → ${number(next, old)}`
    case 'status':
      return old === null ? `${topic} added` : `${topic} ${old} → ${next}`
    case 'pinned':
      return next === 'True' ? `${topic} pinned` : `${topic} unpinned`
    case 'boost_factor':
      return next === null ? `${topic} boost ended` : `${topic} boost ${next}×`
    case 'boost_expires_at':
      return next === null
        ? `${topic} boost expiry cleared`
        : `${topic} boost until ${next.slice(0, 10)}`
    default:
      return `${topic} ${field ?? 'change'} ${old ?? '—'} → ${next ?? '—'}`
  }
}

export interface AuditGroup {
  at: string
  actor: string
  reason: string | null
  lines: string[]
}

/**
 * Entries made by one change, together. A single steering request writes one
 * row per topic it moved, all at the same instant; printed separately they
 * read as several decisions when there was one.
 */
export function auditGroups(entries: readonly SteeringEntry[]): AuditGroup[] {
  const groups: AuditGroup[] = []
  for (const entry of entries) {
    const last = groups[groups.length - 1]
    const reason = entry.reason && !AUTO_REASON.test(entry.reason) ? entry.reason : null
    if (last && last.at === entry.changed_at && last.actor === entry.actor) {
      last.lines.push(auditLine(entry))
      last.reason ??= reason
    } else {
      groups.push({ at: entry.changed_at, actor: entry.actor, reason, lines: [auditLine(entry)] })
    }
  }
  return groups
}

/** The middle column of a run row: what it wrote, or what stopped it. */
export function runOutcome(run: RunRow): string {
  if (run.status === 'done') return `+${run.edges_added.toLocaleString()} edges`
  return run.status
}

/** Money when recorded; tokens when it was not. Never a zero nobody measured. */
export function runCost(run: RunRow): string {
  if (run.cost_usd !== null) return `$${run.cost_usd.toFixed(2)}`
  if (run.tokens_used === 0) return '—'
  return run.tokens_used >= 1000
    ? `${(run.tokens_used / 1000).toFixed(1)}k tok`
    : `${run.tokens_used} tok`
}

export function SteeringRail({
  entries,
  runs,
  groups = 5,
}: {
  entries: readonly SteeringEntry[]
  runs: readonly RunRow[] | null
  groups?: number
}) {
  const audit = auditGroups(entries).slice(0, groups)
  const recent = (runs ?? []).slice(0, 4)
  // The mock's "Note": the newest run that stopped for a reason, with the
  // reason in full. The reason names the condition, and it is usually one line
  // to fix.
  const stopped = recent.find((run) => run.error)

  return (
    <aside className="flex flex-col gap-[22px]" aria-label="Steering audit and recent runs">
      <div className="flex flex-col gap-[13px]">
        <div className={LABEL}>Steering audit</div>
        <p className="text-[11.5px] leading-[1.5] text-text-faint">
          Every change, including the ones that fell out of steering a different topic.
        </p>
        {audit.length === 0 ? (
          <p className="text-[12.5px] text-text-muted">Nothing has been steered yet.</p>
        ) : (
          audit.map((group, i) => (
            <div
              key={`${group.at}-${i}`}
              className={`font-mono text-[11px] leading-[1.75] text-text-muted ${
                i > 0 ? 'border-t border-line/60 pt-[13px]' : ''
              }`}
            >
              <div className="text-text">
                {stamp(group.at)}
                {group.actor !== 'user' ? ` · ${group.actor}` : ''}
              </div>
              {group.lines.map((line, j) => (
                <div key={j}>{line}</div>
              ))}
              {group.reason ? <div className="italic text-text-faint">{group.reason}</div> : null}
            </div>
          ))
        )}
      </div>

      <div className="h-px bg-line" />

      <div className="flex flex-col gap-[13px]">
        <div className={LABEL}>Last runs</div>
        {runs === null ? (
          <p className="text-[12.5px] text-text-muted">Loading runs.</p>
        ) : recent.length === 0 ? (
          <p className="text-[12.5px] text-text-muted">No synthesis run yet.</p>
        ) : (
          <div className="flex flex-col gap-2.5">
            {recent.map((run) => (
              <div
                key={run.run_id}
                data-run={run.run_id}
                className="flex justify-between gap-2.5 font-mono text-[11.5px] tabular-nums"
              >
                <span className="text-text">{stamp(run.started_at).slice(5)}</span>
                <span
                  className={run.status === 'failed' ? 'text-accent-attention' : 'text-text-faint'}
                >
                  {runOutcome(run)}
                </span>
                <span className="text-text">{runCost(run)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {stopped ? (
        <>
          <div className="h-px bg-line" />
          <div className="flex flex-col gap-2.5">
            <div className={LABEL}>Note</div>
            <p className="break-words text-[12.5px] leading-[1.6] text-text-muted">
              Run {stopped.run_id} {stopped.status}
              {stopped.stage ? ` at ${stopped.stage}` : ''}: {stopped.error}
            </p>
          </div>
        </>
      ) : null}
    </aside>
  )
}
