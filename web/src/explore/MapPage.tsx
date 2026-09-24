import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { ApiError, getCorpusMap, type CorpusMap, type MapPoint } from '../lib/api'
import {
  assignSwatches,
  caption,
  legendOrder,
  multiTopicCount,
  percent,
  topicCounts,
  type Swatch,
  type TopicCount,
} from '../lib/corpusmap'
import { hrefForSource, navigate } from '../lib/route'
import { AreasScreen, type MapActions } from './map/AreasScreen'
import { coloursOf, positionsOf, visibilityOf } from './map/geometry'
import { SwatchChip, topicLabel } from './map/HoverCard'
import { readPalette, type CanvasPalette } from './map/palette'
import { Plot2D } from './map/Plot2D'
import type { SceneHandle } from './map/Scene3D'

/*
 * three.js is over half a megabyte, and only this screen draws with it. Loaded
 * on first use, so Explore, a source page and Admin never download it, and a
 * browser that falls back to the flat view never does either.
 */
const Scene3D = lazy(() => import('./map/Scene3D').then((module) => ({ default: module.Scene3D })))

/** The sample sizes on offer. The largest is the API's ceiling. */
export const SAMPLES = [1000, 3000, 8000] as const
const DEFAULT_SAMPLE = 3000

type View = '3d' | '2d'
type Load =
  | { status: 'loading'; map?: CorpusMap }
  | { status: 'error'; message: string }
  | { status: 'ready'; map: CorpusMap }

type Mode = 'areas' | 'points'

/** `?view=points` opens the passage cloud; anything else is the areas. */
export function modeFromSearch(search: string): Mode {
  return new URLSearchParams(search).get('view') === 'points' ? 'points' : 'areas'
}

/**
 * The Map screen (task P6-34): the corpus as nested, named areas, with the
 * passage cloud (`P6-26`, `P6-29`) as a toggle inside it rather than a screen
 * of its own — the operator's "fewer screens, each useful".
 */
export function MapPage({ actions }: { actions?: MapActions } = {}) {
  const [mode, setMode] = useState<Mode>(() => modeFromSearch(window.location.search))

  useEffect(() => {
    const onPop = () => setMode(modeFromSearch(window.location.search))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  function choose(next: Mode) {
    const search = new URLSearchParams(window.location.search)
    if (next === 'points') search.set('view', 'points')
    else search.delete('view')
    const query = search.toString()
    navigate(`/map${query ? `?${query}` : ''}`)
    setMode(next)
  }

  return (
    <Workspace>
      <nav
        aria-label="Map views"
        className="flex h-11 shrink-0 items-center gap-[22px] border-b border-line bg-surface px-5"
      >
        <span className="font-mono text-[9.5px] uppercase tracking-[var(--tracking-label)] text-text-faint">Map</span>
        {(
          [
            ['areas', 'Areas'],
            ['points', 'Passages in 3D'],
          ] as const
        ).map(([value, label]) => (
          <a
            key={value}
            href={value === 'points' ? '/map?view=points' : '/map'}
            aria-current={mode === value ? 'page' : undefined}
            onClick={(event) => {
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return
              event.preventDefault()
              choose(value)
            }}
            className={`border-b-2 px-0.5 pb-[11px] pt-3 text-[13px] no-underline ${
              mode === value ? 'border-accent-graph text-text' : 'border-transparent text-text-muted hover:text-text'
            }`}
          >
            {label}
          </a>
        ))}
      </nav>
      {mode === 'areas' ? <AreasScreen actions={actions} /> : <PointsPage />}
    </Workspace>
  )
}

/**
 * The passage cloud (tasks P6-26, P6-29): every sampled passage placed by its
 * embedding, in three dimensions by default and two on request.
 *
 * What it is for is seeing the corpus as the vector arm sees it — whether
 * topics separate, where one source has piled up, what a crawl wandered into.
 * What it is not is the embedding space itself, and the caption says how much
 * of that space the axes carry, so the picture cannot pass for more than a
 * shadow of it.
 */
export function PointsPage() {
  const [sample, setSample] = useState<number>(DEFAULT_SAMPLE)
  const [state, setState] = useState<Load>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    // Keep the previous picture on screen while the next one projects, so
    // changing the sample does not blank the canvas.
    setState((current) => ({ status: 'loading', map: current.status === 'error' ? undefined : current.map }))
    getCorpusMap({ sample }, { signal: controller.signal })
      .then((map) => setState({ status: 'ready', map }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setState({
          status: 'error',
          message: error instanceof ApiError ? error.message : 'The map did not load.',
        })
      })
    return () => controller.abort()
  }, [sample])

  if (state.status === 'error') {
    return (
      <Inner>
        <p className="p-6 text-text">{state.message}</p>
      </Inner>
    )
  }
  if (!state.map) {
    return (
      <Inner>
        <p className="p-6 font-mono text-[length:var(--text-data)] text-text-muted">Projecting the corpus.</p>
      </Inner>
    )
  }
  return (
    <MapView
      map={state.map}
      sample={sample}
      onSample={setSample}
      projecting={state.status === 'loading'}
    />
  )
}

