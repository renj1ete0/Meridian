import { useEffect, useState } from 'react'

import { applyTheme, nextTheme, readTheme, writeTheme, type Theme } from './lib/theme'

const LABEL: Record<Theme, string> = { system: 'System', light: 'Light', dark: 'Dark' }

/**
 * The application shell (task P2-11).
 *
 * Deliberately not a placeholder saying "coming soon". §12.5's first Explore
 * state has to state the absence — what is not searchable, and why — because
 * that is what the voice guide asks of every empty state and because an app
 * that lies about having no index is harder to debug than one that says so.
 *
 * It renders nothing from the API yet: `/api/explore/*` is `P2-07`.
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
        <span className="font-sans text-[length:var(--text-subhead)] font-semibold">Meridian</span>
        <button
          type="button"
          onClick={() => choose(nextTheme(theme))}
          aria-label={`Theme: ${LABEL[theme]}. Change.`}
          className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted"
        >
          {LABEL[theme]}
        </button>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-16">
        <h1 className="font-sans text-[length:var(--text-display)] font-semibold leading-[var(--leading-display)] tracking-[var(--tracking-display)]">
          Nothing is searchable yet.
        </h1>
        <p className="mt-4 max-w-prose text-text-muted">
          The crawler collects and chunks; the chunks carry vectors and a novelty verdict. Retrieval
          exists as a library — <code className="font-mono text-text">meridian_core.search</code> —
          and nothing serves it. The read endpoints are P2-07.
        </p>
        <p className="mt-6 font-mono text-[length:var(--text-data)] text-text-faint">
          web scaffold · P2-11 · P2-12 · P6-17
        </p>
      </main>
    </div>
  )
}
