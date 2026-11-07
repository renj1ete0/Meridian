// @vitest-environment jsdom
/**
 * The graph audit (`P7-10`, spec §14.1): precision per draw with its interval, a card per
 * sampled relation or merge with the passages to judge it by, and three grades. A grade changes
 * nothing in the graph, so the panel offers no other action.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GraphAuditPanel, gradedText, precisionText } from '../src/admin/GraphAuditPanel'
import { SECTIONS } from '../src/admin/sections'
import { ApiError } from '../src/lib/api'
import type { AuditDraw, AuditDrawDetail, AuditSample, AuditTally, GraphAudit } from '../src/lib/graphAudit'
import { BY_NAME_LABEL } from '../src/lib/permissionLabels'

afterEach(cleanup)

const tally = (over: Partial<AuditTally> = {}): AuditTally => ({
  drawn: 0,
  graded: 0,
  correct: 0,
  wrong: 0,
  unsure: 0,
  precision: null,
  low: null,
  high: null,
  ...over,
})

const DRAW: AuditDraw = {
  draw_id: 4,
  drawn_at: '2026-10-01T00:00:00Z',
  window_start: '2026-09-01T00:00:00Z',
  window_end: '2026-10-01T00:00:00Z',
  seed: 'abc',
  drawn_by: 'scheduler',
  edge_population: 412,
  merge_population: 3,
  match_population: 57,
  merges_reversed: 1,
  edges: tally({ drawn: 2, graded: 1, correct: 1, precision: 1, low: 0.21, high: 1 }),
  merges: tally({ drawn: 1 }),
  matches: tally({ drawn: 1 }),
  by_producer: [
    {
      kind: 'edge',
      produced_by: 'agent-small',
      model: 'model-a',
      tally: tally({ drawn: 2, graded: 1, correct: 1, precision: 1, low: 0.21, high: 1 }),
    },
  ],
}

const passage = (id: number, text: string) => ({ chunk_id: id, source_id: id * 10, title: 'A report', text })

const EDGE: AuditSample = {
  sample_id: 11,
  draw_id: 4,
  kind: 'edge',
  item_id: 900,
  position: 0,
  shown: {
    from: { entity_id: 1, name: 'An agency', node_type: 'organisation' },
    relation: 'funds',
    to: { entity_id: 2, name: 'A programme', node_type: 'intervention' },
    stance: null,
    certainty: 'asserted',
    similarity_dimension: null,
    disanalogy: null,
    valid_from: null,
    valid_to: null,
    chunk_ids: [5, 6],
    passages: [passage(5, 'The agency funds the programme')],
  },
  grade: null,
  note: null,
  graded_by: null,
  graded_at: null,
}

const GRADED: AuditSample = {
  ...EDGE,
  sample_id: 12,
  item_id: 901,
  position: 1,
  grade: 'correct',
  graded_by: 'user',
  graded_at: '2026-10-02T00:00:00Z',
}

const MERGE: AuditSample = {
  sample_id: 13,
  draw_id: 4,
  kind: 'merge',
  item_id: 70,
  position: 2,
  shown: {
    source: { entity_id: 3, name: 'The Authority', node_type: 'organisation', description: null, passages: [] },
    target: {
      entity_id: 4,
      name: 'A transport authority',
      node_type: 'organisation',
      description: 'Runs the network.',
      passages: [passage(7, 'The authority runs the network')],
      aliases: ['TA'],
    },
    score: 0.91,
    signals: { string: 0.9 },
    decided_by: 'user',
    merged_at: '2026-09-12T00:00:00Z',
    moved_edges: 3,
  },
  grade: null,
  note: null,
  graded_by: null,
  graded_at: null,
}

const MATCH: AuditSample = {
  sample_id: 14,
  draw_id: 4,
  kind: 'match',
  item_id: 501,
  position: 3,
  shown: {
    mention: {
      name: 'Transport Authority Pte Ltd',
      node_type: 'organisation',
      jurisdiction: 'SG',
      passages: [passage(8, 'Transport Authority Pte Ltd tendered the line')],
    },
    node: {
      entity_id: 9,
      name: 'Transport Authority',
      node_type: 'organisation',
      jurisdiction: null,
      description: null,
      aliases: [],
      passages: [passage(6, 'The Transport Authority runs the buses')],
    },
    score: 0.93,
    signals: { string: 0.88, embedding: 0.97 },
    matched_at: '2026-09-20T00:00:00Z',
    run_id: 31,
  },
  grade: null,
  note: null,
  graded_by: null,
  graded_at: null,
}

const AUDIT: GraphAudit = { draws: [DRAW], ungraded: 3 }
const DETAIL: AuditDrawDetail = { draw: DRAW, samples: [EDGE, GRADED, MERGE, MATCH] }

async function open(props: Partial<Parameters<typeof GraphAuditPanel>[0]> = {}) {
  const load = props.load ?? vi.fn(async () => AUDIT)
  const loadDraw = props.loadDraw ?? vi.fn(async () => DETAIL)
  await act(async () => {
    render(<GraphAuditPanel load={load} loadDraw={loadDraw} grade={props.grade ?? vi.fn()} />)
  })
  return { load, loadDraw }
}

describe('the measure', () => {
  it('prints precision with its interval, and a dash before anything is judged', () => {
    expect(precisionText(tally({ precision: 0.75, low: 0.3, high: 0.954 }))).toBe('75% (30%–95%)')
    expect(precisionText(tally({ unsure: 2, graded: 2, drawn: 2 }))).toBe('—')
  })

  it('says how much is graded and what it was drawn from', () => {
    expect(gradedText(tally({ drawn: 30, graded: 12, unsure: 2 }), 1200)).toBe(
      '12 of 30 graded · 2 unsure · from 1,200',
    )
    expect(gradedText(tally(), 0)).toBe('none made')
  })
})

describe('the panel', () => {
  it('shows each draw with its precision, and opens the newest', async () => {
    const { loadDraw } = await open()
    const row = document.querySelector('[data-draw="4"]') as HTMLElement
    expect(within(row).getByText('100% (21%–100%)')).toBeTruthy()
    expect(within(row).getByText('1 of 2 graded · from 412')).toBeTruthy()
    expect(within(row).getByText(/1 reversed already/)).toBeTruthy()
    expect(loadDraw).toHaveBeenCalledWith(4)
    expect(screen.getByText(/3 waiting to be graded/)).toBeTruthy()
    // Matches at resolution have their own column, with what they were drawn from.
    expect(within(row).getByText('0 of 1 graded · from 57')).toBeTruthy()
    // By who wrote it: which model's relations are wrong.
    expect(screen.getByText('model-a')).toBeTruthy()
  })

  it('lists what is left to grade, with the claim, its passages and the merge sides', async () => {
    await open()
    expect(screen.getByRole('button', { name: 'To grade (3)' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('link', { name: 'An agency' }).getAttribute('href')).toBe('/nodes/1')
    expect(screen.getByText('funds')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: /The agency funds the programme/ })[0]!.getAttribute('href')).toBe(
      '/sources/50?passage=5',
    )
    expect(screen.getByText(/2 passages cited/)).toBeTruthy()
    expect(screen.getByText('Merged away')).toBeTruthy()
    expect(screen.getByText(/score 0.91, decided by user; 3 links moved/)).toBeTruthy()
    // The graded one is under All, not under To grade.
    expect(screen.queryAllByRole('group', { name: /Grade item/ })).toHaveLength(3)
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'All (4)' }))
    })
    expect(screen.queryAllByRole('group', { name: /Grade item/ })).toHaveLength(4)
    expect(screen.getByRole('status').textContent).toContain('Graded correct')
  })

  it('shows a match at resolution with the name read beside the node as it was (B-273)', async () => {
    await open()
    expect(screen.getByText('4. Match')).toBeTruthy()
    expect(screen.getByText(/joined it to an existing node .*score 0.93/)).toBeTruthy()
    expect(screen.getByText('Name read')).toBeTruthy()
    expect(screen.getByText('Transport Authority Pte Ltd')).toBeTruthy()
    expect(screen.getByText('organisation · SG')).toBeTruthy()
    expect(screen.getByText('Joined to')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Transport Authority' }).getAttribute('href')).toBe('/nodes/9')
    // Each side's own passage, so the grader can see whether they are about one thing.
    expect(screen.getByRole('link', { name: /tendered the line/ }).getAttribute('href')).toBe('/sources/80?passage=8')
    expect(screen.getByRole('link', { name: /runs the buses/ }).getAttribute('href')).toBe('/sources/60?passage=6')
    expect(within(screen.getByRole('group', { name: 'Grade item 4' })).getAllByRole('button')).toHaveLength(3)
  })

  it('grades, keeps the graded card in view to correct a slip, and refreshes the measure', async () => {
    const grade = vi.fn(async (_id: number, verdict: 'correct' | 'wrong' | 'unsure') => ({
      ...EDGE,
      grade: verdict,
      graded_by: 'user',
      graded_at: '2026-10-03T00:00:00Z',
    }))
    const { load } = await open({ grade })
    const card = screen.getByRole('group', { name: 'Grade item 1' })
    await act(async () => {
      fireEvent.click(within(card).getByRole('button', { name: 'Wrong' }))
    })
    expect(grade).toHaveBeenCalledWith(11, 'wrong')
    const after = screen.getByRole('group', { name: 'Grade item 1' })
    expect(within(after).getByRole('button', { name: 'Wrong' }).getAttribute('aria-pressed')).toBe('true')
    expect(within(after).getByRole('status').textContent).toContain('Graded wrong')
    expect(load).toHaveBeenCalledTimes(2)
  })

  it('shows a refusal in the server’s words and leaves the grade unset', async () => {
    const grade = vi.fn(async () => {
      throw new ApiError(409, 'No sampled item 11.')
    })
    await open({ grade })
    const card = screen.getByRole('group', { name: 'Grade item 1' })
    await act(async () => {
      fireEvent.click(within(card).getByRole('button', { name: 'Correct' }))
    })
    expect(within(card).getByRole('alert').textContent).toContain('No sampled item 11.')
    expect(within(card).getByRole('button', { name: 'Correct' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('offers only grades: nothing on a card deletes, reverses or merges', async () => {
    await open()
    for (const card of screen.getAllByRole('group', { name: /Grade item/ })) {
      const labels = within(card)
        .getAllByRole('button')
        .map((b) => b.textContent)
      expect(labels).toEqual(['Correct', 'Wrong', 'Unsure'])
    }
  })

  it('says when nothing has been drawn yet', async () => {
    await open({ load: vi.fn(async () => ({ draws: [], ungraded: 0 })) })
    expect(screen.getByText(/Nothing drawn yet/)).toBeTruthy()
  })

  it('says when it could not load', async () => {
    await open({ load: vi.fn(async () => Promise.reject(new Error('down'))) })
    expect(screen.getByRole('alert').textContent).toContain('could not be loaded')
  })
})

describe('its place in Admin', () => {
  it('has a section whose changes need the grading permission, which has a label', () => {
    const def = SECTIONS.find((s) => s.key === 'audit')
    expect(def).toMatchObject({ path: 'graph-audit', group: 'system', edits: 'graph.audit' })
    expect(BY_NAME_LABEL['graph.audit']).toBe('Graph audit')
  })
})
