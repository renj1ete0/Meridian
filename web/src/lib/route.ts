/**
 * A very small path router (task P6-14/P6-15's screens).
 *
 * Deliberately not `react-router`. There are two routes, and a dependency for
 * two routes is a dependency whose upgrade path you inherit for the life of the
 * project. This is forty lines and does the one thing that actually matters
 * here: **URLs are real**, so a source page can be linked, bookmarked and
 * pasted into a citation — which is the whole point of a corpus that insists
 * everything be checkable.
 *
 * It will stop being the right answer. Phase 6 has fifteen more screens, and
 * when nested layouts or route-level data loading arrive this should be
 * replaced rather than grown — `P6-20`.
 */
import { useEffect, useState } from 'react'

export type Route =
  | { name: 'explore' }
  | { name: 'source'; sourceId: number }
  | { name: 'node'; entityId: number }
  | { name: 'admin' }

const SOURCE = /^\/sources\/(\d+)\/?$/

// A prefix, not an exact match: §12.6's Admin is several screens and they share
// the section. Matching only `/admin` would drop a reader on a sub-path back
// onto Explore, which reads as the link being wrong rather than unbuilt.
const NODE = /^\/nodes\/(\d+)\/?$/

const ADMIN = /^\/admin(\/|$)/

export function parseRoute(pathname: string): Route {
  const match = SOURCE.exec(pathname)
  if (match) return { name: 'source', sourceId: Number(match[1]) }
  const node = NODE.exec(pathname)
  if (node) return { name: 'node', entityId: Number(node[1]) }
  if (ADMIN.test(pathname)) return { name: 'admin' }
  return { name: 'explore' }
}

export function hrefForSource(sourceId: number): string {
  return `/sources/${sourceId}`
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
 * Intercept a left-click on an internal link so it navigates in-app.
 *
 * Modified clicks are left alone: a reader holding ⌘ or the middle button is
 * asking for a new tab, and swallowing that is the most irritating thing a
 * hand-rolled router can do.
 */
export function onInternalClick(path: string) {
  return (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.defaultPrevented || event.button !== 0) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    navigate(path)
  }
}
