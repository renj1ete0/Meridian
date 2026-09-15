import type { DomainStatus, FetchPolicyRow } from '../lib/api'

/**
 * Per-domain fetch policy (task P6-22, spec §6.4, §13.2).
 *
 * The one admin screen whose changes reach somebody else's server, and the copy
 * is written on that basis: these are decisions about how a machine behaves
 * towards a stranger's infrastructure, not preferences.
 *
 * **Three layers, kept visibly separate.** What is set *here*, what the domain
 * *resolves* to once the global row and the file defaults merge under it, and
 * what the crawl *learned* by watching. An operator looking at a domain going
 * through a browser needs to know which of those put it there, because only one
 * of them is something they can change on this screen — and the learned one has
 * its own button, because clearing an observation and setting a policy are
 * different acts.
 *
 * **The safety guards are not here at all**, and their absence is stated rather
 * than left to be noticed. `block_private_addresses`, `respect_robots` and the
 * rest are deployment settings; a form that could switch one off would be one
 * click away from controls about politeness.
 */

export interface FetchPolicyPanelProps {
  rows: readonly FetchPolicyRow[]
  counts: { active: number; paused: number; blocked: number }
  status?: DomainStatus | null
  busy?: string | null
  onStatusFilter?: (status: DomainStatus | null) => void
  onUnblock?: (domain: string) => void
  onForgetRender?: (domain: string) => void
}

const FILTERS: { key: DomainStatus | null; label: string }[] = [
  { key: null, label: 'all' },
  { key: 'active', label: 'active' },
  { key: 'paused', label: 'paused' },
  { key: 'blocked', label: 'blocked' },
]

/** The settings worth showing at a glance; the rest are behind the resolved blob. */
export const SUMMARY_KEYS = [
  'delay_per_domain_ms',
  'concurrency_per_domain',
  'timeout_s',
  'render_js',
] as const

export function isLearnedRender(row: FetchPolicyRow): boolean {
  // Resolved says `always` and nothing on this row says so: the crawl worked it
  // out. Without this distinction an operator sees a setting they cannot find
  // anywhere to change.
  return (
    row.resolved.render_js === 'always' &&
    !row.overridden.includes('render_js') &&
    row.policy.render_js_learned_at !== null
  )
}

export function FetchPolicyPanel({
  rows,
  counts,
  status = null,
  busy = null,
  onStatusFilter,
  onUnblock,
  onForgetRender,
}: FetchPolicyPanelProps) {
  const shown: Record<string, number> = {
    all: counts.active + counts.paused + counts.blocked,
    active: counts.active,
    paused: counts.paused,
    blocked: counts.blocked,
  }

  return (
    <section>
      <h2 className="font-sans text-[length:var(--text-heading)] font-semibold">Domains</h2>
      <p className="mt-2 max-w-prose text-[length:var(--text-small)] text-text-muted">
        How the crawler behaves towards each site: how long it waits between requests, how many it
        makes at once, and whether it needs a browser. Robots handling, the address guards and the
        crawler's identity are set in the deployment and cannot be changed from here.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        {FILTERS.map(({ key, label }) => (
          <button
            key={label}
            type="button"
            onClick={() => onStatusFilter?.(key)}
            aria-pressed={status === key}
            className={`rounded-chip border px-2 py-0.5 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
              status === key
                ? 'border-accent-graph text-accent-graph'
                : 'border-line-strong text-text-muted'
            }`}
          >
            {label} ({shown[label]})
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <p className="mt-6 text-text-muted">
          No domains here. A row appears once the crawler has fetched from a site, or when somebody
          sets policy for one.
        </p>
      ) : (
        <ul className="mt-6 space-y-4">
          {rows.map((row) => (
            <li key={row.policy.domain} className="border border-line bg-surface p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-mono text-[length:var(--text-body)] font-semibold">
                  {row.policy.domain === '*' ? 'every domain (default)' : row.policy.domain}
                </h3>
                <span
                  className={`font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
                    row.policy.status === 'blocked' ? 'text-accent-attention' : 'text-text-muted'
                  }`}
                >
                  {row.policy.status}
                </span>
              </div>

              <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-1 font-mono text-[length:var(--text-small)] text-text-muted">
                {SUMMARY_KEYS.map((key) => (
                  <div key={key} className="flex gap-1">
                    <dt>{key.replace(/_/g, ' ')}</dt>
                    <dd className={row.overridden.includes(key) ? 'text-text' : ''}>
                      {String(row.resolved[key] ?? '—')}
                      {/* An asterisk on values set on this row. Without it a
                          reader cannot tell a domain somebody tuned from one
                          inheriting the default. */}
                      {row.overridden.includes(key) ? '*' : ''}
                    </dd>
                  </div>
                ))}
              </dl>

              {isLearnedRender(row) ? (
                <p className="mt-3 text-[length:var(--text-small)] text-text-muted">
                  The crawler worked out that this site needs a browser, after{' '}
                  {row.policy.render_js_escalations} pages in a row. It re-checks on its own after a
                  week.
                </p>
              ) : null}

              {row.policy.status === 'blocked' ? (
                <p className="mt-3 text-[length:var(--text-small)] text-accent-attention">
                  {/* §6.4 auto-blocks after consecutive failures, so this can
                      appear without anybody choosing it — and a blocked domain
                      produces no sources and no errors, which is why it is said
                      rather than left to the status chip. */}
                  Not being crawled. {row.policy.consecutive_failures} failures in a row.
                </p>
              ) : null}

              <div className="mt-4 flex flex-wrap items-center gap-3">
                {row.policy.status === 'blocked' ? (
                  <button
                    type="button"
                    disabled={busy === row.policy.domain}
                    onClick={() => onUnblock?.(row.policy.domain)}
                    className={ACTION}
                  >
                    Crawl it again
                  </button>
                ) : null}
                {isLearnedRender(row) ? (
                  <button
                    type="button"
                    disabled={busy === row.policy.domain}
                    onClick={() => onForgetRender?.(row.policy.domain)}
                    className={ACTION}
                  >
                    Check again now
                  </button>
                ) : null}
                {row.policy.updated_by ? (
                  <span className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                    changed by {row.policy.updated_by}
                  </span>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

const ACTION =
  'h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 ' +
  'font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ' +
  'disabled:opacity-50'
