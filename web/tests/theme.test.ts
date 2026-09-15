/**
 * Theme selection (task P6-17).
 *
 * The three states are the subject. Two-state theming is the common bug and it
 * is invisible in the working case: a toggle that only ever writes `light` or
 * `dark` looks correct to whoever built it, and silently overrides the system
 * preference for every reader who never touches it.
 */
// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  DEFAULT_THEME,
  THEMES,
  applyTheme,
  nextTheme,
  readTheme,
  writeTheme,
  type Theme,
} from '../src/lib/theme'

beforeEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('the default is the system preference, not a colour', () => {
  it('reads as `system` when nothing is stored', () => {
    expect(readTheme()).toBe('system')
    expect(DEFAULT_THEME).toBe('system')
  })

  it('expresses `system` by removing the attribute, never by a sentinel value', () => {
    // The CSS keys off the attribute's *absence*. `data-theme="system"` would
    // match neither the light block nor the dark one, leaving every
    // never-touched-the-toggle reader on the flagship dark palette whatever
    // their machine says — which is exactly the bug this task exists to fix.
    applyTheme('dark')
    applyTheme('system')

    expect(document.documentElement.hasAttribute('data-theme')).toBe(false)
  })

  it.each(['light', 'dark'] as const)('stamps an explicit %s choice', (theme) => {
    applyTheme(theme)
    expect(document.documentElement.getAttribute('data-theme')).toBe(theme)
  })
})

describe('the stored choice', () => {
  it('round-trips', () => {
    writeTheme('light')
    expect(readTheme()).toBe('light')
  })

  it('falls back to the default when the stored value is not a theme', () => {
    // Rejection: storage is shared with anything else on the origin and
    // survives deploys, so a value written by an older build must not be
    // stamped onto the root element unchecked.
    window.localStorage.setItem('meridian.theme', 'midnight')
    expect(readTheme()).toBe(DEFAULT_THEME)
  })

  it('survives storage that throws rather than returning null', () => {
    // A private window, or a browser set to block site data, throws on the
    // property access itself. This runs during the first render, so an uncaught
    // throw takes the whole page with it — a preference must never be able to
    // stop the app loading.
    vi.spyOn(window.localStorage, 'getItem').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError')
    })

    expect(() => readTheme()).not.toThrow()
    expect(readTheme()).toBe(DEFAULT_THEME)
  })

  it('survives storage that refuses to be written', () => {
    vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError')
    })

    expect(() => writeTheme('dark')).not.toThrow()
  })
})

describe('cycling', () => {
  it('returns to where it started and visits every state once', () => {
    const seen: Theme[] = []
    let theme: Theme = 'system'
    for (let i = 0; i < THEMES.length; i += 1) {
      seen.push(theme)
      theme = nextTheme(theme)
    }

    expect(theme).toBe('system')
    expect(new Set(seen)).toEqual(new Set(THEMES))
  })

  it('offers a way back to `system` once a choice has been made', () => {
    // Without this a reader who touches the toggle once can never return to
    // following their machine, and a two-state toggle has no way to express it.
    let theme: Theme = 'light'
    const reachable = new Set<Theme>()
    for (let i = 0; i < THEMES.length; i += 1) {
      theme = nextTheme(theme)
      reachable.add(theme)
    }

    expect(reachable.has('system')).toBe(true)
  })
})
