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
const node = (label: string, x: number, y: number, extra: object = {}) => ({ label, x, y, size: 4, ...extra })

describe('label placement', () => {
  it('leaves out a label that would overlap one already drawn', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, drawn } = fakeContext()

    draw(ctx, node('stopping sight distances', 100, 100))
    draw(ctx, node('required sight distances', 120, 102))

    expect(drawn).toEqual(['stopping sight distances'])
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

  it('brings a crowded-out label back on the next frame', () => {
    const placer = new LabelPlacer()
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption', placer)
    const { ctx, drawn } = fakeContext()

    draw(ctx, node('first', 100, 100))
    draw(ctx, node('second', 100, 100))
    placer.reset()
    draw(ctx, node('second', 100, 100))

    expect(drawn).toEqual(['first', 'second'])
  })

  it('a drawer with no placer — the hover one — never leaves a label out', () => {
    const draw = makeLabelDrawer(FONTS, 'halo', 'dagger', 'caption')
    const { ctx, drawn } = fakeContext()

    draw(ctx, node('one', 100, 100))
    draw(ctx, node('one again', 100, 100))

    expect(drawn).toEqual(['one', 'one again'])
  })
})
