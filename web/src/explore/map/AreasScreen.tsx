import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { ApiError } from '../../lib/api'
import {
  OFF_TOPIC_BELOW,
  onTopicShare,
  shareFill,
  shareText,
  topicShares,
  AreaCache,
  ancestorAt,
  enclosing,
  fitLabels,
  getArea,
  getBridge,
  jumpToArea,
  keyValuesAtScale,
  levelFromSearch,
  levelNoun,
  linkWidth,
  loadedTo,
  placeAreas,
  placeNested,
  prefersReducedMotion,
  rowsAt,
  edgeToEdge,
  fitRadius,
  radiusOf,
  splitMorph,
  zoomCaption,
  routeBetween,
  HOME,
  depthForZoom,
  linksAt,
  onScreen,
  panBy,
  viewed,
  zoomAbout,
  zoomForDepth,
  shadeOf,
  type Shade,
  type View,
  type Area,
  type AreaDetail,
  type AreaJumpHit,
  type AreaLink,
  type AreasLevel,
  type Bridge,
  type Label,
  type Layout,
  type MorphItem,
  type Placed,
  type Rect,
} from '../../lib/areas'
import { readable } from '../../lib/readable'
import { hrefForSource, navigate, onInternalClick } from '../../lib/route'
import { LevelControl, MorphLayer, useZoomGestures } from './Zoom'
import { dayOf } from '../../lib/time'

/**
 * The Map screen's default view (task P6-34): the corpus as nested areas.
 *
 * On screen these are fields, subfields and themes (level 1, 2, 3); the code,
 * the API and the URL keep the older word "area", so old links still open.
 *
 * One level at a time — fields, then the subfields inside one field, then its
 * themes — so the picture stays readable whatever the corpus holds. Circle
 * *area* is passages collected, with a key drawn at the same scale; an amber
 * outline marks an area that is weak (few independent sources) or stale
 * (nothing new stored for months), and its reasons are in words on hover and
 * in the panel. Lines are bridges: solid where a claim in the graph is cited
 * across the two, dashed where the nearest passages are merely similar.
 *
 * **A field is a cluster, not a topic**, and the screen says so where it
 * lists them: the server names each cluster for the field of work its
 * passages read as; topics are a filter over the clusters, not their bounds.
 */

/** What the context menu can do beyond reading; wired by P6-35. */
export interface MapActions {
  /** Extra menu items for an area, rendered above "Open in Find". */
  areaItems?: (area: Area, close: () => void) => React.ReactNode
  /** What right-clicking empty canvas opens, if anything. */
  emptySpace?: (at: { x: number; y: number }, near: Area[], close: () => void) => React.ReactNode
}

type Load =
  | { status: 'loading'; level?: AreasLevel }
  | { status: 'error'; message: string }
  | { status: 'ready'; level: AreasLevel }

type Panel =
  | { kind: 'none' }
  | { kind: 'area'; areaId: number; detail?: AreaDetail; error?: string }
  | { kind: 'bridge'; a: number; b: number; bridge?: Bridge; error?: string }
  | { kind: 'route'; from: Area; to: Area; hops: AreaLink[] | null }

type Menu = { kind: 'area'; area: Area; x: number; y: number } | { kind: 'empty'; x: number; y: number } | null

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

/** `?area=` in the URL, so a level can be linked and Back walks up. */
export function areaFromSearch(search: string): number | null {
  const value = new URLSearchParams(search).get('area')
  const n = value ? Number(value) : NaN
  return Number.isInteger(n) && n > 0 ? n : null
}

/** The URL for a root (`?area=`) and a level of detail (`?level=`), other parameters kept. */
export function mapHref(search: string, areaId: number | null, depth: number | null): string {
  const params = new URLSearchParams(search)
  if (areaId === null) params.delete('area')
  else params.set('area', String(areaId))
  if (depth === null) params.delete('level')
  else params.set('level', String(depth))
  const query = params.toString()
  return `/map${query ? `?${query}` : ''}`
}

const NO_CHILDREN: ReadonlyMap<number, AreasLevel> = new Map()

