// @vitest-environment jsdom
/**
 * More passages (`B-174`): Find stopped at twenty with no way on, though the route pages by
 * `offset` up to the candidate pool. What matters is that the next page is the *same*
 * search (words and every filter), that it never asks past the pool the ranking saw (the
 * route refuses that window), and that the end is said to be the ranking's, not the corpus's.
 */
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'
import type { SearchHit, SearchResponse } from '../src/lib/api'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

function hit(chunk: number): SearchHit {
  return {
    chunk_id: chunk,
    source_id: chunk,
    text: `Passage number ${chunk}.`,
    page_or_offset: 1,
    chunk_index: 0,
    url: `https://example.test/${chunk}`,
    title: `Doc ${chunk}`,
    page_unit: 'page',
    media_type: 'application/pdf',
    source_tier: 'government',
    publication_date: null,
    language: 'en',
    topic_labels: null,
    passage_topics: null,
    duplicate_of: null,
    score: 0.01,
    lexical_rank: 1,
    vector_rank: null,
    age_days: null,
    decay: 1,
    score_before_decay: 0,
  }
}

function page(from: number, count: number, over: Partial<SearchResponse> = {}): SearchResponse {
  return {
    hits: Array.from({ length: count }, (_, i) => hit(from + i)),
    arms: ['lexical', 'vector'],
    degraded: false,
    degraded_reason: null,
    limit: 20,
    offset: from,
    has_more: true,
    candidate_pool: 100,
    lexical_candidates: 50,
    vector_candidates: 100,
    ...over,
  }
}

/** Answers searches from `pages` in order; anything else hangs. */
function stub(pages: Array<SearchResponse | 'fail'>) {
  const searches: URLSearchParams[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (!String(url).includes('/api/explore/search')) return new Promise<Response>(() => {})
      searches.push(new URL(String(url), 'http://x').searchParams)
      const next = pages.shift()
      if (next === 'fail' || next === undefined)
        return new Response(JSON.stringify({ detail: 'The database is busy.' }), { status: 503 })
      return new Response(JSON.stringify(next))
    }),
  )
  return searches
}

async function open(href: string) {
  window.history.pushState({}, '', href)
  await act(async () => {
    render(<ExplorePage />)
  })
  await screen.findByText('Passage number 0.')
}

async function more() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'More passages' }))
  })
}

describe('the next page', () => {
  it('is the same search, from where the list ends, appended', async () => {
    const searches = stub([page(0, 20), page(20, 20)])
    await open('/?q=bus+lanes&view=passages&tier=government&from=2015&place=JP')
    await more()

    const second = searches[1]!
    expect(second.get('q')).toBe('bus lanes')
    expect(second.get('offset')).toBe('20')
    expect(second.getAll('source_tier')).toEqual(['government'])
    expect(second.getAll('place')).toEqual(['JP'])
    expect(second.get('published_after')).toBe('2015-01-01')
    expect(screen.getByText('Passage number 0.')).toBeTruthy()
    expect(screen.getByText('Passage number 39.')).toBeTruthy()
  })

  it('never asks past the pool the ranking saw', async () => {
    const searches = stub([page(0, 20, { candidate_pool: 30 }), page(20, 10, { candidate_pool: 30, has_more: false })])
    await open('/?q=x&view=passages')
    await more()
    expect(Number(searches[1]!.get('offset')) + Number(searches[1]!.get('limit'))).toBeLessThanOrEqual(30)
  })

  it('shows a passage once even if the ranking shifted between requests', async () => {
    const shifted = page(20, 20)
    shifted.hits[0] = hit(19)
    stub([page(0, 20), shifted])
    await open('/?q=x&view=passages')
    await more()
    expect(screen.getAllByText('Passage number 19.')).toHaveLength(1)
  })
})

describe('the end and the failures', () => {
  it('says the end is the ranking’s once paged to it, and offers no more', async () => {
    stub([page(0, 20), page(20, 5, { has_more: false })])
    await open('/?q=x&view=passages')
    await more()
    expect(screen.queryByRole('button', { name: 'More passages' })).toBeNull()
    expect(screen.getByText(/The end of what this search ranked/)).toBeTruthy()
  })

  it('offers nothing on a first page that is the whole result', async () => {
    stub([page(0, 7, { has_more: false })])
    await open('/?q=x&view=passages')
    expect(screen.queryByRole('button', { name: 'More passages' })).toBeNull()
    expect(screen.queryByText(/The end of what this search ranked/)).toBeNull()
  })

  it('names a failed page and keeps what was already shown', async () => {
    stub([page(0, 20), 'fail'])
    await open('/?q=x&view=passages')
    await more()
    expect(screen.getByText('The database is busy.')).toBeTruthy()
    expect(screen.getByText('Passage number 0.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'More passages' })).toBeTruthy()
  })
})
