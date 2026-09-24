import { onInternalClick } from '../lib/route'
import {
  NOTIFICATION_KINDS,
  clock,
  countsByKind,
  dayHeading,
  kindOf,
  type NotificationKind,
} from '../lib/status'
import { DAGGER } from '../ui/Contested'
import type { Notification } from '../lib/api'

/**
 * What happened while nobody was looking (tasks P6-08, P6-27; spec §12.5,
 * §13.3; design-system.md §8, `Notifications.dc.html`).
 *
 * The in-app counterpart to the Telegram digest, reading the same rows: an
 * alert is recorded before it is delivered (`P5-07`), so a deployment with no
 * bot token still has somewhere to see what would have been sent. Opened from
 * the bell in the top bar.
 *
 * **Filterable by type, not by read state.** §8: the question a returning
 * reader asks is "did anything need me?", not "what have I already seen?". A
 * read/unread split turns a panel of findings into an inbox to be cleared, and
 * an inbox gets cleared without being read. That is also why the artboard's
 * "Mark all read" is not here: there is no read state to mark.
 *
 * The six recorded types fold into §8's three kinds — jobs, approvals, alerts —
 * and the counts on the filter come from every type, not the filtered set: a
 * filter reading "Alerts 0" while three proposals wait would be the filter
 * hiding the thing the reader came for.
 */

export interface NotificationsPanelProps {
  notifications: readonly Notification[]
  countsByType: Readonly<Record<string, number>>
  /** The kind shown, or null for all of them. */
  active?: NotificationKind | null
  onFilter?: (kind: NotificationKind | null) => void
  /** Injected so day headings are testable. */
  now?: Date
}

const KIND_LABEL: Record<NotificationKind, string> = {
  jobs: 'Jobs',
  approvals: 'Approvals',
  alerts: 'Alerts',
}

/** One row's kind, in the singular, for its meta line. */
const KIND_SINGULAR: Record<NotificationKind, string> = {
  jobs: 'Job',
  approvals: 'Approval',
  alerts: 'Alert',
}

/** §4: concrete nouns. The type as a reader would say it. */
const TYPE_LABEL: Record<string, string> = {
  alert: 'condition',
  run_summary: 'run',
  job_complete: 'job',
  seed_proposal: 'seed sources',
  gazetteer_proposal: 'gazetteer',
  merge_adjudication: 'possible duplicate',
  steering_proposal: 'steering proposal',
}

/**
 * Where a row leads, when somewhere exists to act on it. Every decision a
 * notification asks for is made in Admin; a finished job has no page of its
 * own yet, so it has no action rather than a link to nowhere.
 */
function actionFor(item: Notification): { label: string; href: string } | null {
  const kind = kindOf(item.notification_type)
  // `P6-38`: straight to the list where it can be accepted or rejected.
  if (item.notification_type === 'steering_proposal')
    return { label: 'Review', href: '/admin/proposals' }
  if (kind === 'approvals') return { label: 'Review', href: '/admin' }
  if (kind === 'alerts') return { label: 'Admin', href: '/admin' }
  if (item.notification_type === 'run_summary') return { label: 'Run log', href: '/admin' }
  return null
}