export function AreasScreen({ actions, cache: given }: { actions?: MapActions; cache?: AreaCache }) {
  const [parent, setParent] = useState<number | null>(() => areaFromSearch(window.location.search))
  // The level of detail asked for; null is the root's own level.
  const [depth, setDepth] = useState<number | null>(() => levelFromSearch(window.location.search))
  const [load, setLoad] = useState<Load>({ status: 'loading' })
  const cache = useMemo(() => given ?? new AreaCache(), [given])
  const [tree, setTree] = useState<{ root: AreasLevel; byParent: Map<number, AreasLevel> } | null>(null)
  const [zoomError, setZoomError] = useState<string | null>(null)

  useEffect(() => {
    const onPop = () => {
      setParent(areaFromSearch(window.location.search))
      setDepth(levelFromSearch(window.location.search))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    let alive = true
    setLoad((current) => ({ status: 'loading', level: current.status === 'ready' ? current.level : undefined }))
    cache
      .get(parent)
      .then((level) => alive && setLoad({ status: 'ready', level }))
      .catch((cause: unknown) => {
        if (alive) setLoad({ status: 'error', message: messageOf(cause, 'The fields did not load.') })
      })
    return () => {
      alive = false
    }
  }, [parent, cache])

  // The finer levels under the root, each parent's children fetched once.
  const root = load.status === 'ready' ? load.level : null
  useEffect(() => {
    if (!root || root.build === null) return
    const want = Math.min(depth ?? root.level, root.levels)
    if (want <= root.level) return
    let alive = true
    setZoomError(null)
    cache
      .descend(root, want)
      .then((byParent) => {
        if (!alive) return
        setTree((current) => {
          if (!current || current.root !== root) return { root, byParent }
          // Nothing new (a level already loaded, asked for again): keep the
          // same map, or every level's layout would be computed afresh.
          if ([...byParent.keys()].every((id) => current.byParent.has(id))) return current
          return { root, byParent: new Map([...current.byParent, ...byParent]) }
        })
      })
      .catch((cause: unknown) => {
        if (alive) setZoomError(messageOf(cause, 'The finer level did not load.'))
      })
    return () => {
      alive = false
    }
  }, [root, depth, cache])

  const goTo = useCallback((areaId: number | null) => {
    navigate(mapHref(window.location.search, areaId, null))
    setParent(areaId)
    setDepth(null)
  }, [])

  // A level coarser than the root's moves the root up to the ancestor at
  // that level, so zooming out of one field ends at all of them.
  const zoomTo = useCallback(
    (next: number) => {
      if (!root) return
      const target = Math.min(Math.max(1, next), root.levels)
      let areaId = parent
      let rootLevel = root.level
      if (target < root.level) {
        areaId = target === 1 ? null : (root.path.find((c) => c.level === target - 1)?.area_id ?? null)
        rootLevel = target
      }
      const wanted = target === rootLevel ? null : target
      if (areaId === parent && wanted === depth) return
      navigate(mapHref(window.location.search, areaId, wanted))
      setParent(areaId)
      setDepth(wanted)
    },
    [root, parent, depth],
  )

  if (load.status === 'error') {
    return <p className="p-6 text-text">{load.message}</p>
  }
  if (!load.level) {
    return <p className="p-6 font-mono text-[length:var(--text-data)] text-text-muted">Reading the fields.</p>
  }
  const byParent = tree && tree.root === load.level ? tree.byParent : NO_CHILDREN
  return (
    <AreasView
      level={load.level}
      onLevel={goTo}
      actions={actions}
      loading={load.status === 'loading'}
      byParent={byParent}
      depth={Math.max(load.level.level, Math.min(depth ?? load.level.level, load.level.levels))}
      onDepth={zoomTo}
      zoomError={zoomError}
    />
  )
}

/** Everything but the fetch of the level, so tests render it directly. */
export function AreasView({
  level,
  onLevel,
  actions,
  loading = false,
  byParent = NO_CHILDREN,
  depth,
  onDepth,
  zoomError = null,
}: {
  level: AreasLevel
  onLevel: (areaId: number | null) => void
  actions?: MapActions
  loading?: boolean
  /** Children already loaded under the root's areas, by parent id. */
  byParent?: ReadonlyMap<number, AreasLevel>
  /** The level of detail asked for; the root's own level when left out. */
  depth?: number
  /** Change the level of detail without opening any one circle. */
  onDepth?: (depth: number) => void
  zoomError?: string | null
}) {
  const wanted = Math.max(level.level, Math.min(depth ?? level.level, level.levels))
  // The deepest level asked for whose every parent has arrived; until the
  // rest arrive, the level above stays on screen.
  const shown = useMemo(() => {
    let deepest = level.level
    for (let l = level.level + 1; l <= wanted; l++) {
      if (!loadedTo(level, byParent, l)) break
      deepest = l
    }
    return deepest
  }, [level, byParent, wanted])
  const rows = useMemo(() => rowsAt(level, byParent, shown), [level, byParent, shown])
  const canvasFrame = useRef<HTMLDivElement>(null)
  const overlay = useRef<HTMLDivElement>(null)
  const overlayHeight = useHeight(overlay, 36)

  // The view over the map (a zoom and a pan), in the drawing's own frame:
  // below the overlay, above the key. The level of detail follows the zoom.
  const frame = useSize(canvasFrame)
  const drawTop = Math.max(64, overlayHeight + 28)
  const drawHeight = Math.max(200, frame.height - drawTop - CANVAS_BOTTOM)
  const [view, setView] = useState<View>(HOME)
  const viewRef = useRef(view)
  viewRef.current = view
  const wantedRef = useRef(wanted)
  wantedRef.current = wanted
  const follow = useCallback(
    (next: View) => {
      setView(next)
      const depthNow = depthForZoom(next.k, level.level, level.levels)
      if (depthNow !== wantedRef.current) onDepth?.(depthNow)
    },
    [level.level, level.levels, onDepth],
  )
  const zoomToDepth = useCallback(
    (target: number) => {
      const d = Math.max(level.level, Math.min(level.levels, target))
      const v = viewRef.current
      setView(
        zoomAbout(v, zoomForDepth(d, level.level) / v.k, frame.width / 2, drawHeight / 2, frame.width, drawHeight),
      )
      onDepth?.(d)
    },
    [level.level, level.levels, onDepth, frame.width, drawHeight],
  )
  useZoomGestures(
    canvasFrame,
    {
      onZoom: (factor, x, y) => follow(zoomAbout(viewRef.current, factor, x, y - drawTop, frame.width, drawHeight)),
      onPan: (dx, dy) => follow(panBy(viewRef.current, dx, dy, frame.width, drawHeight)),
      onStep: (direction) => zoomToDepth(wanted + direction),
      onReset: () => follow(HOME),
    },
    level.build !== null && !!onDepth,
  )
  // Another field opened, or a new build: the whole of it, from the top.
  useEffect(() => setView(HOME), [level.parent?.area_id, level.build?.build_id])
  // A level asked for from elsewhere — the URL, Back — brings the zoom to it.
  useEffect(() => {
    const v = viewRef.current
    if (depthForZoom(v.k, level.level, level.levels) === wanted) return
    setView(
      zoomAbout(v, zoomForDepth(wanted, level.level) / v.k, frame.width / 2, drawHeight / 2, frame.width, drawHeight),
    )
    // Only when the level asked for moves, not when the frame does.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wanted, level.level, level.levels])

  const [panel, setPanel] = useState<Panel>({ kind: 'none' })
  const [menu, setMenu] = useState<Menu>(null)
  const [hover, setHover] = useState<number | null>(null)
  const [highlight, setHighlight] = useState<number | null>(null)
  const [scale, setScale] = useState(0)

  // A sub-area picked from the jump box on another level: opened once that
  // level has arrived, rather than closed by it.
  const opening = useRef<number | null>(null)

  // A new level closes whatever was open on the last one.
  useEffect(() => {
    const areaId = opening.current
    opening.current = null
    setPanel(areaId === null ? { kind: 'none' } : { kind: 'area', areaId })
    setMenu(null)
  }, [level.parent?.area_id, level.build?.build_id])

  // Panels fetch their own detail.
  useEffect(() => {
    if (panel.kind === 'area' && !panel.detail && !panel.error) {
      const controller = new AbortController()
      getArea(panel.areaId, { signal: controller.signal })
        .then((detail) => setPanel((p) => (p.kind === 'area' && p.areaId === panel.areaId ? { ...p, detail } : p)))
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return
          setPanel((p) => (p.kind === 'area' ? { ...p, error: messageOf(cause, 'This field did not load.') } : p))
        })
      return () => controller.abort()
    }
    if (panel.kind === 'bridge' && !panel.bridge && !panel.error) {
      const controller = new AbortController()
      getBridge(panel.a, panel.b, { signal: controller.signal })
        .then((bridge) => setPanel((p) => (p.kind === 'bridge' ? { ...p, bridge } : p)))
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return
          setPanel((p) => (p.kind === 'bridge' ? { ...p, error: messageOf(cause, 'This bridge did not load.') } : p))
        })
      return () => controller.abort()
    }
  }, [panel])

  // Route mode (`P6-39`): "Route from here…" picks the start; the next circle
  // clicked is the end, and the route is found among the lines on screen.
  const [routeFrom, setRouteFrom] = useState<Area | null>(null)
  const [shade, setShade] = useState<Shade>('topic')
  const routeLinks = useMemo(
    () => (shown === level.level ? level.links : linksAt(level, byParent, shown)),
    [level, byParent, shown],
  )
  useEffect(() => {
    if (!routeFrom) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setRouteFrom(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [routeFrom])
  useEffect(() => setRouteFrom(null), [level.parent?.area_id, shown])

  const openArea = useCallback(
    (area: Area) => {
      setMenu(null)
      if (routeFrom) {
        if (area.area_id === routeFrom.area_id) return
        setPanel({
          kind: 'route',
          from: routeFrom,
          to: area,
          hops: routeBetween(routeLinks, routeFrom.area_id, area.area_id),
        })
        setRouteFrom(null)
        return
      }
      if (area.children > 0) onLevel(area.area_id)
      else setPanel({ kind: 'area', areaId: area.area_id })
    },
    [onLevel, routeFrom, routeLinks],
  )

  const empty = level.build === null
  const top = level.parent === null

  if (empty) {
    return (
      <div className="flex-1 p-6">
        <div className="max-w-xl border border-line bg-surface p-5">
          <h2 className="text-[length:var(--text-subhead)] font-semibold text-text">No fields yet</h2>
          <p className="mt-2 text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
            Fields are built once a day by <span className="font-mono">worker.areas</span> from every searchable,
            embedded passage — at least {level.passages_needed.toLocaleString('en')} of them. Nothing has been built on
            this corpus yet. The passages themselves can be seen now, under Passages in 3D.
          </p>
        </div>
      </div>
    )
  }

  // The root's fields too: a line between two fields is drawn at every level.
  const byId = new Map([...level.areas, ...rows].map((a) => [a.area_id, a]))
  const zooming = shown < wanted && !zoomError

  return (
    <div className="relative flex min-h-0 flex-1">
      {!top ? (
        <LevelAside
          level={level}
          onLevel={onLevel}
          onPick={(area) => {
            setHighlight(area.area_id)
            openArea(area)
          }}
        />
      ) : null}

      <div
        ref={canvasFrame}
        data-theme="dark"
        className="relative min-w-0 flex-1 bg-ground-deep text-text"
        style={{ touchAction: 'none' }}
      >
        <Canvas
          level={level}
          byParent={byParent}
          shown={shown}
          hover={hover}
          highlight={highlight}
          selectedLink={panel.kind === 'bridge' ? [panel.a, panel.b] : null}
          routeLinks={panel.kind === 'route' ? (panel.hops ?? []) : []}
          routeEnds={
            panel.kind === 'route' ? [panel.from.area_id, panel.to.area_id] : routeFrom ? [routeFrom.area_id] : []
          }
          shade={shade}
          onHover={setHover}
          onOpen={openArea}
          onLink={(link) => setPanel({ kind: 'bridge', a: link.area_a, b: link.area_b })}
          onMenu={setMenu}
          onScale={setScale}
          top={drawTop}
          view={view}
        />

        <div ref={overlay} className="absolute inset-x-5 top-4 z-[1] flex flex-wrap items-center gap-2.5">
          <JumpBox
            onPick={(hit) => {
              setHighlight(hit.area.area_id)
              const leaf = hit.area.children === 0
              if (hit.area.parent_id !== (level.parent?.area_id ?? null)) {
                if (leaf) opening.current = hit.area.area_id
                onLevel(hit.area.parent_id)
              } else if (leaf) setPanel({ kind: 'area', areaId: hit.area.area_id })
            }}
          />
          <span className="min-w-0 font-mono text-[11px] text-text-faint" aria-live="polite">
            {routeFrom
              ? `Route from ${routeFrom.name}: click another ${levelNoun(shown)} · Esc to cancel`
              : zoomCaption(level, shown, rows.length)}
            {loading || zooming ? ' · loading' : ''}
            {zoomError ? ` · ${zoomError}` : ''}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <ShadeControl value={shade} onChange={setShade} />
            {onDepth ? <LevelControl levels={level.levels} value={wanted} onChange={zoomToDepth} /> : null}
          </div>
        </div>

        <SizeKey areas={rows} scale={scale} compact={frame.width < COMPACT_KEY_BELOW} />
        <p className="pointer-events-none absolute bottom-4 left-5 right-5 hidden truncate font-mono text-[11px] text-text-faint sm:block">
          size = passages · fill ={' '}
          {shade === 'research' ? 'share peer-reviewed, fullest = most on this map' : 'share on your topics'} · solid =
          cited claim · dashed = similar passages
          {shown === level.level ? '' : ` (hover a circle) · outline = its ${levelNoun(level.level)}`} · scroll to zoom,
          drag to move, 0 for all · right-click for more
        </p>

        {hover !== null && byId.get(hover) ? <AreaTip area={byId.get(hover)!} /> : null}

        {menu ? (
          <ContextMenu
            menu={menu}
            level={{ ...level, areas: rows }}
            actions={actions}
            onClose={() => setMenu(null)}
            onOpen={openArea}
            onRoute={(area) => {
              setMenu(null)
              setPanel({ kind: 'none' })
              setRouteFrom(area)
            }}
          />
        ) : null}
      </div>

      {panel.kind !== 'none' ? (
        <aside
          aria-label={panel.kind === 'bridge' ? 'Bridge' : panel.kind === 'route' ? 'Route' : 'Field'}
          className="absolute inset-y-0 right-0 z-30 flex w-full max-w-[380px] shrink-0 flex-col gap-3.5 overflow-y-auto border-l border-line bg-surface p-5 md:static md:w-[380px]"
        >
          {panel.kind === 'bridge' ? (
            <BridgePanel panel={panel} areas={byId} onClose={() => setPanel({ kind: 'none' })} />
          ) : panel.kind === 'route' ? (
            <RoutePanel
              route={panel}
              areas={byId}
              noun={levelNoun(shown)}
              onBridge={(link) => setPanel({ kind: 'bridge', a: link.area_a, b: link.area_b })}
              onClose={() => setPanel({ kind: 'none' })}
            />
          ) : (
            <AreaPanel panel={panel} onClose={() => setPanel({ kind: 'none' })} />
          )}
        </aside>
      ) : null}
    </div>
  )
}

// --------------------------------------------------------------------------
// The canvas

/** An element's height as laid out, or `fallback` until it has one. */
function useHeight(ref: React.RefObject<HTMLElement | null>, fallback: number): number {
  const [height, setHeight] = useState(fallback)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const measure = () => {
      const h = element.getBoundingClientRect().height
      if (h > 0) setHeight(h)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return height
}

function useSize(ref: React.RefObject<HTMLElement | null>): { width: number; height: number } {
  const [size, setSize] = useState({ width: 900, height: 640 })
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const measure = () => {
      const box = element.getBoundingClientRect()
      if (box.width > 0 && box.height > 0) setSize({ width: box.width, height: box.height })
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref])
  return size
}

/**
 * By how many levels below the root: each level's circle scale as a share of
 * the level above's, how far children spread across their parent's circle,
 * and the space kept between circles. A level's circles together hold the
 * same passages as the level above, so at a share of 1 they cover the same
 * area; the spacing lets them spread into the room the coarser level left.
 */
/** Room kept at the bottom of the canvas for the key's line. */
const CANVAS_BOTTOM = 40

const SHRINK = [1, 0.98, 0.9]
const SPREAD = [0, 0.95, 0.85]
const GAPS = [14, 5, 2.5]

type Morph = {
  items: MorphItem[]
  coarse: Placed[]
  coarseLabels: Map<number, Label>
  split: boolean
}

function Canvas({
  level,
  byParent,
  shown,
  hover,
  highlight,
  selectedLink,
  onHover,
  onOpen,
  onLink,
  onMenu,
  onScale,
  top = 64,
  view = HOME,
  routeLinks = [],
  routeEnds = [],
  shade = 'topic',
}: {
  level: AreasLevel
  byParent: ReadonlyMap<number, AreasLevel>
  shown: number
  hover: number | null
  highlight: number | null
  selectedLink: [number, number] | null
  onHover: (id: number | null) => void
  onOpen: (area: Area) => void
  onLink: (link: AreaLink) => void
  onMenu: (menu: Menu) => void
  onScale: (scale: number) => void
  /** Room kept clear at the top for what floats over the canvas there. */
  top?: number
  /** The zoom and pan the reader has; the layout itself never moves. */
  view?: View
  /** A route's hops, drawn as selected whatever the other filters say (`P6-39`). */
  routeLinks?: readonly AreaLink[]
  /** Its ends, ringed; one end while the reader is still choosing the other. */
  routeEnds?: readonly number[]
  /** What the fill shows (`P6-42`). */
  shade?: Shade
}) {
  const box = useRef<HTMLDivElement>(null)
  const { width, height } = useSize(box)
  // Keyboard focus on a line draws it as selected: the hit line is invisible,
  // so an outline would be a box round nothing.
  const [focused, onFocusLink] = useState<[number, number] | null>(null)
  // Room at the bottom for the key.
  const bottom = CANVAS_BOTTOM
  const drawHeight = Math.max(200, height - top - bottom)
  const maxRadius = fitRadius(
    level.areas.map((a) => a.passages),
    width,
    drawHeight,
  )

  // Where the size key sits, bottom left: circles and labels keep out of it.
  const keyBox = useMemo<Rect>(() => {
    const k = width < COMPACT_KEY_BELOW ? KEY_BOX_COMPACT : KEY_BOX
    return { x: 0, y: drawHeight - k.h, w: k.w, h: k.h }
  }, [drawHeight, width])

  // Every loaded level laid out once per size: the root's by its stored
  // positions, each finer one composed into the level above it.
  const layouts = useMemo(() => {
    const avoid: Rect[] = [keyBox]
    const first = placeAreas(level.areas, width, drawHeight, { maxRadius, gap: GAPS[0], pad: 28, avoid })
    let current: Layout = { level: level.level, ...first }
    const out = new Map<number, Layout>([[level.level, current]])
    const childrenOf = new Map([...byParent].map(([id, children]) => [id, children.areas]))
    for (let l = level.level + 1; l <= level.levels; l++) {
      if (!loadedTo(level, byParent, l)) break
      current = placeNested(current, childrenOf, {
        width,
        height: drawHeight,
        shrink: SHRINK[Math.min(l - level.level, SHRINK.length - 1)]!,
        spread: SPREAD[Math.min(l - level.level, SPREAD.length - 1)]!,
        gap: GAPS[Math.min(l - level.level, GAPS.length - 1)]!,
        pad: 20,
        avoid,
      })
      out.set(l, current)
    }
    return out
  }, [level, byParent, width, drawHeight, maxRadius, keyBox])

  const layout = layouts.get(shown) ?? layouts.get(level.level)!
  useEffect(() => onScale(layout.scale * view.k), [layout.scale, view.k, onScale])

  // The layout as the view shows it: every circle, for lines and outlines,
  // and those on screen, for drawing and naming.
  const placedAll = useMemo(() => layout.placed.map((p) => viewed(p, view)), [layout, view])
  const placed = useMemo(() => placedAll.filter((p) => onScreen(p, width, drawHeight)), [placedAll, width, drawHeight])

  // Every area loaded, for walking a finer one up to its ancestor.
  const everyArea = useMemo(() => {
    const all = new Map<number, Area>(level.areas.map((a) => [a.area_id, a]))
    for (const children of byParent.values()) for (const a of children.areas) all.set(a.area_id, a)
    return all
  }, [level, byParent])

  // At a finer level, a faint outline round each of the root's areas, named,
  // so the reader can still tell which field a theme came from.
  const outlines = useMemo(() => {
    if (layout.level === level.level || level.areas.length > 24) return []
    const groups = new Map<number, Placed[]>()
    for (const p of placedAll) {
      const a = ancestorAt(p.area.area_id, level.level, everyArea)
      if (a === null) continue
      const list = groups.get(a)
      if (list) list.push(p)
      else groups.set(a, [p])
    }
    const drawn = [...groups]
      .map(([id, list]) => {
        const hull = enclosing(list, 8)
        const name = everyArea.get(id)?.name ?? ''
        const w = Math.min(name.length * 6.2 + 10, width)
        const labelY = Math.max(12, hull.y - hull.r - 6)
        const box: Rect = { x: hull.x - w / 2, y: labelY - 10, w, h: 13 }
        return { id, name, hull, labelY, box, named: false }
      })
      .sort((a, b) => b.hull.r - a.hull.r)
    // Named largest first, and only where the name clears every name before it.
    const taken: Rect[] = []
    for (const o of drawn) {
      const { box } = o
      if (box.x < 0 || box.x + box.w > width) continue
      if (taken.some((t) => t.x < box.x + box.w && box.x < t.x + t.w && t.y < box.y + box.h && box.y < t.y + t.h))
        continue
      o.named = true
      taken.push(box)
    }
    return drawn
  }, [layout, placedAll, level, everyArea, width])

  // Research shares are small (the corpus is mostly government pages), so that
  // fill runs up to the highest share on screen rather than to 1.
  const shadeMax = useMemo(() => {
    if (shade !== 'research') return 1
    const values = layout.placed.map((p) => shadeOf(p.area, 'research') ?? 0)
    return Math.max(0, ...values)
  }, [layout, shade])

  const labels = useMemo(
    () =>
      fitLabels(placed, {
        width,
        captions: placed.length <= 40,
        crowded: placed.length > 40 || width < 640,
        reserved: [keyBox, ...outlines.filter((o) => o.named).map((o) => o.box)],
      }),
    [placed, width, outlines],
  )

  // A change of level splits each circle into its children, or gathers them
  // back. Set before paint, so the finished level never flashes first.
  const [morph, setMorph] = useState<Morph | null>(null)
  const last = useRef<{ root: AreasLevel; shown: number } | null>(null)
  useLayoutEffect(() => {
    const previous = last.current
    last.current = { root: level, shown: layout.level }
    if (!previous || previous.root !== level || previous.shown === layout.level) return
    if (prefersReducedMotion()) {
      setMorph(null)
      return
    }
    const lo = Math.min(previous.shown, layout.level)
    const hi = Math.max(previous.shown, layout.level)
    const coarse = layouts.get(lo)
    const fine = layouts.get(hi)
    if (!coarse || !fine) return
    // Drawn as the reader sees them now, so a split set off by zooming
    // happens where they are looking.
    const coarsePlaced = coarse.placed.map((p) => viewed(p, view))
    const finePlaced = fine.placed.map((p) => viewed(p, view))
    setMorph({
      items: splitMorph(coarsePlaced, finePlaced, (id) => ancestorAt(id, lo, everyArea)),
      coarse: coarsePlaced,
      coarseLabels: fitLabels(
        coarsePlaced.filter((p) => onScreen(p, width, drawHeight)),
        {
          width,
          captions: false,
          reserved: [keyBox],
        },
      ),
      split: layout.level > previous.shown,
    })
    // The view at the moment the level changed; a pan during the split does not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [level, layout, layouts, everyArea, width, keyBox])
  const endMorph = useCallback(() => setMorph(null), [])

  const childNoun = levelNoun(layout.level + 1)
  // Lines at every level (`linksAt`): siblings' bridges between circles, and
  // below the root the root's own bridges between the outlines of its fields,
  // so a link between two fields does not vanish on zooming in.
  const ends = new Map<number, { x: number; y: number; r: number; name: string }>(
    placedAll.map((p) => [p.area.area_id, { x: p.x, y: p.y, r: p.r, name: p.area.name }]),
  )
  // Below the root a level has hundreds of sibling lines, and the dashed
  // ones (similar passages, no cited claim) would cover the map: they show
  // for the circle in hand. Cited ones always show; they are the rare kind.
  const focusId = hover ?? highlight
  const onRoute = new Set(routeLinks.map((l) => [l.area_a, l.area_b].sort((x, y) => x - y).join('-')))
  const routeKey = (l: AreaLink) => [l.area_a, l.area_b].sort((x, y) => x - y).join('-')
  const links = linksAt(level, byParent, layout.level).filter(
    (link) =>
      layout.level === level.level ||
      link.cited_claims > 0 ||
      link.area_a === focusId ||
      link.area_b === focusId ||
      onRoute.has(routeKey(link)),
  )
  const fieldLinks: AreaLink[] = []
  if (layout.level > level.level) {
    for (const o of outlines)
      ends.set(o.id, { x: o.hull.x, y: o.hull.y, r: o.hull.r, name: everyArea.get(o.id)?.name ?? '' })
    if (outlines.length > 0) fieldLinks.push(...level.links)
  }

  const contextMenu = useCallback(
    (event: React.MouseEvent, area: Area | null) => {
      event.preventDefault()
      event.stopPropagation()
      const frame = box.current?.getBoundingClientRect()
      const x = event.clientX - (frame?.left ?? 0)
      const y = event.clientY - (frame?.top ?? 0)
      onMenu(area ? { kind: 'area', area, x, y } : { kind: 'empty', x, y })
    },
    [onMenu],
  )

  const cx = width / 2
  const globe = Math.min(width, drawHeight) * 0.46 * view.k
  const gx = cx * view.k + view.x
  const gy = top + (drawHeight / 2) * view.k + view.y

  return (
    <div ref={box} className="absolute inset-0" onContextMenu={(event) => contextMenu(event, null)}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label="Fields of the corpus"
        className="block"
        data-level={layout.level}
      >
        <g fill="none" stroke="var(--dark-canvas-graticule)">
          <circle cx={gx} cy={gy} r={globe} />
          <ellipse cx={gx} cy={gy} rx={globe * 0.42} ry={globe} />
        </g>
        <defs>
          <clipPath id="map-draw">
            <rect x={0} y={0} width={width} height={drawHeight} />
          </clipPath>
        </defs>
        {/* Clipped to the drawing's own frame, so a zoomed map stays out from
            under the controls above it and the key's line below. */}
        <g transform={`translate(0 ${top})`} clipPath="url(#map-draw)">
          {/* Links first, under the circles (`B-124`): drawn over them, a long
              line crossed the names and captions of every circle on its way. */}
          {morph
            ? null
            : [
                ...fieldLinks.map((link) => ({ link, between: 'fields' })),
                ...links.map((link) => ({ link, between: '' })),
              ].map(({ link, between }) => {
                const a = ends.get(link.area_a)
                const b = ends.get(link.area_b)
                if (!a || !b) return null
                // Edge to edge, not centre to centre: a line drawn through a
                // circle crosses its label and makes the circle hard to click.
                const segment = edgeToEdge(a, b)
                if (!segment) return null
                const [x1, y1, x2, y2] = segment
                const cited = link.cited_claims > 0
                const selected =
                  onRoute.has(routeKey(link)) ||
                  (selectedLink?.includes(link.area_a) && selectedLink.includes(link.area_b)) ||
                  (focused?.[0] === link.area_a && focused[1] === link.area_b)
                const label = `${a.name} and ${b.name}: ${
                  cited
                    ? `${link.cited_claims} cited claim${link.cited_claims === 1 ? '' : 's'}, ${link.cited_sources} source${link.cited_sources === 1 ? '' : 's'}`
                    : 'similar passages, no cited claim'
                }`
                return (
                  <g
                    key={`${link.area_a}-${link.area_b}`}
                    data-link={between || 'siblings'}
                    opacity={between ? 0.55 : 1}
                  >
                    <line
                      x1={x1}
                      y1={y1}
                      x2={x2}
                      y2={y2}
                      stroke={selected ? 'var(--text)' : cited ? 'var(--accent-graph)' : 'var(--dark-canvas-neighbour)'}
                      strokeOpacity={selected ? 1 : cited ? 0.6 : 0.5}
                      strokeWidth={selected ? Math.max(3, linkWidth(link)) : linkWidth(link)}
                      strokeDasharray={cited ? undefined : '4 5'}
                    />
                    {/* A wide transparent twin, so a thin line is clickable. */}
                    <line
                      x1={x1}
                      y1={y1}
                      x2={x2}
                      y2={y2}
                      stroke="transparent"
                      strokeWidth={14}
                      className="cursor-pointer focus:outline-none"
                      onFocus={() => onFocusLink([link.area_a, link.area_b])}
                      onBlur={() => onFocusLink(null)}
                      role="button"
                      tabIndex={0}
                      aria-label={label}
                      onClick={() => onLink(link)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') onLink(link)
                      }}
                    >
                      <title>{label}</title>
                    </line>
                  </g>
                )
              })}
          {morph ? (
            <MorphLayer
              items={morph.items}
              coarse={morph.coarse}
              coarseLabels={morph.coarseLabels}
              split={morph.split}
              onDone={endMorph}
            />
          ) : (
            <>
              {outlines.map((o) => (
                <circle
                  key={o.id}
                  aria-hidden
                  data-outline={o.id}
                  cx={o.hull.x}
                  cy={o.hull.y}
                  r={o.hull.r}
                  fill="none"
                  stroke="var(--dark-canvas-neighbour)"
                  strokeOpacity={0.28}
                  strokeDasharray="2 5"
                />
              ))}
              {placed.map((p) => (
                <AreaCircle
                  key={p.area.area_id}
                  placed={p}
                  label={labels.get(p.area.area_id)}
                  childNoun={childNoun}
                  hovered={hover === p.area.area_id}
                  highlighted={highlight === p.area.area_id}
                  onHover={onHover}
                  onOpen={onOpen}
                  onMenu={contextMenu}
                  shade={shade}
                  shadeMax={shadeMax}
                />
              ))}
              {routeEnds.map((id) => {
                const p = placedAll.find((q) => q.area.area_id === id)
                return p ? (
                  <circle
                    key={`end${id}`}
                    data-route-end={id}
                    aria-hidden
                    cx={p.x}
                    cy={p.y}
                    r={p.r + 5}
                    fill="none"
                    stroke="var(--accent-attention)"
                    strokeWidth={1.5}
                    strokeDasharray="4 3"
                    className="pointer-events-none"
                  />
                ) : null
              })}
              {/* Over the circles, so the halo keeps a name legible where
                  another field's circles pass under it. */}
              <g aria-hidden className="pointer-events-none">
                {outlines
                  .filter((o) => o.named)
                  .map((o) => (
                    <text
                      key={o.id}
                      x={o.hull.x}
                      y={o.labelY}
                      textAnchor="middle"
                      fontFamily="var(--font-mono)"
                      fontSize={10}
                      letterSpacing="0.04em"
                      fill="var(--text-muted)"
                      paintOrder="stroke"
                      stroke="var(--ground-deep)"
                      strokeWidth={4}
                    >
                      {o.name}
                    </text>
                  ))}
              </g>
            </>
          )}
        </g>
      </svg>
    </div>
  )
}

/**
 * One circle and, where {@link fitLabels} found it room, its name. Memoised:
 * a finer level draws hundreds, and hovering one should not redraw the rest.
 */
const AreaCircle = memo(function AreaCircle({
  placed,
  label,
  childNoun,
  hovered,
  highlighted,
  onHover,
  onOpen,
  onMenu,
  shade = 'topic',
  shadeMax = 1,
}: {
  placed: Placed
  label: Label | undefined
  childNoun: string
  hovered: boolean
  highlighted: boolean
  onHover: (id: number | null) => void
  onOpen: (area: Area) => void
  onMenu: (event: React.MouseEvent, area: Area) => void
  shade?: Shade
  /** The fill's full scale: 1 for topic share; the highest share shown for research. */
  shadeMax?: number
}) {
  const { area, x, y, r } = placed
  const flagged = area.weak || area.stale
  const share = onTopicShare(area)
  const raw = shadeOf(area, shade)
  const fill = raw === null ? null : Math.min(1, raw / (shadeMax > 0 ? shadeMax : 1))
  // Muted names mean "not about your topics", whatever the fill is showing.
  const background = share !== null && share < OFF_TOPIC_BELOW
  const caption =
    area.children > 0
      ? `${area.children} ${childNoun}${area.children === 1 ? '' : 's'} inside`
      : `${area.passages.toLocaleString('en')} passages`
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`${area.name}: ${area.passages.toLocaleString('en')} passages from ${area.sources.toLocaleString('en')} sources${
        shareText(area) ? `, ${shareText(area)}` : ''
      }${flagged ? ` — ${area.reasons.join('; ')}` : ''}${area.children > 0 ? '. Open to zoom in.' : ''}`}
      className="cursor-pointer focus:outline-none"
      onMouseEnter={() => onHover(area.area_id)}
      onMouseLeave={() => onHover(null)}
      onFocus={() => onHover(area.area_id)}
      onBlur={() => onHover(null)}
      onClick={() => onOpen(area)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen(area)
        }
      }}
      onContextMenu={(event) => onMenu(event, area)}
      data-area={area.area_id}
      data-flagged={flagged ? 'true' : undefined}
      data-share={share === null ? undefined : share.toFixed(3)}
      data-shade={raw === null ? undefined : raw.toFixed(3)}
    >
      <circle
        cx={x}
        cy={y}
        r={r}
        fill="var(--accent-graph-deep)"
        fillOpacity={hovered || highlighted ? Math.max(0.5, shareFill(fill)) : shareFill(fill)}
        stroke={flagged ? 'var(--accent-attention)' : 'var(--accent-graph)'}
        strokeOpacity={background && !hovered && !highlighted ? 0.45 : 1}
        strokeWidth={highlighted ? 2.4 : r < 12 ? 1 : 1.2}
        strokeDasharray={flagged ? (r < 12 ? '3 2' : '5 4') : undefined}
      />
      {label?.lines.map((line, i) => (
        <text
          key={i}
          x={x}
          y={label.y + i * label.lineHeight}
          textAnchor="middle"
          fontFamily="var(--font-sans)"
          fontSize={label.fontSize}
          fontWeight={600}
          fill={background ? 'var(--text-muted)' : 'var(--text)'}
          paintOrder="stroke"
          stroke="var(--ground-deep)"
          strokeWidth={3.5}
        >
          {line}
        </text>
      ))}
      {label?.caption ? (
        <text
          x={x}
          y={label.y + label.lines.length * label.lineHeight + 2}
          textAnchor="middle"
          fontFamily="var(--font-mono)"
          fontSize={10.5}
          fill="var(--text-faint)"
          paintOrder="stroke"
          stroke="var(--ground-deep)"
          strokeWidth={3}
        >
          {caption}
        </text>
      ) : null}
    </g>
  )
})

