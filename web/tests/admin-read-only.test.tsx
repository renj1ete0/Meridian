// @vitest-environment jsdom
/**
 * Admin for a person who may view a section but not change it (task B-258, ADR 0024): every
 * control that changes something is disabled and its title names the permission it needs;
 * controls that only read (filters, paging, search, disclosure) stay live. A control is one or
 * the other — `data-edit` from `useEdit`, or `data-view` — so a new control that is neither fails
 * here, and so does a new section with changes but no panel below.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AdminPage } from '../src/admin/AdminPage'
import { AgentsPanel } from '../src/admin/AgentsPanel'
import { DisplayPanel } from '../src/admin/DisplayPanel'
import { DuplicatesPanel } from '../src/admin/DuplicatesPanel'
import { EditGate, lockedReason } from '../src/admin/editGate'
import { FetchPolicyPanel } from '../src/admin/FetchPolicyPanel'
import { FirstRunPanel } from '../src/admin/FirstRunPanel'
import { GazetteerQueue } from '../src/admin/GazetteerQueue'
import { GraphAuditPanel } from '../src/admin/GraphAuditPanel'
import { PinsPanel } from '../src/admin/PinsPanel'
import { ProposalsPanel } from '../src/admin/ProposalsPanel'
import { SECTIONS, type Section } from '../src/admin/sections'
import { TopicPanel } from '../src/admin/TopicPanel'
import type { AgentRow, FetchPolicyRow, FirstRun, GazetteerRow, TopicConfig, TopicRow } from '../src/lib/api'
import type { DuplicatePair } from '../src/lib/duplicates'
import type { AuditDrawDetail, GraphAudit } from '../src/lib/graphAudit'
import { MeProvider } from '../src/lib/me'
import { BY_NAME_LABEL } from '../src/lib/permissionLabels'
import type { Me } from '../src/lib/people'
import type { Proposal } from '../src/lib/proposals'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

const me = (permissions: string[]): Me => ({
  subject: 'colleague@example.test',
  profile: 'analyst',
  owner: false,
  permissions,
  setup_needed: false,
})

// --------------------------------------------------------------------------
// One panel per section with changes, holding every kind of row it can show
// --------------------------------------------------------------------------

const topic = (over: Partial<TopicConfig> = {}): TopicRow => ({
  topic: {
    topic: 'walkability',
    weight: 0.4,
    floor: 0.05,
    ceiling: 0.6,
    boost_factor: null,
    boost_expires_at: null,
    pinned: false,
    status: 'active',
    description: null,
    ...over,
  },
  effective_weight: 0.4,
  share: 0.4,
  boost_active: false,
})

const TOPICS: TopicRow[] = [
  { ...topic({ boost_factor: 1.5, boost_expires_at: '2099-01-01T00:00:00Z' }), boost_active: true },
  topic({ topic: 'transit', boost_factor: 2, boost_expires_at: '2020-01-01T00:00:00Z' }),
  topic({ topic: 'archived-one', status: 'archived' }),
]

const agent = (over: Partial<AgentRow> = {}): AgentRow => ({
  agent_id: 'local-model',
  provider: 'openai_compatible',
  model: 'a-model',
  task_types: ['relation_extraction'],
  quality_tier: 2,
  cost_tier: 'free',
  availability: 'always',
  enabled: true,
  fallback_agent_id: null,
  route_order: null,
  endpoint: null,
  api_key_env_var: null,
  thinking: 'server',
  structured: 'server',
  key_present: true,
  blocked_by: [],
  ...over,
})

const domain = (name: string, over: Partial<FetchPolicyRow['policy']>): FetchPolicyRow => ({
  policy: {
    domain: name,
    settings: null,
    status: 'active',
    note: null,
    consecutive_failures: 0,
    render_js_escalations: 0,
    render_js_learned_at: null,
    seed_allowed: null,
    first_seen_via: null,
    novel_fetches: 0,
    trust_state: 'unscreened',
    clean_fetches: 0,
    trust_decided_at: null,
    trust_decided_by: null,
    trust_reason: null,
    updated_at: null,
    updated_by: null,
    ...over,
  },
  resolved: {
    delay_per_domain_ms: 1000,
    concurrency_per_domain: 2,
    timeout_s: 30,
    render_js: over.render_js_learned_at ? 'always' : 'auto',
    block_private_addresses: true,
  },
  overridden: [],
})

const term = (id: number, rejected: boolean): GazetteerRow => ({
  term: {
    term_id: id,
    canonical: `Term ${id}`,
    aliases: [],
    entity_type: 'concept',
    jurisdiction: null,
    ambiguous: false,
    topic_labels: null,
    source: 'auto_acronym',
    approved: false,
    occurrence_count: 3,
    rejected_at: rejected ? '2026-10-01T00:00:00Z' : null,
    created_at: '2026-09-15T00:00:00Z',
  },
  will_load: false,
  withheld_reason: rejected ? 'rejected' : 'unapproved',
  collides_with: [],
})

const side = (id: number) => ({
  entity_id: id,
  canonical_name: `node ${id}`,
  node_type: 'concept',
  jurisdiction: null,
  aliases: [],
  description: null,
  links: 1,
  passages: [],
})

const PAIR: DuplicatePair = {
  notification_id: 7,
  mention: 'a name',
  created: side(1),
  candidate: side(2),
  created_at: '2026-10-08T00:00:00Z',
}

const PROPOSAL: Proposal = {
  proposal_id: 7,
  created_at: '2026-09-24T09:00:00Z',
  actor: 'steerproposals',
  topic: 'walkability',
  kind: 'boost',
  current_value: 1,
  proposed_value: 1.5,
  expires_at: '2026-09-25T21:00:00Z',
  reason: 'A reason in words.',
  evidence: {},
  apply_after: '2026-09-24T21:00:00Z',
  status: 'pending',
  decided_by: null,
  decided_at: null,
  applied_at: null,
  note: null,
}

const RUN: FirstRun = {
  is_first_run: true,
  sources: 0,
  pending_seeds: [
    {
      task_id: 1,
      url_or_query: 'https://example.gov/',
      task_type: 'url',
      status: 'pending',
      priority: 100,
      topic: null,
      seed_source: 'user',
      attempts: 0,
      claimed_by: null,
      created_at: '2026-09-20T00:00:00Z',
    },
  ],
  seeds_in_flight: 0,
}

const AUDIT_TALLY = { drawn: 1, graded: 0, correct: 0, wrong: 0, unsure: 0, precision: null, low: null, high: null }
const AUDIT_DRAW: AuditDrawDetail = {
  draw: {
    draw_id: 1,
    drawn_at: '2026-10-01T00:00:00Z',
    window_start: '2026-09-01T00:00:00Z',
    window_end: '2026-10-01T00:00:00Z',
    seed: 's',
    drawn_by: 'scheduler',
    edge_population: 1,
    merge_population: 0,
    match_population: 0,
    merges_reversed: 0,
    edges: AUDIT_TALLY,
    merges: { ...AUDIT_TALLY, drawn: 0 },
    matches: { ...AUDIT_TALLY, drawn: 0 },
    by_producer: [],
  },
  samples: [
    {
      sample_id: 1,
      draw_id: 1,
      kind: 'edge',
      item_id: 1,
      position: 0,
      shown: {
        from: { entity_id: 1, name: 'one', node_type: 'concept' },
        relation: 'relates_to',
        to: { entity_id: 2, name: 'two', node_type: 'concept' },
        stance: null,
        certainty: null,
        similarity_dimension: null,
        disanalogy: null,
        valid_from: null,
        valid_to: null,
        chunk_ids: [],
        passages: [],
      },
      grade: null,
      note: null,
      graded_by: null,
      graded_at: null,
      split_entity_id: null,
    },
  ],
}
const AUDIT: GraphAudit = { draws: [AUDIT_DRAW.draw], ungraded: 1 }

/** Each section that has changes, as AdminPage renders it. A section added with `edits` needs one here. */
const PANELS: Partial<Record<Section, () => ReactElement>> = {
  topics: () => <TopicPanel rows={TOPICS} sumsTo={1} onDescribe={vi.fn()} />,
  boosts: () => <PinsPanel rows={TOPICS} />,
  proposals: () => <ProposalsPanel proposals={{ pending: [PROPOSAL], recent: [], window_hours: 12 }} />,
  seeds: () => <FirstRunPanel run={RUN} />,
  agents: () => <AgentsPanel rows={[agent()]} unserved={[]} onModel={vi.fn()} />,
  gazetteer: () => (
    <GazetteerQueue
      rows={[term(1, false), term(2, true)]}
      state="all"
      counts={{ pending: 1, approved: 0, rejected: 1 }}
      hasMore
      onSearch={vi.fn()}
    />
  ),
  duplicates: () => <DuplicatesPanel pairs={[PAIR]} total={1} onDecide={vi.fn()} onUndo={vi.fn()} />,
  audit: () => <GraphAuditPanel initial={AUDIT} initialDraw={AUDIT_DRAW} grade={vi.fn()} />,
  domains: () => (
    <FetchPolicyPanel
      rows={[
        domain('blocked.test', { status: 'blocked', consecutive_failures: 4 }),
        domain('learned.test', { render_js_learned_at: '2026-10-01T00:00:00Z', render_js_escalations: 3 }),
      ]}
      counts={{ active: 1, paused: 0, blocked: 1 }}
      hasMore
    />
  ),
  display: () => <DisplayPanel initial={{ display_timezone: 'UTC', label: 'GMT' }} save={vi.fn()} />,
}

