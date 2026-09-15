import { ICONS, type IconGeometry } from './icons'

/**
 * One icon, drawn to §7's grid (task P6-16).
 *
 * Every shared attribute lives here rather than in the path data: the 24-unit
 * grid, the two stroke weights, the round terminals. An icon that carried its
 * own would be an icon that could quietly stop matching the others, and a set
 * whose strokes disagree reads as amateurish long before anyone can say why.
 */

/** §7: silhouette 1.6, interior detail 1.35 — the mark's own 2.8:2.4 ratio. */
export const STROKE_SILHOUETTE = 1.6
export const STROKE_DETAIL = 1.35

/** §7: the drawing grid, and the live areas within it. */
export const GRID = 24
export const LIVE_INTERFACE = 20
export const LIVE_GLYPH = 16

/**
 * Below this, detail strokes are dropped and the silhouette carries alone.
 *
 * The same optical-size rule the compact mark follows. Interior detail at 1.35
 * units on a 24-unit grid is a third of a pixel at 16px: it does not render as
 * detail, it renders as a smudge that makes the silhouette look blurry.
 */
export const DETAIL_FLOOR = 20

export interface IconProps {
  name: keyof typeof ICONS
  size?: number
  /** Accessible name. Omit for an icon that only repeats adjacent text. */
  title?: string
  className?: string
}

export function Icon({ name, size = LIVE_INTERFACE, title, className }: IconProps) {
  const geometry: IconGeometry | undefined = ICONS[name]
  if (!geometry) return null

  const showDetail = size >= DETAIL_FLOOR && geometry.detail !== undefined

  return (
    <svg
      viewBox={`0 0 ${GRID} ${GRID}`}
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      // An icon with no accessible name is decoration. Saying so is not
      // optional: a screen reader otherwise announces "graphic" beside every
      // control, which is noise that trains people to ignore the real ones.
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}
      <path d={geometry.silhouette} strokeWidth={STROKE_SILHOUETTE} />
      {showDetail ? <path d={geometry.detail} strokeWidth={STROKE_DETAIL} /> : null}
    </svg>
  )
}
