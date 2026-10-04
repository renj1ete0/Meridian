// @vitest-environment jsdom
/**
 * The neighbourhood panel beside Find's results (task P6-33).
 *
 * What this file protects is the task's one rule — **cited and similar are
 * different kinds of evidence and are never shown as one** — plus the honest
 * empties: no node named, no stated links, resemblance not measured.
 *
 * The drift half ties the client's field lists to the pydantic classes they
 * mirror, parsed out of `meridian_core/schemas/neighbourhood.py`, including the
 * fields a subclass inherits from `TermRead`.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  NeighbourhoodPanel,
  basisText,
  relationText,
  arcPositions,
  labelAt,
  labelLines,
  shortLabel,
} from '../src/explore/neighbourhood/NeighbourhoodPanel'
import {
  CITED_TERM_FIELDS,
  RELATION_FIELDS,
  SIMILAR_PASSAGE_FIELDS,
  SIMILAR_TERM_FIELDS,
  TERM_FIELDS,
  TERM_NEIGHBOURHOOD_FIELDS,
  getTermNeighbourhood,
  hasNeighbourhood,
  neighbourhoodQuery,
  type TermNeighbourhood,
} from '../src/explore/neighbourhood/api'
import { ApiError, type SearchHit } from '../src/lib/api'

// Not `import.meta.url`: under jsdom it is not a file URL. Vitest runs from web/.
const REPO = join(process.cwd(), '..')
const SCHEMA = join(REPO, 'packages/meridian_core/meridian_core/schemas/neighbourhood.py')

/** Fields declared on one pydantic class plus those of its local base class. */
function pydanticFields(className: string): string[] {
  const source = readFileSync(SCHEMA, 'utf8')
  const start = source.indexOf(`class ${className}(`)
  if (start === -1) throw new Error(`no class ${className}`)
  const base = /^class \w+\((\w+)\)/.exec(source.slice(start))![1]!
  const rest = source.slice(start)
  const end = rest.slice(1).search(/^class /m)
  const body = (end === -1 ? rest : rest.slice(0, end + 1)).replace(/"""[\s\S]*?"""/g, '')
  const own = [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map((m) => m[1]!)
  const inherited = source.includes(`class ${base}(`) ? pydanticFields(base) : []
  return [...inherited, ...own]
}

describe('the neighbourhood client matches its DTOs', () => {
  const pairs = [
    ['TermRead', TERM_FIELDS],
    ['RelationRead', RELATION_FIELDS],
    ['CitedTermRead', CITED_TERM_FIELDS],
    ['SimilarTermRead', SIMILAR_TERM_FIELDS],
    ['SimilarPassageRead', SIMILAR_PASSAGE_FIELDS],
    ['TermNeighbourhoodRead', TERM_NEIGHBOURHOOD_FIELDS],
  ] as const
  it.each(pairs)('%s', (className, fields) => {
    expect([...fields].sort()).toEqual(pydanticFields(className).sort())
  })
})

function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    chunk_id: 11,
    source_id: 7,
    passage_topics: null,
    text: 'Covered linkways near stations were associated with more walking trips.',
    page_or_offset: 3,
    chunk_index: 0,
    url: 'https://example.test/report',
    title: 'A report',
    page_unit: 'offset',
    media_type: 'text/html',
    source_tier: 'government',
    publication_date: '2022-03-01',
    language: 'en',
    topic_labels: [],
    duplicate_of: null,
    score: 0,
    lexical_rank: null,
    vector_rank: null,
    age_days: null,
    decay: 1,
    score_before_decay: 0,
    ...over,
  }
}

