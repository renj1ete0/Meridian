import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { ApiError } from '../../lib/api'
import {
  getArea,
  getAreas,
  getBridge,
  jumpToArea,
  keyValuesAtScale,
  labelLines,
  levelCaption,
  linkWidth,
  placeAreas,
  edgeToEdge,
  fitRadius,
  radiusOf,
  type Area,
  type AreaDetail,
  type AreaJumpHit,
  type AreaLink,
  type AreasLevel,
  type Bridge,
  type Placed,
} from '../../lib/areas'
import { hrefForSource, navigate, onInternalClick } from '../../lib/route'

/**
 * The Map screen's default view (task P6-34): the corpus as nested areas.
 *
 * One level at a time — regions, then the areas inside one region, then its
 * sub-areas — so the picture stays readable whatever the corpus holds. Circle
 * *area* is passages collected, with a key drawn at the same scale; an amber
 * outline marks an area that is weak (few independent sources) or stale
 * (nothing new stored for months), and its reasons are in words on hover and
 * in the panel. Lines are bridges: solid where a claim in the graph is cited
 * across the two, dashed where the nearest passages are merely similar.
 *
 * **An area is a cluster, not a topic**, and the screen says so where it
 * lists them: names are the terms that set a cluster's passages apart, not a
 * label anybody chose.
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

type Menu =
  | { kind: 'area'; area: Area; x: number; y: number }
  | { kind: 'empty'; x: number; y: number }
  | null

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

/** `?area=` in the URL, so a level can be linked and Back walks up. */
export function areaFromSearch(search: string): number | null {
  const value = new URLSearchParams(search).get('area')
  const n = value ? Number(value) : NaN
  return Number.isInteger(n) && n > 0 ? n : null
}

export function AreasScreen({ actions }: { actions?: MapActions }) {
  const [parent, setParent] = useState<number | null>(() => areaFromSearch(window.location.search))
  const [load, setLoad] = useState<Load>({ status: 'loading' })

  useEffect(() => {
    const onPop = () => setParent(areaFromSearch(window.location.search))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoad((current) => ({ status: 'loading', level: current.status === 'ready' ? current.level : undefined }))
    getAreas(parent, { signal: controller.signal })
      .then((level) => setLoad({ status: 'ready', level }))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setLoad({ status: 'error', message: messageOf(cause, 'The areas did not load.') })
      })
    return () => controller.abort()
  }, [parent])

  const goTo = useCallback((areaId: number | null) => {
    const search = new URLSearchParams(window.location.search)
    if (areaId === null) search.delete('area')
    else search.set('area', String(areaId))
    const query = search.toString()
    navigate(`/map${query ? `?${query}` : ''}`)
    setParent(areaId)
  }, [])

  if (load.status === 'error') {
    return <p className="p-6 text-text">{load.message}</p>
  }
  if (!load.level) {
    return <p className="p-6 font-mono text-[length:var(--text-data)] text-text-muted">Reading the areas.</p>
  }
  return <AreasView level={load.level} onLevel={goTo} actions={actions} loading={load.status === 'loading'} />
}

