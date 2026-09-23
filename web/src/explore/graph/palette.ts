/**
 * The canvas palette, read from the token file at runtime (task P6-01).
 *
 * WebGL takes colour strings, not CSS, so the renderer cannot use a Tailwind
 * class. Reading the custom properties keeps `tokens.css` the only place a
 * colour is written — `tests/tokens.test.ts` fails on a literal anywhere else.
 */

import { PALETTE_TOKENS, type CanvasPalette } from './style'

export function readPalette(root: Element = document.documentElement): CanvasPalette {
  const computed = getComputedStyle(root)
  const out = {} as CanvasPalette
  for (const [role, token] of Object.entries(PALETTE_TOKENS) as [keyof CanvasPalette, string][]) {
    out[role] = computed.getPropertyValue(token).trim()
  }
  return out
}
