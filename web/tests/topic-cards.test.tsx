// @vitest-environment jsdom
/**
 * The topics on the landing (task `B-238`): each card says what the topic is, how much it
 * holds and how fresh, and every way in leads where the page it names will read it.
 * Topics, titles and fields are made up.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ExplorePage } from '../src/explore/ExplorePage'
import { TopicCards, fieldHref, topicFindHref } from '../src/explore/TopicCards'
import { areaFromSearch, pickFromSearch } from '../src/explore/map/AreasScreen'
import type { TopicCard } from '../src/lib/api'
import { readFind } from '../src/lib/find'
import { readGrowthQuery } from '../src/lib/growth'
import { parseRoute } from '../src/lib/route'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

function card(over: Partial<TopicCard> = {}): TopicCard {
  return {
    topic: 'kerbside-access',
    description: 'How streets are shared at the kerb.',
    sources: 1284,
    newest: { source_id: 71, title: 'Northfield kerb allocation review', published: '2026-09-12' },
    fields: [
      { area_id: 41, parent_id: 4, name: 'Urban freight', passages: 320 },
      { area_id: 42, parent_id: 4, name: 'Street design', passages: 120 },
    ],
    ...over,
  }
}

function hrefs(element: HTMLElement): string[] {
  return within(element)
    .getAllByRole('link')
    .map((a) => a.getAttribute('href')!)
}

describe('a topic card', () => {
  it('says what the topic is, how much it holds, and its newest source by date', () => {
    render(<TopicCards topics={[card()]} />)
    const article = screen.getByRole('article', { name: 'kerbside access' })
    expect(article.textContent).toContain('How streets are shared at the kerb.')
    expect(article.textContent).toContain('1,284 sources')
    // A calendar date, never shifted by the display zone (ADR 0009).
    expect(article.textContent).toContain('newest · 12 Sep 2026 · Northfield kerb allocation review')
    expect(within(article).getByRole('link', { name: 'Northfield kerb allocation review' }).getAttribute('href')).toBe(
      '/sources/71',
    )
  })

  it('says when there is no description or nothing yet, rather than padding', () => {
    render(<TopicCards topics={[card({ description: null, sources: 0, newest: null, fields: [] })]} />)
    const article = screen.getByRole('article')
    expect(article.textContent).toContain('No description has been written for this topic yet.')
    expect(article.textContent).toContain('0 sources')
    expect(article.textContent).toContain('nothing collected yet')
    expect(article.textContent).not.toContain('Fields')
  })

  it('tells an undated topic from an empty one', () => {
    render(<TopicCards topics={[card({ newest: null, sources: 3 })]} />)
    expect(screen.getByRole('article').textContent).toContain('no source in it carries a date')
  })

  it('is absent while the list is unknown or empty, not an empty frame', () => {
    const { container, rerender } = render(<TopicCards topics={null} />)
    expect(container.textContent).toBe('')
    rerender(<TopicCards topics={[]} />)
    expect(container.textContent).toBe('')
  })

  it('numbers the topics and keeps the order served', () => {
    render(<TopicCards topics={[card(), card({ topic: 'bus-priority' })]} />)
    expect(screen.getByRole('heading', { name: 'Topics · 2' })).toBeTruthy()
    expect(screen.getAllByRole('article').map((a) => a.getAttribute('data-topic'))).toEqual([
      'kerbside-access',
      'bus-priority',
    ])
  })
})

describe('every way in reads back as what it names', () => {
  it('opens a field on the Map at its region, picked', () => {
    const href = fieldHref({ area_id: 41, parent_id: 4, name: 'x', passages: 1 })
    const url = new URL(href, 'http://x')
    expect(parseRoute(url.pathname)).toEqual(parseRoute('/map'))
    expect(areaFromSearch(url.search)).toBe(4)
    expect(pickFromSearch(url.search)).toBe(41)
    // A region-level field has no parent: the whole map, picked.
    const top = new URL(fieldHref({ area_id: 9, parent_id: null, name: 'x', passages: 1 }), 'http://x')
    expect(areaFromSearch(top.search)).toBeNull()
    expect(pickFromSearch(top.search)).toBe(9)
  })

  it('opens Find narrowed to the topic, and Growth filtered by it', () => {
    const find = readFind(new URL(topicFindHref('kerbside-access'), 'http://x').search)
    expect(find.q).toBe('')
    expect(find.filters.topics).toEqual(['kerbside-access'])
    render(<TopicCards topics={[card()]} />)
    const growth = hrefs(screen.getByRole('article')).find((h) => h.startsWith('/growth'))!
    expect(readGrowthQuery(new URL(growth, 'http://x').search).topics).toEqual(['kerbside-access'])
  })

  it('narrows Find in place when the landing offers it, and leaves modified clicks alone', () => {
    const onFind = vi.fn()
    render(<TopicCards topics={[card()]} onFind={onFind} />)
    const link = screen.getByRole('link', { name: 'Find in this topic →' })
    fireEvent.click(link, { metaKey: true })
    expect(onFind).not.toHaveBeenCalled()
    fireEvent.click(link)
    expect(onFind).toHaveBeenCalledWith('kerbside-access')
  })
})

describe('on the landing', () => {
  function respond(value: unknown, status = 200) {
    return Promise.resolve(new Response(JSON.stringify(value), { status }))
  }

  async function landing(topics: (url: string) => Promise<Response>) {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        if (url.startsWith('/api/explore/topics')) return topics(url)
        return new Promise<Response>(() => {})
      }),
    )
    await act(async () => {
      render(<ExplorePage />)
    })
  }

  it('lists the topics, and a Find from one narrows the search to it', async () => {
    await landing(() => respond({ topics: [card()] }))
    expect(screen.getByRole('article', { name: 'kerbside access' })).toBeTruthy()
    fireEvent.click(screen.getByRole('link', { name: 'Find in this topic →' }))
    expect(readFind(window.location.search).filters.topics).toEqual(['kerbside-access'])
    expect(screen.getByText(/Searching within/)).toBeTruthy()
  })

  it('still renders the landing when the topics cannot be read', async () => {
    await landing(() => respond({ detail: 'down' }, 503))
    expect(screen.queryByRole('heading', { name: /Topics/ })).toBeNull()
    expect(document.querySelector('input')).not.toBeNull()
  })
})
