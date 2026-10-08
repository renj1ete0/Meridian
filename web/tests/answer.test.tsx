// @vitest-environment jsdom
/**
 * The answer page: evidence grouped by country, coverage stated against its
 * rule, and "find more" queuing exactly the search it says it will.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AnswerBody, AnswerView, countLine, mixLine, thinReason } from '../src/explore/AnswerView'
import { ModeSwitch } from '../src/explore/ExplorePage'
import {
  findMoreQuery,
  initialMode,
  looksLikeQuestion,
  rememberMode,
  type Answer,
  type AnswerGroup,
  type AnswerItem,
} from '../src/lib/answer'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const QUESTION = 'What rules govern street trading, and how do they differ?'

function item(over: Partial<AnswerItem> = {}): AnswerItem {
  return {
    source_id: 11,
    chunk_id: 101,
    title: 'Trading licence guidance',
    url: 'https://www.example.gov/guidance',
    publisher: 'example.gov',
    source_tier: 'government',
    publication_date: '2025-04-02',
    text: 'A licence is required before trading on a public street.',
    score: 0.03,
    passages: 3,
    ...over,
  }
}

function group(over: Partial<AnswerGroup> = {}): AnswerGroup {
  return {
    code: 'DE',
    name: 'Germany',
    sources: 4,
    publishers: 3,
    tier_mix: { government: 1, press: 3 },
    newest: '2025-04-02',
    coverage: 'strong',
    items: [
      item(),
      item({
        source_id: 12,
        chunk_id: 102,
        source_tier: 'press',
        title: 'News report',
        publisher: 'news.example',
        passages: 1,
      }),
    ],
    unexamined: 0,
    several_places: 0,
    ...over,
  }
}

function answer(over: Partial<Answer> = {}): Answer {
  return {
    query: QUESTION,
    groups: [
      group(),
      group({
        code: 'FR',
        name: 'France',
        sources: 2,
        publishers: 2,
        tier_mix: { press: 2 },
        coverage: 'thin',
        items: [item({ source_id: 21, chunk_id: 201, source_tier: 'press', title: 'Paris markets' })],
      }),
    ],
    unplaced: group({
      code: null,
      name: 'No place named',
      sources: 5,
      publishers: 5,
      tier_mix: { informal: 5 },
      coverage: 'thin',
      unexamined: 2,
      items: [item({ source_id: 31, chunk_id: 301, source_tier: 'informal', title: 'A blog post' })],
    }),
    coverage_rule:
      'Strong means at least 3 sources from different publishers, including at least one government or peer-reviewed source. Anything less is thin. A country with no matching source is not listed.',
    strong_min_publishers: 3,
    strong_needs_tiers: ['government', 'peer_reviewed'],
    topics: ['trading'],
    passages_considered: 40,
    sources_considered: 11,
    candidate_pool: 300,
    arms: ['lexical', 'vector'],
    degraded: false,
    degraded_reason: null,
    ...over,
  }
}

/** Answers by path; records every call. */
function server(routes: Record<string, { status?: number; body: unknown }>) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    void init
    const key = Object.keys(routes).find((path) => url.startsWith(path))
    const route = key ? routes[key]! : { status: 404, body: { detail: 'no route' } }
    return new Response(JSON.stringify(route.body), { status: route.status ?? 200 })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

const QUEUED = {
  task_id: 9,
  url_or_query: 'x',
  task_type: 'query',
  status: 'pending',
  priority: 100,
  topic: 'trading',
  seed_source: 'user',
  attempts: 0,
  claimed_by: null,
  created_at: '2026-09-25T00:00:00Z',
}

