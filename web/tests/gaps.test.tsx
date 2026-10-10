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
import { FLAG_AT, GapsPage, SOURCE_NAMES, UNDO_HREF, splitBangs } from '../src/explore/GapsPage'
import { SECTIONS } from '../src/admin/sections'
import {
  GAP_ACTION_FIELDS,
  GAP_FIELDS,
  GAP_RESULT_FIELDS,
  GAP_SOURCE_FIELDS,
  GAPS_FIELDS,
  evidenceLine,
  findHref,
  groupOf,
  mapHrefOf,
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
      { kind: 'boost_topic', label: 'Boost ×2 for 7 days', topic: 'robotics', factor: 2, days: 7, query: null },
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
  return Promise.resolve(
    new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } }),
  )
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
  it('has a Gaps section after Map, then Growth (ADR 0010), then Admin', () => {
    expect(NAV.map((n) => n.label)).toEqual(['Explore', 'Map', 'Gaps', 'Growth', 'Admin'])
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
    expect(evidenceLine({ ...gap().evidence, passage_sources: 4 })).toContain('in other documents 4')
    expect(evidenceLine({ ...gap().evidence, passage_sources: 0 }).join(' ')).not.toContain('other documents')
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
      (k) => !shownElsewhere.has(k) && !client.includes(`has('${k}')`) && !client.includes(`evidence.${k}`),
    )
    expect(missing).toEqual([])
  })

  it('links a field gap to the level of the Map that draws it (P6-42)', () => {
    expect(mapHrefOf({ area_id: 12, parent_id: 4 })).toBe('/map?area=4')
    // A top-level field has no parent: the Map's first level draws it.
    expect(mapHrefOf({ area_id: 12, parent_id: null })).toBe('/map')
    // Any other gap has no place on the Map.
    expect(mapHrefOf({ place: 'JP', sources: 1 })).toBeNull()
    expect(mapHrefOf({ area_id: '12' })).toBeNull()
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

  it('writes a kind with several words as words', async () => {
    // `replace('_', ' ')` changed only the first, so the label read "search off_topic".
    await renderWith(() => respond(body([gap({ id: 'search-off-topic:x', subject: 'x', kind: 'search_off_topic' })])))
    const row = within(screen.getByRole('list', { name: /most severe first/ })).getAllByRole('listitem')[0]!
    expect(row.textContent).toContain('x · search off topic')
    expect(row.textContent).not.toContain('_')
  })

  it('ranks as served and flags the severe ones with the dagger', async () => {
    const mild = gap({
      id: 'topic-stale:x',
      subject: 'x',
      kind: 'stale',
      title: 'x: newest 2012',
      severity: FLAG_AT - 0.1,
    })
    await renderWith(() => respond(body([gap(), mild])))
    const rows = within(screen.getByRole('list', { name: /most severe first/ })).getAllByRole('listitem')
    expect(rows[0]!.textContent).toContain('01')
    expect(rows[0]!.textContent).toContain('†')
    expect(rows[1]!.textContent).not.toContain('†')
  })

  it('seeds with the words the reader edited, for the gap it came from', async () => {
    const fetchMock = await renderWith((url) =>
      url.startsWith('/api/admin/gaps/seed')
        ? respond(
            {
              kind: 'seed_query',
              topic: 'robotics',
              detail: 'queued as a search seed · task 7',
              undo: 'remove it in Admin',
              task_id: 7,
              expires_at: null,
            },
            201,
          )
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
      actions: [
        {
          kind: 'open_search',
          label: 'Search it in Find',
          query: 'the question',
          topic: null,
          factor: null,
          days: null,
        },
      ],
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
    // Numbered within the list shown (`B-184`): "All" is now two lists, and a number taken
    // from the whole would read 01, 02, 05 in either of them.
    expect(rows[0]!.textContent).toContain('01')
    expect(window.location.search).toBe('?kind=questions')
  })
})

describe('what a reader can ask comes first (B-184)', () => {
  afterEach(() => window.history.pushState({}, '', '/'))

  const search = gap({
    id: 'search-off-topic:robotics',
    source: 'search-results',
    kind: 'search_off_topic',
    title: '3 of 90 search results are about robotics',
    severity: 0.59,
    actions: [
      {
        kind: 'seed_query',
        label: 'Seed a search',
        topic: 'robotics',
        query: '!news !science gripper torque',
        factor: null,
        days: null,
      },
    ],
  })
  const question = gap({
    id: 'question:Q1',
    source: 'question-set',
    subject: 'Q1',
    title: 'Q1 low',
    severity: 0.4,
    actions: [],
  })

  it('lists questions and coverage, and folds the search yield below them', async () => {
    await renderWith(() => respond(body([search, question])))
    const reader = within(screen.getByRole('list', { name: 'Gaps, most severe first' })).getAllByRole('listitem')
    expect(reader.map((r) => r.textContent)).toEqual([expect.stringContaining('Q1 low')])
    expect(screen.queryByText('3 of 90 search results are about robotics')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Search yield · 1/ }))
    expect(screen.getByText('3 of 90 search results are about robotics')).toBeTruthy()
  })

  it('opens on the tab a link names, so Back returns to it', async () => {
    window.history.pushState({}, '', '/gaps?kind=search')
    await renderWith(() => respond(body([search, question])))
    expect(screen.getByText('3 of 90 search results are about robotics')).toBeTruthy()
    expect(screen.queryByText('Q1 low')).toBeNull()
  })

  it('points an off-topic search at the description that would steer it, and keeps engine syntax out of the words', async () => {
    window.history.pushState({}, '', '/gaps?kind=search')
    const fetchMock = await renderWith(() => respond(body([search])))
    expect(screen.getByRole('link', { name: 'Edit the topic’s description' }).getAttribute('href')).toBe(
      '/admin/topics',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Seed a search' }))
    const input = screen.getByRole('textbox') as HTMLInputElement
    expect(input.value).toBe('gripper torque')
    expect(screen.getByText('as a news, science search')).toBeTruthy()
    void fetchMock
  })

  it('splits engine syntax from words, and puts it back on what is queued', () => {
    expect(splitBangs('!news !science gripper torque')).toEqual({ bangs: '!news !science', words: 'gripper torque' })
    expect(splitBangs('gripper !news')).toEqual({ bangs: '', words: 'gripper !news' })
  })
})

describe('an action says where it can be undone, and that place exists (B-184)', () => {
  it('names only Admin sections that exist', () => {
    // Read from the route: the boost's text named "Admin › Topics", where boosts are not.
    const route = readFileSync(join(__dirname, '..', '..', 'services', 'api', 'api', 'routes', 'gaps.py'), 'utf8')
    const named = [...route.matchAll(/Admin › ([^"]+?)(?: while|"|,)/g)].map((m) => m[1]!.trim())
    expect(named.length).toBeGreaterThan(1)
    const labels = SECTIONS.map((s) => s.label)
    for (const name of named) expect(labels).toContain(name)
    for (const href of Object.values(UNDO_HREF)) {
      expect(SECTIONS.map((s) => `/admin/${s.path}`)).toContain(href)
    }
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
