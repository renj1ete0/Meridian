// @vitest-environment jsdom
/**
 * A passage as a citation (`B-179`) and the way back to the results (`lastFind`).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

import { passageCitation, QUOTE_MAX, type CitedSource } from '../src/lib/cite'
import { lastFind, rememberFind } from '../src/lib/lastFind'

afterEach(() => {
  vi.unstubAllGlobals()
  window.sessionStorage.clear()
})

const PDF: CitedSource = {
  source_id: 7,
  title: 'Annual transport report',
  url: 'https://example.test/report.pdf',
  publisher: 'Example Authority',
  publication_date: '2026-04-02',
  page_unit: 'page',
}

describe('the citation', () => {
  it('quotes the words, names the source and page, and links back', () => {
    const text = passageCitation(
      PDF,
      { chunk_id: 100, text: 'Speeds fell by a third.', page_or_offset: 24 },
      'https://m.example',
    )
    expect(text.split('\n')).toEqual([
      '“Speeds fell by a third.”',
      '— Annual transport report, Example Authority, 2026-04-02, p. 24. https://example.test/report.pdf',
      'Passage in Meridian: https://m.example/sources/7?passage=100',
    ])
  })

  it('never gives a character offset as a page, and says n.d. for no date', () => {
    const web = { ...PDF, page_unit: 'offset' as const, publication_date: null, publisher: null }
    const text = passageCitation(web, { chunk_id: 1, text: 'x', page_or_offset: 30223 }, '')
    expect(text).not.toContain('30223')
    expect(text).toContain('Annual transport report, n.d. https://')
  })

  it('cuts a long passage at a word, as a quote rather than a reprint', () => {
    const long = 'word '.repeat(200)
    const quoted = passageCitation(PDF, { chunk_id: 1, text: long, page_or_offset: 1 }, '').split('\n')[0]!
    expect(quoted.length).toBeLessThanOrEqual(QUOTE_MAX + 4)
    expect(quoted.endsWith('word …”')).toBe(true)
  })

  it('quotes what the reader saw, without the markup the page was stored in', () => {
    const text = passageCitation(
      PDF,
      { chunk_id: 1, text: 'See **the [report](https://x.test)**.', page_or_offset: 1 },
      '',
    )
    expect(text.split('\n')[0]).not.toContain('**')
    expect(text.split('\n')[0]).not.toContain('https://x.test')
  })
})

describe('the way back to the results', () => {
  it('keeps the last search with words, and nothing else', () => {
    expect(lastFind()).toBeNull()
    rememberFind('/?q=some+words&place=JP')
    expect(lastFind()).toEqual({ href: '/?q=some+words&place=JP', q: 'some words' })
    rememberFind('/?topic=a')
    expect(lastFind()).toBeNull()
    rememberFind('/map')
    expect(lastFind()).toBeNull()
  })

  it('survives storage that throws, as a private window does', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    })
    expect(() => rememberFind('/?q=x')).not.toThrow()
    expect(lastFind()).toBeNull()
  })
})

describe('an untitled source (B-206)', () => {
  it('is named by its publisher, or its host, and its address is written once', () => {
    const untitled = { ...PDF, title: null, url: 'https://www.example.gov/a/long/report.pdf' }
    const text = passageCitation(untitled, { chunk_id: 1, text: 'x', page_or_offset: 2 }, '')
    expect(text.split('\n')[1]).toBe('— Example Authority, 2026-04-02, p. 2. https://www.example.gov/a/long/report.pdf')
    const bare = passageCitation({ ...untitled, publisher: null }, { chunk_id: 1, text: 'x', page_or_offset: 2 }, '')
    expect(bare.split('\n')[1]).toBe('— example.gov, 2026-04-02, p. 2. https://www.example.gov/a/long/report.pdf')
  })
})
