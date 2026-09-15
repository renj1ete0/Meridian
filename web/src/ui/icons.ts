/**
 * The icon set (task P6-16) — docs/design/design-system.md §7.
 *
 * Path data only. Every shared attribute — viewBox, stroke, linecap, linejoin —
 * is applied by `<Icon>` rather than repeated here, so the grid rules cannot be
 * violated one icon at a time. `tests/icons.test.ts` checks the geometry against
 * the published values.
 *
 * Drawn in the mark's own language: circles and arcs before rectangles. Two
 * weights, and the ratio is the mark's own — its meridian is 2.8 against a 2.4
 * globe, which is the same relationship as 1.6 to 1.35. Silhouette is always
 * heavier than the detail inside it.
 *
 * `detail` is dropped below 20px (§7's optical-size rule, the same one the
 * compact mark follows), so an icon must remain recognisable from `silhouette`
 * alone. Where an icon has no interior detail, `detail` is simply absent.
 *
 * These are a first pass at the eleven interface icons and six node-type glyphs
 * §7 names. The geometry follows the published grid; the drawing itself is the
 * part a designer should still put hands on.
 */

export interface IconGeometry {
  /** The heavier outline. Must read alone at small sizes. */
  silhouette: string
  /** Interior detail at the lighter weight. Dropped below 20px. */
  detail?: string
}

/** §7's interface set, at the 20-unit live area. */
export const INTERFACE_ICONS = {
  search: {
    silhouette: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z',
    detail: 'M16 16l4 4',
  },
  coverage: {
    silhouette: 'M4 4h16v16H4z',
    detail: 'M4 12h16M12 4v16',
  },
  // §6: the dagger drawn to grid. The one icon that is also a typographic mark,
  // which is why contested needs no colour to be read.
  contested: {
    silhouette: 'M12 3v18',
    detail: 'M7 8h10',
  },
  savedView: {
    silhouette: 'M6 3h12v18l-6-5-6 5z',
  },
  annotate: {
    silhouette: 'M4 20l1-5L17 3l4 4L9 19z',
    detail: 'M15 5l4 4',
  },
  report: {
    silhouette: 'M6 2h8l5 5v15H6z',
    detail: 'M14 2v5h5M9 13h7M9 17h7',
  },
  notifications: {
    silhouette: 'M12 3a6 6 0 0 0-6 6c0 5-2 7-2 7h16s-2-2-2-7a6 6 0 0 0-6-6Z',
    detail: 'M10 20a2 2 0 0 0 4 0',
  },
  history: {
    silhouette: 'M12 4a8 8 0 1 0 8 8',
    detail: 'M12 7v5l4 2M20 4v5h-5',
  },
  steering: {
    silhouette: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18Z',
    detail: 'M12 12l5-4',
  },
  export: {
    silhouette: 'M4 15v5h16v-5',
    detail: 'M12 3v12M8 8l4-5 4 5',
  },
  pathMode: {
    silhouette: 'M6 18a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM18 12a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
    detail: 'M8 13.5C10 10 13 9.5 15.5 9.5',
  },
} as const satisfies Record<string, IconGeometry>

/**
 * §5.4's node ontology, at the 16-unit live area and the secondary weight.
 *
 * These are glyphs rather than icons: they appear at node scale on the canvas
 * and beside chips in prose, never as controls. The names are the spec's node
 * types and the set must stay in step with them — `tests/icons.test.ts` checks
 * that against the database enum rather than against a list written here.
 */
export const NODE_GLYPHS = {
  concept: { silhouette: 'M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z' },
  place: {
    silhouette: 'M12 21s6-6.5 6-11a6 6 0 0 0-12 0c0 4.5 6 11 6 11Z',
    detail: 'M12 8a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z',
  },
  organisation: {
    silhouette: 'M8 8a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM17 6a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5Z',
    detail: 'M17 11v4a2 2 0 0 1-2 2h-4',
  },
  intervention: {
    silhouette: 'M5 12h12',
    detail: 'M13 8l4 4-4 4',
  },
  finding: {
    silhouette: 'M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z',
    detail: 'M12 10.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z',
  },
  source: {
    silhouette: 'M7 3h7l4 4v14H7z',
    detail: 'M14 3v4h4',
  },
} as const satisfies Record<string, IconGeometry>

export type InterfaceIconName = keyof typeof INTERFACE_ICONS
export type NodeGlyphName = keyof typeof NODE_GLYPHS

export const ICONS: Record<string, IconGeometry> = { ...INTERFACE_ICONS, ...NODE_GLYPHS }
