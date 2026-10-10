/**
 * Crawl health (task P6-25, spec §12.5, §13.4).
 *
 * The screen exists for one failure: a crawl that has silently stopped while
 * every process stays up. So the tests that matter are about telling the four
 * states apart in words — above all `stalled` from `waiting`, which look the
 * same in the numbers and want opposite reactions — and about the chart showing
 * a gap as a gap.
 */
// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AdminPage, HEALTH_REFRESH_MS } from '../src/admin/AdminPage'
import {
  CrawlHealthPanel,
  HourlyChart,
  OUTCOME_WORDS,
  STATUS_WORDS,
  bucketLabel,
  duration,
  verdict,
} from '../src/admin/CrawlHealthPanel'
import type { CrawlHealth, HourBucket, Liveness } from '../src/lib/api'

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&[a-z#0-9]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function hours(fill: (i: number) => [number, number] = () => [0, 0]): HourBucket[] {
  return Array.from({ length: 24 }, (_, i) => {
    const [succeeded, failed] = fill(i)
    return { start: `2026-09-22T${String(i).padStart(2, '0')}:00:00Z`, succeeded, failed }
  })
}

function liveness(over: Partial<Liveness> = {}): Liveness {
  return {
    state: 'crawling',
    last_attempt_at: '2026-09-23T11:58:00Z',
    quiet_seconds: 120,
    embed_backlog: null,
    embed_ceiling: null,
    ready: 40,
    pending: 42,
    ...over,
  }
}

