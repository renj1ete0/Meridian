/**
 * Narrowing a search to a topic (task P6-24, spec §12.5).
 *
 * `P2-14` put the labels on every source and the filter on the API; this is the
 * control. It is chips rather than a dropdown because the set is small — a
 * handful of topics, which is what §10's weight vector is — and because a
 * dropdown hides the count of what is available behind a click.
 *
 * **The caveat is the interesting part.** A source crawled before `P2-14`
 * carries no labels, and a topic filter excludes it: nothing has established
 * that it belongs to the topic, and claiming it would assert something no pass
 * checked. That is correct and it is also invisible — a reader who narrows to a
 * topic and sees three results has no way to know the corpus holds three hundred
 * documents nobody has examined. So the control says so, once, while a filter is
 * active.
 */

export interface TopicFilterProps {
  topics: readonly string[]
  active: readonly string[]
  /** True when some sources have never been examined for topics (`P2-14`). */
  unexamined?: boolean
  onToggle?: (topic: string) => void
  onClear?: () => void
  /**
   * The same control over another axis (`P2-23`): places are chips too, and a
   * second component would drift from this one in exactly the caveat that
   * matters. `names` shows a stored code as a name; the value toggled is
   * still the code.
   */
  label?: string
  every?: string
  names?: Readonly<Record<string, string>>
  caveat?: string
  /**
   * How several chosen values combine (`B-72`). Offered only when two or more
   * are chosen — with one, any and all are the same search — and only when
   * the caller passes `onMatch`, since places have no such switch.
   */
  match?: 'any' | 'all'
  onMatch?: (match: 'any' | 'all') => void
}

export function TopicFilter({
  topics,
  active,
  unexamined = false,
  onToggle,
  onClear,
  label = 'Topic',
  every = 'every topic',
  names,
  caveat = 'Documents collected before topics were recorded are not included — narrowing here can hide material that is relevant.',
  match = 'any',
  onMatch,
}: TopicFilterProps) {
  if (topics.length === 0) return null

  return (
    <div>
      <div
        className="flex flex-wrap items-center gap-1.5"
        role="group"
        aria-label={`Narrow by ${label.toLowerCase()}`}
      >
        <span className="mr-2 font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
          {label}
        </span>
        <button
          type="button"
          onClick={() => onClear?.()}
          aria-pressed={active.length === 0}
          className={chip(active.length === 0)}
        >
          {every}
        </button>
        {topics.map((topic) => (
          <button
            key={topic}
            type="button"
            onClick={() => onToggle?.(topic)}
            aria-pressed={active.includes(topic)}
            className={chip(active.includes(topic))}
            title={names?.[topic] ? topic : undefined}
          >
            {names?.[topic] ?? topic}
          </button>
        ))}
        {onMatch && active.length > 1 ? (
          <span
            role="radiogroup"
            aria-label={`Match ${label.toLowerCase()}s`}
            className="ml-2 inline-flex items-center gap-1.5"
          >
            <span className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
              match
            </span>
            {(
              [
                ['any', 'any of these'],
                ['all', 'all at once'],
              ] as const
            ).map(([value, text]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={match === value}
                onClick={() => onMatch(value)}
                className={chip(match === value)}
              >
                {text}
              </button>
            ))}
          </span>
        ) : null}
      </div>

      {active.length > 0 && unexamined ? (
        <p className="mt-2 font-mono text-[10.5px] leading-[1.5] text-text-muted">
          {/* Said once, and only while narrowing. On every render it would be
              noise; never, it would be a silent omission a reader cannot see. */}
          {caveat}
        </p>
      ) : null}
    </div>
  )
}

/**
 * Selected chips carry the graph accent and nothing else.
 *
 * §2: the palette has no green and no red, and colour must not imply a verdict.
 * A topic is not better or worse than another one, so the only thing colour says
 * here is "this is on".
 */
function chip(selected: boolean): string {
  return `border px-2 py-[3px] font-mono text-[10.5px] leading-[1.4] ${
    selected
      ? 'border-accent-graph/70 bg-accent-graph/10 text-accent-graph'
      : 'border-line text-text-faint hover:border-line-strong hover:text-text-muted'
  }`
}
