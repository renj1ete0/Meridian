/**
 * ⌘K from anywhere (task P6-27): on any page, ⌘K or Ctrl+K goes to Explore and focuses
 * the search field.
 */
import { navigate } from './route'

/** The one search field ⌘K targets. Pages that own a search box reuse this id. */
export const SEARCH_INPUT_ID = 'explore-search'

/** ⌘K on a Mac, Ctrl+K elsewhere — either is accepted, since which one a
 * reader presses is a fact about their keyboard, not their operating system. */
export function isCommandK(event: Pick<KeyboardEvent, 'key' | 'metaKey' | 'ctrlKey' | 'altKey' | 'shiftKey'>): boolean {
  return (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k'
}

/**
 * Focus the search field, navigating to Explore first if it is not on screen. Waits a
 * few frames for the field, which appears a render or more after the navigation.
 */
export function focusSearch(
  doc: Document = document,
  schedule: (fn: () => void) => void = (fn) => {
    if (typeof window.requestAnimationFrame === 'function') window.requestAnimationFrame(fn)
    else window.setTimeout(fn, 16)
  },
): void {
  const now = doc.getElementById(SEARCH_INPUT_ID)
  if (now instanceof HTMLInputElement) {
    now.focus()
    now.select()
    return
  }
  navigate('/')
  let tries = 0
  const attempt = () => {
    const field = doc.getElementById(SEARCH_INPUT_ID)
    if (field instanceof HTMLInputElement) {
      field.focus()
      return
    }
    if (++tries < 30) schedule(attempt)
  }
  schedule(attempt)
}