function health(over: Partial<CrawlHealth> = {}): CrawlHealth {
  return {
    as_of: '2026-09-23T12:00:00+00:00',
    stall_after_seconds: 900,
    hours: hours(() => [10, 2]),
    outcomes: [
      { outcome: 'success', count: 240 },
      { outcome: 'timeout', count: 48 },
      { outcome: 'blocked', count: 0 },
    ],
    queue: { pending: 42, done: 300, failed: 7 },
    embedding_backlog: 1234,
    top_domains: [{ domain: 'docs.example', attempts: 12, succeeded: 11 }],
    liveness: liveness(),
    ...over,
  }
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

// --------------------------------------------------------------------------
// The verdict
// --------------------------------------------------------------------------

describe('the verdict, in words', () => {
  it('says a working crawl is crawling, and how recently it fetched', () => {
    const line = verdict(liveness(), 900)

    expect(line).toMatch(/^Crawling/)
    expect(line).toContain('2 min ago')
    expect(line).toContain('42 pages pending')
  })

  it('warns that a crawl with nothing pending is about to go idle', () => {
    expect(verdict(liveness({ pending: 0, ready: 0 }), 900)).toContain('Nothing is left pending')
  })

  it('names a stall with how long, how much, the threshold and what to check', () => {
    // The state the screen exists for. Every number in it is one the reader
    // needs to decide whether to get up.
    const line = verdict(liveness({ state: 'stalled', quiet_seconds: 42 * 60, ready: 3000 }), 900)

    expect(line).toMatch(/^Stalled/)
    expect(line).toContain('no fetch in 42 min')
    expect(line).toContain('3,000 pages ready')
    expect(line).toContain('every 15 min')
    expect(line).toContain('worker')
  })

  it('names a stall that never started as one, not as a quiet crawl', () => {
    const line = verdict(liveness({ state: 'stalled', quiet_seconds: null, last_attempt_at: null, ready: 1 }), 900)

    expect(line).toContain('nothing has ever been fetched')
    expect(line).toContain('1 page ready')
    expect(line).not.toContain('undefined')
    expect(line).not.toContain('null')
  })

  it('does not call a queue that is only backing off a stall', () => {
    // The distinction the fourth state exists for: somebody told "stalled"
    // restarts a worker that has nothing it is allowed to claim.
    const line = verdict(liveness({ state: 'waiting', quiet_seconds: 3600, ready: 0, pending: 5 }), 900)

    expect(line).toMatch(/^Waiting/)
    expect(line).toContain('backing off')
    expect(line).not.toMatch(/stall/i)
  })

  it('says an idle crawl has run out of work, and what to do', () => {
    const line = verdict(liveness({ state: 'idle', quiet_seconds: 7200, ready: 0, pending: 0 }), 900)

    expect(line).toMatch(/^Idle — the queue is empty/)
    expect(line).toContain('2 h ago')
    expect(line).toContain('Seed more')
  })

  it('handles a machine that has done nothing at all', () => {
    const line = verdict(
      liveness({ state: 'idle', quiet_seconds: null, last_attempt_at: null, ready: 0, pending: 0 }),
      900,
    )

    expect(line).toContain('nothing has ever been fetched')
    expect(line).not.toContain('null')
  })

  it('marks only a stall in the attention colour', () => {
    const stalled = renderToStaticMarkup(
      <CrawlHealthPanel health={health({ liveness: liveness({ state: 'stalled' }) })} />,
    )
    const crawling = renderToStaticMarkup(<CrawlHealthPanel health={health()} />)

    expect(stalled).toContain('data-state="stalled"')
    expect(stalled).toContain('border-accent-attention')
    expect(crawling).not.toContain('border-accent-attention')
  })
})

describe('durations', () => {
  it('rounds down, so a gap is never reported longer than it has been', () => {
    expect(duration(59)).toBe('under a minute')
    expect(duration(59 * 60 + 59)).toBe('59 min')
    expect(duration(3600)).toBe('1 h')
    expect(duration(3600 + 5 * 60)).toBe('1 h 5 min')
    expect(duration(3 * 86400)).toBe('3 days')
  })
})

// --------------------------------------------------------------------------
// The chart
// --------------------------------------------------------------------------

describe('the hourly chart', () => {
  it('draws one slot per hour, stacked, scaled to the busiest hour', () => {
    const { container } = render(
      <svg>
        <HourlyChart hours={hours((i) => (i === 23 ? [30, 10] : i === 0 ? [0, 20] : [0, 0]))} />
      </svg>,
    )

    expect(container.querySelectorAll('[data-bucket]')).toHaveLength(24)
    const newest = container.querySelector('[data-bucket="23"]')!
    const ok = newest.querySelector('[data-part="succeeded"]')!
    const bad = newest.querySelector('[data-part="failed"]')!
    // 30 of a 40 peak and 10 of it: three to one, whatever the pixel scale.
    expect(Number(ok.getAttribute('height')) / Number(bad.getAttribute('height'))).toBeCloseTo(3)
    // Failures stack above successes, with a gap between.
    expect(Number(bad.getAttribute('y')) + Number(bad.getAttribute('height'))).toBeLessThan(
      Number(ok.getAttribute('y')),
    )

    const oldest = container.querySelector('[data-bucket="0"]')!
    expect(oldest.querySelector('[data-part="succeeded"]')).toBeNull()
    expect(oldest.querySelector('[data-part="failed"]')).not.toBeNull()
  })

  it('keeps an empty hour as a slot rather than closing the gap', () => {
    // The gap is what somebody is looking for: the hour the fetching stopped.
    const { container } = render(
      <svg>
        <HourlyChart hours={hours((i) => (i === 12 ? [0, 0] : [5, 0]))} />
      </svg>,
    )

    const gap = container.querySelector('[data-bucket="12"]')!
    expect(gap.querySelectorAll('[data-part]')).toHaveLength(0)
    expect(gap.querySelector('title')!.textContent).toContain('0 succeeded, 0 failed')
    expect(container.querySelectorAll('[data-part="succeeded"]')).toHaveLength(23)
  })

  it('labels each bar by how long ago, not by a clock time it is not aligned to', () => {
    expect(bucketLabel(23, 24)).toBe('Last hour')
    expect(bucketLabel(0, 24)).toBe('23–24 h ago')
  })

  it('draws no bars and divides by nothing when there were no attempts', () => {
    const markup = renderToStaticMarkup(<HourlyChart hours={hours()} />)

    expect(markup).not.toContain('data-part')
    expect(markup).not.toContain('NaN')
    expect(markup).not.toContain('Infinity')
  })
})

// --------------------------------------------------------------------------
// The rest of the panel
// --------------------------------------------------------------------------

describe('the panel', () => {
  it('shows the outcome mix without the outcomes that did not happen', () => {
    const body = text(renderToStaticMarkup(<CrawlHealthPanel health={health()} />))

    // In words (`B-197`).
    expect(body).toContain('fetched 240')
    expect(body).toContain('timed out 48')
    expect(body).not.toContain('blocked')
  })

  it('shows the queue by status and the embedding backlog beside it', () => {
    const body = text(renderToStaticMarkup(<CrawlHealthPanel health={health()} />))

    expect(body).toContain('waiting to be fetched 42')
    expect(body).toContain('failed 7')
    expect(body).toContain('awaiting embedding 1,234')
  })

  it('names the busiest domains with both halves of their rate', () => {
    const body = text(renderToStaticMarkup(<CrawlHealthPanel health={health()} />))

    expect(body).toContain('docs.example')
    expect(body).toContain('11 of 12 succeeded')
  })

  it('says so when there is nothing to show, rather than rendering blanks', () => {
    const body = text(
      renderToStaticMarkup(
        <CrawlHealthPanel
          health={health({
            hours: hours(),
            outcomes: [{ outcome: 'success', count: 0 }],
            queue: {},
            embedding_backlog: 0,
            top_domains: [],
            liveness: liveness({
              state: 'idle',
              quiet_seconds: null,
              last_attempt_at: null,
              ready: 0,
              pending: 0,
            }),
          })}
        />,
      ),
    )

    expect(body).toContain('Idle')
    expect(body).toContain('No fetch attempts in the last 24 hours')
    expect(body).toContain('None recorded')
    expect(body).toContain('Nothing fetched in the last hour')
    expect(body).not.toContain('NaN')
  })
})

// --------------------------------------------------------------------------
// Refreshing
// --------------------------------------------------------------------------

describe('refreshing while open', () => {
  function stubApi() {
    const calls: string[] = []
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      calls.push(url)
      const body = url.includes('/crawl-health')
        ? health()
        : url.includes('/first-run')
          ? { is_first_run: false, seeds: [], topics: [], queue: {} }
          : url.includes('/runs')
            ? { rows: [], total: 0, active: null }
            : { rows: [], pending: 0, approved: 0, rejected: 0 }
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)
    return () => calls.filter((u) => u.includes('/api/explore/crawl-health')).length
  }

  function setVisibility(state: 'visible' | 'hidden') {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
    document.dispatchEvent(new Event('visibilitychange'))
  }

  it('refetches on the interval, pauses while hidden, and stops when closed', async () => {
    vi.useFakeTimers()
    const healthCalls = stubApi()
    setVisibility('visible')

    render(<AdminPage />)
    await act(async () => {
      fireEvent.click(screen.getByRole('link', { name: 'Crawl health' }))
    })
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(healthCalls()).toBe(1)
    expect(screen.getByText(/^Crawling —/)).toBeTruthy()

    await act(() => vi.advanceTimersByTimeAsync(HEALTH_REFRESH_MS))
    expect(healthCalls()).toBe(2)

    // Hidden: the timer fires and nothing is fetched.
    await act(async () => setVisibility('hidden'))
    await act(() => vi.advanceTimersByTimeAsync(HEALTH_REFRESH_MS * 3))
    expect(healthCalls()).toBe(2)

    // Back: refreshed at once, not up to half a minute later.
    await act(async () => setVisibility('visible'))
    await act(() => vi.advanceTimersByTimeAsync(0))
    expect(healthCalls()).toBe(3)

    // Another section: the polling stops with the panel.
    await act(async () => {
      fireEvent.click(screen.getByRole('link', { name: 'Run history' }))
    })
    const before = healthCalls()
    await act(() => vi.advanceTimersByTimeAsync(HEALTH_REFRESH_MS * 3))
    expect(healthCalls()).toBe(before)
  })
})

