import type { CrawlHealth, HourBucket, Liveness, LivenessState } from '../lib/api'

/**
 * Crawl health (task P6-25, spec §12.5, §13.4).
 *
 * The first hour has `FirstHour`: one line saying the queue is draining. A run
 * left alone for days needs something else — not "is it doing anything" but
 * **when did it stop, and why**. So this screen leads with a verdict in words,
 * and everything under it is the evidence for that verdict.
 *
 * **The verdict is the server's.** `crawlhealth.judge` decides the state from
 * the last attempt and the queue; this file only puts it into a sentence. A
 * client that re-derived "stalled" from the numbers would one day disagree with
 * the alert that fired about the same crawl, and then neither would be trusted.
 *
 * **A day, not an hour, and empty hours drawn.** The thing being looked for is
 * a gap — the hour the fetching stopped — and a chart that skipped empty hours
 * would close exactly that gap up.
 *
 * **No chart library.** Twenty-four stacked bars are a few rectangles, and the
 * package has deliberately kept its dependencies to React.
 */

export interface CrawlHealthPanelProps {
  health: CrawlHealth
}

/** Seconds as the unit somebody would say out loud. Rounded down: "no fetch in
 * 59 min" must not read as an hour it has not yet been. */
export function duration(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  if (minutes < 1) return 'under a minute'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`
  return `${Math.floor(hours / 24)} days`
}

function pages(n: number): string {
  return `${n.toLocaleString()} ${n === 1 ? 'page' : 'pages'}`
}

/** The headline word per state. Keyed by the full union, so a state added to
 * `LivenessState` and not given a word here fails to compile. */
export const STATE_LABELS: Record<LivenessState, string> = {
  crawling: 'Crawling',
  stalled: 'Stalled',
  waiting: 'Waiting',
  idle: 'Idle',
}

/** The verdict as one sentence, with the numbers that make it actionable. */
export function verdict(live: Liveness, stallAfterSeconds: number): string {
  const since =
    live.quiet_seconds === null ? null : `the last fetch was ${duration(live.quiet_seconds)} ago`

  switch (live.state) {
    case 'crawling':
      return live.pending === 0
        ? `Crawling — ${since}. Nothing is left pending, so this goes idle once the rows in flight finish.`
        : `Crawling — ${since}, with ${pages(live.pending)} pending.`
    case 'stalled':
      // The one worth acting on, so it says what to check. The threshold is
      // named because "stalled" without it reads as a judgement nobody made.
      return live.quiet_seconds === null
        ? `Stalled — nothing has ever been fetched, with ${pages(live.ready)} ready. Check that the worker is running.`
        : `Stalled — no fetch in ${duration(live.quiet_seconds)} with ${pages(live.ready)} ready. ` +
            `A fetch is expected at least every ${duration(stallAfterSeconds)} while work is ready; check that the worker is running.`
    case 'waiting':
      // Not a stall, and says why: every pending row is inside its backoff,
      // which is the queue doing its job.
      return `Waiting — ${pages(live.pending)} pending, all backing off after failed attempts${since ? `; ${since}` : ''}.`
    case 'idle':
      return `Idle — the queue is empty${since ? ` and ${since}` : ' and nothing has ever been fetched'}. Seed more, or check that expansion is enqueueing.`
  }
}

// --------------------------------------------------------------------------
// The day, drawn

const BAR_STEP = 10
const BAR_WIDTH = 8
const CHART_HEIGHT = 80
/** The surface gap between the two stacked segments, so they read as two
 * marks rather than one bar in two colours. */
const SEGMENT_GAP = 2

/** Which hour a bucket is, for its hover title. Counted back from `as_of`
 * rather than printed as a clock time, because the buckets are rolling hours
 * ending now and "14:00" would imply they are aligned to the clock. */
export function bucketLabel(index: number, total: number): string {
  const ago = total - index
  return ago === 1 ? 'Last hour' : `${ago - 1}–${ago} h ago`
}

export function HourlyChart({ hours }: { hours: readonly HourBucket[] }) {
  const peak = Math.max(1, ...hours.map((b) => b.succeeded + b.failed))
  // Less the gap, so the tallest stacked bar still fits the frame.
  const scale = (n: number) => (n / peak) * (CHART_HEIGHT - SEGMENT_GAP)
  const width = hours.length * BAR_STEP
  const total = hours.reduce((sum, b) => sum + b.succeeded + b.failed, 0)

  return (
    <figure className="mt-3">
      <svg
        viewBox={`0 0 ${width} ${CHART_HEIGHT}`}
        preserveAspectRatio="none"
        className="block h-24 w-full"
        role="img"
        aria-label={`Fetch attempts per hour over the last ${hours.length} hours, ${total.toLocaleString()} in all`}
      >
        {/* Baseline, so an empty hour is visibly an hour and not missing. */}
        <line
          x1={0}
          x2={width}
          y1={CHART_HEIGHT - 0.5}
          y2={CHART_HEIGHT - 0.5}
          className="stroke-line"
          strokeWidth={1}
        />
        {hours.map((bucket, i) => {
          const ok = scale(bucket.succeeded)
          const bad = scale(bucket.failed)
          const gap = ok > 0 && bad > 0 ? SEGMENT_GAP : 0
          const x = i * BAR_STEP + (BAR_STEP - BAR_WIDTH) / 2
          return (
            <g key={bucket.start} data-bucket={i}>
              <title>
                {`${bucketLabel(i, hours.length)}: ${bucket.succeeded.toLocaleString()} succeeded, ${bucket.failed.toLocaleString()} failed`}
              </title>
              {/* A full-height hit target, so an empty hour still answers on hover. */}
              <rect x={i * BAR_STEP} y={0} width={BAR_STEP} height={CHART_HEIGHT} fill="transparent" />
              {ok > 0 ? (
                <rect
                  data-part="succeeded"
                  x={x}
                  y={CHART_HEIGHT - ok}
                  width={BAR_WIDTH}
                  height={ok}
                  className="fill-accent-graph"
                />
              ) : null}
              {bad > 0 ? (
                <rect
                  data-part="failed"
                  x={x}
                  y={CHART_HEIGHT - ok - gap - bad}
                  width={BAR_WIDTH}
                  height={bad}
                  className="fill-accent-attention"
                />
              ) : null}
            </g>
          )
        })}
      </svg>
      <figcaption className="mt-1 flex justify-between font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
        <span>{hours.length} h ago</span>
        <span className="flex gap-4">
          <span>
            <span aria-hidden="true" className="mr-1 inline-block h-2 w-2 bg-accent-graph" />
            succeeded
          </span>
          <span>
            <span aria-hidden="true" className="mr-1 inline-block h-2 w-2 bg-accent-attention" />
            failed
          </span>
        </span>
        <span>now</span>
      </figcaption>
    </figure>
  )
}

// --------------------------------------------------------------------------
// The panel

const heading =
  'mb-2 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted'

export function CrawlHealthPanel({ health }: CrawlHealthPanelProps) {
  const { liveness } = health
  const attempted = health.hours.reduce((sum, b) => sum + b.succeeded + b.failed, 0)
  const outcomes = health.outcomes.filter((row) => row.count > 0)
  const alarm = liveness.state === 'stalled'

  return (
    <section>
      <h2 className={heading}>Crawl health</h2>

      <div
        data-state={liveness.state}
        className={`border bg-surface p-4 ${alarm ? 'border-accent-attention' : 'border-line'}`}
      >
        <p
          className={`font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
            alarm ? 'text-accent-attention' : 'text-text-muted'
          }`}
        >
          {STATE_LABELS[liveness.state]}
        </p>
        <p className="mt-1 text-[length:var(--text-body)] text-text">
          {verdict(liveness, health.stall_after_seconds)}
        </p>
      </div>

      <p className="mt-2 text-[length:var(--text-small)] text-text-muted">
        As of {health.as_of.slice(11, 16)} UTC. Refreshes every 30 seconds while this is open.
      </p>

      <h3 className={`mt-8 ${heading}`}>Fetches per hour, last 24 h</h3>
      {attempted === 0 ? (
        <p className="text-[length:var(--text-small)] text-text-muted">
          No fetch attempts in the last 24 hours.
        </p>
      ) : null}
      <HourlyChart hours={health.hours} />

      <div className="mt-8 grid gap-8 sm:grid-cols-2">
        <div>
          <h3 className={heading}>Outcomes, last 24 h</h3>
          {outcomes.length === 0 ? (
            <p className="text-[length:var(--text-small)] text-text-muted">None recorded.</p>
          ) : (
            <ul className="space-y-2">
              {outcomes.map((row) => (
                <li key={row.outcome}>
                  <div className="flex justify-between gap-2 text-[length:var(--text-small)]">
                    <span className="font-mono text-text">{row.outcome}</span>
                    <span className="font-mono text-text-muted">{row.count.toLocaleString()}</span>
                  </div>
                  {/* Share of the day, drawn. One hue: the outcome is named
                      beside it, so colour carries no verdict. */}
                  <div className="mt-1 h-1.5 w-full bg-surface-raised">
                    <div
                      className="h-full bg-accent-graph"
                      style={{ width: `${(row.count / attempted) * 100}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div>
          <h3 className={heading}>Queue</h3>
          <dl className="space-y-1 text-[length:var(--text-small)]">
            {Object.entries(health.queue).map(([status, count]) => (
              <div key={status} className="flex justify-between gap-2">
                <dt className="font-mono text-text">{status}</dt>
                <dd className="font-mono text-text-muted">{count.toLocaleString()}</dd>
              </div>
            ))}
            {/* Beside the queue because it is one: the crawl can be healthy
                while none of what it fetches becomes searchable. */}
            <div className="flex justify-between gap-2 border-t border-line pt-1">
              <dt className="font-mono text-text">awaiting embedding</dt>
              <dd className="font-mono text-text-muted">
                {health.embedding_backlog.toLocaleString()}
              </dd>
            </div>
          </dl>
        </div>
      </div>

      <h3 className={`mt-8 ${heading}`}>Busiest domains, last hour</h3>
      {health.top_domains.length === 0 ? (
        <p className="text-[length:var(--text-small)] text-text-muted">
          Nothing fetched in the last hour.
        </p>
      ) : (
        <ul className="space-y-1 text-[length:var(--text-small)]">
          {health.top_domains.map((row) => (
            <li key={row.domain} className="flex justify-between gap-2">
              <span className="font-mono text-text">{row.domain}</span>
              <span className="font-mono text-text-muted">
                {row.succeeded.toLocaleString()} of {row.attempts.toLocaleString()} succeeded
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
