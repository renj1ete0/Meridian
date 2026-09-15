import { DataChip, TierChip } from '../ui/Tier'
import type { SearchHit } from '../lib/api'

/**
 * Search results, with the provenance that makes them citable (task P2-08).
 *
 * **Every hit shows where it came from, and that is the feature.** §2 principle
 * 3 is that nothing is assertable without a citation you can follow back to a
 * file, and the README is explicit that this is not a RAG chatbot. A result
 * list that showed text and left the source to a hover or a detail panel would
 * make the citation optional in practice, which is the same as not having one.
 *
 * So tier, date and position sit on every row, at the same weight, whether or
 * not the reader asked for them. §12.5 puts source tier and date in the minimum
 * viable Explore surface for this reason.
 *
 * **The date says "no date" rather than nothing.** A source that published
 * undated and a source whose date was never extracted look identical from a
 * blank space, and §9 makes staleness a first-class signal — a reader weighing
 * evidence needs to know which of those they are looking at.
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

function Provenance({ hit }: { hit: SearchHit }) {
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <TierChip tier={hit.source_tier} />
      <DataChip>{hit.publication_date ?? 'no date'}</DataChip>
      {hit.page_or_offset !== null ? (
        // Labelled with both names because the API cannot say which it is:
        // §5.3 defines the column as a page number for paginated documents and
        // a character offset otherwise, and a hit carries nothing that
        // distinguishes them. Guessing "page" would mislabel every HTML source.
        <DataChip>page/offset {hit.page_or_offset}</DataChip>
      ) : null}
      {hit.duplicate_of !== null ? (
        <DataChip>duplicate of {hit.duplicate_of}</DataChip>
      ) : null}
    </div>
  )
}

export function ResultList({ hits }: ResultListProps) {
  return (
    <ol className="space-y-6">
      {hits.map((hit) => (
        <li key={hit.chunk_id} className="border border-line bg-surface p-4">
          <a
            href={hit.url}
            target="_blank"
            rel="noreferrer"
            className="font-sans text-[length:var(--text-subhead)] font-semibold text-accent-graph"
          >
            {hit.title ?? domainOf(hit.url)}
          </a>
          <p className="mt-1 break-all font-mono text-[length:var(--text-label)] text-text-faint">
            {domainOf(hit.url)}
          </p>

          <Provenance hit={hit} />

          {/* `whitespace-pre-wrap`: chunks are verbatim slices of the document
              (`P2-02`), and collapsing their line breaks reflows tables and
              page furniture into prose that reads as though the source wrote
              it that way. */}
          <p className="mt-3 whitespace-pre-wrap text-[length:var(--text-body)] leading-[var(--leading-body)] text-text-muted">
            {hit.text}
          </p>
        </li>
      ))}
    </ol>
  )
}
