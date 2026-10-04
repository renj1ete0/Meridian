import type { MapPoint } from '../../lib/api'
import { swatchVar, topicLine, type Swatch } from '../../lib/corpusmap'

export const CARD_WIDTH = 280

/** A topic as a reader reads it: `on-demand-bus` → `on demand bus` (`P6-44`). */
export function topicLabel(topic: string | null): string {
  return topic === null ? 'Unlabelled' : topic.replaceAll('-', ' ').replaceAll('_', ' ')
}

/** A topic's colour as a small square chip — square, as §5 asks of everything. */
export function SwatchChip({ swatch, size = 8 }: { swatch: Swatch | undefined; size?: number }) {
  const colour = swatch ? `var(${swatchVar(swatch)})` : 'var(--text-faint)'
  return (
    <span aria-hidden className="inline-block shrink-0" style={{ background: colour, width: size, height: size }} />
  )
}

/** Where a card goes: beside the cursor, flipped to stay inside the frame. */
export function cardPosition(
  at: [number, number],
  frame: { width: number; height: number },
  card = { width: CARD_WIDTH, height: 150 },
): { left: number; top: number } {
  const gap = 14
  const left = at[0] + gap + card.width > frame.width ? Math.max(8, at[0] - gap - card.width) : at[0] + gap
  const top = at[1] + gap + card.height > frame.height ? Math.max(8, at[1] - gap - card.height) : at[1] + gap
  return { left, top }
}

/**
 * The translucent card from the graph canvas (design-system §5): it floats over
 * the picture and is gone when the cursor moves, so it is the one surface here
 * allowed to be see-through. `surface` at 90% with a `text` hairline at 16% is
 * §5's `rgba(21,30,51,0.90)` and `rgba(230,235,245,0.16)`, spelled in tokens.
 */
export function HoverCard({
  point,
  swatch,
  at,
  frame,
}: {
  point: MapPoint
  swatch: Swatch | undefined
  at: [number, number]
  frame: { width: number; height: number }
}) {
  const { left, top } = cardPosition(at, frame)
  let host = point.url
  try {
    host = new URL(point.url).hostname
  } catch {
    // Not a URL we can parse; show it whole rather than nothing.
  }
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 flex flex-col gap-1.5 border border-text/16 bg-surface/90 px-[13px] py-[11px] backdrop-blur-[10px]"
      style={{ left, top, width: CARD_WIDTH }}
    >
      <span className="line-clamp-2 text-[13px] font-semibold leading-snug text-text">{point.title ?? host}</span>
      <span className="flex items-center gap-2 font-mono text-[9.5px] uppercase tracking-[0.12em] text-text-faint">
        <SwatchChip swatch={swatch} size={7} />
        {/* Every topic, primary first: the dot is drawn in one colour and the
            passage may be about several (`P2-21`). */}
        <span className="truncate" data-testid="hover-topics">
          {topicLine(point)}
        </span>
      </span>
      <span className="line-clamp-3 text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
        {point.snippet}
      </span>
      <span className="truncate font-mono text-[10.5px] text-text-faint">
        {host} · passage {point.chunk_id}
      </span>
    </div>
  )
}
