/**
 * Gaps (task P6-36): the ranked list, its honest empty and failure states, and
 * actions that go to the admin API with exactly the gap they came from.
 */
// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { App, sectionOf } from '../src/App'
import { FLAG_AT, GapsPage, SOURCE_NAMES } from '../src/explore/GapsPage'
import {
  GAP_ACTION_FIELDS,
  GAP_FIELDS,
  GAP_RESULT_FIELDS,
  GAP_SOURCE_FIELDS,
  GAPS_FIELDS,
  evidenceLine,
  findHref,
  groupOf,
  type Gap,
  type Gaps,
} from '../src/lib/gaps'
import { parseRoute } from '../src/lib/route'
import { NAV } from '../src/ui/TopBar'

// Not `import.meta.url`: under jsdom it is not a file: URL. Vitest runs from web/.
const REPO = join(process.cwd(), '..')
const SCHEMA = join(REPO, 'packages/meridian_core/meridian_core/schemas/gaps.py')

function pydanticFields(className: string): string[] {
  const source = readFileSync(SCHEMA, 'utf8')
  const start = source.indexOf(`class ${className}(`)
  if (start === -1) throw new Error(`no class ${className}`)
  const rest = source.slice(start)
  const end = rest.slice(1).search(/^class /m)
  const body = (end === -1 ? rest : rest.slice(0, end + 1)).replace(/"""[\s\S]*?"""/g, '')
  return [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map((m) => m[1]!)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

function gap(over: Partial<Gap> = {}): Gap {
  return {
    id: 'topic-thin:robotics',
    source: 'topic-coverage',
    kind: 'thin',
    subject: 'robotics',
    title: 'robotics: 0 sources',
    reason: '0 sources labelled robotics by content, 0 passages; fewer than 10 is thin.',
    severity: 1,
    evidence: { sources: 0, strong_sources: 0, passages: 0, newest: null, crawl_share: 0.15, corpus_share: 0 },
    actions: [
      { kind: 'seed_query', label: 'Seed a search', topic: 'robotics', query: 'robotics', factor: null, days: null },
      { kind: 'boost_topic', label: 'Boost ×2 for 7 days', topic: 'robotics', factor: 2, days: 7 , query: null },
    ],
    ...over,
  }
}

function body(gaps: Gap[], over: Partial<Gaps> = {}): Gaps {
  return {
    gaps,
    sources: [
      { name: 'topic-coverage', status: 'ok', note: null, gaps: gaps.length },
      { name: 'question-set', status: 'unavailable', note: 'no run file', gaps: 0 },
      { name: 'areas', status: 'pending', note: 'arrive with P6-30', gaps: 0 },
    ],
    computed_at: '2026-09-24T12:00:00Z',
    ...over,
  }
}

function respond(value: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } }))
}

async function renderWith(fetchImpl: (url: string, init?: RequestInit) => Promise<Response>) {
  const fetchMock = vi.fn(fetchImpl)
  vi.stubGlobal('fetch', fetchMock)
  await act(async () => {
    render(<GapsPage />)
  })
  return fetchMock
}

// --------------------------------------------------------------------------

describe('the client types match the DTOs', () => {
  it.each([
    ['GapRead', GAP_FIELDS],
    ['GapActionRead', GAP_ACTION_FIELDS],
    ['GapSourceRead', GAP_SOURCE_FIELDS],
    ['GapsRead', GAPS_FIELDS],
    ['GapActionResult', GAP_RESULT_FIELDS],
  ])('%s', (name, fields) => {
    const parsed = pydanticFields(name)
    expect(parsed.length).toBeGreaterThan(2)
    expect([...fields].sort()).toEqual([...parsed].sort())
  })
})

describe('routing', () => {
  it('has a Gaps section between Map and Admin', () => {
    expect(NAV.map((n) => n.label)).toEqual(['Explore', 'Map', 'Gaps', 'Admin'])
    expect(sectionOf(parseRoute('/gaps'))).toBe('gaps')
    expect(parseRoute('/gaps/').name).toBe('gaps')
    expect(parseRoute('/gapsx').name).toBe('explore')
  })
})

