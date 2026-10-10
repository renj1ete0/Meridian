// @vitest-environment jsdom
/**
 * `/?q=` runs a search on arrival (task P6-34): how the map's "Open in Find"
 * hands an area's terms over to Explore.
 */
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'
import { findLink, NO_FILTERS, readFind, type FindFilters } from '../src/lib/find'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

function pending() {
  // Nothing answers: the test is about what is asked, not what is shown.
  return vi.fn((_url: string, _init?: RequestInit) => new Promise<Response>(() => {}))
}

describe('a query in the URL', () => {
  it('is searched for once, as typed', () => {
    const fetchMock = pending()
    vi.stubGlobal('fetch', fetchMock)
    window.history.pushState({}, '', '/?q=shade%20canopy')
    render(<ExplorePage />)
    const searches = fetchMock.mock.calls.map(([url]) => String(url)).filter((u) => u.includes('/api/explore/search'))
    expect(searches).toHaveLength(1)
    expect(new URL(searches[0]!, 'http://x').searchParams.get('q')).toBe('shade canopy')
    expect((document.querySelector('input[type="search"], input') as HTMLInputElement).value).toBe('shade canopy')
  })

  it('asks for nothing when there is none', () => {
    const fetchMock = pending()
    vi.stubGlobal('fetch', fetchMock)
    render(<ExplorePage />)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/explore/search'))).toBe(false)
  })
})

describe('a search written back to the URL (B-95)', () => {
  function searchesOf(fetchMock: ReturnType<typeof pending>): string[] {
    return fetchMock.mock.calls
      .map(([url]) => String(url))
      .filter((u) => u.includes('/api/explore/search'))
      .map((u) => new URL(u, 'http://x').searchParams.get('q') ?? '')
  }

  function ask(text: string) {
    const input = document.querySelector('input[type="search"], input') as HTMLInputElement
    fireEvent.change(input, { target: { value: text } })
    fireEvent.submit(input.closest('form') ?? input)
    if (!input.closest('form')) fireEvent.keyDown(input, { key: 'Enter' })
  }

  it('puts a typed search in the URL, where a reload finds it', () => {
    vi.stubGlobal('fetch', pending())
    render(<ExplorePage />)
    ask('shade canopy')
    expect(readFind(window.location.search).q).toBe('shade canopy')
  })

  it('makes each new question a step of Back, and Back runs the one before', () => {
    const fetchMock = pending()
    vi.stubGlobal('fetch', fetchMock)
    render(<ExplorePage />)
    ask('first question')
    ask('second question')
    expect(readFind(window.location.search).q).toBe('second question')

    act(() => {
      window.history.replaceState({}, '', findLink('first question'))
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(searchesOf(fetchMock).at(-1)).toBe('first question')
  })

  it('does not add an entry for the search the URL already holds', () => {
    vi.stubGlobal('fetch', pending())
    window.history.pushState({}, '', '/?q=shade')
    const before = window.history.length
    render(<ExplorePage />)
    expect(window.history.length).toBe(before)
  })

  it('round-trips the words, every filter and the chosen view (B-173)', () => {
    const cases: Array<[string, FindFilters, 'answer' | 'passages' | undefined]> = [
      [
        'x y',
        { topics: ['a', 'b'], match: 'all', places: ['DE', 'FR'], tiers: ['press'], from: 2010, to: 2020 },
        'answer',
      ],
      ['x', { ...NO_FILTERS, topics: ['a'] }, undefined],
      ['', { ...NO_FILTERS, places: ['JP'] }, undefined],
      ['z', { ...NO_FILTERS, from: 2001 }, 'passages'],
    ]
    for (const [q, filters, view] of cases) {
      const back = readFind(new URL(findLink(q, filters, view), 'http://x').search)
      expect(back.q).toBe(q)
      expect(back.filters).toEqual(filters)
      expect(back.view).toBe(view ?? null)
    }
    expect(findLink('')).toBe('/')
  })

  it('drops what a hand-edited link cannot mean, rather than searching for it', () => {
    const back = readFind('?q=x&tier=rumour&tier=press&from=19&to=abcd&view=both&topic_match=bogus&topic=a&topic=a')
    expect(back.filters).toEqual({ ...NO_FILTERS, topics: ['a'], tiers: ['press'] })
    expect(back.view).toBeNull()
    expect(readFind('?from=1200&to=3000').filters).toEqual(NO_FILTERS)
  })

  it('sends every filter in the link to the search it opens', () => {
    const fetchMock = pending()
    vi.stubGlobal('fetch', fetchMock)
    window.history.pushState({}, '', '/?q=shade&place=DE&tier=government&from=2015&to=2012')
    render(<ExplorePage />)
    const url = fetchMock.mock.calls.map(([u]) => String(u)).find((u) => u.includes('/api/explore/search'))!
    const params = new URL(url, 'http://x').searchParams
    expect(params.getAll('place')).toEqual(['DE'])
    expect(params.getAll('source_tier')).toEqual(['government'])
    // A range typed backwards is the same span.
    expect(params.get('published_after')).toBe('2012-01-01')
    expect(params.get('published_before')).toBe('2015-12-31')
  })
})
