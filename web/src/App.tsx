import { AskContextProvider, AskPanel } from './explore/AskPanel'
import { useEffect, useState } from 'react'

import { AboutPage } from './about/AboutPage'
import { AdminPage } from './admin/AdminPage'
import { ContestedPage } from './explore/ContestedPage'
import { ExplorePage } from './explore/ExplorePage'
import { GapsPage } from './explore/GapsPage'
import { GrowthPage } from './explore/GrowthPage'
import { MapPage } from './explore/MapPage'
import { NodePage } from './explore/NodePage'
import { SourcePage } from './explore/SourcePage'
import { WhatsNewPage } from './explore/WhatsNewPage'
import { displaySettings } from './lib/api'
import { MeProvider, useMe } from './lib/me'
import { focusSearch, isCommandK } from './lib/hotkeys'
import { useRoute, type Route } from './lib/route'
import { applyTheme, readTheme, writeTheme, type Theme } from './lib/theme'
import { displayZone, setDisplayZone } from './lib/time'
import { NoAccess } from './ui/NoAccess'
import { TopBar, TopBarSlotProvider, type Section } from './ui/TopBar'

/**
 * The section a route belongs to: source and node pages are Explore's, and About belongs
 * to none, since it is reached outside the nav.
 */
export function sectionOf(route: Route): Section | null {
  if (route.name === 'about') return null
  return route.name === 'admin' ||
    route.name === 'map' ||
    route.name === 'gaps' ||
    route.name === 'growth' ||
    route.name === 'new'
    ? route.name
    : 'explore'
}

/**
 * The application shell (tasks P2-11, P2-08, P6-13, P6-27): the top bar and the page
 * for the route. The Explore/Admin seam matches the API's; pages own their width.
 * See docs/features/web-app.md#the-shell.
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

  // The display zone (ADR 0009). Until it answers, times read in the default zone; when it
  // does, `zone` changes and every page re-renders in the deployment's zone.
  const [zone, setZone] = useState(displayZone)
  useEffect(() => {
    const controller = new AbortController()
    displaySettings({ signal: controller.signal })
      .then((settings) => {
        setDisplayZone(settings.display_timezone)
        setZone(displayZone())
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [])

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
    <MeProvider>
      <Shell section={section} route={route} theme={theme} onTheme={choose} slot={slot} setSlot={setSlot} zone={zone} />
    </MeProvider>
  )
}

/** The app inside {@link MeProvider}, so it can show a person only what they may see (`B-257`). */
function Shell({
  section,
  route,
  theme,
  onTheme,
  slot,
  setSlot,
  zone,
}: {
  section: Section | null
  route: Route
  theme: Theme
  onTheme: (next: Theme) => void
  slot: HTMLElement | null
  setSlot: (el: HTMLElement | null) => void
  zone: string
}) {
  const { me } = useMe()
  // Someone Access let in with no permission at all: one page that says so (and, on a fresh
  // instance, how to claim it), instead of every screen failing in its own words.
  const locked = me !== null && me.permissions.length === 0
  return (
    <TopBarSlotProvider value={slot}>
      <AskContextProvider>
        <div className="flex min-h-screen flex-col bg-ground text-text">
          <TopBar
            section={section}
            translucent={section !== 'admin'}
            theme={theme}
            onTheme={onTheme}
            slotRef={setSlot}
          />

          {/* Real URLs for every screen. A source page that could not be linked
            would be a corpus insisting everything be checkable while making its
            own documents unaddressable. */}
          {/* Room at the foot of every reading page for the question toggle, so
            the last content can scroll clear of it (`B-125`). Admin has none. */}
          <main data-zone={zone} className={`relative flex-1 ${section !== 'admin' ? 'pb-20' : ''}`}>
            {locked ? <NoAccess /> : null}
            {!locked && route.name === 'source' ? <SourcePage sourceId={route.sourceId} /> : null}
            {!locked && route.name === 'node' ? <NodePage entityId={route.entityId} /> : null}
            {!locked && route.name === 'admin' ? <AdminPage /> : null}
            {!locked && route.name === 'map' ? <MapPage /> : null}
            {!locked && route.name === 'gaps' ? <GapsPage /> : null}
            {!locked && route.name === 'growth' ? <GrowthPage /> : null}
            {!locked && route.name === 'new' ? <WhatsNewPage /> : null}
            {!locked && route.name === 'about' ? <AboutPage /> : null}
            {!locked && route.name === 'contested' ? <ContestedPage /> : null}
            {!locked && route.name === 'explore' ? <ExplorePage /> : null}
          </main>
          {/* On the reader's screens only: Admin is for configuring, not asking. */}
          {section !== 'admin' && !locked ? <AskPanel /> : null}
        </div>
      </AskContextProvider>
    </TopBarSlotProvider>
  )
}
