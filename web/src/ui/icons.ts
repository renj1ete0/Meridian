/**
 * The icon set (tasks P6-16, P6-27) — docs/design/design-system.md §7.
 *
 * Path data only. Every shared attribute — viewBox, stroke, linecap, linejoin —
 * is applied by `<Icon>` rather than repeated here, so the grid rules cannot be
 * violated one icon at a time. `tests/ui.test.tsx` checks the geometry against
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
 * Transcribed from `docs/design/Icons.dc.html` rather than drawn here (`P6-27`):
 * the first pass was drawn to the grid but not to the artboard, and the
 * difference was visible beside every mock — a gauge where the design has
 * sliders, a plain square where it has a grid with one cell lit.
 */

export interface IconGeometry {
  /** The heavier outline. Must read alone at small sizes. */
  silhouette: string
  /** Interior detail at the lighter weight. Dropped below 20px. */
  detail?: string
  /**
   * Solid shapes — the knobs on the steering sliders, the ends of a path.
   * Filled rather than stroked, and kept at every size, for the same reason the
   * mark's nodes are: a filled dot survives rasterisation where a stroke does not.
   */
  solid?: string
  /** A filled area at reduced opacity — coverage's one lit cell. */
  tint?: string
}

/** §7's interface set, at the 20-unit live area. */
export const INTERFACE_ICONS = {
  search: {
    silhouette: 'M10.5 3.8a6.7 6.7 0 1 0 0 13.4 6.7 6.7 0 0 0 0-13.4ZM15.4 15.4L20.6 20.6',
  },
  coverage: {
    silhouette: 'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z',
    detail: 'M9 3V21M15 3V21M3 9H21M3 15H21',
    tint: 'M15 9h6v6h-6Z',
  },
  // §6: the dagger drawn to grid. The one icon that is also a typographic mark,
  // which is why contested needs no colour to be read.
  contested: {
    silhouette: 'M12 2.6V21.4M6 7.6H18',
  },
  savedView: {
    silhouette: 'M6 3H18V21L12 16.4L6 21Z',
  },
  annotate: {
    silhouette: 'M4 20.2L4.9 16.4L16.1 5.2L18.9 8L7.7 19.2Z',
    detail: 'M14.2 7.1L17 9.9',
  },
  report: {
    silhouette:
      'M7 2.6h10a2 2 0 0 1 2 2v14.8a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4.6a2 2 0 0 1 2-2Z',
    detail: 'M8.6 8.2H15.4M8.6 12.2H15.4M8.6 16.2H13',
  },
  notifications: {
    silhouette: 'M7 10.6a5 5 0 0 1 10 0v4l2 3H5l2-3ZM9.9 20.6a2.3 2.3 0 0 0 4.2 0',
  },
  history: {
    silhouette: 'M12 3.6a8.4 8.4 0 1 0 0 16.8 8.4 8.4 0 0 0 0-16.8Z',
    detail: 'M12 6.8V12L15.8 14',
  },
  // Two sliders. Also the settings control in the top bar: every top-bar
  // artboard draws settings with this glyph.
  steering: {
    silhouette: 'M3.4 8.6H20.6M3.4 15.4H20.6',
    solid:
      'M9 6.4h1.6a1 1 0 0 1 1 1v2.4a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1V7.4a1 1 0 0 1 1-1Z' +
      'M14.8 13.2h1.6a1 1 0 0 1 1 1v2.4a1 1 0 0 1-1 1h-1.6a1 1 0 0 1-1-1v-2.4a1 1 0 0 1 1-1Z',
  },
  export: {
    silhouette: 'M12 3.4V15M8.2 7.2L12 3.4L15.8 7.2M4.6 14.6V20.6H19.4V14.6',
  },
  pathMode: {
    silhouette: 'M6.4 16.8A13 13 0 0 1 17.6 7.2',
    detail: 'M10.6 8.9a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4Z',
    solid:
      'M4.6 16a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2Z' +
      'M19.4 2.8a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2Z',
  },
} as const satisfies Record<string, IconGeometry>

/**
 * §5.4's node ontology, at the 16-unit live area and the secondary weight.
 *
 * These are glyphs rather than icons: they appear at node scale on the canvas
 * and beside chips in prose, never as controls. The names are the spec's node
 * types and the set must stay in step with them.
 */
export const NODE_GLYPHS = {
  concept: { silhouette: 'M12 5a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z' },
  place: {
    silhouette:
      'M12 20.6C12 20.6 5.6 14.4 5.6 10.6A6.4 6.4 0 1 1 18.4 10.6C18.4 14.4 12 20.6 12 20.6Z',
    detail: 'M12 8.1a2.3 2.3 0 1 0 0 4.6 2.3 2.3 0 0 0 0-4.6Z',
  },
  organisation: {
    silhouette: 'M6 7h12v13H6Z',
    detail: 'M10 7V20M14 7V20',
  },
  intervention: {
    silhouette: 'M13.8 5.8a6.2 6.2 0 1 0 0 12.4 6.2 6.2 0 0 0 0-12.4ZM2.8 17.8L8.4 13.8',
    detail: 'M5.6 13.9L8.6 13.7L8.3 16.7',
  },
  finding: {
    silhouette: 'M5 17.2H19',
    solid: 'M12 6.8a3.2 3.2 0 1 0 0 6.4 3.2 3.2 0 0 0 0-6.4Z',
  },
  source: {
    silhouette: 'M4.6 7h11.4v14H4.6Z',
    detail: 'M8 4.2H19.4V18',
  },
} as const satisfies Record<string, IconGeometry>

export type InterfaceIconName = keyof typeof INTERFACE_ICONS
export type NodeGlyphName = keyof typeof NODE_GLYPHS

export const ICONS: Record<string, IconGeometry> = { ...INTERFACE_ICONS, ...NODE_GLYPHS }

/** Whether a name is a node glyph, which §7 draws at the secondary weight. */
export function isNodeGlyph(name: string): name is NodeGlyphName {
  return Object.prototype.hasOwnProperty.call(NODE_GLYPHS, name)
}
