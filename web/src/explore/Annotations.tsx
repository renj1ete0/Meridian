import { useState } from 'react'

import { hrefForNode, onInternalClick } from '../lib/route'
import { DataChip } from '../ui/Tier'
import { ApiError, type Annotation, type AnnotationTarget, type NoteDraft } from '../lib/api'
import { dayOf } from '../lib/time'

/**
 * Writing and reading the reader's own notes (task P6-05, spec §12.5, §12.6). The composer
 * is always offered, says what the note will attach to, and never claims authorship.
 * See docs/features/web-app.md#notes.
 */

export interface NoteComposerProps {
  /**
   * What the note will attach to, named. Empty is normal and is stated rather
   * than left blank — "attached to nothing" is a fact about the note.
   */
  about?: readonly AnnotationTarget[]
  /** The passages in front of the reader. Becomes `supporting_chunk_ids`. */
  citing?: readonly number[]
  busy?: boolean
  error?: string | null
  onWrite?: (draft: NoteDraft) => void
}

/** What the note will carry, as a sentence rather than a count of ids. */
export function describeAttachment(about: readonly AnnotationTarget[], citing: readonly number[]): string {
  const targets =
    about.length === 0 ? 'Attached to nothing yet' : `About ${about.map((t) => t.canonical_name).join(', ')}`
  const cited =
    citing.length === 0 ? 'citing no passage' : `citing ${citing.length} passage${citing.length === 1 ? '' : 's'}`
  return `${targets}, ${cited}.`
}

export function NoteComposer({ about = [], citing = [], busy = false, error = null, onWrite }: NoteComposerProps) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={CONTROL}>
        Write a note
      </button>
    )
  }

  return (
    <form
      className="flex flex-col gap-2 border border-line bg-surface p-3"
      onSubmit={(event) => {
        event.preventDefault()
        const trimmed = title.trim()
        if (!trimmed) return
        onWrite?.({
          title: trimmed,
          // Empty prose is absent prose. Storing `""` would make "a title with
          // no note" and "a note whose text was cleared" the same row.
          body: body.trim() || null,
          about: about.map((t) => t.entity_id),
          supporting_chunk_ids: [...citing],
        })
        setTitle('')
        setBody('')
        setOpen(false)
      }}
    >
      <label className="flex flex-col gap-1">
        <span className="sr-only">Title for this note</span>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="What is this?"
          disabled={busy}
          className="h-[var(--control-height)] border border-line-strong bg-surface-raised px-2 text-[13px] text-text placeholder:text-text-faint focus:border-accent-graph/70 focus:outline-none"
        />
      </label>

      <label className="flex flex-col gap-1">
        <span className="sr-only">The note itself</span>
        <textarea
          value={body}
          onChange={(event) => setBody(event.target.value)}
          placeholder="Your thinking. This is the layer nothing else can write."
          rows={4}
          disabled={busy}
          className="border border-line-strong bg-surface-raised px-2 py-1.5 text-[13px] leading-[1.55] text-text placeholder:text-text-faint focus:border-accent-graph/70 focus:outline-none"
        />
      </label>

      <p className="font-mono text-[10.5px] text-text-faint">{describeAttachment(about, citing)}</p>

      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" disabled={busy || !title.trim()} className={CONTROL}>
          Keep it
        </button>
        <button type="button" onClick={() => setOpen(false)} disabled={busy} className={CONTROL}>
          Cancel
        </button>
        {error ? (
          // The API's own sentence. A refused citation names the chunk that did
          // not resolve, and a write on an instance without Access names the two
          // environment variables that would allow it — both actionable, and
          // both lost by a generic "could not save".
          <span className="text-[length:var(--text-small)] text-accent-attention">{error}</span>
        ) : null}
      </div>
    </form>
  )
}

export interface NoteListProps {
  notes: readonly Annotation[]
  /**
   * The node being read, when there is one. Its own chip is dropped from each
   * note — a panel about a node does not need every note on it to repeat which
   * node it is about, and the chips that remain are the ones worth following.
   */
  inContextOf?: number
  /** Rewrite a note in place (`B-201`); absent where nothing can save it. */
  onEdit?: (note: Annotation, change: { title: string; body: string | null }) => Promise<void>
}

/** One note's rewrite, in place: title and body, saved through the edit route. */
function NoteEditor({
  note,
  onSave,
  onCancel,
}: {
  note: Annotation
  onSave: (change: { title: string; body: string | null }) => Promise<void>
  onCancel: () => void
}) {
  const [title, setTitle] = useState(note.title)
  const [body, setBody] = useState(note.body ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        setBusy(true)
        setError(null)
        onSave({ title: title.trim(), body: body.trim() ? body.trim() : null })
          .catch((cause: unknown) => setError(cause instanceof ApiError ? cause.message : 'That was not saved.'))
          .finally(() => setBusy(false))
      }}
    >
      <input
        aria-label="Note title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        className="h-8 border border-line-strong bg-surface-raised px-2 text-[13px] text-text"
      />
      <textarea
        aria-label="Note text"
        value={body}
        rows={4}
        onChange={(e) => setBody(e.target.value)}
        className="border border-line-strong bg-surface-raised px-2 py-1.5 text-[12.5px] text-text"
      />
      <span className="flex items-center gap-3 font-mono text-[10.5px]">
        <button
          type="submit"
          disabled={busy || !title.trim()}
          className="text-accent-graph hover:underline disabled:opacity-50"
        >
          save
        </button>
        <button type="button" onClick={onCancel} className="text-text-faint hover:text-text">
          cancel
        </button>
        {error ? <span className="text-text-muted">{error}</span> : null}
      </span>
    </form>
  )
}

