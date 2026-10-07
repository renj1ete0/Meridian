import { ago } from '../lib/status'

/**
 * What arrived while you were away (tasks P6-11, P6-27; spec §12.5, design-system.md §5):
 * one mono line. `null` is a first visit, `0` is nothing new, and edges and contested
 * pairs are absent. See docs/features/web-app.md#since-last-visit.
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
