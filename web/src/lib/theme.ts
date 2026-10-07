/**
 * Theme selection (task P6-17): `system`, `light` or `dark`, stamped on the root element.
 * `system` is the default and is the *absence* of `data-theme`; the CSS is in tokens.css.
 * See docs/features/web-app.md#theme.
 */

export const THEMES = ['system', 'light', 'dark'] as const
export type Theme = (typeof THEMES)[number]

export const DEFAULT_THEME: Theme = 'system'

const STORAGE_KEY = 'meridian.theme'

function isTheme(value: unknown): value is Theme {
  return typeof value === 'string' && (THEMES as readonly string[]).includes(value)
}

/**
 * The stored choice, or `system`. Guarded: reading `localStorage` can throw during the
 * first render. See docs/features/web-app.md#browser-storage.
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
 * Stamp the root element. `system` *removes* the attribute: the CSS keys off its absence,
 * and a sentinel value would match neither palette.
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
