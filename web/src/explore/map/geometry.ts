/**
 * The 3D map's arithmetic (task P6-29), without three.js or WebGL, so it is testable in
 * jsdom: projection, picking, camera distance and colours. The scene only moves these
 * numbers onto the GPU.
 */
import type { MapPoint } from '../../lib/api'

export type RGB = [number, number, number]

/**
 * A computed CSS colour as linear-ish 0..1 channels, or null if unreadable (never black,
 * which would vanish on the canvas). Accepts `#rgb`, `#rrggbb`, `rgb()` and `rgba()`.
 */
export function parseColour(value: string): RGB | null {
  const text = value.trim()
  const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text)
  if (hex) {
    const digits = hex[1]!
    const full = digits.length === 3 ? [...digits].map((d) => d + d).join('') : digits
    return [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255) as RGB
  }
  const rgb = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i.exec(text)
  if (rgb) return [rgb[1], rgb[2], rgb[3]].map((c) => Math.min(255, Number(c)) / 255) as RGB
  return null
}

/** `a` moved `t` of the way to `b`. */
export function mix(a: RGB, b: RGB, t: number): RGB {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]
}

/** Interleaved xyz positions for a GPU buffer. The map's y is up, as in three.js. */
export function positionsOf(points: readonly Pick<MapPoint, 'x' | 'y' | 'z'>[]): Float32Array {
  const out = new Float32Array(points.length * 3)
  points.forEach((p, i) => {
    out[i * 3] = p.x
    out[i * 3 + 1] = p.y
    out[i * 3 + 2] = p.z
  })
  return out
}

/** Interleaved rgb per point, looked up by topic; unknown topics take `fallback`. */
export function coloursOf(
  points: readonly Pick<MapPoint, 'topic'>[],
  colours: ReadonlyMap<string | null, RGB>,
  fallback: RGB,
): Float32Array {
  const out = new Float32Array(points.length * 3)
  points.forEach((p, i) => {
    const [r, g, b] = colours.get(p.topic) ?? fallback
    out[i * 3] = r
    out[i * 3 + 1] = g
    out[i * 3 + 2] = b
  })
  return out
}

/** 1 for a point whose topic is shown, 0 for one the legend has hidden. */
export function visibilityOf(
  points: readonly Pick<MapPoint, 'topic'>[],
  hidden: ReadonlySet<string | null>,
): Float32Array {
  return Float32Array.from(points, (p) => (hidden.has(p.topic) ? 0 : 1))
}

/**
 * The smallest sphere about the visible points' centroid that holds them all.
 *
 * Centroid rather than origin: with a topic hidden, what remains can sit well
 * off-centre, and fitting about the origin would frame empty space.
 */
export function bounds(
  positions: Float32Array,
  visible: Float32Array,
): { centre: [number, number, number]; radius: number } | null {
  let count = 0
  const c: [number, number, number] = [0, 0, 0]
  for (let i = 0; i < visible.length; i++) {
    if (!visible[i]) continue
    c[0] += positions[i * 3]!
    c[1] += positions[i * 3 + 1]!
    c[2] += positions[i * 3 + 2]!
    count++
  }
  if (count === 0) return null
  c[0] /= count
  c[1] /= count
  c[2] /= count
  let radius = 0
  for (let i = 0; i < visible.length; i++) {
    if (!visible[i]) continue
    const dx = positions[i * 3]! - c[0]
    const dy = positions[i * 3 + 1]! - c[1]
    const dz = positions[i * 3 + 2]! - c[2]
    radius = Math.max(radius, Math.hypot(dx, dy, dz))
  }
  return { centre: c, radius }
}

/**
 * How far the camera must sit from a sphere's centre for the sphere to fit the
 * view, with `margin` to spare. Limited by whichever of the vertical and
 * horizontal field of view is narrower, so a tall narrow window fits too.
 */
export function fitDistance(radius: number, fovDegrees: number, aspect: number, margin = 1.08): number {
  const vertical = (fovDegrees * Math.PI) / 180 / 2
  const horizontal = Math.atan(Math.tan(vertical) * aspect)
  const half = Math.min(vertical, horizontal)
  return (Math.max(radius, 1e-3) * margin) / Math.sin(half)
}

/**
 * A point through a column-major 4×4 view-projection matrix onto the screen.
 *
 * Returns `[x, y, depth]` in CSS pixels, depth in normalised device units
 * (−1 near, 1 far), or null for a point behind the camera or beyond the clip
 * planes — which cannot be under the cursor however close its projection lands.
 */
export function toViewport(
  matrix: ArrayLike<number>,
  x: number,
  y: number,
  z: number,
  width: number,
  height: number,
): [number, number, number] | null {
  const m = matrix
  const cx = m[0]! * x + m[4]! * y + m[8]! * z + m[12]!
  const cy = m[1]! * x + m[5]! * y + m[9]! * z + m[13]!
  const cz = m[2]! * x + m[6]! * y + m[10]! * z + m[14]!
  const cw = m[3]! * x + m[7]! * y + m[11]! * z + m[15]!
  if (cw <= 0) return null
  const nz = cz / cw
  if (nz < -1 || nz > 1) return null
  return [((cx / cw + 1) / 2) * width, ((1 - cy / cw) / 2) * height, nz]
}

/**
 * The index of the visible point under the cursor, or −1. Picked in screen space, with
 * `radius` in pixels; the point nearest the cursor wins, then the one nearer the camera.
 * See docs/features/map.md#the-passage-cloud.
 */
export function pick(
  positions: Float32Array,
  visible: Float32Array,
  matrix: ArrayLike<number>,
  width: number,
  height: number,
  cursor: [number, number],
  radius: number,
): number {
  let best = -1
  let bestDistance = radius * radius
  let bestDepth = Infinity
  for (let i = 0; i < visible.length; i++) {
    if (!visible[i]) continue
    const at = toViewport(matrix, positions[i * 3]!, positions[i * 3 + 1]!, positions[i * 3 + 2]!, width, height)
    if (!at) continue
    const distance = (at[0] - cursor[0]) ** 2 + (at[1] - cursor[1]) ** 2
    if (distance > radius * radius) continue
    // Within half a pixel counts as the same spot; then depth decides.
    const closer = distance < bestDistance - 0.25
    const same = Math.abs(distance - bestDistance) <= 0.25
    if (closer || (same && at[2] < bestDepth)) {
      best = i
      bestDistance = distance
      bestDepth = at[2]
    }
  }
  return best
}

/** True when the pointer moved far enough between down and up to be a drag, not a click. */
export function isDrag(down: [number, number], up: [number, number], tolerance = 4): boolean {
  return Math.hypot(up[0] - down[0], up[1] - down[1]) > tolerance
}
