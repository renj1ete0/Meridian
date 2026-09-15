/**
 * The contested mark (task P6-16) — docs/design/design-system.md §6.
 *
 * **† (U+2020, dagger).** The typographic mark for "a qualifying note is
 * attached to this", which is exactly what contested means. It exists in both
 * type families, so it needs no icon system and inherits weight and colour from
 * the type around it.
 *
 * §6 states the rule and states its test in the same breath: the dagger is
 * always paired with the brass tint and the brass tint never appears without
 * it — *"strip the colour and the reading must survive — that is the test."*
 * `tests/contested.test.tsx` is that test, written as one.
 *
 * This matters beyond aesthetics. §9 makes contradiction a result rather than
 * an error, and contested nodes the highest-value ones in the graph. A reader
 * who cannot see which those are — because they are colour-blind, because the
 * page is printed, because the screen is in sunlight — loses the finding, not
 * the decoration.
 */

export const DAGGER = '†'

export type ContestedForm = 'inline' | 'chip' | 'badge'

export interface ContestedProps {
  /** `inline` marks a claim in prose; `chip` a node; `badge` a detail panel. */
  form?: ContestedForm
  children?: React.ReactNode
}

export function Contested({ form = 'inline', children }: ContestedProps) {
  if (form === 'badge') {
    // §6: leading dagger with a non-breaking space, so the mark and the word
    // never wrap apart from each other.
    return (
      <span className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-accent-attention">
        {DAGGER}
        {' '}Contested
      </span>
    )
  }

  if (form === 'chip') {
    return (
      <span className="rounded-chip border border-accent-attention-deep bg-accent-attention-deep/35 px-2 py-0.5 font-mono text-[length:var(--text-data)] text-accent-attention">
        {children}
        <Mark />
      </span>
    )
  }

  return (
    <span className="border-b border-accent-attention text-accent-attention">
      {children}
      <Mark />
    </span>
  )
}

/**
 * The dagger itself, as a superscript.
 *
 * Rendered as text rather than as a pseudo-element or a background image, which
 * is the whole point: it has to survive being copied, printed, read aloud and
 * rendered without CSS.
 */
function Mark() {
  return (
    <sup
      className="font-mono text-[0.72em]"
      // Read aloud as a word rather than as "dagger", which most screen readers
      // either say literally or skip.
      aria-label="contested"
    >
      {DAGGER}
    </sup>
  )
}
