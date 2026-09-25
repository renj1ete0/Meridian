/**
 * The web of topics (task B-72): the Map's Topics view and its hand-off to Find.
 *
 * The API returns sources by *exact* topic combination; every count the view
 * shows is a sum over those sets. The failures worth catching are arithmetic
 * ones that still render a plausible number — a subset counted instead of a
 * superset, an empty selection read as "nothing" — and a hand-off that drops
 * `topic_match=all` on the way to Find, which silently turns "where these
 * meet" into "any of these".
 *
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'
import { MAP_VIEWS, modeFromSearch } from '../src/explore/MapPage'
import { TopicFilter } from '../src/explore/TopicFilter'
import { TopicsView, readoutLine } from '../src/explore/map/TopicsScreen'
import { searchQuery, type TopicOverlaps } from '../src/lib/api'
import {
  carrying,
  combinationsContaining,
  findParams,
  labelWidth,
  layoutTopics,
  rimToRim,
  searchHref,
  selectLink,
  toggleTopic,
  topicLinks,
  topicTotals,
} from '../src/lib/topicweb'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

/**
 * Four topics, deliberately lopsided: `d` is carried by nobody alongside `a`,
 * and one source sits in three topics at once, so a pair and a triple differ.
 */
const DATA: TopicOverlaps = {
  overlaps: [
    { topics: ['a'], sources: 10 },
    { topics: ['b'], sources: 6 },
    { topics: ['a', 'b'], sources: 4 },
    { topics: ['a', 'b', 'c'], sources: 1 },
    { topics: ['b', 'c'], sources: 2 },
    { topics: ['c'], sources: 3 },
    { topics: ['d'], sources: 5 },
    { topics: ['c', 'd'], sources: 1 },
  ],
  labelled_sources: 32,
}

/** Brute force: expand every exact set into one entry per source, then filter. */
function bruteForce(data: TopicOverlaps, selection: string[]): number {
  const sources = data.overlaps.flatMap((o) => Array.from({ length: o.sources }, () => o.topics))
  return sources.filter((topics) => selection.every((t) => topics.includes(t))).length
}

describe('sources carrying a selection', () => {
  it('counts every labelled source when nothing is chosen', () => {
    expect(carrying(DATA, [])).toBe(DATA.labelled_sources)
    // And the exact sets add up to that total: they are a partition.
    expect(DATA.overlaps.reduce((n, o) => n + o.sources, 0)).toBe(DATA.labelled_sources)
  })

  it('sums supersets, not only the exact set', () => {
    // a+b exactly is 4; the source in a+b+c also carries both.
    expect(carrying(DATA, ['a', 'b'])).toBe(5)
    expect(carrying(DATA, ['b'])).toBe(6 + 4 + 1 + 2)
    expect(carrying(DATA, ['a', 'b', 'c'])).toBe(1)
  })

  it('is zero for a selection nobody carries, not the sum of its parts', () => {
    expect(carrying(DATA, ['a', 'd'])).toBe(0)
    expect(carrying(DATA, ['a', 'b', 'c', 'd'])).toBe(0)
    expect(carrying(DATA, ['nobody'])).toBe(0)
  })

  it('agrees with a per-source count for every possible selection', () => {
    const topics = ['a', 'b', 'c', 'd']
    for (let mask = 1; mask < 1 << topics.length; mask++) {
      const selection = topics.filter((_, i) => mask & (1 << i))
      expect(carrying(DATA, selection), selection.join('+')).toBe(bruteForce(DATA, selection))
    }
  })

  it('does not depend on the order the selection was made in', () => {
    expect(carrying(DATA, ['c', 'b'])).toBe(carrying(DATA, ['b', 'c']))
  })
})

