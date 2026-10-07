import type { DomainStatus, FetchPolicyRow } from '../lib/api'
import { BUTTON_ROW, FIELD, Filters, PAGER, PageHeader, ROW, TD, TDM, TH, TableCard } from './ui'

/**
 * Per-domain fetch policy (task P6-22, spec §6.4, §13.2): what is set here, what the
 * domain resolves to, and what the crawl learned, kept apart. The safety guards are not
 * editable here. See docs/features/web-app.md#fetch-policy.
 */

export interface FetchPolicyPanelProps {
  rows: readonly FetchPolicyRow[]
  counts: { active: number; paused: number; blocked: number }
  status?: DomainStatus | null
  busy?: string | null
  onStatusFilter?: (status: DomainStatus | null) => void
  onUnblock?: (domain: string) => void
  onForgetRender?: (domain: string) => void
  /** Paging and search (`P6-28`): a long crawl leaves thousands of rows. */
  query?: string
  offset?: number
  hasMore?: boolean
  pageSize?: number
  onQuery?: (query: string) => void
  onPage?: (offset: number) => void
}

const FILTERS: { key: DomainStatus | null; label: string }[] = [
  { key: null, label: 'all' },
  { key: 'active', label: 'active' },
  { key: 'paused', label: 'paused' },
  { key: 'blocked', label: 'blocked' },
]

/** The settings worth showing at a glance; the rest are behind the resolved blob. */
export const SUMMARY_KEYS = ['delay_per_domain_ms', 'concurrency_per_domain', 'timeout_s', 'render_js'] as const

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
  query = '',
  offset = 0,
  hasMore = false,
  pageSize = 200,
  onQuery,
  onPage,
}: FetchPolicyPanelProps) {
  const shown: Record<string, number> = {
    all: counts.active + counts.paused + counts.blocked,
    active: counts.active,
    paused: counts.paused,
    blocked: counts.blocked,
  }

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Fetch policy">
        How the crawler behaves towards each site: how long it waits between requests, how many it makes at once, and
        whether it needs a browser. Robots handling, the address guards and the crawler's identity are set in the
        deployment and cannot be changed from here.
      </PageHeader>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <Filters
          options={FILTERS.map(({ key, label }) => ({ key, label, count: shown[label] }))}
          value={status}
          onChange={onStatusFilter}
        />
        <input
          type="search"
          className={`${FIELD} h-[30px] w-64 font-mono text-[12px]`}
          value={query}
          placeholder="find a domain"
          aria-label="Find a domain"
          onChange={(event) => onQuery?.(event.target.value)}
        />
        <div className="ml-auto flex items-center gap-2 font-mono text-[11px] text-text-faint">
          {/* An asterisk on values set on this row. Without it a reader cannot
              tell a domain somebody tuned from one inheriting the default. */}
          <span className="mr-3">* set on this row</span>
          <span>
            {rows.length === 0 ? 'none' : `${offset + 1}–${offset + rows.length}`}
            {query ? ' matching' : ` of ${shown[status ?? 'all']!.toLocaleString()}`}
          </span>
          <button
            type="button"
            className={PAGER}
            disabled={offset === 0}
            onClick={() => onPage?.(Math.max(offset - pageSize, 0))}
          >
            Previous
          </button>
          <button type="button" className={PAGER} disabled={!hasMore} onClick={() => onPage?.(offset + pageSize)}>
            Next
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
          No domains here. A row appears once the crawler has fetched from a site, or when somebody sets policy for one.
        </p>
      ) : (
        <TableCard>
          <thead>
            <tr>
              <th className={TH}>Domain</th>
              <th className={TH}>Status</th>
              {SUMMARY_KEYS.map((key) => (
                <th key={key} className={`${TH} text-right`}>
                  {key.replace(/_/g, ' ')}
                </th>
              ))}
              <th className={TH}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const blocked = row.policy.status === 'blocked'
              const learned = isLearnedRender(row)
              return (
                <tr key={row.policy.domain} className={ROW} data-domain={row.policy.domain}>
                  <td className={`${TDM} min-w-[200px]`}>
                    <div className="text-text">
                      {row.policy.domain === '*' ? 'every domain (default)' : row.policy.domain}
                    </div>
                    {blocked ? (
                      // §6.4 auto-blocks after consecutive failures, so this
                      // can appear without anybody choosing it — and a blocked
                      // domain produces no sources and no errors, which is why
                      // it is said rather than left to the status column.
                      <div className="font-sans text-[12px] text-accent-attention">
                        Not being crawled. {row.policy.consecutive_failures} failures in a row.
                      </div>
                    ) : null}
                    {learned ? (
                      <div className="font-sans text-[12px] text-text-muted">
                        The crawler worked out that this site needs a browser, after {row.policy.render_js_escalations}{' '}
                        pages in a row. It re-checks on its own after a week.
                      </div>
                    ) : null}
                    {row.policy.updated_by ? (
                      <div className="text-[10.5px] text-text-faint">changed by {row.policy.updated_by}</div>
                    ) : null}
                  </td>
                  <td className={`${TDM} whitespace-nowrap ${blocked ? 'text-accent-attention' : 'text-text-muted'}`}>
                    {row.policy.status}
                  </td>
                  {SUMMARY_KEYS.map((key) => (
                    <td
                      key={key}
                      className={`${TDM} whitespace-nowrap text-right ${
                        row.overridden.includes(key) ? 'text-text' : 'text-text-muted'
                      }`}
                    >
                      {String(row.resolved[key] ?? '—')}
                      {row.overridden.includes(key) ? '*' : ''}
                    </td>
                  ))}
                  <td className={`${TD} whitespace-nowrap text-right`}>
                    <span className="inline-flex gap-1.5">
                      {blocked ? (
                        <button
                          type="button"
                          disabled={busy === row.policy.domain}
                          onClick={() => onUnblock?.(row.policy.domain)}
                          className={BUTTON_ROW}
                        >
                          Crawl it again
                        </button>
                      ) : null}
                      {learned ? (
                        <button
                          type="button"
                          disabled={busy === row.policy.domain}
                          onClick={() => onForgetRender?.(row.policy.domain)}
                          className={BUTTON_ROW}
                        >
                          Check again now
                        </button>
                      ) : null}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </TableCard>
      )}
    </section>
  )
}
