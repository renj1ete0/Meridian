import { useEffect, useImperativeHandle, useRef, useState, type Ref } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

import type { MapPoint } from '../../lib/api'
import { percent, type Swatch } from '../../lib/corpusmap'
import { bounds, fitDistance, isDrag, pick, toViewport, type RGB } from './geometry'
import { HoverCard } from './HoverCard'
import type { CanvasPalette } from './palette'

export interface SceneHandle {
  fit(): void
  zoom(factor: number): void
}

/** Pixels either side of a dot that still count as over it. */
const PICK_RADIUS = 7
/** A dot's diameter, in CSS pixels, at the fitted distance. It grows as you zoom in. */
const DOT = 4.2
const FOV = 30
/** Where the camera first looks from: a little right of and above the first two axes' plane. */
const START = new THREE.Spherical(1, THREE.MathUtils.degToRad(70), THREE.MathUtils.degToRad(34))
/** The floor sits just below the lowest possible point, so nothing pokes through it. */
const FLOOR = -1.18
const AXIS_REACH = 1.18
const AXIS_ENDS = [
  new THREE.Vector3(AXIS_REACH, 0, 0),
  new THREE.Vector3(0, AXIS_REACH, 0),
  new THREE.Vector3(0, 0, AXIS_REACH),
]

/*
 * One draw call for every dot: a single `Points` with per-vertex colour and visibility.
 * Round soft-edged dots fade with depth so the cloud reads as a volume.
 * See docs/features/map.md#the-passage-cloud.
 */
const VERTEX = /* glsl */ `
  attribute vec3 aColour;
  attribute float aVisible;
  uniform float uSize;
  uniform float uFocus;
  uniform float uDepth;
  varying vec3 vColour;
  varying float vFar;
  void main() {
    // A hidden point is moved outside the clip volume, not shrunk to size
    // zero: some drivers clamp a zero point size to one pixel, and the hidden
    // topic would stay on screen as dust.
    if (aVisible < 0.5) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      gl_PointSize = 0.0;
      return;
    }
    vec4 view = modelViewMatrix * vec4(position, 1.0);
    gl_Position = projectionMatrix * view;
    gl_PointSize = uSize * (uFocus / -view.z);
    vColour = aColour;
    vFar = clamp(0.5 + (-view.z - uFocus) / (2.0 * uDepth), 0.0, 1.0);
  }
`

const FRAGMENT = /* glsl */ `
  varying vec3 vColour;
  varying float vFar;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float edge = smoothstep(0.5, 0.36, d);
    gl_FragColor = vec4(vColour, edge * mix(0.96, 0.38, vFar));
  }
`

function colour(rgb: RGB): THREE.Color {
  return new THREE.Color().setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace)
}

interface Props {
  points: readonly MapPoint[]
  positions: Float32Array
  colours: Float32Array
  visible: Float32Array
  palette: CanvasPalette
  swatches: ReadonlyMap<string | null, Swatch>
  shares: readonly number[]
  onOpen: (point: MapPoint) => void
  /** WebGL failed to start or was lost; the page falls back to the flat view. */
  onUnavailable: () => void
  ref?: Ref<SceneHandle>
}

interface Live {
  renderer: THREE.WebGLRenderer
  camera: THREE.PerspectiveCamera
  controls: OrbitControls
  geometry: THREE.BufferGeometry
  material: THREE.ShaderMaterial
  grid: THREE.LineSegments
  axes: THREE.LineSegments
  width: number
  height: number
}

/**
 * The corpus map in three dimensions (task P6-29): orbit by dragging, zoom with the wheel,
 * pan with the right button or shift-drag. It turns slowly until first touched, then holds
 * still for good.
 */
