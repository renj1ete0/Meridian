import { AskContextProvider, AskPanel } from './explore/AskPanel'
import { useEffect, useState } from 'react'

import { AboutPage } from './about/AboutPage'
import { AdminPage } from './admin/AdminPage'
import { ExplorePage } from './explore/ExplorePage'
import { GapsPage } from './explore/GapsPage'
import { MapPage } from './explore/MapPage'
import { NodePage } from './explore/NodePage'
import { SourcePage } from './explore/SourcePage'
import { focusSearch, isCommandK } from './lib/hotkeys'
import { useRoute, type Route } from './lib/route'
import { applyTheme, readTheme, writeTheme, type Theme } from './lib/theme'
import { TopBar, TopBarSlotProvider, type Section } from './ui/TopBar'

/**
 * The section a route belongs to. A source page and a node page belong to
 * Explore: marking no destination while a reader is two clicks into the corpus
 * would say the bar does not know where they are. About belongs to none — it
 * is reached from Settings, not the nav, and marking a tab for it would claim
 * a section it is not in.
 */
export function sectionOf(route: Route): Section | null {
  if (route.name === 'about') return null
  return route.name === 'admin' || route.name === 'map' || route.name === 'gaps'
    ? route.name
    : 'explore'
}

/**
 * The application shell (tasks P2-11, P2-08, P6-13, P6-27).
 *
 * §12.6 splits the interface in two, Explore and Admin, and the split is the
 * same one the API draws: `/api/explore/*` reads through the read-only role,
 * `/api/admin/*` writes. Keeping the interface's seam in the same place means a
 * reader always knows whether the screen they are on can change anything —
 * which is also why the bar is opaque on Admin and translucent everywhere else
 * (§5).
 *
 * **Pages own their width** (`P6-27`). `main` used to force every screen into
 * a 48rem reading column, which drew the map and the graph at thumbnail size and
 * made a dense Explore surface impossible. Now `main` is the full width under
 * the bar and each page sets its own measure.
 */
export function App() {
  const [theme, setTheme] = useState<Theme>(readTheme)
  const [slot, setSlot] = useState<HTMLElement | null>(null)
  const route = useRoute()
  const section = sectionOf(route)

  // On the root element, not on a wrapper div. The reader's preference has to
  // reach `color-scheme`, which the browser reads from the document element to
  // style scrollbars, form controls and autofill — none of which are inside
  // this component's subtree.
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  // ⌘K from anywhere. Registered once, on the document, so it works whatever
  // has focus — including inside another input, where a reader mid-note who
  // wants to look something up should not have to click out first.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!isCommandK(event)) return
      event.preventDefault()
      focusSearch()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [])

  function choose(next: Theme) {
    setTheme(next)
    writeTheme(next)
  }

  return (
    <TopBarSlotProvider value={slot}>
      <AskContextProvider>
      <div className="flex min-h-screen flex-col bg-ground text-text">
        <TopBar
          section={section}
          translucent={section !== 'admin'}
          theme={theme}
          onTheme={choose}
          slotRef={setSlot}
        />

        {/* Real URLs for every screen. A source page that could not be linked
            would be a corpus insisting everything be checkable while making its
            own documents unaddressable. */}
        <main className="relative flex-1">
          {route.name === 'source' ? <SourcePage sourceId={route.sourceId} /> : null}
          {route.name === 'node' ? <NodePage entityId={route.entityId} /> : null}
          {route.name === 'admin' ? <AdminPage /> : null}
          {route.name === 'map' ? <MapPage /> : null}
          {route.name === 'gaps' ? <GapsPage /> : null}
          {route.name === 'about' ? <AboutPage /> : null}
          {route.name === 'explore' ? <ExplorePage /> : null}
        </main>
        {/* On the reader's screens only: Admin is for configuring, not asking. */}
        {section !== 'admin' ? <AskPanel /> : null}
      </div>
      </AskContextProvider>
    </TopBarSlotProvider>
  )
}
