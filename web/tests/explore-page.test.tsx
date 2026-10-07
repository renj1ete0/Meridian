/**
 * Explore, wired to the read surface (task P2-08, spec §12.5).
 *
 * Two properties carry this file, and both are about what the screen says when
 * it has nothing to show.
 *
 * **Provenance is on every hit.** §2 principle 3: nothing is assertable without
 * a citation you can follow back to a file. A result list that showed text and
 * hid the source would make this a RAG chatbot, which the README says it is not.
 *
 * **A degraded search that found nothing must say so.** `P2-07` runs the
 * lexical arm only. A reader who sees an empty page and is not told that
 * meaning-based matching was off concludes the corpus lacks the topic — a false
 * claim the search is not entitled to make, and the exact confusion §12.5 asks
 * the interface to prevent.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { CLAMP_OVER, ResultList, passageOnlyTopics } from '../src/explore/ResultList'
import { RetrievalNotice, SearchOutcome, entryState, summaryLine } from '../src/explore/ExplorePage'
import type { CorpusStats } from '../src/lib/api'
import type { SearchHit, SearchResponse } from '../src/lib/api'

const REPO = join(fileURLToPath(new URL('..', import.meta.url)), '..')

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    chunk_id: 590,
    source_id: 1059,
    text: 'A sustainable land transport sector.',
    page_or_offset: 1,
    chunk_index: 0,
    url: 'https://example.test/report.pdf',
    title: 'Annual Report 2026',
    page_unit: 'page',
    media_type: 'application/pdf',
    source_tier: 'government',
    publication_date: '2025-12-03',
    language: 'en',
    topic_labels: ['walkability'],
    passage_topics: null,
    duplicate_of: null,
    score: 0.016,
    lexical_rank: 1,
    vector_rank: null,
    age_days: null,
    decay: 1,
    score_before_decay: 0,
    ...over,
  }
}

function response(over: Partial<SearchResponse> = {}): SearchResponse {
  return {
    hits: [hit()],
    arms: ['lexical'],
    degraded: true,
    degraded_reason: 'This API has no embedder, so only the lexical arm ran.',
    limit: 20,
    offset: 0,
    has_more: false,
    candidate_pool: 100,
    lexical_candidates: 9,
    vector_candidates: 0,
    ...over,
  }
}

// --------------------------------------------------------------------------
// Provenance
// --------------------------------------------------------------------------

describe('every result carries its citation', () => {
  it('shows the source, tier, date and position', () => {
    const rendered = text(renderToStaticMarkup(<ResultList hits={[hit()]} />))

    expect(rendered).toContain('Annual Report 2026')
    expect(rendered).toContain('example.test')
    expect(rendered).toContain('Government')
    expect(rendered).toContain('2025-12-03')
    expect(rendered).toContain('1')
  })

  it('links to the source', () => {
    const markup = renderToStaticMarkup(<ResultList hits={[hit()]} />)
    expect(markup).toContain('href="https://example.test/report.pdf"')
  })

  it('says "no date" rather than leaving a blank', () => {
    // A source that published undated and one whose date was never extracted
    // look identical from an empty space, and §9 makes staleness a first-class
    // signal — a reader weighing evidence needs to tell those apart.
    const rendered = text(renderToStaticMarkup(<ResultList hits={[hit({ publication_date: null })]} />))
    expect(rendered).toContain('no date')
  })

  it('falls back to the host when a source has no title', () => {
    const rendered = text(renderToStaticMarkup(<ResultList hits={[hit({ title: null })]} />))
    expect(rendered).toContain('example.test')
  })

  it('shows a URL it cannot parse rather than hiding it', () => {
    // A URL the crawler stored and this cannot parse is evidence about the
    // corpus. Swallowing it turns a data problem into a rendering mystery.
    const rendered = text(renderToStaticMarkup(<ResultList hits={[hit({ url: 'not a url' })]} />))
    expect(rendered).toContain('not a url')
  })

  it('shows the topics the source was filed under', () => {
    // A filtered result set a reader cannot check is a filter they have to
    // trust. §12.5 puts topic among the filters, so it belongs on the row.
    const rendered = text(
      renderToStaticMarkup(<ResultList hits={[hit({ topic_labels: ['walkability', 'transit'] })]} />),
    )

    expect(rendered).toContain('walkability')
    expect(rendered).toContain('transit')
  })

  it('shows no topic chip when the source was never examined', () => {
    // Null and [] are different facts — "nothing examined this" and "examined,
    // matched nothing" — and neither is a topic. Inventing a chip for either
    // would put a label on a document that has none.
    for (const labels of [null, []]) {
      const markup = renderToStaticMarkup(<ResultList hits={[hit({ topic_labels: labels })]} />)
      expect(text(markup)).not.toContain('walkability')
    }
  })

  it('shows a passage topic its document does not carry, and only that one', () => {
    // `P2-24`: a topic filter matches a passage on its own labels, so a hit
    // from a document filed under something else needs a visible reason to be
    // in the filtered list. A topic the source already shows is not repeated.
    const rendered = text(
      renderToStaticMarkup(
        <ResultList hits={[hit({ topic_labels: ['walkability'], passage_topics: ['transit', 'walkability'] })]} />,
      ),
    )
    expect(rendered).toContain('passage · transit')
    expect(rendered).not.toContain('passage · walkability')
  })

  it('shows no passage chip for an unexamined or off-topic passage', () => {
    for (const passage of [null, []]) {
      const markup = renderToStaticMarkup(<ResultList hits={[hit({ passage_topics: passage })]} />)
      expect(text(markup)).not.toContain('passage ·')
    }
  })

  it('keeps the passage order and drops the source topics', () => {
    expect(passageOnlyTopics({ topic_labels: null, passage_topics: ['b', 'a'] })).toEqual(['b', 'a'])
    expect(passageOnlyTopics({ topic_labels: ['a'], passage_topics: ['b', 'a'] })).toEqual(['b'])
    expect(passageOnlyTopics({ topic_labels: ['a'], passage_topics: null })).toEqual([])
  })

  it('marks a near-duplicate as one', () => {
    // §12.5: a chunk filtered as a near-duplicate and one never crawled are
    // indistinguishable from a result set, and only one is worth investigating.
    const rendered = text(renderToStaticMarkup(<ResultList hits={[hit({ duplicate_of: 12 })]} />))
    expect(rendered).toContain('duplicate of 12')
  })

  it('keeps the chunk verbatim, line breaks included', () => {
    // Chunks are verbatim slices (`P2-02`). Collapsing their breaks reflows
    // tables and page furniture into prose that reads as though the source
    // wrote it that way.
    const markup = renderToStaticMarkup(<ResultList hits={[hit({ text: 'one\ntwo' })]} />)
    expect(markup).toContain('whitespace-pre-wrap')
    expect(markup).toContain('one\ntwo')
  })
})

// --------------------------------------------------------------------------
// The degraded search — §12.5's absence
// --------------------------------------------------------------------------

describe('a degraded search says what it did not do', () => {
  it('reports the reason when it still found something', () => {
    const rendered = text(renderToStaticMarkup(<SearchOutcome asked="transport" results={response()} />))
    expect(rendered).toContain('no embedder')
  })

  it('states the consequence when it found nothing', () => {
    // The case that matters. Silence here is a claim about the corpus that the
    // search is not entitled to make.
    const rendered = text(renderToStaticMarkup(<SearchOutcome asked="walkability" results={response({ hits: [] })} />))

    expect(rendered).toContain('different wording were not searched')
    expect(rendered).toContain('not evidence that the corpus lacks the subject')
  })

  it('does not claim that when retrieval was complete', () => {
    // The converse, which is what makes the test above mean anything: if the
    // caveat appeared on every empty result it would say nothing about
    // degradation and readers would learn to skip it.
    const rendered = text(
      renderToStaticMarkup(
        <SearchOutcome
          asked="walkability"
          results={response({ hits: [], degraded: false, degraded_reason: null, arms: ['lexical', 'vector'] })}
        />,
      ),
    )

    expect(rendered).toContain('Nothing matched')
    expect(rendered).not.toContain('different wording')
  })

  it('draws the notice as a bordered notice only when empty', () => {
    const withHits = renderToStaticMarkup(<RetrievalNotice reason="r" empty={false} />)
    const withNone = renderToStaticMarkup(<RetrievalNotice reason="r" empty />)

    expect(withHits).toContain('data-weight="caveat"')
    expect(withNone).toContain('data-weight="notice"')
  })

  it('does not paint the notice brass', () => {
    // §6: the brass tint never appears without the dagger, and brass means
    // contested, stale or flagged. A search that ran one arm is none of those,
    // and a brass box would read as a verdict on the corpus.
    for (const empty of [true, false]) {
      expect(renderToStaticMarkup(<RetrievalNotice reason="r" empty={empty} />)).not.toContain('accent-attention')
    }
  })

  it('names the query it found nothing for', () => {
    const rendered = text(renderToStaticMarkup(<SearchOutcome asked="walkability" results={response({ hits: [] })} />))
    expect(rendered).toContain('walkability')
  })
})

// --------------------------------------------------------------------------
// §4 — the voice
// --------------------------------------------------------------------------

describe('the copy holds the voice guide', () => {
  function bannedWords(): string[] {
    const design = readFileSync(join(REPO, 'docs/design/design-system.md'), 'utf8')
    const line = /\*\*Words this system does not use:\*\*([^\n]*(?:\n[^\n*]*)?)/.exec(design)
    expect(line, '§4’s banned-word list is no longer written the way this test reads it').toBeTruthy()
    return line![1]!
      .split(',')
      .map((word) => word.replace(/[.\n]/g, '').trim().toLowerCase())
      .filter((word) => word.length > 0 && !word.includes(' '))
  }

  const rendered = [
    text(renderToStaticMarkup(<SearchOutcome asked="x" results={response()} />)),
    text(renderToStaticMarkup(<SearchOutcome asked="x" results={response({ hits: [] })} />)),
    text(renderToStaticMarkup(<ResultList hits={[hit()]} />)),
  ]

  it('parses a real list out of the design system', () => {
    const words = bannedWords()
    expect(words.length).toBeGreaterThan(5)
    expect(words).toContain('insights')
  })

  it('uses none of the words §4 forbids', () => {
    const words = bannedWords()
    const offences: string[] = []
    for (const copy of rendered) {
      for (const word of words) {
        if (new RegExp(`\\b${word}\\b`, 'i').test(copy)) offences.push(`${word}: ${copy}`)
      }
    }
    expect(offences).toEqual([])
  })

  it('contains no exclamation marks', () => {
    for (const copy of rendered) expect(copy).not.toContain('!')
  })
})

// --------------------------------------------------------------------------
// A citation says what its number counts (task P2-18, spec §5.3)
// --------------------------------------------------------------------------

describe('the page number is labelled, not hedged', () => {
  it('says "page" for a paginated source', () => {
    const rendered = text(renderToStaticMarkup(<ResultList hits={[hit({ page_unit: 'page' })]} />))
    expect(rendered).toContain('page 1')
  })

  it('says "offset" for one that is not', () => {
    const rendered = text(
      renderToStaticMarkup(<ResultList hits={[hit({ page_unit: 'offset', media_type: 'text/html' })]} />),
    )
    expect(rendered).toContain('offset 1')
  })

  it('hedges only when the source never recorded a media type', () => {
    // The fallback has to stay, and has to stay rare. Picking one name for an
    // unknown source mislabels a citation someone will open; hedging on every
    // source teaches readers the label carries no information.
    const rendered = text(renderToStaticMarkup(<ResultList hits={[hit({ page_unit: null, media_type: null })]} />))
    expect(rendered).toContain('page/offset 1')
  })
})

// --------------------------------------------------------------------------
// P6-27 — the results summary, long passages, the entry cards' honest states
// --------------------------------------------------------------------------

describe('the results summary', () => {
  it('counts per arm, so a vector-only result does not read "20 of 0"', () => {
    const line = summaryLine(response({ arms: ['lexical', 'vector'], lexical_candidates: 0, vector_candidates: 100 }))
    expect(line).toBe('1 passage shown · 0 matched the words · 100+ near in meaning')
  })

  it('says "at least" for an arm that stopped at the candidate pool', () => {
    // Both arms stop at the pool; "100 matched the words" read as an exact count.
    const capped = response({ arms: ['lexical', 'vector'], lexical_candidates: 100, vector_candidates: 99 })
    expect(summaryLine(capped)).toBe('1 passage shown · 100+ matched the words · 99 near in meaning')
    expect(summaryLine(response({ candidate_pool: 2000, lexical_candidates: 1500 }))).toBe(
      '1 passage shown · 1,500 matched the words',
    )
  })

  it('names only the arms that ran', () => {
    expect(summaryLine(response())).toBe('1 passage shown · 9 matched the words')
  })

  it('says how it was found in words, not in the machinery', () => {
    const line = summaryLine(response({ arms: ['lexical', 'vector'], lexical_candidates: 5, vector_candidates: 7 }))
    expect(line).not.toMatch(/lexical|vector/)
  })
})

describe('a long passage', () => {
  it('is clamped by CSS, never truncated in the markup', () => {
    // Find-in-page, copying and a screen reader all need the verbatim chunk.
    const long = 'x'.repeat(CLAMP_OVER + 1) + 'TAIL'
    const markup = renderToStaticMarkup(<ResultList hits={[hit({ text: long })]} />)
    expect(markup).toContain('TAIL')
    expect(markup).toContain('line-clamp-[8]')
    expect(text(markup)).toContain('whole passage')
  })

  it('offers no toggle for a short one', () => {
    const markup = renderToStaticMarkup(<ResultList hits={[hit()]} />)
    expect(markup).not.toContain('line-clamp')
    expect(text(markup)).not.toContain('whole passage')
  })
})

describe('what the entry cards claim', () => {
  function stats(over: Partial<CorpusStats> = {}): CorpusStats {
    return {
      as_of: '2026-09-23T00:00:00Z',
      sources: 10,
      chunks: 100,
      embedded_chunks: 100,
      duplicate_chunks: 0,
      searchable_chunks: 100,
      entities: 5,
      edges: 4,
      contested_edges: 0,
      new_sources: null,
      new_chunks: null,
      topics: [],
      sources_without_topics: 0,
      ...over,
    }
  }

  it('opens coverage through Gaps rather than calling it unbuilt, and invents no count', () => {
    const { unavailable, descriptions } = entryState(stats())
    expect(unavailable.coverage).toBeUndefined()
    expect(descriptions.coverage).toBeUndefined()
  })

  it('says there is nothing to disagree about when there are no edges', () => {
    expect(entryState(stats({ edges: 0 })).unavailable.contested).toMatch(/No stated links yet/)
  })

  it('states a real zero when edges exist and none is contested', () => {
    const { unavailable, descriptions } = entryState(stats({ edges: 4, contested_edges: 0 }))
    expect(unavailable.contested).toBeUndefined()
    expect(descriptions.contested).toMatch(/^No two sources disagree across 4 stated links/)
  })

  it('carries the real count when there is one, in the right number', () => {
    expect(entryState(stats({ contested_edges: 1 })).descriptions.contested).toMatch(/^1 claim sources/)
    expect(entryState(stats({ contested_edges: 214 })).descriptions.contested).toMatch(/^214 claims sources/)
  })

  it('makes no claim about contested pairs before the counts arrive', () => {
    const { unavailable, descriptions } = entryState(null)
    expect(unavailable.contested).toBeUndefined()
    expect(descriptions.contested).toBeUndefined()
  })
})
