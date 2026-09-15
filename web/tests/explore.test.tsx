/**
 * Explore landing components — docs/design/design-system.md §8, spec §12.5.
 *
 * Components with props, not a page. `/api/explore/*` does not exist yet, so
 * none of these fetch anything and none of them have a default that invents a
 * number — which is the substance of half this file: the difference between "we
 * do not know" and "the answer is zero" has to survive into the markup, because
 * a reader cannot recover it afterwards.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { CorpusCounts, UNKNOWN, type CorpusFigures } from '../src/explore/CorpusCounts'
import { ENTRY_POINTS, EntryPoints } from '../src/explore/EntryPoints'
import { FILTER_NOTE, MODE_MARKER, SearchField } from '../src/explore/SearchField'
import { WhereYouWere } from '../src/explore/WhereYouWere'
import { DAGGER } from '../src/ui/Contested'

const REPO = join(fileURLToPath(new URL('..', import.meta.url)), '..')
const noop = () => {}

/** The words a reader actually sees. */
function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

// --------------------------------------------------------------------------
// §8 — the search field
// --------------------------------------------------------------------------

describe('the search field', () => {
  it('names the retrieval mode', () => {
    // Retrieval is two arms fused by rank (`P2-06`), and lexical-only results
    // fail differently from fused ones. A search box that hid the mode would
    // make a thin result set unreadable — you could not tell a narrow corpus
    // from a missing vector arm.
    const markup = renderToStaticMarkup(<SearchField value="" onChange={noop} />)
    expect(text(markup)).toContain(MODE_MARKER)
  })

  it('states that filters run before the vector search', () => {
    // §12.5's behaviour, said where the query is typed. It is the difference
    // between "twenty government sources" and "whatever survived filtering the
    // top twenty", and a reader who assumes the second mistrusts a correct
    // result set.
    expect(text(renderToStaticMarkup(<SearchField value="" onChange={noop} />))).toContain(
      FILTER_NOTE,
    )
  })

  it('lets the note be replaced, so a degraded mode can say so', () => {
    // The embedder is a separate pass, so a corpus can be searchable lexically
    // and not semantically. Leaving the standing note in place would claim a
    // hybrid search that did not happen.
    const markup = renderToStaticMarkup(
      <SearchField value="" onChange={noop} note="No vectors yet. Lexical matches only." />,
    )

    expect(text(markup)).toContain('Lexical matches only.')
    expect(text(markup)).not.toContain(FILTER_NOTE)
  })

  it('labels the input for a screen reader', () => {
    // A bare search box announces as "search" and nothing else; the corpus it
    // searches is the part worth saying.
    expect(renderToStaticMarkup(<SearchField value="" onChange={noop} />)).toContain('<label')
  })
})

// --------------------------------------------------------------------------
// §8 — the four counts
// --------------------------------------------------------------------------

describe('the corpus counts', () => {
  it('renders an em dash per figure when the counts are unknown', () => {
    // The substance of this component. Zeros here would be a lie that reads as
    // a true statement about an empty corpus, and a reader cannot tell "nothing
    // has been crawled" from "the API did not answer". §4's first rule is to
    // state the absence; rendering 0 states the opposite.
    const markup = renderToStaticMarkup(<CorpusCounts counts={null} />)

    expect(text(markup).match(new RegExp(UNKNOWN, 'g'))).toHaveLength(4)
    expect(text(markup)).not.toMatch(/\b0\b/)
  })

  it('renders a real zero as a zero', () => {
    // The converse, and it is what makes the test above mean anything: if the
    // em dash were simply how this component draws zero, the distinction would
    // not exist.
    const empty: CorpusFigures = { documents: 0, nodes: 0, edges: 0, contested: 0 }
    const markup = text(renderToStaticMarkup(<CorpusCounts counts={empty} />))

    expect(markup).toMatch(/\b0\b/)
    expect(markup).not.toContain(UNKNOWN)
  })

  it('names all four figures §8 asks for', () => {
    const markup = text(renderToStaticMarkup(<CorpusCounts counts={null} />))

    expect(markup).toContain('Documents')
    expect(markup).toContain('Nodes')
    expect(markup).toContain('Edges')
    expect(markup).toContain('Contested')
  })

  it('marks the contested count with the dagger, not only with brass', () => {
    // §6: the tint never appears without the mark. A count is exactly where a
    // reader who cannot distinguish brass would otherwise lose which figure is
    // which.
    const markup = renderToStaticMarkup(<CorpusCounts counts={null} />)
    const withoutColour = markup.replace(/class="[^"]*"/g, '')

    expect(withoutColour).toContain(DAGGER)
  })
})

