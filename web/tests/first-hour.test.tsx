/**
 * What an empty corpus shows (task B-09, scaffold §1.7, §12.3).
 *
 * Production starts empty by design, so the first hour has nothing to search.
 * The task offered two ways out — ship a small real crawl as an opt-in demo
 * corpus, or make the first hour legible — and this is the second. The reasons
 * are in the component, and the tests are about the property that follows from
 * them: **everything on this screen is true of this machine right now.**
 *
 * Which makes the honest-failure cases the ones worth testing. A crawl that has
 * not started and a crawl failing every fetch both look like "no results", and
 * each needs a different sentence.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FirstHour, hasStarted, waiting } from '../src/explore/FirstHour'
import type { CrawlProgress } from '../src/lib/api'

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z#0-9]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function progress(over: Partial<CrawlProgress> = {}): CrawlProgress {
  return {
    as_of: '2026-09-20T12:00:00Z',
    queue: { pending: 42 },
    recent_domains: [],
    attempts_last_hour: 0,
    successes_last_hour: 0,
    ...over,
  }
}

describe('reading the queue', () => {
  it('counts what is waiting', () => {
    expect(waiting(progress({ queue: { pending: 7, done: 3 } }))).toBe(7)
  })

  it('treats an absent status as zero rather than undefined', () => {
    expect(waiting(progress({ queue: { done: 3 } }))).toBe(0)
  })

  it('separates "not started" from "started and failing"', () => {
    // The two states that both render as an empty corpus. Conflating them is
    // how somebody waits an hour for a crawl that had nowhere to begin.
    expect(hasStarted(progress({ queue: {}, attempts_last_hour: 0 }))).toBe(false)
    expect(hasStarted(progress({ queue: {}, attempts_last_hour: 5 }))).toBe(true)
  })
})

describe('a crawl that is working', () => {
  it('says what it is working through and when to expect results', () => {
    const body = text(renderToStaticMarkup(<FirstHour progress={progress()} />))

    expect(body).toContain('42 queued pages')
    expect(body).toContain('within the hour')
  })

  it('names recently fetched domains rather than only counting them', () => {
    // A name somebody recognises says more about whether this is working than
    // any count does.
    const body = text(
      renderToStaticMarkup(
        <FirstHour progress={progress({ recent_domains: ['lta.example', 'ura.example'] })} />,
      ),
    )

    expect(body).toContain('lta.example')
    expect(body).toContain('ura.example')
  })

  it('reports both halves of the fetch rate', () => {
    const body = text(
      renderToStaticMarkup(
        <FirstHour progress={progress({ attempts_last_hour: 20, successes_last_hour: 18 })} />,
      ),
    )

    expect(body).toContain('18 of 20 fetches succeeded')
  })
})

describe('the two ways it is not working', () => {
  it('says so when there is nothing queued and nothing attempted', () => {
    const body = text(
      renderToStaticMarkup(
        <FirstHour progress={progress({ queue: {}, attempts_last_hour: 0 })} />,
      ),
    )

    expect(body).toContain('nowhere to begin')
    expect(body).not.toContain('within the hour')
  })

  it('calls out a crawl where every fetch failed', () => {
    // Twenty attempts and no successes is not progress, and a screen showing
    // only the attempt count would read as though it were.
    const body = text(
      renderToStaticMarkup(
        <FirstHour progress={progress({ attempts_last_hour: 20, successes_last_hour: 0 })} />,
      ),
    )

    expect(body).toContain('Every one failed')
    expect(body).toContain('Domains')
  })

  it('does not cry failure when some fetches worked', () => {
    const body = text(
      renderToStaticMarkup(
        <FirstHour progress={progress({ attempts_last_hour: 20, successes_last_hour: 1 })} />,
      ),
    )

    expect(body).not.toContain('Every one failed')
  })
})

describe('the queue breakdown', () => {
  it('lists statuses rather than summing them', () => {
    // §12.5's argument: 4,000 pending and 4,000 failed are the same depth and
    // opposite situations.
    const body = text(
      renderToStaticMarkup(
        <FirstHour progress={progress({ queue: { pending: 10, failed: 4 } })} />,
      ),
    )

    expect(body).toContain('pending')
    expect(body).toContain('failed')
  })

  it('omits statuses with nothing in them', () => {
    const body = text(
      renderToStaticMarkup(<FirstHour progress={progress({ queue: { pending: 10 } })} />),
    )

    expect(body).not.toContain('embedded')
  })
})
