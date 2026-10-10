/**
 * The source detail page (tasks P6-14, P6-15).
 *
 * The first screen where the corpus reads like documents rather than results,
 * so the tests are about what a reader arriving at a citation needs: what this
 * is, how it was read, what it said, and a way to take it with them.
 */
// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../src/lib/api', async () => {
  const actual = await vi.importActual<typeof import('../src/lib/api')>('../src/lib/api')
  return { ...actual, getSource: vi.fn(), getSourceChunks: vi.fn(), getSourceFigures: vi.fn() }
})

import { ApiError, getSource, getSourceChunks, getSourceFigures } from '../src/lib/api'
import { SourcePage } from '../src/explore/SourcePage'

const SOURCE = {
  source_id: 7,
  url: 'https://example.test/report.pdf',
  archive_url: null,
  title: 'Annual transport report',
  author: null,
  publisher: 'Example Authority',
  publication_date: '2026-04-02',
  doi: null,
  accessed_at: '2026-09-01T00:00:00Z',
  checksum: 'sha256:x',
  etag: null,
  last_modified: null,
  source_tier: 'government' as const,
  retention_tier: 'primary' as const,
  raw_file_path: 'example.test/ab/cd.pdf',
  raw_root: null,
  language: 'en',
  extractor: 'pdftotext',
  text_available: true,
  ocr_applied: false,
  ocr_tier: 'none' as const,
  ocr_confidence: null,
  topic_labels: ['walkability'],
  crawled_for: ['walkability'],
  duplicate_of: null,
  duplicate_reason: null,
  doc_kind: null,
  topics_examined_at: '2026-09-02T00:00:00Z',
  topic_basis: 'v1:test',
  topic_scores: { walkability: 0.6 },
  topic_sample_best: null,
  places: null,
  places_examined_at: null,
  place_basis: null,
  place_evidence: null,
  trust_state: 'cleared' as const,
  acronyms_harvested_at: null,
  page_unit: 'page' as const,
  extra: null,
  created_at: '2026-09-01T00:00:00Z',
}

// Testing Library's auto-cleanup needs a globals setup this project does not
// have, so it is called explicitly. Without it each render stacks in the same
// document and `findByText` reports "multiple elements" — which reads as a
// duplicate-render bug in the component rather than a test-harness one.
afterEach(cleanup)

beforeEach(() => {
  vi.mocked(getSource).mockResolvedValue(SOURCE)
  vi.mocked(getSourceChunks).mockResolvedValue({
    source_id: 7,
    chunks: [
      {
        chunk_id: 100,
        source_id: 7,
        text: 'A passage about modal share.',
        page_or_offset: 4,
        chunk_index: 0,
        novelty_checked_at: null,
        nearest_similarity: null,
        duplicate_of: null,
        superseded_at: null,
        embedding_view: 1,
        created_at: '2026-09-01T00:00:00Z',
      },
    ],
    limit: 50,
    offset: 0,
    has_more: false,
  })
  vi.mocked(getSourceFigures).mockResolvedValue({
    source_id: 7,
    figures: [],
    raw_available: false,
    furniture_hidden: 0,
  })
})

describe('what a reader arriving at a citation needs', () => {
  it('says what this is and how it was read', async () => {
    render(<SourcePage sourceId={7} />)

    expect(await screen.findByText('Annual transport report')).toBeTruthy()
    // `P1-44`. A source whose extractor failed and one that simply had no text
    // are indistinguishable without this; it is kept, in the record details (`B-156`).
    const details = screen.getByText(/record details/).closest('details')!
    expect(details.textContent).toContain('pdftotext')
    expect(details.textContent).toContain('7')
    expect(details.open).toBe(false)
    expect(screen.getByText(/Government/i)).toBeTruthy()
  })

  it('keeps the corpus’s bookkeeping off the reader’s page (B-156)', async () => {
    render(<SourcePage sourceId={7} />)
    await screen.findByText(/A passage about modal share/)

    const article = document.querySelector('article')!
    const outsideDetails = Array.from(article.childNodes)
      .map((node) => (node as HTMLElement).textContent ?? '')
      .join(' ')
      .replace(screen.getByText(/record details/).closest('details')!.textContent ?? '', '')
    expect(outsideDetails).not.toMatch(/source 7|read by|chunk 100/)
    // The breadcrumb names the site, and a paginated source's passage says its page.
    expect(screen.getByRole('navigation', { name: 'Breadcrumb' }).textContent).toContain('example.test')
    expect(screen.getByText('page 4')).toBeTruthy()
    expect(screen.getByText('page 4').getAttribute('title')).toBe('chunk 100 · at 4')
  })

  it('names no position for a page whose offset is bookkeeping', async () => {
    vi.mocked(getSource).mockResolvedValue({ ...SOURCE, page_unit: 'offset' })
    render(<SourcePage sourceId={7} />)
    await screen.findByText(/A passage about modal share/)
    expect(screen.queryByText(/page 4|at 4/)).toBeNull()
  })

  it('shows the passages the corpus actually holds', async () => {
    render(<SourcePage sourceId={7} />)

    expect(await screen.findByText(/A passage about modal share/)).toBeTruthy()
  })

  it('offers both exports', async () => {
    render(<SourcePage sourceId={7} />)

    const bibtex = await screen.findByText('BibTeX')
    expect(bibtex.getAttribute('href')).toContain('/api/explore/export/bibtex?source_id=7')
    expect(bibtex.getAttribute('download')).toBe('meridian-7.bib')
  })
})

