import { useState } from 'react'

/**
 * Saving the current search as a view (task P6-09, spec §12.5), offered only when there is
 * a search to save. A write, so refused while Admin is closed.
 * See docs/features/web-app.md#saved-views.
 */

export interface SaveViewProps {
  /** The query behind the current results. Empty means nothing to save. */
  query: string
  /** The filters that produced them, stored verbatim. */
  filters: Record<string, unknown>
  busy?: boolean
  error?: string | null
  onSave?: (name: string, query: string, filters: Record<string, unknown>) => void
}

export function suggestedName(query: string, filters: Record<string, unknown>): string {
  // The query, plus the topics if any narrowed it. A default that is merely the
  // query makes two views of the same words indistinguishable in the list, which
  // is the one thing the name has to prevent.
  const value = filters.topics ?? filters.topic
  const topics = Array.isArray(value) ? (value as string[]) : []
  return topics.length > 0 ? `${query} · ${topics.join(', ')}` : query
}

export function SaveView({ query, filters, busy = false, error = null, onSave }: SaveViewProps) {
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')

  if (!query.trim()) return null

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => {
          setName(suggestedName(query, filters))
          setOpen(true)
        }}
        className={CONTROL}
      >
        Save this view
      </button>
    )
  }

  return (
    <form
      className="flex flex-wrap items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        const trimmed = name.trim()
        if (!trimmed) return
        onSave?.(trimmed, query, filters)
      }}
    >
      <label className="flex items-center gap-2">
        <span className="sr-only">Name for this view</span>
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Name it"
          disabled={busy}
          className="h-8 w-64 border border-line-strong bg-surface px-2 text-[13px] text-text placeholder:text-text-faint focus:border-accent-graph/70 focus:outline-none"
        />
      </label>
      <button type="submit" disabled={busy || !name.trim()} className={CONTROL}>
        Save
      </button>
      <button type="button" onClick={() => setOpen(false)} disabled={busy} className={CONTROL}>
        Cancel
      </button>
      {error ? (
        // The API's own sentence. On an instance without Access it names the two
        // environment variables that would allow this, which is the only
        // actionable thing in the response.
        <span className="font-mono text-[10.5px] leading-[1.5] text-text-muted">{error}</span>
      ) : null}
    </form>
  )
}

/** §5's secondary control: `surface.raised` with a `line.strong` border. */
const CONTROL =
  'h-8 border border-line-strong bg-surface-raised px-3 font-sans text-[12.5px] ' +
  'text-text/85 hover:text-text disabled:opacity-50'