/**
 * The size key, drawn at the canvas's own scale: a key circle is exactly the
 * size an area of that many passages is drawn at, so the two can be compared
 * by eye. Values that would not fit the key are left out rather than shrunk.
 */
function SizeKey({ areas, scale, compact = false }: { areas: readonly Area[]; scale: number; compact?: boolean }) {
  // On a phone, one short row (`B-101`): the full key covered a fifth of a
  // 390px canvas, the part the map most needs.
  const values = keyValuesAtScale(
    areas.map((a) => a.passages),
    scale,
    compact ? KEY_RADIUS_COMPACT : KEY_RADIUS,
  )
  if (values.length === 0 || scale <= 0) return null
  const radii = values.map((v) => Math.max(1.5, radiusOf(v, scale)))
  const height = Math.max(...radii) * 2 + 20
  let cursor = 4
  const placed = values.map((value, i) => {
    const r = radii[i]!
    const x = cursor + Math.max(r, 16)
    cursor += Math.max(r, 16) * 2 + 12
    return { value, r, x }
  })
  if (compact) {
    return (
      <div
        aria-label="Size key"
        className="absolute bottom-3 left-3 flex items-center gap-2 border border-line bg-surface/90 px-2 py-1"
      >
        <svg width={cursor} height={height} aria-hidden>
          {placed.map(({ value, r, x }) => (
            <g key={value}>
              <circle
                cx={x}
                cy={height - 2 - r}
                r={r}
                fill="var(--accent-graph-deep)"
                fillOpacity={0.35}
                stroke="var(--accent-graph)"
              />
              <text
                x={x}
                y={10}
                textAnchor="middle"
                fontFamily="var(--font-mono)"
                fontSize={9.5}
                fill="var(--text-muted)"
              >
                {value.toLocaleString('en')}
              </text>
            </g>
          ))}
        </svg>
        <span className="font-mono text-[9.5px] text-text-faint">passages</span>
      </div>
    )
  }
  return (
    <div
      aria-label="Size key"
      className="absolute bottom-10 left-5 flex flex-col gap-1.5 border border-line bg-surface/90 px-3 py-2.5"
    >
      <span className="font-mono text-[9.5px] uppercase tracking-[var(--tracking-label)] text-text-faint">
        Passages collected
      </span>
      <svg width={Math.max(150, cursor)} height={height} aria-hidden>
        {placed.map(({ value, r, x }) => (
          <g key={value}>
            <circle
              cx={x}
              cy={height - 2 - r}
              r={r}
              fill="var(--accent-graph-deep)"
              fillOpacity={0.35}
              stroke="var(--accent-graph)"
            />
            <text x={x} y={10} textAnchor="middle" fontFamily="var(--font-mono)" fontSize={10} fill="var(--text-muted)">
              {value.toLocaleString('en')}
            </text>
          </g>
        ))}
      </svg>
      <span className="text-[11px] text-text-faint">to scale · area, not importance</span>
    </div>
  )
}

