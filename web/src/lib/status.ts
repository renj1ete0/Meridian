/**
 * What the top-right cluster says (task P6-27) — design-system.md §5, §8: the status pill,
 * the bell, and each notification's kind. Pure, so the rules test without rendering.
 * See docs/features/web-app.md#the-status-pill-and-bell.
 */

import type { CrawlProgress, Notification, RunRow } from './api'
import { clockOf, dayOf, daysBetween } from './time'

// --------------------------------------------------------------------------
// The status pill

/** Whether run history was readable, and what it said. */
export type RunHistory =
  | { kind: 'read'; rows: readonly RunRow[] }
  /** The API refused or failed; `reason` is its own sentence where it gave one. */
  | { kind: 'unreadable'; reason: string }

export type RunHealth = 'ok' | 'failed' | 'unknown'

/**
 * The most recent run that has finished, judged. A running run is skipped, so an earlier
 * failure still shows; `deferred` is not a failure.
 */
export function runHealth(history: RunHistory | null): RunHealth {
  if (history === null || history.kind === 'unreadable') return 'unknown'
  const latest = history.rows.find((run) => run.status !== 'running')
  if (latest === undefined) return 'ok'
  return latest.status === 'failed' ? 'failed' : 'ok'
}

/** `HH:MM` in the display zone (ADR 0009), from a full ISO timestamp. */
export function clock(iso: string): string {
  if (Number.isNaN(new Date(iso).getTime())) return '—'
  return clockOf(iso)
}

/**
 * The pill's one mono line: `queue 412 · fetch 96% · 07:14`.
 *
 * The fetch figure is last hour's success rate, or `idle` when nothing was attempted; the
 * time is when the counts were taken, not the wall clock.
 */
export function statusLine(progress: CrawlProgress): string {
  const pending = progress.queue.pending ?? 0
  const fetch =
    progress.attempts_last_hour === 0
      ? 'fetch idle'
      : `fetch ${Math.round((100 * progress.successes_last_hour) / progress.attempts_last_hour)}%`
  return `queue ${pending.toLocaleString('en')} · ${fetch} · ${clock(progress.as_of)}`
}

/** The pill's accessible description, which also serves as its tooltip. */
export function statusDetail(progress: CrawlProgress | null, health: RunHealth, history: RunHistory | null): string {
  const parts: string[] = []
  if (progress === null) {
    parts.push('Crawl progress is unavailable.')
  } else {
    const pending = progress.queue.pending ?? 0
    parts.push(`${pending.toLocaleString('en')} pages queued.`)
    parts.push(
      progress.attempts_last_hour === 0
        ? 'No fetch attempted in the last hour.'
        : `${progress.successes_last_hour.toLocaleString('en')} of ${progress.attempts_last_hour.toLocaleString('en')} fetches succeeded in the last hour.`,
    )
  }
  if (health === 'failed') parts.push('The most recent synthesis run failed.')
  if (health === 'ok') parts.push('No failed synthesis run.')
  if (health === 'unknown') {
    parts.push(
      history?.kind === 'unreadable'
        ? `Run history is unreadable: ${history.reason}`
        : 'Run history has not been read yet.',
    )
  }
  return parts.join(' ')
}

// --------------------------------------------------------------------------
// Notifications

/** §8's three kinds. */
export const NOTIFICATION_KINDS = ['jobs', 'approvals', 'alerts'] as const
export type NotificationKind = (typeof NOTIFICATION_KINDS)[number]

/**
 * Every recorded type, mapped to the kind a reader filters by. Mirrors
 * `NOTIFICATION_TYPE` in `models/runs.py`; `tests/shell.test.tsx` compares the two.
 */
export const KIND_OF_TYPE: Record<string, NotificationKind> = {
  run_summary: 'jobs',
  job_complete: 'jobs',
  alert: 'alerts',
  seed_proposal: 'approvals',
  gazetteer_proposal: 'approvals',
  merge_adjudication: 'approvals',
  // `P6-38`: an approval that grants itself if nobody answers — still the
  // group a reader opens to find what wants a decision.
  steering_proposal: 'approvals',
}

/** Unknown types are shown as jobs rather than dropped: a row is never hidden. */
export function kindOf(type: string): NotificationKind {
  return KIND_OF_TYPE[type] ?? 'jobs'
}

/** Per-kind totals, summed from the API's per-type counts. */
export function countsByKind(countsByType: Readonly<Record<string, number>>): Record<NotificationKind, number> {
  const totals: Record<NotificationKind, number> = { jobs: 0, approvals: 0, alerts: 0 }
  for (const [type, count] of Object.entries(countsByType)) totals[kindOf(type)] += count
  return totals
}

/**
 * The bell's count and tone: what arrived after `seenAt`, the moment this reader last
 * opened the panel, rather than the server's ever-growing `unread`.
 */
export function bellState(
  notifications: readonly Notification[],
  seenAt: string | null,
): { count: number; tone: 'graph' | 'attention' } {
  const since = seenAt === null ? Number.NEGATIVE_INFINITY : Date.parse(seenAt)
  const fresh = notifications.filter((item) => Date.parse(item.created_at) > since)
  return {
    count: fresh.length,
    tone: fresh.some((item) => kindOf(item.notification_type) === 'alerts') ? 'attention' : 'graph',
  }
}

const SEEN_KEY = 'meridian.notificationsSeen'

/** Guarded, like every storage access here: storage can throw on read. */
export function readSeenAt(): string | null {
  try {
    const stored = window.localStorage.getItem(SEEN_KEY)
    return stored && !Number.isNaN(Date.parse(stored)) ? stored : null
  } catch {
    return null
  }
}

export function writeSeenAt(at: Date = new Date()): void {
  try {
    window.localStorage.setItem(SEEN_KEY, at.toISOString())
  } catch {
    /* A badge that cannot be cleared is a badge. Not an error. */
  }
}

/**
 * The day heading a notification sits under: `Today`, `Yesterday`, or its date.
 *
 * Days in the display zone (ADR 0009), so "today" is the operator's today wherever the page is
 * opened. `created_at` is a full timestamp; bare `YYYY-MM-DD` strings never reach this.
 */
export function dayHeading(iso: string, now: Date = new Date()): string {
  if (Number.isNaN(new Date(iso).getTime())) return iso.slice(0, 10)
  const days = daysBetween(dayOf(iso), dayOf(now))
  if (days === 0) return 'Today'
  if (days === 1) return 'Yesterday'
  return dayOf(iso)
}

/**
 * How long ago, in the register of the landing's "where you were" list:
 * `today`, `yesterday`, `3 days ago`, then the date.
 */
export function ago(iso: string, now: Date = new Date()): string {
  const heading = dayHeading(iso, now)
  if (heading === 'Today') return 'today'
  if (heading === 'Yesterday') return 'yesterday'
  if (Number.isNaN(new Date(iso).getTime())) return heading
  const days = daysBetween(dayOf(iso), dayOf(now))
  return days > 0 && days < 30 ? `${days} days ago` : heading
}
