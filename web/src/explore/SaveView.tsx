import { useState } from 'react'

/**
 * Saving the current search as a view (task P6-09, spec §12.5).
 *
 * §12.5 asks for "a filter set plus focus node, named and re-openable", and the
 * affordance has one job beyond the obvious: **it has to be offered at the
 * moment the view is worth saving**, which is while looking at results, not from
 * a menu somewhere. A saved-views feature nobody reaches is the same as not
 * having one — the same argument §12.5 makes for annotations.
 *
 * It appears only when there is something to save. An empty search box has no
 * view behind it, and a disabled control that is always on screen teaches the
 * reader to stop seeing it.
 *
 * **Saving is a write**, so on an instance without Cloudflare Access it is
 * refused — the API says so and the message is shown as written. That is
 * deliberate (`P6-13`): views are shared state with no per-viewer scoping.
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
  const topics = Array.isArray(filters.topic) ? (filters.topic as string[]) : []
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
          className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-2 text-[length:var(--text-small)]"
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
        <span className="text-[length:var(--text-small)] text-accent-attention">{error}</span>
      ) : null}
    </form>
  )
}

const CONTROL =
  'h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 ' +
  'font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ' +
  'disabled:opacity-50'
