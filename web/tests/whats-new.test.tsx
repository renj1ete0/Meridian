// @vitest-environment jsdom
/**
 * What's new (task `B-237`): new sources by topic and kind since the reader's last visit or a
 * chosen window, each with its best passage, and new findings with their citations. Topics,
 * titles, sites and findings are made up.
 */
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { sectionOf } from '../src/App'
import { SinceLastVisit } from '../src/explore/SinceLastVisit'
import {
  KIND_ONE,
  WhatsNewPage,
  claimOf,
  metaOf,
  newHref,
  readNewQuery,
  sinceFor,
  summaryOf,
} from '../src/explore/WhatsNewPage'
import { DOC_KINDS, type NewFinding, type NewSource, type WhatsNew } from '../src/lib/api'
import { readFind } from '../src/lib/find'
import { markVisited, openSession, readPreviousVisit } from '../src/lib/lastVisit'
import { parseRoute } from '../src/lib/route'

const NOW = new Date('2026-11-01T12:00:00Z')

function source(over: Partial<NewSource> = {}): NewSource {
  return {
    source_id: 71,
    title: 'Northfield kerb allocation review, year one',
    url: 'https://northfield.example/review',
    site: 'northfield.example',
    publisher: 'Northfield Council',
    doc_kind: 'report',
    source_tier: 'government',
    published: '2026-10-30',
    added: '2026-10-31T02:00:00Z',
    other_topics: [],
    excerpt: {
      chunk_id: 9001,
      text: 'Loading bays on the high street were reallocated over a year.',
      page_or_offset: 4,
    },
    ...over,
  }
}

function finding(over: Partial<NewFinding> = {}): NewFinding {
  return {
    kind: 'link',
    finding_id: 501,
    subject_id: 11,
    subject: 'Loading bays',
    relation: 'reduces',
    object_id: 12,
    object: 'Delivery dwell time',
    topic: 'kerbside-access',
    added: '2026-10-31T03:00:00Z',
    citation: {
      chunk_id: 9001,
      source_id: 71,
      title: 'Northfield kerb allocation review',
      url: 'https://northfield.example/review',
      page_or_offset: 4,
      text: 'Dwell time fell by a fifth where bays were reallocated.',
    },
    ...over,
  }
}

function body(over: Partial<WhatsNew> = {}): WhatsNew {
  return {
    since: '2026-10-29T01:14:00Z',
    as_of: NOW.toISOString(),
    basis: 'added',
    topics: [
      {
        topic: 'kerbside-access',
        description: 'Made-up kerbside studies.',
        total: 31,
        by_kind: [
          { doc_kind: 'news', sources: 14 },
          { doc_kind: 'report', sources: 7 },
          { doc_kind: 'listing', sources: 2 },
          { doc_kind: null, sources: 1 },
        ],
        sources: [
          source(),
          source({
            source_id: 72,
            doc_kind: 'news',
            title: 'Drop-off zones trialled outside three Eastvale schools',
            site: 'eastvale-courier.example',
            publisher: null,
            other_topics: ['heat-and-shade'],
            excerpt: null,
          }),
        ],
      },
      { topic: 'bus-priority', description: null, total: 0, by_kind: [], sources: [] },
    ],
    elsewhere: 12,
    findings: [
      finding(),
      finding({ kind: 'tag', finding_id: 77, relation: 'school_drop_off_zones', object_id: null, object: '3' }),
    ],
    findings_total: 5,
    ...over,
  }
}

describe('the query in the URL', () => {
  it('reads and writes the window and basis, and drops what it does not know', () => {
    expect(readNewQuery('')).toEqual({ window: 'last', basis: 'added', since: null })
    expect(readNewQuery('?window=30d&basis=published')).toEqual({ window: '30d', basis: 'published', since: null })
    expect(readNewQuery('?window=year&basis=rumour&since=yesterday')).toEqual({
      window: 'last',
      basis: 'added',
      since: null,
    })
    const linked = { window: 'last', basis: 'added', since: '2026-10-29T01:14:00Z' } as const
    expect(readNewQuery(new URL(newHref(linked), 'http://x').search)).toEqual(linked)
    expect(newHref({ window: 'last', basis: 'added', since: null })).toBe('/new')
    // A chosen window carries no instant: "last 7 days" means from now, whenever opened.
    expect(newHref({ window: '7d', basis: 'added', since: '2026-10-29T01:14:00Z' })).toBe('/new?window=7d')
  })

  it('is its own section, reached at /new', () => {
    expect(parseRoute('/new').name).toBe('new')
    expect(parseRoute('/new/').name).toBe('new')
    expect(sectionOf(parseRoute('/new'))).toBe('new')
  })
})