export function NoteList({ notes, inContextOf, onEdit }: NoteListProps) {
  const [editing, setEditing] = useState<number | null>(null)
  if (notes.length === 0) {
    return (
      <p className="mt-2 text-[length:var(--text-small)] text-text-muted">
        Nothing written here yet. Notes are the one layer in this corpus that cannot be re-derived by crawling again.
      </p>
    )
  }

  return (
    <ol className="space-y-4">
      {notes.map((note) => {
        const elsewhere = note.about.filter((t) => t.entity_id !== inContextOf)
        return (
          <li key={note.entity_id} className="border-l-2 border-accent-graph/60 pl-3">
            {editing === note.entity_id && onEdit ? (
              <NoteEditor
                note={note}
                onCancel={() => setEditing(null)}
                onSave={async (change) => {
                  await onEdit(note, change)
                  setEditing(null)
                }}
              />
            ) : (
              <>
                <h3 className="font-sans text-[13.5px] font-semibold text-text">{note.title}</h3>
                {note.body ? (
                  <p className="mt-1 max-w-prose whitespace-pre-wrap text-[12.5px] leading-[1.55] text-text-muted">
                    {note.body}
                  </p>
                ) : null}
                <p className="mt-2 flex flex-wrap items-center gap-2">
                  {/* Written, not created. A note rewritten this morning is a note
                  the reader touched this morning, whatever month the row
                  appeared in. */}
                  <DataChip>written {dayOf(note.produced_at ?? note.created_at)}</DataChip>
                  {note.supporting_chunk_ids.length > 0 ? (
                    <DataChip>
                      {note.supporting_chunk_ids.length} passage
                      {note.supporting_chunk_ids.length === 1 ? '' : 's'}
                    </DataChip>
                  ) : null}
                  {elsewhere.map((target) => (
                    <a
                      key={target.entity_id}
                      href={hrefForNode(target.entity_id)}
                      onClick={onInternalClick(hrefForNode(target.entity_id))}
                      className="font-mono text-[10.5px] text-accent-graph hover:underline"
                    >
                      {target.canonical_name}
                    </a>
                  ))}
                  {onEdit ? (
                    <button
                      type="button"
                      onClick={() => setEditing(note.entity_id)}
                      className="font-mono text-[10.5px] text-accent-graph hover:underline"
                    >
                      edit
                    </button>
                  ) : null}
                </p>
              </>
            )}
          </li>
        )
      })}
    </ol>
  )
}

export interface NotesPanelProps {
  notes: readonly Annotation[]
  /** How many exist, which is not how many came back. */
  total: number
  /** Read the rest (`B-180`); offered only while some are not shown. */
  onShowAll?: () => void
  onEdit?: NoteListProps['onEdit']
}

/**
 * The reader's own layer, on the landing screen (§12.5), with a plain Markdown export
 * link. See docs/features/web-app.md#notes.
 */
export function NotesPanel({ notes, total, onShowAll, onEdit }: NotesPanelProps) {
  return (
    <section aria-labelledby="your-notes" className="flex flex-col gap-3.5">
      <div className="flex items-baseline justify-between gap-4">
        <h2
          id="your-notes"
          className="font-mono text-[9px] font-medium uppercase leading-none tracking-[var(--tracking-label)] text-text-faint"
        >
          Your notes
        </h2>
        {total > 0 ? (
          <a
            href="/api/explore/export/annotations"
            className="font-mono text-[10.5px] text-accent-graph hover:underline"
          >
            export as Markdown →
          </a>
        ) : null}
      </div>
      <p className="font-mono text-[11.5px] leading-[1.5] text-text-muted">
        {total === 0
          ? 'Nothing yet. A note is the only thing here that cannot be recovered by crawling again.'
          : `${total} note${total === 1 ? '' : 's'}${notes.length < total ? `, ${notes.length} shown` : ''}.`}
        {onShowAll && notes.length < total ? (
          <>
            {' '}
            <button type="button" onClick={onShowAll} className="text-accent-graph hover:underline">
              show all
            </button>
          </>
        ) : null}
      </p>

      {notes.length > 0 ? <NoteList notes={notes} onEdit={onEdit} /> : null}
    </section>
  )
}

const CONTROL =
  'h-[var(--control-height)] border border-line-strong bg-surface-raised px-3 ' +
  'font-sans text-[12.5px] text-text/85 hover:text-text disabled:opacity-50'
