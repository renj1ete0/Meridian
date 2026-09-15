import { useEffect, useState } from 'react'

import { ExplorePage } from './explore/ExplorePage'
import { applyTheme, nextTheme, readTheme, writeTheme, type Theme } from './lib/theme'
import { Lockup } from './ui/Mark'

const LABEL: Record<Theme, string> = { system: 'System', light: 'Light', dark: 'Dark' }

/**
 * The application shell (tasks P2-11, P2-08).
 *
 * Explore is the whole app for now, so the shell is a header and a surface.
 * §12.6 splits Explore from Admin, and Admin does not exist — a nav bar with
 * one destination would be furniture.
 */
export function App() {
  const [theme, setTheme] = useState<Theme>(readTheme)

  // On the root element, not on a wrapper div. The reader's preference has to
  // reach `color-scheme`, which the browser reads from the document element to
  // style scrollbars, form controls and autofill — none of which are inside
  // this component's subtree.
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  function choose(next: Theme) {
    setTheme(next)
    writeTheme(next)
  }

  return (
    <div className="min-h-screen bg-ground text-text">
      <header className="flex items-center justify-between border-b border-line px-6 py-4">
        <Lockup size={28} />
        <button
          type="button"
          onClick={() => choose(nextTheme(theme))}
          aria-label={`Theme: ${LABEL[theme]}. Change.`}
          className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted"
        >
          {LABEL[theme]}
        </button>
      </header>

      <main>
        <ExplorePage />
      </main>
    </div>
  )
}
