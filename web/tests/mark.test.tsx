/**
 * The mark and its lockups — docs/design/design-system.md §1.
 *
 * §1 publishes a geometry table and a bounding box, which makes most of this
 * file a drift test rather than a matter of opinion: the box is derivable from
 * the coordinates, so if the two disagree a number has been mistyped. That is
 * the failure worth catching — a mark 1.2 units off centre looks fine in
 * isolation and wrong beside a previous export of itself.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import {
  COMPACT_BELOW,
  COMPACT_MARK,
  FULL_MARK,
  FULL_MARK_BOX,
  Lockup,
  MARK_GRID,
  MIN_COMPACT_SIZE,
  MIN_FULL_SIZE,
  MIN_LOCKUP_WIDTH,
  Mark,
  WORDMARK,
  geometryFor,
  type MarkGeometry,
} from '../src/ui/Mark'

/** Every x/y extreme the drawing reaches, ignoring stroke width. */
function extents(g: MarkGeometry) {
  const xs: number[] = []
  const ys: number[] = []

  const circle = (c: { cx: number; cy: number; r: number }) => {
    xs.push(c.cx - c.r, c.cx + c.r)
    ys.push(c.cy - c.r, c.cy + c.r)
  }

  xs.push(g.globe.cx - g.globe.r, g.globe.cx + g.globe.r)
  ys.push(g.globe.cy - g.globe.r, g.globe.cy + g.globe.r)
  xs.push(g.meridian.cx - g.meridian.rx, g.meridian.cx + g.meridian.rx)
  ys.push(g.meridian.cy - g.meridian.ry, g.meridian.cy + g.meridian.ry)

  circle(g.zenith)
  circle(g.hub)
  circle(g.base)
  if (g.bearingNode) circle(g.bearingNode)

  if (g.bearing) {
    const numbers = g.bearing.d.match(/-?\d+(\.\d+)?/g)!.map(Number)
    for (let i = 0; i < numbers.length; i += 2) {
      xs.push(numbers[i]!)
      ys.push(numbers[i + 1]!)
    }
  }

  return {
    width: Math.max(...xs) - Math.min(...xs),
    height: Math.max(...ys) - Math.min(...ys),
  }
}

describe('the geometry matches what §1 publishes', () => {
  it('produces the published 76 × 86.2 bounding box', () => {
    // Derived from the coordinates rather than asserted alongside them. A
    // mistyped radius or centre moves the box, and nothing else in the drawing
    // would show it.
    const box = extents(FULL_MARK)

    expect(box.width).toBeCloseTo(FULL_MARK_BOX.width, 5)
    expect(box.height).toBeCloseTo(FULL_MARK_BOX.height, 5)
  })

  it('keeps the meridian heavier than the globe', () => {
    // §1: "The meridian stroke is heavier than the globe outline. The reference
    // line is the subject; the sphere is context." Equalise them and the mark
    // becomes a globe with a line on it — a different drawing that passes every
    // other check here.
    expect(FULL_MARK.meridian.strokeWidth).toBeGreaterThan(FULL_MARK.globe.strokeWidth)
    expect(COMPACT_MARK.meridian.strokeWidth).toBeGreaterThanOrEqual(COMPACT_MARK.globe.strokeWidth)
  })

  it('thickens every stroke in the compact variant', () => {
    // The point of having a second variant at all: thin strokes disappear into
    // the pixel grid at small sizes, so the compact mark is redrawn rather than
    // scaled.
    expect(COMPACT_MARK.globe.strokeWidth).toBeGreaterThan(FULL_MARK.globe.strokeWidth)
    expect(COMPACT_MARK.meridian.strokeWidth).toBeGreaterThan(FULL_MARK.meridian.strokeWidth)
  })

  it('widens the meridian and enlarges the nodes when it drops the edge', () => {
    expect(COMPACT_MARK.meridian.rx).toBeGreaterThan(FULL_MARK.meridian.rx)
    expect(COMPACT_MARK.zenith.r).toBeGreaterThan(FULL_MARK.zenith.r)
    expect(COMPACT_MARK.hub.r).toBeGreaterThan(FULL_MARK.hub.r)
    expect(COMPACT_MARK.base.r).toBeGreaterThan(FULL_MARK.base.r)
  })

  it('orders the minimum sizes so the switch sits inside the usable range', () => {
    // A compact minimum above the switch point would leave a band of sizes with
    // no legal variant, which is the kind of gap nobody notices until an icon is
    // requested at 20px.
    expect(MIN_COMPACT_SIZE).toBeLessThan(COMPACT_BELOW)
    expect(MIN_FULL_SIZE).toBe(COMPACT_BELOW)
  })
})

