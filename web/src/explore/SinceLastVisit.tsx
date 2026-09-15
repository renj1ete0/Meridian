/**
 * What arrived while you were away (task P6-11, spec §12.5).
 *
 * §12.5 asks the landing state to carry a delta, because "what is new to me" is
 * the question somebody opens this with and a total answers a different one.
 *
 * **Three states, not two.** `null` means this reader has never been here, so
 * there is no "since" to speak of and claiming one would be inventing history.
 * `0` means they have, and nothing arrived — which is a finding worth stating,
 * because a silent panel reads as a page that failed to load its delta. Any
 * other number is the delta itself.
 */

export interface SinceLastVisitProps {
  /** `null` when this reader has no previous visit recorded. */
  newSources: number | null
  newChunks: number | null
}

export function SinceLastVisit({ newSources, newChunks }: SinceLastVisitProps) {
  if (newSources === null || newChunks === null) {
    // First visit. Not an error and not a zero — there is genuinely no
    // previous moment to measure from.
    return null
  }

  if (newSources === 0 && newChunks === 0) {
    return (
      <p className="font-mono text-[length:var(--text-data)] text-text-faint">
        Nothing new since you were last here.
      </p>
    )
  }

  return (
    <p className="font-mono text-[length:var(--text-data)] text-text-muted">
      <span className="text-accent-graph">{newSources.toLocaleString()}</span>{' '}
      {newSources === 1 ? 'source' : 'sources'} and{' '}
      <span className="text-accent-graph">{newChunks.toLocaleString()}</span>{' '}
      {newChunks === 1 ? 'passage' : 'passages'} since you were last here.
    </p>
  )
}
