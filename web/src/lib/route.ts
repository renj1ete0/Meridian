/**
 * A small hand-rolled path router (task P6-14/P6-15); URLs are real, so every page can be
 * linked. Replace rather than grow it (`P6-20`). See docs/features/web-app.md#routing.
 */
import { useEffect, useState } from 'react'

export type Route =
  | { name: 'explore' }
  | { name: 'source'; sourceId: number }
  | { name: 'node'; entityId: number }
  | { name: 'admin' }
  | { name: 'map' }
  | { name: 'gaps' }
  | { name: 'growth' }
  | { name: 'contested' }
  | { name: 'about' }

const SOURCE = /^\/sources\/(\d+)\/?$/

// A prefix, not an exact match: §12.6's Admin is several screens and they share
// the section. Matching only `/admin` would drop a reader on a sub-path back
// onto Explore, which reads as the link being wrong rather than unbuilt.
const NODE = /^\/nodes\/(\d+)\/?$/

const ADMIN = /^\/admin(\/|$)/

const MAP = /^\/map\/?$/

const GAPS = /^\/gaps\/?$/

const GROWTH = /^\/growth\/?$/

const ABOUT = /^\/about\/?$/

const CONTESTED = /^\/contested\/?$/

export function parseRoute(pathname: string): Route {
  const match = SOURCE.exec(pathname)
  if (match) return { name: 'source', sourceId: Number(match[1]) }
  const node = NODE.exec(pathname)
  if (node) return { name: 'node', entityId: Number(node[1]) }
  if (ADMIN.test(pathname)) return { name: 'admin' }
  if (MAP.test(pathname)) return { name: 'map' }
  if (GAPS.test(pathname)) return { name: 'gaps' }
  if (GROWTH.test(pathname)) return { name: 'growth' }
  if (ABOUT.test(pathname)) return { name: 'about' }
  if (CONTESTED.test(pathname)) return { name: 'contested' }
  return { name: 'explore' }
}

/**
 * A source's page; with a passage, opened on it in its context (`B-178`). Every link from a
 * passage passes it, so a reader lands on what they clicked, not on page one.
 */
export function hrefForSource(sourceId: number, passage?: number | null): string {
  return passage != null ? `/sources/${sourceId}?passage=${passage}` : `/sources/${sourceId}`
}

/** The passage a source link asks to open on, or null. */
export function passageOf(search: string): number | null {
  const raw = new URLSearchParams(search).get('passage')
  return raw !== null && /^\d+$/.test(raw) ? Number(raw) : null
}

export function hrefForNode(entityId: number): string {
  return `/nodes/${entityId}`
}

/** Push a new URL without reloading, and tell React about it. */
export function navigate(path: string): void {
  window.history.pushState({}, '', path)
  // `popstate` does not fire for `pushState`, so the listeners below would miss
  // every in-app navigation. Dispatching it keeps one code path for "the URL
  // changed" rather than two that can drift.
  window.dispatchEvent(new PopStateEvent('popstate'))
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.pathname))

  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.pathname))
    window.addEventListener('popstate', onChange)
    return () => window.removeEventListener('popstate', onChange)
  }, [])

  return route
}

/**
 * Intercept a left-click on an internal link so it navigates in-app. Modified and
 * middle clicks are left alone, so a new tab still opens.
 */
export function onInternalClick(path: string) {
  return (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.defaultPrevented || event.button !== 0) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(path)
  }
}