/** The largest circle the key will draw, and on a phone. */
const KEY_RADIUS = 34
const KEY_RADIUS_COMPACT = 11
/** Below this canvas width the key is one short row. */
export const COMPACT_KEY_BELOW = 640
/** The room the key takes over the canvas, which circles keep out of. */
const KEY_BOX = { w: 230, h: 150 }
const KEY_BOX_COMPACT = { w: 170, h: 44 }

function AreaTip({ area }: { area: Area }) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute right-5 top-28 z-10 flex w-[280px] max-w-[calc(100%-40px)] sm:top-16 flex-col gap-1.5 border border-text/16 bg-surface/90 px-[13px] py-[11px] backdrop-blur-[10px]"
    >
      <span className="text-[13px] font-semibold leading-snug text-text">{area.name}</span>
      <span className="font-mono text-[10.5px] text-text-faint">
        {area.passages.toLocaleString('en')} passages · {area.sources.toLocaleString('en')} sources
        {area.newest_at ? ` · newest ${dayOf(area.newest_at)}` : ''}
      </span>
      <TopicShare area={area} />
      <TierMix mix={area.tier_mix} total={area.passages} />
      {area.reasons.length ? (
        <span className="text-[length:var(--text-small)] text-accent-attention">{area.reasons.join('; ')}</span>
      ) : null}
    </div>
  )
}

