/**
 * Shared UI primitives (task P6-16) — docs/design/design-system.md §2, §5, §6, §7.
 *
 * Rendered to static markup rather than into a DOM. These are presentational:
 * there is no state to drive and no event to fire, and the questions worth
 * asking — does the dagger survive without colour, do all tier chips look
 * alike, does an icon keep the published stroke weights — are questions about
 * the output, not about a live tree.
 *
 * Two of the assertions here are transcriptions of rules the design system
 * states outright, one of which it explicitly calls a test.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { Contested, DAGGER } from '../src/ui/Contested'
import { DETAIL_FLOOR, GRID, Icon, STROKE_DETAIL, STROKE_SILHOUETTE } from '../src/ui/Icon'
import { ICONS, INTERFACE_ICONS, NODE_GLYPHS } from '../src/ui/icons'
import { SOURCE_TIERS, TIER_LABEL, TierChip, type SourceTier } from '../src/ui/Tier'

const REPO = join(fileURLToPath(new URL('..', import.meta.url)), '..')

// --------------------------------------------------------------------------
// §6 — the contested mark
// --------------------------------------------------------------------------

describe('the contested mark survives having its colour stripped', () => {
  // §6 states this as the rule *and* as its test: "It is always paired with the
  // brass tint, and the brass tint never appears without it. Strip the colour
  // and the reading must survive — that is the test."
  //
  // It matters beyond appearance. §9 makes contradiction a result rather than
  // an error and contested nodes the highest-value ones in the graph, so a
  // reader who cannot see which those are — colour-blind, printed page, screen
  // in sunlight — loses the finding itself.
  it.each(['inline', 'chip', 'badge'] as const)('in its %s form', (form) => {
    const markup = renderToStaticMarkup(<Contested form={form}>Modal shift</Contested>)
    const withoutColour = markup.replace(/class="[^"]*"/g, '')

    expect(withoutColour).toContain(DAGGER)
  })

  it('renders the dagger as text, not as a background or a pseudo-element', () => {
    // The distinction the rule turns on: a dagger drawn in CSS vanishes when
    // the page is copied, printed without backgrounds, or read aloud.
    const markup = renderToStaticMarkup(<Contested>A claim</Contested>)

    expect(markup).toMatch(new RegExp(`>${DAGGER}<`))
  })

  it('gives the mark a spoken name', () => {
    // Screen readers either say "dagger" or skip U+2020 entirely, and neither
    // conveys that the claim is disputed.
    expect(renderToStaticMarkup(<Contested>x</Contested>)).toContain('aria-label="contested"')
  })
})

// --------------------------------------------------------------------------
// §2, §5 — tier chips carry no verdict
// --------------------------------------------------------------------------

describe('source tiers', () => {
  it('matches the database enum across the language boundary', () => {
    // A tier added to Postgres and not here renders through the fallback as a
    // raw enum value — `peer_reviewed`, underscore and all — which nobody
    // notices until it is in front of someone. The Python model is the source
    // of truth; this list mirrors it.
    const model = readFileSync(
      join(REPO, 'packages/meridian_core/meridian_core/models/source.py'),
      'utf8',
    )
    const declaration = /SOURCE_TIER = constrained\(([\s\S]*?)name="source_tier"/.exec(model)
    expect(declaration, 'SOURCE_TIER is no longer declared the way this test reads it').toBeTruthy()

    const inDatabase = [...declaration![1]!.matchAll(/"([a-z_]+)"/g)].map((m) => m[1])

    expect([...SOURCE_TIERS].sort()).toEqual(inDatabase.sort())
  })

  it('gives every tier a label, so none falls through to its enum value', () => {
    for (const tier of SOURCE_TIERS) {
      expect(TIER_LABEL[tier], tier).toBeTruthy()
      expect(TIER_LABEL[tier]).not.toContain('_')
    }
  })

  it('draws every tier identically, because colour must not imply a verdict', () => {
    // §2: "There is deliberately no green and no red in the palette. Nothing in
    // this system is pass/fail, and colour must not imply a verdict." A chip
    // that rendered peer-reviewed in one colour and informal in another would
    // be the interface asserting a credibility judgement the system explicitly
    // refuses to make (§8 extracts structure and scores nothing).
    const classes = SOURCE_TIERS.map((tier) => {
      const markup = renderToStaticMarkup(<TierChip tier={tier} />)
      return /class="([^"]*)"/.exec(markup)?.[1]
    })

    expect(new Set(classes).size, 'tier chips differ in appearance').toBe(1)
  })

  it('distinguishes tiers by text, not only by an attribute', () => {
    // The other half of §5's "state in form, not only colour": if the chips are
    // identical *and* the text were identical, the chip would say nothing.
    const rendered = SOURCE_TIERS.map((tier) =>
      renderToStaticMarkup(<TierChip tier={tier as SourceTier} />).replace(/<[^>]+>/g, ''),
    )

    expect(new Set(rendered).size).toBe(SOURCE_TIERS.length)
  })
})

// --------------------------------------------------------------------------
// §7 — the icon grid
// --------------------------------------------------------------------------

describe('icons hold the published grid', () => {
  it('names the eleven interface icons and six node glyphs §7 lists', () => {
    expect(Object.keys(INTERFACE_ICONS)).toHaveLength(11)
    expect(Object.keys(NODE_GLYPHS)).toHaveLength(6)
  })

  it.each(Object.keys(ICONS))('%s draws on the 24-unit grid at both weights', (name) => {
    const markup = renderToStaticMarkup(<Icon name={name} />)

    expect(markup).toContain(`viewBox="0 0 ${GRID} ${GRID}"`)
    expect(markup).toContain(`stroke-width="${STROKE_SILHOUETTE}"`)
    expect(markup).toContain('stroke-linecap="round"')
    expect(markup).toContain('stroke-linejoin="round"')
  })

  it('keeps the silhouette heavier than the detail inside it', () => {
    // §7's ratio is the mark's own — a 2.8 meridian against a 2.4 globe. Invert
    // it and the drawing reads inside-out.
    expect(STROKE_SILHOUETTE).toBeGreaterThan(STROKE_DETAIL)
  })

  it('drops interior detail below the optical-size floor', () => {
    // The same rule the compact mark follows. At 16px a 1.35-unit stroke on a
    // 24-unit grid is under half a pixel: it renders as a smudge that makes the
    // silhouette look blurry rather than as detail.
    const large = renderToStaticMarkup(<Icon name="search" size={DETAIL_FLOOR} />)
    const small = renderToStaticMarkup(<Icon name="search" size={DETAIL_FLOOR - 4} />)

    expect(large).toContain(`stroke-width="${STROKE_DETAIL}"`)
    expect(small).not.toContain(`stroke-width="${STROKE_DETAIL}"`)
  })

  it('leaves every icon recognisable from its silhouette alone', () => {
    // The consequence of the rule above: an icon whose meaning lives entirely
    // in its detail becomes an unidentifiable blob at glyph size.
    for (const [name, geometry] of Object.entries(ICONS)) {
      expect(geometry.silhouette, `${name} has no silhouette`).toBeTruthy()
    }
  })

  it('hides a titleless icon from screen readers', () => {
    // An icon that only repeats adjacent text is decoration. Announced, it
    // becomes "graphic" beside every control — noise that trains people to
    // ignore the announcements that matter.
    expect(renderToStaticMarkup(<Icon name="search" />)).toContain('aria-hidden="true"')

    const named = renderToStaticMarkup(<Icon name="search" title="Search" />)
    expect(named).toContain('role="img"')
    expect(named).toContain('<title>Search</title>')
    expect(named).not.toContain('aria-hidden')
  })
})
