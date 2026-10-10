import { useState } from 'react'

import { ApiError, type SavedViewRecord } from '../lib/api'

/**
 * Every saved view, renamed or deleted in place (`B-180`). The landing lists six; the rest, and
 * any way to tidy them, had nowhere to be. A view is a bookmark, so deleting one touches nothing
 * in the corpus; it still asks once, since the name and filters are the reader's own work.
 * See docs/features/web-app.md#saved-views.
 */
export interface ManageViewsProps {
  views: readonly SavedViewRecord[]
  onOpen: (view: SavedViewRecord) => void
  onRename: (view: SavedViewRecord, name: string) => Promise<void>
  onDelete: (view: SavedViewRecord) => Promise<void>
  onDone: () => void
}

const LINK = 'font-mono text-[10.5px] text-accent-graph hover:underline disabled:opacity-50'

function Row({
  view,
  onOpen,
  onRename,
  onDelete,
}: {
  view: SavedViewRecord
  onOpen: () => void
  onRename: (name: string) => Promise<void>
  onDelete: () => Promise<void>
}) {
  const [mode, setMode] = useState<'idle' | 'renaming' | 'deleting'>('idle')
  const [name, setName] = useState(view.name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function act(work: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await work()
      setMode('idle')
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'That did not go through.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="flex flex-col gap-1.5 py-[9px]">
      {mode === 'renaming' ? (
        <form
          className="flex flex-wrap items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            const trimmed = name.trim()
            if (trimmed && trimmed !== view.name) void act(() => onRename(trimmed))
            else setMode('idle')
          }}
        >
          <input
            aria-label={`New name for ${view.name}`}
            value={name}
            maxLength={120}
            autoFocus
            onChange={(event) => setName(event.target.value)}
            className="h-8 min-w-0 flex-1 border border-line-strong bg-surface-raised px-2 text-[13px] text-text"
          />
          <button type="submit" disabled={busy || !name.trim()} className={LINK}>
            save
          </button>
          <button type="button" onClick={() => setMode('idle')} className={LINK}>
            cancel
          </button>
        </form>
      ) : (
        <div className="flex items-baseline gap-3">
          <button
            type="button"
            onClick={onOpen}
            className="min-w-0 grow truncate text-left text-[13.5px] text-text/85 hover:text-accent-graph"
          >
            {view.name}
          </button>
          {mode === 'deleting' ? (
            <span className="flex items-baseline gap-2 font-mono text-[10.5px] text-text-muted">
              delete it?
              <button type="button" disabled={busy} onClick={() => void act(onDelete)} className={LINK}>
                yes
              </button>
              <button type="button" onClick={() => setMode('idle')} className={LINK}>
                no
              </button>
            </span>
          ) : (
            <span className="flex shrink-0 gap-3">
              <button type="button" onClick={() => setMode('renaming')} className={LINK}>
                rename
              </button>
              <button type="button" onClick={() => setMode('deleting')} className={LINK}>
                delete
              </button>
            </span>
          )}
        </div>
      )}
      {error ? <p className="font-mono text-[10.5px] text-text-muted">{error}</p> : null}
    </li>
  )
}

export function ManageViews({ views, onOpen, onRename, onDelete, onDone }: ManageViewsProps) {
  return (
    <section aria-label="Saved views" className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
          All saved views · {views.length}
        </span>
        <button type="button" onClick={onDone} className={LINK}>
          done
        </button>
      </div>
      {views.length === 0 ? (
        <p className="text-[13px] text-text-muted">None saved.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line/50">
          {views.map((view) => (
            <Row
              key={view.view_id}
              view={view}
              onOpen={() => onOpen(view)}
              onRename={(name) => onRename(view, name)}
              onDelete={() => onDelete(view)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
