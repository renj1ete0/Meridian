import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { NodePanel } from './NodePanel'
import {
  getGraphNode,
  getNeighbourhood,
  getPath,
  saveGraphView,
  type GraphNodeDetail,
  type GraphPath,
  type Neighbourhood,
} from './graph/api'
import {
  BUILT_VIEWS,
  NO_FILTERS,
  VIEWS,
  VIEW_LABEL,
  filtersFromRecord,
  parseSearch,
  toSearch,
  type GraphView,
  type WorkspaceUrlState,
} from './graph/filters'
import { FilterRail } from './graph/FilterRail'
import { GraphCanvas, type CanvasApi, type HoverTarget } from './graph/GraphCanvas'
import { NodeSearchBox } from './graph/NodeSearchBox'
import { readPalette } from './graph/palette'
import { recordRecentNode } from './graph/recent'
import { neighbourhoodScene, pathScene } from './graph/scene'
import { MatrixView, adjacency, matrixLine } from './graph/MatrixView'
import { TableView } from './graph/TableView'
import { nextTrail, readTrail, writeTrail, type Crumb } from './graph/trail'
import {
  ApiError,
  getSavedViews,
  markViewOpened,
  writeAnnotation,
  type NoteDraft,
  type SavedViewRecord,
} from '../lib/api'
import { hrefForNode, navigate } from '../lib/route'

/**
 * The graph workspace at `/nodes/{id}` (tasks P6-01, P6-02, P6-03; spec §12.2,
 * §12.3; design `Explore`).
 *
 * §12.2's focus + expand, laid out as the artboard draws it: filters on the
 * left, the canvas in the middle, the node panel on the right, filling the
 * viewport under the top bar. The URL is the focus and the filters, so any
 * state a reader can see is a state they can link — which is the same reason
 * `P6-04` gave nodes URLs before there was a canvas to put them on.
 *
 * **Refocusing is navigation.** Clicking a neighbour moves to its URL, so the
 * browser's back button is the breadcrumb's back, and a refocused view can be
 * pasted into a note.
 *
 * It also owns the one write on this screen (`P6-05`): a note is re-fetched
 * rather than spliced into the panel, because the server decides what a note
 * ends up being.
 */

/** §12.2: "capped at ~30". Expand shows the next batch, up to the API's ceiling. */
export const PAGE = 30
export const CEILING = 100

/** The top bar's height when there is no header to measure. The artboard's. */
const FALLBACK_TOP = 54

/**
 * Where the top bar ends. The workspace fills the viewport under it, whatever
 * the shell around it draws — so it measures rather than assumes a height.
 */