function data(over: Partial<TermNeighbourhood> = {}): TermNeighbourhood {
  return {
    term: 'walkway',
    anchor: { entity_id: 1, canonical_name: 'covered walkways', node_type: 'intervention' },
    candidates: [],
    cited: [
      {
        entity_id: 2,
        canonical_name: 'walking trips',
        node_type: 'finding',
        support: 3,
        relations: [{ relation_type: 'increases', outgoing: true }],
        contested: false,
      },
      {
        entity_id: 3,
        canonical_name: 'heat exposure',
        node_type: 'concept',
        support: 1,
        relations: [{ relation_type: 'reduces', outgoing: false }],
        contested: true,
      },
    ],
    cited_total: 2,
    similar: [
      { entity_id: 4, canonical_name: 'bus shelter design', node_type: 'concept', similarity: 0.81 },
      { entity_id: 5, canonical_name: 'street trees', node_type: 'concept', similarity: 0.72 },
    ],
    similar_total: 2,
    passages: [
      { hit: hit({ chunk_id: 11 }), similarity: 0.66 },
      { hit: hit({ chunk_id: 12, text: 'Already on screen.' }), similarity: 0.6 },
    ],
    similar_basis: 'node',
    similar_floor: 0.7,
    passage_floor: 0.55,
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('pure helpers', () => {
  it('spreads points over the arc, leaving the top free for the caption', () => {
    const points = arcPositions(4, 10, { x: 0, y: 0 }, 45, 315)
    expect(points.map((p) => p.angle)).toEqual([78.75, 146.25, 213.75, 281.25])
    arcPositions(7, 10, { x: 0, y: 0 }).forEach((p) => {
      expect(p.angle).toBeGreaterThan(40)
      expect(p.angle).toBeLessThan(320)
    })
    expect(arcPositions(0, 10, { x: 0, y: 0 })).toEqual([])
  })

  it('puts a label outward from the centre', () => {
    expect(labelAt({ x: 10, y: 0, angle: 90 }).anchor).toBe('start')
    expect(labelAt({ x: -10, y: 0, angle: 270 }).anchor).toBe('end')
    const bottom = labelAt({ x: 0, y: 10, angle: 180 })
    expect(bottom.anchor).toBe('middle')
    expect(bottom.y).toBeGreaterThan(10)
  })

  it('shortens long labels and leaves short ones alone', () => {
    expect(shortLabel('street trees')).toBe('street trees')
    const long = shortLabel('Permit to Deploy Autonomous Vehicles on Public Streets')
    expect(long.length).toBeLessThanOrEqual(18)
    expect(long.endsWith('…')).toBe(true)
  })

  it('writes a relation from the anchor’s side', () => {
    expect(relationText({ relation_type: 'piloted_in', outgoing: true })).toBe('piloted in →')
    expect(relationText({ relation_type: 'reduces', outgoing: false })).toBe('← reduces')
  })

  it('says when resemblance was not measured at all', () => {
    expect(basisText('none')).toMatch(/^Not measured/)
    expect(basisText('node')).not.toBe(basisText('term'))
  })

  it('builds the query for a term or a picked node', () => {
    expect(neighbourhoodQuery({ q: '  walkway ' })).toBe('q=walkway')
    expect(neighbourhoodQuery({ entityId: 9 })).toBe('entity_id=9')
  })
})

describe('the panel keeps cited and similar apart', () => {
  it('lists each ring separately and never a cited node among the similar', () => {
    render(<NeighbourhoodPanel state={{ phase: 'done', data: data() }} />)
    const cited = screen.getByRole('region', { name: 'Stated in a passage' })
    const similar = screen.getByRole('region', { name: 'Near in meaning' })
    expect(within(cited).getByText('walking trips')).toBeTruthy()
    expect(within(cited).queryByText('bus shelter design')).toBeNull()
    expect(within(similar).getByText('bus shelter design')).toBeTruthy()
    expect(within(similar).queryByText('walking trips')).toBeNull()
    // The similar heading says what it is not.
    expect(within(similar).getByText(/no stated link/)).toBeTruthy()
  })

  it('draws cited spokes solid and similar spokes dashed', () => {
    const { container } = render(<NeighbourhoodPanel state={{ phase: 'done', data: data() }} />)
    const citedLines = container.querySelectorAll('[data-ring="cited"] line')
    const similarLines = container.querySelectorAll('[data-ring="similar"] line')
    expect(citedLines).toHaveLength(2)
    expect(similarLines).toHaveLength(2)
    citedLines.forEach((line) => expect(line.getAttribute('stroke-dasharray')).toBeNull())
    similarLines.forEach((line) => expect(line.getAttribute('stroke-dasharray')).toBeTruthy())
  })

  it('marks a contested link with the dagger and a badge', () => {
    render(<NeighbourhoodPanel state={{ phase: 'done', data: data() }} />)
    expect(screen.getByText(/Contested/)).toBeTruthy()
    expect(screen.getByText('← reduces')).toBeTruthy()
    expect(screen.getByText('increases →')).toBeTruthy()
  })

  it('does not repeat a passage the result list already shows', () => {
    render(<NeighbourhoodPanel state={{ phase: 'done', data: data() }} shownChunkIds={new Set([12])} />)
    expect(screen.queryByText('Already on screen.')).toBeNull()
    expect(screen.getByText(/Covered linkways/)).toBeTruthy()
  })

  it('says how many were left out when a ring is capped', () => {
    render(<NeighbourhoodPanel state={{ phase: 'done', data: data({ cited_total: 14 }) }} />)
    expect(screen.getByText(/2 of 14 shown/)).toBeTruthy()
  })
})

describe('the panel is honest when there is little to show', () => {
  it('says a node has no stated links and still shows what reads alike', () => {
    render(<NeighbourhoodPanel state={{ phase: 'done', data: data({ cited: [], cited_total: 0 }) }} />)
    expect(screen.getByText(/No passage states a link to this yet/)).toBeTruthy()
    const similar = screen.getByRole('region', { name: 'Near in meaning' })
    expect(within(similar).getByText('bus shelter design')).toBeTruthy()
  })

  it('says when the term names no node, and offers the near names to pick', () => {
    const onPick = vi.fn()
    render(
      <NeighbourhoodPanel
        state={{
          phase: 'done',
          data: data({
            anchor: null,
            cited: [],
            cited_total: 0,
            candidates: [{ entity_id: 8, canonical_name: 'covered walkways', node_type: 'intervention' }],
            similar_basis: 'term',
          }),
        }}
        onPick={onPick}
      />,
    )
    expect(screen.getByText(/No node is named “walkway”/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'covered walkways' }))
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ entity_id: 8 }))
  })

  it('does not present an unmeasured outer ring as "nothing similar"', () => {
    render(
      <NeighbourhoodPanel
        state={{
          phase: 'done',
          data: data({ similar: [], similar_total: 0, passages: [], similar_basis: 'none' }),
        }}
      />,
    )
    expect(screen.getByText(/^Not measured/)).toBeTruthy()
    expect(screen.queryByText(/No node reads alike/)).toBeNull()
  })

  it('draws no diagram when both rings are empty, and does not point at an empty list', () => {
    const { container } = render(
      <NeighbourhoodPanel
        state={{ phase: 'done', data: data({ cited: [], cited_total: 0, similar: [], similar_total: 0 }) }}
      />,
    )
    expect(container.querySelector('[data-role="rings"]')).toBeNull()
    expect(screen.getByText('No passage states a link to this yet.')).toBeTruthy()
    expect(screen.getByText(/No node reads alike above 0.70/)).toBeTruthy()
  })

  it('names a failure inside the panel', () => {
    render(<NeighbourhoodPanel state={{ phase: 'failed', message: 'The API returned 500.' }} />)
    expect(screen.getByText('The API returned 500.')).toBeTruthy()
  })
})

