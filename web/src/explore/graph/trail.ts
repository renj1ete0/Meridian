/**
 * The breadcrumb trail back through refocused nodes (§12.2: "click any
 * neighbour to expand; breadcrumb trail back").
 *
 * Returning to a node already on the trail cuts the trail there rather than
 * appending it again — the trail is the way back, and a loop in it is a way
 * back to where you are.
 */

export interface Crumb {
  id: number
  name: string
}

export const MAX_TRAIL = 12
const KEY = 'meridian.graphTrail'

export function nextTrail(trail: readonly Crumb[], here: Crumb): Crumb[] {
  const at = trail.findIndex((c) => c.id === here.id)
  if (at !== -1) return [...trail.slice(0, at), here]
  return [...trail, here].slice(-MAX_TRAIL)
}

/** Per tab: a second tab exploring elsewhere keeps its own way back. */
export function readTrail(): Crumb[] {
  try {
    const parsed: unknown = JSON.parse(window.sessionStorage.getItem(KEY) ?? '[]')
    return Array.isArray(parsed)
      ? parsed.filter(
          (c): c is Crumb =>
            typeof c === 'object' &&
            c !== null &&
            typeof (c as Crumb).id === 'number' &&
            typeof (c as Crumb).name === 'string',
        )
      : []
  } catch {
    return []
  }
}

export function writeTrail(trail: readonly Crumb[]): void {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(trail))
  } catch {
    /* A trail that cannot be kept across a reload is still a trail. */
  }
}
