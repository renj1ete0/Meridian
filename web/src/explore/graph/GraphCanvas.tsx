import { useEffect, useRef, useState, type MutableRefObject } from 'react'

import { MERIDIAN_ASPECT, MERIDIAN_RADIUS } from './layout'
import { readPalette } from './palette'
import type { Scene, SceneNode } from './scene'
import { DAGGER } from '../../ui/Contested'

/**
 * The node-link canvas: Sigma.js over graphology (task P6-01, spec §12.1), with an SVG
 * layer under the WebGL canvas, hand-drawn labels, and Sigma imported lazily.
 * See docs/features/knowledge-graph.md#the-graph-workspace.
 */

export interface CanvasApi {
  fit(): void
  zoomIn(): void
  zoomOut(): void
}

export interface HoverTarget {
  node: SceneNode
  /** Viewport pixels within the canvas element. */
  x: number
  y: number
  /** The canvas's own size, so the card can stay inside it. */
  width: number
  height: number
}

export interface GraphCanvasProps {
  scene: Scene
  onNodeClick?: (id: number) => void
  onHover?: (target: HoverTarget | null) => void
  apiRef?: MutableRefObject<CanvasApi | null>
  onUnavailable?: (reason: string) => void
}

interface Overlay {
  centre: { x: number; y: number } | null
  rx: number
  ry: number
  rings: { key: string; x: number; y: number; r: number; color: string; opacity: number }[]
  dashed: { key: string; x1: number; y1: number; x2: number; y2: number; color: string; width: number }[]
}

const EMPTY_OVERLAY: Overlay = { centre: null, rx: 0, ry: 0, rings: [], dashed: [] }

type LabelData = {
  x: number
  y: number
  size: number
  label: string | null
  labelColor?: string
  caption?: string | null
  dagger?: boolean
  isFocus?: boolean
}

/** A drawn label's box, in viewport pixels. */
export interface LabelBox {
  x0: number
  y0: number
  x1: number
  y1: number
}

/**
 * Which labels fit this frame (`B-124`), greedily: a label overlapping one already drawn
 * is left out (its node still names itself on hover). The focus is always drawn. Reset
 * every frame.
 */
export class LabelPlacer {
  private boxes: LabelBox[] = []

  reset(): void {
    this.boxes = []
  }

  /** Place the box if it overlaps none already placed; say whether it was. */
  place(box: LabelBox, force = false): boolean {
    if (!force && this.boxes.some((b) => box.x0 < b.x1 && b.x0 < box.x1 && box.y0 < b.y1 && b.y0 < box.y1)) {
      return false
    }
    this.boxes.push(box)
    return true
  }
}

/** Breathing room kept between two labels, in pixels. */
const LABEL_GAP = 3

export function makeLabelDrawer(
  fonts: { sans: string; mono: string },
  halo: string,
  dagger: string,
  caption: string,
  placer?: LabelPlacer,
) {
  return function drawLabel(context: CanvasRenderingContext2D, data: LabelData): void {
    if (!data.label) return
    const size = data.isFocus ? 14 : 12
    const weight = data.isFocus ? 600 : 400
    const y = data.y + data.size + (data.isFocus ? 16 : 14)

    context.font = `${weight} ${size}px ${fonts.sans}`
    context.textAlign = 'left'
    context.textBaseline = 'alphabetic'
    const labelWidth = context.measureText(data.label).width
    context.font = `400 10px ${fonts.mono}`
    const daggerWidth = data.dagger ? context.measureText(DAGGER).width + 1 : 0
    const left = data.x - (labelWidth + daggerWidth) / 2

    if (placer) {
      const box = {
        x0: left - LABEL_GAP,
        x1: left + labelWidth + daggerWidth + LABEL_GAP,
        y0: y - size - LABEL_GAP,
        y1: y + (data.caption ? 16 : 4) + LABEL_GAP,
      }
      if (!placer.place(box, data.isFocus)) return
    }

    context.lineJoin = 'round'
    context.strokeStyle = halo
    context.lineWidth = 3.5
    context.font = `${weight} ${size}px ${fonts.sans}`
    context.strokeText(data.label, left, y)
    context.fillStyle = data.labelColor ?? caption
    context.fillText(data.label, left, y)

    if (data.dagger) {
      // §6: the dagger is a 10px mono superscript, raised 4px, in brass.
      context.font = `400 10px ${fonts.mono}`
      context.strokeText(DAGGER, left + labelWidth + 1, y - 4)
      context.fillStyle = dagger
      context.fillText(DAGGER, left + labelWidth + 1, y - 4)
    }

    if (data.caption) {
      context.font = `500 9px ${fonts.mono}`
      context.textAlign = 'center'
      const text = data.caption.toUpperCase()
      // The artboard's 0.08em tracking. `letterSpacing` is recent on 2D
      // canvases; where it is missing the caption is merely tighter.
      const spaced = context as CanvasRenderingContext2D & { letterSpacing?: string }
      spaced.letterSpacing = '0.72px'
      context.lineWidth = 3
      context.strokeText(text, data.x, y + 13)
      context.fillStyle = caption
      context.fillText(text, data.x, y + 13)
      spaced.letterSpacing = '0px'
    }
  }
}

