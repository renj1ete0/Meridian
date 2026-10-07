/**
 * Since-last-visit (task P6-11, spec §12.5): the previous visit's stamp, read once per
 * session, per browser, in guarded `localStorage`.
 * See docs/features/web-app.md#since-last-visit.
 */

const KEY = 'meridian.lastVisit'

/** The previous visit, or null on a first ever visit. */
export function readLastVisit(): string | null {
  try {
    const stored = window.localStorage.getItem(KEY)
    // A stored value that is not a timestamp would be sent to the API as a
    // query parameter, so it is checked here rather than by a 422 later.
    return stored && !Number.isNaN(Date.parse(stored)) ? stored : null
  } catch {
    return null
  }
}

export function markVisited(at: Date = new Date()): void {
  try {
    window.localStorage.setItem(KEY, at.toISOString())
  } catch {
    /* A delta that cannot be remembered is a delta not shown. Not an error. */
  }
}

/**
 * Read the previous visit and record this one, in that order, in one call so the stamp
 * cannot advance before it is read.
 */
export function openSession(now: Date = new Date()): string | null {
  const previous = readLastVisit()
  markVisited(now)
  return previous
}
