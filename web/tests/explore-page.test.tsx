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

import { ResultList } from '../src/explore/ResultList'
import { RetrievalNotice, SearchOutcome } from '../src/explore/ExplorePage'
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
    duplicate_of: null,
    score: 0.016,
    lexical_rank: 1,
    vector_rank: null,
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
    const rendered = text(
      renderToStaticMarkup(<ResultList hits={[hit({ publication_date: null })]} />),
    )
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
    const rendered = text(
      renderToStaticMarkup(<SearchOutcome asked="walkability" results={response({ hits: [] })} />),
    )

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

  it('draws the notice at attention weight only when empty', () => {
    const withHits = renderToStaticMarkup(<RetrievalNotice reason="r" empty={false} />)
    const withNone = renderToStaticMarkup(<RetrievalNotice reason="r" empty />)

    expect(withHits).not.toContain('border-accent-attention')
    expect(withNone).toContain('border-accent-attention')
  })

  it('names the query it found nothing for', () => {
    const rendered = text(
      renderToStaticMarkup(<SearchOutcome asked="walkability" results={response({ hits: [] })} />),
    )
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
      renderToStaticMarkup(
        <ResultList hits={[hit({ page_unit: 'offset', media_type: 'text/html' })]} />,
      ),
    )
    expect(rendered).toContain('offset 1')
  })

  it('hedges only when the source never recorded a media type', () => {
    // The fallback has to stay, and has to stay rare. Picking one name for an
    // unknown source mislabels a citation someone will open; hedging on every
    // source teaches readers the label carries no information.
    const rendered = text(
      renderToStaticMarkup(<ResultList hits={[hit({ page_unit: null, media_type: null })]} />),
    )
    expect(rendered).toContain('page/offset 1')
  })
})