describe('circles and links', () => {
  it('sizes each topic by every source carrying it', () => {
    const totals = topicTotals(DATA.overlaps)
    for (const { topic, sources } of totals) expect(sources).toBe(carrying(DATA, [topic]))
    expect(totals[0]).toEqual({ topic: 'a', sources: 15 })
  })

  it('links exactly the pairs some source carries together, weighted by how many', () => {
    const links = topicLinks(DATA.overlaps)
    const byKey = Object.fromEntries(links.map((l) => [`${l.a}|${l.b}`, l.shared]))
    expect(byKey).toEqual({ 'a|b': 5, 'a|c': 1, 'b|c': 3, 'c|d': 1 })
    for (const link of links) expect(link.shared).toBe(carrying(DATA, [link.a, link.b]))
    // No link where nobody overlaps: a and d never meet.
    expect(links.some((l) => (l.a === 'a' && l.b === 'd') || (l.a === 'd' && l.b === 'a'))).toBe(false)
  })

  it('lays every circle inside the drawing, at area proportional to sources', () => {
    const totals = topicTotals(DATA.overlaps)
    for (const width of [320, 640, 1000]) {
      const height = Math.max(300, width * 0.82)
      const placed = layoutTopics(totals, width, height, { minR: 1 })
      for (const p of placed) {
        expect(p.x - p.r).toBeGreaterThanOrEqual(0)
        expect(p.x + p.r).toBeLessThanOrEqual(width)
        expect(p.y - p.r).toBeGreaterThanOrEqual(0)
        expect(p.y + p.r).toBeLessThanOrEqual(height)
      }
      const [big, small] = [placed[0]!, placed.at(-1)!]
      const ratio = (big.r * big.r) / (small.r * small.r)
      expect(ratio).toBeCloseTo(big.sources / small.sources, 5)
    }
  })

  it('keeps every name inside the drawing, beside its circle on a desk and under it on a phone', () => {
    const totals = topicTotals([
      ...DATA.overlaps,
      { topics: ['a-rather-long-topic-name'], sources: 7 },
      { topics: ['another-long-one'], sources: 2 },
    ])
    for (const [width, height] of [
      [343, 446],
      [1000, 600],
    ] as const) {
      for (const p of layoutTopics(totals, width, height)) {
        const w = labelWidth(p.topic)
        const left = p.label.anchor === 'start' ? p.label.x : p.label.anchor === 'end' ? p.label.x - w : p.label.x - w / 2
        expect(left, `${p.topic} at ${width}`).toBeGreaterThanOrEqual(0)
        expect(left + w, `${p.topic} at ${width}`).toBeLessThanOrEqual(width)
        expect(p.label.y + 14).toBeLessThanOrEqual(height)
        expect(p.label.y - 12).toBeGreaterThanOrEqual(0)
      }
    }
  })

  it('runs a link from rim to rim, so it never hides under a circle', () => {
    const ends = rimToRim({ x: 0, y: 0, r: 10 }, { x: 100, y: 0, r: 20 })
    expect(ends).toEqual({ x1: 10, y1: 0, x2: 80, y2: 0 })
  })

  it('selecting a link replaces the selection with its two ends', () => {
    expect(selectLink({ a: 'b', b: 'a' })).toEqual(['a', 'b'])
    expect(toggleTopic(['c'], 'a')).toEqual(['a', 'c'])
    expect(toggleTopic(['a', 'c'], 'a')).toEqual(['c'])
  })

  it('breaks the count down into the exact sets that make it up', () => {
    const combos = combinationsContaining(DATA.overlaps, ['b'], Infinity)
    expect(combos.every((c) => c.topics.includes('b'))).toBe(true)
    expect(combos.reduce((n, c) => n + c.sources, 0)).toBe(carrying(DATA, ['b']))
    expect(combos[0]!.sources).toBeGreaterThanOrEqual(combos.at(-1)!.sources)
    expect(combinationsContaining(DATA.overlaps, ['a', 'd'])).toEqual([])
  })
})

