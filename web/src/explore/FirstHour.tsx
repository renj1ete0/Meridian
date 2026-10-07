import type { CrawlProgress } from '../lib/api'

/**
 * What an empty corpus has to show (task B-09, scaffold §1.7, §12.3): the queue draining,
 * with both halves of the fetch rate. Every number is live.
 * See docs/features/web-app.md#the-first-hour.
 */

export interface FirstHourProps {
  progress: CrawlProgress
}

/** Statuses worth naming, in the order a task moves through them. */
export const QUEUE_ORDER = ['pending', 'fetched', 'extracted', 'embedded', 'done', 'failed'] as const

export function waiting(progress: CrawlProgress): number {
  return progress.queue.pending ?? 0
}

/** Whether anything has been attempted at all, which is a different question
 * from whether anything succeeded. */
export function hasStarted(progress: CrawlProgress): boolean {
  return progress.attempts_last_hour > 0 || Object.keys(progress.queue).length > 0
}

export function FirstHour({ progress }: FirstHourProps) {
  const pending = waiting(progress)
  const started = hasStarted(progress)

  return (
    <section className="flex flex-col gap-3 border border-line bg-surface p-5" aria-labelledby="first-hour-heading">
      <h2 id="first-hour-heading" className="font-sans text-[16px] font-semibold text-text">
        Nothing to search yet
      </h2>

      {started ? (
        <p className="text-[12.5px] leading-[1.55] text-text-muted">
          The crawl is working through {pending.toLocaleString()} queued {pending === 1 ? 'page' : 'pages'}. Documents
          become searchable as they are fetched, extracted and chunked — the first ones usually within the hour.
        </p>
      ) : (
        <p className="text-[12.5px] leading-[1.55] text-text-muted">
          The queue is empty and nothing has been attempted. Add a cold-start seed in Admin, or the crawl has nowhere to
          begin.
        </p>
      )}

      <dl className="flex flex-wrap gap-x-6 gap-y-2">
        {QUEUE_ORDER.map((status) => [status, progress.queue[status] ?? 0] as const)
          .filter(([, count]) => count > 0)
          .map(([status, count]) => (
            <div key={status} className="flex flex-col-reverse gap-1">
              <dt className="font-mono text-[9px] uppercase tracking-[var(--tracking-label)] text-text-faint">
                {status}
              </dt>
              <dd className="font-mono text-[15px] tabular-nums text-text">{count.toLocaleString()}</dd>
            </div>
          ))}
      </dl>

      {progress.attempts_last_hour > 0 ? (
        <p className="font-mono text-[10.5px] leading-[1.5] text-text-faint">
          {progress.successes_last_hour.toLocaleString()} of {progress.attempts_last_hour.toLocaleString()} fetches
          succeeded in the last hour.
          {progress.successes_last_hour === 0 ? ' Every one failed — check Domains for what is being refused.' : ''}
        </p>
      ) : null}

      {progress.recent_domains.length > 0 ? (
        <div className="flex flex-col gap-1.5">
          <h3 className="font-mono text-[9px] uppercase tracking-[var(--tracking-label)] text-text-faint">
            Most recently fetched
          </h3>
          {/* Names, not a count. "14 fetches" says less about whether this is
              working than one domain somebody recognises. */}
          <ul className="flex flex-wrap gap-x-3 gap-y-1 font-mono text-[10.5px] text-accent-graph">
            {progress.recent_domains.map((domain) => (
              <li key={domain}>{domain}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  )
}
