/**
 * Since-last-visit (task P6-11, spec §12.5).
 *
 * The subject is the three-state distinction. A component that collapsed "never
 * been here" into "nothing arrived" would tell a first-time reader their corpus
 * is idle, and one that rendered nothing for zero would look like a panel that
 * failed to load.
 */
// @vitest-environment jsdom
import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SinceLastVisit } from '../src/explore/SinceLastVisit'
import { markVisited, openSession, readLastVisit } from '../src/lib/lastVisit'

function text(markup: string): string {
  return markup.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()
}

describe('three states, not two', () => {
  it('says nothing at all on a first visit', () => {
    // There is genuinely no previous moment to measure from, and claiming one
    // would be inventing history.
    expect(renderToStaticMarkup(<SinceLastVisit newSources={null} newChunks={null} />)).toBe('')
  })

  it('says so explicitly when nothing arrived', () => {
    // A silent panel here reads as a page that failed to load its delta.
    const rendered = text(
      renderToStaticMarkup(<SinceLastVisit newSources={0} newChunks={0} />),
    )
    expect(rendered).toBe('Nothing new since you were last here.')
  })

  it('reports the delta when there is one', () => {
    const rendered = text(
      renderToStaticMarkup(<SinceLastVisit newSources={12} newChunks={340} />),
    )
    expect(rendered).toContain('12 sources and 340 passages')
  })

  it('does not call one source "sources"', () => {
    const rendered = text(renderToStaticMarkup(<SinceLastVisit newSources={1} newChunks={1} />))
    expect(rendered).toContain('1 source and 1 passage')
  })
})

describe('the stamp', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })

  it('returns null on a first ever visit and records one', () => {
    expect(openSession()).toBeNull()
    expect(readLastVisit()).not.toBeNull()
  })

  it('returns the previous visit and advances in a single call', () => {
    // One call, because doing it in two places is how the stamp ends up
    // advanced before it was read — and the symptom is a delta that is always
    // zero, which looks exactly like a corpus where nothing happened.
    const first = new Date('2026-09-01T00:00:00Z')
    markVisited(first)

    const previous = openSession(new Date('2026-09-15T00:00:00Z'))

    expect(previous).toBe(first.toISOString())
    expect(readLastVisit()).toBe('2026-09-15T00:00:00.000Z')
  })

  it('ignores a stored value that is not a timestamp', () => {
    // It would otherwise be sent to the API as a query parameter, so it is
    // checked here rather than coming back as a 422 the reader cannot act on.
    window.localStorage.setItem('meridian.lastVisit', 'yesterday')
    expect(readLastVisit()).toBeNull()
  })

  it('survives storage that throws rather than returning null', () => {
    // A private window, or a browser blocking site data, throws on the property
    // access itself — and this runs during the first render.
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })

    expect(() => readLastVisit()).not.toThrow()
    expect(readLastVisit()).toBeNull()
    vi.restoreAllMocks()
  })

  it('survives storage that refuses to be written', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError')
    })

    expect(() => markVisited()).not.toThrow()
    vi.restoreAllMocks()
  })
})
