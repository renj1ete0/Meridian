import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import { GAZETTEER_BULK_MAX, type GazetteerEntityType, type GazetteerRow, type GazetteerState } from '../lib/api'
import { BUTTON_ROW, Filters, PAGER, PageHeader, ROW, TD, TDM, TH, TableCard } from './ui'

/**
 * The gazetteer approval queue (tasks P6-13, P6-28; spec §5.6): a dense, paged table
 * with bulk decisions, each row saying whether the matcher will load it. Approve and turn
 * down are not coloured. See docs/features/web-app.md#gazetteer-approvals.
 */

export interface GazetteerQueueProps {
  rows: readonly GazetteerRow[]
  state: GazetteerState
  counts: { pending: number; approved: number; rejected: number }
  busy?: number | null
  /** A bulk decision is in flight; every control waits for it. */
  bulkBusy?: boolean
  offset?: number
  hasMore?: boolean
  onState?: (state: GazetteerState) => void
  onDecide?: (termId: number, decision: 'approve' | 'reject' | 'restore') => void
  onEdit?: (termId: number, entityType: GazetteerEntityType) => void
  onBulk?: (termIds: number[], decision: 'approve' | 'reject' | 'restore') => void
  onPage?: (offset: number) => void
  /** Find a term among thousands (`B-209`). */
  search?: string
  /** How many terms the search matched, when there is one: the pager counts these. */
  matched?: number | null
  onSearch?: (text: string) => void
}

/** How many rows a page holds. Well inside `GAZETTEER_BULK_MAX`, so a whole
 * page can always be decided in one request. */
export const PAGE_SIZE = 100

const STATES: { key: GazetteerState; label: string }[] = [
  { key: 'pending', label: 'waiting' },
  { key: 'approved', label: 'approved' },
  { key: 'rejected', label: 'turned down' },
  { key: 'all', label: 'all' },
]

/**
 * One class for every per-row decision button, which is the point rather than
 * tidiness: approve and turn-down must be *identical* — any difference in
 * weight reads as one being the safe option, and here neither is.
 */
const DECISION = BUTTON_ROW

/** Cells a step tighter than Admin's other tables: this is the one list that
 * runs to thousands of rows, and a row per line is what makes it workable. */
const CELL = TD.replace('py-2.5', 'py-[6px]').replace('align-baseline', 'align-middle')
const CELLM = TDM.replace('py-2.5', 'py-[6px]').replace('align-baseline', 'align-middle')

/** The same, for the decisions that act on a selection. */
const BULK_DECISION =
  'inline-flex h-[30px] items-center whitespace-nowrap border border-line-strong bg-surface px-3 ' +
  'text-[12.5px] text-text disabled:cursor-not-allowed disabled:opacity-45'

export const ENTITY_TYPES: GazetteerEntityType[] = ['agency', 'scheme', 'infrastructure', 'metric', 'concept']

/** The keys the table answers to, as the hint line prints them. */
export const KEYS: { key: string; does: string }[] = [
  { key: 'j / k', does: 'move' },
  { key: 'x', does: 'select' },
  { key: 'a', does: 'approve' },
  { key: 'd', does: 'turn down' },
  { key: 'u', does: 'put back' },
]

/**
 * What the matcher does with this row, in a sentence. The reason is named rather than a
 * boolean, because each reason has a different fix.
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

/** The verdicts that need somebody to act, and so carry the attention accent. */
function needsAttention(row: GazetteerRow): boolean {
  return row.withheld_reason === 'collision' || row.withheld_reason === 'no_patterns'
}

/** Documents, not mentions: one report repeating a definition forty times has
 * said one thing forty times. */
export function documents(count: number): string {
  return count === 1 ? '1 document' : `${count.toLocaleString()} documents`
}

/** The decision a key asks for on a row, or null when that row cannot take it. */
export function keyDecision(key: string, row: GazetteerRow): 'approve' | 'reject' | 'restore' | null {
  const rejected = row.term.rejected_at !== null
  if (key === 'a' && !rejected && !row.term.approved) return 'approve'
  if (key === 'd' && !rejected) return 'reject'
  if (key === 'u' && rejected) return 'restore'
  return null
}

