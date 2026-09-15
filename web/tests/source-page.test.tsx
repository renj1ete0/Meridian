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
        created_at: '2026-09-01T00:00:00Z',
      },
    ],
    limit: 50,
    offset: 0,
    has_more: false,
  })
  vi.mocked(getSourceFigures).mockResolvedValue({ source_id: 7, figures: [], raw_available: false })
})

describe('what a reader arriving at a citation needs', () => {
  it('says what this is and how it was read', async () => {
    render(<SourcePage sourceId={7} />)

    expect(await screen.findByText('Annual transport report')).toBeTruthy()
    // `P1-44`. A source whose extractor failed and one that simply had no text
    // are indistinguishable without this.
    expect(screen.getByText(/read by pdftotext/)).toBeTruthy()
    expect(screen.getByText(/Government/i)).toBeTruthy()
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
