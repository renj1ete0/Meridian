import { ago } from '../lib/status'

/**
 * What arrived while you were away (tasks P6-11, P6-27; spec §12.5,
 * design-system.md §5).
 *
 * §5: "a single mono line of deltas since their last visit, set with the other
 * counts, not as a separate widget." So this is one line of `data` type at the
 * head of "where you were", and the figures in it are brighter than the words
 * around them — they are what a returning reader's eye is looking for.
 *
 * **Three states, not two.** `null` means this reader has never been here, so
 * there is no "since" to speak of and claiming one would be inventing history.
 * `0` means they have, and nothing arrived — which is a finding worth stating,
 * because a silent line reads as a page that failed to load its delta. Any
 * other number is the delta itself.
 *
 * The artboard's line also carries edges and newly-contested pairs. The API's
 * delta covers sources and passages only (`P6-11`), so those two are absent
 * rather than shown as zeros.
 */

export interface SinceLastVisitProps {
  /** `null` when this reader has no previous visit recorded. */
  newSources: number | null
  newChunks: number | null
  /** When the previous visit was, for "3 days ago". Omitted: no time is named. */
  since?: string | null
  now?: Date
}

export function SinceLastVisit({ newSources, newChunks, since = null, now }: SinceLastVisitProps) {
  if (newSources === null || newChunks === null) {
    // First visit. Not an error and not a zero — there is genuinely no
    // previous moment to measure from.
    return null
  }

  const when = since ? `, ${ago(since, now)}` : ''

  if (newSources === 0 && newChunks === 0) {
    return (
      <p className="font-mono text-[11.5px] leading-[1.5] text-text-faint">
        Nothing new since you were last here{when}.
      </p>
    )
  }

  return (
    <p className="font-mono text-[11.5px] leading-[1.5] text-text-muted tabular-nums">
      Since you were last here{when} — <span className="text-text">{newSources.toLocaleString('en')}</span>{' '}
      {newSources === 1 ? 'source' : 'sources'} and <span className="text-text">{newChunks.toLocaleString('en')}</span>{' '}
      {newChunks === 1 ? 'passage' : 'passages'}.
    </p>
  )
}