describe('helpers', () => {
  it('prints evidence with units and em dashes for unknowns', () => {
    const line = evidenceLine(gap().evidence)
    expect(line).toContain('sources 0')
    expect(line).toContain('newest —')
    expect(line).toContain('crawl share 15%')
    expect(line).toContain('corpus share 0%')
  })

  it('names documents holding on-topic passages only when there are some', () => {
    // `P2-24`: passages about a topic inside documents filed under another.
    expect(evidenceLine({ ...gap().evidence, passage_sources: 4 })).toContain(
      'in other documents 4',
    )
    expect(evidenceLine({ ...gap().evidence, passage_sources: 0 }).join(' ')).not.toContain(
      'other documents',
    )
  })

  it('prints every evidence key the server emits', () => {
    // Drift across the language boundary: a key added to a gap's evidence in
    // gaps.py and never printed here is a number the operator cannot see.
    const server = readFileSync(join(REPO, 'packages/meridian_core/meridian_core/gaps.py'), 'utf8')
    const client = readFileSync(join(REPO, 'web/src/lib/gaps.ts'), 'utf8')
    const keys = new Set<string>()
    for (const block of server.matchAll(/evidence\s*=\s*\{([^}]*)\}/g)) {
      for (const key of block[1]!.matchAll(/"([a-z_]+)"\s*:/g)) keys.add(key[1]!)
    }
    expect(keys.size).toBeGreaterThan(5)
    // A question's kind is already the first word of its title, and a failed
    // search group's queries are quoted in its reason (P6-37), and a route
    // gap's two ends are named in its title (P6-36 routes).
    const shownElsewhere = new Set(['kind', 'queries', 'from_node', 'to_node'])
    const missing = [...keys].filter(
      (k) =>
        !shownElsewhere.has(k) &&
        !client.includes(`has('${k}')`) &&
        !client.includes(`evidence.${k}`),
    )
    expect(missing).toEqual([])
  })

  it('prints a place gap with its place first and the topic total (P2-23)', () => {
    const line = evidenceLine({ place: 'JP', sources: 1, strong_sources: 0, topic_sources: 40 })
    expect(line[0]).toBe('place JP')
    expect(line).toContain('sources 1')
    expect(line).toContain('in the topic 40')
  })

  it('links a question to Find with the search already asked', () => {
    expect(findHref('a b&c')).toBe('/?q=a%20b%26c')
  })

  it('groups by source', () => {
    expect(groupOf(gap())).toBe('coverage')
    expect(groupOf(gap({ source: 'question-set' }))).toBe('questions')
    // Place coverage is coverage along another axis (P2-23), not "other".
    expect(groupOf(gap({ source: 'place-coverage' }))).toBe('coverage')
    expect(groupOf(gap({ source: 'search-queries' }))).toBe('search')
    expect(groupOf(gap({ source: 'search-results' }))).toBe('search')
    expect(groupOf(gap({ source: 'something-new' }))).toBe('other')
  })

  it('names and groups every source the server registers or lists as pending', () => {
    // Drift (P6-37): a source added in gaps.py and missed here shows under its
    // raw name and in no filter but "all". Read from the Python, not a list.
    const source = readFileSync(join(REPO, 'packages/meridian_core/meridian_core/gaps.py'), 'utf8')
    const registered = [...source.matchAll(/^@register\("([a-z-]+)"\)/gm)].map((m) => m[1]!)
    const pendingBlock = source.slice(source.indexOf('PENDING: dict[str, str] = {'))
    const pending = [...pendingBlock.slice(0, pendingBlock.indexOf('}')).matchAll(/^ {4}"([a-z-]+)":/gm)].map(
      (m) => m[1]!,
    )
    expect(registered.length).toBeGreaterThan(3)
    for (const name of [...registered, ...pending]) {
      expect(SOURCE_NAMES[name], name).toBeTruthy()
      expect(groupOf(gap({ source: name })), name).not.toBe('other')
    }
  })

  it('prints a failed search group with its counts and task ids', () => {
    const line = evidenceLine({ failed: 3, searches_done: 8, results: 0, queries: 'a; b', task_ids: '9, 7, 4' })
    expect(line).toEqual(['failed 3', 'searches 8', 'results 0', 'tasks 9, 7, 4'])
    const off = evidenceLine({ examined: 10, on_topic: 2, on_topic_share: 0.2 })
    expect(off).toEqual(['read 10', 'on topic 2', 'on-topic share 20%'])
  })

  it('prints a route gap: hops, how many by resemblance, and the depth searched', () => {
    const similar = evidenceLine({ from_node: 1, to_node: 2, max_depth: 4, hops: 3, similar_hops: 2 })
    expect(similar).toEqual(['hops 3', 'by resemblance 2', 'within 4'])
    const none = evidenceLine({ from_node: 1, to_node: 2, max_depth: 4, hops: null, similar_hops: null })
    expect(none).toEqual(['hops none', 'within 4'])
    expect(groupOf(gap({ source: 'routes' }))).toBe('coverage')
  })
})

