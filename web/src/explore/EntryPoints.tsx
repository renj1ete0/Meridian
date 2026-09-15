import { Icon } from '../ui/Icon'

/**
 * §12.5's three entry points, as §8's parallel cards.
 *
 * Search, Coverage, Contested — and they are parallel on purpose. Search is the
 * one everyone reaches for, and giving it the whole screen would make the other
 * two features you have to know about. §12.3 notes that the coverage grid is the
 * view people skip and then miss, because absence is what gap analysis acts on
 * and node-link diagrams are bad at showing it; §9 makes contested pairs the
 * highest-value nodes in the graph. Neither survives being a menu item.
 *
 * The copy states what each one does, in §4's register: short declaratives,
 * concrete nouns, no promises about what you will find.
 */

export type EntryPointName = 'search' | 'coverage' | 'contested'

export interface EntryPoint {
  name: EntryPointName
  title: string
  /** One line. What this view shows, not what it will do for you. */
  description: string
}

export const ENTRY_POINTS: readonly EntryPoint[] = [
  {
    name: 'search',
    title: 'Search',
    description: 'Hybrid retrieval over the corpus. Every hit carries its source and tier.',
  },
  {
    name: 'coverage',
    title: 'Coverage',
    description: 'Topic by dimension. Thin and ageing cells first.',
  },
  {
    name: 'contested',
    title: 'Contested',
    description: 'Where sources disagree. Both edges kept, neither resolved.',
  },
] as const

export interface EntryPointsProps {
  /** Called with the entry point chosen. Routing belongs to the caller. */
  onOpen: (name: EntryPointName) => void
  /**
   * Entry points that cannot be opened yet, with the reason shown in place of
   * the description. A disabled card that says nothing is worse than no card.
   */
  unavailable?: Partial<Record<EntryPointName, string>>
}

export function EntryPoints({ onOpen, unavailable = {} }: EntryPointsProps) {
  return (
    <ul className="grid gap-4 sm:grid-cols-3">
      {ENTRY_POINTS.map((entry) => {
        const reason = unavailable[entry.name]
        return (
          <li key={entry.name}>
            <button
              type="button"
              disabled={reason !== undefined}
              onClick={() => onOpen(entry.name)}
              className="h-full w-full border border-line bg-surface p-4 text-left hover:border-line-strong disabled:cursor-not-allowed disabled:text-text-faint"
            >
              <span className="flex items-center gap-2 text-accent-graph">
                <Icon name={entry.name} size={20} />
                <span className="font-sans text-[length:var(--text-subhead)] font-semibold text-text">
                  {entry.title}
                </span>
              </span>
              <span className="mt-2 block text-[length:var(--text-small)] text-text-muted">
                {reason ?? entry.description}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}
