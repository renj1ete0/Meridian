/**
 * `/?q=` runs a search on arrival (task P6-34): how the map's "Open in Find"
 * hands an area's terms over to Explore.
 *
 * @vitest-environment jsdom
 */
import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'

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