/**
 * How much of a field is about the topics, and which (`P6-42`). Said in words
 * and numbers, since the fill that shows it is a shade and a shade is not read
 * exactly. Nothing is drawn when the build did not measure it.
 */
function TopicShare({ area }: { area: Area }) {
  const text = shareText(area)
  if (!text) return null
  const topics = topicShares(area)
  return (
    <span className="flex flex-col gap-0.5" data-testid="topic-share">
      <span
        className={`text-[12px] ${(onTopicShare(area) ?? 0) < OFF_TOPIC_BELOW ? 'text-accent-attention' : 'text-text'}`}
      >
        {text}
        {(onTopicShare(area) ?? 0) < OFF_TOPIC_BELOW ? ' — mostly material outside your topics' : ''}
      </span>
      {topics.length ? (
        <span className="flex flex-wrap gap-x-2 font-mono text-[10px] text-text-muted">
          {topics.map(({ topic, share }) => (
            <span key={topic}>
              {topic} {share > 0 && share < 0.01 ? '<1' : Math.round(share * 100)}%
            </span>
          ))}
        </span>
      ) : null}
    </span>
  )
}

/** Tier mix as a stacked bar with labels, in the order tiers are ranked. */
const TIER_ORDER = ['peer_reviewed', 'government', 'institutional', 'press', 'informal']

