import { useEffect, useState } from 'react'

import { AdminPage } from './admin/AdminPage'
import { ExplorePage } from './explore/ExplorePage'
import { onInternalClick, useRoute } from './lib/route'
import { NodePage } from './explore/NodePage'
import { SourcePage } from './explore/SourcePage'
import { applyTheme, nextTheme, readTheme, writeTheme, type Theme } from './lib/theme'
import { Lockup } from './ui/Mark'

const LABEL: Record<Theme, string> = { system: 'System', light: 'Light', dark: 'Dark' }

/** §12.6's two halves. A source page belongs to Explore, so it marks Explore. */
const NAV = [
  { path: '/', label: 'Explore', name: 'explore' },
  { path: '/admin', label: 'Admin', name: 'admin' },
] as const

/**
 * The application shell (tasks P2-11, P2-08, P6-13).
 *
 * §12.6 splits the interface in two, and as of `P6-13` both halves exist — so
 * the header now carries the nav it deliberately did not carry while Admin was
 * unbuilt. Two destinations, which is the fewest that justifies one.
 *
 * The split is the same one the API draws: `/api/explore/*` reads through the
 * read-only role, `/api/admin/*` writes. Keeping the interface's seam in the
 * same place means a reader always knows whether the screen they are on can
 * change anything.
 */
export function App() {
  const [theme, setTheme] = useState<Theme>(readTheme)
  const route = useRoute()
  // A source page belongs to Explore. Marking neither destination while a
  // reader is two clicks into the corpus would say the header does not know
  // where they are.
  const section = route.name === 'admin' ? 'admin' : 'explore'

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
        <div className="flex items-baseline gap-6">
          <a href="/" onClick={onInternalClick('/')} aria-label="Meridian — Explore">
            <Lockup size={28} />
          </a>
          <nav className="flex items-center gap-4">
            {NAV.map(({ path, label, name }) => (
              <a
                key={path}
                href={path}
                onClick={onInternalClick(path)}
                aria-current={section === name ? 'page' : undefined}
                className={`font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
                  section === name ? 'text-text' : 'text-text-muted'
                }`}
              >
                {label}
              </a>
            ))}
          </nav>
        </div>
        <button
          type="button"
          onClick={() => choose(nextTheme(theme))}
          aria-label={`Theme: ${LABEL[theme]}. Change.`}
          className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted"
        >
          {LABEL[theme]}
        </button>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-10">
        {/* Real URLs for every screen. A source page that could not be linked
            would be a corpus insisting everything be checkable while making its
            own documents unaddressable. */}
        {route.name === 'source' ? <SourcePage sourceId={route.sourceId} /> : null}
        {route.name === 'node' ? <NodePage entityId={route.entityId} /> : null}
        {route.name === 'admin' ? <AdminPage /> : null}
        {route.name === 'explore' ? <ExplorePage /> : null}
      </main>
    </div>
  )
}
