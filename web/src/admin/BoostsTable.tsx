import { useState } from 'react'

import type { TopicRow } from '../lib/api'
import {
  BUTTON_PRIMARY,
  BUTTON_ROW,
  FIELD,
  LINK_ACTION,
  ROW,
  SubHeading,
  TD,
  TDM,
  TH,
  TableCard,
} from './ui'

/**
 * Boosts (task P6-28, spec §10; `AdminLight`'s "Active boosts").
 *
 * **A boost here belongs to a topic.** The mock's table has a Term column —
 * "covered walkway 1.8×" — and this system has no term boosts: §10's boost is a
 * multiplier on one topic's weight with an expiry, stored on the topic row. The
 * column is left out rather than filled with the topic name twice, which would
 * suggest a finer control than exists.
 *
 * **Both a factor and an expiry, or neither.** §10 makes decay the mechanism
 * that removes a boost ("steer back later without needing to remember"), so the
 * form cannot submit a factor without a date, and the server refuses one
 * anyway. Ending a boost early clears both.
 */

export interface BoostChange {
  boost_factor: number | null
  boost_expires_at: string | null
}

/** A boost the row still records but no longer applies — kept as the audit trail. */
export function boostExpired(row: TopicRow): boolean {
  return row.topic.boost_factor !== null && row.topic.boost_expires_at !== null && !row.boost_active
}

/** `YYYY-MM-DD`, `days` from now. */
export function dayFromNow(days: number, now: Date = new Date()): string {
  return new Date(now.getTime() + days * 86_400_000).toISOString().slice(0, 10)
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

  const shown = rows.filter((row) => row.boost_active || (includeExpired && boostExpired(row)))
  const chosen = topic || candidates[0]?.topic.topic || ''
  const multiplier = factorOf(factor)

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
              // Midnight UTC at the start of the chosen day: a date is what
              // a person picks, and the server needs a moment.
              boost_expires_at: `${expires}T00:00:00Z`,
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
            Multiplies the topic’s weight until the date, then stops counting on its own. The stored
            weight is untouched.
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
                <td
                  className={`${TDM} whitespace-nowrap ${row.boost_active ? '' : 'text-text-faint'}`}
                >
                  {row.boost_active ? '' : 'expired '}
                  {row.topic.boost_expires_at?.slice(0, 10)}
                </td>
                <td className={`${TD} whitespace-nowrap text-right`}>
                  {row.boost_active ? (
                    <button
                      type="button"
                      className={BUTTON_ROW}
                      disabled={busy === row.topic.topic}
                      onClick={() =>
                        onBoost?.(row.topic.topic, { boost_factor: null, boost_expires_at: null })
                      }
                    >
                      End now
                    </button>
                  ) : null}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </TableCard>
    </div>
  )
}
