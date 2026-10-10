/**
 * The last search this tab ran in Find, so a source page can lead back to its results rather
 * than to an empty landing (`B-179`). Per tab, in guarded `sessionStorage`: a convenience that
 * may be missing, never state anything depends on.
 */
const KEY = 'meridian.lastFind'

export function rememberFind(href: string): void {
  try {
    window.sessionStorage.setItem(KEY, href)
  } catch {
    /* A private window or blocked storage: the breadcrumb says "Explore" instead. */
  }
}

/** The last Find link with words in it, or null. */
export function lastFind(): { href: string; q: string } | null {
  try {
    const href = window.sessionStorage.getItem(KEY)
    if (!href?.startsWith('/?')) return null
    const q = new URLSearchParams(href.slice(1)).get('q')?.trim()
    return q ? { href, q } : null
  } catch {
    return null
  }
}
