import { useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, getCorpusMap, type CorpusMap, type MapPoint } from '../lib/api'
import {
  assignSwatches,
  nearest,
  percent,
  swatchVar,
  toScreen,
  topicCounts,
  type Swatch,
} from '../lib/corpusmap'
import { hrefForSource, navigate } from '../lib/route'

type Load = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; map: CorpusMap }

const PADDING = 16
const DOT = 2.5
const HIT_RADIUS = 8

/**
 * The corpus map (task P6-26): every sampled passage placed by its embedding.
 *
 * What it is for is seeing the corpus as the vector arm sees it — whether
 * topics separate, where one source has piled up, what a crawl wandered into.
 * What it is not is the embedding space itself, and the caption says how much
 * of that space the two axes carry, so the picture cannot pass for more than a
 * shadow of it.
 */
export function MapPage() {
  const [state, setState] = useState<Load>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    getCorpusMap({}, { signal: controller.signal })
      .then((map) => setState({ status: 'ready', map }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setState({
          status: 'error',
          message: error instanceof ApiError ? error.message : 'The map did not load.',
        })
      })
    return () => controller.abort()
  }, [])

  if (state.status === 'loading') {
    return <p className="text-text-muted">Projecting the corpus.</p>
  }
  if (state.status === 'error') return <p className="text-text">{state.message}</p>
  return <MapView map={state.map} />
}

/** Everything but the fetch, so it renders the same in a test as on the page. */
export function MapView({ map }: { map: CorpusMap }) {
  const swatches = useMemo(() => assignSwatches(map.points), [map.points])
  const counts = useMemo(() => topicCounts(map.points), [map.points])
  const [hidden, setHidden] = useState<ReadonlySet<string | null>>(new Set())

  const visible = useMemo(
    () => map.points.filter((p) => !hidden.has(p.topic)),
    [map.points, hidden],
  )

  function toggle(topic: string | null) {
    setHidden((current) => {
      const next = new Set(current)
      if (next.has(topic)) next.delete(topic)
      else next.add(topic)
      return next
    })
  }

  const [first, second] = map.explained_variance
  const sampled = map.points.length < map.eligible

  return (
    <div>
      <h1 className="font-sans text-[length:var(--text-display)] font-semibold leading-[var(--leading-display)] tracking-[var(--tracking-display)]">
        Corpus map
      </h1>
      <p className="mt-2 max-w-prose text-text-muted">
        Each dot is a passage, placed by its embedding. Near means the vector search would call
        them similar; the colour is the topic its source was collected under. Hover to read one,
        click to open its source.
      </p>

      {map.points.length === 0 ? (
        <p className="mt-6 text-text">
          Nothing to draw yet. Passages appear here once they are embedded, and none are.
        </p>
      ) : (
        <>
          <p className="mt-4 font-mono text-[length:var(--text-data)] text-text-faint">
            {sampled
              ? `${map.points.length.toLocaleString()} of ${map.eligible.toLocaleString()} passages, sampled`
              : `${map.points.length.toLocaleString()} passages`}
            {' · '}
            {`axes carry ${percent(first)} and ${percent(second)} of the variation`}
          </p>

          <Legend swatches={swatches} counts={counts} hidden={hidden} onToggle={toggle} />
          <Plot points={visible} swatches={swatches} />
          <TableView counts={counts} total={map.points.length} />
        </>
      )}
    </div>
  )
}

function label(topic: string | null): string {
  return topic ?? 'Unlabelled'
}

function Chip({ swatch }: { swatch: Swatch | undefined }) {
  const colour = swatch ? `var(${swatchVar(swatch)})` : 'var(--text-faint)'
  return <span aria-hidden className="inline-block size-2.5 rounded-full" style={{ background: colour }} />
}

