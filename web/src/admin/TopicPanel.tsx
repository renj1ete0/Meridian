import { useEffect, useRef, useState } from 'react'

import type { SteeringEntry, TopicRow, TopicStatus, Topics } from '../lib/api'
import { BoostsTable, type BoostChange } from './BoostsTable'
import { weight } from './Reweight'
import { BUTTON_PRIMARY, BUTTON_SECONDARY, Badge, Card, LABEL, LINK_ACTION, PageHeader } from './ui'

/**
 * Topic weights (tasks P6-12, P6-28; spec §10, §10.1, §10.2; `AdminLight`).
 *
 * §10's model in one line: attention is a weight vector over topics, and seeds
 * are drawn proportionally. This screen is where a person changes it, and the
 * design problem is that **the number you set is not always the number that
 * applies**. A floor lifts a starved topic, a ceiling caps a dominant one, a
 * boost multiplies until it expires, and pausing a topic removes it from the
 * pool. So the stored weight is the number on the row and the slider, and the
 * share actually drawn is on the line beneath it — with a note saying which
 * mechanism made them differ, only when one did.
 *
 * **A slider stages; Apply commits.** Moving one topic re-normalises every
 * other, and a slider that wrote on release made that a side effect of
 * dragging. Now a drag is a draft: the server previews it (the write, rolled
 * back), every row shows where it would land, and Revert or Apply decides. One
 * topic is staged at a time, because the server holds exactly one topic at the
 * value asked for and redistributes the rest — two staged topics would be
 * applied as two writes, and the second would move the first.
 *
 * **Archive is not delete, and the copy says so.** §10.2: nodes, edges and tags
 * stay untouched and coming back is a status change. Archived topics move to a
 * collapsed list with Restore beside each, rather than vanishing.
 */

export interface Draft {
  topic: string
  weight: number
}

export interface TopicPanelProps {
  rows: readonly TopicRow[]
  sumsTo: number
  /** The status changes the log records, for "archived 12 Aug" on archived rows. */
  entries?: readonly SteeringEntry[]
  busy?: string | null
  draft?: Draft | null
  /** The server's preview of `draft`, once it has answered. */
  preview?: Topics | null
  /** The server's refusal of `draft`, as written. */
  refusal?: string | null
  onDraft?: (topic: string, weight: number) => void
  onRevert?: () => void
  onApply?: () => void
  onStatus?: (topic: string, status: TopicStatus) => void
  onPinned?: (topic: string, pinned: boolean) => void
  onArchive?: (topic: string) => void
  onAddTopic?: () => void
  onBoost?: (topic: string, change: BoostChange) => void
}