describe('the grouped answer', () => {
  it('shows a coverage chip per country, marked by word and form, and the unplaced count', () => {
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    const strip = screen.getByRole('region', { name: 'Coverage by country' })
    const chips = strip.querySelectorAll('[data-coverage]')
    expect([...chips].map((c) => c.getAttribute('data-coverage'))).toEqual(['strong', 'thin', 'unplaced'])
    expect(chips[0]!.textContent).toContain('Germany')
    expect(chips[0]!.textContent).toContain('4')
    expect(chips[0]!.textContent).toContain('strong')
    // Thin is dashed, not only differently coloured.
    expect(chips[1]!.className).toContain('border-dashed')
    expect(chips[1]!.textContent).toContain('thin')
    expect(chips[2]!.textContent).toContain('with no place')
    expect(chips[2]!.textContent).toContain('5')
    // Each chip jumps to its section.
    expect(chips[0]!.getAttribute('href')).toBe('#answer-DE')
    expect(document.getElementById('answer-DE')).not.toBeNull()
    // The rule is on the page, in words.
    expect(strip.textContent).toContain('Strong means at least 3 sources')
    expect(strip.textContent).toContain('2 countries · 1 strong · 1 thin · from 11 sources')
  })

  it('gives each country a count line and items linking to the source page', () => {
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    const de = screen.getByRole('region', { name: 'Germany' })
    expect(de.textContent).toContain('4 sources · 3 publishers · 1 government · 3 press · newest 2025-04-02')
    const links = within(de).getAllByRole('link')
    expect(links.map((a) => a.getAttribute('href'))).toEqual(['/sources/11', '/sources/12'])
    expect(de.textContent).toContain('A licence is required before trading')
    expect(de.textContent).toContain('3 matching passages')
    expect(de.textContent).toContain('2 more sources not shown')
    // A strong country offers no find-more button.
    expect(within(de).queryByRole('button', { name: /Find more/ })).toBeNull()
  })

  it('says why a thin country is thin and offers to find more', () => {
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    const fr = screen.getByRole('region', { name: 'France' })
    expect(fr.textContent).toContain('Thin: 2 publishers, fewer than 3; no government or peer-reviewed source.')
    expect(within(fr).getByRole('button', { name: 'Find more about France' })).toBeTruthy()
  })

  it('says how many unplaced sources were never checked for places', () => {
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    const rest = screen.getByRole('region', { name: 'No place named' })
    expect(rest.textContent).toContain('2 sources not yet checked for places')
    expect(within(rest).queryByRole('button', { name: /Find more about/ })).toBeNull()
  })

  it('counts each kind of unplaced source, so several-country sources are not called placeless (B-168)', () => {
    const unplaced = { ...answer().unplaced!, sources: 6, unexamined: 2, several_places: 3 }
    render(<AnswerBody answer={answer({ unplaced })} question={QUESTION} topic="trading" />)
    const rest = screen.getByRole('region', { name: 'No place named' })
    expect(rest.textContent).toContain(
      '1 source that names no country often enough to be about one, 3 sources about several countries, in passages that name none of them and 2 sources not yet checked for places.',
    )
  })

  it('says nothing about several countries when there are none', () => {
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    const rest = screen.getByRole('region', { name: 'No place named' })
    expect(rest.textContent).not.toContain('several countries')
    expect(rest.textContent).toContain('3 sources that name no country often enough to be about one')
  })

  it('uses plain words, not the system’s internal ones', () => {
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    const page = document.body.textContent!.toLowerCase()
    for (const jargon of ['passage_', 'peer_reviewed', 'tier', 'chunk', 'node', 'lexical']) {
      expect(page).not.toContain(jargon)
    }
  })

  it('says so when nothing matched', () => {
    render(<AnswerBody answer={answer({ groups: [], unplaced: null })} question="zzz" topic={null} />)
    expect(document.body.textContent).toContain('No source matched "zzz".')
    expect(screen.queryByRole('region', { name: 'Coverage by country' })).toBeNull()
  })

  it('reads the thin reason off the rule it was given, not a copy of it', () => {
    const one = group({ publishers: 1, tier_mix: { government: 1 }, coverage: 'thin' })
    expect(thinReason(one, { strong_min_publishers: 3, strong_needs_tiers: ['government'] })).toBe(
      'Thin: one publisher, fewer than 3.',
    )
    expect(thinReason(one, { strong_min_publishers: 1, strong_needs_tiers: ['peer_reviewed'] })).toBe(
      'Thin: no peer-reviewed source.',
    )
  })

  it('lists primary tiers first and keeps an unknown tier rather than dropping it', () => {
    expect(mixLine({ press: 2, peer_reviewed: 1, government: 1 })).toBe('1 government · 1 peer-reviewed · 2 press')
    expect(mixLine({ press: 1, archive: 2 })).toBe('1 press · 2 archive')
    expect(countLine(group({ sources: 1, publishers: 1, tier_mix: { press: 1 }, newest: null }))).toBe(
      '1 source · 1 press · no dates',
    )
  })
})

describe('find more', () => {
  it('queues the question plus the country as a search seed under the topic, then says it is queued', async () => {
    const fetchMock = server({ '/api/admin/seeds': { status: 201, body: QUEUED } })
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    fireEvent.click(screen.getByRole('button', { name: 'Find more about France' }))

    await screen.findByText('Queued — results arrive as the crawler fetches them.')
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]!
    expect(url).toBe('/api/admin/seeds')
    expect(init?.method).toBe('POST')
    const body = JSON.parse(String(init?.body))
    expect(body).toMatchObject({
      url_or_query: `${QUESTION} France`,
      task_type: 'query',
      topic: 'trading',
    })
    expect(body.reason).toContain('France')
    expect(screen.queryByRole('button', { name: 'Find more about France' })).toBeNull()
  })

  it('treats "already queued" as queued, not as a failure', async () => {
    server({ '/api/admin/seeds': { status: 409, body: { detail: 'That is already queued.' } } })
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    fireEvent.click(screen.getByRole('button', { name: 'Find more about France' }))
    expect(await screen.findByText('Already queued — results arrive as the crawler fetches them.')).toBeTruthy()
  })

  it('shows the server’s refusal and leaves the button to retry', async () => {
    server({ '/api/admin/seeds': { status: 503, body: { detail: 'Admin is closed.' } } })
    render(<AnswerBody answer={answer()} question={QUESTION} topic="trading" />)
    fireEvent.click(screen.getByRole('button', { name: 'Find more about France' }))
    await screen.findByText('Not queued: Admin is closed.')
    expect(screen.getByRole('button', { name: 'Find more about France' })).toBeTruthy()
  })

  it('queues any country typed by name, with no topic when none is known', async () => {
    const fetchMock = server({ '/api/admin/seeds': { status: 201, body: QUEUED } })
    render(<AnswerBody answer={answer()} question={QUESTION} topic={null} />)
    const submit = screen.getByRole('button', { name: 'Find more' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText(/A country missing/), { target: { value: '  Kenya ' } })
    fireEvent.click(submit)
    await screen.findByText('Queued — results arrive as the crawler fetches them.')
    const body = JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))
    expect(body.url_or_query).toBe(`${QUESTION} Kenya`)
    expect(body.topic).toBeNull()
  })

  it('builds the query without stray spaces', () => {
    expect(findMoreQuery('  a question?  ', ' Chile ')).toBe('a question? Chile')
  })
})