describe('the states that are not success', () => {
  it('names the cause when the source will not load', async () => {
    // §4: an error says what broke. "Something went wrong" tells a reader
    // nothing they can act on, and a stale citation is the usual cause.
    vi.mocked(getSource).mockRejectedValue(new ApiError(404, 'No source 7.'))

    render(<SourcePage sourceId={7} />)

    expect(await screen.findByText('No source 7.')).toBeTruthy()
    expect(screen.getByText(/Back to search/)).toBeTruthy()
  })

  it('treats a source with no text as a finding, not a failure', async () => {
    // §6.5 makes metadata-only a valid resting state — a scan, or a paywall.
    // It is still citable and still counts toward coverage.
    vi.mocked(getSourceChunks).mockResolvedValue({
      source_id: 7,
      chunks: [],
      limit: 50,
      offset: 0,
      has_more: false,
    })

    render(<SourcePage sourceId={7} />)

    await waitFor(() => expect(screen.getByText(/No text was extracted/)).toBeTruthy())
    expect(screen.getByText(/still citable/)).toBeTruthy()
  })
})

describe('opening on the passage that was clicked (B-178)', () => {
  function chunk(id: number, index: number) {
    return {
      chunk_id: id,
      source_id: 7,
      text: `Passage at index ${index}.`,
      page_or_offset: index,
      chunk_index: index,
      novelty_checked_at: null,
      nearest_similarity: null,
      duplicate_of: null,
      superseded_at: null,
      embedding_view: 1,
      created_at: '2026-09-01T00:00:00Z',
    }
  }
  function page(start: number, count: number, more: boolean) {
    return {
      source_id: 7,
      chunks: Array.from({ length: count }, (_, i) => chunk(1000 + start + i, start + i)),
      limit: 20,
      offset: start,
      has_more: more,
    }
  }

  afterEach(() => window.history.pushState({}, '', '/'))

  it('asks for the window around it, marks it, and reads earlier and later from its edges', async () => {
    window.history.pushState({}, '', '/sources/7?passage=1043')
    vi.mocked(getSourceChunks)
      .mockResolvedValueOnce(page(40, 20, true))
      .mockResolvedValueOnce(page(20, 20, false))
      .mockResolvedValueOnce(page(60, 5, false))
    render(<SourcePage sourceId={7} />)

    const marked = await screen.findByText('Passage at index 43.')
    expect(vi.mocked(getSourceChunks).mock.calls[0]![1]).toEqual({ around: 1043, limit: 20 })
    expect(marked.closest('li')!.getAttribute('aria-current')).toBe('location')
    expect(screen.getByText('41–60, more after')).toBeTruthy()

    screen.getByRole('button', { name: '↑ Earlier passages' }).click()
    await screen.findByText('Passage at index 20.')
    expect(vi.mocked(getSourceChunks).mock.calls[1]![1]).toEqual({ offset: 20, limit: 20 })

    screen.getByRole('button', { name: 'Later passages ↓' }).click()
    await screen.findByText('Passage at index 64.')
    expect(vi.mocked(getSourceChunks).mock.calls[2]![1]).toEqual({ offset: 60, limit: 20 })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Later passages ↓' })).toBeNull())
    expect(screen.getByText('21–65')).toBeTruthy()
  })

  it('says so when the passage is no longer in the source, rather than marking nothing silently', async () => {
    window.history.pushState({}, '', '/sources/7?passage=99999')
    vi.mocked(getSourceChunks).mockResolvedValueOnce(page(0, 3, false))
    render(<SourcePage sourceId={7} />)
    expect(await screen.findByText(/no longer in the source's current text/)).toBeTruthy()
    expect(document.querySelector('[aria-current="location"]')).toBeNull()
  })

  it('opens at the top without a passage, and offers later passages', async () => {
    vi.mocked(getSourceChunks).mockResolvedValueOnce(page(0, 20, true))
    render(<SourcePage sourceId={7} />)
    await screen.findByText('Passage at index 0.')
    expect(vi.mocked(getSourceChunks).mock.calls[0]![1]).toEqual({ limit: 20 })
    expect(screen.queryByRole('button', { name: '↑ Earlier passages' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Later passages ↓' })).toBeTruthy()
  })
})
