import { DataChip } from '../ui/Tier'
import type { Notification } from '../lib/api'

/**
 * What happened while nobody was looking (task P6-08, spec §12.5, §13.3).
 *
 * The in-app counterpart to the Telegram digest, reading the same rows: an
 * alert is recorded before it is delivered (`P5-07`), so a deployment with no
 * bot token still has somewhere to see what would have been sent.
 *
 * **Filterable by type, not by read state.** The model says why, and it is
 * right: the useful question is "what finished" or "what needs a decision", not
 * "what have I glanced at". A read/unread split turns a panel of findings into
 * an inbox to be cleared, and an inbox gets cleared without being read.
 */

export interface NotificationsPanelProps {
  notifications: readonly Notification[]
  countsByType: Readonly<Record<string, number>>
  /** Types currently shown. Empty means all. */
  active?: readonly string[]
  onFilter?: (type: string | null) => void
}

/** §4: concrete nouns. "alert" is what it is; "Alerts" is a menu heading. */
const LABEL: Record<string, string> = {
  alert: 'alerts',
  run_summary: 'runs',
  job_complete: 'jobs',
  seed_proposal: 'seeds',
  gazetteer_proposal: 'gazetteer',
  merge_adjudication: 'merges',
}

export function NotificationsPanel({
  notifications,
  countsByType,
  active = [],
  onFilter,
}: NotificationsPanelProps) {
  const types = Object.keys(countsByType).sort()

  return (
    <section>
      <h2 className="font-sans text-[length:var(--text-heading)] font-semibold">Notifications</h2>

      {types.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => onFilter?.(null)}
            className={`rounded-chip border px-2 py-0.5 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
              active.length === 0
                ? 'border-accent-graph text-accent-graph'
                : 'border-line-strong text-text-muted'
            }`}
          >
            all
          </button>
          {types.map((type) => (
            <button
              key={type}
              type="button"
              onClick={() => onFilter?.(type)}
              className={`rounded-chip border px-2 py-0.5 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
                active.includes(type)
                  ? 'border-accent-graph text-accent-graph'
                  : 'border-line-strong text-text-muted'
              }`}
            >
              {/* The count comes from every type, not the filtered set — a panel
                  reading "alerts (0)" while three seed proposals wait is the
                  filter hiding the thing the reader came for. */}
              {LABEL[type] ?? type} ({countsByType[type]})
            </button>
          ))}
        </div>
      ) : null}

      {notifications.length === 0 ? (
        <p className="mt-4 text-text-muted">
          Nothing recorded. Alerts are written when a condition has been true long enough to
          matter, so an empty panel is the system saying it has nothing to report.
        </p>
      ) : (
        <ul className="mt-4 space-y-4">
          {notifications.map((item) => (
            <li
              key={item.notification_id}
              className={
                item.notification_type === 'alert'
                  ? 'border-l-2 border-accent-attention pl-3'
                  : 'border-l border-line pl-3'
              }
            >
              <p className="text-text">{item.title}</p>
              {item.body !== null ? (
                <p className="mt-1 text-text-muted">{item.body}</p>
              ) : null}
              <p className="mt-2 flex flex-wrap items-center gap-2">
                <DataChip>{LABEL[item.notification_type] ?? item.notification_type}</DataChip>
                {/* The timestamp as written. `new Date(...)` on a date-only
                    string shifts it a day in negative offsets — the same trap
                    the API client documents. */}
                <DataChip>{item.created_at.slice(0, 16).replace('T', ' ')}</DataChip>
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