export function NotificationsPanel({
  notifications,
  countsByType,
  active = null,
  onFilter,
  now = new Date(),
}: NotificationsPanelProps) {
  const totals = countsByKind(countsByType)
  const all = totals.jobs + totals.approvals + totals.alerts
  const shown = active === null
    ? notifications
    : notifications.filter((item) => kindOf(item.notification_type) === active)

  // Grouped by local day, in the order the API returns them (newest first).
  const days: Array<{ heading: string; items: Notification[] }> = []
  for (const item of shown) {
    const heading = dayHeading(item.created_at, now)
    const last = days[days.length - 1]
    if (last && last.heading === heading) last.items.push(item)
    else days.push({ heading, items: [item] })
  }

  return (
    <section aria-labelledby="notifications-heading" className="flex flex-col">
      <header className="flex items-center gap-3 border-b border-line px-[18px] py-3.5">
        <h2
          id="notifications-heading"
          className="font-sans text-[14.5px] font-semibold leading-tight text-text"
        >
          Notifications
        </h2>
      </header>

      {all > 0 ? (
        <div className="border-b border-line px-[18px] py-3">
          <div className="inline-flex border border-line" role="group" aria-label="Filter by type">
            <FilterButton on={active === null} onClick={() => onFilter?.(null)}>
              All {all}
            </FilterButton>
            {NOTIFICATION_KINDS.map((kind) => (
              <FilterButton
                key={kind}
                on={active === kind}
                attention={kind === 'alerts' && totals.alerts > 0}
                onClick={() => onFilter?.(kind)}
              >
                {KIND_LABEL[kind]} {totals[kind]}
              </FilterButton>
            ))}
          </div>
        </div>
      ) : null}

      {shown.length === 0 ? (
        <p className="px-[18px] py-5 text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
          {all === 0
            ? 'Nothing recorded. Alerts are written when a condition has been true long enough to matter, so an empty panel is the system saying it has nothing to report.'
            : `No ${active === null ? '' : `${KIND_LABEL[active].toLowerCase()} `}among the recent notifications.`}
        </p>
      ) : (
        <div className="max-h-[min(560px,calc(100vh-140px))] overflow-y-auto">
          {days.map(({ heading, items }) => (
            <div key={heading}>
              <h3 className="border-b border-line/60 bg-ground/50 px-[18px] py-2 font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
                {heading}
              </h3>
              <ul>
                {items.map((item) => (
                  <Row key={item.notification_id} item={item} />
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      <footer className="border-t border-line bg-ground/50 px-[18px] py-3 font-mono text-[10px] text-text-faint">
        The daily digest reads these same rows.
      </footer>
    </section>
  )
}

function FilterButton({
  on,
  attention = false,
  onClick,
  children,
}: {
  on: boolean
  attention?: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`border-r border-line px-[11px] py-1.5 font-sans text-[11.5px] last:border-r-0 ${
        on ? 'bg-surface-raised text-text' : attention ? 'text-accent-attention' : 'text-text-faint hover:text-text-muted'
      }`}
    >
      {children}
    </button>
  )
}

function Row({ item }: { item: Notification }) {
  const kind = kindOf(item.notification_type)
  const alert = kind === 'alerts'
  const action = actionFor(item)

  return (
    <li
      data-kind={kind}
      className={`flex items-start gap-3 border-b border-line/60 px-[18px] py-[13px] ${
        alert ? 'bg-accent-attention-deep/15' : ''
      }`}
    >
      <span className="mt-px w-4 shrink-0 text-center" aria-hidden="true">
        {alert ? (
          // §8: alerts carry the brass tint and the dagger — the dagger as
          // text, so the row still reads as an alert with the colour gone.
          <span className="font-mono text-[14px] leading-[1.2] text-accent-attention">{DAGGER}</span>
        ) : (
          <KindGlyph kind={kind} />
        )}
      </span>

      <div className="flex min-w-0 grow flex-col gap-[5px]">
        <p className="text-[13px] leading-[1.45] text-text">
          {item.title}
          {alert ? <span className="sr-only"> (alert)</span> : null}
        </p>
        {item.body !== null ? (
          <p className="line-clamp-2 text-[12px] leading-[1.5] text-text-muted">{item.body}</p>
        ) : null}
        <p className={`font-mono text-[10px] ${alert ? 'text-accent-attention' : 'text-text-faint'}`}>
          {KIND_SINGULAR[kind]} · {TYPE_LABEL[item.notification_type] ?? item.notification_type} ·{' '}
          <time dateTime={item.created_at}>{clock(item.created_at)}</time>
        </p>
      </div>

      {action ? (
        <a
          href={action.href}
          onClick={onInternalClick(action.href)}
          className={`shrink-0 font-mono text-[10.5px] ${alert ? 'text-accent-attention' : 'text-accent-graph'} hover:underline`}
        >
          {action.label}
        </a>
      ) : null}
    </li>
  )
}

/** The artboard's row glyphs: a sheet for jobs, a ring for approvals. */
function KindGlyph({ kind }: { kind: NotificationKind }) {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" className="text-text-muted" focusable="false">
      {kind === 'approvals' ? (
        <circle cx="12" cy="12" r="8.4" stroke="currentColor" strokeWidth="1.6" />
      ) : (
        <rect x="5" y="2.6" width="14" height="18.8" rx="2" stroke="currentColor" strokeWidth="1.6" />
      )}
    </svg>
  )
}