function TierMix({ mix, total }: { mix: Record<string, number>; total: number }) {
  const rows = TIER_ORDER.filter((tier) => (mix[tier] ?? 0) > 0)
  if (!rows.length || total <= 0) return null
  return (
    <span className="flex flex-wrap gap-x-2 font-mono text-[10px] text-text-muted" data-testid="tier-mix">
      {rows.map((tier) => (
        <span key={tier}>
          {tier.replace('_', ' ')} {Math.round(((mix[tier] ?? 0) / total) * 100)}%
        </span>
      ))}
    </span>
  )
}

// --------------------------------------------------------------------------
// Around the canvas

function LevelAside({
  level,
  onLevel,
  onPick,
}: {
  level: AreasLevel
  onLevel: (areaId: number | null) => void
  onPick: (area: Area) => void
}) {
  const plural = levelNoun(level.level, 2)
  const noun = capitalised(plural)
  const parentNoun = levelNoun(level.level - 1)
  return (
    <aside className="hidden w-[280px] shrink-0 flex-col gap-3.5 overflow-y-auto border-r border-line bg-surface p-[18px] md:flex">
      <nav aria-label="Where you are" className="flex flex-col gap-1 font-mono text-[11px]">
        <a
          href="/map"
          className="text-accent-graph no-underline"
          onClick={(event) => {
            event.preventDefault()
            onLevel(null)
          }}
        >
          All fields
        </a>
        {level.path.map((crumb, i) => {
          const last = i === level.path.length - 1
          return last ? (
            <span key={crumb.area_id} className="text-text-muted">
              › {crumb.name} · level {crumb.level + 1}
            </span>
          ) : (
            <a
              key={crumb.area_id}
              href={`/map?area=${crumb.area_id}`}
              className="text-accent-graph no-underline"
              onClick={(event) => {
                event.preventDefault()
                onLevel(crumb.area_id)
              }}
            >
              › {crumb.name}
            </a>
          )
        })}
      </nav>

      <h2 className="font-mono text-[9.5px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
        {noun} in this {parentNoun}
      </h2>
      <ul className="flex flex-col">
        {level.areas.map((area) => (
          <li key={area.area_id} className="border-t border-line">
            <button
              type="button"
              onClick={() => onPick(area)}
              className="flex w-full items-baseline justify-between gap-3 py-2 text-left text-[13px] text-text hover:text-accent-graph"
            >
              <span
                className={
                  area.weak || area.stale
                    ? 'underline decoration-accent-attention decoration-dashed underline-offset-4'
                    : undefined
                }
              >
                {area.name}
              </span>
              <span className="font-mono text-[11.5px] text-text-faint">{area.passages.toLocaleString('en')}</span>
            </button>
          </li>
        ))}
      </ul>

      <h2 className="mt-2.5 font-mono text-[9.5px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
        Links
      </h2>
      <ul className="flex flex-col gap-2 text-[12.5px] text-text-muted">
        <li className="flex items-center gap-2">
          <span className="h-[3px] w-[26px] bg-accent-graph" aria-hidden />
          claims — a passage states it, cited
        </li>
        <li className="flex items-center gap-2">
          <span className="w-[26px] border-t-2 border-text-muted" style={{ borderTopStyle: 'dashed' }} aria-hidden />
          similar passages — near in meaning
        </li>
        <li className="flex items-center gap-2">
          <span className="h-1.5 w-[26px] bg-accent-graph" aria-hidden />
          thicker — more independent sources
        </li>
        <li className="flex items-center gap-2">
          <span className="h-3.5 w-3.5 rounded-full border border-accent-graph" aria-hidden />
          circle size — passages collected, not importance
        </li>
        <li className="flex items-center gap-2">
          <span className="flex gap-0.5" aria-hidden>
            <span className="h-3.5 w-3.5 rounded-full border border-accent-graph/45 bg-accent-graph-deep/10" />
            <span className="h-3.5 w-3.5 rounded-full border border-accent-graph bg-accent-graph-deep/60" />
          </span>
          fainter — less of it on your topics
        </li>
        <li className="flex items-center gap-2">
          <span
            className="h-3.5 w-3.5 rounded-full border border-accent-attention"
            style={{ borderStyle: 'dashed' }}
            aria-hidden
          />
          amber outline — fewer than {level.weak_below_sources} sources, or nothing new in {level.stale_after_days} days
        </li>
      </ul>

      <p className="mt-auto text-[12px] leading-[1.55] text-text-faint">
        Fields are clusters of passages, each named for the field of work it reads as. Topics are a filter over them,
        not their boundaries.
      </p>
    </aside>
  )
}

