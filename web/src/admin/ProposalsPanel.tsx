import { useState } from 'react'

import {
  changeLine,
  evidenceFacts,
  outcomeLine,
  untilLine,
  zoned,
  type Proposal,
  type Proposals,
} from '../lib/proposals'
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  Badge,
  Card,
  FIELD,
  LABEL,
  PageHeader,
  ROW,
  SubHeading,
  TD,
  TDM,
  TH,
  TableCard,
  stamp,
} from './ui'

/**
 * Steering proposals (task P6-38, spec §10.1, §10.2).
 *
 * The operator's rule: the system proposes what to steer, and if nobody
 * objects it is steered that way. So the page leads with **when each one
 * applies by itself**, because that is the thing a reader is deciding against —
 * a proposal left alone is a decision, and the screen says so rather than
 * reading like a queue waiting for approval.
 *
 * Each proposal carries its reason in words and the numbers under it, in that
 * order: the sentence is what a person reads, the numbers are what they check
 * it against. Accept applies it now; Reject means it never applies, with an
 * optional reason that goes into the steering audit beside the rail.
 */

export interface ProposalsPanelProps {
  proposals: Proposals
  busy?: number | null
  /** Injected so "in 3 h" is testable. */
  now?: Date
  onAccept?: (proposalId: number) => void
  onReject?: (proposalId: number, reason: string | null) => void
}

export function ProposalsPanel({
  proposals,
  busy = null,
  now = new Date(),
  onAccept,
  onReject,
}: ProposalsPanelProps) {
  const { pending, recent, window_hours: windowHours } = proposals
  return (
    <section className="flex flex-col gap-6">
      <PageHeader title="Proposals">
        Changes the crawl proposes to its own steering, from a day of new sources and fetches
        against each topic’s weight. Each applies by itself {windowHours} hours after it is
        proposed unless you reject it; Accept applies it now. Pinned topics, and topics changed in
        the last day, are never proposed for.
      </PageHeader>

      <div className="flex flex-col gap-3">
        <SubHeading>Waiting ({pending.length})</SubHeading>
        {pending.length === 0 ? (
          <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
            Nothing is waiting. The pass runs hourly; a proposal appears here when a topic’s share
            of new sources falls well under its weight, or runs well over it.
          </p>
        ) : (
          pending.map((p) => (
            <PendingCard
              key={p.proposal_id}
              proposal={p}
              busy={busy === p.proposal_id}
              now={now}
              onAccept={onAccept}
              onReject={onReject}
            />
          ))
        )}
      </div>

      <div className="flex flex-col gap-3">
        <SubHeading>Recently decided</SubHeading>
        {recent.length === 0 ? (
          <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
            No proposal has been decided yet.
          </p>
        ) : (
          <TableCard>
            <thead>
              <tr>
                <th className={TH}>Decided</th>
                <th className={TH}>Topic</th>
                <th className={TH}>Change</th>
                <th className={`${TH} w-full`}>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((p) => (
                <tr key={p.proposal_id} className={ROW} data-proposal={p.proposal_id}>
                  <td className={`${TDM} whitespace-nowrap text-text-muted`}>
                    {stamp(p.decided_at ?? p.created_at)}
                  </td>
                  <td className={`${TD} whitespace-nowrap`}>{p.topic}</td>
                  <td className={`${TDM} whitespace-nowrap`}>{changeLine(p)}</td>
                  {/* A floor on its width: in a narrow window the table scrolls
                      sideways, and without one the outcome wraps a word per
                      line and makes every row tall. */}
                  <td className={`${TD} min-w-[18rem]`}>
                    <span className="flex items-baseline gap-2">
                      <Badge tone={p.status === 'failed' ? 'attention' : 'plain'}>{p.status}</Badge>
                      <span className="text-text-muted">{outcomeLine(p)}</span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </TableCard>
        )}
      </div>
    </section>
  )
}

function PendingCard({
  proposal: p,
  busy,
  now,
  onAccept,
  onReject,
}: {
  proposal: Proposal
  busy: boolean
  now: Date
  onAccept?: (proposalId: number) => void
  onReject?: (proposalId: number, reason: string | null) => void
}) {
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')
  const facts = evidenceFacts(p.evidence)

  return (
    <Card>
      <article
        className="flex flex-col gap-3 px-[18px] py-4"
        data-proposal={p.proposal_id}
        aria-label={`Proposal ${p.proposal_id}: ${p.topic}`}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h3 className="flex items-baseline gap-2.5 text-[14px] font-semibold text-text">
            {p.topic}
            <span className="font-mono text-[12px] font-normal text-accent-graph">
              {changeLine(p)}
            </span>
          </h3>
          <p className="font-mono text-[11px] text-text-muted">
            Applies automatically at <span className="text-text">{zoned(p.apply_after)}</span> ·{' '}
            {untilLine(p.apply_after, now)}
          </p>
        </div>

        <p className="max-w-[80ch] text-[13px] leading-[1.55] text-text">{p.reason}</p>

        {facts.length ? (
          <div className="flex flex-col gap-1.5">
            <span className={LABEL}>Evidence</span>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[11px] tabular-nums text-text-muted">
              {facts.map((fact) => (
                <li key={fact}>{fact}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {rejecting ? (
          <form
            className="flex flex-wrap items-center gap-2 border-t border-line/60 pt-3"
            onSubmit={(event) => {
              event.preventDefault()
              onReject?.(p.proposal_id, reason.trim() || null)
            }}
          >
            <label className="sr-only" htmlFor={`reject-${p.proposal_id}`}>
              Reason for rejecting (optional)
            </label>
            <input
              id={`reject-${p.proposal_id}`}
              className={`${FIELD} min-w-0 flex-1 sm:max-w-[48ch]`}
              placeholder="Why not (optional, goes in the steering audit)"
              maxLength={500}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              disabled={busy}
            />
            <button type="submit" className={BUTTON_SECONDARY} disabled={busy}>
              Reject
            </button>
            <button
              type="button"
              className="h-[34px] px-2 text-[12.5px] text-text-muted hover:text-text"
              onClick={() => {
                setRejecting(false)
                setReason('')
              }}
              disabled={busy}
            >
              Cancel
            </button>
          </form>
        ) : (
          <div className="flex flex-wrap items-center gap-2 border-t border-line/60 pt-3">
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={busy}
              onClick={() => onAccept?.(p.proposal_id)}
            >
              Accept now
            </button>
            <button
              type="button"
              className={BUTTON_SECONDARY}
              disabled={busy}
              onClick={() => setRejecting(true)}
            >
              Reject…
            </button>
            <span className="ml-auto font-mono text-[10.5px] text-text-faint">
              #{p.proposal_id} · proposed {stamp(p.created_at)}
            </span>
          </div>
        )}
      </article>
    </Card>
  )
}
