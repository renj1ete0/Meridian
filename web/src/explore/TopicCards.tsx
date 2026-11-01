import { findLink, NO_FILTERS } from '../lib/find'
import { growthQuery, topicWords } from '../lib/growth'
import { hrefForSource, onInternalClick } from '../lib/route'
import { dateOf } from '../lib/time'
import type { TopicCard, TopicField } from '../lib/api'

/**
 * The topics on the landing (task `B-238`, `ExploreTopics.dc.html`): what this corpus collects
 * for, in the operator's words, each with its size, its newest source, the Map fields it
 * spans, and a way in. See docs/features/web-app.md#topics-on-the-landing.
 */

const LABEL = 'font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint'
const ACTION = 'font-mono text-[10.5px] text-accent-graph hover:underline'

/** Where a field is drawn on the Map: the region holding it, with the field picked (`B-200`). */
export function fieldHref(field: TopicField): string {
  const params = new URLSearchParams()
  if (field.parent_id !== null) params.set('area', String(field.parent_id))
  params.set('pick', String(field.area_id))
  return `/map?${params.toString()}`
}

/** Find narrowed to one topic, waiting for the words (`B-173`). */
export function topicFindHref(topic: string): string {
  return findLink('', { ...NO_FILTERS, topics: [topic] })
}

export interface TopicCardsProps {
  /** Null while loading or when the list could not be read: the section is then absent. */
  topics: TopicCard[] | null
  /** Narrow Find to a topic in place; without it the link navigates. */
  onFind?: (topic: string) => void
}

export function TopicCards({ topics, onFind }: TopicCardsProps) {
  if (!topics || topics.length === 0) return null
  return (
    <section aria-labelledby="topics-label" className="flex flex-col gap-3.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="topics-label" className={LABEL}>
          Topics · {topics.length}
        </h2>
        <span className="font-mono text-[10.5px] text-text-faint">What this corpus collects for, heaviest first</span>
      </div>
      <ul className="grid gap-4 sm:grid-cols-2">
        {topics.map((card) => (
          <li key={card.topic}>
            <Card card={card} onFind={onFind} />
          </li>
        ))}
      </ul>
      <p className="font-mono text-[10.5px] text-text-faint">
        A field opens where it sits on the Map. A source counts once it is labelled with the topic.
      </p>
    </section>
  )
}

function Card({ card, onFind }: { card: TopicCard; onFind?: (topic: string) => void }) {
  const find = topicFindHref(card.topic)
  const growth = `/growth${growthQuery('30d', [card.topic])}`
  const name = topicWords(card.topic)
  return (
    <article
      aria-label={name}
      className="flex h-full flex-col gap-2.5 border border-line bg-surface px-5 py-[18px]"
      data-topic={card.topic}
    >
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-[15px] font-semibold leading-tight tracking-[-0.004em] text-text first-letter:uppercase">
          {name}
        </h3>
        <span className="whitespace-nowrap font-mono text-[10.5px] tabular-nums text-text-faint">
          {card.sources.toLocaleString('en')} source{card.sources === 1 ? '' : 's'}
        </span>
      </div>
      {card.description ? (
        <p className="text-[12.5px] leading-[1.55] text-text-muted">{card.description}</p>
      ) : (
        <p className="text-[12.5px] leading-[1.55] text-text-faint">
          No description has been written for this topic yet.
        </p>
      )}
      <p className="font-mono text-[10.5px] leading-[1.5] text-text-faint">
        {card.newest ? (
          <>
            newest · {dateOf(card.newest.published)} ·{' '}
            <a
              href={hrefForSource(card.newest.source_id)}
              onClick={onInternalClick(hrefForSource(card.newest.source_id))}
              className="text-text/80 hover:text-text hover:underline"
            >
              {card.newest.title ?? 'Untitled source'}
            </a>
          </>
        ) : card.sources > 0 ? (
          'no source in it carries a date'
        ) : (
          'nothing collected yet'
        )}
      </p>
      {card.fields.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className={`${LABEL} mr-1`}>Fields</span>
          {card.fields.map((field) => (
            <a
              key={field.area_id}
              href={fieldHref(field)}
              onClick={onInternalClick(fieldHref(field))}
              title={`${field.passages.toLocaleString('en')} of its passages · open on the Map`}
              className="border border-line px-2 py-[3px] text-[12px] text-text/80 hover:border-line-strong hover:text-text"
            >
              {field.name}
            </a>
          ))}
        </div>
      ) : null}
      <div className="mt-auto flex flex-wrap gap-x-[18px] gap-y-1 pt-0.5">
        <a
          href={find}
          onClick={(event) => {
            if (onFind && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
              event.preventDefault()
              onFind(card.topic)
            } else onInternalClick(find)(event)
          }}
          className={ACTION}
        >
          Find in this topic →
        </a>
        <a href={growth} onClick={onInternalClick(growth)} className={ACTION}>
          Growth →
        </a>
      </div>
    </article>
  )
}
