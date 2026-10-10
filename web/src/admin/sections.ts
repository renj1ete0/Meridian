import { useEffect, useState } from 'react'

/**
 * Admin's sections and their URLs (task P6-28), grouped as the `AdminLight` mock's
 * STEERING and SYSTEM. Every section is a URL, `/admin/<path>`. See docs/features/web-app.md#admin.
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
  | 'display'
  | 'access'
  | 'duplicates'

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
  // `B-202`. Beside the other approvals: a pair resolution could not decide.
  { key: 'duplicates', label: 'Possible duplicates', path: 'duplicates', group: 'system' },
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
  // `B-146`, ADRs 0003 and 0011: tokens for assistants over MCP.
  { key: 'access', label: 'Assistant access', path: 'access', group: 'system' },
  // `B-145`, ADR 0009: the zone every time is shown in.
  { key: 'display', label: 'Display', path: 'display', group: 'system' },
]

/**
 * Where bare `/admin` lands: Topics (`B-122`). A fresh install is sent to Seeds instead,
 * by `AdminPage`. See docs/features/web-app.md#admin.
 */
export const DEFAULT_SECTION: Section = 'topics'

export function hrefForSection(section: Section): string {
  const def = SECTIONS.find((s) => s.key === section)
  return `/admin/${def?.path ?? ''}`
}

/**
 * The section a path names, or null for bare `/admin` and anything unknown, so a caller
 * can tell "nobody chose" from a linked section.
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
 * Admin's ground (design-system §2): paper unless somebody explicitly chose dark.
 * See docs/features/web-app.md#admin.
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
