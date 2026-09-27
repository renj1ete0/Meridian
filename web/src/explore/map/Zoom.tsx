import { useEffect, useLayoutEffect, useRef } from 'react'

import { easeInOut, levelNoun, morphAt, type Label, type MorphItem, type Placed } from '../../lib/areas'

/**
 * The Map's semantic zoom: a level control that is always on the canvas, the
 * wheel, pinch and keyboard gestures that step it, and the animation that
 * splits each circle into its children or gathers them back.
 *
 * Changing the level here never navigates into one circle; clicking a circle
 * still does, so this is a second way down rather than a replacement.
 */

/** How long a split or a merge takes. */
export const MORPH_MS = 520

/** Fields · Subfields · Themes, with − and + either side. */
export function LevelControl({
  levels,
  value,
  onChange,
}: {
  levels: number
  value: number
  onChange: (level: number) => void
}) {
  const options = Array.from({ length: levels }, (_, i) => i + 1)
  const step =
    'h-8 w-8 border-line text-[14px] leading-none text-text-muted hover:text-text disabled:cursor-not-allowed disabled:opacity-40'
  return (
    <div
      role="group"
      aria-label="Level of detail"
      title="Scroll or pinch to zoom, drag to move, + and − for a level, 0 or double-click for the whole map"
      className="flex shrink-0 items-stretch border border-line-strong bg-surface"
    >
      <button
        type="button"
        aria-label="Less detail"
        disabled={value <= 1}
        onClick={() => onChange(value - 1)}
        className={`${step} border-r`}
      >
        &minus;
      </button>
      {options.map((level) => {
        const on = level === value
        const label = levelNoun(level, 2)
        return (
          <button
            key={level}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(level)}
            className={`h-8 border-r border-line px-2.5 font-mono text-[11px] sm:px-3 ${
              on ? 'bg-surface-raised text-text' : 'text-text-faint hover:text-text-muted'
            }`}
          >
            {label.charAt(0).toUpperCase() + label.slice(1)}
          </button>
        )
      })}
      <button
        type="button"
        aria-label="More detail"
        disabled={value >= levels}
        onClick={() => onChange(value + 1)}
        className={step}
      >
        +
      </button>
    </div>
  )
}

/**
 * How much one pixel of wheel travel zooms. A mouse notch (about 100 px) is a
 * little under 1.2×; a trackpad's stream of small deltas is smooth; a pinch
 * on a trackpad arrives as a wheel with ctrl held and small deltas, so it is
 * weighted up to feel like a pinch.
 */
const WHEEL_RATE = 0.0017
const PINCH_WEIGHT = 6
/** Travel, in pixels, before a press becomes a drag rather than a click. */
const DRAG_SLOP = 4

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

export interface ZoomHandlers {
  /** Zoom by `factor` about (x, y), in the element's own pixels. */
  onZoom: (factor: number, x: number, y: number) => void
  /** Move the view by a drag of (dx, dy) pixels. */
  onPan: (dx: number, dy: number) => void
  /** + or −: one level of detail in or out. */
  onStep: (direction: 1 | -1) => void
  /** Back to the whole map. */
  onReset: () => void
}

/** The factor one wheel event zooms by: towards the reader is in. */
export function wheelFactor(deltaY: number, deltaMode: number, ctrlKey: boolean): number {
  const unit = deltaMode === 1 ? 16 : deltaMode === 2 ? 400 : 1
  const travel = Math.max(-300, Math.min(300, deltaY * unit * (ctrlKey ? PINCH_WEIGHT : 1)))
  return Math.exp(-travel * WHEEL_RATE)
}

/**
 * The Map's gestures (a map's, not a document's): the wheel and a pinch zoom
 * the view about the pointer, a drag pans it, + and − step the level of
 * detail, and a double-click on empty ground goes back to the whole map.
 * The level itself follows the zoom, so these are the only gestures needed.
 *
 * A drag must not also be a click: the press that starts it is usually on a
 * circle, and a circle's click opens it. So a drag past {@link DRAG_SLOP}
 * swallows the click that ends it.
 */
