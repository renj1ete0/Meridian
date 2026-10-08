/**
 * Graph labels do not run into each other (task B-124).
 *
 * A fake 2D context, because jsdom has no canvas and the question is where
 * text would be drawn, not how it looks: `fillText` calls are the drawn labels.
 */
import { describe, expect, it } from 'vitest'

import { LabelPlacer, makeLabelDrawer } from '../src/explore/graph/GraphCanvas'

function fakeContext() {
  const drawn: string[] = []
  const ctx = {
    font: '',
    textAlign: '',
    textBaseline: '',
    lineJoin: '',
    strokeStyle: '',
    fillStyle: '',
    lineWidth: 0,
    // Seven pixels a character: wide enough that neighbours collide.
    measureText: (t: string) => ({ width: t.length * 7 }),
    strokeText: () => {},
    fillText: (t: string) => drawn.push(t),
  }
  return { ctx: ctx as unknown as CanvasRenderingContext2D, drawn }
}

const FONTS = { sans: 'sans', mono: 'mono' }

/** Records where each label was drawn: the left edge and baseline of its text. */
function placingContext() {
  const at: Array<{ text: string; x: number; y: number }> = []
  const { ctx } = fakeContext()
  ;(ctx as unknown as { fillText: (t: string, x: number, y: number) => void }).fillText = (t, x, y) =>
    at.push({ text: t, x, y })
  return { ctx, at }
}
const node = (label: string, x: number, y: number, extra: object = {}) => ({ label, x, y, size: 4, ...extra })

describe('label placement', () => {
  it('leaves out a label only when every place around its node is taken', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, drawn } = fakeContext()

    // Four long labels around (100, 100), then a fifth node there: below, above, right
    // and left are all covered.
    draw(ctx, node('stopping sight distances', 100, 100))
    draw(ctx, node('required sight distances', 100, 90))
    draw(ctx, node('passing sight distances', 230, 100))
    draw(ctx, node('decision sight distances', -30, 100))
    draw(ctx, node('crowded out', 100, 100))

    expect(drawn).not.toContain('crowded out')
  })

  it('moves a crowded label above, then beside, its node before leaving it out (B-169)', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, at } = placingContext()

    // Two one-letter labels take below and above a node; a third there goes to its right.
    draw(ctx, node('a', 300, 100))
    draw(ctx, node('b', 300, 100))
    draw(ctx, node('third', 300, 100))

    const [first, second, third] = at
    expect(at.map((a) => a.text)).toEqual(['a', 'b', 'third'])
    expect(first!.y).toBeGreaterThan(100) // below its node
    expect(second!.y).toBeLessThan(100) // above its node
    expect(third!.x).toBeGreaterThan(300) // to the right of its node
  })

  it('tries the left when the right is taken', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, at } = placingContext()

    for (const label of ['a', 'b', 'c', 'fourth']) draw(ctx, node(label, 300, 100))

    expect(at.map((a) => a.text)).toEqual(['a', 'b', 'c', 'fourth'])
    expect(at[3]!.x + 'fourth'.length * 7).toBeLessThan(300) // wholly left of its node
  })

  it('keeps a captioned label above or below, where its caption can hang', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, at } = placingContext()

    draw(ctx, node('stopping sight distances', 100, 100))
    draw(ctx, node('required sight distances', 100, 90))
    draw(ctx, node('policy x', 100, 100, { caption: 'cross-topic' }))

    expect(at.map((a) => a.text)).not.toContain('policy x')
  })

  it('draws labels that have room', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, drawn } = fakeContext()

    draw(ctx, node('traffic delays', 100, 100))
    draw(ctx, node('road capacity', 100, 200))
    draw(ctx, node('SB 1298', 400, 100))

    expect(drawn).toEqual(['traffic delays', 'road capacity', 'SB 1298'])
  })

  it('always draws the focus, even over a label already there', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, drawn } = fakeContext()

    draw(ctx, node('a neighbour here', 100, 100))
    draw(ctx, node('the focus', 105, 101, { isFocus: true }))

    expect(drawn).toEqual(['a neighbour here', 'the focus'])
  })

  it('starts every frame empty, so a label drawn beside its node returns below it', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, at } = placingContext()

    draw(ctx, node('first', 100, 100))
    draw(ctx, node('second', 100, 100))
    placer.reset()
    draw(ctx, node('second', 100, 100))

    expect(at.map((a) => a.text)).toEqual(['first', 'second', 'second'])
    expect(at[1]!.y).toBeLessThan(100)
    expect(at[2]!.y).toBeGreaterThan(100)
  })

  it('a drawer with no placer — the hover one — never leaves a label out', () => {
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption')
    const { ctx, drawn } = fakeContext()

    draw(ctx, node('one', 100, 100))
    draw(ctx, node('one again', 100, 100))

    expect(drawn).toEqual(['one', 'one again'])
  })
})
