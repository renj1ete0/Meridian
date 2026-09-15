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
}

export function TopicFilter({
  topics,
  active,
  unexamined = false,
  onToggle,
  onClear,
}: TopicFilterProps) {
  if (topics.length === 0) return null

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => onClear?.()}
          aria-pressed={active.length === 0}
          className={chip(active.length === 0)}
        >
          every topic
        </button>
        {topics.map((topic) => (
          <button
            key={topic}
            type="button"
            onClick={() => onToggle?.(topic)}
            aria-pressed={active.includes(topic)}
            className={chip(active.includes(topic))}
          >
            {topic}
          </button>
        ))}
      </div>

      {active.length > 0 && unexamined ? (
        <p className="mt-2 text-[length:var(--text-small)] text-text-muted">
          {/* Said once, and only while narrowing. On every render it would be
              noise; never, it would be a silent omission a reader cannot see. */}
          Documents collected before topics were recorded are not included — narrowing here can
          hide material that is relevant.
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
  return `rounded-chip border px-2 py-0.5 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
    selected ? 'border-accent-graph text-accent-graph' : 'border-line-strong text-text-muted'
  }`
}
