/**
 * The contested mark (task P6-16) — docs/design/design-system.md §6: the dagger (†),
 * always paired with the brass tint, never the tint alone. `tests/contested.test.tsx`
 * holds the rule. See docs/features/web-app.md#the-contested-mark.
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
 * The dagger itself, as a superscript, rendered as text so it survives copying, printing,
 * reading aloud and a page without CSS.
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
