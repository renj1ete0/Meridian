import { SEARCH_INPUT_ID } from '../lib/hotkeys'
import { Icon } from '../ui/Icon'

/**
 * The Explore search field — design-system.md §8, `ExploreLanding.dc.html` — with the
 * retrieval-mode marker and the note on filters, both in a reader's words (`B-99`, ADR 0013).
 * Controlled; holds no state. See docs/features/web-app.md#the-search-field.
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
  /** `large` on the landing, where the field is the page; `regular` above results. */
  size?: 'large' | 'regular'
  id?: string
}

export const MODE_MARKER = 'words + meaning'
export const FILTER_NOTE = 'Filters narrow what is searched, not what is shown.'
export const SHORTCUT_NOTE = '⌘K from anywhere'

export function SearchField({
  value,
  onChange,
  onSubmit,
  disabled = false,
  note,
  size = 'large',
  id = SEARCH_INPUT_ID,
}: SearchFieldProps) {
  const large = size === 'large'

  return (
    <form
      className="flex w-full flex-col gap-3"
      role="search"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit?.(value)
      }}
    >
      <label htmlFor={id} className="sr-only">
        Search the corpus
      </label>

      <div
        className={`flex items-center gap-[13px] border border-line-strong bg-surface focus-within:border-accent-graph/70 ${
          large ? 'px-[18px] py-2.5' : 'px-3.5 py-1'
        }`}
      >
        <span className="shrink-0 text-text-faint">
          <Icon name="search" size={17} />
        </span>
        <input
          id={id}
          type="search"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          placeholder="Search concepts, places, findings, sources"
          autoComplete="off"
          spellCheck={false}
          className={`min-w-0 flex-1 bg-transparent font-sans text-text placeholder:text-text-faint focus:outline-none ${
            large ? 'h-9 text-[15px]' : 'h-8 text-[14px]'
          }`}
        />
        {/* Not on a phone, where it took the room the placeholder needs (`B-125`). */}
        <span className="hidden shrink-0 border border-line px-1.5 py-[3px] font-mono text-[9.5px] leading-none text-text-faint sm:inline">
          {MODE_MARKER}
        </span>
      </div>

      <p className="flex justify-between gap-4 font-mono text-[10.5px] text-text-faint">
        <span>{note ?? FILTER_NOTE}</span>
        <span className="hidden shrink-0 sm:inline" aria-hidden="true">
          {SHORTCUT_NOTE}
        </span>
      </p>
    </form>
  )
}