export function Scene3D({
  points,
  positions,
  colours,
  visible,
  palette,
  swatches,
  shares,
  onOpen,
  onUnavailable,
  ref,
}: Props) {
  const frame = useRef<HTMLDivElement>(null)
  const ring = useRef<HTMLDivElement>(null)
  const labels = useRef<(HTMLSpanElement | null)[]>([])
  const live = useRef<Live | null>(null)
  const down = useRef<[number, number] | null>(null)
  const [hover, setHover] = useState<{ index: number; at: [number, number] } | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  // What the render loop reads without re-subscribing on every render.
  const state = useRef({
    positions,
    visible,
    points,
    onOpen,
    hovered: -1,
    cursor: null as [number, number] | null,
    dirty: true,
    dragging: false,
  })
  state.current.positions = positions
  state.current.visible = visible
  state.current.points = points
  state.current.onOpen = onOpen

  function fit() {
    const scene = live.current
    if (!scene) return
    const box = bounds(state.current.positions, state.current.visible) ?? { centre: [0, 0, 0], radius: 1 }
    const target = new THREE.Vector3(...box.centre)
    // The axis ends too, so their labels are never framed out.
    const reach = Math.max(box.radius, ...AXIS_ENDS.map((end) => end.distanceTo(target)))
    const direction = scene.camera.position.clone().sub(scene.controls.target)
    if (direction.lengthSq() < 1e-9) direction.setFromSpherical(START)
    direction.normalize()
    const distance = fitDistance(reach, FOV, scene.width / Math.max(1, scene.height), 1.02)
    scene.controls.target.copy(target)
    scene.camera.position.copy(target).addScaledVector(direction, distance)
    scene.material.uniforms.uFocus!.value = distance
    scene.material.uniforms.uDepth!.value = Math.max(box.radius, 0.1)
    scene.controls.update()
    state.current.dirty = true
  }

  function zoom(factor: number) {
    const scene = live.current
    if (!scene) return
    scene.controls.autoRotate = false
    const offset = scene.camera.position.clone().sub(scene.controls.target).multiplyScalar(factor)
    const length = THREE.MathUtils.clamp(offset.length(), scene.controls.minDistance, scene.controls.maxDistance)
    scene.camera.position.copy(scene.controls.target).add(offset.setLength(length))
    scene.controls.update()
    state.current.dirty = true
  }

  useImperativeHandle(ref, () => ({ fit, zoom }))

  // The scene itself: built once, torn down whole on unmount.
  useEffect(() => {
    const host = frame.current
    if (!host) return

    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
    } catch {
      onUnavailable()
      return
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.domElement.style.display = 'block'
    renderer.domElement.style.outline = 'none'
    host.prepend(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(FOV, 1, 0.01, 100)
    camera.position.setFromSpherical(START).multiplyScalar(4)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.09
    controls.rotateSpeed = 0.7
    controls.screenSpacePanning = true
    controls.minDistance = 0.3
    controls.maxDistance = 20
    controls.autoRotate = !window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    controls.autoRotateSpeed = 0.55
    controls.listenToKeyEvents(host)
    // Any deliberate move ends the idle turn for good.
    const still = () => {
      controls.autoRotate = false
    }
    controls.addEventListener('start', still)
    const moved = () => {
      state.current.dirty = true
    }
    controls.addEventListener('change', moved)

    const geometry = new THREE.BufferGeometry()
    const material = new THREE.ShaderMaterial({
      vertexShader: VERTEX,
      fragmentShader: FRAGMENT,
      transparent: true,
      depthWrite: false,
      uniforms: {
        uSize: { value: DOT * renderer.getPixelRatio() },
        uFocus: { value: 4 },
        uDepth: { value: 1 },
      },
    })
    const cloud = new THREE.Points(geometry, material)
    cloud.frustumCulled = false
    scene.add(cloud)

    // The graticule: a floor grid under the cloud, the graph canvas's ground
    // lines laid flat. Quiet enough to give the volume a floor and no more.
    const gridGeometry = new THREE.BufferGeometry()
    const lines: number[] = []
    const steps = 8
    for (let i = 0; i <= steps; i++) {
      const t = -AXIS_REACH + (2 * AXIS_REACH * i) / steps
      lines.push(t, FLOOR, -AXIS_REACH, t, FLOOR, AXIS_REACH, -AXIS_REACH, FLOOR, t, AXIS_REACH, FLOOR, t)
    }
    gridGeometry.setAttribute('position', new THREE.Float32BufferAttribute(lines, 3))
    const grid = new THREE.LineSegments(gridGeometry, new THREE.LineBasicMaterial({ transparent: true }))
    scene.add(grid)

    // Three rules through the origin, one per principal component.
    const axesGeometry = new THREE.BufferGeometry()
    const r = AXIS_REACH
    axesGeometry.setAttribute(
      'position',
      new THREE.Float32BufferAttribute([-r, 0, 0, r, 0, 0, 0, -r, 0, 0, r, 0, 0, 0, -r, 0, 0, r], 3),
    )
    const axes = new THREE.LineSegments(axesGeometry, new THREE.LineBasicMaterial({ transparent: true }))
    scene.add(axes)

    live.current = { renderer, camera, controls, geometry, material, grid, axes, width: 1, height: 1 }

    const resize = new ResizeObserver(([entry]) => {
      const width = Math.max(1, Math.floor(entry!.contentRect.width))
      const height = Math.max(1, Math.floor(entry!.contentRect.height))
      const first = live.current!.width === 1 && live.current!.height === 1
      live.current!.width = width
      live.current!.height = height
      renderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      setSize({ width, height })
      if (first) fit()
      state.current.dirty = true
    })
    resize.observe(host)

    const lost = (event: Event) => {
      event.preventDefault()
      onUnavailable()
    }
    renderer.domElement.addEventListener('webglcontextlost', lost)

    const matrix = new THREE.Matrix4()
    let frameId = 0
    const tick = () => {
      frameId = requestAnimationFrame(tick)
      const s = state.current
      const { width, height } = live.current!
      // The idle turn pauses while a dot is under the cursor, so a card
      // being read does not drift away from the dot it describes.
      const turning = controls.autoRotate
      if (s.hovered >= 0) controls.autoRotate = false
      controls.update()
      if (s.hovered >= 0) controls.autoRotate = turning
      renderer.render(scene, camera)

      matrix.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse)
      if (s.dragging && s.hovered >= 0) {
        // A drag is turning the picture, not pointing at a dot.
        s.hovered = -1
        setHover(null)
      } else if (s.dirty && s.cursor && !s.dragging) {
        const index = pick(s.positions, s.visible, matrix.elements, width, height, s.cursor, PICK_RADIUS)
        if (index !== s.hovered) {
          s.hovered = index
          setHover(index >= 0 ? { index, at: s.cursor } : null)
        }
      }
      s.dirty = false

      // Overlays follow the camera without a React render per frame.
      if (ring.current) {
        const at =
          s.hovered >= 0
            ? toViewport(
                matrix.elements,
                s.positions[s.hovered * 3]!,
                s.positions[s.hovered * 3 + 1]!,
                s.positions[s.hovered * 3 + 2]!,
                width,
                height,
              )
            : null
        ring.current.style.display = at ? 'block' : 'none'
        if (at) ring.current.style.transform = `translate(${at[0] - 7}px, ${at[1] - 7}px)`
      }
      AXIS_ENDS.forEach((end, axis) => {
        const label = labels.current[axis]
        if (!label) return
        const at = toViewport(matrix.elements, end.x, end.y, end.z, width, height)
        label.style.display = at ? 'block' : 'none'
        if (at) label.style.transform = `translate(${at[0] + 6}px, ${at[1] - 8}px)`
      })
    }
    tick()

    return () => {
      cancelAnimationFrame(frameId)
      resize.disconnect()
      controls.removeEventListener('start', still)
      controls.removeEventListener('change', moved)
      controls.dispose()
      renderer.domElement.removeEventListener('webglcontextlost', lost)
      geometry.dispose()
      material.dispose()
      gridGeometry.dispose()
      ;(grid.material as THREE.Material).dispose()
      axesGeometry.dispose()
      ;(axes.material as THREE.Material).dispose()
      renderer.dispose()
      renderer.forceContextLoss()
      renderer.domElement.remove()
      live.current = null
    }
    // Built once per mount; later changes arrive through the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // New data: new buffers, and a fresh fit, since the old framing was for other points.
  useEffect(() => {
    const scene = live.current
    if (!scene) return
    scene.geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
    scene.geometry.setAttribute('aColour', new THREE.BufferAttribute(colours, 3))
    scene.geometry.setAttribute('aVisible', new THREE.BufferAttribute(visible, 1))
    state.current.hovered = -1
    setHover(null)
    if (scene.width > 1) fit()
    // Colours and visibility have their own effects; this one is the data.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions])

  useEffect(() => {
    live.current?.geometry.setAttribute('aColour', new THREE.BufferAttribute(colours, 3))
  }, [colours])

  useEffect(() => {
    const scene = live.current
    if (!scene) return
    scene.geometry.setAttribute('aVisible', new THREE.BufferAttribute(visible, 1))
    state.current.dirty = true
  }, [visible])

  useEffect(() => {
    const scene = live.current
    if (!scene) return
    scene.renderer.setClearColor(colour(palette.ground))
    const grid = scene.grid.material as THREE.LineBasicMaterial
    grid.color = colour(palette.graticule)
    const axes = scene.axes.material as THREE.LineBasicMaterial
    axes.color = colour(palette.axis)
  }, [palette])

  const hovered = hover ? points[hover.index] : undefined

  return (
    <div
      ref={frame}
      tabIndex={0}
      role="img"
      aria-label={`A rotating cloud of ${points.length} passages placed by embedding similarity on three axes. The table lists the same data by topic.`}
      className="absolute inset-0 overflow-hidden focus-visible:outline-offset-[-2px]"
      style={{ cursor: hovered ? 'pointer' : 'grab' }}
      onPointerMove={(event) => {
        const box = event.currentTarget.getBoundingClientRect()
        const at: [number, number] = [event.clientX - box.left, event.clientY - box.top]
        state.current.cursor = at
        state.current.dirty = true
        if (hover) setHover({ index: hover.index, at })
      }}
      onPointerLeave={() => {
        state.current.cursor = null
        state.current.hovered = -1
        setHover(null)
      }}
      onPointerDown={(event) => {
        const box = event.currentTarget.getBoundingClientRect()
        down.current = [event.clientX - box.left, event.clientY - box.top]
      }}
      onPointerMoveCapture={(event) => {
        const start = down.current
        if (!start || state.current.dragging) return
        const box = event.currentTarget.getBoundingClientRect()
        if (isDrag(start, [event.clientX - box.left, event.clientY - box.top])) state.current.dragging = true
      }}
      onPointerUp={(event) => {
        const box = event.currentTarget.getBoundingClientRect()
        const up: [number, number] = [event.clientX - box.left, event.clientY - box.top]
        const start = down.current
        down.current = null
        state.current.dragging = false
        state.current.dirty = true
        if (event.button !== 0 || !start || isDrag(start, up)) return
        const s = state.current
        if (s.hovered >= 0) s.onOpen(s.points[s.hovered]!)
      }}
    >
      <div
        ref={ring}
        aria-hidden
        className="pointer-events-none absolute left-0 top-0 hidden size-[14px] rounded-full border-2 border-text"
      />
      {['PC1', 'PC2', 'PC3'].map((name, axis) => (
        <span
          key={name}
          ref={(element) => {
            labels.current[axis] = element
          }}
          aria-hidden
          className="pointer-events-none absolute left-0 top-0 whitespace-nowrap font-mono text-[9.5px] uppercase tracking-[0.12em] text-text-faint [text-shadow:0_0_3px_var(--ground-deep),0_0_3px_var(--ground-deep)]"
        >
          {name} · {percent(shares[axis] ?? 0)}
        </span>
      ))}
      {hovered && hover ? (
        <HoverCard point={hovered} swatch={swatches.get(hovered.topic)} at={hover.at} frame={size} />
      ) : null}
    </div>
  )
}
