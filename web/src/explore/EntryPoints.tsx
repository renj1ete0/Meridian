import { DAGGER } from '../ui/Contested'
import { Icon } from '../ui/Icon'

/**
 * §12.5's three entry points, as §8's parallel cards (`ExploreLanding.dc.html`).
 *
 * Search, Coverage, Contested — and they are parallel on purpose. Search is the
 * one everyone reaches for, and giving it the whole screen would make the other
 * two features you have to know about. §12.3 notes that the coverage grid is the
 * view people skip and then miss, because absence is what gap analysis acts on
 * and node-link diagrams are bad at showing it; §9 makes contested pairs the
 * highest-value nodes in the graph. Neither survives being a menu item.
 *
 * The copy states what each one does, in §4's register: short declaratives,
 * concrete nouns, no promises about what you will find. Each card may carry one
 * mono line beneath — a measured fact about that view, where one exists. Where
 * none exists the line is absent rather than invented: the artboard's
 * "9 thin cells · 3 stale" needs coverage scoring, which is not built.
 *
 * Coverage opens Gaps (`P6-36`, `P6-42`): the one ranked list of what the
 * corpus cannot answer is where absence became visible, and a card that said
 * "not built" beside it was a dead end on the first screen (`P6-44`).
 */

export type EntryPointName = 'search' | 'coverage' | 'contested'

export interface EntryPoint {
  name: EntryPointName
  title: string
  /** One or two lines. What this view shows, not what it will do for you. */
  description: string
}

export const ENTRY_POINTS: readonly EntryPoint[] = [
  {
    name: 'search',
    title: 'Ask a question',
    description:
      'The evidence grouped by country, each place marked strong or thin, in the sources’ own words. Nothing is generated.',
  },
  {
    name: 'coverage',
    title: 'Coverage',
    description:
      'What the corpus cannot answer yet, most severe first — thin topics, places and fields — each with a search to fill it.',
  },
  {
    name: 'contested',
    title: 'Contested',
    description: 'Claims sources disagree about. Both sides are kept; neither is resolved.',
  },
] as const

export interface EntryPointsProps {
  /**
   * What opening each card does. Routing belongs to the caller, and a card
   * with no action here is drawn as a card, not a button — a control that
   * does nothing when pressed is worse than one that is not a control.
   */
  actions?: Partial<Record<EntryPointName, () => void>>
  /**
   * Entry points that cannot be opened yet, with the reason. The reason is
   * shown on the card — a disabled card that says nothing is worse than no
   * card — and the card stops being a control.
   */
  unavailable?: Partial<Record<EntryPointName, string>>
  /** Per-card descriptions that carry a real figure, replacing the default. */
  descriptions?: Partial<Record<EntryPointName, string>>
  /** The optional mono line under a card, where there is a measured fact to show. */
  details?: Partial<Record<EntryPointName, string>>
}

export function EntryPoints({
  actions = {},
  unavailable = {},
  descriptions = {},
  details = {},
}: EntryPointsProps) {
  return (
    <ul className="grid gap-5 sm:grid-cols-3">
      {ENTRY_POINTS.map((entry) => {
        const reason = unavailable[entry.name]
        const contested = entry.name === 'contested'
        const body = (
          <>
            <span className={contested ? 'text-accent-attention' : 'text-accent-graph'}>
              {contested ? <DisagreeGlyph /> : <Icon name={entry.name} size={22} />}
            </span>
            <span className="font-sans text-[16px] font-semibold leading-tight tracking-[-0.004em] text-text">
              {entry.title}
              {contested ? (
                <sup className="ml-px font-mono text-[12px] text-accent-attention" aria-label="contested">
                  {DAGGER}
                </sup>
              ) : null}
            </span>
            <span className="text-[12.5px] leading-[1.55] text-text-muted">
              {descriptions[entry.name] ?? entry.description}
            </span>
            {reason !== undefined || details[entry.name] ? (
              <span
                className={`mt-auto font-mono text-[10.5px] leading-[1.5] ${
                  contested && reason === undefined ? 'text-accent-attention' : 'text-text-faint'
                }`}
              >
                {reason ?? details[entry.name]}
              </span>
            ) : null}
          </>
        )
        const card = 'flex h-full w-full flex-col gap-3 border border-line bg-surface p-5 text-left'
        const action = actions[entry.name]
        return (
          <li key={entry.name}>
            {reason === undefined && action ? (
              <button
                type="button"
                onClick={action}
                className={`${card} hover:border-line-strong hover:bg-surface-raised/40`}
              >
                {body}
              </button>
            ) : (
              <div className={card} aria-disabled={reason !== undefined ? true : undefined}>
                {body}
              </div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

/**
 * The landing artboard's glyph for the contested card: two arcs facing each
 * other across a point — two sources, one claim. Drawn at the interface weight
 * on the 24-unit grid.
 */
function DisagreeGlyph() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true" focusable="false">
      <path d="M9 3.5A9 9 0 0 0 9 20.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M15 3.5A9 9 0 0 1 15 20.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="12" r="2.2" fill="currentColor" />
    </svg>
  )
}