describe('the evidence in words, and a way to act on it (B-197)', () => {
  function enumOf(name: string): string[] {
    // Read from the model, so a value added to the database must be given words here.
    const model = readFileSync(
      join(__dirname, '..', '..', 'packages', 'meridian_core', 'meridian_core', 'models', 'queue.py'),
      'utf8',
    )
    const body = new RegExp(`${name} = constrained\\(([\\s\\S]*?)name=`).exec(model)![1]!
    return [...body.matchAll(/^\s*"([a-z_]+)"/gm)].map((m) => m[1]!).sort()
  }

  it('words every outcome and queue state the database allows', () => {
    expect(Object.keys(OUTCOME_WORDS).sort()).toEqual(enumOf('FETCH_OUTCOME'))
    expect(Object.keys(STATUS_WORDS).sort()).toEqual(enumOf('TASK_STATUS'))
  })

  it('shows an outcome in words with the stored value in reach, and leaves empty states out', () => {
    const markup = renderToStaticMarkup(
      <CrawlHealthPanel
        health={health({
          outcomes: [{ outcome: 'robots_unreachable', count: 3 }],
          queue: { pending: 42, extracted: 0, failed: 7 },
        })}
      />,
    )
    expect(markup).toContain('robots.txt could not be read')
    expect(markup).toContain('title="robots_unreachable"')
    expect(markup).toContain('waiting to be fetched')
    expect(markup).not.toContain('read, being stored')
  })

  it('links a busy domain to its fetch policy row', () => {
    const markup = renderToStaticMarkup(<CrawlHealthPanel health={health()} />)
    expect(markup).toContain('href="/admin/fetch-policy?q=docs.example"')
  })
})

describe('Fetch policy opened from a domain link (B-197)', () => {
  afterEach(() => window.history.pushState({}, '', '/'))

  it('asks for that domain first', async () => {
    const urls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => {
        urls.push(String(url))
        return new Promise<Response>(() => {})
      }),
    )
    window.history.pushState({}, '', '/admin/fetch-policy?q=docs.example')
    await act(async () => {
      render(<AdminPage />)
    })
    const policy = urls.filter((u) => u.includes('/api/admin/fetch-policy'))
    expect(policy.length).toBeGreaterThan(0)
    expect(new URL(policy[0]!, 'http://x').searchParams.get('q')).toBe('docs.example')
  })
})