/** Everything but the fetch of the level, so tests render it directly. */
export function AreasView({
  level,
  onLevel,
  actions,
  loading = false,
}: {
  level: AreasLevel
  onLevel: (areaId: number | null) => void
  actions?: MapActions
  loading?: boolean
}) {
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
          setPanel((p) => (p.kind === 'area' ? { ...p, error: messageOf(cause, 'This area did not load.') } : p))
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

  const openArea = useCallback(
    (area: Area) => {
      setMenu(null)
      if (area.children > 0) onLevel(area.area_id)
      else setPanel({ kind: 'area', areaId: area.area_id })
    },
    [onLevel],
  )

  const empty = level.build === null
  const top = level.parent === null

  if (empty) {
    return (
      <div className="flex-1 p-6">
        <div className="max-w-xl border border-line bg-surface p-5">
          <h2 className="text-[length:var(--text-subhead)] font-semibold text-text">No areas yet</h2>
          <p className="mt-2 text-[length:var(--text-small)] leading-[var(--leading-small)] text-text-muted">
            Areas are built once a day by <span className="font-mono">worker.areas</span> from every searchable,
            embedded passage — at least {level.passages_needed.toLocaleString('en')} of them. Nothing has been
            built on this corpus yet. The passages themselves can be seen now, under Passages in 3D.
          </p>
        </div>
      </div>
    )
  }

  const siblings = level.areas
  const byId = new Map(siblings.map((a) => [a.area_id, a]))

  return (
    <div className="flex min-h-0 flex-1">
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

      <div data-theme="dark" className="relative min-w-0 flex-1 bg-ground-deep text-text">
        <Canvas
          level={level}
          hover={hover}
          highlight={highlight}
          selectedLink={panel.kind === 'bridge' ? [panel.a, panel.b] : null}
          onHover={setHover}
          onOpen={openArea}
          onLink={(link) => setPanel({ kind: 'bridge', a: link.area_a, b: link.area_b })}
          onMenu={setMenu}
          onScale={setScale}
        />

        <div className="absolute left-5 top-4 z-[1] flex items-center gap-2.5">
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
          <span className="font-mono text-[11px] text-text-faint" aria-live="polite">
            {levelCaption(level)}
            {loading ? ' · loading' : ''}
          </span>
        </div>

        <SizeKey areas={siblings} scale={scale} />
        <p className="pointer-events-none absolute bottom-4 left-5 font-mono text-[11px] text-text-faint">
          size = passages · solid = a cited claim spans both, thicker = more sources · dashed = similar
          passages · click to zoom in · right-click for more
        </p>

        {hover !== null && byId.get(hover) ? <AreaTip area={byId.get(hover)!} /> : null}

        {menu ? (
          <ContextMenu
            menu={menu}
            level={level}
            actions={actions}
            onClose={() => setMenu(null)}
            onOpen={openArea}
          />
        ) : null}
      </div>

      {panel.kind !== 'none' ? (
        <aside
          aria-label={panel.kind === 'bridge' ? 'Bridge' : 'Area'}
          className="flex w-[380px] shrink-0 flex-col gap-3.5 overflow-y-auto border-l border-line bg-surface p-5"
        >
          {panel.kind === 'bridge' ? (
            <BridgePanel
              panel={panel}
              areas={byId}
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

function Canvas({
  level,
  hover,
  highlight,
  selectedLink,
  onHover,
  onOpen,
  onLink,
  onMenu,
  onScale,
}: {
  level: AreasLevel
  hover: number | null
  highlight: number | null
  selectedLink: [number, number] | null
  onHover: (id: number | null) => void
  onOpen: (area: Area) => void
  onLink: (link: AreaLink) => void
  onMenu: (menu: Menu) => void
  onScale: (scale: number) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const { width, height } = useSize(box)
  // Keyboard focus on a line draws it as selected: the hit line is invisible,
  // so an outline would be a box round nothing.
  const [focused, onFocusLink] = useState<[number, number] | null>(null)
  // Room at the top for the jump box and at the bottom for the key.
  const top = 64
  const bottom = 40
  const drawHeight = Math.max(200, height - top - bottom)
  const maxRadius = fitRadius(
    level.areas.map((a) => a.passages),
    width,
    drawHeight,
  )
  const { placed, scale } = useMemo(
    () =>
      placeAreas(level.areas, width, drawHeight, {
        maxRadius,
        gap: 14,
        pad: 28,
        // Where the size key sits, bottom left.
        avoid: [{ x: 0, y: drawHeight - KEY_BOX.h, w: KEY_BOX.w, h: KEY_BOX.h }],
      }),
    [level.areas, width, drawHeight, maxRadius],
  )
  useEffect(() => onScale(scale), [scale, onScale])
  const childNoun = level.level + 1 >= level.levels ? 'sub-area' : 'area'
  const at = new Map(placed.map((p) => [p.area.area_id, p]))

  function contextMenu(event: React.MouseEvent, area: Area | null) {
    event.preventDefault()
    event.stopPropagation()
    const frame = box.current?.getBoundingClientRect()
    const x = event.clientX - (frame?.left ?? 0)
    const y = event.clientY - (frame?.top ?? 0)
    onMenu(area ? { kind: 'area', area, x, y } : { kind: 'empty', x, y })
  }

  const cx = width / 2
  const cy = top + drawHeight / 2
  const globe = Math.min(width, drawHeight) * 0.46

  return (
    <div ref={box} className="absolute inset-0" onContextMenu={(event) => contextMenu(event, null)}>
      <svg width={width} height={height} role="img" aria-label="Areas of the corpus" className="block">
        <g fill="none" stroke="var(--dark-canvas-graticule)">
          <circle cx={cx} cy={cy} r={globe} />
          <ellipse cx={cx} cy={cy} rx={globe * 0.42} ry={globe} />
        </g>
        <g transform={`translate(0 ${top})`}>
          {placed.map((p) => (
            <AreaCircle
              key={p.area.area_id}
              placed={p}
              childNoun={childNoun}
              hovered={hover === p.area.area_id}
              highlighted={highlight === p.area.area_id}
              onHover={onHover}
              onOpen={onOpen}
              onMenu={contextMenu}
            />
          ))}
          {level.links.map((link) => {
            const a = at.get(link.area_a)
            const b = at.get(link.area_b)
            if (!a || !b) return null
            // Edge to edge, not centre to centre: a line drawn through a
            // circle crosses its label and makes the circle hard to click.
            const segment = edgeToEdge(a, b)
            if (!segment) return null
            const [x1, y1, x2, y2] = segment
            const cited = link.cited_claims > 0
            const selected =
              (selectedLink?.includes(link.area_a) && selectedLink.includes(link.area_b)) ||
              (focused?.[0] === link.area_a && focused[1] === link.area_b)
            const label = `${a.area.name} and ${b.area.name}: ${
              cited
                ? `${link.cited_claims} cited claim${link.cited_claims === 1 ? '' : 's'}, ${link.cited_sources} source${link.cited_sources === 1 ? '' : 's'}`
                : 'similar passages, no cited claim'
            }`
            return (
              <g key={`${link.area_a}-${link.area_b}`}>
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
        </g>
      </svg>
    </div>
  )
}

function AreaCircle({
  placed,
  childNoun,
  hovered,
  highlighted,
  onHover,
  onOpen,
  onMenu,
}: {
  placed: Placed
  childNoun: string
  hovered: boolean
  highlighted: boolean
  onHover: (id: number | null) => void
  onOpen: (area: Area) => void
  onMenu: (event: React.MouseEvent, area: Area) => void
}) {
  const { area, x, y, r } = placed
  const flagged = area.weak || area.stale
  const lines = labelLines(area.name)
  // Inside the circle when the longest line fits across it, else below it.
  const longest = Math.max(...lines.map((line) => line.length))
  const inside = r * 2 >= longest * (r >= 60 ? 7 : 6.2) + 10
  const labelY = inside ? y - (lines.length - 1) * 8 : y + r + 14
  const caption =
    area.children > 0
      ? `${area.children} ${childNoun}${area.children === 1 ? '' : 's'} inside`
      : `${area.passages.toLocaleString('en')} passages`
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`${area.name}: ${area.passages.toLocaleString('en')} passages from ${area.sources.toLocaleString('en')} sources${
        flagged ? ` — ${area.reasons.join('; ')}` : ''
      }${area.children > 0 ? '. Open to zoom in.' : ''}`}
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
    >
      <circle
        cx={x}
        cy={y}
        r={r}
        fill="var(--accent-graph-deep)"
        fillOpacity={hovered || highlighted ? 0.5 : 0.3}
        stroke={flagged ? 'var(--accent-attention)' : 'var(--accent-graph)'}
        strokeWidth={highlighted ? 2.4 : 1.2}
        strokeDasharray={flagged ? '5 4' : undefined}
      />
      {lines.map((line, i) => (
        <text
          key={i}
          x={x}
          y={labelY + i * 16}
          textAnchor="middle"
          fontFamily="var(--font-sans)"
          fontSize={r >= 60 ? 15 : 13}
          fontWeight={600}
          fill="var(--text)"
          paintOrder="stroke"
          stroke="var(--ground-deep)"
          strokeWidth={3.5}
        >
          {line}
        </text>
      ))}
      <text
        x={x}
        y={labelY + lines.length * 16 + 2}
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
    </g>
  )
}

/**
 * The size key, drawn at the canvas's own scale: a key circle is exactly the
 * size an area of that many passages is drawn at, so the two can be compared
 * by eye. Values that would not fit the key are left out rather than shrunk.
 */
function SizeKey({ areas, scale }: { areas: readonly Area[]; scale: number }) {
  const values = keyValuesAtScale(
    areas.map((a) => a.passages),
    scale,
    KEY_RADIUS,
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

/** The largest circle the key will draw. */
const KEY_RADIUS = 34
/** The room the key takes over the canvas, which circles keep out of. */
const KEY_BOX = { w: 230, h: 150 }

function AreaTip({ area }: { area: Area }) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute right-5 top-16 z-10 flex w-[280px] flex-col gap-1.5 border border-text/16 bg-surface/90 px-[13px] py-[11px] backdrop-blur-[10px]"
    >
      <span className="text-[13px] font-semibold leading-snug text-text">{area.name}</span>
      <span className="font-mono text-[10.5px] text-text-faint">
        {area.passages.toLocaleString('en')} passages · {area.sources.toLocaleString('en')} sources
        {area.newest_at ? ` · newest ${area.newest_at.slice(0, 10)}` : ''}
      </span>
      <TierMix mix={area.tier_mix} total={area.passages} />
      {area.reasons.length ? (
        <span className="text-[length:var(--text-small)] text-accent-attention">{area.reasons.join('; ')}</span>
      ) : null}
    </div>
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
  const noun = level.level === level.levels ? 'Sub-areas' : 'Areas'
  return (
    <aside className="flex w-[280px] shrink-0 flex-col gap-3.5 overflow-y-auto border-r border-line bg-surface p-[18px]">
      <nav aria-label="Where you are" className="flex flex-col gap-1 font-mono text-[11px]">
        <a
          href="/map"
          className="text-accent-graph no-underline"
          onClick={(event) => {
            event.preventDefault()
            onLevel(null)
          }}
        >
          All areas
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
        {noun} in this {level.level === 2 ? 'region' : 'area'}
      </h2>
      <ul className="flex flex-col">
        {level.areas.map((area) => (
          <li key={area.area_id} className="border-t border-line">
            <button
              type="button"
              onClick={() => onPick(area)}
              className="flex w-full items-baseline justify-between gap-3 py-2 text-left text-[13px] text-text hover:text-accent-graph"
            >
              <span className={area.weak || area.stale ? 'underline decoration-accent-attention decoration-dashed underline-offset-4' : undefined}>
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
          <span className="h-3.5 w-3.5 rounded-full border border-accent-attention" style={{ borderStyle: 'dashed' }} aria-hidden />
          amber outline — fewer than {level.weak_below_sources} sources, or nothing new in {level.stale_after_days}{' '}
          days
        </li>
      </ul>

      <p className="mt-auto text-[12px] leading-[1.55] text-text-faint">
        Areas are clusters of passages, named by their most distinctive terms. Topics are a filter over them,
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
    <form role="search" onSubmit={search} className="relative">
      <input
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          if (!event.target.value) setHits(null)
        }}
        placeholder="Jump to an area or a term"
        aria-label="Jump to an area or a term"
        className="h-9 w-[340px] border border-line-strong bg-surface px-3 text-[13.5px] text-text placeholder:text-text-faint"
      />
      {error ? <p className="absolute left-0 top-10 w-[340px] border border-line bg-surface p-2 text-[12px] text-text">{error}</p> : null}
      {hits ? (
        <ul
          aria-label="Areas found"
          className="absolute left-0 top-10 z-20 max-h-[360px] w-[420px] overflow-y-auto border border-line-strong bg-surface py-1"
        >
          {hits.length === 0 ? (
            <li className="px-3 py-2 text-[12.5px] text-text-muted">
              No area is named by it, and no passage in the map contains it.
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
                    .join(' › ') || 'a region'}
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
}: {
  menu: NonNullable<Menu>
  level: AreasLevel
  actions?: MapActions
  onClose: () => void
  onOpen: (area: Area) => void
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
      <MenuItem
        onClick={() => {
          onClose()
          navigate(find)
        }}
      >
        Open in Find
      </MenuItem>
      <div className="my-1.5 border-t border-line" />
      <MenuItem disabled note="Route search across areas (P6-32) is not built yet.">
        Route from here…
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
        <span className="font-mono text-[9.5px] uppercase tracking-[var(--tracking-label)] text-text-faint">{label}</span>
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
                <li key={claim.edge_id} className="border border-line bg-surface-raised p-3 text-[13px] leading-[1.5] text-text">
                  <a href={`/nodes/${claim.from_node}`} onClick={onInternalClick(`/nodes/${claim.from_node}`)} className="font-semibold text-text no-underline hover:underline">
                    {claim.from_name}
                  </a>{' '}
                  <span className="text-accent-graph">{claim.relation_type.replaceAll('_', ' ')}</span>{' '}
                  <a href={`/nodes/${claim.to_node}`} onClick={onInternalClick(`/nodes/${claim.to_node}`)} className="font-semibold text-text no-underline hover:underline">
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
              No claim in the graph has evidence in both. What follows is similarity only: passages that read
              alike, which is not a source saying the two connect.
            </p>
          )}

          <SectionLabel>Terms both areas share</SectionLabel>
          {bridge.shared_terms.length ? (
            <div className="flex flex-wrap gap-1.5">
              {bridge.shared_terms.map((term) => (
                <Chip key={term}>{term}</Chip>
              ))}
            </div>
          ) : (
            <p className="text-[12.5px] text-text-muted">None among either area's distinctive terms.</p>
          )}

          <SectionLabel>Similar passages across the two</SectionLabel>
          {bridge.similar.length ? (
            <ul className="flex flex-col gap-3">
              {bridge.similar.map((pair) => (
                <li key={`${pair.a.chunk_id}-${pair.b.chunk_id}`} className="flex flex-col gap-1.5 border-t border-line pt-2">
                  <span className="font-mono text-[10.5px] text-text-faint">cosine {pair.score.toFixed(2)}</span>
                  {[pair.a, pair.b].map((p) => (
                    <a
                      key={p.chunk_id}
                      href={hrefForSource(p.source_id)}
                      onClick={onInternalClick(hrefForSource(p.source_id))}
                      className="text-[12.5px] italic leading-[1.55] text-text-muted no-underline hover:text-text"
                    >
                      “{p.snippet.trim()}…”
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
      <PanelHead label="Area" title={detail?.area.name ?? '…'} onClose={onClose} />
      {panel.error ? <p className="text-[13px] text-text">{panel.error}</p> : null}
      {detail ? (
        <>
          <span className="font-mono text-[11px] text-text-faint">
            {detail.area.passages.toLocaleString('en')} passages · {detail.area.sources.toLocaleString('en')} sources
            {detail.area.newest_at ? ` · newest ${detail.area.newest_at.slice(0, 10)}` : ''}
          </span>
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
        <p className="font-mono text-[11px] text-text-faint">Reading the area.</p>
      ) : null}
    </>
  )
}
