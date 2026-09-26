import { useState } from 'react'

import { readable } from '../lib/readable'
import { hrefForSource, onInternalClick } from '../lib/route'
import { DataChip, TierChip } from '../ui/Tier'
import type { SearchHit } from '../lib/api'

/**
 * Search results, with the provenance that makes them citable (tasks P2-08,
 * P6-27).
 *
 * No artboard draws this list. Its form is taken from the nearest thing that
 * does — the supporting chunks in the Explore artboard's detail panel: one
 * bordered surface, rows divided by hairlines, the passage first and a single
 * mono line of provenance beneath it (domain in cyan, tier as a bordered mono
 * chip, date, position). Dense, square, and read top to bottom.
 *
 * **Every hit shows where it came from, and that is the feature.** §2 principle
 * 3 is that nothing is assertable without a citation you can follow back to a
 * file, and the README is explicit that this is not a RAG chatbot. A result
 * list that showed text and left the source to a hover or a detail panel would
 * make the citation optional in practice, which is the same as not having one.
 *
 * **The date says "no date" rather than nothing.** A source that published
 * undated and a source whose date was never extracted look identical from a
 * blank space, and §9 makes staleness a first-class signal — a reader weighing
 * evidence needs to know which of those they are looking at.
 *
 * Passages stay in Archivo at reading size rather than the artboard's mono
 * italic quotes: there, a quote is two lines of evidence beside a node; here it
 * is the thing being read, often a paragraph long.
 */

export interface ResultListProps {
  hits: readonly SearchHit[]
}

/** The host, for a reader deciding whether to follow a link. */
function domainOf(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    // A URL the crawler stored and this cannot parse is worth showing whole
    // rather than hiding: it is evidence about the corpus, not a render bug.
    return url
  }
}

const META = 'font-mono text-[10px] text-text-faint'

/**
 * The passage's own topics that its source does not carry (`P2-24`), in the
 * passage's order. Empty when the passage is unexamined (null) or about
 * nothing beyond its document.
 */
export function passageOnlyTopics(
  hit: Pick<SearchHit, 'topic_labels' | 'passage_topics'>,
): string[] {
  const onSource = new Set(hit.topic_labels ?? [])
  return (hit.passage_topics ?? []).filter((topic) => !onSource.has(topic))
}

function Provenance({ hit }: { hit: SearchHit }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
      {/* Two destinations, kept distinct: the domain goes to the live page,
          the title to what this corpus actually holds — the passages it
          extracted, the figures, and the stored copy. */}
      <a
        href={hit.url}
        target="_blank"
        rel="noreferrer"
        className="break-all font-mono text-[10px] text-accent-graph hover:underline"
      >
        {domainOf(hit.url)}
      </a>
      <TierChip tier={hit.source_tier} />
      <span className={META}>{hit.publication_date ?? 'no date'}</span>
      {hit.page_or_offset !== null ? (
        // `P2-18` gave the hit a `page_unit`, so this can name the number
        // instead of hedging. `null` still means the source's media type was
        // never recorded — falling back to both names there is honest, where
        // picking one would mislabel a citation someone will try to follow.
        <span className={META}>
          {hit.page_unit ?? 'page/offset'} {hit.page_or_offset}
        </span>
      ) : null}
      {/* Topics, when the source has been examined for them (`P2-14`). A hit
          whose topic a reader cannot see is a filter they have to trust rather
          than check. Nothing is shown when the list is null or empty: neither
          is a topic, and inventing a chip for either would put a label on a
          document that has none. */}
      {(hit.topic_labels ?? []).map((topic) => (
        <DataChip key={topic}>{topic}</DataChip>
      ))}
      {/* `P2-24`: topics this passage is about that its document is not. A
          topic filter matches on these too, so without the chip a hit from a
          document labelled with something else would sit in a filtered list
          with no visible reason. Topics the source already carries are not
          repeated. */}
      {passageOnlyTopics(hit).map((topic) => (
        <DataChip key={`passage-${topic}`}>passage · {topic}</DataChip>
      ))}
      {hit.duplicate_of !== null ? <DataChip>duplicate of {hit.duplicate_of}</DataChip> : null}
    </div>
  )
}

/** Passages longer than this start clamped. Measured in characters, not lines,
 * so the decision is the same on every screen width. */
export const CLAMP_OVER = 600

/**
 * One passage, clamped to eight lines when long. The whole text is always in
 * the page — the clamp is CSS, so find-in-page, copy and a screen reader all
 * get the verbatim chunk — and one click shows it in full. A result list where
 * a single extraction-damaged chunk fills three screens is a list nobody reads
 * past the first hit.
 */
function Passage({ text: raw }: { text: string }) {
  const [open, setOpen] = useState(false)
  const text = readable(raw)
  const long = text.length > CLAMP_OVER
  return (
    <div className="flex flex-col items-start gap-1.5">
      <p
        className={`whitespace-pre-wrap text-[13.5px] leading-[1.6] text-text/85 ${
          long && !open ? 'line-clamp-[8]' : ''
        }`}
      >
        {text}
      </p>
      {long ? (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="font-mono text-[10.5px] text-accent-graph hover:underline"
        >
          {open ? 'shorter' : `whole passage · ${text.length.toLocaleString('en')} characters`}
        </button>
      ) : null}
    </div>
  )
}

export function ResultList({ hits }: ResultListProps) {
  return (
    <ol className="divide-y divide-line/60 border border-line bg-surface">
      {hits.map((hit) => (
        <li key={hit.chunk_id} className="flex flex-col gap-2 px-5 py-4">
          <div className="flex items-baseline justify-between gap-4">
            <a
              href={hrefForSource(hit.source_id)}
              onClick={onInternalClick(hrefForSource(hit.source_id))}
              className="min-w-0 font-sans text-[14.5px] font-semibold leading-snug text-text hover:text-accent-graph"
            >
              {hit.title ?? domainOf(hit.url)}
            </a>
            <a
              href={hrefForSource(hit.source_id)}
              onClick={onInternalClick(hrefForSource(hit.source_id))}
              className="shrink-0 font-mono text-[10.5px] text-accent-graph hover:underline"
            >
              in this corpus →
            </a>
          </div>

          {/* `whitespace-pre-wrap`: chunks are verbatim slices of the document
              (`P2-02`), and collapsing their line breaks reflows tables and
              page furniture into prose that reads as though the source wrote
              it that way. */}
          <Passage text={hit.text} />

          <Provenance hit={hit} />
        </li>
      ))}
    </ol>
  )
}
