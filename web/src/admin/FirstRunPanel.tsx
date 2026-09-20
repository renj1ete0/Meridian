import { useState } from 'react'

import type { FirstRun, QueueTask } from '../lib/api'

/**
 * The first run (task B-07, scaffold §1.7, spec §15 phase 0, §16).
 *
 * §16 lists cold-start seed quality as a real risk — "worth spending an evening
 * on" — and until now that evening had to be spent editing
 * `config/seed_sources.yaml` *before* the first boot, because the file is read
 * once and never again (§13.1). Somebody installing Meridian to find out what
 * it does has no idea yet what belongs in it.
 *
 * **This is not a wizard, and it does not gate anything.** By the time anyone
 * opens it the crawl has started — `make quickstart` brings the worker up with
 * everything else. A screen that implied otherwise would invite someone to
 * remove a seed that has already been fetched and then wonder why the document
 * is still there. What it offers instead is the window between a seed being
 * queued and being reached, which per-domain rate limiting makes generous.
 *
 * So both halves are shown: what is still changeable, and what is already
 * underway. The second is not an error state and is not styled as one.
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
  return task.task_type === 'query'
    ? `search: ${task.url_or_query}`
    : task.url_or_query
}

export function FirstRunPanel({
  run,
  busy = null,
  adding = false,
  error = null,
  onAdd,
  onRemove,
}: FirstRunPanelProps) {
  const [draft, setDraft] = useState('')
  const [type, setType] = useState<'url' | 'query'>('url')
  const [topic, setTopic] = useState('')

  const submit = () => {
    const trimmed = draft.trim()
    if (!trimmed) return
    onAdd?.(trimmed, type, topic.trim() || null)
    setDraft('')
  }

  return (
    <section className="first-run" aria-labelledby="first-run-heading">
      <h2 id="first-run-heading">Cold-start seeds</h2>

      {run.is_first_run ? (
        <p className="first-run__lede">
          Nothing has been crawled yet. These are the pages and searches the
          crawl starts from — everything else is reached by following links,
          sitemaps and citations out of them. Seed quality propagates, so this
          is the one list worth thinking about.
        </p>
      ) : (
        <p className="first-run__lede">
          {run.sources.toLocaleString()} documents so far. Seeds still waiting
          can be changed; the crawl has moved past the rest.
        </p>
      )}

      {error ? <p role="alert">{error}</p> : null}

      <form
        className="first-run__add"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <label>
          <span>Add a seed</span>
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={type === 'url' ? 'https://example.gov/' : 'a search to run'}
            aria-label="Seed URL or search"
          />
        </label>
        <label>
          <span>Kind</span>
          <select
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
        <label>
          <span>Topic</span>
          <input
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
            placeholder="optional"
            aria-label="Seed topic"
          />
        </label>
        <button type="submit" disabled={adding || !draft.trim()}>
          {adding ? 'Adding…' : 'Add'}
        </button>
      </form>

      {run.pending_seeds.length === 0 ? (
        <p className="first-run__empty">
          No seeds are waiting. {run.seeds_in_flight > 0
            ? 'They have all been reached.'
            : 'Add one above, or the crawl has nowhere to start.'}
        </p>
      ) : (
        <ul className="first-run__seeds">
          {run.pending_seeds.map((seed) => (
            <li key={seed.task_id}>
              <span className="first-run__what">{describeSeed(seed)}</span>
              {seed.topic ? <span className="first-run__topic">{seed.topic}</span> : null}
              <button
                type="button"
                onClick={() => onRemove?.(seed.task_id)}
                disabled={busy === seed.task_id}
                aria-label={`Remove ${seed.url_or_query}`}
              >
                {busy === seed.task_id ? '…' : 'Remove'}
              </button>
            </li>
          ))}
        </ul>
      )}

      {run.seeds_in_flight > 0 ? (
        <p className="first-run__inflight">
          {run.seeds_in_flight.toLocaleString()} already reached. Those cannot be
          removed — they have produced fetch attempts, and dropping the queue row
          would leave that evidence with nothing explaining where it came from.
          Block the domain in Domains to stop it going further.
        </p>
      ) : null}
    </section>
  )
}
