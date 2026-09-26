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
      title="Scroll, pinch or press + and − to change the level"
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

/** Wheel movement, in pixels, that makes one step of level. A mouse notch is about 100. */
const WHEEL_STEP = 80
/** After a step, gestures are ignored this long, so one flick is one level. */
const STEP_LOCK_MS = 420

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

/**
 * Wheel, trackpad pinch (a wheel event with ctrl held), two-finger touch
 * pinch, and + / − on the keyboard, each as one step in or out. Scrolling
 * towards the reader, or spreading two fingers, is "in": more detail.
 */
export function useZoomGestures(
  ref: React.RefObject<HTMLElement | null>,
  onStep: (direction: 1 | -1) => void,
  active = true,
): void {
  const step = useRef(onStep)
  step.current = onStep

  useEffect(() => {
    const element = ref.current
    if (!element || !active) return
    let total = 0
    let quietSince = 0
    let lockedUntil = 0
    let pinchFrom = 0

    function fire(direction: 1 | -1) {
      lockedUntil = performance.now() + STEP_LOCK_MS
      total = 0
      step.current(direction)
    }

    function onWheel(event: WheelEvent) {
      event.preventDefault()
      const now = performance.now()
      if (now - quietSince > 250) total = 0
      quietSince = now
      if (now < lockedUntil) return
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? 400 : 1
      // A pinch reports small deltas; weight it so a deliberate pinch is a step.
      total += event.deltaY * unit * (event.ctrlKey ? 8 : 1)
      if (Math.abs(total) >= WHEEL_STEP) fire(total < 0 ? 1 : -1)
    }

    function spread(touches: TouchList): number {
      const a = touches[0]!
      const b = touches[1]!
      return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY)
    }
    function onTouchStart(event: TouchEvent) {
      pinchFrom = event.touches.length === 2 ? spread(event.touches) : 0
    }
    function onTouchMove(event: TouchEvent) {
      if (event.touches.length !== 2 || pinchFrom <= 0) return
      if (performance.now() < lockedUntil) return
      const ratio = spread(event.touches) / pinchFrom
      if (ratio > 1.3 || ratio < 0.77) {
        pinchFrom = spread(event.touches)
        fire(ratio > 1 ? 1 : -1)
      }
    }

    function onKey(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey || typing(event.target)) return
      if (event.key === '+' || event.key === '=') fire(1)
      else if (event.key === '-' || event.key === '_') fire(-1)
    }

    element.addEventListener('wheel', onWheel, { passive: false })
    element.addEventListener('touchstart', onTouchStart, { passive: true })
    element.addEventListener('touchmove', onTouchMove, { passive: true })
    window.addEventListener('keydown', onKey)
    return () => {
      element.removeEventListener('wheel', onWheel)
      element.removeEventListener('touchstart', onTouchStart)
      element.removeEventListener('touchmove', onTouchMove)
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
