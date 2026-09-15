/**
 * The gazetteer approval queue (task P6-13, spec §5.6, design §2).
 *
 * §5.6 calls approving terms "a two-minute weekly task", and the two minutes are
 * the design constraint: the screen has to say enough for a decision without
 * being read carefully.
 *
 * Three properties carry this file.
 *
 * **The verdict is legible.** An approved term whose wording another row already
 * claims is withheld from the matcher, so it reads approved and matches nothing
 * in any document. Nothing else in the system reports that, so if the sentence
 * here is wrong or missing the failure is silent and permanent.
 *
 * **Counts are unfiltered.** A tab reading "waiting (0)" while forty sit under
 * another one is the filter hiding the thing the curator came for.
 *
 * **Neither decision is coloured.** §2: the palette has no green and no red, and
 * colour must not imply a verdict. A green approve button would also make
 * turning a term down read as the damaging option, when rejecting bad terms is
 * what the screen is for.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ENTITY_TYPES, GazetteerQueue, verdictOf } from '../src/admin/GazetteerQueue'
import type { GazetteerRow, GazetteerTerm } from '../src/lib/api'

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

function term(over: Partial<GazetteerTerm> = {}): GazetteerTerm {
  return {
    term_id: 12,
    canonical: 'Provisional Coordination Bureau',
    aliases: ['PCB'],
    entity_type: 'concept',
    jurisdiction: null,
    ambiguous: false,
    topic_labels: null,
    source: 'auto_acronym',
    approved: false,
    occurrence_count: 3,
    rejected_at: null,
    created_at: '2026-09-15T00:00:00Z',
    ...over,
  }
}

function row(over: Partial<GazetteerRow> = {}): GazetteerRow {
  return {
    term: term(),
    will_load: false,
    withheld_reason: 'unapproved',
    collides_with: [],
    ...over,
  }
}

const COUNTS = { pending: 7, approved: 64, rejected: 2 }

function render(rows: GazetteerRow[], over: Partial<Parameters<typeof GazetteerQueue>[0]> = {}) {
  return renderToStaticMarkup(
    <GazetteerQueue rows={rows} state="pending" counts={COUNTS} {...over} />,
  )
}

// --------------------------------------------------------------------------
// The verdict
// --------------------------------------------------------------------------

describe('every row says what the matcher will do with it', () => {
  it('says a loading term matches', () => {
    expect(verdictOf(row({ will_load: true, withheld_reason: null }))).toContain('Matches')
  })

  it('names the other rows in a collision', () => {
    // A curator cannot fix a collision without being told what it collided with.
    // "This term is not used" alone is a dead end.
    const sentence = verdictOf(
      row({ will_load: false, withheld_reason: 'collision', collides_with: [4, 9] }),
    )

    expect(sentence).toContain('4, 9')
    expect(sentence.toLowerCase()).toContain('change one')
  })

  it('distinguishes a term still waiting from one turned down', () => {
    // Both match nothing, and the two mean opposite things: one is waiting for
    // this curator and one already had their answer.
    const waiting = verdictOf(row({ withheld_reason: 'unapproved' }))
    const refused = verdictOf(row({ withheld_reason: 'rejected' }))

    expect(waiting).not.toEqual(refused)
  })

  it('says the full name still matches when only the short forms are held back', () => {
    // The case a boolean would lose. Reporting "matches" alone would tell a
    // curator everything is fine while the acronym they care about matches
    // nothing.
    const sentence = verdictOf(row({ will_load: true, withheld_reason: 'ambiguous' }))

    expect(sentence).toContain('full name')
    expect(sentence).toContain('short forms')
  })

  it('has a sentence for every reason the server can send', () => {
    // Drift: a reason added in `meridian_core/gazetteer.py` and not here falls
    // through to a generic line, which is the one case where the screen stops
    // being worth reading.
    const source = readFileSync(
      join(REPO, 'packages/meridian_core/meridian_core/gazetteer.py'),
      'utf8',
    )
    const reasons = [...source.matchAll(/reason: str\s*=|"(ambiguous|collision|no_patterns)"/g)]
      .map((m) => m[1])
      .filter((r): r is string => Boolean(r))
    const known = [...new Set([...reasons, 'unapproved', 'rejected'])]

    expect(known.length).toBeGreaterThanOrEqual(5)
    for (const reason of known) {
      const sentence = verdictOf(row({ withheld_reason: reason }))
      expect(sentence, `no sentence for ${reason}`).not.toEqual('Not matching.')
    }
  })
})

// --------------------------------------------------------------------------
// The list
// --------------------------------------------------------------------------

describe('the queue', () => {
  it('shows counts for every state, not just the one being viewed', () => {
    const rendered = text(render([row()]))

    expect(rendered).toContain('waiting (7)')
    expect(rendered).toContain('approved (64)')
    expect(rendered).toContain('turned down (2)')
  })

  it('counts documents rather than mentions', () => {
    // One report repeating a definition forty times has said one thing forty
    // times, and a curator reading "40 mentions" would approve it on that.
    const rendered = text(render([row({ term: term({ occurrence_count: 3 }) })]))

    expect(rendered).toContain('3 documents')
  })

  it('says a single document in the singular', () => {
    const rendered = text(render([row({ term: term({ occurrence_count: 1 }) })]))

    expect(rendered).toContain('1 document')
    expect(rendered).not.toContain('1 documents')
  })

  it('offers to put a turned-down term back, and not to approve it outright', () => {
    // The second look should start from "undecided" rather than from the answer
    // being reconsidered.
    const rendered = text(
      render([row({ term: term({ rejected_at: '2026-09-15T00:00:00Z' }) })]),
    )

    expect(rendered).toContain('Put back')
    expect(rendered).not.toContain('Approve')
  })

  it('explains an empty queue rather than showing a blank panel', () => {
    const rendered = text(render([]))

    expect(rendered).toContain('Nothing here')
  })

  it('shows the aliases, which are what a collision is usually about', () => {
    const rendered = text(render([row({ term: term({ aliases: ['PCB', 'P.C.B.'] }) })]))

    expect(rendered).toContain('PCB')
    expect(rendered).toContain('P.C.B.')
  })
})

// --------------------------------------------------------------------------
// §2 — colour carries no verdict
// --------------------------------------------------------------------------

describe('the decisions are not coloured', () => {
  it('gives approve and turn-down the same treatment', () => {
    const markup = render([row()])
    const buttons = [...markup.matchAll(/<button[^>]*>([^<]*)<\/button>/g)]
    const decisions = buttons.filter((m) => /Approve|Turn down/.test(m[1] ?? ''))

    expect(decisions).toHaveLength(2)
    const classes = decisions.map((m) => /class="([^"]*)"/.exec(m[0])?.[1]?.replace(/\s+/g, ' '))
    expect(classes[0]).toEqual(classes[1])
  })

  it('uses the attention accent only where something is wrong', () => {
    // The accent is the design system's one emphasis colour. Spending it on an
    // ordinary waiting term would leave nothing to mark the collision with.
    const ordinary = render([row()])
    const broken = render([row({ withheld_reason: 'collision', collides_with: [4] })])

    expect(ordinary).not.toContain('accent-attention')
    expect(broken).toContain('accent-attention')
  })
})

// --------------------------------------------------------------------------
// Cross-language drift
// --------------------------------------------------------------------------

describe('the type list matches the database', () => {
  it('offers exactly the five types the CHECK constraint allows', () => {
    // A type added in Postgres and not here cannot be chosen, so terms of that
    // type can only be filed wrongly; one removed there and left here is a
    // dropdown whose value the database refuses after the curator picks it.
    const source = readFileSync(
      join(REPO, 'packages/meridian_core/meridian_core/models/gazetteer.py'),
      'utf8',
    )
    const call = /GAZETTEER_ENTITY_TYPE = constrained\(([\s\S]*?)\)/.exec(source)
    expect(call, 'GAZETTEER_ENTITY_TYPE is no longer written the way this test reads it').toBeTruthy()

    const declared = [...call![1]!.matchAll(/"([a-z_]+)"/g)]
      .map((m) => m[1]!)
      .filter((value) => value !== 'gazetteer_entity_type')

    expect([...declared].sort()).toEqual([...ENTITY_TYPES].sort())
  })
})