function useTopOffset(): number {
  const [top, setTop] = useState(FALLBACK_TOP)
  useEffect(() => {
    const header = document.querySelector('header')
    if (!header) return
    const measure = () => setTop(Math.max(0, Math.round(header.getBoundingClientRect().bottom)))
    measure()
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(header)
    window.addEventListener('resize', measure)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [])
  return top
}

export function statusLine(hood: Neighbourhood): string {
  const parts = [
    'depth 1',
    `${hood.shown} of ${hood.total} neighbour${hood.total === 1 ? '' : 's'} shown`,
    'ranked by supporting passages',
  ]
  if (hood.unfiltered > hood.total) parts.push(`${hood.unfiltered - hood.total} filtered out`)
  return parts.join(' · ')
}

export function expandBlocked(hood: Neighbourhood, limit: number): string | null {
  if (hood.shown >= hood.total) {
    return hood.total === 0
      ? 'Nothing to expand: no neighbour is shown.'
      : `All ${hood.total} neighbours are shown.`
  }
  if (limit >= CEILING) return `The canvas stops at ${CEILING} neighbours. Filter to narrow it.`
  return null
}

type PathState =
  | { phase: 'off' }
  | { phase: 'picking' }
  | { phase: 'loading'; target: number }
  | { phase: 'shown'; path: GraphPath }
  | { phase: 'failed'; message: string }

const BTN =
  'border border-line-strong bg-surface-raised px-3 py-[7px] text-[12.5px] text-text-muted hover:text-text disabled:opacity-50'

const OFFERED_VIEWS = VIEWS.filter((v) => BUILT_VIEWS.has(v))

export function NodePage({ entityId }: { entityId: number }) {
  const top = useTopOffset()
  const [url, setUrl] = useState<WorkspaceUrlState>(() => parseSearch(window.location.search))
  const [limit, setLimit] = useState(PAGE)
  const [hood, setHood] = useState<Neighbourhood | null>(null)
  const [hoodError, setHoodError] = useState<string | null>(null)
  const [detail, setDetail] = useState<GraphNodeDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [views, setViews] = useState<SavedViewRecord[] | null>(null)
  const [saveState, setSaveState] = useState({ busy: false, error: null as string | null, saved: null as string | null })
  const [trail, setTrail] = useState<Crumb[]>(readTrail)
  const [hover, setHover] = useState<HoverTarget | null>(null)
  const [path, setPath] = useState<PathState>({ phase: 'off' })
  const [unavailable, setUnavailable] = useState<string | null>(null)
  const [writing, setWriting] = useState(false)
  const [writeError, setWriteError] = useState<string | null>(null)
  const [written, setWritten] = useState(0)
  const canvas = useRef<CanvasApi | null>(null)
  const shown = useRef<number | null>(null)
  const panelShown = useRef<number | null>(null)
  const followed = useRef<number | null>(null)

  // Back and forward change the query string as well as the path.
  useEffect(() => {
    // Only when the query actually changed: a new object with the same
    // content would refetch the neighbourhood for nothing.
    const onPop = () => {
      const next = parseSearch(window.location.search)
      setUrl((current) => (toSearch(current) === toSearch(next) ? current : next))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  // A new focus starts at the first page, out of path mode.
  useEffect(() => {
    setLimit(PAGE)
    setPath({ phase: 'off' })
    setHover(null)
  }, [entityId])

  useEffect(() => {
    const controller = new AbortController()
    setHoodError(null)
    if (shown.current !== entityId) setHood(null)
    getNeighbourhood(entityId, url.filters, limit, { signal: controller.signal })
      .then((next) => {
        if (next.redirects_to !== null && followed.current !== entityId) {
          // Once per node: a redirect chain that loops back must not become a
          // request loop.
          followed.current = entityId
          // §5.5: a merged node is a redirect. Follow it, replacing the URL,
          // so back does not bounce the reader into it again.
          window.history.replaceState({}, '', `${hrefForNode(next.redirects_to)}${toSearch(url)}`)
          window.dispatchEvent(new PopStateEvent('popstate'))
          return
        }
        shown.current = entityId
        setHood(next)
        const crumb = { id: next.focus.entity_id, name: next.focus.canonical_name }
        setTrail((t) => {
          const updated = nextTrail(t, crumb)
          writeTrail(updated)
          return updated
        })
        recordRecentNode({ ...next.focus, contested: next.focus_contested })
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setHoodError(cause instanceof ApiError ? cause.message : 'The neighbourhood could not be loaded.')
      })
    return () => controller.abort()
  }, [entityId, url, limit])

  useEffect(() => {
    const controller = new AbortController()
    setDetailError(null)
    // Blanked only when the focus moved. A refresh after writing a note is the
    // same node, and dropping the panel there would flash it away.
    if (panelShown.current !== entityId) setDetail(null)
    getGraphNode(entityId, { signal: controller.signal })
      .then((next) => {
        panelShown.current = entityId
        setDetail(next)
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setDetailError(cause instanceof ApiError ? cause.message : 'That concept could not be loaded.')
      })
    return () => controller.abort()
  }, [entityId, written])

  const loadViews = useCallback(() => {
    getSavedViews()
      .then((v) => setViews(v.views))
      .catch(() => setViews([]))
  }, [])
  useEffect(loadViews, [loadViews])

  function updateUrl(next: WorkspaceUrlState) {
    setUrl(next)
    setLimit(PAGE)
    window.history.replaceState({}, '', `${hrefForNode(entityId)}${toSearch(next)}`)
  }

  function refocus(id: number) {
    if (id === entityId) return
    navigate(`${hrefForNode(id)}${toSearch(url)}`)
  }

  function openView(view: SavedViewRecord) {
    void markViewOpened(view.view_id).catch(() => {})
    if (view.focus_entity_id === null) {
      navigate('/')
      return
    }
    const next = { filters: filtersFromRecord(view.filters), view: url.view }
    setUrl(next)
    navigate(`${hrefForNode(view.focus_entity_id)}${toSearch(next)}`)
  }

  function saveView(name: string) {
    setSaveState({ busy: true, error: null, saved: null })
    saveGraphView(name, entityId, url.filters)
      .then(() => {
        setSaveState({ busy: false, error: null, saved: name })
        loadViews()
      })
      .catch((cause: unknown) =>
        setSaveState({
          busy: false,
          error: cause instanceof ApiError ? cause.message : 'The view was not saved.',
          saved: null,
        }),
      )
  }

  function onWrite(draft: NoteDraft) {
    setWriting(true)
    setWriteError(null)
    writeAnnotation(draft)
      .then(() => setWritten((n) => n + 1))
      .catch((cause: unknown) =>
        setWriteError(cause instanceof ApiError ? cause.message : 'That note was not saved.'),
      )
      .finally(() => setWriting(false))
  }

  function pathTo(target: number) {
    if (target === entityId) return
    setPath({ phase: 'loading', target })
    setHover(null)
    getPath(entityId, target)
      .then((found) =>
        setPath(
          found.found
            ? { phase: 'shown', path: found }
            : {
                phase: 'failed',
                message: `No route within ${found.max_depth} hops. A gap is a finding: nothing in the graph joins these two yet.`,
              },
        ),
      )
      .catch((cause: unknown) =>
        setPath({
          phase: 'failed',
          message: cause instanceof ApiError ? cause.message : 'The path could not be found.',
        }),
      )
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPath({ phase: 'off' })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const scene = useMemo(() => {
    const palette = readPalette()
    if (path.phase === 'shown') return pathScene(path.path, palette)
    return hood ? neighbourhoodScene(hood, palette) : null
  }, [hood, path])

  const view: GraphView = unavailable && url.view === 'node-link' ? 'table' : url.view
  const focusName = hood?.focus.canonical_name ?? detail?.entity.canonical_name ?? `#${entityId}`
  const missing = hoodError && detailError

  return (
    <div className="fixed inset-x-0 bottom-0 z-10 flex bg-ground text-text" style={{ top }}>
      <FilterRail
        facets={hood?.facets ?? null}
        filters={url.filters}
        onChange={(filters) => updateUrl({ ...url, filters })}
        views={views}
        onOpenView={openView}
        onSaveView={saveView}
        saveState={saveState}
        onPickNode={(id) => (path.phase === 'picking' ? pathTo(id) : refocus(id))}
      />

      {/* The canvas is a dark ground in both themes (§2 publishes no light
          canvas), so everything drawn over it — breadcrumb, switcher, status,
          controls, hover card — takes the dark roles too. `data-theme` scopes
          the token remapping to this subtree. */}
      <section
        aria-label="Graph"
        data-theme="dark"
        className="relative min-w-0 flex-1 overflow-hidden bg-ground-deep text-text"
      >
        {missing ? (
          <Centre>
            <p className="text-[13px] text-accent-attention">{hoodError}</p>
            <p className="mt-2 text-[12.5px] text-text-muted">Find another concept from the rail.</p>
          </Centre>
        ) : hoodError ? (
          <Centre>
            <p className="text-[13px] text-accent-attention">{hoodError}</p>
          </Centre>
        ) : !hood || !scene ? (
          <Centre>
            <p className="font-mono text-[11px] text-text-faint">Loading.</p>
          </Centre>
        ) : !BUILT_VIEWS.has(view) ? (
          <Centre>
            <p className="text-[13px] text-text-muted">{VIEW_LABEL[view]} is not built yet.</p>
            <p className="mt-1 text-[12.5px] text-text-faint">
              Node-link, Table and Matrix show this neighbourhood.
            </p>
          </Centre>
        ) : view === 'table' || view === 'matrix' ? (
          hood.shown === 0 ? (
            <EmptyHood hood={hood} onClear={() => updateUrl({ ...url, filters: NO_FILTERS })} />
          ) : view === 'table' ? (
            <TableView hood={hood} />
          ) : (
            <MatrixView hood={hood} onPick={refocus} hrefFor={(id) => `${hrefForNode(id)}${toSearch(url)}`} />
          )
        ) : (
          <>
            <GraphCanvas
              scene={scene}
              apiRef={canvas}
              onNodeClick={(id) => (path.phase === 'picking' ? pathTo(id) : refocus(id))}
              onHover={setHover}
              onUnavailable={setUnavailable}
            />
            {hood.shown === 0 && path.phase !== 'shown' ? (
              <EmptyHood hood={hood} onClear={() => updateUrl({ ...url, filters: NO_FILTERS })} />
            ) : null}
            {hover ? <HoverCard target={hover} /> : null}
          </>
        )}

        {/* Breadcrumb, top left; view switcher, top right. */}
        <div className="pointer-events-none absolute inset-x-[18px] top-4 flex items-start justify-between gap-4">
          <Breadcrumb
            trail={trail}
            topic={hood?.focus.home_topics[0] ?? null}
            onPick={(id) => refocus(id)}
          />
          <div role="tablist" aria-label="View" className="pointer-events-auto flex shrink-0 border border-line bg-ground-deep">
            {/* Only the views that exist are offered: a tab whose whole content
                is "not built yet" is a dead end (`P6-44`). An old link naming
                one still opens, and says so. */}
            {OFFERED_VIEWS.map((v, i) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={url.view === v}
                onClick={() => updateUrl({ ...url, view: v })}
                className={`px-3 py-[7px] text-[12px] ${i < OFFERED_VIEWS.length - 1 ? 'border-r border-line' : ''} ${
                  url.view === v ? 'bg-surface-raised text-text' : 'text-text-faint hover:text-text-muted'
                }`}
              >
                {VIEW_LABEL[v]}
              </button>
            ))}
          </div>
        </div>

        {unavailable ? (
          <p className="absolute inset-x-[18px] top-14 text-[12px] text-accent-attention">{unavailable}</p>
        ) : null}

        {/* Status, bottom left. */}
        <div className="absolute bottom-4 left-[18px] right-[250px] font-mono text-[10.5px] text-text-faint">
          {path.phase === 'picking' ? (
            <div className="pointer-events-auto flex max-w-[360px] flex-col gap-2">
              <span>Path from {focusName}: click a concept, or find one.</span>
              <NodeSearchBox onPick={pathTo} placeholder="Path to…" autoFocus exclude={[entityId]} dropUp />
            </div>
          ) : path.phase === 'loading' ? (
            <span>Finding a route.</span>
          ) : path.phase === 'shown' ? (
            <span>
              path · {path.path.hops} hop{path.path.hops === 1 ? '' : 's'} ·{' '}
              {path.path.nodes.map((n) => n.canonical_name).join(' › ')}
            </span>
          ) : path.phase === 'failed' ? (
            <span className="text-accent-attention">{path.message}</span>
          ) : hood && view === 'matrix' && hood.shown > 0 ? (
            <span>{matrixLine(adjacency(hood), hood)}</span>
          ) : hood && view !== 'timeline' && view !== 'coverage' ? (
            <span>{statusLine(hood)}</span>
          ) : null}
        </div>

        {/* Controls, bottom right. */}
        {view === 'node-link' && hood ? (
          <div className="absolute bottom-4 right-[18px] flex gap-2">
            {path.phase === 'off' ? (
              <button type="button" className={BTN} onClick={() => setPath({ phase: 'picking' })}>
                Path from…
              </button>
            ) : (
              <button type="button" className={BTN} onClick={() => setPath({ phase: 'off' })}>
                Leave path
              </button>
            )}
            <button type="button" className={BTN} onClick={() => canvas.current?.fit()}>
              Fit
            </button>
            <button type="button" className={BTN} aria-label="Zoom out" onClick={() => canvas.current?.zoomOut()}>
              −
            </button>
            <button type="button" className={BTN} aria-label="Zoom in" onClick={() => canvas.current?.zoomIn()}>
              +
            </button>
          </div>
        ) : null}
      </section>

      {detail ? (
        <NodePanel
          detail={detail}
          onExpand={() => setLimit((l) => Math.min(CEILING, l + PAGE))}
          expandBlocked={hood ? expandBlocked(hood, limit) : 'Loading.'}
          onWrite={onWrite}
          writing={writing}
          writeError={writeError}
        />
      ) : (
        <aside className="w-[384px] shrink-0 border-l border-line bg-surface px-5 py-5">
          {detailError ? (
            <p className="text-[13px] text-accent-attention">{detailError}</p>
          ) : (
            <p className="font-mono text-[11px] text-text-faint">Loading.</p>
          )}
        </aside>
      )}
    </div>
  )
}

function Centre({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center px-8 text-center">
      {children}
    </div>
  )
}

function EmptyHood({ hood, onClear }: { hood: Neighbourhood; onClear: () => void }) {
  if (hood.unfiltered === 0) {
    return (
      <div className="pointer-events-none absolute inset-x-0 bottom-20 flex justify-center px-8 text-center">
        <p className="text-[12.5px] text-text-muted">
          Nothing is connected to {hood.focus.canonical_name} yet. No edge names it.
        </p>
      </div>
    )
  }
  return (
    <div className="absolute inset-x-0 bottom-20 flex flex-col items-center gap-2 px-8 text-center">
      <p className="text-[12.5px] text-text-muted">
        No neighbour survives these filters. {hood.unfiltered} without them.
      </p>
      {hood.total < hood.unfiltered ? (
        <button type="button" onClick={onClear} className={BTN}>
          Clear filters
        </button>
      ) : null}
    </div>
  )
}

function Breadcrumb({
  trail,
  topic,
  onPick,
}: {
  trail: readonly Crumb[]
  topic: string | null
  onPick: (id: number) => void
}) {
  const visible = trail.slice(-4)
  return (
    <nav aria-label="Trail" className="pointer-events-auto flex min-w-0 flex-wrap items-center gap-2 font-mono text-[10.5px]">
      {topic ? <span className="text-text-faint">{topic.replaceAll('-', ' ')}</span> : null}
      {trail.length > visible.length ? <span className="text-text-faint">…</span> : null}
      {visible.map((crumb, i) => {
        const last = i === visible.length - 1
        return (
          <span key={crumb.id} className="flex min-w-0 items-center gap-2">
            {i > 0 || topic || trail.length > visible.length ? (
              <span className="text-line-strong" aria-hidden="true">
                ›
              </span>
            ) : null}
            {last ? (
              <span aria-current="page" className="truncate text-text">
                {crumb.name}
              </span>
            ) : (
              <button type="button" onClick={() => onPick(crumb.id)} className="truncate text-text-faint hover:text-text-muted">
                {crumb.name}
              </button>
            )}
          </span>
        )
      })}
    </nav>
  )
}

export function hoverLines(target: HoverTarget): { title: string; kind: string; stats: string } {
  const n = target.node.node
  const kind = [n.node_type.replaceAll('_', ' ')]
  if (n.role === 'hint') kind.push('second hop')
  else if (n.topics.length) kind.push(`${n.topics.length} topic${n.topics.length === 1 ? '' : 's'}`)
  const stats =
    n.role === 'hint'
      ? 'click to focus'
      : [
          `${n.degree} link${n.degree === 1 ? '' : 's'}`,
          `${n.sources} source${n.sources === 1 ? '' : 's'}`,
          n.newest ? `newest ${n.newest.slice(0, 7)}` : 'undated',
        ].join(' · ')
  return { title: n.canonical_name, kind: kind.join(' · '), stats }
}

/** Right of the node, or left of it when that would leave the canvas. */
export function cardPosition(target: HoverTarget, card = { width: 230, height: 90 }) {
  const left = target.x + 18 + card.width > target.width - 12 ? target.x - 18 - card.width : target.x + 18
  const top = Math.min(Math.max(12, target.y - 12), Math.max(12, target.height - card.height - 12))
  return { left, top }
}

function HoverCard({ target }: { target: HoverTarget }) {
  const lines = hoverLines(target)
  return (
    // Translucent: it floats over the canvas and is transient (§5).
    <div
      role="tooltip"
      className="pointer-events-none absolute z-10 flex w-[230px] flex-col gap-1.5 border border-text/15 bg-surface/90 px-[13px] py-[11px] backdrop-blur-[10px]"
      style={cardPosition(target)}
    >
      <span className="text-[13px] font-semibold text-text">
        {lines.title}
        {target.node.node.contested ? (
          <sup className="ml-0.5 font-mono text-[0.72em] text-accent-attention" aria-label="contested">
            †
          </sup>
        ) : null}
      </span>
      <span className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-text-faint">{lines.kind}</span>
      <span className="font-mono text-[10.5px] text-text-muted">{lines.stats}</span>
    </div>
  )
}