describe('the window', () => {
  it('measures from the linked instant, else the previous visit, else a week and says so', () => {
    const last = { window: 'last', basis: 'added', since: null } as const
    expect(sinceFor({ ...last, since: '2026-10-20T00:00:00Z' }, '2026-10-01T00:00:00Z', NOW)).toEqual({
      since: '2026-10-20T00:00:00Z',
      fellBack: false,
    })
    expect(sinceFor(last, '2026-10-01T00:00:00Z', NOW)).toEqual({ since: '2026-10-01T00:00:00Z', fellBack: false })
    expect(sinceFor(last, null, NOW)).toEqual({ since: '2026-10-25T12:00:00.000Z', fellBack: true })
    expect(sinceFor({ ...last, window: '30d' }, '2026-10-01T00:00:00Z', NOW).since).toBe('2026-10-02T12:00:00.000Z')
  })

  describe('the previous visit', () => {
    beforeEach(() => window.localStorage.clear())

    it('is what the landing counted from, and opening What is new never moves it', () => {
      expect(readPreviousVisit()).toBeNull()
      markVisited(new Date('2026-10-01T00:00:00Z'))
      const counted = openSession(new Date('2026-10-20T00:00:00Z'))
      expect(readPreviousVisit()).toBe(counted)
      expect(readPreviousVisit()).toBe('2026-10-01T00:00:00.000Z')
    })

    it('survives storage that throws', () => {
      vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
        throw new DOMException('denied', 'SecurityError')
      })
      expect(readPreviousVisit()).toBeNull()
      vi.restoreAllMocks()
    })
  })
})

describe('the words', () => {
  it('names one document of every kind the database has', () => {
    for (const kind of DOC_KINDS) expect(KIND_ONE[kind]).toBeTruthy()
  })

  it('sums the topics for the summary, and names what lies elsewhere', () => {
    expect(summaryOf(body())).toBe('+31 sources on 1 topic · 12 on none of them · 5 new findings')
    expect(summaryOf(body({ elsewhere: 0, findings_total: 1 }))).toBe('+31 sources on 1 topic · 1 new finding')
  })

  it('gives site, publisher and calendar date, and when it arrived where that matters', () => {
    expect(metaOf(source(), 'added')).toBe('northfield.example · Northfield Council · 30 Oct 2026')
    expect(metaOf(source({ published: null }), 'added')).toMatch(/· undated · added \d\d \w{3}/)
    expect(metaOf(source(), 'published')).toMatch(/30 Oct 2026 · added \d\d \w{3}/)
    expect(metaOf(source({ other_topics: ['heat-and-shade'] }), 'added')).toContain('also Heat and shade')
  })

  it('writes a finding as a claim', () => {
    expect(claimOf(finding())).toBe('Loading bays reduces Delivery dwell time')
    expect(claimOf(finding({ relation: 'bay_count', object: '12', object_id: null }))).toBe('Loading bays bay count 12')
  })
})

