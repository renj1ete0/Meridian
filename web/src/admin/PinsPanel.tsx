import type { TopicRow } from '../lib/api'
import { BoostsTable, type BoostChange } from './BoostsTable'
import { weight } from './Reweight'
import { BUTTON_ROW, Badge, PageHeader, ROW, SubHeading, TD, TDM, TH, TableCard } from './ui'

/**
 * Pins & boosts (task P6-28, spec §10, §10.1).
 *
 * The two steering controls that are not a weight. A pin is about *who* may
 * move a weight — §10.1's autonomous adjustment may not touch a pinned topic —
 * and a boost is about *how long*: a multiplier with an expiry that removes
 * itself. Both are on Topic weights too; here they are the whole page, with the
 * boosts that have expired kept in view, because an expired boost left on the
 * row is the record of what was boosted and until when.
 */

export function PinsPanel({
  rows,
  busy = null,
  onPinned,
  onBoost,
}: {
  rows: readonly TopicRow[]
  busy?: string | null
  onPinned?: (topic: string, pinned: boolean) => void
  onBoost?: (topic: string, change: BoostChange) => void
}) {
  const current = rows.filter((row) => row.topic.status !== 'archived')
  return (
    <section className="flex flex-col gap-6">
      <PageHeader title="Pins & boosts">
        A pinned topic is one the orchestrator may not re-weight on its own; you still can. A boost
        multiplies a topic’s weight until it expires, then stops counting — nothing has to remember
        to undo it.
      </PageHeader>

      <div className="flex flex-col gap-3">
        <SubHeading>Pins</SubHeading>
        <TableCard>
          <thead>
            <tr>
              <th className={`${TH} w-full`}>Topic</th>
              <th className={TH}>Weight</th>
              <th className={TH}>Status</th>
              <th className={TH}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {current.length === 0 ? (
              <tr className={ROW}>
                <td colSpan={4} className={`${TD} text-text-muted`}>
                  No topics to pin.
                </td>
              </tr>
            ) : (
              current.map((row) => (
                <tr key={row.topic.topic} className={ROW} data-topic={row.topic.topic}>
                  <td className={TD}>
                    <span className="flex items-baseline gap-2">
                      {row.topic.topic}
                      {row.topic.pinned ? <Badge tone="attention">Pinned</Badge> : null}
                    </span>
                  </td>
                  <td className={TDM}>{weight(row.topic.weight)}</td>
                  <td className={`${TDM} text-text-muted`}>{row.topic.status}</td>
                  <td className={`${TD} text-right`}>
                    <button
                      type="button"
                      className={BUTTON_ROW}
                      disabled={busy === row.topic.topic}
                      onClick={() => onPinned?.(row.topic.topic, !row.topic.pinned)}
                    >
                      {row.topic.pinned ? 'Unpin' : 'Pin'}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </TableCard>
      </div>

      <BoostsTable rows={rows} busy={busy} onBoost={onBoost} includeExpired title="Boosts" />
    </section>
  )
}
