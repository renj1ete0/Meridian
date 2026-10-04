/**
 * The steering-proposals client (task P6-38). Mirrors
 * `meridian_core/schemas/steering_proposals.py`; `tests/admin-proposals.test.tsx`
 * fails if a field is added on one side only.
 *
 * Its own module, like `gaps.ts`, and the same error shape (`ApiError`,
 * `describeDetail`), so a refusal reads the same as everywhere else in Admin.
 */
import { ApiError, describeDetail } from './api'
import { stampOf, zoneLabel } from './time'

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

/** `PROPOSAL_KIND` and `PROPOSAL_STATUS` in `models/config.py`. */
export type ProposalKind = 'boost' | 'weight'
export type ProposalStatus = 'pending' | 'rejected' | 'applied' | 'superseded' | 'failed'

/** Mirrors `SteeringProposalRead`. */
export interface Proposal {
  proposal_id: number
  created_at: string
  actor: string
  topic: string
  kind: ProposalKind
  current_value: number
  proposed_value: number
  expires_at: string | null
  reason: string
  evidence: Record<string, string | number | null>
  apply_after: string
  status: ProposalStatus
  decided_by: string | null
  decided_at: string | null
  applied_at: string | null
  note: string | null
}

/** Mirrors `SteeringProposalsRead`. */
export interface Proposals {
  pending: Proposal[]
  recent: Proposal[]
  window_hours: number
}

export const PROPOSAL_FIELDS = [
  'proposal_id',
  'created_at',
  'actor',
  'topic',
  'kind',
  'current_value',
  'proposed_value',
  'expires_at',
  'reason',
  'evidence',
  'apply_after',
  'status',
  'decided_by',
  'decided_at',
  'applied_at',
  'note',
] as const
export const PROPOSALS_FIELDS = ['pending', 'recent', 'window_hours'] as const

export type AssertProposal = Expect<Equal<keyof Proposal, (typeof PROPOSAL_FIELDS)[number]>>
export type AssertProposals = Expect<Equal<keyof Proposals, (typeof PROPOSALS_FIELDS)[number]>>

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { ...init, headers: { accept: 'application/json', ...init?.headers } })
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause
    throw new ApiError(0, 'The API is unreachable. Check it is running.')
  }
  if (!response.ok) {
    let detail: unknown
    try {
      detail = (await response.json())?.detail
    } catch {
      detail = undefined
    }
    throw new ApiError(response.status, describeDetail(detail, response.status))
  }
  return (await response.json()) as T
}

export function getProposals(init?: RequestInit): Promise<Proposals> {
  return call<Proposals>('/api/admin/proposals', init)
}

export function acceptProposal(proposalId: number): Promise<Proposal> {
  return call<Proposal>(`/api/admin/proposals/${proposalId}/accept`, { method: 'POST' })
}

export function rejectProposal(proposalId: number, reason: string | null): Promise<Proposal> {
  const trimmed = reason?.trim() || null
  return call<Proposal>(`/api/admin/proposals/${proposalId}/reject`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ reason: trimmed }),
  })
}

// --------------------------------------------------------------------------
// What the panel prints
// --------------------------------------------------------------------------

/** The change in one line: `boost ×1.5 for 24 h`, `weight 0.33 → 0.28`. */
export function changeLine(p: Proposal): string {
  if (p.kind === 'boost') {
    const hours = p.evidence.boost_hours
    return `boost ×${p.proposed_value}${typeof hours === 'number' ? ` for ${hours} h` : ''}`
  }
  return `weight ${p.current_value.toFixed(2)} → ${p.proposed_value.toFixed(2)}`
}

function pct(v: unknown): string {
  return typeof v === 'number' ? `${Math.round(v * 100)}%` : '—'
}

/**
 * The numbers a proposal rests on, as short labelled facts in a fixed order.
 * Known keys only: a key the server adds later is not printed as a raw name.
 */
export function evidenceFacts(evidence: Proposal['evidence']): string[] {
  const has = (k: string) => Object.prototype.hasOwnProperty.call(evidence, k)
  const out: string[] = []
  if (has('draw_share')) out.push(`crawl share ${pct(evidence.draw_share)}`)
  if (has('new_sources'))
    out.push(
      `new sources ${evidence.new_sources} of ${evidence.new_sources_total ?? '—'} (${pct(evidence.new_source_share)})`,
    )
  if (has('fetches'))
    out.push(`fetches ${evidence.fetches} of ${evidence.fetches_total ?? '—'} (${pct(evidence.fetch_share)})`)
  // Yield decides a weight cut (`B-64`); null when the topic drew no fetches.
  if (has('yield_per_fetch')) {
    const y = evidence.yield_per_fetch
    const mean = evidence.mean_yield_per_fetch
    out.push(
      `yield ${typeof y === 'number' ? y.toFixed(2) : '—'} per fetch` +
        (typeof mean === 'number' ? ` (crawl ${mean.toFixed(2)})` : ''),
    )
  }
  if (has('labelled_sources')) out.push(`labelled sources ${evidence.labelled_sources}`)
  if (has('lookback_hours')) out.push(`last ${evidence.lookback_hours} h`)
  return out
}

/** `2026-09-24 21:40 GMT+8`: a time as Admin prints it, in the display zone (ADR 0009). */
export function zoned(iso: string): string {
  return `${stampOf(iso)} ${zoneLabel(iso)}`
}

/** How long until it applies, in the largest unit that is not zero. */
export function untilLine(applyAfter: string, now: Date): string {
  const ms = Date.parse(applyAfter) - now.getTime()
  if (ms <= 0) return 'at the next pass'
  const minutes = Math.round(ms / 60_000)
  if (minutes < 60) return `in ${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `in ${hours} h ${rest} min` : `in ${hours} h`
}

/** A decided proposal's outcome, as the recent list says it. */
export function outcomeLine(p: Proposal): string {
  switch (p.status) {
    case 'applied':
      return p.decided_by === 'proposal' || p.decided_by === null
        ? 'Applied with no objection'
        : `Accepted by ${p.decided_by}`
    case 'rejected':
      return p.note ? `Rejected: ${p.note}` : 'Rejected'
    case 'superseded':
      return p.note ? `Superseded: ${p.note}` : 'Superseded'
    case 'failed':
      return p.note ? `Refused by steering: ${p.note}` : 'Refused by steering'
    default:
      return 'Waiting'
  }
}