describe('fetching the answer', () => {
  it('asks the answer route with the question and filters, and files find-more under the leading topic', async () => {
    const fetchMock = server({
      '/api/explore/answer': { body: answer() },
      '/api/admin/seeds': { status: 201, body: QUEUED },
    })
    render(<AnswerView question={QUESTION} topics={[]} match="any" places={['DE']} />)
    await screen.findByRole('region', { name: 'Coverage by country' })
    const url = new URL(String(fetchMock.mock.calls[0]![0]), 'http://x')
    expect(url.pathname).toBe('/api/explore/answer')
    expect(url.searchParams.get('q')).toBe(QUESTION)
    expect(url.searchParams.getAll('place')).toEqual(['DE'])
    expect(url.searchParams.getAll('topic')).toEqual([])

    fireEvent.click(screen.getByRole('button', { name: 'Find more about France' }))
    await screen.findByText(/^Queued/)
    // No topic chosen by the reader: the one most matching sources carry.
    expect(JSON.parse(String(fetchMock.mock.calls[1]![1]?.body)).topic).toBe('trading')
  })

  it('names a failure rather than showing an empty page', async () => {
    server({ '/api/explore/answer': { status: 500, body: { detail: 'The database is down.' } } })
    render(<AnswerView question={QUESTION} topics={['a']} match="all" places={[]} />)
    expect(await screen.findByText('The database is down.')).toBeTruthy()
  })
})

describe('answer or passages', () => {
  it('guesses from the text when the reader has no remembered choice', () => {
    expect(looksLikeQuestion(QUESTION)).toBe(true)
    expect(looksLikeQuestion('how licences compare')).toBe(true)
    expect(looksLikeQuestion('street trading licence')).toBe(false)
    expect(looksLikeQuestion('licence?')).toBe(true)
    const empty = { getItem: () => null }
    expect(initialMode(QUESTION, empty)).toBe('answer')
    expect(initialMode('street trading licence', empty)).toBe('passages')
  })

  it('remembers the last choice over the guess, and survives storage that throws', () => {
    const store = new Map<string, string>()
    const storage = {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    }
    rememberMode('passages', storage)
    expect(initialMode(QUESTION, storage)).toBe('passages')
    rememberMode('answer', storage)
    expect(initialMode('street trading licence', storage)).toBe('answer')

    const broken = {
      getItem: () => {
        throw new Error('denied')
      },
      setItem: () => {
        throw new Error('denied')
      },
    }
    expect(() => rememberMode('answer', broken)).not.toThrow()
    expect(initialMode(QUESTION, broken)).toBe('answer')
    // Garbage in storage is ignored.
    expect(initialMode('words', { getItem: () => 'graph' })).toBe('passages')
  })

  it('switches with two tabs and reports the choice', () => {
    const onChange = vi.fn()
    render(<ModeSwitch mode="answer" onChange={onChange} />)
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(['Answer', 'Passages'])
    expect(tabs[0]!.getAttribute('aria-selected')).toBe('true')
    fireEvent.click(tabs[1]!)
    expect(onChange).toHaveBeenCalledWith('passages')
  })
})

describe('the rule the client shows is the server’s', () => {
  it('matches the thresholds in meridian_core/answer.py', () => {
    // The page shows `coverage_rule` from the response; this pins the fixture
    // above to the real rule so the tests do not assert against a fiction.
    // jsdom gives `import.meta.url` an http scheme, so the path comes from the
    // working directory vitest runs in (web/).
    const repo = join(process.cwd(), '..')
    const source = readFileSync(join(repo, 'packages/meridian_core/meridian_core/answer.py'), 'utf8')
    const min = Number(source.match(/^STRONG_MIN_PUBLISHERS = (\d+)/m)![1])
    expect(answer().strong_min_publishers).toBe(min)
    expect(source).toMatch(/PRIMARY_TIERS[^=]*= frozenset\(\{"government", "peer_reviewed"\}\)/)
  })
})
