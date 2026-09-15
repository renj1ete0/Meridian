import { DataChip } from '../ui/Tier'
import { Icon } from '../ui/Icon'

/**
 * The Explore search field — docs/design/design-system.md §8.
 *
 * §8 puts three things in this control and two of them are unusual enough to be
 * worth defending.
 *
 * **The `hybrid` marker.** Retrieval is two arms fused by reciprocal rank
 * (`P2-06`), and a search box that hid that would make the results harder to
 * reason about: a lexical-only result set and a fused one fail in different ways
 * and want different follow-ups. Naming the mode is the same instinct as putting
 * the tier on every hit.
 *
 * **"Filters apply before the vector search."** An implementation note on the
 * surface, deliberately. It is the difference between "twenty government
 * sources" and "whatever survived filtering the top twenty", and a reader who
 * assumes the second will mistrust a correct result set. §12.5 states the
 * behaviour; this says it where the query is typed.
 *
 * Takes props and holds no state. The field is controlled by whatever wires it
 * up, because the query belongs to the page's URL rather than to this control.
 */

export interface SearchFieldProps {
  value: string
  onChange: (value: string) => void
  /** Called on submit. The caller decides what searching means. */
  onSubmit?: (value: string) => void
  /** Disable while no retrieval path is reachable, with `note` saying why. */
  disabled?: boolean
  /**
   * Replaces the standing mode note. Use it to state an absence — a corpus with
   * no vectors yet is searchable lexically and the reader should be told, not
   * left to infer it from thin results.
   */
  note?: string
  id?: string
}

export const MODE_MARKER = 'hybrid'
export const FILTER_NOTE = 'Filters apply before the vector search.'

export function SearchField({
  value,
  onChange,
  onSubmit,
  disabled = false,
  note,
  id = 'explore-search',
}: SearchFieldProps) {
  return (
    <form
      className="mx-auto w-full max-w-2xl"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit?.(value)
      }}
    >
      <label htmlFor={id} className="sr-only">
        Search the corpus
      </label>

      <div className="flex items-center gap-3 border border-line-strong bg-surface-raised px-3">
        <Icon name="search" size={20} />
        <input
          id={id}
          type="search"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Search the corpus"
          className="h-[var(--control-height)] flex-1 bg-transparent font-sans text-[length:var(--text-body)] text-text placeholder:text-text-faint focus:outline-none"
        />
        <DataChip>{MODE_MARKER}</DataChip>
      </div>

      <p className="mt-2 text-center font-mono text-[length:var(--text-data)] text-text-faint">
        {note ?? FILTER_NOTE}
      </p>
    </form>
  )
}
