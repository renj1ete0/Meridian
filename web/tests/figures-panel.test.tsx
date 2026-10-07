/**
 * The figures panel (task P6-14, spec §6.6, §12.5).
 *
 * §6.6 puts the value in the caption — "often the most information-dense
 * sentence about the figure" — so that is what this panel is for, and the tests
 * are about the two links beside it being distinguishable and never broken.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FiguresPanel, furnitureLine } from '../src/explore/FiguresPanel'
import type { FigureRef } from '../src/lib/api'

function figure(over: Partial<FigureRef> = {}): FigureRef {
  return {
    figure_id: 1,
    source_id: 7,
    caption: 'Figure 1: modal share by corridor',
    alt_text: null,
    image_url: 'https://example.test/charts/modal.png',
    page: 4,
    source_title: 'Annual report',
    source_url: 'https://example.test/report',
    raw_url: '/api/explore/sources/7/raw#page=4',
    reader_caption: 'Figure 1: modal share by corridor',
    ...over,
  }
}

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

describe('the caption is the content', () => {
  it('shows it', () => {
    const rendered = text(renderToStaticMarkup(<FiguresPanel figures={[figure()]} rawAvailable />))
    expect(rendered).toContain('Figure 1: modal share by corridor')
  })

  it('falls back to alt text when there is no caption', () => {
    const rendered = text(
      renderToStaticMarkup(
        <FiguresPanel
          figures={[figure({ caption: null, alt_text: 'A bar chart', reader_caption: 'A bar chart' })]}
          rawAvailable
        />,
      ),
    )
    expect(rendered).toContain('A bar chart')
  })

  it('shows alt text alongside a caption rather than instead of it', () => {
    // They are different evidence: the caption is what the author wrote about
    // the figure, the alt text is what they wrote for someone who cannot see it.
    const rendered = text(
      renderToStaticMarkup(<FiguresPanel figures={[figure({ alt_text: 'A bar chart' })]} rawAvailable />),
    )
    expect(rendered).toContain('modal share by corridor')
    expect(rendered).toContain('A bar chart')
  })
})

describe('a file name is not a caption (B-156)', () => {
  it('shows an untitled image rather than the file name', () => {
    const name = 'olivia hutcherson 0 Wjcr3j8CU unsplash'
    const rendered = text(
      renderToStaticMarkup(
        <FiguresPanel figures={[figure({ caption: null, alt_text: name, reader_caption: null })]} rawAvailable />,
      ),
    )
    expect(rendered).toContain('Untitled image')
    expect(rendered).not.toContain('Wjcr3j8CU')
  })

  it('says how many logos and icons it left out, in words', () => {
    expect(furnitureLine(0)).toBeNull()
    expect(furnitureLine(1)).toBe('One logo or icon from the page is not shown.')
    expect(furnitureLine(1204)).toBe('1,204 logos and icons from the page are not shown.')
    const withFigures = text(
      renderToStaticMarkup(<FiguresPanel figures={[figure()]} rawAvailable furnitureHidden={3} />),
    )
    expect(withFigures).toContain('3 logos and icons from the page are not shown.')
    const without = text(renderToStaticMarkup(<FiguresPanel figures={[]} rawAvailable furnitureHidden={3} />))
    expect(without).toContain('No figures were extracted')
    expect(without).toContain('3 logos and icons')
  })
})

describe('the two links are not the same link', () => {
  it('points into the stored copy at the figure’s page', () => {
    // §5.4 keeps raw files because link rot is the binding reason: government
    // URLs reorganise constantly, and a local copy is what keeps a citation
    // checkable years later. The `#page=` fragment is what makes it land on the
    // figure rather than on page one.
    const markup = renderToStaticMarkup(<FiguresPanel figures={[figure()]} rawAvailable />)

    expect(markup).toContain('href="/api/explore/sources/7/raw#page=4"')
  })

  it('also offers the publisher’s own image', () => {
    const markup = renderToStaticMarkup(<FiguresPanel figures={[figure()]} rawAvailable />)

    expect(markup).toContain('href="https://example.test/charts/modal.png"')
  })

  it('shows no stored link when this deployment serves no raw files', () => {
    // A caption with a dead link is worse than a caption alone: the reader
    // spends a click finding out. Absent, with a line saying why.
    const markup = renderToStaticMarkup(<FiguresPanel figures={[figure({ raw_url: null })]} rawAvailable={false} />)

    expect(markup).not.toContain('/raw#page=')
    expect(text(markup)).toContain('does not serve stored files')
  })

  it('renders a figure that has no links at all', () => {
    // A PDF caption has no image URL — the picture is not addressable — and
    // without raw serving it has no stored link either. The caption is still
    // the evidence.
    const rendered = text(
      renderToStaticMarkup(
        <FiguresPanel figures={[figure({ image_url: null, raw_url: null })]} rawAvailable={false} />,
      ),
    )

    expect(rendered).toContain('modal share by corridor')
  })
})

describe('absence is stated', () => {
  it('says a source has no figures rather than rendering nothing', () => {
    // §12.5's first principle. "This document has no figures" and "figures were
    // never extracted from it" are different facts, and a blank panel says
    // neither.
    const rendered = text(renderToStaticMarkup(<FiguresPanel figures={[]} rawAvailable />))

    expect(rendered).toContain('No figures were extracted')
  })

  it('counts what it is showing', () => {
    const rendered = text(
      renderToStaticMarkup(<FiguresPanel figures={[figure(), figure({ figure_id: 2 })]} rawAvailable />),
    )
    expect(rendered).toContain('Figures (2)')
  })
})