export function GazetteerQueue({
  rows,
  state,
  counts,
  busy = null,
  bulkBusy = false,
  offset = 0,
  hasMore = false,
  onState,
  onDecide,
  onEdit,
  onBulk,
  onPage,
  search,
  matched = null,
  onSearch,
}: GazetteerQueueProps) {
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [cursor, setCursor] = useState(0)
  const body = useRef<HTMLTableSectionElement>(null)

  // A new page or a refetch is a new set of rows: a selection of ids that are
  // no longer on screen would be decided without being seen.
  // And somebody working through the queue by keyboard keeps their place: the
  // row they decided leaves the page, and focus goes to the one that took its
  // place rather than falling back to the document.
  const keyboarding = useRef(false)
  useEffect(() => {
    const onScreen = new Set(rows.map((r) => r.term.term_id))
    setSelected((prev) => new Set([...prev].filter((id) => onScreen.has(id))))
    const next = Math.min(cursor, Math.max(rows.length - 1, 0))
    setCursor(next)
    if (keyboarding.current && document.activeElement === document.body) {
      body.current?.querySelectorAll<HTMLTableRowElement>('tr[data-term]')[next]?.focus()
    }
    // Only a new set of rows should move focus, not a cursor step.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows])

  const shown: Record<GazetteerState, number> = {
    pending: counts.pending,
    approved: counts.approved,
    rejected: counts.rejected,
    all: counts.pending + counts.approved + counts.rejected,
  }
  const total = matched ?? shown[state]
  const of = matched === null ? total.toLocaleString() : `${total.toLocaleString()} matching`
  const chosen = rows.filter((r) => selected.has(r.term.term_id))
  const allChosen = rows.length > 0 && chosen.length === rows.length
  const anyRejected = chosen.some((r) => r.term.rejected_at !== null)
  const anyOpen = chosen.some((r) => r.term.rejected_at === null)

  const toggle = (termId: number) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(termId)) next.delete(termId)
      else if (next.size < GAZETTEER_BULK_MAX) next.add(termId)
      return next
    })

  const bulk = (decision: 'approve' | 'reject' | 'restore') => {
    const ids = chosen
      .filter((r) => (decision === 'restore' ? r.term.rejected_at !== null : r.term.rejected_at === null))
      .map((r) => r.term.term_id)
    if (ids.length) onBulk?.(ids, decision)
  }

  const focusRow = (index: number) => {
    setCursor(index)
    body.current?.querySelectorAll<HTMLTableRowElement>('tr[data-term]')[index]?.focus()
  }

  const onKey = (event: KeyboardEvent<HTMLTableSectionElement>) => {
    // Keys belong to the table, not the page, and never to a field somebody is
    // typing in or a control that already handles them.
    const target = event.target as HTMLElement
    if (target.closest('select, input, button')) return
    if (event.metaKey || event.ctrlKey || event.altKey) return
    keyboarding.current = true
    const row = rows[cursor]
    if (!row) return
    if (event.key === 'j' || event.key === 'ArrowDown') {
      event.preventDefault()
      focusRow(Math.min(cursor + 1, rows.length - 1))
    } else if (event.key === 'k' || event.key === 'ArrowUp') {
      event.preventDefault()
      focusRow(Math.max(cursor - 1, 0))
    } else if (event.key === 'x' || event.key === ' ') {
      event.preventDefault()
      toggle(row.term.term_id)
    } else {
      const decision = keyDecision(event.key, row)
      if (decision && busy === null && !bulkBusy) {
        event.preventDefault()
        onDecide?.(row.term.term_id, decision)
      }
    }
  }

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Gazetteer approvals">
        Terms the crawl proposed by reading acronym definitions out of documents. An approved term takes precedence over
        the statistical model wherever it matches, so it is worth being sure.
      </PageHeader>

      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Counts are unfiltered, so a queue reading "waiting (0)" while forty
            sit under another tab cannot happen. */}
        <span className="flex flex-wrap items-center gap-3">
          <Filters
            options={STATES.map(({ key, label }) => ({ key, label, count: shown[key] }))}
            value={state}
            onChange={onState}
          />
          {onSearch ? (
            <input
              type="search"
              aria-label="Find a term"
              placeholder="Find a term"
              value={search ?? ''}
              onChange={(event) => onSearch(event.target.value)}
              className="h-8 w-48 border border-line-strong bg-surface px-2 text-[12.5px] text-text placeholder:text-text-faint"
            />
          ) : null}
        </span>
        <div className="flex items-center gap-2 font-mono text-[11px] text-text-faint">
          <span>
            {rows.length === 0
              ? `0 of ${of}`
              : `${(offset + 1).toLocaleString()}–${(offset + rows.length).toLocaleString()} of ${of}`}
          </span>
          <button
            type="button"
            className={PAGER}
            disabled={offset === 0 || bulkBusy}
            onClick={() => onPage?.(Math.max(offset - PAGE_SIZE, 0))}
          >
            Previous
          </button>
          <button
            type="button"
            className={PAGER}
            disabled={!hasMore || bulkBusy}
            onClick={() => onPage?.(offset + PAGE_SIZE)}
          >
            Next
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
          {matched !== null && search?.trim()
            ? `No term under this tab contains “${search.trim()}”. Another tab may hold it.`
            : 'Nothing here. The harvest files terms as it reads documents, so an empty queue means every definition found so far has been decided.'}
        </p>
      ) : (
        <>
          <div className="flex min-h-[30px] flex-wrap items-center gap-2">
            <span className="mr-1 font-mono text-[11px] text-text-muted" aria-live="polite">
              {chosen.length ? `${chosen.length} selected` : 'None selected'}
            </span>
            {anyOpen ? (
              <>
                <button
                  type="button"
                  className={BULK_DECISION}
                  disabled={bulkBusy}
                  onClick={() => bulk('approve')}
                  data-bulk="approve"
                >
                  Approve selected
                </button>
                <button
                  type="button"
                  className={BULK_DECISION}
                  disabled={bulkBusy}
                  onClick={() => bulk('reject')}
                  data-bulk="reject"
                >
                  Turn down selected
                </button>
              </>
            ) : null}
            {anyRejected ? (
              <button
                type="button"
                className={BULK_DECISION}
                disabled={bulkBusy}
                onClick={() => bulk('restore')}
                data-bulk="restore"
              >
                Put back selected
              </button>
            ) : null}
            <span className="ml-auto font-mono text-[10.5px] text-text-faint">
              {KEYS.map(({ key, does }) => `${key} ${does}`).join(' · ')}
            </span>
          </div>

          <TableCard>
            <thead>
              <tr>
                <th className={`${TH} w-8`}>
                  <input
                    type="checkbox"
                    className="accent-accent-graph"
                    aria-label="Select every term on this page"
                    checked={allChosen}
                    disabled={bulkBusy}
                    onChange={() => setSelected(allChosen ? new Set() : new Set(rows.map((r) => r.term.term_id)))}
                  />
                </th>
                <th className={TH}>Term</th>
                <th className={TH}>Type</th>
                <th className={`${TH} text-right`}>Found in</th>
                <th className={TH}>Matcher</th>
                <th className={TH}>
                  <span className="sr-only">Decision</span>
                </th>
              </tr>
            </thead>
            <tbody ref={body} onKeyDown={onKey}>
              {rows.map((row, index) => {
                const id = row.term.term_id
                const rowBusy = busy === id || bulkBusy
                return (
                  <tr
                    key={id}
                    data-term={id}
                    tabIndex={index === cursor ? 0 : -1}
                    onFocus={() => setCursor(index)}
                    aria-selected={selected.has(id)}
                    className={`${ROW} outline-none focus:bg-surface-raised ${
                      selected.has(id) ? 'bg-accent-graph/5' : ''
                    }`}
                  >
                    <td className={`${CELL} w-8`}>
                      <input
                        type="checkbox"
                        className="accent-accent-graph"
                        aria-label={`Select ${row.term.canonical}`}
                        checked={selected.has(id)}
                        disabled={bulkBusy}
                        onChange={() => toggle(id)}
                      />
                    </td>
                    <td className={`${CELL} min-w-[220px]`}>
                      <span className="text-[13px] text-text">{row.term.canonical}</span>
                      {row.term.aliases && row.term.aliases.length > 0 ? (
                        <span className="ml-2 font-mono text-[11px] text-text-faint">
                          {row.term.aliases.join(' · ')}
                        </span>
                      ) : null}
                    </td>
                    <td className={CELL}>
                      <label>
                        <span className="sr-only">Type for {row.term.canonical}</span>
                        {/* A harvested term arrives as `concept` because a
                            regex cannot tell an agency from a metric.
                            Correcting that is most of the work of approving
                            one. */}
                        <select
                          value={row.term.entity_type}
                          disabled={rowBusy}
                          onChange={(event) => onEdit?.(id, event.target.value as GazetteerEntityType)}
                          className="h-[24px] border border-line-strong bg-surface px-1.5 font-mono text-[11px] text-text"
                        >
                          {ENTITY_TYPES.map((type) => (
                            <option key={type} value={type}>
                              {type}
                            </option>
                          ))}
                        </select>
                      </label>
                    </td>
                    <td className={`${CELLM} whitespace-nowrap text-right text-text-muted`}>
                      {documents(row.term.occurrence_count)}
                      {/* Harvested is the ordinary case and goes unsaid; a
                          term that came from anywhere else says where. */}
                      {row.term.source !== 'auto_acronym' ? (
                        <div className="text-[10.5px] text-text-faint">{row.term.source}</div>
                      ) : null}
                    </td>
                    <td
                      className={`${CELL} min-w-[240px] max-w-[360px] text-[12px] ${
                        needsAttention(row)
                          ? 'text-accent-attention'
                          : // Waiting is the ordinary state of this queue; it
                            // recedes so the verdicts that differ stand out.
                            row.withheld_reason === 'unapproved'
                            ? 'text-text-faint'
                            : 'text-text-muted'
                      }`}
                    >
                      {verdictOf(row)}
                    </td>
                    <td className={`${CELL} whitespace-nowrap text-right`} data-decisions={id}>
                      <span className="inline-flex gap-1.5">
                        {row.term.rejected_at ? (
                          <button
                            type="button"
                            disabled={rowBusy}
                            onClick={() => onDecide?.(id, 'restore')}
                            className={DECISION}
                          >
                            Put back
                          </button>
                        ) : (
                          <>
                            <button
                              type="button"
                              disabled={rowBusy || row.term.approved}
                              onClick={() => onDecide?.(id, 'approve')}
                              className={DECISION}
                            >
                              Approve
                            </button>
                            <button
                              type="button"
                              disabled={rowBusy}
                              onClick={() => onDecide?.(id, 'reject')}
                              className={DECISION}
                            >
                              Turn down
                            </button>
                          </>
                        )}
                      </span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </TableCard>
        </>
      )}
    </section>
  )
}
