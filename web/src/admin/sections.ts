import { useEffect, useState } from 'react'

/**
 * Admin's sections and their URLs (task P6-28).
 *
 * The `AdminLight` mock groups the left nav as STEERING and SYSTEM. Its labels
 * are mapped onto what exists: "Seed log" is the cold-start seed list, "Fetch
 * policy" is the per-domain table, and Crawl health (`P6-25`) joins SYSTEM
 * because it is read alongside fetch policy. The mock's "Enrichment queue" is
 * listed and marked unbuilt rather than dropped: the table exists (`P0-09`)
 * and the task that fills it is `P7-07`, and a nav that silently omitted it
 * would read as the design having been forgotten.
 *
 * **Every section is a URL** (`/admin/<path>`), so a section can be linked and
 * the back button works. `route.ts` already treats everything under `/admin/`
 * as Admin; which section is read here, from the path, so the router does not
 * need to know Admin has sections at all.
 */

export type Section =
  | 'topics'
  | 'boosts'
  | 'proposals'
  | 'seeds'
  | 'agents'
  | 'gazetteer'
  | 'enrichment'
  | 'runs'
  | 'domains'
  | 'health'

export interface SectionDef {
  key: Section
  label: string
  path: string
  group: 'steering' | 'system'
  /** A section with no backend yet. Listed, and says so when opened. */
  unbuilt?: string
}

export const SECTIONS: readonly SectionDef[] = [
  { key: 'topics', label: 'Topic weights', path: 'topics', group: 'steering' },
  { key: 'boosts', label: 'Pins & boosts', path: 'boosts', group: 'steering' },
  // `P6-38`. Beside the controls it proposes changes to: a proposal is a boost
  // or a weight that will be set unless somebody says no.
  { key: 'proposals', label: 'Proposals', path: 'proposals', group: 'steering' },
  // Last in its group, because it is the one section that stops mattering. It
  // is also the first thing anybody needs on a fresh install, which is why
  // Admin opens on it when nothing has been crawled yet.
  { key: 'seeds', label: 'Seeds', path: 'seeds', group: 'steering' },
  // `P6-23`. Beside Run history in spirit: a run that deferred and the registry
  // row that made it defer are one question asked twice.
  { key: 'agents', label: 'Agent registry', path: 'agents', group: 'system' },
  { key: 'gazetteer', label: 'Gazetteer approvals', path: 'gazetteer', group: 'system' },
  {
    key: 'enrichment',
    label: 'Enrichment queue',
    path: 'enrichment',
    group: 'system',
    unbuilt: 'P7-07',
  },
  { key: 'runs', label: 'Run history', path: 'runs', group: 'system' },
  { key: 'domains', label: 'Fetch policy', path: 'fetch-policy', group: 'system' },
  // `P6-25`. Beside Fetch policy because they are read together: an outcome
  // mix full of refusals is answered by that domain's policy row.
  { key: 'health', label: 'Crawl health', path: 'crawl', group: 'system' },
]

/**
 * Where bare `/admin` lands: Topics, as the `AdminLight` artboard draws it
 * (`B-122`). It was the gazetteer queue, as the weekly task (§5.6), until the
 * queue grew to tens of thousands of harvested terms — a landing page that
 * opens on a backlog nobody will clear reads as the system being behind,
 * while Topics is what steers the crawl. The queue is one click away. A fresh
 * install is sent to Seeds instead, by `AdminPage`.
 */
export const DEFAULT_SECTION: Section = 'topics'

export function hrefForSection(section: Section): string {
  const def = SECTIONS.find((s) => s.key === section)
  return `/admin/${def?.path ?? ''}`
}

/**
 * The section a path names, or null for bare `/admin` and anything unknown.
 *
 * Null rather than the default, so the caller can tell "nobody chose" (where a
 * fresh install may be redirected to Seeds) from "somebody linked Gazetteer".
 */
export function sectionFromPath(pathname: string): Section | null {
  const match = /^\/admin\/([^/]+)\/?$/.exec(pathname)
  if (!match) return null
  return SECTIONS.find((s) => s.path === match[1])?.key ?? null
}

/** The section in the address bar, kept current across in-app navigation. */
export function usePathSection(): Section | null {
  const [section, setSection] = useState(() => sectionFromPath(window.location.pathname))
  useEffect(() => {
    const onChange = () => setSection(sectionFromPath(window.location.pathname))
    window.addEventListener('popstate', onChange)
    return () => window.removeEventListener('popstate', onChange)
  }, [])
  return section
}

/**
 * Admin's ground (design-system §2: "Light exists for docs, Admin and print").
 *
 * Admin is paper unless somebody has explicitly chosen dark. "No choice" means
 * the designed look, which for Admin is paper whatever the machine prefers —
 * the flagship dark belongs to the canvas, and Admin is a document. An explicit
 * dark choice still wins, because overriding a preference somebody set is the
 * one thing a theme control must never do.
 */
export function adminTheme(rootTheme: string | null): 'light' | 'dark' {
  return rootTheme === 'dark' ? 'dark' : 'light'
}

/** `adminTheme` for the document as it is now, following the theme toggle. */
export function useAdminTheme(): 'light' | 'dark' {
  const read = () => adminTheme(document.documentElement.getAttribute('data-theme'))
  const [theme, setTheme] = useState(read)
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()))
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme'],
    })
    return () => observer.disconnect()
  }, [])
  return theme
}