export function useZoomGestures(
  ref: React.RefObject<HTMLElement | null>,
  handlers: ZoomHandlers,
  active = true,
): void {
  const current = useRef(handlers)
  current.current = handlers

  useEffect(() => {
    const element = ref.current
    if (!element || !active) return
    let press: { id: number; x: number; y: number; dragging: boolean } | null = null
    let swallowClick = false
    const touches = new Map<number, { x: number; y: number }>()
    let pinch = 0

    const local = (clientX: number, clientY: number) => {
      const box = element.getBoundingClientRect()
      return { x: clientX - box.left, y: clientY - box.top }
    }

    function onWheel(event: WheelEvent) {
      event.preventDefault()
      const at = local(event.clientX, event.clientY)
      current.current.onZoom(wheelFactor(event.deltaY, event.deltaMode, event.ctrlKey), at.x, at.y)
    }

    function onPointerDown(event: PointerEvent) {
      if (event.pointerType === 'touch') {
        touches.set(event.pointerId, { x: event.clientX, y: event.clientY })
        if (touches.size === 2) {
          const [a, b] = [...touches.values()]
          pinch = Math.hypot(a!.x - b!.x, a!.y - b!.y)
          press = null
          return
        }
      }
      if (event.button !== 0 || touches.size > 1) return
      press = { id: event.pointerId, x: event.clientX, y: event.clientY, dragging: false }
    }

    function onPointerMove(event: PointerEvent) {
      if (event.pointerType === 'touch' && touches.has(event.pointerId)) {
        touches.set(event.pointerId, { x: event.clientX, y: event.clientY })
        if (touches.size === 2 && pinch > 0) {
          const [a, b] = [...touches.values()]
          const spread = Math.hypot(a!.x - b!.x, a!.y - b!.y)
          const mid = local((a!.x + b!.x) / 2, (a!.y + b!.y) / 2)
          current.current.onZoom(spread / pinch, mid.x, mid.y)
          pinch = spread
          return
        }
      }
      if (!press || press.id !== event.pointerId) return
      const dx = event.clientX - press.x
      const dy = event.clientY - press.y
      if (!press.dragging && Math.hypot(dx, dy) < DRAG_SLOP) return
      press.dragging = true
      press.x = event.clientX
      press.y = event.clientY
      current.current.onPan(dx, dy)
    }

    function onPointerUp(event: PointerEvent) {
      touches.delete(event.pointerId)
      if (touches.size < 2) pinch = 0
      if (press && press.id === event.pointerId) {
        swallowClick = press.dragging
        press = null
      }
    }

    function onClick(event: MouseEvent) {
      if (!swallowClick) return
      swallowClick = false
      event.stopPropagation()
      event.preventDefault()
    }

    function onDoubleClick(event: MouseEvent) {
      // Empty ground only: a circle's own double-click is two opens.
      if ((event.target as Element | null)?.closest?.('[data-area]')) return
      current.current.onReset()
    }

    function onKey(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return
      if (event.key === '+' || event.key === '=') current.current.onStep(1)
      else if (event.key === '-' || event.key === '_') current.current.onStep(-1)
      else if (event.key === '0') current.current.onReset()
    }

    element.addEventListener('wheel', onWheel, { passive: false })
    element.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    window.addEventListener('pointercancel', onPointerUp)
    element.addEventListener('click', onClick, true)
    element.addEventListener('dblclick', onDoubleClick)
    window.addEventListener('keydown', onKey)
    return () => {
      element.removeEventListener('wheel', onWheel)
      element.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerUp)
      element.removeEventListener('click', onClick, true)
      element.removeEventListener('dblclick', onDoubleClick)
      window.removeEventListener('keydown', onKey)
    }
  }, [ref, active])
}

/**
 * One split or merge, drawn outside React's render: the frame loop writes
 * each circle's attributes directly, so some four hundred circles move
 * without four hundred components re-rendering sixty times a second.
 *
 * The coarse level is drawn with its labels and fades as its children leave
 * it (or reappears as they gather back); the children carry no labels until
 * they settle, when the ordinary drawing takes over.
 */
export function MorphLayer({
  items,
  coarse,
  coarseLabels,
  split,
  onDone,
  duration = MORPH_MS,
}: {
  items: readonly MorphItem[]
  coarse: readonly Placed[]
  coarseLabels: ReadonlyMap<number, Label>
  split: boolean
  onDone: () => void
  duration?: number
}) {
  const circles = useRef<(SVGCircleElement | null)[]>([])
  const parents = useRef<SVGGElement>(null)
  const children = useRef<SVGGElement>(null)
  const done = useRef(onDone)
  done.current = onDone

  const first = morphAt(items, split ? 0 : 1)

  useLayoutEffect(() => {
    if (typeof requestAnimationFrame === 'undefined') {
      done.current()
      return
    }
    let frame = 0
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration)
      const e = easeInOut(t)
      const p = split ? e : 1 - e
      const state = morphAt(items, p)
      state.circles.forEach((c, i) => {
        const element = circles.current[i]
        if (!element) return
        element.setAttribute('cx', c.x.toFixed(1))
        element.setAttribute('cy', c.y.toFixed(1))
        element.setAttribute('r', Math.max(0, c.r).toFixed(2))
      })
      parents.current?.setAttribute('opacity', state.parentOpacity.toFixed(3))
      // The same curve both ways: gathering back, the children fade as they
      // arrive, just as they faded in on leaving.
      children.current?.setAttribute('opacity', state.childOpacity.toFixed(3))
      if (t < 1) frame = requestAnimationFrame(tick)
      else done.current()
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [items, split, duration])

  return (
    <g data-testid="morph" aria-hidden>
      <g ref={parents} opacity={first.parentOpacity}>
        {coarse.map((p) => {
          const label = coarseLabels.get(p.area.area_id)
          return (
            <g key={p.area.area_id}>
              <circle
                cx={p.x}
                cy={p.y}
                r={p.r}
                fill="var(--accent-graph-deep)"
                fillOpacity={0.3}
                stroke="var(--accent-graph)"
                strokeWidth={1.2}
              />
              {label?.lines.map((line, i) => (
                <text
                  key={i}
                  x={p.x}
                  y={label.y + i * label.lineHeight}
                  textAnchor="middle"
                  fontFamily="var(--font-sans)"
                  fontSize={label.fontSize}
                  fontWeight={600}
                  fill="var(--text)"
                  paintOrder="stroke"
                  stroke="var(--ground-deep)"
                  strokeWidth={3.5}
                >
                  {line}
                </text>
              ))}
            </g>
          )
        })}
      </g>
      <g ref={children} opacity={first.childOpacity}>
        {first.circles.map((c, i) => (
          <circle
            key={c.id}
            ref={(element) => {
              circles.current[i] = element
            }}
            cx={c.x}
            cy={c.y}
            r={Math.max(0, c.r)}
            fill="var(--accent-graph-deep)"
            fillOpacity={0.3}
            stroke="var(--accent-graph)"
            strokeWidth={1}
          />
        ))}
      </g>
    </g>
  )
}
