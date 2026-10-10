import { useState } from 'react'

import type { TopicRow } from '../lib/api'
import { BUTTON_PRIMARY, BUTTON_ROW, FIELD, LINK_ACTION, ROW, SubHeading, TD, TDM, TH, TableCard } from './ui'
import { dayOf, startOfDayIso } from '../lib/time'

/**
 * Boosts (task P6-28, spec §10; `AdminLight`'s "Active boosts"): one per topic, with no
 * Term column, and a factor only together with an expiry. See docs/features/web-app.md#pins-and-boosts.
 */

export interface BoostChange {
  boost_factor: number | null
  boost_expires_at: string | null
}

/** A boost the row still records but no longer applies — kept as the audit trail. */
export function boostExpired(row: TopicRow): boolean {
  return row.topic.boost_factor !== null && row.topic.boost_expires_at !== null && !row.boost_active
}

/** `YYYY-MM-DD` in the display zone, `days` from now. */
export function dayFromNow(days: number, now: Date = new Date()): string {
  return dayOf(new Date(now.getTime() + days * 86_400_000))
}

/** A multiplier as typed, or null unless it is a positive number. */
export function factorOf(text: string): number | null {
  const value = Number(text)
  return text.trim() !== '' && Number.isFinite(value) && value > 0 ? value : null
}

export function BoostsTable({
  rows,
  busy = null,
  includeExpired = false,
  title = 'Active boosts',
  onBoost,
}: {
  rows: readonly TopicRow[]
  busy?: string | null
  includeExpired?: boolean
  title?: string
  onBoost?: (topic: string, change: BoostChange) => void
}) {
  const [adding, setAdding] = useState(false)
  const candidates = rows.filter((row) => row.topic.status !== 'archived')
  const [topic, setTopic] = useState('')
  const [factor, setFactor] = useState('1.5')
  const [expires, setExpires] = useState(() => dayFromNow(14))

  // Running boosts first: they are what is acting on the crawl now.
  const shown = rows
    .filter((row) => row.boost_active || (includeExpired && boostExpired(row)))
    .sort((a, b) => Number(b.boost_active) - Number(a.boost_active))
  const chosen = topic || candidates[0]?.topic.topic || ''
  const multiplier = factorOf(factor)
  const replacing = rows.find((row) => row.topic.topic === chosen && row.boost_active)

  /** An expired boost run again: the form opens on its topic and multiplier (`B-211`). */
  function runAgain(row: TopicRow) {
    setTopic(row.topic.topic)
    setFactor(String(row.topic.boost_factor ?? 1.5))
    setExpires(dayFromNow(14))
    setAdding(true)
  }

  return (
    <div className="flex flex-col gap-3">
      <SubHeading
        action={
          <button
            type="button"
            className={LINK_ACTION}
            aria-expanded={adding}
            disabled={candidates.length === 0}
            onClick={() => setAdding((v) => !v)}
          >
            {adding ? 'Close' : '+ Add boost'}
          </button>
        }
      >
        {title}
      </SubHeading>

      {adding ? (
        <form
          className="flex flex-wrap items-end gap-3 border border-line bg-surface px-[18px] py-3.5"
          onSubmit={(event) => {
            event.preventDefault()
            if (!chosen || multiplier === null || !expires) return
            onBoost?.(chosen, {
              boost_factor: multiplier,
              // The start of the chosen day in the display zone: a date is what a person
              // picks, and the server needs a moment (ADR 0009).
              boost_expires_at: startOfDayIso(expires),
            })
            setAdding(false)
          }}
        >
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[9px] uppercase tracking-[var(--tracking-label)] text-text-faint">
              Topic
            </span>
            <select
              className={`${FIELD} min-w-44 font-mono text-[12px]`}
              value={chosen}
              onChange={(event) => setTopic(event.target.value)}
              aria-label="Boost topic"
            >
              {candidates.map((row) => (
                <option key={row.topic.topic} value={row.topic.topic}>
                  {row.topic.topic}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[9px] uppercase tracking-[var(--tracking-label)] text-text-faint">
              Multiplier
            </span>
            <input
              className={`${FIELD} w-24 font-mono text-[12px]`}
              value={factor}
              inputMode="decimal"
              onChange={(event) => setFactor(event.target.value)}
              aria-label="Boost multiplier"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="font-mono text-[9px] uppercase tracking-[var(--tracking-label)] text-text-faint">
              Expires
            </span>
            <input
              type="date"
              className={`${FIELD} font-mono text-[12px]`}
              value={expires}
              min={dayFromNow(1)}
              onChange={(event) => setExpires(event.target.value)}
              aria-label="Boost expires"
            />
          </label>
          <button
            type="submit"
            className={BUTTON_PRIMARY}
            disabled={!chosen || multiplier === null || !expires || busy !== null}
          >
            Add boost
          </button>
          <span className="basis-full text-[12px] text-text-faint">
            {replacing
              ? `Replaces its running ${replacing.topic.boost_factor}× boost until ${
                  replacing.topic.boost_expires_at ? dayOf(replacing.topic.boost_expires_at) : '—'
                }. `
              : null}
            Multiplies the topic’s weight until the date, then stops counting on its own. The stored weight is
            untouched.
          </span>
        </form>
      ) : null}

      <TableCard>
        <thead>
          <tr>
            <th className={`${TH} w-full`}>Topic</th>
            <th className={TH}>Multiplier</th>
            <th className={TH}>Expires</th>
            <th className={TH}>
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {shown.length === 0 ? (
            <tr className={ROW}>
              <td colSpan={4} className={`${TD} text-text-muted`}>
                No boost is running. A boost multiplies one topic’s weight until it expires.
              </td>
            </tr>
          ) : (
            shown.map((row) => (
              <tr key={row.topic.topic} className={ROW} data-topic={row.topic.topic}>
                <td className={TDM}>{row.topic.topic}</td>
                <td className={`${TDM} whitespace-nowrap`}>{row.topic.boost_factor}×</td>
                <td className={`${TDM} sm:whitespace-nowrap ${row.boost_active ? '' : 'text-text-faint'}`}>
                  {row.boost_active ? '' : 'expired '}
                  {row.topic.boost_expires_at ? (
                    <span className="whitespace-nowrap">{dayOf(row.topic.boost_expires_at)}</span>
                  ) : null}
                </td>
                <td className={`${TD} whitespace-nowrap text-right`}>
                  {row.boost_active ? (
                    <button
                      type="button"
                      className={BUTTON_ROW}
                      disabled={busy === row.topic.topic}
                      onClick={() => onBoost?.(row.topic.topic, { boost_factor: null, boost_expires_at: null })}
                    >
                      End now
                    </button>
                  ) : (
                    // The steering log keeps the record, so clearing loses nothing.
                    // Stacked on a phone, where two buttons side by side ran off the card.
                    <span className="flex flex-col items-end gap-2 sm:flex-row sm:justify-end">
                      <button
                        type="button"
                        className={BUTTON_ROW}
                        disabled={busy !== null || row.topic.status === 'archived'}
                        onClick={() => runAgain(row)}
                      >
                        Run again
                      </button>
                      <button
                        type="button"
                        className={BUTTON_ROW}
                        disabled={busy === row.topic.topic}
                        title="Remove the expired boost. The steering log keeps the record."
                        onClick={() => onBoost?.(row.topic.topic, { boost_factor: null, boost_expires_at: null })}
                      >
                        Clear
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </TableCard>
    </div>
  )
}
