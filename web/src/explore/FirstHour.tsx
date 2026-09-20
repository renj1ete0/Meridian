import type { CrawlProgress } from '../lib/api'

/**
 * What an empty corpus has to show (task B-09, scaffold §1.7, §12.3).
 *
 * Production starts empty by design, so for the first hour there is nothing to
 * search. The two options the task names were shipping a small real crawl as an
 * opt-in demo corpus, or making the first hour legible. **This takes the
 * second**, for reasons that are not only effort:
 *
 * - A snapshot of a real crawl is third-party content, and whether it may be
 *   redistributed is the question §14.2 keeps separate from everything else.
 *   `MERIDIAN_SERVE_RAW` defaults to off for exactly that reason, and shipping
 *   a corpus in the repository would be answering the same question the other
 *   way without saying so.
 * - Synthetic fixtures are ruled out by the task itself: they do not resemble
 *   real extraction output, so the first impression would be of a system that
 *   works better than it does.
 *
 * So: a queue draining is a system working, and that is the honest thing to
 * show. Every number here is true of this machine right now.
 *
 * **Both halves of the fetch rate, always.** A crawl failing steadily and a
 * crawl succeeding steadily produce the same attempt count and want opposite
 * reactions — §12.5 makes the same argument about queue depth, which is why
 * the statuses are listed rather than summed.
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
    <section className="first-hour" aria-labelledby="first-hour-heading">
      <h2 id="first-hour-heading">Nothing to search yet</h2>

      {started ? (
        <p>
          The crawl is working through {pending.toLocaleString()} queued{' '}
          {pending === 1 ? 'page' : 'pages'}. Documents become searchable as they
          are fetched, extracted and chunked — the first ones usually within the
          hour.
        </p>
      ) : (
        <p>
          The queue is empty and nothing has been attempted. Add a cold-start
          seed in Admin, or the crawl has nowhere to begin.
        </p>
      )}

      <dl className="first-hour__queue">
        {QUEUE_ORDER.map((status) => [status, progress.queue[status] ?? 0] as const)
          .filter(([, count]) => count > 0)
          .map(([status, count]) => (
            <div key={status}>
              <dt>{status}</dt>
              <dd>{count.toLocaleString()}</dd>
            </div>
          ))}
      </dl>

      {progress.attempts_last_hour > 0 ? (
        <p className="first-hour__rate">
          {progress.successes_last_hour.toLocaleString()} of{' '}
          {progress.attempts_last_hour.toLocaleString()} fetches succeeded in the
          last hour.
          {progress.successes_last_hour === 0
            ? ' Every one failed — check Domains for what is being refused.'
            : ''}
        </p>
      ) : null}

      {progress.recent_domains.length > 0 ? (
        <>
          <h3>Most recently fetched</h3>
          {/* Names, not a count. "14 fetches" says less about whether this is
              working than one domain somebody recognises. */}
          <ul className="first-hour__domains">
            {progress.recent_domains.map((domain) => (
              <li key={domain}>{domain}</li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  )
}