function controls(root: ParentNode): HTMLInputElement[] {
  return Array.from(root.querySelectorAll<HTMLInputElement>('button, input, select, textarea'))
}

function name(el: Element): string {
  return el.getAttribute('aria-label') ?? el.textContent?.trim() ?? el.tagName
}

async function renderAs(permissions: string[], def: (typeof SECTIONS)[number]) {
  const view = render(
    <MeProvider load={async () => me(permissions)}>
      <EditGate permission={def.edits}>{PANELS[def.key]!()}</EditGate>
    </MeProvider>,
  )
  // `can` answers yes until `/api/me` has; wait for the answer.
  await act(async () => undefined)
  // Open what is folded away, so the controls inside are checked too.
  for (const toggle of view.container.querySelectorAll<HTMLElement>('[data-view][aria-expanded="false"]')) {
    fireEvent.click(toggle)
  }
  return view
}

const EDITABLE = SECTIONS.filter((def) => def.edits)

describe('sections with changes', () => {
  it('each name a permission that is labelled, and have a panel checked here', () => {
    expect(EDITABLE.length).toBeGreaterThan(5)
    for (const def of EDITABLE) {
      expect(BY_NAME_LABEL[def.edits!], def.key).toBeDefined()
      expect(PANELS[def.key], `no panel for ${def.key} in this test`).toBeDefined()
    }
  })
})