describe('the view', () => {
  function readout(): string {
    return document.querySelector('[data-role="readout"]')!.textContent!.replace(/\s+/g, ' ').trim()
  }

  it('shows every labelled source before anything is chosen', () => {
    render(<TopicsView data={DATA} />)
    expect(readout()).toBe('32 labelled sources')
    expect(screen.getByRole('button', { name: 'Search where these meet' })).toHaveProperty('disabled', true)
  })

  it('draws one circle per topic and one line per overlapping pair', () => {
    const { container } = render(<TopicsView data={DATA} />)
    expect(container.querySelectorAll('[data-topic]')).toHaveLength(4)
    expect(container.querySelectorAll('[data-link]')).toHaveLength(topicLinks(DATA.overlaps).length)
  })

  it('multi-selects circles and reads out where they meet', () => {
    const { container } = render(<TopicsView data={DATA} />)
    fireEvent.click(container.querySelector('[data-topic="a"]')!)
    fireEvent.click(container.querySelector('[data-topic="b"]')!)
    expect(readout()).toBe('5 sources carry all of: a, b')
    expect(container.querySelector('[data-topic="a"]')!.getAttribute('aria-pressed')).toBe('true')

    // A second click on a circle takes it out again.
    fireEvent.click(container.querySelector('[data-topic="a"]')!)
    expect(readout()).toBe('13 sources carry b')
  })

  it('selects both ends when a link is clicked', () => {
    const { container } = render(<TopicsView data={DATA} initial={['d']} />)
    const link = [...container.querySelectorAll('[data-link]')].find(
      (el) => el.getAttribute('aria-label')?.startsWith('b and c'),
    )!
    fireEvent.click(link)
    expect(readout()).toBe('3 sources carry all of: b, c')
    expect(link.getAttribute('aria-pressed')).toBe('true')
    // The earlier choice is replaced, not added to.
    expect(container.querySelector('[data-topic="d"]')!.getAttribute('aria-pressed')).toBe('false')
  })

  it('says so when nobody carries the selection', () => {
    render(<TopicsView data={DATA} initial={['a', 'd']} />)
    expect(readout()).toBe('0 sources carry all of: a, d')
    expect(document.body.textContent).toMatch(/No source is labelled with all of these/)
  })

  it('searches Find inside every chosen topic at once', () => {
    const onSearch = vi.fn()
    render(<TopicsView data={DATA} initial={['a', 'b']} onSearch={onSearch} />)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: ' shade ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Search where these meet' }))
    expect(onSearch).toHaveBeenCalledTimes(1)
    const url = new URL(onSearch.mock.calls[0]![0] as string, 'http://x')
    expect(url.pathname).toBe('/')
    expect(url.searchParams.getAll('topic')).toEqual(['a', 'b'])
    expect(url.searchParams.get('topic_match')).toBe('all')
    expect(url.searchParams.get('q')).toBe('shade')
  })

  it('has readout sentences that agree in number', () => {
    expect(readoutLine(1, ['a', 'b'])).toBe('source carries all of: a, b')
    expect(readoutLine(0, [])).toBe('labelled sources')
    expect(readoutLine(1, [])).toBe('labelled source')
  })
})

describe('where the view lives', () => {
  it('is a view of the Map, reached at ?view=topics', () => {
    expect(modeFromSearch('?view=topics')).toBe('topics')
    expect(modeFromSearch('?view=nonsense')).toBe('areas')
    const topics = MAP_VIEWS.find(([mode]) => mode === 'topics')!
    expect(modeFromSearch(new URL(topics[2], 'http://x').search)).toBe('topics')
    // Every view's href round-trips to its own mode, so a tab never lies.
    for (const [mode, , href] of MAP_VIEWS) expect(modeFromSearch(new URL(href, 'http://x').search)).toBe(mode)
  })
})