describe('the optical-size switch', () => {
  it('uses the full mark at and above the switch point', () => {
    expect(geometryFor(COMPACT_BELOW)).toBe(FULL_MARK)
    expect(geometryFor(76)).toBe(FULL_MARK)
  })

  it('uses the compact mark below it', () => {
    expect(geometryFor(COMPACT_BELOW - 1)).toBe(COMPACT_MARK)
    expect(geometryFor(MIN_COMPACT_SIZE)).toBe(COMPACT_MARK)
  })

  it('drops the bearing edge and its node, not just the stroke weight', () => {
    // §1 is specific that the compact variant *drops* them. Drawing them
    // thinner would put a 4-unit node and a diagonal inside a 16px square,
    // where they read as a smudge on the rim.
    const small = renderToStaticMarkup(<Mark size={MIN_COMPACT_SIZE} />)
    const large = renderToStaticMarkup(<Mark size={76} />)

    expect(small).not.toContain('M50 50')
    expect(small).not.toContain('81.1')
    expect(large).toContain('M50 50 L81.1 71.8')
    expect(large).toContain('cx="81.1"')
  })
})

describe('single ink', () => {
  it('reads with the accent dropped', () => {
    // §1: "the mark must read with the accent dropped. It is a stroke drawing,
    // not a colour composition." The test is the same shape as §6's for the
    // contested dagger — strip the colour and check the meaning survives.
    const markup = renderToStaticMarkup(<Mark size={76} />)
    const withoutColour = markup.replace(/class="[^"]*"/g, '').replace(/fill="[^"]*"/g, '')

    expect(withoutColour).toContain('r="38"') // globe
    expect(withoutColour).toContain('rx="14"') // meridian
    expect(withoutColour).toContain('M50 50 L81.1 71.8') // bearing
    expect(withoutColour).toContain('cy="12"') // zenith
    expect(withoutColour).toContain('cy="88"') // base
  })

  it('accents the zenith node and nothing else', () => {
    // §1 gives exactly one element the accent. It is also why §1 forbids placing
    // the mark on `accent.graph`: on that ground this node vanishes and the
    // meridian reads as running off the top.
    const markup = renderToStaticMarkup(<Mark size={76} />)

    expect(markup).toContain('fill-accent-graph')
    expect(markup.match(/fill-accent-graph/g)).toHaveLength(1)
  })

  it('draws in one ink when asked', () => {
    // Not a degradation. §1 names two cases that need it: print as 100% K, and a
    // knockout over unpredictable ground.
    const markup = renderToStaticMarkup(<Mark size={76} monochrome />)

    expect(markup).not.toContain('fill-accent-graph')
    expect(markup).toContain('cy="12"')
  })
})

describe('the drawing as markup', () => {
  it('scales from a 100-unit grid rather than from a fixed size', () => {
    const markup = renderToStaticMarkup(<Mark size={24} />)
    expect(markup).toContain(`viewBox="0 0 ${MARK_GRID} ${MARK_GRID}"`)
    expect(markup).toContain('width="24"')
  })

  it('hides a titleless mark from screen readers', () => {
    // Same rule as `Icon`: a mark beside the wordmark is decoration, and
    // announcing it twice trains people to ignore the announcements.
    expect(renderToStaticMarkup(<Mark />)).toContain('aria-hidden="true"')

    const named = renderToStaticMarkup(<Mark title="Meridian" />)
    expect(named).toContain('role="img"')
    expect(named).toContain('<title>Meridian</title>')
  })
})

describe('the lockup', () => {
  it('declares §1’s minimum width rather than assuming it', () => {
    // Below it the wordmark's counters close up before the mark does, so the
    // lockup fails as type while the drawing still looks correct.
    const markup = renderToStaticMarkup(<Lockup />)
    expect(markup).toContain(`min-width:${MIN_LOCKUP_WIDTH}px`)
  })

  it('carries the mark, a hairline and the wordmark, in that order', () => {
    const markup = renderToStaticMarkup(<Lockup />)

    const mark = markup.indexOf('<svg')
    const hairline = markup.indexOf('data-role="hairline"')
    // The wordmark as a *text node*. `indexOf(WORDMARK)` finds the lockup's own
    // `aria-label` first, which sits on the outer element and would make this
    // assertion pass regardless of the order the children are drawn in.
    const wordmark = markup.indexOf(`>${WORDMARK}<`)

    expect(mark).toBeGreaterThan(-1)
    expect(hairline).toBeGreaterThan(mark)
    expect(wordmark).toBeGreaterThan(hairline)
  })

  it('sets the wordmark at §1’s tracking', () => {
    expect(renderToStaticMarkup(<Lockup />)).toContain('tracking-[-0.006em]')
  })

  it('names itself once, not twice', () => {
    // The lockup is one thing to a screen reader. Letting the mark announce
    // itself inside a labelled lockup reads the product name twice.
    const markup = renderToStaticMarkup(<Lockup />)

    expect(markup).toContain(`aria-label="${WORDMARK}"`)
    expect(markup).toContain('aria-hidden="true"')
  })

  it('carries the mono descriptor only when stacked', () => {
    // §1 puts the descriptor under the stacked lockup alone. On the horizontal
    // one it would compete with the wordmark it is meant to qualify.
    expect(renderToStaticMarkup(<Lockup orientation="stacked" />)).toContain(
      'AUTONOMOUS RESEARCH SYSTEM',
    )
    expect(renderToStaticMarkup(<Lockup />)).not.toContain('AUTONOMOUS RESEARCH SYSTEM')
  })
})
