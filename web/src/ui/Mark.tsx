/**
 * The mark and its lockups — docs/design/design-system.md §1.
 *
 * A globe outline crossed by one meridian. Three nodes sit on the meridian —
 * zenith, hub, base — and the arc between them *is* the edge. One straight edge
 * leaves the hub on a bearing and terminates on the rim: the graph continues
 * past the reference line.
 *
 * The meridian stroke is heavier than the globe's, and that is the whole idea
 * rather than a detail: the reference line is the subject and the sphere is
 * context. Equalise them and the drawing becomes a globe with a line on it.
 *
 * Until now the app had no mark at all — the README uses exported PNGs, which
 * cannot inherit the reader's theme, cannot be drawn at an arbitrary size
 * without resampling, and cannot honour §1's optical-size rule. A stroke drawing
 * that scales is the point of having published geometry.
 *
 * The numbers here are transcribed from §1's tables, not eyeballed from the
 * artboards, and `tests/mark.test.tsx` checks the resulting bounding box against
 * the one §1 publishes — which is how a fat-fingered coordinate gets caught.
 */

/** §1: the drawing grid. Optical centre is the circle centre, (50, 50). */
export const MARK_GRID = 100

/**
 * §1's optical-size switch: the compact mark is used *below* 32px.
 *
 * Not a stylistic preference. At small sizes the bearing edge and its node
 * collapse into the globe's rim and the meridian closes up, so the compact
 * variant drops the edge entirely and widens the meridian rather than shrinking
 * a drawing that has stopped being legible. The same rule `Icon` follows for
 * interior detail.
 */
export const COMPACT_BELOW = 32

/** §1's minimum sizes. Below these the drawing stops reading, whichever variant. */
export const MIN_FULL_SIZE = 32
export const MIN_COMPACT_SIZE = 16
export const MIN_LOCKUP_WIDTH = 132

/** §1's published bounding box for the full mark, in grid units. */
export const FULL_MARK_BOX = { width: 76, height: 86.2 }

export interface MarkNode {
  cx: number
  cy: number
  r: number
}

export interface MarkGeometry {
  globe: { cx: number; cy: number; r: number; strokeWidth: number }
  meridian: { cx: number; cy: number; rx: number; ry: number; strokeWidth: number }
  zenith: MarkNode
  hub: MarkNode
  base: MarkNode
  /** Present on the full mark only. The compact variant drops both. */
  bearing?: { d: string; strokeWidth: number }
  bearingNode?: MarkNode
}

/** §1, "Full mark" — 32px and above. */
export const FULL_MARK: MarkGeometry = {
  globe: { cx: 50, cy: 50, r: 38, strokeWidth: 2.4 },
  meridian: { cx: 50, cy: 50, rx: 14, ry: 38, strokeWidth: 2.8 },
  bearing: { d: 'M50 50 L81.1 71.8', strokeWidth: 2.4 },
  zenith: { cx: 50, cy: 12, r: 6 },
  hub: { cx: 50, cy: 50, r: 5 },
  base: { cx: 50, cy: 88, r: 4.2 },
  bearingNode: { cx: 81.1, cy: 71.8, r: 4 },
}

/** §1, "Compact mark" — below 32px. Thicker strokes survive rasterisation. */
export const COMPACT_MARK: MarkGeometry = {
  globe: { cx: 50, cy: 50, r: 38, strokeWidth: 3.6 },
  meridian: { cx: 50, cy: 50, rx: 17, ry: 38, strokeWidth: 3.6 },
  zenith: { cx: 50, cy: 12, r: 7 },
  hub: { cx: 50, cy: 50, r: 6 },
  base: { cx: 50, cy: 88, r: 5 },
}

export function geometryFor(size: number): MarkGeometry {
  return size < COMPACT_BELOW ? COMPACT_MARK : FULL_MARK
}

export interface MarkProps {
  /** Rendered size in px. Selects the variant; see `COMPACT_BELOW`. */
  size?: number
  /**
   * Drop the accent and draw in a single ink.
   *
   * §1 requires the mark to read with the accent removed — "it is a stroke
   * drawing, not a colour composition" — and names two cases that need it: print
   * as 100% K, and a solid knockout over unpredictable ground. So this is a
   * supported state rather than a degradation.
   */
  monochrome?: boolean
  /** Accessible name. Omit where adjacent text already names the product. */
  title?: string
  className?: string
}