describe('whether there is a neighbourhood to show', () => {
  const empty = data({
    anchor: null,
    candidates: [],
    cited: [],
    cited_total: 0,
    similar: [],
    similar_total: 0,
    passages: [],
  })

  it('is nothing when no ring, name or passage holds anything', () => {
    expect(hasNeighbourhood(empty)).toBe(false)
  })

  it.each([
    ['an anchor', { anchor: { entity_id: 1, canonical_name: 'x', node_type: 'concept' } }],
    ['a near name to pick', { candidates: [{ entity_id: 2, canonical_name: 'y', node_type: 'concept' }] }],
    ['a cited term', { cited: data().cited }],
    ['a similar term', { similar: data().similar }],
    ['a passage', { passages: data().passages }],
  ] as const)('is something when it has %s', (_, over) => {
    expect(hasNeighbourhood({ ...empty, ...(over as Partial<TermNeighbourhood>) })).toBe(true)
  })
})

describe('the client', () => {
  it('turns a refusal into an ApiError with the server’s words', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: 'No entity 5.' }), { status: 404 }),
    )
    await expect(getTermNeighbourhood({ entityId: 5 })).rejects.toEqual(new ApiError(404, 'No entity 5.'))
  })

  it('asks the read-only route', async () => {
    const fetched = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify(data()), { status: 200 }))
    await getTermNeighbourhood({ q: 'walkway' })
    expect(String(fetched.mock.calls[0]![0])).toBe('/api/explore/neighbourhood?q=walkway')
  })
})

describe('names in the ring diagram (B-100)', () => {
  it('keeps a short name whole on one line', () => {
    expect(labelLines('street trees')).toEqual(['street trees'])
  })

  it('wraps a long name onto two lines at a word, each within the width', () => {
    const lines = labelLines('pavement lifespan extension', 16)
    expect(lines).toHaveLength(2)
    expect(lines.every((line) => line.length <= 16)).toBe(true)
    expect(lines.join(' ').replace('…', '')).toContain('pavement lifespan')
  })

  it('shortens only what does not fit in two lines, and never loses the start', () => {
    const lines = labelLines('Permit to Deploy Autonomous Vehicles on Public Streets', 16)
    expect(lines).toHaveLength(2)
    expect(lines[0]).toBe('Permit to Deploy')
    expect(lines[1]!.endsWith('…')).toBe(true)
    expect(lines.every((line) => line.length <= 16)).toBe(true)
  })

  it('cuts a single word too long for a line rather than dropping it', () => {
    const lines = labelLines('Supercalifragilisticexpialidocious', 16)
    expect(lines).toHaveLength(1)
    expect(lines[0]!.length).toBeLessThanOrEqual(16)
  })
})
