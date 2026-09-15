/**
 * Theme selection (task P6-17).
 *
 * Three states, not two. "No explicit choice" must mean *the system
 * preference*, not *dark* — a reader whose machine is in light mode and who has
 * never touched the toggle should get light. `system` is therefore the default
 * and is expressed by the **absence** of `data-theme`, which is what lets the
 * `prefers-color-scheme` block in tokens.css apply.
 *
 * All of the CSS lives in tokens.css. This module only decides which of the
 * three states is in effect and stamps the root element accordingly.
 */

export const THEMES = ['system', 'light', 'dark'] as const
export type Theme = (typeof THEMES)[number]

export const DEFAULT_THEME: Theme = 'system'

const STORAGE_KEY = 'meridian.theme'

function isTheme(value: unknown): value is Theme {
  return typeof value === 'string' && (THEMES as readonly string[]).includes(value)
}

/**
 * The stored choice, or `system`.
 *
 * Every access is guarded. `localStorage` does not merely return null in a
 * private window or with site data blocked — reading the property itself
 * throws, and an uncaught throw here happens during the first render and takes
 * the whole page with it. A theme is a preference; it must never be able to
 * prevent the app from loading.
 */
export function readTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return isTheme(stored) ? stored : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

/** Persist the choice. Failing to remember it is not worth an error. */
export function writeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, theme)
  } catch {
    /* A preference that cannot be stored is still a preference for this tab. */
  }
}

/**
 * Stamp the root element.
 *
 * `system` **removes** the attribute rather than setting `data-theme="system"`.
 * The CSS keys off the attribute's absence, so a sentinel value would match
 * neither the light block nor the dark one and would silently leave the reader
 * on the flagship dark palette whatever their machine says.
 */
export function applyTheme(theme: Theme, root: HTMLElement = document.documentElement): void {
  if (theme === 'system') {
    root.removeAttribute('data-theme')
  } else {
    root.setAttribute('data-theme', theme)
  }
}

/** `system → light → dark → system`, for a single control that cycles. */
export function nextTheme(theme: Theme): Theme {
  return THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length] as Theme
}
