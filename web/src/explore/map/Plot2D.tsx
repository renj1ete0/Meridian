import { useEffect, useRef, useState } from 'react'

import type { MapPoint } from '../../lib/api'
import { nearest, percent, toScreen, type Swatch } from '../../lib/corpusmap'
import { HoverCard } from './HoverCard'
import { css, type CanvasPalette } from './palette'

const PADDING = 44
const DOT = 2.5
const HIT_RADIUS = 8
/** Graticule cells per side of the square plot, as on the graph canvas. */
const CELLS = 4

/**
 * The flat map (task P6-26, restyled in P6-29): the first two axes only, on a 2D canvas,
 * kept beside the 3D view. See docs/features/map.md#the-passage-cloud.
 */
export function Plot2D({
  points,
  palette,
  swatches,
  shares,
  onOpen,
}: {
  points: readonly MapPoint[]
  palette: CanvasPalette
  swatches: ReadonlyMap<string | null, Swatch>
  shares: readonly number[]
  onOpen: (point: MapPoint) => void
}) {
  const frame = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [hover, setHover] = useState<{ point: MapPoint; at: [number, number] } | null>(null)

  useEffect(() => {
    const element = frame.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: Math.floor(entry!.contentRect.width), height: Math.floor(entry!.contentRect.height) })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const element = canvas.current
    const context = element?.getContext('2d')
    if (!element || !context || size.width === 0) return

    const ratio = window.devicePixelRatio || 1
    element.width = size.width * ratio
    element.height = size.height * ratio
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
    context.fillStyle = css(palette.ground)
    context.fillRect(0, 0, size.width, size.height)

    // The graticule and the two axes, on the same square the dots are placed in.
    const [left, top] = toScreen({ x: -1, y: 1 }, size.width, size.height, PADDING)
    const [right, bottom] = toScreen({ x: 1, y: -1 }, size.width, size.height, PADDING)
    context.lineWidth = 1
    context.strokeStyle = css(palette.graticule)
    context.beginPath()
    for (let i = 0; i <= CELLS; i++) {
      const x = Math.round(left + ((right - left) * i) / CELLS) + 0.5
      const y = Math.round(top + ((bottom - top) * i) / CELLS) + 0.5
      context.moveTo(x, top)
      context.lineTo(x, bottom)
      context.moveTo(left, y)
      context.lineTo(right, y)
    }
    context.stroke()
    context.strokeStyle = css(palette.axis)
    context.beginPath()
    const [cx, cy] = toScreen({ x: 0, y: 0 }, size.width, size.height, PADDING)
    context.moveTo(left, Math.round(cy) + 0.5)
    context.lineTo(right, Math.round(cy) + 0.5)
    context.moveTo(Math.round(cx) + 0.5, top)
    context.lineTo(Math.round(cx) + 0.5, bottom)
    context.stroke()

    context.globalAlpha = 0.85
    for (const point of points) {
      const [x, y] = toScreen(point, size.width, size.height, PADDING)
      context.fillStyle = css(palette.topics.get(point.topic) ?? palette.neutral)
      context.beginPath()
      context.arc(x, y, DOT, 0, Math.PI * 2)
      context.fill()
    }
    context.globalAlpha = 1

    if (hover) {
      const [x, y] = toScreen(hover.point, size.width, size.height, PADDING)
      context.strokeStyle = css(palette.topics.get(hover.point.topic) ?? palette.neutral)
      context.lineWidth = 2
      context.beginPath()
      context.arc(x, y, DOT + 4, 0, Math.PI * 2)
      context.stroke()
    }
  }, [points, palette, size, hover])

  function locate(event: React.MouseEvent<HTMLCanvasElement>): [MapPoint | null, [number, number]] {
    const box = event.currentTarget.getBoundingClientRect()
    const cursor: [number, number] = [event.clientX - box.left, event.clientY - box.top]
    const point = nearest(points, cursor, (p) => toScreen(p, size.width, size.height, PADDING), HIT_RADIUS)
    return [point, cursor]
  }

  const [right, middle] = toScreen({ x: 1, y: 0 }, size.width, size.height, PADDING)
  const [centre, top] = toScreen({ x: 0, y: 1 }, size.width, size.height, PADDING)

  return (
    <div ref={frame} className="absolute inset-0 overflow-hidden">
      <canvas
        ref={canvas}
        role="img"
        aria-label={`A flat scatter of ${points.length} passages on the first two axes. The table lists the same data by topic.`}
        style={{ width: size.width, height: size.height, cursor: hover ? 'pointer' : 'default', display: 'block' }}
        onPointerMove={(event) => {
          const [point, at] = locate(event)
          setHover(point ? { point, at } : null)
        }}
        onPointerLeave={() => setHover(null)}
        onClick={(event) => {
          const [point] = locate(event)
          if (point) onOpen(point)
        }}
      />
      {size.width > 0 ? (
        <>
          <AxisLabel at={[right + 10, middle - 7]}>PC1 · {percent(shares[0] ?? 0)}</AxisLabel>
          <AxisLabel at={[centre + 8, top - 20]}>PC2 · {percent(shares[1] ?? 0)}</AxisLabel>
        </>
      ) : null}
      {hover ? (
        <HoverCard point={hover.point} swatch={swatches.get(hover.point.topic)} at={hover.at} frame={size} />
      ) : null}
    </div>
  )
}

function AxisLabel({ at, children }: { at: [number, number]; children: React.ReactNode }) {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute whitespace-nowrap font-mono text-[9.5px] uppercase tracking-[0.12em] text-text-faint"
      style={{ top: at[1], left: at[0] }}
    >
      {children}
    </span>
  )
}
