import { useState } from 'react'

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
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  return (
    <div
      data-theme={theme === 'light' ? 'light' : undefined}
      className="min-h-screen bg-ground text-text"
    >
      <header className="flex items-center justify-between border-b border-line px-6 py-4">
        <span className="font-sans text-[length:var(--text-subhead)] font-semibold">Meridian</span>
        <button
          type="button"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted"
        >
          {theme === 'dark' ? 'Light' : 'Dark'}
        </button>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-16">
        <h1 className="font-sans text-[length:var(--text-display)] font-semibold leading-[var(--leading-display)] tracking-[var(--tracking-display)]">
          Nothing is searchable yet.
        </h1>
        <p className="mt-4 max-w-prose text-text-muted">
          The crawler collects and chunks; the chunks carry vectors and a novelty verdict. No
          retrieval path exists — <code className="font-mono text-text">search.py</code> is P2-06
          and the read endpoints are P2-07.
        </p>
        <p className="mt-6 font-mono text-[length:var(--text-data)] text-text-faint">
          web scaffold · P2-11 · P2-12
        </p>
      </main>
    </div>
  )
}