describe.each(EDITABLE)('$key', (def) => {
  it('disables every control that changes something for a person who may only view it', async () => {
    const { container } = await renderAs(['admin.view'], def)
    const all = controls(container)
    const edits = all.filter((el) => !el.hasAttribute('data-view'))
    expect(edits.length, 'nothing to change was rendered').toBeGreaterThan(0)
    for (const el of all) {
      expect(el.hasAttribute('data-view') !== el.hasAttribute('data-edit'), `${name(el)}: view or edit, not both`).toBe(
        true,
      )
    }
    for (const el of edits) {
      expect(el.disabled, `${name(el)} is enabled`).toBe(true)
      expect(el.title, name(el)).toBe(lockedReason(def.edits!))
    }
  })

  it('names the permission in words', async () => {
    const { container } = await renderAs(['admin.view'], def)
    const first = container.querySelector<HTMLElement>('[data-edit]')!
    expect(first.title).toContain(BY_NAME_LABEL[def.edits!])
  })

  it('leaves the controls to their own rules for a person who may change it', async () => {
    const { container } = await renderAs(['admin.view', def.edits!], def)
    const edits = controls(container).filter((el) => el.hasAttribute('data-edit'))
    expect(edits.some((el) => !el.disabled)).toBe(true)
    for (const el of edits) expect(el.title).not.toBe(lockedReason(def.edits!))
  })
})

describe('reading controls stay live', () => {
  it('pages, filters and searches the fetch policy without the permission to change it', async () => {
    const def = SECTIONS.find((s) => s.key === 'domains')!
    const { container } = await renderAs(['admin.view'], def)
    expect((screen.getByRole('searchbox', { name: 'Find a domain' }) as HTMLInputElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: /^blocked/ }) as HTMLButtonElement).disabled).toBe(false)
    expect(container.querySelectorAll('[data-edit]:disabled').length).toBeGreaterThan(0)
  })

  it('keeps a keyboard decision from going through on the gazetteer', async () => {
    const onDecide = vi.fn()
    render(
      <MeProvider load={async () => me(['admin.view'])}>
        <EditGate permission="gazetteer.curate">
          <GazetteerQueue
            rows={[term(1, false)]}
            state="pending"
            counts={{ pending: 1, approved: 0, rejected: 0 }}
            onDecide={onDecide}
          />
        </EditGate>
      </MeProvider>,
    )
    await act(async () => undefined)
    const row = document.querySelector<HTMLElement>('tr[data-term="1"]')!
    fireEvent.keyDown(row, { key: 'a' })
    expect(onDecide).not.toHaveBeenCalled()
  })
})

describe('outside a gate', () => {
  it('changes nothing: a panel rendered alone keeps its controls', () => {
    render(<PinsPanel rows={TOPICS} />)
    const pin = screen.getAllByRole('button', { name: 'Pin' })[0] as HTMLButtonElement
    expect(pin.disabled).toBe(false)
    expect(pin.title).toBe('')
  })
})

describe('AdminPage', () => {
  it('gates the open section on the permission its changes need', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL) =>
        String(input).includes('display')
          ? Promise.resolve(
              new Response(JSON.stringify({ display_timezone: 'UTC', label: 'GMT' }), {
                headers: { 'Content-Type': 'application/json' },
              }),
            )
          : new Promise<Response>(() => {}),
      ),
    )
    window.history.pushState({}, '', '/admin/display')
    render(
      <MeProvider load={async () => me(['admin.view'])}>
        <AdminPage />
      </MeProvider>,
    )
    const save = (await screen.findByRole('button', { name: 'Save' })) as HTMLButtonElement
    await waitFor(() => expect(save.title).toBe(lockedReason('settings.edit')))
    expect(save.disabled).toBe(true)
    expect(screen.getByText(/You can see this section but not change it/)).toBeTruthy()
    // The section picker is not a change, and stays live.
    expect((screen.getByRole('combobox', { name: 'Admin section' }) as HTMLSelectElement).disabled).toBe(false)
  })
})