describe('the page', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    window.history.pushState({}, '', '/')
    window.localStorage.clear()
  })

  async function open(path: string, answer: () => Promise<Response>) {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        calls.push(url)
        return url.startsWith('/api/explore/new') ? answer() : new Promise<Response>(() => {})
      }),
    )
    window.history.pushState({}, '', path)
    await act(async () => {
      render(<WhatsNewPage />)
    })
    return calls
  }

  const ok =
    (value: unknown, status = 200) =>
    () =>
      Promise.resolve(new Response(JSON.stringify(value), { status }))

  it('asks from the linked instant and lists each topic with its kinds and sources', async () => {
    const calls = await open('/new?since=2026-10-29T01:14:00Z', ok(body()))
    expect(new URL(calls[0]!, 'http://x').searchParams.get('since')).toBe('2026-10-29T01:14:00Z')

    const kerb = screen.getByRole('region', { name: 'kerbside access' })
    expect(kerb.textContent).toContain('+31 sources')
    const kinds = within(kerb).getByRole('list', { name: 'By kind' })
    expect(kinds.textContent).toContain('News 14')
    expect(kinds.textContent).toContain('Unclassified 1')
    // The title opens the source on the passage shown, not on page one.
    expect(
      within(kerb).getByRole('link', { name: 'Northfield kerb allocation review, year one' }).getAttribute('href'),
    ).toBe('/sources/71?passage=9001')
    expect(kerb.textContent).toContain('Loading bays on the high street were reallocated over a year.')
    expect(kerb.textContent).toContain('No passage of it is readable yet.')
    expect(kerb.textContent).toContain('2 of 31 listed · listings are counted, not listed')
    // A topic with nothing new says so rather than vanishing.
    expect(screen.getByRole('region', { name: 'bus priority' }).textContent).toContain('nothing new')
    expect(document.querySelector('[data-summary]')!.textContent).toBe(summaryOf(body()))
  })

  it('cites every finding and links its concepts', async () => {
    await open('/new', ok(body()))
    const link = screen.getByRole('listitem', { name: 'Loading bays reduces Delivery dwell time' })
    expect(within(link).getByRole('link', { name: 'Loading bays' }).getAttribute('href')).toBe('/nodes/11')
    expect(within(link).getByRole('link', { name: 'Delivery dwell time' }).getAttribute('href')).toBe('/nodes/12')
    expect(within(link).getByRole('link', { name: 'Northfield kerb allocation review' }).getAttribute('href')).toBe(
      '/sources/71?passage=9001',
    )
    expect(link.textContent).toContain('Dwell time fell by a fifth')
    const tag = screen.getByRole('listitem', { name: 'Loading bays school drop off zones 3' })
    expect(tag.textContent).toContain('tag · from')
    expect(screen.getByText('2 of 5 listed')).toBeTruthy()
  })

  it('falls back to a week, and says so, with no visit recorded', async () => {
    await open('/new', ok(body()))
    expect(screen.getByText(/No earlier visit is recorded in this browser/)).toBeTruthy()
  })

  it('changes window and basis through the URL and asks again', async () => {
    const calls = await open('/new', ok(body()))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Published' }))
    })
    expect(window.location.search).toBe('?basis=published')
    expect(new URL(calls.at(-1)!, 'http://x').searchParams.get('basis')).toBe('published')
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '30 days' }))
    })
    expect(readNewQuery(window.location.search)).toEqual({ window: '30d', basis: 'published', since: null })
    expect(screen.getByRole('button', { name: '30 days' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('opens Find on a topic', async () => {
    await open('/new', ok(body()))
    const kerb = screen.getByRole('region', { name: 'kerbside access' })
    const find = within(kerb).getByRole('link', { name: 'Find in this topic →' }).getAttribute('href')!
    expect(readFind(new URL(find, 'http://x').search).filters.topics).toEqual(['kerbside-access'])
  })

  it('says when it cannot be read', async () => {
    await open('/new', ok({ detail: 'since needs an offset' }, 422))
    expect(screen.getByRole('alert').textContent).toContain('What is new is unavailable')
  })
})

describe('on the landing', () => {
  it('links the since-last-visit line to the list, from the instant it counted', () => {
    const href = newHref({ window: 'last', basis: 'added', since: '2026-10-29T01:14:00Z' })
    const markup = renderToStaticMarkup(<SinceLastVisit newSources={12} newChunks={340} href={href} />)
    expect(markup).toContain(`href="${href.replace('&', '&amp;')}"`)
    expect(markup).toContain('What&#x27;s new →')
    // Nothing new: nothing to list, so no link.
    expect(renderToStaticMarkup(<SinceLastVisit newSources={0} newChunks={0} href={href} />)).not.toContain('href')
  })
})
