/**
 * Steering proposals in Admin (task P6-38, spec §10.1, §10.2).
 *
 * What matters on this screen: each pending proposal says when it applies by
 * itself, in words and with the numbers under it; Accept and Reject send
 * exactly the proposal they sit on; a reject reason is optional and sent
 * trimmed; the list is refetched after a decision, and a refusal from the
 * server is shown as the server wrote it. The client types are tied to the
 * pydantic DTOs so neither side can grow a field alone.
 */
// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AdminPage } from '../src/admin/AdminPage'
import { ProposalsPanel } from '../src/admin/ProposalsPanel'
import { auditLine } from '../src/admin/SteeringRail'
import { SECTIONS } from '../src/admin/sections'
import {
  PROPOSAL_FIELDS,
  PROPOSALS_FIELDS,
  changeLine,
  evidenceFacts,
  outcomeLine,
  untilLine,
  type Proposal,
  type Proposals,
} from '../src/lib/proposals'

// Vitest runs from web/.
const REPO = join(process.cwd(), '..')
const SCHEMAS = join(REPO, 'packages/meridian_core/meridian_core/schemas')

function pydanticFields(file: string, className: string): string[] {
  const source = readFileSync(join(SCHEMAS, file), 'utf8')
  const start = source.indexOf(`class ${className}(`)
  if (start === -1) throw new Error(`${file} has no class ${className}`)
  const rest = source.slice(start)
  const end = rest.slice(1).search(/^class /m)
  const body = (end === -1 ? rest : rest.slice(0, end + 1)).replace(/"""[\s\S]*?"""/g, '')
  return [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map((m) => m[1]!)
}

/** A `Literal[...]` value set, or the `constrained(...)` one it is built from. */
function modelValues(name: string): string[] {
  const source = readFileSync(join(REPO, 'packages/meridian_core/meridian_core/models/config.py'), 'utf8')
  const match = new RegExp(`${name} = constrained\\(([\\s\\S]*?)name=`).exec(source)
  if (!match) throw new Error(`${name} is not declared the way this test reads it`)
  return [...match[1]!.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]!)
}

const NOW = new Date('2026-09-24T12:00:00Z')

function proposal(over: Partial<Proposal> = {}): Proposal {
  return {
    proposal_id: 7,
    created_at: '2026-09-24T09:00:00Z',
    actor: 'steerproposals',
    topic: 'robotics',
    kind: 'boost',
    current_value: 1,
    proposed_value: 1.5,
    expires_at: '2026-09-25T21:00:00Z',
    reason:
      'robotics is weighted for 33% of the crawl and produced 5% of new on-topic sources in the last 24 hours (2 of 42).',
    evidence: {
      lookback_hours: 24,
      draw_share: 0.3333,
      new_sources: 2,
      new_sources_total: 42,
      new_source_share: 0.0476,
      fetches: 60,
      fetches_total: 300,
      fetch_share: 0.2,
      yield_per_fetch: 0.0333,
      mean_yield_per_fetch: 0.14,
      boost_hours: 24,
    },
    apply_after: '2026-09-24T21:00:00Z',
    status: 'pending',
    decided_by: null,
    decided_at: null,
    applied_at: null,
    note: null,
    ...over,
  }
}