// --------------------------------------------------------------------------
// §12.5 — three entry points, in parallel
// --------------------------------------------------------------------------

describe('the entry points', () => {
  it('offers exactly the three §12.5 names', () => {
    expect(ENTRY_POINTS.map((entry) => entry.name)).toEqual(['search', 'coverage', 'contested'])
  })

  it('gives each one a description rather than a bare title', () => {
    // §12.3 notes the coverage grid is the view people skip and then miss,
    // because absence is what gap analysis acts on. A card labelled only
    // "Coverage" is a menu item, and menu items get skipped.
    for (const entry of ENTRY_POINTS) {
      expect(entry.description.length, entry.name).toBeGreaterThan(20)
    }
  })

  it('draws them as siblings, so search does not swallow the other two', () => {
    const markup = renderToStaticMarkup(<EntryPoints onOpen={noop} />)
    expect(markup.match(/<li>/g)).toHaveLength(3)
  })

  it('says why an unavailable entry point is unavailable', () => {
    // A disabled card that still shows its normal description tells the reader
    // nothing about why it will not open, and §4 asks an error to name the cause
    // and the recovery.
    const markup = text(
      renderToStaticMarkup(
        <EntryPoints onOpen={noop} unavailable={{ coverage: 'Coverage scoring is not built yet.' }} />,
      ),
    )

    expect(markup).toContain('Coverage scoring is not built yet.')
    expect(markup).not.toContain('Thin and ageing cells first.')
  })
})

// --------------------------------------------------------------------------
// §8 — where you were
// --------------------------------------------------------------------------

describe('where you were', () => {
  it('names the empty condition instead of rendering nothing', () => {
    // A reader with nothing here has not used the system yet; a reader who saved
    // three views last week and sees an empty region has a bug. Rendering
    // nothing makes those indistinguishable.
    const markup = text(renderToStaticMarkup(<WhereYouWere savedViews={[]} recentNodes={[]} />))

    expect(markup).toContain('No saved views and no recent nodes.')
  })

  it('lists saved views and recent nodes when there are some', () => {
    const markup = text(
      renderToStaticMarkup(
        <WhereYouWere
          savedViews={[{ id: 'v1', name: 'Kerbside pilots' }]}
          recentNodes={[{ id: 'n1', name: 'Modal shift', nodeType: 'concept' }]}
        />,
      ),
    )

    expect(markup).toContain('Kerbside pilots')
    expect(markup).toContain('Modal shift')
  })

  it('says which half is empty when only one is', () => {
    const markup = text(
      renderToStaticMarkup(
        <WhereYouWere savedViews={[{ id: 'v1', name: 'Kerbside pilots' }]} recentNodes={[]} />,
      ),
    )

    expect(markup).toContain('None opened yet.')
  })

  it('marks a contested node with the dagger as text', () => {
    const markup = renderToStaticMarkup(
      <WhereYouWere
        savedViews={[]}
        recentNodes={[{ id: 'n1', name: 'Modal shift', nodeType: 'finding', contested: true }]}
      />,
    )

    expect(markup.replace(/class="[^"]*"/g, '')).toContain(DAGGER)
  })
})

// --------------------------------------------------------------------------
// §4 — the voice, checked against the list the design system publishes
// --------------------------------------------------------------------------

describe('the copy holds the voice guide', () => {
  /** §4 ends with "Words this system does not use: …". Parsed, not retyped. */
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
    renderToStaticMarkup(<SearchField value="" onChange={noop} />),
    renderToStaticMarkup(<CorpusCounts counts={null} />),
    renderToStaticMarkup(<EntryPoints onOpen={noop} />),
    renderToStaticMarkup(<WhereYouWere savedViews={[]} recentNodes={[]} />),
  ].map(text)

  it('parses a real list out of the design system', () => {
    // Guard on the parse. A regex that silently matched nothing would make the
    // assertion below vacuously true, which is the usual way a rule like this
    // stops being enforced.
    const words = bannedWords()
    expect(words.length).toBeGreaterThan(5)
    expect(words).toContain('unlock')
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
    // §4 lists them beside the banned words, and they are the fastest way for
    // this system's register to slip: warmth here comes from precision.
    for (const copy of rendered) expect(copy).not.toContain('!')
  })
})
