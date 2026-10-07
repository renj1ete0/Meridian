import { useState } from 'react'

import type { FirstRun, QueueTask } from '../lib/api'
import { BUTTON_PRIMARY, BUTTON_ROW, FIELD, ROW, TD, TDM, TH, TableCard, stamp } from './ui'

/**
 * The first run (task B-07, scaffold §1.7, spec §15 phase 0, §16): the seeds still
 * changeable and those already underway. Not a wizard; it gates nothing.
 * See docs/features/web-app.md#the-first-run.
 */

export interface FirstRunPanelProps {
  run: FirstRun
  busy?: number | null
  adding?: boolean
  error?: string | null
  onAdd?: (url: string, type: 'url' | 'query', topic: string | null) => void
  onRemove?: (taskId: number) => void
}

/** What a seed is, in the words the queue uses for it. */
export function describeSeed(task: QueueTask): string {
  return task.task_type === 'query' ? `search: ${task.url_or_query}` : task.url_or_query
}

export function FirstRunPanel({ run, busy = null, adding = false, error = null, onAdd, onRemove }: FirstRunPanelProps) {
  const [draft, setDraft] = useState('')
  const [type, setType] = useState<'url' | 'query'>('url')
  const [topic, setTopic] = useState('')

  const submit = () => {
    const trimmed = draft.trim()
    if (!trimmed) return
    onAdd?.(trimmed, type, topic.trim() || null)
    setDraft('')
  }

  const small = 'font-mono text-[9px] uppercase tracking-[var(--tracking-label)] text-text-faint'

  return (
    <section className="flex flex-col gap-5" aria-labelledby="first-run-heading">
      <header className="flex flex-col gap-1.5">
        <h1
          id="first-run-heading"
          className="text-[25px] font-semibold leading-[1.15] tracking-[var(--tracking-display)] text-text"
        >
          Cold-start seeds
        </h1>
        <p className="max-w-[74ch] text-[13px] leading-[1.55] text-text-muted">
          {run.is_first_run
            ? 'Nothing has been crawled yet. These are the pages and searches the crawl starts from — everything else is reached by following links, sitemaps and citations out of them. Seed quality propagates, so this is the one list worth thinking about.'
            : `${run.sources.toLocaleString()} documents so far. Seeds still waiting can be changed; the crawl has moved past the rest.`}
        </p>
      </header>

      {error ? (
        <p role="alert" className="text-[12.5px] text-accent-attention">
          {error}
        </p>
      ) : null}

      <form
        className="flex flex-wrap items-end gap-3 border border-line bg-surface px-[18px] py-3.5"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <label className="flex min-w-[240px] flex-1 flex-col gap-1.5">
          <span className={small}>Add a seed</span>
          <input
            className={`${FIELD} font-mono text-[12px]`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={type === 'url' ? 'https://example.gov/' : 'a search to run'}
            aria-label="Seed URL or search"
          />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className={small}>Kind</span>
          <select
            className={`${FIELD} text-[12.5px]`}
            value={type}
            onChange={(event) => setType(event.target.value as 'url' | 'query')}
            aria-label="Seed kind"
          >
            {/* A site root, not a deep link: §6.4 prefers letting discovery
                work outward from an organisation's front door, which rots far
                less than a hand-copied path. */}
            <option value="url">a page to crawl</option>
            <option value="query">a search to run</option>
          </select>
        </label>
        <label className="flex w-40 flex-col gap-1.5">
          <span className={small}>Topic</span>
          <input
            className={`${FIELD} font-mono text-[12px]`}
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="optional"
            aria-label="Seed topic"
          />
        </label>
        <button type="submit" className={BUTTON_PRIMARY} disabled={adding || !draft.trim()}>
          {adding ? 'Adding…' : 'Add'}
        </button>
      </form>

      {run.pending_seeds.length === 0 ? (
        <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
          No seeds are waiting.{' '}
          {run.seeds_in_flight > 0
            ? 'They have all been reached.'
            : 'Add one above, or the crawl has nowhere to start.'}
        </p>
      ) : (
        <TableCard>
          <thead>
            <tr>
              <th className={`${TH} w-full`}>Waiting · {run.pending_seeds.length.toLocaleString()}</th>
              <th className={TH}>Topic</th>
              <th className={TH}>Queued</th>
              <th className={TH}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {run.pending_seeds.map((seed) => (
              <tr key={seed.task_id} className={ROW} data-seed={seed.task_id}>
                <td className={`${TDM} break-all`}>{describeSeed(seed)}</td>
                <td className={`${TDM} whitespace-nowrap text-text-muted`}>{seed.topic ?? '—'}</td>
                <td className={`${TDM} whitespace-nowrap text-text-muted`}>{stamp(seed.created_at)}</td>
                <td className={`${TD} text-right`}>
                  <button
                    type="button"
                    className={BUTTON_ROW}
                    onClick={() => onRemove?.(seed.task_id)}
                    disabled={busy === seed.task_id}
                    aria-label={`Remove ${seed.url_or_query}`}
                  >
                    {busy === seed.task_id ? '…' : 'Remove'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}

      {run.seeds_in_flight > 0 ? (
        <p className="max-w-[74ch] text-[12.5px] leading-[1.55] text-text-muted">
          {run.seeds_in_flight.toLocaleString()} already reached. Those cannot be removed — they have produced fetch
          attempts, and dropping the queue row would leave that evidence with nothing explaining where it came from.
          Block the domain in Fetch policy to stop it going further.
        </p>
      ) : null}
    </section>
  )
}