function list(over: Partial<Proposals> = {}): Proposals {
  return { pending: [proposal()], recent: [], window_hours: 12, ...over }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// --------------------------------------------------------------------------
// Drift
// --------------------------------------------------------------------------

describe('the client matches the server', () => {
  it('has the fields of the DTOs, no more and no fewer', () => {
    const read = pydanticFields('steering_proposals.py', 'SteeringProposalRead')
    expect(read.length).toBeGreaterThan(10)
    expect([...PROPOSAL_FIELDS].sort()).toEqual(read.sort())
    expect([...PROPOSALS_FIELDS].sort()).toEqual(
      pydanticFields('steering_proposals.py', 'SteeringProposalsRead').sort(),
    )
  })

  it('knows every status and kind the database allows', () => {
    // `outcomeLine` falls through to "Waiting" for a status it does not know;
    // a status added on the server would then read as a pending proposal in the
    // decided list. Held to the model, not retyped.
    const statuses = modelValues('PROPOSAL_STATUS')
    expect(statuses.length).toBeGreaterThan(3)
    for (const status of statuses.filter((s) => s !== 'pending')) {
      expect(outcomeLine(proposal({ status: status as Proposal['status'] }))).not.toBe('Waiting')
    }
    for (const kind of modelValues('PROPOSAL_KIND')) {
      expect(changeLine(proposal({ kind: kind as Proposal['kind'] }))).toMatch(/^(boost|weight) /)
    }
  })

  it('is a section of Admin, in the steering group', () => {
    const def = SECTIONS.find((s) => s.key === 'proposals')
    expect(def?.group).toBe('steering')
    expect(def?.unbuilt).toBeUndefined()
  })
})

// --------------------------------------------------------------------------
// What is printed
// --------------------------------------------------------------------------

describe('what a proposal says', () => {
  it('names the change in one line', () => {
    expect(changeLine(proposal())).toBe('boost ×1.5 for 24 h')
    expect(changeLine(proposal({ kind: 'weight', current_value: 0.3333, proposed_value: 0.2833, evidence: {} }))).toBe(
      'weight 0.33 → 0.28',
    )
  })

  it('prints the evidence it knows, in order, and nothing it does not', () => {
    const facts = evidenceFacts({ ...proposal().evidence, surprise_key: 3 })
    expect(facts).toEqual([
      'crawl share 33%',
      'new sources 2 of 42 (5%)',
      'fetches 60 of 300 (20%)',
      'yield 0.03 per fetch (crawl 0.14)',
      'last 24 h',
    ])
    expect(facts.join(' ')).not.toContain('surprise')
  })

  it('says a topic that drew no fetches has no yield, rather than a yield of nothing', () => {
    const facts = evidenceFacts({ ...proposal().evidence, fetches: 0, yield_per_fetch: null })
    expect(facts).toContain('yield — per fetch (crawl 0.14)')
    expect(facts.join(' ')).not.toContain('0.00 per fetch')
  })

  it('counts down to when it applies, and never into the past', () => {
    expect(untilLine('2026-09-24T21:00:00Z', NOW)).toBe('in 9 h')
    expect(untilLine('2026-09-24T12:40:00Z', NOW)).toBe('in 40 min')
    expect(untilLine('2026-09-24T14:30:00Z', NOW)).toBe('in 2 h 30 min')
    expect(untilLine('2026-09-24T11:00:00Z', NOW)).toBe('at the next pass')
  })

  it('tells an auto-applied proposal from an accepted one', () => {
    expect(outcomeLine(proposal({ status: 'applied', decided_by: 'proposal' }))).toBe('Applied with no objection')
    expect(outcomeLine(proposal({ status: 'applied', decided_by: 'user' }))).toBe('Accepted by user')
    expect(outcomeLine(proposal({ status: 'rejected', note: 'not now' }))).toBe('Rejected: not now')
    expect(outcomeLine(proposal({ status: 'superseded', note: 'robotics is paused now' }))).toContain('paused')
  })

  it('shows a rejection in the audit rail as a rejection, not an arrow', () => {
    expect(
      auditLine({
        log_id: 1,
        changed_at: '2026-09-24T12:00:00Z',
        actor: 'user',
        topic: 'robotics',
        field: 'proposal',
        old_value: null,
        new_value: 'rejected: Boost robotics ×1.5 for 24 hours',
        reason: 'rejected proposal #7: not now',
      }),
    ).toBe('robotics proposal rejected')
  })
})

describe('the panel', () => {
  it('leads each pending proposal with when it applies by itself', () => {
    render(<ProposalsPanel proposals={list()} now={NOW} />)
    const card = screen.getByRole('article', { name: /Proposal 7/ })
    expect(card.textContent).toContain('Applies automatically at 2026-09-25 05:00 GMT+8')
    expect(card.textContent).toContain('in 9 h')
    expect(card.textContent).toContain('produced 5% of new on-topic sources')
    expect(card.textContent).toContain('new sources 2 of 42 (5%)')
    expect(screen.getByText(/Each applies by itself 12 hours after/)).toBeTruthy()
  })

  it('says so when nothing is waiting, rather than showing an empty table', () => {
    render(<ProposalsPanel proposals={list({ pending: [] })} now={NOW} />)
    expect(screen.getByText(/Nothing is waiting/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Accept now' })).toBeNull()
  })

  it('accepts exactly the proposal it sits on', () => {
    const onAccept = vi.fn()
    render(
      <ProposalsPanel
        proposals={list({ pending: [proposal(), proposal({ proposal_id: 8, topic: 'biology' })] })}
        now={NOW}
        onAccept={onAccept}
      />,
    )
    const card = screen.getByRole('article', { name: /Proposal 8/ })
    fireEvent.click(within(card).getByRole('button', { name: 'Accept now' }))
    expect(onAccept).toHaveBeenCalledWith(8)
  })

  it('asks for an optional reason before rejecting, and can be cancelled', () => {
    const onReject = vi.fn()
    render(<ProposalsPanel proposals={list()} now={NOW} onReject={onReject} />)
    fireEvent.click(screen.getByRole('button', { name: 'Reject…' }))
    // Opening the form sends nothing.
    expect(onReject).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByLabelText(/Reason for rejecting/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Reject…' }))
    fireEvent.change(screen.getByLabelText(/Reason for rejecting/), {
      target: { value: '  watching it myself  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    expect(onReject).toHaveBeenCalledWith(7, 'watching it myself')
  })

  it('sends no reason when none was written', () => {
    const onReject = vi.fn()
    render(<ProposalsPanel proposals={list()} now={NOW} onReject={onReject} />)
    fireEvent.click(screen.getByRole('button', { name: 'Reject…' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    expect(onReject).toHaveBeenCalledWith(7, null)
  })

  it('disables the buttons of the proposal being decided', () => {
    render(<ProposalsPanel proposals={list()} now={NOW} busy={7} />)
    expect((screen.getByRole('button', { name: 'Accept now' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('lists recent decisions with their outcome in words and in form', () => {
    render(
      <ProposalsPanel
        proposals={list({
          pending: [],
          recent: [proposal({ status: 'failed', note: 'outside bounds', decided_at: '2026-09-24T10:00:00Z' })],
        })}
        now={NOW}
      />,
    )
    const row = screen.getByText('Refused by steering: outside bounds').closest('tr')!
    expect(row.textContent).toContain('failed')
    expect(row.textContent).toContain('2026-09-24 18:00') // display zone, GMT+8
  })
})

// --------------------------------------------------------------------------
// Driven through Admin
// --------------------------------------------------------------------------

interface Call {
  method: string
  url: string
  body: unknown
}

function stubApi(refuse = false) {
  const calls: Call[] = []
  let decided = false
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null })
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

      if (url.includes('/first-run'))
        return json({ is_first_run: false, sources: 0, pending_seeds: [], seeds_in_flight: 0 })
      if (url.includes('/steering-log')) return json({ entries: [], limit: 60, has_more: false })
      if (url.includes('/runs')) return json({ rows: [], total: 0, active: null })
      if (url.endsWith('/accept') || url.endsWith('/reject')) {
        if (refuse) return json({ detail: 'proposal 7 is superseded, not pending.' }, 409)
        decided = true
        return json(proposal({ status: 'applied', decided_by: 'user' }))
      }
      if (url.includes('/api/admin/proposals'))
        return json(
          decided ? list({ pending: [], recent: [proposal({ status: 'applied', decided_by: 'user' })] }) : list(),
        )
      return json({})
    }),
  )
  return calls
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

describe('Admin › Proposals', () => {
  beforeEach(() => window.history.replaceState({}, '', '/admin/proposals'))

  it('opens on its own URL with the steering rail beside it', async () => {
    const calls = stubApi()
    render(<AdminPage />)
    await settle()
    expect(screen.getByRole('heading', { name: 'Proposals' })).toBeTruthy()
    expect(calls.some((c) => c.url === '/api/admin/proposals')).toBe(true)
    expect(calls.some((c) => c.url.includes('/steering-log'))).toBe(true)
  })

  it('accepts with one POST, then refetches and says what happened', async () => {
    const calls = stubApi()
    render(<AdminPage />)
    await settle()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Accept now' }))
    })
    await settle()
    const posts = calls.filter((c) => c.method === 'POST')
    expect(posts.map((c) => c.url)).toEqual(['/api/admin/proposals/7/accept'])
    expect(screen.getByRole('status').textContent).toBe('Applied now.')
    expect(screen.getByText('Accepted by user')).toBeTruthy()
  })

  it('rejects with the reason in the body', async () => {
    const calls = stubApi()
    render(<AdminPage />)
    await settle()
    fireEvent.click(screen.getByRole('button', { name: 'Reject…' }))
    fireEvent.change(screen.getByLabelText(/Reason for rejecting/), { target: { value: 'not now' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
    })
    await settle()
    const post = calls.find((c) => c.method === 'POST')!
    expect(post.url).toBe('/api/admin/proposals/7/reject')
    expect(post.body).toEqual({ reason: 'not now' })
  })

  it('shows the server’s refusal as written, and the list as it now is', async () => {
    const calls = stubApi(true)
    render(<AdminPage />)
    await settle()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Accept now' }))
    })
    await settle()
    expect(screen.getByRole('alert').textContent).toBe('proposal 7 is superseded, not pending.')
    expect(calls.filter((c) => c.url === '/api/admin/proposals').length).toBe(2)
  })
})
