/**
 * Since-last-visit (task P6-11, spec §12.5).
 *
 * §12.5 wants the landing state to carry a delta, because "what arrived while I
 * was away" is the question somebody opens this with and a total answers a
 * different one.
 *
 * **When the stamp advances is the whole design.** Writing it on render makes
 * the delta vanish the moment you look at it — you would see "12 new", blink,
 * and see "0 new" on the next render, which is worse than not offering it. So
 * the previous stamp is read *once* per session and the new one is written
 * immediately: the delta means "since you were last here", not "since a second
 * ago", and it stays stable while you read the page.
 *
 * **Per viewer, per browser, and that is correct.** This is not a fact about
 * the corpus — two people looking at the same Meridian have genuinely different
 * answers to "what is new to me". It belongs in `localStorage`, not in a table.
 *
 * Every access is guarded. `localStorage` does not merely return null in a
 * private window or with site data blocked — reading the property itself throws
 * — and this runs during the first render.
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
 * Read the previous visit and record this one, in that order.
 *
 * One call, because doing it in two places is how the stamp ends up advanced
 * before it was read — and the symptom is a delta that is always zero, which
 * looks exactly like a corpus where nothing happened.
 */
export function openSession(now: Date = new Date()): string | null {
  const previous = readLastVisit()
  markVisited(now)
  return previous
}
