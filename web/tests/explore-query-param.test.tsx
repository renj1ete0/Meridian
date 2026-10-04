/**
 * `/?q=` runs a search on arrival (task P6-34): how the map's "Open in Find"
 * hands an area's terms over to Explore.
 *
 * @vitest-environment jsdom
 */
import { act, cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'
import { findHref, findParams } from '../src/lib/topicweb'

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
    expect(findParams(window.location.search).q).toBe('shade canopy')
  })

  it('makes each new question a step of Back, and Back runs the one before', () => {
    const fetchMock = pending()
    vi.stubGlobal('fetch', fetchMock)
    render(<ExplorePage />)
    ask('first question')
    ask('second question')
    expect(findParams(window.location.search).q).toBe('second question')

    act(() => {
      window.history.replaceState({}, '', findHref('first question', [], 'any'))
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

  it('round-trips words, topics and the all-topics match, and nothing else', () => {
    for (const [q, topics, match] of [
      ['x y', ['a', 'b'], 'all'],
      ['x', ['a'], 'any'],
      ['', ['a'], 'all'],
      ['z', [], 'all'],
    ] as const) {
      const back = findParams(new URL(findHref(q, topics, match), 'http://x').search)
      expect(back.q).toBe(q)
      expect(back.topics).toEqual(topics)
      expect(back.match).toBe(topics.length > 0 ? match : 'any')
    }
    expect(findHref('', [], 'any')).toBe('/')
  })
})
