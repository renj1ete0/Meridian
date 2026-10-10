import type { CrawlHealth, HourBucket, Liveness, LivenessState } from '../lib/api'
import { Card, LABEL, PageHeader, ROW, TD, TDM, TH, TableCard } from './ui'
import { onInternalClick } from '../lib/route'
import { clockOf, zoneLabel } from '../lib/time'

/**
 * Crawl health (task P6-25, spec §12.5, §13.4): the server's verdict in words, then the
 * evidence, over a day with empty hours drawn. See docs/features/web-app.md#crawl-health.
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

/**
 * Fetch outcomes in words (`B-197`), keyed by the database's values: the raw enum
 * (`robots_unreachable`) is the corpus's bookkeeping, and stays in the row's title.
 */
export const OUTCOME_WORDS: Record<string, string> = {
  success: 'fetched',
  not_modified: 'unchanged since last fetch',
  http_error: 'the site answered with an error',
  timeout: 'timed out',
  too_large: 'too large to fetch',
  robots_denied: 'refused by the site’s robots.txt',
  robots_unreachable: 'robots.txt could not be read',
  blocked: 'domain blocked by policy',
  connection_error: 'could not connect',
  parse_error: 'fetched but could not be read',
  unsafe_target: 'refused: unsafe address',
  content_type_rejected: 'not a kind of document kept',
  decompression_bomb: 'refused: expands too far',
  too_many_redirects: 'too many redirects',
}

/** Queue states in words, keyed by the database's values. */
export const STATUS_WORDS: Record<string, string> = {
  pending: 'waiting to be fetched',
  fetched: 'fetched, being read',
  extracted: 'read, being stored',
  embedded: 'stored',
  done: 'done',
  failed: 'failed',
  rejected_duplicate: 'skipped as copies',
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
  const since = live.quiet_seconds === null ? null : `the last fetch was ${duration(live.quiet_seconds)} ago`

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

export function CrawlHealthPanel({ health }: CrawlHealthPanelProps) {
  const { liveness } = health
  const attempted = health.hours.reduce((sum, b) => sum + b.succeeded + b.failed, 0)
  const outcomes = health.outcomes.filter((row) => row.count > 0)
  const alarm = liveness.state === 'stalled'

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Crawl health">
        As of {clockOf(health.as_of)} {zoneLabel(health.as_of)}. Refreshes every 30 seconds while this is open.
      </PageHeader>

      <div
        data-state={liveness.state}
        className={`border bg-surface px-[18px] py-3.5 ${alarm ? 'border-accent-attention' : 'border-line'}`}
      >
        <p
          className={`font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] ${
            alarm ? 'text-accent-attention' : 'text-text-faint'
          }`}
        >
          {STATE_LABELS[liveness.state]}
        </p>
        <p className="mt-1 text-[14px] text-text">{verdict(liveness, health.stall_after_seconds)}</p>
      </div>

      <Card className="px-[18px] py-3.5">
        <h2 className={LABEL}>Fetches per hour, last 24 h</h2>
        {attempted === 0 ? (
          <p className="mt-2 text-[12.5px] text-text-muted">No fetch attempts in the last 24 hours.</p>
        ) : null}
        <HourlyChart hours={health.hours} />
      </Card>

      <div className="grid items-start gap-5 md:grid-cols-2">
        <TableCard>
          <thead>
            <tr>
              <th className={`${TH} w-full`}>Outcomes, last 24 h</th>
              <th className={`${TH} text-right`}>Count</th>
            </tr>
          </thead>
          <tbody>
            {outcomes.length === 0 ? (
              <tr className={ROW}>
                <td colSpan={2} className={`${TD} text-text-muted`}>
                  None recorded.
                </td>
              </tr>
            ) : (
              outcomes.map((row) => (
                <tr key={row.outcome} className={ROW}>
                  <td className={TDM} title={row.outcome}>
                    {OUTCOME_WORDS[row.outcome] ?? row.outcome}
                    {/* Share of the day, drawn. One hue: the outcome is named
                        beside it, so colour carries no verdict. */}
                    <div className="mt-1 h-1 w-full bg-surface-raised">
                      <div className="h-full bg-accent-graph" style={{ width: `${(row.count / attempted) * 100}%` }} />
                    </div>
                  </td>
                  <td className={`${TDM} text-right text-text-muted`}>{row.count.toLocaleString()}</td>
                </tr>
              ))
            )}
          </tbody>
        </TableCard>

        <TableCard>
          <thead>
            <tr>
              <th className={`${TH} w-full`}>Queue</th>
              <th className={`${TH} text-right`}>Rows</th>
            </tr>
          </thead>
          <tbody>
            {/* States with nothing in them are left out: rows that read 0 every day are noise. */}
            {Object.entries(health.queue)
              .filter(([, count]) => count > 0)
              .map(([status, count]) => (
                <tr key={status} className={ROW}>
                  <td className={TDM} title={status}>
                    {STATUS_WORDS[status] ?? status}
                  </td>
                  <td className={`${TDM} text-right text-text-muted`}>{count.toLocaleString()}</td>
                </tr>
              ))}
            {/* Beside the queue because it is one: the crawl can be healthy
                while none of what it fetches becomes searchable. */}
            <tr className="border-t border-line">
              <td className={TDM}>awaiting embedding</td>
              <td className={`${TDM} text-right text-text-muted`}>{health.embedding_backlog.toLocaleString()}</td>
            </tr>
          </tbody>
        </TableCard>
      </div>

      <TableCard>
        <thead>
          <tr>
            <th className={`${TH} w-full`}>Busiest domains, last hour</th>
            <th className={`${TH} text-right`}>Succeeded</th>
          </tr>
        </thead>
        <tbody>
          {health.top_domains.length === 0 ? (
            <tr className={ROW}>
              <td colSpan={2} className={`${TD} text-text-muted`}>
                Nothing fetched in the last hour.
              </td>
            </tr>
          ) : (
            health.top_domains.map((row) => (
              <tr key={row.domain} className={ROW}>
                <td className={TDM}>
                  {/* To the domain's own policy row: what to change when it keeps failing (`B-197`). */}
                  <a
                    href={`/admin/fetch-policy?q=${encodeURIComponent(row.domain)}`}
                    onClick={onInternalClick(`/admin/fetch-policy?q=${encodeURIComponent(row.domain)}`)}
                    className="hover:text-accent-graph hover:underline"
                  >
                    {row.domain}
                  </a>
                </td>
                <td className={`${TDM} whitespace-nowrap text-right text-text-muted`}>
                  {row.succeeded.toLocaleString()} of {row.attempts.toLocaleString()} succeeded
                </td>
              </tr>
            ))
          )}
        </tbody>
      </TableCard>
    </section>
  )
}