describe('the page', () => {
  it('says the list is unavailable rather than showing an empty one', async () => {
    await renderWith(() => respond({ detail: 'database down' }, 503))
    expect(screen.getByRole('alert').textContent).toContain('database down')
    expect(screen.queryByText(/No gaps/)).toBeNull()
  })

  it('an empty list names what was not checked', async () => {
    await renderWith(() => respond(body([])))
    expect(screen.getByText(/No gaps from the sources that ran/).textContent).toMatch(/unavailable or pending/)
    const sources = screen.getByRole('region', { name: 'Gap sources' })
    expect(sources.textContent).toContain('unavailable — no run file')
    expect(sources.textContent).toContain('pending')
  })

  it('ranks as served and flags the severe ones with the dagger', async () => {
    const mild = gap({ id: 'topic-stale:x', subject: 'x', kind: 'stale', title: 'x: newest 2012', severity: FLAG_AT - 0.1 })
    await renderWith(() => respond(body([gap(), mild])))
    const rows = within(screen.getByRole('list', { name: /most severe first/ })).getAllByRole('listitem')
    expect(rows[0]!.textContent).toContain('01')
    expect(rows[0]!.textContent).toContain('†')
    expect(rows[1]!.textContent).not.toContain('†')
  })

  it('seeds with the words the reader edited, for the gap it came from', async () => {
    const fetchMock = await renderWith((url) =>
      url.startsWith('/api/admin/gaps/seed')
        ? respond({ kind: 'seed_query', topic: 'robotics', detail: 'queued as a search seed · task 7', undo: 'remove it in Admin', task_id: 7, expires_at: null }, 201)
        : respond(body([gap()])),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Seed a search' }))
    const input = screen.getByLabelText(/Words to search for/)
    expect((input as HTMLInputElement).value).toBe('robotics')
    fireEvent.change(input, { target: { value: 'delivery robot pavement rules' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Queue this search' }))
    })
    const [url, init] = fetchMock.mock.calls.at(-1)!
    expect(url).toBe('/api/admin/gaps/seed')
    expect(JSON.parse(String(init!.body))).toEqual({
      gap_id: 'topic-thin:robotics',
      topic: 'robotics',
      query: 'delivery robot pavement rules',
    })
    expect(screen.getByRole('status').textContent).toContain('task 7')
  })

  it('shows the server refusal in its own words', async () => {
    await renderWith((url) =>
      url.startsWith('/api/admin/gaps/boost') ? respond({ detail: 'admin is closed' }, 503) : respond(body([gap()])),
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Boost/ }))
    })
    expect(screen.getByRole('alert').textContent).toContain('Refused: admin is closed')
  })

  it('offers a question-set gap only a Find link, never a seed or a boost', async () => {
    const q = gap({
      id: 'question:Q07',
      source: 'question-set',
      kind: 'question_low',
      subject: 'Q07',
      actions: [{ kind: 'open_search', label: 'Search it in Find', query: 'the question', topic: null, factor: null, days: null }],
    })
    await renderWith(() => respond(body([q])))
    expect(screen.queryByRole('button', { name: /Seed|Boost/ })).toBeNull()
    expect(screen.getByRole('link', { name: 'Search it in Find' }).getAttribute('href')).toBe('/?q=the%20question')
  })

  it('filters by kind with counts', async () => {
    const q = gap({ id: 'question:Q1', source: 'question-set', subject: 'Q1', title: 'Q1 low', actions: [] })
    await renderWith(() => respond(body([gap(), q])))
    fireEvent.click(screen.getByRole('button', { name: 'Question set (1)' }))
    const rows = within(screen.getByRole('list', { name: /most severe first/ })).getAllByRole('listitem')
    expect(rows).toHaveLength(1)
    // The rank stays the row's place in the whole list, not in the filtered one.
    expect(rows[0]!.textContent).toContain('02')
  })
})

describe('Find opens a linked search', () => {
  it('runs /?q= on arrival', async () => {
    const fetchMock = vi.fn((_url: string) => new Promise<Response>(() => {}))
    vi.stubGlobal('fetch', fetchMock)
    window.history.pushState({}, '', '/?q=shade%20study')
    await act(async () => {
      render(<App />)
    })
    const searched = fetchMock.mock.calls.map(([url]) => String(url)).filter((u) => u.startsWith('/api/explore/search'))
    expect(searched.some((u) => u.includes('q=shade+study') || u.includes('q=shade%20study'))).toBe(true)
  })
})
