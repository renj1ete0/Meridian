/**
 * The first run (task B-07, scaffold §1.7, spec §15 phase 0, §16).
 *
 * §16 calls cold-start seed quality a real risk, "worth spending an evening
 * on" — and until now that evening had to be spent editing
 * `config/seed_sources.yaml` *before* first boot, because the file is read once
 * and never again (§13.1). Somebody installing Meridian to see what it does has
 * no idea yet what belongs in it.
 *
 * Most of these tests are about copy, for the reason the topics tests are: the
 * screen's job is to be honest about a window that is already closing. The
 * crawl has started by the time anyone opens this, so the two states — what is
 * still changeable and what has already been reached — have to be visibly
 * different, and the second must not read as an error.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FirstRunPanel, describeSeed } from '../src/admin/FirstRunPanel'
import type { FirstRun, QueueTask } from '../src/lib/api'

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function seed(overrides: Partial<QueueTask> = {}): QueueTask {
  return {
    task_id: 1,
    url_or_query: 'https://example.gov/',
    task_type: 'url',
    status: 'pending',
    priority: 100,
    topic: null,
    seed_source: 'user',
    attempts: 0,
    claimed_by: null,
    created_at: '2026-09-20T00:00:00Z',
    ...overrides,
  }
}

function run(overrides: Partial<FirstRun> = {}): FirstRun {
  return {
    is_first_run: true,
    sources: 0,
    pending_seeds: [seed()],
    seeds_in_flight: 0,
    ...overrides,
  }
}

describe('what a seed is', () => {
  it('names a search as a search', () => {
    // A query is not an address, and showing it as one would have somebody
    // wondering why "pedestrian comfort" did not resolve.
    expect(describeSeed(seed({ task_type: 'query', url_or_query: 'kerb ramps' }))).toContain(
      'search:',
    )
  })

  it('shows a URL as itself', () => {
    expect(describeSeed(seed())).toBe('https://example.gov/')
  })
})

describe('a fresh install', () => {
  it('says nothing has been crawled and why this list matters', () => {
    const body = text(renderToStaticMarkup(<FirstRunPanel run={run()} />))

    expect(body).toContain('Nothing has been crawled yet')
    expect(body).toContain('propagates')
  })

  it('warns when there is nothing to start from at all', () => {
    // An empty queue on a fresh install is not a tidy state, it is a crawl
    // with nowhere to go — and the screen that shows it is the only place
    // anybody would find out.
    const body = text(
      renderToStaticMarkup(<FirstRunPanel run={run({ pending_seeds: [] })} />),
    )

    expect(body).toContain('nowhere to start')
  })
})

describe('once the crawl has moved', () => {
  it('reports progress rather than pretending it has not started', () => {
    const body = text(
      renderToStaticMarkup(
        <FirstRunPanel run={run({ is_first_run: false, sources: 1234 })} />,
      ),
    )

    expect(body).toContain('1,234 documents')
    expect(body).not.toContain('Nothing has been crawled yet')
  })

  it('explains why reached seeds cannot be removed, and what to do instead', () => {
    // The distinction this screen exists to keep: a reached seed has produced
    // fetch attempts, and dropping the queue row would leave them unexplained.
    // Saying only "cannot be removed" would read as a bug.
    const body = text(
      renderToStaticMarkup(<FirstRunPanel run={run({ seeds_in_flight: 3 })} />),
    )

    expect(body).toContain('3 already reached')
    expect(body).toContain('evidence')
    expect(body).toContain('Block the domain')
  })

  it('does not mention reached seeds when there are none', () => {
    const body = text(renderToStaticMarkup(<FirstRunPanel run={run()} />))

    expect(body).not.toContain('already reached')
  })
})

describe('the list', () => {
  it('offers a removal control per pending seed, labelled with the seed', () => {
    const markup = renderToStaticMarkup(
      <FirstRunPanel
        run={run({
          pending_seeds: [seed({ task_id: 7, url_or_query: 'https://lta.example/' })],
        })}
      />,
    )

    expect(markup).toContain('aria-label="Remove https://lta.example/"')
  })

  it('shows a seed topic when it has one', () => {
    const body = text(
      renderToStaticMarkup(
        <FirstRunPanel run={run({ pending_seeds: [seed({ topic: 'walkability' })] })} />,
      ),
    )

    expect(body).toContain('walkability')
  })

  it('surfaces a refusal from the server as written', () => {
    // The API's own sentence: a 409 explains *which* of the two refusals it
    // was, and replacing it with a generic line throws that away.
    const body = text(
      renderToStaticMarkup(
        <FirstRunPanel run={run()} error="That seed is being fetched right now." />,
      ),
    )

    expect(body).toContain('being fetched right now')
  })
})
