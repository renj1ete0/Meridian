/**
 * Source-tier and provenance chips (task P6-16) — design-system.md §2, §5: a tier is a
 * bordered mono chip, every tier drawn alike, with no colour prop. See docs/features/web-app.md#tier-chips.
 */

/**
 * §5.2's tiers, in the order the spec lists them, mirrored from the database enum;
 * `tests/tier.test.ts` compares the two.
 */
export const SOURCE_TIERS = ['peer_reviewed', 'government', 'institutional', 'press', 'informal'] as const

export type SourceTier = (typeof SOURCE_TIERS)[number]

/** Display text. Not a ranking, and deliberately not abbreviated to initials. */
export const TIER_LABEL: Record<SourceTier, string> = {
  peer_reviewed: 'Peer reviewed',
  government: 'Government',
  institutional: 'Institutional',
  press: 'Press',
  informal: 'Informal',
}

/**
 * One shared appearance, as the Explore artboard draws a supporting chunk's tier: 9px
 * mono caps on a hairline border, no fill, faint ink.
 */
const TIER_CHIP =
  'inline-flex items-center border border-line px-1.5 py-px font-mono text-[9px] ' +
  'uppercase leading-[1.5] tracking-[0.1em] text-text-faint'

export function TierChip({ tier }: { tier: SourceTier }) {
  return (
    <span className={TIER_CHIP} data-tier={tier}>
      {TIER_LABEL[tier] ?? tier}
    </span>
  )
}

/**
 * A chip for anything else the system measured — an extractor name, a chunk
 * id, the retrieval mode. The same hairline form as a tier chip because it is
 * the same kind of thing, but set in lower case: these are values (`words + meaning`,
 * `chunk 412`), and capitals would turn a value into a heading.
 */
const DATA_CHIP =
  'inline-flex items-center border border-line px-1.5 py-px font-mono text-[10px] ' + 'leading-[1.5] text-text-faint'

export function DataChip({ children }: { children: React.ReactNode }) {
  return <span className={DATA_CHIP}>{children}</span>
}