/** §10.2's four states, in the order a person moves through them. */
export const STATUSES: { key: TopicStatus; label: string; hint: string }[] = [
  { key: 'active', label: 'active', hint: 'Draws seeds.' },
  { key: 'maintenance', label: 'maintenance', hint: 'Finishes its queue, starts nothing new.' },
  { key: 'paused', label: 'paused', hint: 'Out of the pool. Its weight is kept.' },
  {
    key: 'archived',
    label: 'archived',
    hint: 'Out of the pool for good, until you say otherwise.',
  },
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

/** The date the log last saw this topic reach `status`, as `12 Aug`. */
export function statusSince(
  entries: readonly SteeringEntry[],
  topic: string,
  status: TopicStatus,
): string | null {
  const entry = entries.find(
    (e) => e.topic === topic && e.field === 'status' && e.new_value === status,
  )
  if (!entry) return null
  // Written out rather than `toLocaleDateString`: ICU versions disagree on
  // "Sep" and "Sept", and a date that changes shape between machines is a
  // column that stops lining up.
  const date = new Date(entry.changed_at)
  return `${String(date.getUTCDate()).padStart(2, '0')} ${MONTHS[date.getUTCMonth()]}`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** The middle caption under a slider: what the row draws, or why it draws nothing. */
export function caption(row: TopicRow, entries: readonly SteeringEntry[]): string {
  const { status, topic } = row.topic
  if (status === 'paused' || status === 'maintenance') {
    const since = statusSince(entries, topic, status)
    const what = status === 'paused' ? 'acquisition suspended' : 'finishing its queue'
    return `weight kept · ${what}${since ? ` ${since}` : ''}`
  }
  const note = shareNote(row)
  return note ? `draws ${percent(row.share)} · ${note}` : `draws ${percent(row.share)} of seeds`
}

// --------------------------------------------------------------------------
// The slider
// --------------------------------------------------------------------------

/**
 * A weight on a 0–1 axis, with the floor and ceiling marked.
 *
 * Drawn rather than a styled native range, because the design needs marks the
 * native control cannot carry. A real `<input type="range">` sits transparently
 * over the floor-to-ceiling span, so dragging, clicking and the arrow keys all
 * work, and the slider cannot be moved to a value the server would refuse.
 */
export function WeightSlider({
  topic,
  value,
  floor,
  ceiling,
  tone,
  disabled,
  onChange,
}: {
  topic: string
  value: number
  floor: number
  ceiling: number
  tone: 'graph' | 'attention' | 'muted'
  disabled: boolean
  onChange?: (value: number) => void
}) {
  const fill =
    tone === 'attention'
      ? 'bg-accent-attention'
      : tone === 'muted'
        ? 'bg-text-faint'
        : 'bg-accent-graph'
  const at = (v: number) => `${Math.min(Math.max(v, 0), 1) * 100}%`

  return (
    <div className="relative h-1 bg-surface-raised" data-slider={topic}>
      <div className={`absolute left-0 top-0 h-1 ${fill}`} style={{ width: at(value) }} />
      <div className="absolute -top-1 h-3 w-px bg-line-strong" style={{ left: at(floor) }} />
      <div className="absolute -top-1 h-3 w-px bg-line-strong" style={{ left: at(ceiling) }} />
      <div
        className={`pointer-events-none absolute -top-1.5 -ml-[7px] h-4 w-3.5 ${fill}`}
        style={{ left: at(value) }}
      />
      <input
        type="range"
        min={Math.round(floor * 100)}
        max={Math.round(ceiling * 100)}
        step={1}
        value={Math.round(value * 100)}
        disabled={disabled}
        aria-label={`Weight for ${topic}`}
        aria-valuetext={weight(value)}
        onChange={(event) => onChange?.(Number(event.currentTarget.value) / 100)}
        className="absolute -top-2 h-5 cursor-pointer opacity-0 disabled:cursor-not-allowed"
        style={{ left: at(floor), width: `${(ceiling - floor) * 100}%` }}
      />
    </div>
  )
}

// --------------------------------------------------------------------------
// The row menu
// --------------------------------------------------------------------------

function RowMenu({
  row,
  disabled,
  onStatus,
  onPinned,
  onArchive,
}: {
  row: TopicRow
  disabled: boolean
  onStatus?: (topic: string, status: TopicStatus) => void
  onPinned?: (topic: string, pinned: boolean) => void
  onArchive?: (topic: string) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLSpanElement>(null)
  const { topic, status, pinned } = row.topic

  useEffect(() => {
    if (!open) return
    const away = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  const item =
    'block w-full border-b border-line/60 px-[13px] py-[9px] text-left text-[12.5px] last:border-b-0 hover:bg-surface-raised'
  const pick = (run: () => void) => () => {
    setOpen(false)
    run()
  }
  const hint = (key: TopicStatus) => STATUSES.find((s) => s.key === key)?.hint

  return (
    <span ref={ref} className="relative flex">
      <button
        type="button"
        aria-label={`Actions for ${topic}`}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className="flex gap-[2.5px] px-1 py-2 disabled:opacity-45"
      >
        {[0, 1, 2].map((i) => (
          <span key={i} className="block h-[3px] w-[3px] rounded-full bg-text-faint" />
        ))}
      </button>
      {open ? (
        <span
          role="menu"
          className="absolute right-[-6px] top-7 z-20 flex w-[214px] flex-col border border-line-strong bg-surface"
        >
          <button
            role="menuitem"
            type="button"
            className={`${item} text-text`}
            onClick={pick(() => onPinned?.(topic, !pinned))}
          >
            {pinned ? 'Unpin' : 'Pin'}
          </button>
          {status === 'active' ? (
            <>
              <button
                role="menuitem"
                type="button"
                title={hint('paused')}
                className={`${item} text-text`}
                onClick={pick(() => onStatus?.(topic, 'paused'))}
              >
                Pause acquisition
              </button>
              <button
                role="menuitem"
                type="button"
                title={hint('maintenance')}
                className={`${item} text-text`}
                onClick={pick(() => onStatus?.(topic, 'maintenance'))}
              >
                Finish queue, start nothing new
              </button>
            </>
          ) : (
            <button
              role="menuitem"
              type="button"
              title={hint('active')}
              className={`${item} text-text`}
              onClick={pick(() => onStatus?.(topic, 'active'))}
            >
              Resume acquisition
            </button>
          )}
          <button
            role="menuitem"
            type="button"
            className={`${item} text-accent-attention`}
            onClick={pick(() => onArchive?.(topic))}
          >
            Archive topic…
          </button>
        </span>
      ) : null}
    </span>
  )
}

// --------------------------------------------------------------------------
// The panel
// --------------------------------------------------------------------------

export function TopicPanel({
  rows,
  sumsTo,
  entries = [],
  busy = null,
  draft = null,
  preview = null,
  refusal = null,
  onDraft,
  onRevert,
  onApply,
  onStatus,
  onPinned,
  onArchive,
  onAddTopic,
  onBoost,
}: TopicPanelProps) {
  const [archivedOpen, setArchivedOpen] = useState(false)
  const current = rows.filter((row) => row.topic.status !== 'archived')
  const archived = rows.filter((row) => row.topic.status === 'archived')
  const drawing = current.filter((row) => row.topic.status === 'active').length
  const held = current.length - drawing
  // While a draft is staged, every row shows where the preview puts it.
  const shown = new Map((preview ?? { rows }).rows.map((row) => [row.topic.topic, row]))

  return (
    <section className="flex flex-col gap-6">
      <PageHeader
        title="Topics"
        actions={
          <>
            <button type="button" className={BUTTON_SECONDARY} disabled={!draft} onClick={onRevert}>
              Revert
            </button>
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={!draft || !preview || busy !== null}
              onClick={onApply}
            >
              Apply
            </button>
          </>
        }
      >
        Weights set each topic’s share of what the crawl acquires. Nothing here deletes: lowering,
        pausing or archiving a topic changes what gets acquired next, and nothing else.
      </PageHeader>

      <Card className="flex flex-col gap-[18px] px-6 pb-[22px] pt-5">
        <div className="flex items-baseline justify-between border-b border-line/60 pb-1">
          <span className={LABEL}>
            Active · {drawing}
            {held ? ` · held · ${held}` : ''}
          </span>
          <button type="button" className={LINK_ACTION} onClick={onAddTopic}>
            + Add topic
          </button>
        </div>

        {current.length === 0 ? (
          <p className="text-[12.5px] text-text-muted">
            No topics. Add one, and the crawl starts drawing seeds for it at its floor.
          </p>
        ) : null}

        {current.map((stored) => {
          const row = shown.get(stored.topic.topic) ?? stored
          const { topic, status, pinned, floor, ceiling } = stored.topic
          const staged = draft?.topic === topic
          const value = staged ? draft.weight : row.topic.weight
          const moved =
            preview !== null && !staged && Math.abs(row.topic.weight - stored.topic.weight) >= 0.005
          const drawingNow = status === 'active'
          return (
            <div
              key={topic}
              data-topic={topic}
              className={`flex flex-col gap-2.5 ${drawingNow ? '' : 'opacity-60'}`}
            >
              <div className="flex items-baseline justify-between gap-2.5">
                <span className="flex items-baseline gap-[9px]">
                  <span className="text-[14px] font-semibold text-text">{topic}</span>
                  {status === 'paused' ? <Badge>Paused</Badge> : null}
                  {status === 'maintenance' ? <Badge>Maintenance</Badge> : null}
                  {pinned ? <Badge tone="attention">Pinned</Badge> : null}
                  {row.boost_active ? <Badge>Boosted ×{row.topic.boost_factor}</Badge> : null}
                </span>
                <span className="flex items-center gap-3">
                  {moved ? (
                    <span className="font-mono text-[11px] text-text-faint">
                      was {weight(stored.topic.weight)}
                    </span>
                  ) : null}
                  <span
                    className={`font-mono text-[13px] tabular-nums ${staged || moved ? 'text-accent-graph' : 'text-text'}`}
                  >
                    {weight(value)}
                  </span>
                  <RowMenu
                    row={stored}
                    disabled={busy === topic}
                    onStatus={onStatus}
                    onPinned={onPinned}
                    onArchive={onArchive}
                  />
                </span>
              </div>
              <WeightSlider
                topic={topic}
                value={value}
                floor={floor}
                ceiling={ceiling}
                tone={!drawingNow ? 'muted' : pinned ? 'attention' : 'graph'}
                // The server refuses a weight for a topic outside the pool;
                // the control says so by not moving.
                disabled={!drawingNow || busy !== null}
                onChange={(next) => onDraft?.(topic, next)}
              />
              <div className="flex justify-between gap-4 font-mono text-[10px] text-text-faint">
                <span className="shrink-0">floor {weight(floor)}</span>
                <span className="min-w-0 truncate text-center">{caption(row, entries)}</span>
                <span className="shrink-0">ceiling {weight(ceiling)}</span>
              </div>
            </div>
          )
        })}

        <div className="flex items-center justify-between border-t border-line pt-3">
          <span className={LABEL}>Normalised</span>
          <span className="font-mono text-[13px] tabular-nums text-text">
            {weight(preview ? preview.sums_to : sumsTo)}
          </span>
        </div>

        {refusal ? (
          <p role="alert" className="text-[12.5px] text-accent-attention">
            {refusal}
          </p>
        ) : draft ? (
          <p className="text-[12px] leading-[1.55] text-text-muted">
            Previewing {draft.topic} at {weight(draft.weight)}. The others move to make room, down
            to their floors; nothing is applied until Apply.
          </p>
        ) : null}

        <p className="text-[12px] leading-[1.55] text-text-faint">
          A paused topic keeps its stored weight, so resuming it costs nothing; while it is out, its
          share is drawn by the others. Archiving releases the weight the same way and moves the
          topic to the list below.
        </p>
      </Card>

      <Card className="flex flex-col gap-3 px-[18px] py-3.5">
        <button
          type="button"
          className="flex items-center gap-2.5 text-left"
          aria-expanded={archivedOpen}
          onClick={() => setArchivedOpen((v) => !v)}
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 12 12"
            fill="none"
            aria-hidden="true"
            className={`shrink-0 stroke-text-faint transition-transform ${archivedOpen ? '' : '-rotate-90'}`}
          >
            <path
              d="M2.5 4 L6 8 L9.5 4"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className={LABEL}>Archived · {archived.length}</span>
          <span className="ml-auto font-mono text-[10px] text-text-faint">
            not seeded · nothing deleted
          </span>
        </button>
        {archivedOpen ? (
          archived.length === 0 ? (
            <p className="border-t border-line/60 pt-2.5 text-[12.5px] text-text-muted">
              Nothing archived.
            </p>
          ) : (
            archived.map((row) => {
              const since = statusSince(entries, row.topic.topic, 'archived')
              return (
                <div
                  key={row.topic.topic}
                  data-topic={row.topic.topic}
                  className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1 border-t border-line/60 pt-2.5"
                >
                  <span className="w-44 shrink-0 text-[12.5px] text-text">{row.topic.topic}</span>
                  <span className="min-w-0 flex-1 font-mono text-[11.5px] text-text-faint">
                    {since ? `archived ${since} · ` : ''}weight {weight(row.topic.weight)} kept ·
                    nodes kept · still searchable
                  </span>
                  <button
                    type="button"
                    className={LINK_ACTION}
                    disabled={busy === row.topic.topic}
                    onClick={() => onStatus?.(row.topic.topic, 'active')}
                  >
                    Restore
                  </button>
                </div>
              )
            })
          )
        ) : null}
      </Card>

      <BoostsTable rows={rows} busy={busy} onBoost={onBoost} />
    </section>
  )
}