export function GraphCanvas({ scene, onNodeClick, onHover, apiRef, onUnavailable }: GraphCanvasProps) {
  const container = useRef<HTMLDivElement>(null)
  const [overlay, setOverlay] = useState<Overlay>(EMPTY_OVERLAY)
  const handlers = useRef({ onNodeClick, onHover })
  handlers.current = { onNodeClick, onHover }

  useEffect(() => {
    const element = container.current
    if (!element) return
    let killed = false
    let cleanup = () => {}

    Promise.all([import('sigma'), import('graphology')])
      .then(([{ default: Sigma }, { default: Graph }]) => {
        if (killed) return
        const palette = readPalette()
        const styles = getComputedStyle(document.documentElement)
        const fonts = {
          sans: styles.getPropertyValue('--font-sans').trim() || 'sans-serif',
          mono: styles.getPropertyValue('--font-mono').trim() || 'monospace',
        }

        const graph = new Graph({ multi: false, type: 'undirected' })
        const byId = new Map(scene.nodes.map((n) => [String(n.id), n]))
        for (const n of scene.nodes) {
          graph.addNode(String(n.id), {
            x: n.x,
            // Sigma's y axis points up; the layout's points down, as SVG's does.
            y: -n.y,
            size: n.look.size,
            color: n.look.color,
            label: n.look.label,
            labelColor: n.look.labelColor,
            caption: n.look.caption,
            dagger: n.look.dagger,
            isFocus: n.node.role === 'focus' && n.id === scene.focus,
            zIndex: n.look.zIndex,
            forceLabel: Boolean(n.look.label),
          })
        }
        for (const e of scene.edges) {
          const [a, b] = [String(e.source), String(e.target)]
          if (!graph.hasNode(a) || !graph.hasNode(b) || graph.hasEdge(a, b)) continue
          graph.addEdgeWithKey(String(e.id), a, b, {
            size: e.look.size,
            color: e.look.color,
            zIndex: e.look.zIndex,
            // Dashed edges are drawn by the SVG layer instead.
            hidden: e.look.dashed,
            label: scene.edgeLabels ? e.edge.relation_type.replaceAll('_', ' ') : null,
          })
        }

        let renderer: InstanceType<typeof Sigma>
        try {
          const placer = new LabelPlacer()
          const drawLabel = makeLabelDrawer(fonts, palette.ground, palette.contested, palette.caption, placer)
          // The hovered node always names itself, whatever it was crowded out by.
          const drawHover = makeLabelDrawer(fonts, palette.ground, palette.contested, palette.caption)
          renderer = new Sigma(graph, element, {
            zIndex: true,
            renderEdgeLabels: scene.edgeLabels,
            edgeLabelFont: fonts.mono,
            edgeLabelSize: 10,
            edgeLabelColor: { color: palette.caption },
            labelRenderedSizeThreshold: 0,
            stagePadding: 24,
            minCameraRatio: 0.2,
            maxCameraRatio: 4,
            defaultDrawNodeLabel: drawLabel as never,
            // The hover card is the hover state; Sigma's white hover box would
            // be a second, unstyled one.
            defaultDrawNodeHover: drawHover as never,
          })
          renderer.on('beforeRender', () => placer.reset())
        } catch (cause) {
          onUnavailable?.(
            cause instanceof Error && /webgl/i.test(cause.message)
              ? 'This browser has no WebGL, which the canvas needs. The table view shows the same neighbourhood.'
              : 'The canvas could not start. The table view shows the same neighbourhood.',
          )
          return
        }

        const project = () => {
          const focus = scene.nodes.find((n) => n.id === scene.focus)
          const centre = focus ? renderer.graphToViewport({ x: focus.x, y: -focus.y }) : null
          const scale = (r: number) => renderer.scaleSize(r)
          let rx = 0
          let ry = 0
          if (centre && scene.meridian) {
            const east = renderer.graphToViewport({ x: MERIDIAN_RADIUS, y: 0 })
            const north = renderer.graphToViewport({ x: 0, y: MERIDIAN_RADIUS })
            ry = Math.hypot(north.x - centre.x, north.y - centre.y)
            rx = Math.hypot(east.x - centre.x, east.y - centre.y) * MERIDIAN_ASPECT
          }
          const rings = scene.nodes
            .filter((n) => n.look.ring)
            .map((n) => {
              const at = renderer.graphToViewport({ x: n.x, y: -n.y })
              return {
                key: String(n.id),
                x: at.x,
                y: at.y,
                r: scale(n.look.ring!.radius),
                color: n.look.ring!.color,
                opacity: n.look.ring!.opacity,
              }
            })
          const dashed = scene.edges
            .filter((e) => e.look.dashed)
            .flatMap((e) => {
              const a = byId.get(String(e.source))
              const b = byId.get(String(e.target))
              if (!a || !b) return []
              const p = renderer.graphToViewport({ x: a.x, y: -a.y })
              const q = renderer.graphToViewport({ x: b.x, y: -b.y })
              return [
                { key: String(e.id), x1: p.x, y1: p.y, x2: q.x, y2: q.y, color: e.look.color, width: e.look.size },
              ]
            })
          setOverlay({ centre: scene.meridian ? centre : null, rx, ry, rings, dashed })
        }
        // Layout units in, not the nodes' own extent: see `Scene.frame`. The
        // y range is negated with the nodes.
        renderer.setCustomBBox({ x: scene.frame.x, y: [-scene.frame.y[1], -scene.frame.y[0]] })
        renderer.refresh()
        renderer.on('afterRender', project)
        // The first frame is drawn inside the constructor, before this listener
        // existed, so the overlay is placed once by hand.
        project()

        renderer.on('clickNode', ({ node }) => handlers.current.onNodeClick?.(Number(node)))
        renderer.on('enterNode', ({ node }) => {
          const found = byId.get(node)
          if (!found) return
          const at = renderer.graphToViewport({ x: found.x, y: -found.y })
          handlers.current.onHover?.({
            node: found,
            ...at,
            width: element.clientWidth,
            height: element.clientHeight,
          })
          element.style.cursor = 'pointer'
        })
        renderer.on('leaveNode', () => {
          handlers.current.onHover?.(null)
          element.style.cursor = ''
        })
        renderer.getCamera().on('updated', () => handlers.current.onHover?.(null))

        if (apiRef) {
          const camera = renderer.getCamera()
          apiRef.current = {
            fit: () => void camera.animatedReset({ duration: 250 }),
            zoomIn: () => void camera.animatedZoom({ duration: 200 }),
            zoomOut: () => void camera.animatedUnzoom({ duration: 200 }),
          }
        }

        cleanup = () => {
          renderer.kill()
          if (apiRef) apiRef.current = null
        }
      })
      .catch(() => {
        onUnavailable?.('The canvas could not load. The table view shows the same neighbourhood.')
      })

    return () => {
      killed = true
      cleanup()
    }
    // The scene is rebuilt only when the data changes; handlers are read
    // through a ref so a new callback does not tear the renderer down.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene])

  return (
    <div className="absolute inset-0">
      <svg className="pointer-events-none absolute inset-0 h-full w-full" aria-hidden="true">
        <Graticule overlay={overlay} />
        {overlay.dashed.map((d) => (
          <line
            key={d.key}
            x1={d.x1}
            y1={d.y1}
            x2={d.x2}
            y2={d.y2}
            stroke={d.color}
            strokeWidth={d.width}
            strokeDasharray="5 4"
          />
        ))}
        {overlay.rings.map((r) => (
          <circle
            key={r.key}
            cx={r.x}
            cy={r.y}
            r={r.r}
            fill="none"
            stroke={r.color}
            strokeWidth={1.3}
            opacity={r.opacity}
          />
        ))}
      </svg>
      <div
        ref={container}
        className="absolute inset-0"
        data-testid="sigma-container"
        role="img"
        aria-label={`Graph of ${scene.nodes.length} nodes and ${scene.edges.length} edges. The table view lists the same neighbourhood.`}
      />
    </div>
  )
}

/**
 * The background geometry: four straight graticule lines and, around the
 * focus, the mark's circle and meridian ellipse. Decoration (design-system.md
 * §8) — nothing on it is data.
 */
function Graticule({ overlay }: { overlay: Overlay }) {
  const palette = readPaletteSafe()
  return (
    <g>
      <g opacity={0.5} stroke={palette.graticule} strokeWidth={1}>
        {[25, 50, 75].map((p) => (
          <line key={`h${p}`} x1="0" x2="100%" y1={`${p}%`} y2={`${p}%`} />
        ))}
        {[25, 50, 75].map((p) => (
          <line key={`v${p}`} y1="0" y2="100%" x1={`${p}%`} x2={`${p}%`} />
        ))}
      </g>
      {overlay.centre && overlay.ry > 0 ? (
        <g stroke={palette.meridian} strokeWidth={1} fill="none">
          <circle cx={overlay.centre.x} cy={overlay.centre.y} r={overlay.ry} />
          <ellipse cx={overlay.centre.x} cy={overlay.centre.y} rx={overlay.rx} ry={overlay.ry} />
        </g>
      ) : null}
    </g>
  )
}

function readPaletteSafe() {
  try {
    return readPalette()
  } catch {
    return { graticule: 'transparent', meridian: 'transparent' } as ReturnType<typeof readPalette>
  }
}