/** Full width and full height under the top bar; the shell supplies the bar. */
function Workspace({ children }: { children: React.ReactNode }) {
  return <div className="flex h-[calc(100dvh-54px)] min-h-[480px] w-full flex-col bg-ground">{children}</div>
}

/** The cloud's frame inside the Map screen: whatever the sub-bar leaves. */
function Inner({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-0 w-full flex-1 flex-col bg-ground">{children}</div>
}

/** Can this browser draw WebGL at all? Asked once, without keeping a context. */
export function detectWebGL(): boolean {
  try {
    const canvas = document.createElement('canvas')
    const context = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    if (!context) return false
    ;(context as WebGLRenderingContext).getExtension('WEBGL_lose_context')?.loseContext()
    return true
  } catch {
    return false
  }
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

/**
 * Everything but the fetch, so it renders the same in a test as on the page.
 *
 * ``webgl`` is for tests and for the one caller that already knows; left out,
 * the page asks the browser after mounting.
 */
export function MapView({
  map,
  sample = DEFAULT_SAMPLE,
  onSample,
  projecting = false,
  webgl: knownWebGL,
}: {
  map: CorpusMap
  sample?: number
  onSample?: (sample: number) => void
  projecting?: boolean
  webgl?: boolean
}) {
  const swatches = useMemo(() => assignSwatches(map.points), [map.points])
  const counts = useMemo(() => topicCounts(map.points), [map.points])
  const multi = useMemo(() => multiTopicCount(map.points), [map.points])
  const [hidden, setHidden] = useState<ReadonlySet<string | null>>(new Set())
  const [view, setView] = useState<View>('3d')
  const [webgl, setWebgl] = useState<boolean | undefined>(knownWebGL)
  const [table, setTable] = useState(true)
  const [palette, setPalette] = useState<CanvasPalette | null>(null)
  const canvasArea = useRef<HTMLDivElement>(null)
  const scene = useRef<SceneHandle>(null)
  const tick = useThemeTick()

  useEffect(() => {
    if (knownWebGL === undefined) setWebgl(detectWebGL())
  }, [knownWebGL])

  // Colours are read from the canvas area itself, which is pinned to the dark
  // theme, so the dots are always the series values published for a dark ground.
  useEffect(() => {
    if (canvasArea.current) setPalette(readPalette(canvasArea.current, swatches))
  }, [swatches, tick])

  const visiblePoints = useMemo(() => map.points.filter((p) => !hidden.has(p.topic)), [map.points, hidden])
  const positions = useMemo(() => positionsOf(map.points), [map.points])
  const visible = useMemo(() => visibilityOf(map.points, hidden), [map.points, hidden])
  const colours = useMemo(
    () => (palette ? coloursOf(map.points, palette.topics, palette.neutral) : new Float32Array(map.points.length * 3)),
    [map.points, palette],
  )

  const open = useCallback((point: MapPoint) => navigate(hrefForSource(point.source_id)), [])
  const unavailable = useCallback(() => setWebgl(false), [])

  function toggle(topic: string | null) {
    setHidden((current) => {
      const next = new Set(current)
      if (next.has(topic)) next.delete(topic)
      else next.add(topic)
      return next
    })
  }

  const flat = view === '2d' || webgl === false
  const empty = map.points.length === 0

  return (
    <Inner>
      <header className="flex items-center gap-x-5 border-b border-line bg-surface px-[18px] py-2">
        <div className="flex shrink-0 flex-col">
          <h1 className="text-[length:var(--text-subhead)] font-semibold leading-[var(--leading-subhead)] text-text">
            Corpus map
          </h1>
          {empty ? null : (
            <p className="font-mono text-[10.5px] leading-[1.5] text-text-faint" aria-live="polite">
              {caption(map, flat ? 2 : 3)}
            </p>
          )}
        </div>

        {empty ? null : (
          <Legend swatches={swatches} counts={counts} hidden={hidden} onToggle={toggle} />
        )}

        {empty ? null : (
          <div className="ml-auto flex shrink-0 items-center gap-2">
            <Segmented
              label="View"
              value={flat ? '2d' : '3d'}
              options={[
                { value: '3d', label: '3D', disabled: webgl === false },
                { value: '2d', label: '2D' },
              ]}
              onChange={(value) => setView(value as View)}
            />
            <button
              type="button"
              aria-expanded={table}
              aria-controls="map-table"
              onClick={() => setTable((t) => !t)}
              className={`h-8 border px-3 text-[12px] ${
                table ? 'border-line-strong bg-surface-raised text-text' : 'border-line text-text-muted'
              }`}
            >
              Table
            </button>
          </div>
        )}
      </header>

      {empty ? (
        <div className="flex-1 p-6">
          <p className="text-text">
            Nothing to draw yet. Passages appear here once they are embedded, and none are.
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          {/* The canvas stays dark in both themes, as the graph canvas does
              (§2 publishes its colours for dark only). Pinning the theme here
              makes every token inside — series, surface, text — resolve to its
              dark value, so the dots, the hover card and the overlays agree. */}
          <div ref={canvasArea} data-theme="dark" className="relative min-w-0 flex-1 bg-ground-deep text-text">
            {palette && !flat && webgl ? (
              <Suspense
                fallback={
                  <p className="absolute left-[18px] top-4 font-mono text-[10.5px] text-text-faint">
                    Loading the 3D view.
                  </p>
                }
              >
                <Scene3D
                  ref={scene}
                  points={map.points}
                  positions={positions}
                  colours={colours}
                  visible={visible}
                  palette={palette}
                  swatches={swatches}
                  shares={map.explained_variance}
                  onOpen={open}
                  onUnavailable={unavailable}
                />
              </Suspense>
            ) : null}
            {palette && flat ? (
              <Plot2D
                points={visiblePoints}
                palette={palette}
                swatches={swatches}
                shares={map.explained_variance}
                onOpen={open}
              />
            ) : null}

            {webgl === false ? (
              <p className="absolute bottom-11 left-[18px] max-w-sm border border-line bg-surface px-3 py-2 text-[length:var(--text-small)] text-text-muted">
                This browser offers no WebGL, so the map is drawn flat: the first two axes only.
              </p>
            ) : null}
            {projecting ? (
              <p className="absolute right-[18px] top-4 font-mono text-[10.5px] text-text-faint">
                Projecting {sample.toLocaleString('en')} passages.
              </p>
            ) : null}

            <p className="pointer-events-none absolute bottom-4 left-[18px] font-mono text-[10.5px] text-text-faint">
              {flat
                ? 'hover a dot to read it · click to open its source'
                : 'drag to turn · scroll to zoom · right-drag to pan · click a dot to open its source'}
            </p>
            {!flat ? (
              <div className="absolute bottom-4 right-[18px] flex gap-2">
                <CanvasButton onClick={() => scene.current?.fit()}>Fit</CanvasButton>
                <CanvasButton label="Zoom out" onClick={() => scene.current?.zoom(1.25)}>
                  &minus;
                </CanvasButton>
                <CanvasButton label="Zoom in" onClick={() => scene.current?.zoom(0.8)}>
                  +
                </CanvasButton>
              </div>
            ) : null}
          </div>

          {table ? (
            <TablePanel
              counts={counts}
              multi={multi}
              total={map.points.length}
              swatches={swatches}
              shares={map.explained_variance}
              flat={flat}
              sample={sample}
              onSample={onSample}
            />
          ) : null}
        </div>
      )}
    </Inner>
  )
}

function CanvasButton({
  onClick,
  label,
  children,
}: {
  onClick: () => void
  label?: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="min-w-8 border border-line-strong bg-surface-raised px-3 py-[7px] text-[12.5px] leading-none text-text-muted hover:text-text"
    >
      {children}
    </button>
  )
}

function Segmented({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: { value: string; label: string; disabled?: boolean }[]
  onChange: (value: string) => void
}) {
  return (
    <div role="group" aria-label={label} className="flex self-start border border-line">
      {options.map((option) => {
        const on = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            className={`h-8 border-r border-line px-3 font-mono text-[11px] last:border-r-0 disabled:cursor-not-allowed disabled:opacity-40 ${
              on ? 'bg-surface-raised text-text' : 'text-text-faint hover:text-text-muted'
            }`}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

function Legend({
  swatches,
  counts,
  hidden,
  onToggle,
}: {
  swatches: Map<string | null, Swatch>
  counts: TopicCount[]
  hidden: ReadonlySet<string | null>
  onToggle: (topic: string | null) => void
}) {
  // In name order rather than by size, the order colours were given in, so
  // the legend does not reshuffle as topics grow.
  const rows = new Map(counts.map((row) => [row.topic, row]))

  return (
    <ul className="flex min-w-0 flex-1 flex-wrap gap-1.5" aria-label="Topics">
      {legendOrder(swatches.keys()).map((topic) => {
        const off = hidden.has(topic)
        const row = rows.get(topic)
        const drawn = row?.count ?? 0
        // Passages that carry the topic as a second or third subject, drawn in
        // another topic's colour (`P2-21`). Shown beside the colour count rather
        // than folded into it, so the colour counts still add up to the canvas.
        const also = (row?.carrying ?? drawn) - drawn
        return (
          <li key={topic ?? '∅'}>
            <button
              type="button"
              aria-pressed={!off}
              title={`${off ? 'Show' : 'Hide'} ${topicLabel(topic)} — ${drawn.toLocaleString('en')} drawn in this colour${
                also > 0 ? `, ${also.toLocaleString('en')} more carry it as another topic` : ''
              }`}
              onClick={() => onToggle(topic)}
              className={`flex h-7 items-center gap-2 rounded-[var(--radius-chip)] border px-2.5 text-[12px] ${
                off
                  ? 'border-line text-text-faint line-through'
                  : 'border-line bg-surface-raised text-text'
              }`}
            >
              <span data-theme="dark" className={off ? 'opacity-30' : undefined}>
                <SwatchChip swatch={swatches.get(topic)} />
              </span>
              {topicLabel(topic)}
              <span className="font-mono text-[10.5px] text-text-faint">
                {drawn.toLocaleString('en')}
                {also > 0 ? <span data-testid="legend-also"> +{also.toLocaleString('en')}</span> : null}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

/**
 * The same picture as numbers, for a reader who cannot see the canvas or does
 * not want to. Opaque, because it is read (§5): a table over a live canvas is
 * measurably harder to read than one on its own surface.
 */
function TablePanel({
  counts,
  multi,
  total,
  swatches,
  shares,
  flat,
  sample,
  onSample,
}: {
  counts: TopicCount[]
  multi: number
  total: number
  swatches: Map<string | null, Swatch>
  shares: readonly number[]
  flat: boolean
  sample: number
  onSample?: (sample: number) => void
}) {
  const drawn = flat ? shares.slice(0, 2) : shares
  const together = drawn.reduce((a, b) => a + b, 0)
  return (
    <aside
      id="map-table"
      aria-label="Table view"
      className="flex w-[320px] shrink-0 flex-col overflow-y-auto border-l border-line bg-surface"
    >
      <section className="flex flex-col gap-3 border-b border-line px-5 py-4">
        <h2 className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
          Topics · passages drawn
        </h2>
        <table className="w-full text-[12.5px]">
          <thead className="sr-only">
            <tr>
              <th>Topic</th>
              <th>Passages drawn in its colour</th>
              <th>Passages carrying it, where more</th>
              <th>Share</th>
            </tr>
          </thead>
          <tbody>
            {counts.map(({ topic, count, carrying }) => (
              <tr key={topic ?? '∅'} className="border-b border-line last:border-b-0">
                <td className="py-1.5 pr-2 text-text">
                  <span className="flex items-center gap-2">
                    <span data-theme="dark">
                      <SwatchChip swatch={swatches.get(topic)} />
                    </span>
                    {topicLabel(topic)}
                  </span>
                </td>
                <td className="py-1.5 text-right font-mono text-[10.5px] text-text-muted">{count.toLocaleString('en')}</td>
                <td
                  className="w-14 py-1.5 text-right font-mono text-[10.5px] text-text-faint"
                  title="Passages carrying this topic in any position"
                >
                  {carrying > count ? `+${(carrying - count).toLocaleString('en')}` : ''}
                </td>
                <td className="w-14 py-1.5 text-right font-mono text-[10.5px] text-text-faint">{percent(count / total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {multi > 0 ? (
          <p className="font-mono text-[10.5px] leading-[1.5] text-text-faint" data-testid="multi-topic-note">
            {multi.toLocaleString('en')} {multi === 1 ? 'passage is' : 'passages are'} about more than one
            topic, each drawn in its first; +n counts them under the others.
          </p>
        ) : null}
      </section>

      <section className="flex flex-col gap-3 border-b border-line px-5 py-4">
        <h2 className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
          Axes · share of the variation
        </h2>
        <ul className="flex flex-col gap-2">
          {drawn.map((share, axis) => (
            <li key={axis} className="flex items-center gap-3">
              <span className="w-8 font-mono text-[10.5px] text-text-muted">PC{axis + 1}</span>
              {/* On a track of 100%, not of the largest share: a bar that
                  filled its track would say the axis shows the whole space. */}
              <span className="h-1.5 flex-1 bg-surface-raised">
                <span className="block h-full bg-accent-graph" style={{ width: `${Math.min(100, share * 100)}%` }} />
              </span>
              <span className="w-12 text-right font-mono text-[10.5px] text-text">{percent(share)}</span>
            </li>
          ))}
        </ul>
        <p className="text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
          {drawn.length === 3 ? 'Three' : 'Two'} axes carry {percent(together)} of how the passages differ. The rest
          lies along directions this picture cannot show, so two dots that look close may not be, and a
          gap is more trustworthy than a cluster.
        </p>
      </section>

      {onSample ? (
        <section className="flex flex-col gap-3 px-5 py-4">
          <h2 className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
            Sample
          </h2>
          <Segmented
            label="Sample size"
            value={String(sample)}
            options={SAMPLES.map((n) => ({ value: String(n), label: n.toLocaleString('en') }))}
            onChange={(value) => onSample(Number(value))}
          />
          <p className="text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
            Passages are chosen by a fixed hash of their id, so the same corpus gives the same
            picture and a larger sample contains every passage of a smaller one. The axes are
            recomputed from whichever sample is drawn, so its dots shift a little between sizes.
          </p>
        </section>
      ) : null}
    </aside>
  )
}
