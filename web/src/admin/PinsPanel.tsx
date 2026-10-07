import type { TopicRow } from '../lib/api'
import { BoostsTable, type BoostChange } from './BoostsTable'
import { weight } from './Reweight'
import { BUTTON_ROW, Badge, PageHeader, ROW, SubHeading, TD, TDM, TH, TableCard } from './ui'

/**
 * Pins & boosts (task P6-28, spec §10, §10.1): the two steering controls that are not a
 * weight, with expired boosts kept in view. See docs/features/web-app.md#pins-and-boosts.
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
        A pinned topic is one the orchestrator may not re-weight on its own; you still can. A boost multiplies a topic’s
        weight until it expires, then stops counting — nothing has to remember to undo it.
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