function Legend({
  swatches,
  counts,
  hidden,
  onToggle,
}: {
  swatches: Map<string | null, Swatch>
  counts: { topic: string | null; count: number }[]
  hidden: ReadonlySet<string | null>
  onToggle: (topic: string | null) => void
}) {
  // In colour order rather than by size, so the legend reads the same way the
  // colours were assigned and does not reshuffle as topics grow.
  const ordered = [...swatches.keys()].sort((a, b) => {
    if (a === null) return 1
    if (b === null) return -1
    return a.localeCompare(b)
  })
  const count = new Map(counts.map((row) => [row.topic, row.count]))

  return (
    <ul className="mt-4 flex flex-wrap gap-2" aria-label="Topics">
      {ordered.map((topic) => {
        const off = hidden.has(topic)
        return (
          <li key={topic ?? '∅'}>
            <button
              type="button"
              aria-pressed={!off}
              onClick={() => onToggle(topic)}
              className={`flex h-[var(--control-height)] items-center gap-2 border border-line bg-surface-raised px-3 text-[length:var(--text-small)] ${
                off ? 'text-text-faint line-through' : 'text-text'
              }`}
            >
              <Chip swatch={swatches.get(topic)} />
              {label(topic)}
              <span className="font-mono text-text-faint">{count.get(topic) ?? 0}</span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/** Redraw when the theme changes: the dots read their colours from CSS roles. */
function useThemeTick(): number {
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const bump = () => setTick((t) => t + 1)
    const observer = new MutationObserver(bump)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const media = window.matchMedia?.('(prefers-color-scheme: light)')
    media?.addEventListener('change', bump)
    return () => {
      observer.disconnect()
      media?.removeEventListener('change', bump)
    }
  }, [])
  return tick
}

function Plot({ points, swatches }: { points: MapPoint[]; swatches: Map<string | null, Swatch> }) {
  const frame = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [hover, setHover] = useState<{ point: MapPoint; at: [number, number] } | null>(null)
  const tick = useThemeTick()

  useEffect(() => {
    const element = frame.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry!.contentRect.width)
      // Square up to a height that still fits a laptop screen with the legend.
      setSize({ width, height: Math.min(width, 720) })
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
    context.clearRect(0, 0, size.width, size.height)

    const style = getComputedStyle(document.documentElement)
    const colours = new Map<string | null, string>()
    for (const [topic, swatch] of swatches) {
      colours.set(topic, style.getPropertyValue(swatchVar(swatch)).trim())
    }

    context.globalAlpha = 0.8
    for (const point of points) {
      const [x, y] = toScreen(point, size.width, size.height, PADDING)
      context.fillStyle = colours.get(point.topic) ?? style.getPropertyValue('--text-faint')
      context.beginPath()
      context.arc(x, y, DOT, 0, Math.PI * 2)
      context.fill()
    }
    context.globalAlpha = 1

    if (hover) {
      const [x, y] = toScreen(hover.point, size.width, size.height, PADDING)
      context.strokeStyle = style.getPropertyValue('--text').trim()
      context.lineWidth = 2
      context.beginPath()
      context.arc(x, y, DOT + 4, 0, Math.PI * 2)
      context.stroke()
    }
  }, [points, swatches, size, hover, tick])

  function locate(event: React.MouseEvent<HTMLCanvasElement>): MapPoint | null {
    const box = event.currentTarget.getBoundingClientRect()
    const cursor: [number, number] = [event.clientX - box.left, event.clientY - box.top]
    return nearest(points, cursor, (p) => toScreen(p, size.width, size.height, PADDING), HIT_RADIUS)
  }

  return (
    <div ref={frame} className="relative mt-4 border border-line bg-surface">
      <canvas
        ref={canvas}
        role="img"
        aria-label={`A scatter of ${points.length} passages by embedding similarity. The table below lists the same data by topic.`}
        style={{ width: size.width, height: size.height, cursor: hover ? 'pointer' : 'default' }}
        onMouseMove={(event) => {
          const point = locate(event)
          const box = event.currentTarget.getBoundingClientRect()
          setHover(point ? { point, at: [event.clientX - box.left, event.clientY - box.top] } : null)
        }}
        onMouseLeave={() => setHover(null)}
        onClick={(event) => {
          const point = locate(event)
          if (point) navigate(hrefForSource(point.source_id))
        }}
      />
      {hover ? <HoverCard hover={hover} swatch={swatches.get(hover.point.topic)} width={size.width} /> : null}
    </div>
  )
}

function HoverCard({
  hover,
  swatch,
  width,
}: {
  hover: { point: MapPoint; at: [number, number] }
  swatch: Swatch | undefined
  width: number
}) {
  const { point, at } = hover
  // Flip to the cursor's left near the right edge, so the card is never cut off.
  const left = at[0] + 300 > width ? Math.max(0, at[0] - 312) : at[0] + 12
  return (
    <div
      className="pointer-events-none absolute w-[300px] border border-line-strong bg-surface-raised p-3 shadow-lg"
      style={{ left, top: at[1] + 12 }}
    >
      <p className="flex items-center gap-2 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-faint">
        <Chip swatch={swatch} />
        {label(point.topic)}
      </p>
      <p className="mt-1 font-semibold text-text">{point.title ?? point.url}</p>
      <p className="mt-1 line-clamp-4 text-[length:var(--text-small)] text-text-muted">
        {point.snippet}
      </p>
    </div>
  )
}

function TableView({
  counts,
  total,
}: {
  counts: { topic: string | null; count: number }[]
  total: number
}) {
  return (
    <details className="mt-4">
      <summary className="cursor-pointer text-[length:var(--text-small)] text-text-muted">
        Table view
      </summary>
      <table className="mt-2 w-full max-w-md text-[length:var(--text-small)]">
        <thead>
          <tr className="border-b border-line-strong text-left text-text-muted">
            <th className="py-1 font-normal">Topic</th>
            <th className="py-1 text-right font-normal">Passages</th>
            <th className="py-1 text-right font-normal">Share</th>
          </tr>
        </thead>
        <tbody>
          {counts.map(({ topic, count }) => (
            <tr key={topic ?? '∅'} className="border-b border-line">
              <td className="py-1">{label(topic)}</td>
              <td className="py-1 text-right font-mono">{count.toLocaleString()}</td>
              <td className="py-1 text-right font-mono">{percent(count / total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  )
}