function JumpBox({ onPick }: { onPick: (hit: AreaJumpHit) => void }) {
  const [text, setText] = useState('')
  const [hits, setHits] = useState<AreaJumpHit[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inFlight = useRef<AbortController | null>(null)

  function search(event: React.FormEvent) {
    event.preventDefault()
    const query = text.trim()
    if (!query) return
    inFlight.current?.abort()
    const controller = new AbortController()
    inFlight.current = controller
    setError(null)
    jumpToArea(query, { signal: controller.signal })
      .then((body) => setHits(body.hits))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(messageOf(cause, 'The jump did not complete.'))
      })
  }

  return (
    <form role="search" onSubmit={search} className="relative min-w-0 max-w-full">
      <input
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          if (!event.target.value) setHits(null)
        }}
        placeholder="Jump to a field or a term"
        aria-label="Jump to a field or a term"
        className="h-9 w-[340px] max-w-full border border-line-strong bg-surface px-3 text-[13.5px] text-text placeholder:text-text-faint"
      />
      {error ? (
        <p className="absolute left-0 top-10 w-[340px] border border-line bg-surface p-2 text-[12px] text-text">
          {error}
        </p>
      ) : null}
      {hits ? (
        <ul
          aria-label="Fields found"
          className="absolute left-0 top-10 z-20 max-h-[360px] w-[420px] overflow-y-auto border border-line-strong bg-surface py-1"
        >
          {hits.length === 0 ? (
            <li className="px-3 py-2 text-[12.5px] text-text-muted">
              No field is named by it, and no passage in the map contains it.
            </li>
          ) : null}
          {hits.map((hit) => (
            <li key={hit.area.area_id}>
              <button
                type="button"
                onClick={() => {
                  setHits(null)
                  onPick(hit)
                }}
                className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left hover:bg-surface-raised"
              >
                <span className="text-[13px] text-text">{hit.area.name}</span>
                <span className="font-mono text-[10.5px] text-text-faint">
                  {hit.match === 'name'
                    ? 'named by it'
                    : `${hit.hits.toLocaleString('en')} passage${hit.hits === 1 ? '' : 's'} mention it`}
                  {' · '}
                  {hit.path.length > 1 ? 'in ' : ''}
                  {hit.path
                    .slice(0, -1)
                    .map((c) => c.name)
                    .join(' › ') || 'a field'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </form>
  )
}

function ContextMenu({
  menu,
  level,
  actions,
  onClose,
  onOpen,
  onRoute,
}: {
  menu: NonNullable<Menu>
  level: AreasLevel
  actions?: MapActions
  onClose: () => void
  onOpen: (area: Area) => void
  onRoute?: (area: Area) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    function onDown(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) onClose()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    ref.current?.querySelector<HTMLElement>('[role=menuitem]:not([disabled])')?.focus()
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onDown)
    }
  }, [onClose])

  // Kept inside the canvas: flipped left or up from the cursor when it would
  // overflow, re-measured whenever the menu's content changes size.
  const [style, setStyle] = useState({ left: Math.max(8, menu.x), top: Math.max(8, menu.y) })
  useLayoutEffect(() => {
    const element = ref.current
    const frame = element?.parentElement
    if (!element || !frame) return
    const place = () => {
      const w = element.offsetWidth
      const h = element.offsetHeight
      const maxX = frame.clientWidth - w - 8
      const maxY = frame.clientHeight - h - 8
      const left = menu.x > maxX ? Math.max(8, menu.x - w) : Math.max(8, menu.x)
      const top = menu.y > maxY ? Math.max(8, Math.min(maxY, menu.y - h)) : Math.max(8, menu.y)
      setStyle((s) => (s.left === left && s.top === top ? s : { left, top }))
    }
    place()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(place)
    observer.observe(element)
    return () => observer.disconnect()
  }, [menu.x, menu.y])

  if (menu.kind === 'empty') {
    const content = actions?.emptySpace?.({ x: menu.x, y: menu.y }, level.areas, onClose)
    if (!content) return null
    return (
      <div ref={ref} className="absolute z-20" style={style}>
        {content}
      </div>
    )
  }

  const area = menu.area
  const find = `/?q=${encodeURIComponent(area.terms.slice(0, 3).join(' '))}`
  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Steer from ${area.name}`}
      className="absolute z-20 w-[280px] border border-line-strong bg-surface py-1.5"
      style={style}
    >
      <div className="px-3 py-1.5 font-mono text-[9.5px] uppercase tracking-[var(--tracking-label)] text-text-faint">
        {area.name}
      </div>
      {actions?.areaItems?.(area, onClose)}
      {area.children > 0 ? (
        <MenuItem
          onClick={() => {
            onClose()
            onOpen(area)
          }}
        >
          Zoom in
        </MenuItem>
      ) : null}
      {onRoute ? (
        <MenuItem onClick={() => onRoute(area)} note="then click where to; the way the lines join them">
          Route from here…
        </MenuItem>
      ) : null}
      <MenuItem
        onClick={() => {
          onClose()
          navigate(find)
        }}
      >
        Open in Find
      </MenuItem>
    </div>
  )
}

export function MenuItem({
  children,
  note,
  disabled = false,
  onClick,
}: {
  children: React.ReactNode
  note?: string
  disabled?: boolean
  onClick?: () => void
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left text-[13px] text-text hover:bg-surface-raised focus:bg-surface-raised disabled:cursor-not-allowed disabled:text-text-faint disabled:hover:bg-transparent"
    >
      {children}
      {note ? <span className="text-[11.5px] text-text-faint">{note}</span> : null}
    </button>
  )
}

// --------------------------------------------------------------------------
// Panels

function PanelHead({ label, title, onClose }: { label: string; title: string; onClose: () => void }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[9.5px] uppercase tracking-[var(--tracking-label)] text-text-faint">
          {label}
        </span>
        <button type="button" onClick={onClose} aria-label="Close" className="px-1 text-text-faint hover:text-text">
          ×
        </button>
      </div>
      <h2 className="m-0 text-[19px] font-semibold leading-[1.3] text-text">{title}</h2>
    </div>
  )
}

function Chip({ children, tone = 'plain' }: { children: React.ReactNode; tone?: 'plain' | 'graph' }) {
  return (
    <span
      className={`border px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] ${
        tone === 'graph' ? 'border-accent-graph text-accent-graph' : 'border-line-strong text-text-muted'
      }`}
    >
      {children}
    </span>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="mt-1 font-mono text-[9.5px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
      {children}
    </h3>
  )
}

/** Topic share or research share as the circles' fill (`P6-42`). */
function ShadeControl({ value, onChange }: { value: Shade; onChange: (shade: Shade) => void }) {
  const options: { shade: Shade; label: string; title: string }[] = [
    { shade: 'topic', label: 'Topics', title: 'Fill by the share of passages on your topics' },
    { shade: 'research', label: 'Research', title: 'Fill by the share of passages from peer-reviewed sources' },
  ]
  return (
    <div
      role="group"
      aria-label="Fill shows"
      className="flex shrink-0 items-stretch border border-line-strong bg-surface"
    >
      {options.map((o) => (
        <button
          key={o.shade}
          type="button"
          aria-pressed={value === o.shade}
          title={o.title}
          onClick={() => onChange(o.shade)}
          className={`h-8 px-2.5 font-mono text-[11px] ${
            value === o.shade ? 'bg-surface-raised text-text' : 'text-text-faint hover:text-text-muted'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** A route between two areas (`P6-39`): each hop, cited or only similar, and a way into its bridge. */
function RoutePanel({
  route,
  areas,
  noun,
  onBridge,
  onClose,
}: {
  route: Extract<Panel, { kind: 'route' }>
  areas: Map<number, Area>
  noun: string
  onBridge: (link: AreaLink) => void
  onClose: () => void
}) {
  const title = `${route.from.name} → ${route.to.name}`
  const name = (id: number) => areas.get(id)?.name ?? `#${id}`
  if (route.hops === null) {
    return (
      <>
        <PanelHead label="Route" title={title} onClose={onClose} />
        <p className="text-[13px] leading-[1.55] text-text">No chain of lines joins these two {noun}s on this map.</p>
        <p className="text-[12.5px] leading-[1.55] text-text-muted">
          That is a finding, not a failure: nothing the corpus holds connects them at this level, cited or by
          resemblance. It is a gap worth a search from either side.
        </p>
      </>
    )
  }
  const similarOnly = route.hops.filter((h) => h.cited_claims === 0).length
  return (
    <>
      <PanelHead label="Route" title={title} onClose={onClose} />
      <p className="font-mono text-[11px] text-text-faint">
        {route.hops.length} hop{route.hops.length === 1 ? '' : 's'} ·{' '}
        {similarOnly === 0 ? 'every hop cited' : `${similarOnly} by resemblance only`}
      </p>
      <ol className="flex flex-col gap-2">
        {route.hops.map((hop, i) => {
          const cited = hop.cited_claims > 0
          return (
            <li key={`${hop.area_a}-${hop.area_b}`} className="border border-line bg-surface-raised p-3">
              <div className="text-[13px] leading-[1.45] text-text">
                <span className="font-mono text-[10.5px] text-text-faint">{i + 1}.</span> {name(hop.area_a)} →{' '}
                {name(hop.area_b)}
              </div>
              <div className="mt-1.5 flex items-center gap-2">
                {cited ? <Chip tone="graph">cited</Chip> : <Chip>similar only</Chip>}
                <span className="font-mono text-[10.5px] text-text-faint">
                  {cited
                    ? `${hop.cited_claims} claim${hop.cited_claims === 1 ? '' : 's'} · ${hop.cited_sources} source${hop.cited_sources === 1 ? '' : 's'}`
                    : `${hop.similar_pairs} similar passage pair${hop.similar_pairs === 1 ? '' : 's'}`}
                </span>
                <button type="button" onClick={() => onBridge(hop)} className="ml-auto text-[12px] text-accent-graph">
                  Bridge
                </button>
              </div>
            </li>
          )
        })}
      </ol>
    </>
  )
}

function BridgePanel({
  panel,
  areas,
  onClose,
}: {
  panel: Extract<Panel, { kind: 'bridge' }>
  areas: Map<number, Area>
  onClose: () => void
}) {
  const a = areas.get(panel.a)
  const b = areas.get(panel.b)
  const title = `${a?.name ?? panel.bridge?.a.name ?? '…'} ↔ ${b?.name ?? panel.bridge?.b.name ?? '…'}`
  const bridge = panel.bridge
  return (
    <>
      <PanelHead label="Bridge" title={title} onClose={onClose} />
      {panel.error ? <p className="text-[13px] text-text">{panel.error}</p> : null}
      {!bridge && !panel.error ? <p className="font-mono text-[11px] text-text-faint">Reading the bridge.</p> : null}
      {bridge ? (
        <>
          <div className="flex items-center gap-2">
            {bridge.claims.length ? <Chip tone="graph">claim · cited</Chip> : <Chip>no cited claim</Chip>}
            <span className="font-mono text-[11px] text-text-faint">
              {bridge.claims.length} claim{bridge.claims.length === 1 ? '' : 's'} · {bridge.cited_sources} source
              {bridge.cited_sources === 1 ? '' : 's'}
            </span>
          </div>
          {bridge.claims.length ? (
            <ul className="flex flex-col gap-2.5">
              {bridge.claims.map((claim) => (
                <li
                  key={claim.edge_id}
                  className="border border-line bg-surface-raised p-3 text-[13px] leading-[1.5] text-text"
                >
                  <a
                    href={`/nodes/${claim.from_node}`}
                    onClick={onInternalClick(`/nodes/${claim.from_node}`)}
                    className="font-semibold text-text no-underline hover:underline"
                  >
                    {claim.from_name}
                  </a>{' '}
                  <span className="text-accent-graph">{claim.relation_type.replaceAll('_', ' ')}</span>{' '}
                  <a
                    href={`/nodes/${claim.to_node}`}
                    onClick={onInternalClick(`/nodes/${claim.to_node}`)}
                    className="font-semibold text-text no-underline hover:underline"
                  >
                    {claim.to_name}
                  </a>
                  <div className="mt-1.5 font-mono text-[10.5px] text-text-faint">
                    {claim.tiers.map((t) => t.replace('_', ' ')).join(', ') || 'no tier'} · {claim.citations} citation
                    {claim.citations === 1 ? '' : 's'}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12.5px] leading-[1.55] text-text-muted">
              No claim in the graph has evidence in both. What follows is similarity only: passages that read alike,
              which is not a source saying the two connect.
            </p>
          )}

          <SectionLabel>Terms both fields share</SectionLabel>
          {bridge.shared_terms.length ? (
            <div className="flex flex-wrap gap-1.5">
              {bridge.shared_terms.map((term) => (
                <Chip key={term}>{term}</Chip>
              ))}
            </div>
          ) : (
            <p className="text-[12.5px] text-text-muted">None among either field's distinctive terms.</p>
          )}

          <SectionLabel>Similar passages across the two</SectionLabel>
          {bridge.similar.length ? (
            <ul className="flex flex-col gap-3">
              {bridge.similar.map((pair) => (
                <li
                  key={`${pair.a.chunk_id}-${pair.b.chunk_id}`}
                  className="flex flex-col gap-1.5 border-t border-line pt-2"
                >
                  <span className="font-mono text-[10.5px] text-text-faint">cosine {pair.score.toFixed(2)}</span>
                  {[pair.a, pair.b].map((p) => (
                    <a
                      key={p.chunk_id}
                      href={hrefForSource(p.source_id)}
                      onClick={onInternalClick(hrefForSource(p.source_id))}
                      className="text-[12.5px] italic leading-[1.55] text-text-muted no-underline hover:text-text"
                    >
                      “{readable(p.snippet)}…”
                      <span className="mt-0.5 block font-mono text-[10px] not-italic text-text-faint">
                        {p.title ?? `source ${p.source_id}`} · {p.source_tier.replace('_', ' ')}
                      </span>
                    </a>
                  ))}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[12.5px] text-text-muted">No passage pair was recorded for these two.</p>
          )}
        </>
      ) : null}
    </>
  )
}

function AreaPanel({ panel, onClose }: { panel: Extract<Panel, { kind: 'area' }>; onClose: () => void }) {
  const detail = panel.detail
  return (
    <>
      <PanelHead
        label={detail ? capitalised(levelNoun(detail.area.level)) : 'Field'}
        title={detail?.area.name ?? '…'}
        onClose={onClose}
      />
      {panel.error ? <p className="text-[13px] text-text">{panel.error}</p> : null}
      {detail ? (
        <>
          <span className="font-mono text-[11px] text-text-faint">
            {detail.area.passages.toLocaleString('en')} passages · {detail.area.sources.toLocaleString('en')} sources
            {detail.area.newest_at ? ` · newest ${dayOf(detail.area.newest_at)}` : ''}
          </span>
          <TopicShare area={detail.area} />
          <TierMix mix={detail.area.tier_mix} total={detail.area.passages} />
          {detail.area.reasons.length ? (
            <p className="text-[12.5px] text-accent-attention">{detail.area.reasons.join('; ')}</p>
          ) : null}
          <SectionLabel>Its distinctive terms</SectionLabel>
          <div className="flex flex-wrap gap-1.5">
            {detail.area.terms.map((term) => (
              <Chip key={term}>{term}</Chip>
            ))}
          </div>
          <SectionLabel>Passages nearest its centre</SectionLabel>
          <ul className="flex flex-col gap-2.5">
            {detail.passages.map((p) => (
              <li key={p.chunk_id} className="border-t border-line pt-2">
                <a
                  href={hrefForSource(p.source_id)}
                  onClick={onInternalClick(hrefForSource(p.source_id))}
                  className="text-[12.5px] leading-[1.55] text-text-muted no-underline hover:text-text"
                >
                  “{p.snippet.trim()}…”
                  <span className="mt-0.5 block font-mono text-[10px] text-text-faint">
                    {p.title ?? `source ${p.source_id}`} · {p.source_tier.replace('_', ' ')}
                  </span>
                </a>
              </li>
            ))}
          </ul>
        </>
      ) : !panel.error ? (
        <p className="font-mono text-[11px] text-text-faint">Reading the field.</p>
      ) : null}
    </>
  )
}

function capitalised(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1)
}
