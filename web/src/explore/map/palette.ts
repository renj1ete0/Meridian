/**
 * The canvas's colours, read from the design tokens at draw time (task P6-29).
 *
 * Nothing here holds a colour. Every value is looked up on the element the map
 * is drawn in, so a token change or a theme switch reaches the dots on the next
 * read rather than waiting on a rebuild — and `tokens.test.ts` keeps holding
 * the palette to the design system without this file needing an exemption.
 */
import { swatchVar, type Swatch } from '../../lib/corpusmap'
import { mix, parseColour, type RGB } from './geometry'

export interface CanvasPalette {
  /** `ground.deep`, behind everything. */
  ground: RGB
  /**
   * The graticule. §2 publishes it for the graph canvas as a step off the
   * ground, but not as a token; mixing the hairline `line` into `ground.deep`
   * lands on the same quiet step without inventing a colour.
   */
  graticule: RGB
  /** Axis rules — one step stronger than the graticule, still quieter than data. */
  axis: RGB
  /** A topic with no series slot: Other, and Unlabelled. */
  neutral: RGB
  topics: Map<string | null, RGB>
}

/** How far toward `line` the graticule sits from `ground.deep`. */
export const GRATICULE_MIX = 0.45

type Read = (property: string) => string

/** The palette from any property reader — `getComputedStyle` in the page, a map in a test. */
export function paletteFrom(read: Read, swatches: ReadonlyMap<string | null, Swatch>): CanvasPalette | null {
  const colour = (property: string) => parseColour(read(property))
  const ground = colour('--ground-deep')
  const line = colour('--line')
  const strong = colour('--line-strong')
  const neutral = colour('--text-faint')
  if (!ground || !line || !strong || !neutral) return null

  const topics = new Map<string | null, RGB>()
  for (const [topic, swatch] of swatches) topics.set(topic, colour(swatchVar(swatch)) ?? neutral)

  return {
    ground,
    graticule: mix(ground, line, GRATICULE_MIX),
    axis: mix(ground, strong, 0.7),
    neutral,
    topics,
  }
}

/** The palette as it currently resolves on ``element``. */
export function readPalette(element: Element, swatches: ReadonlyMap<string | null, Swatch>): CanvasPalette | null {
  const style = getComputedStyle(element)
  return paletteFrom((property) => style.getPropertyValue(property), swatches)
}

/** `rgb(…)` for a 2D context. */
export function css([r, g, b]: RGB, alpha = 1): string {
  const channel = (v: number) => Math.round(v * 255)
  return `rgba(${channel(r)}, ${channel(g)}, ${channel(b)}, ${alpha})`
}