export function Mark({ size = 76, monochrome = false, title, className }: MarkProps) {
  const g = geometryFor(size)

  return (
    <svg
      viewBox={`0 0 ${MARK_GRID} ${MARK_GRID}`}
      width={size}
      height={size}
      fill="none"
      className={className}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}

      {/* Strokes in `currentColor` so the mark inherits the theme's ink rather
          than carrying a colour of its own. §1's "single ink" rule is only true
          if there is one ink to begin with. */}
      <circle
        cx={g.globe.cx}
        cy={g.globe.cy}
        r={g.globe.r}
        stroke="currentColor"
        strokeWidth={g.globe.strokeWidth}
      />
      <ellipse
        cx={g.meridian.cx}
        cy={g.meridian.cy}
        rx={g.meridian.rx}
        ry={g.meridian.ry}
        stroke="currentColor"
        strokeWidth={g.meridian.strokeWidth}
      />
      {g.bearing ? (
        <path
          d={g.bearing.d}
          stroke="currentColor"
          strokeWidth={g.bearing.strokeWidth}
          strokeLinecap="round"
        />
      ) : null}
      {g.bearingNode ? (
        <circle cx={g.bearingNode.cx} cy={g.bearingNode.cy} r={g.bearingNode.r} fill="currentColor" />
      ) : null}

      <circle cx={g.base.cx} cy={g.base.cy} r={g.base.r} fill="currentColor" />
      <circle cx={g.hub.cx} cy={g.hub.cy} r={g.hub.r} fill="currentColor" />

      {/* Zenith last, so it sits above the meridian it terminates. The one
          accented element (§1), and the reason §1 forbids placing the mark on
          `accent.graph`: on that ground this node disappears and the meridian
          reads as running off the top. */}
      <circle
        cx={g.zenith.cx}
        cy={g.zenith.cy}
        r={g.zenith.r}
        className={monochrome ? undefined : 'fill-accent-graph'}
        fill={monochrome ? 'currentColor' : undefined}
        data-role="zenith"
      />
    </svg>
  )
}

export const WORDMARK = 'Meridian'

/** §1's stacked descriptor. Mono, because it is a label rather than prose. */
export const DESCRIPTOR = 'AUTONOMOUS RESEARCH SYSTEM'

export interface LockupProps {
  /** `horizontal` is §1's primary; `stacked` its secondary. */
  orientation?: 'horizontal' | 'stacked'
  /** Size of the mark within the lockup. */
  size?: number
  monochrome?: boolean
  className?: string
}

/**
 * §1's lockup: mark · 22px gap · 1px hairline rule at cap height · 22px gap ·
 * wordmark, set in Archivo 500 at −0.006em.
 *
 * The rule is a separator, not a border: it is the height of the wordmark's cap
 * rather than of the mark, which is what keeps the three elements reading as one
 * line of type with a drawing at its head.
 */
export function Lockup({
  orientation = 'horizontal',
  size = 40,
  monochrome = false,
  className,
}: LockupProps) {
  const mark = <Mark size={size} monochrome={monochrome} />

  if (orientation === 'stacked') {
    return (
      <span
        className={`inline-flex flex-col items-center gap-3 text-text ${className ?? ''}`}
        role="img"
        aria-label={WORDMARK}
      >
        {mark}
        <span
          className="font-sans font-medium tracking-[-0.006em]"
          style={{ fontSize: size * 0.42 }}
        >
          {WORDMARK}
        </span>
        <span className="font-mono text-[8.5px] uppercase tracking-[0.19em] text-text-muted">
          {DESCRIPTOR}
        </span>
      </span>
    )
  }

  return (
    <span
      className={`inline-flex items-center text-text ${className ?? ''}`}
      role="img"
      aria-label={WORDMARK}
      // §1's minimum, declared rather than assumed: below it the wordmark's
      // counters close up before the mark does, so the lockup fails as type
      // rather than as a drawing.
      style={{ minWidth: MIN_LOCKUP_WIDTH }}
    >
      {mark}
      <span className="inline-block w-[22px]" />
      {/* The hairline, at cap height. Archivo's cap height is ~0.72em, so the
          rule is sized from the wordmark rather than from the mark — a rule as
          tall as the drawing would read as a table border. */}
      <span
        aria-hidden="true"
        className="inline-block w-px bg-line-strong"
        style={{ height: size * 0.42 * 0.72 }}
        data-role="hairline"
      />
      <span className="inline-block w-[22px]" />
      <span className="font-sans font-medium tracking-[-0.006em]" style={{ fontSize: size * 0.42 }}>
        {WORDMARK}
      </span>
    </span>
  )
}