describe('Find carries topic and topic_match', () => {
  it('round-trips the hand-off URL', () => {
    expect(findParams(new URL(searchHref(['a', 'b'], 'q x'), 'http://x').search)).toEqual({
      q: 'q x',
      topics: ['a', 'b'],
      match: 'all',
    })
    expect(findParams('?topic=a&topic=a&topic_match=bogus')).toEqual({ q: '', topics: ['a'], match: 'any' })
    expect(searchHref([])).toBe('/')
  })

  it('sends topic_match only when it narrows', () => {
    const all = new URLSearchParams(searchQuery({ q: 'x', topic: ['a', 'b'], topic_match: 'all' }))
    expect(all.getAll('topic')).toEqual(['a', 'b'])
    expect(all.get('topic_match')).toBe('all')
    expect(new URLSearchParams(searchQuery({ q: 'x', topic: ['a'], topic_match: 'any' })).has('topic_match')).toBe(false)
  })

  function stubbed() {
    const stats = {
      as_of: '2026-09-23T00:00:00Z',
      sources: 10,
      chunks: 100,
      embedded_chunks: 100,
      duplicate_chunks: 0,
      searchable_chunks: 100,
      entities: 5,
      edges: 4,
      contested_edges: 0,
      new_sources: null,
      new_chunks: null,
      topics: ['a', 'b', 'c'],
      sources_without_topics: 0,
    }
    const empty = {
      hits: [],
      arms: ['lexical'],
      degraded: false,
      degraded_reason: null,
      limit: 20,
      offset: 0,
      has_more: false,
      candidate_pool: 0,
      lexical_candidates: 0,
      vector_candidates: 0,
    }
    const fetchMock = vi.fn((url: string, _init?: RequestInit) => {
      if (url.includes('/api/explore/stats')) return Promise.resolve(new Response(JSON.stringify(stats)))
      if (url.includes('/api/explore/search')) return Promise.resolve(new Response(JSON.stringify(empty)))
      return new Promise<Response>(() => {})
    })
    vi.stubGlobal('fetch', fetchMock)
    const searches = () =>
      fetchMock.mock.calls
        .map(([url]) => String(url))
        .filter((u) => u.includes('/api/explore/search'))
        .map((u) => new URL(u, 'http://x').searchParams)
    return searches
  }

  it('searches with every topic and topic_match=all when the link says so', async () => {
    const searches = stubbed()
    window.history.pushState({}, '', searchHref(['a', 'b'], 'shade'))
    await act(async () => {
      render(<ExplorePage />)
    })
    expect(searches()).toHaveLength(1)
    expect(searches()[0]!.getAll('topic')).toEqual(['a', 'b'])
    expect(searches()[0]!.get('topic_match')).toBe('all')
    expect(searches()[0]!.get('q')).toBe('shade')
  })

  it('preselects the topics without searching when the link has no words', async () => {
    const searches = stubbed()
    window.history.pushState({}, '', searchHref(['a', 'b']))
    await act(async () => {
      render(<ExplorePage />)
    })
    expect(searches()).toHaveLength(0)
    expect(screen.getByRole('button', { name: 'a' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('radio', { name: 'all at once' }).getAttribute('aria-checked')).toBe('true')

    // The next search carries what the link set up.
    const input = document.querySelector('input') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'canopy' } })
    await act(async () => {
      fireEvent.submit(input.closest('form')!)
    })
    expect(searches().at(-1)!.get('topic_match')).toBe('all')
  })

  it('switches back to any from the filter, and the next search says so', async () => {
    const searches = stubbed()
    window.history.pushState({}, '', searchHref(['a', 'b'], 'shade'))
    await act(async () => {
      render(<ExplorePage />)
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('radio', { name: 'any of these' }))
    })
    expect(searches()).toHaveLength(2)
    expect(searches()[1]!.has('topic_match')).toBe(false)
    expect(searches()[1]!.getAll('topic')).toEqual(['a', 'b'])
  })

  it('offers the any/all switch only with two or more topics, and only for topics', () => {
    const one = renderToStaticMarkup(<TopicFilter topics={['a', 'b']} active={['a']} onMatch={() => {}} />)
    expect(one).not.toContain('all at once')
    const two = renderToStaticMarkup(<TopicFilter topics={['a', 'b']} active={['a', 'b']} onMatch={() => {}} />)
    expect(two).toContain('all at once')
    const places = renderToStaticMarkup(<TopicFilter topics={['a', 'b']} active={['a', 'b']} />)
    expect(places).not.toContain('all at once')
  })
})
