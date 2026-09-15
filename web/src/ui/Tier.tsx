/**
 * Source-tier and provenance chips (task P6-16) — design-system.md §2, §5.
 *
 * Two published rules meet here, and both are load-bearing rather than
 * stylistic.
 *
 * **"A tier is a bordered mono chip, not a coloured dot" (§5).** State must be
 * carried by form, not only by colour. A dot conveys nothing to a reader who
 * cannot distinguish its hue, and nothing at all in print.
 *
 * **"There is deliberately no green and no red in the palette. Nothing in this
 * system is pass/fail, and colour must not imply a verdict" (§2).** So every
 * tier chip is drawn identically and differs only in its text. A palette that
 * ranked tiers — peer-reviewed in green, informal in red — would be the
 * interface asserting a credibility judgement the system explicitly refuses to
 * make: §8 extracts funder, stance and hedging and scores nothing, and the copy
 * must not imply it did.
 *
 * That is why `TierChip` takes no variant, no tone and no colour prop. There is
 * nowhere to put a verdict.
 */

/**
 * §5.2's tiers, in the order the spec lists them.
 *
 * Mirrored from the database enum rather than invented here, and
 * `tests/tier.test.ts` compares the two across the language boundary — a tier
 * added to Postgres and not to this list renders as an unstyled fallback, which
 * is the kind of thing nobody notices until it is in front of someone.
 */
export const SOURCE_TIERS = [
  'peer_reviewed',
  'government',
  'institutional',
  'press',
  'informal',
] as const

export type SourceTier = (typeof SOURCE_TIERS)[number]

/** Display text. Not a ranking, and deliberately not abbreviated to initials. */
export const TIER_LABEL: Record<SourceTier, string> = {
  peer_reviewed: 'Peer reviewed',
  government: 'Government',
  institutional: 'Institutional',
  press: 'Press',
  informal: 'Informal',
}

/** One shared appearance. See the module docstring for why there is only one. */
const CHIP =
  'inline-flex items-center rounded-chip border border-line-strong bg-surface-raised ' +
  'px-2 py-0.5 font-mono text-[length:var(--text-label)] uppercase ' +
  'tracking-[var(--tracking-label)] text-text-muted'

export function TierChip({ tier }: { tier: SourceTier }) {
  return (
    <span className={CHIP} data-tier={tier}>
      {TIER_LABEL[tier] ?? tier}
    </span>
  )
}

/**
 * A chip for anything else the system measured — an extractor name, a date, a
 * chunk id. Same form as a tier chip because it is the same kind of thing:
 * something the system recorded rather than something a person wrote.
 */
export function DataChip({ children }: { children: React.ReactNode }) {
  return <span className={CHIP}>{children}</span>
}
