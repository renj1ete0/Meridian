import { useEffect, useId, useState } from 'react'

import { searchNodes, type NodeMatch } from './api'
import { ApiError } from '../../lib/api'

/**
 * Find a node by name or alias (task P6-01): the workspace's own way in, and
 * path mode's way to pick a node that is not on screen.
 *
 * A combobox with the keyboard behaviour one expects — arrows move, Enter
 * picks, Escape closes — because a reader who types a name wants to land on
 * it without reaching for the mouse. Requests are debounced and aborted when
 * superseded, so a fast typist never sees results for a prefix they have
 * already typed past.
 */

export const DEBOUNCE_MS = 180

export interface NodeSearchBoxProps {
  onPick: (entityId: number, match: NodeMatch) => void
  placeholder?: string
  autoFocus?: boolean
  /** Ids to leave out of the results — e.g. the focus, when picking a path end. */
  exclude?: readonly number[]
  /** Open the list above the box — for a box at the bottom of the canvas. */
  dropUp?: boolean
}

export function NodeSearchBox({
  onPick,
  placeholder = 'Find a node',
  autoFocus,
  exclude = [],
  dropUp = false,
}: NodeSearchBoxProps) {
  const [text, setText] = useState('')
  const [matches, setMatches] = useState<NodeMatch[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState(0)
  const [open, setOpen] = useState(false)
  const list = useId()

  useEffect(() => {
    const q = text.trim()
    if (!q) {
      setMatches(null)
      setError(null)
      return
    }
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      searchNodes(q, 8, { signal: controller.signal })
        .then((found) => {
          setMatches(found.matches.filter((m) => !exclude.includes(m.entity_id)))
          setError(null)
          setActive(0)
        })
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === 'AbortError') return
          setError(cause instanceof ApiError ? cause.message : 'Search failed.')
        })
    }, DEBOUNCE_MS)
    return () => {
      window.clearTimeout(timer)
      controller.abort()
    }
    // `exclude` is compared by content; a new array each render must not refetch.
  }, [text, exclude.join(',')])

  function pick(match: NodeMatch) {
    onPick(match.entity_id, match)
    setText('')
    setMatches(null)
    setOpen(false)
  }

  const showing = open && text.trim() !== ''

  return (
    <div className="relative">
      <div className="flex items-center gap-2 border border-line-strong bg-ground px-2.5 py-[7px] focus-within:border-accent-graph">
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true" className="shrink-0 text-text-faint">
          <circle cx="7" cy="7" r="4.6" stroke="currentColor" strokeWidth="1.4" />
          <path d="M10.4 10.4 L14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <input
          role="combobox"
          aria-expanded={showing}
          aria-controls={list}
          aria-autocomplete="list"
          aria-label={placeholder}
          autoFocus={autoFocus}
          value={text}
          placeholder={placeholder}
          onChange={(e) => {
            setText(e.target.value)
            setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (!matches || matches.length === 0) {
              if (e.key === 'Escape') setOpen(false)
              return
            }
            if (e.key === 'ArrowDown') {
              e.preventDefault()
              setActive((i) => (i + 1) % matches.length)
            } else if (e.key === 'ArrowUp') {
              e.preventDefault()
              setActive((i) => (i - 1 + matches.length) % matches.length)
            } else if (e.key === 'Enter') {
              e.preventDefault()
              pick(matches[active] ?? matches[0]!)
            } else if (e.key === 'Escape') {
              setOpen(false)
            }
          }}
          className="min-w-0 flex-1 bg-transparent text-[12.5px] text-text outline-none placeholder:text-text-faint"
        />
      </div>
      {showing ? (
        <ul
          id={list}
          role="listbox"
          className={`absolute inset-x-0 z-20 max-h-72 overflow-y-auto border border-line-strong bg-surface ${
            dropUp ? 'bottom-full mb-1' : 'top-full mt-1'
          }`}
        >
          {error ? (
            <li className="px-2.5 py-2 text-[12px] text-accent-attention">{error}</li>
          ) : matches === null ? (
            <li className="px-2.5 py-2 text-[12px] text-text-faint">Searching.</li>
          ) : matches.length === 0 ? (
            <li className="px-2.5 py-2 text-[12px] text-text-faint">No node by that name or alias.</li>
          ) : (
            matches.map((m, i) => (
              <li
                key={m.entity_id}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault()
                  pick(m)
                }}
                onMouseEnter={() => setActive(i)}
                className={`flex cursor-pointer flex-col px-2.5 py-1.5 ${i === active ? 'bg-surface-raised' : ''}`}
              >
                <span className="truncate text-[12.5px] text-text">{m.canonical_name}</span>
                <span className="font-mono text-[9.5px] uppercase tracking-[0.12em] text-text-faint">
                  {m.node_type.replaceAll('_', ' ')}
                  {m.jurisdiction ? ` · ${m.jurisdiction}` : ''} · {m.degree} edge{m.degree === 1 ? '' : 's'}
                  {m.matched_alias ? ` · as ${m.matched_alias}` : ''}
                </span>
              </li>
            ))
          )}
        </ul>
      ) : null}
    </div>
  )
}
