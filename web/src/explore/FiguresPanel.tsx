import { DataChip } from '../ui/Tier'
import type { FigureRef } from '../lib/api'

/**
 * Figures for one source (task P6-14, spec §6.6, §12.5).
 *
 * §12.5 asks for "thumbnails linked to the node, with page-accurate links to
 * raw files". There are no thumbnails: nothing downloads figure images
 * (`P1-10`), so `thumbnail_path` is empty and inventing a placeholder grid
 * would be a promise the corpus cannot keep.
 *
 * What there is, is the part §6.6 says carries most of the value — the caption.
 * "Figure captions are text, usually extractable, and often the most
 * information-dense sentence about the figure." So the caption is the content
 * here, and the links are what make it checkable.
 */

export interface FiguresPanelProps {
  figures: readonly FigureRef[]
  /** Whether this deployment serves raw files at all. */
  rawAvailable: boolean
}

export function FiguresPanel({ figures, rawAvailable }: FiguresPanelProps) {
  if (figures.length === 0) {
    // Stated, not blank. §12.5's whole first principle is that absence is a
    // finding — and "this document has no figures" and "figures were never
    // extracted from it" are different facts a reader may need to act on.
    return (
      <p className="text-text-muted">
        No figures were extracted from this source. Captions are taken at ingestion; a document
        whose figures carry no caption or alt text yields none.
      </p>
    )
  }

  return (
    <section>
      <h3 className="font-sans text-[length:var(--text-subhead)] font-semibold">
        Figures ({figures.length})
      </h3>

      <ul className="mt-3 space-y-4">
        {figures.map((figure) => (
          <li key={figure.figure_id} className="border-l border-line pl-3">
            <p className="text-text">{figure.caption ?? figure.alt_text}</p>

            <div className="mt-2 flex flex-wrap items-center gap-2">
              {figure.page !== null ? <DataChip>page {figure.page}</DataChip> : null}

              {/* Two different links, and the difference matters. `image_url`
                  is the picture where the publisher has it — live, and liable
                  to move. `raw_url` is this corpus's own copy at the page the
                  caption belongs to, which is what §5.4 keeps raw files for:
                  link rot is the binding reason, and a local copy is what keeps
                  a citation checkable years later. */}
              {figure.raw_url !== null ? (
                <a href={figure.raw_url} className="text-accent-graph underline">
                  open in the stored copy
                </a>
              ) : null}

              {figure.image_url !== null ? (
                <a
                  href={figure.image_url}
                  className="font-mono text-[length:var(--text-data)] text-text-faint underline"
                  rel="noreferrer"
                >
                  image on the publisher&rsquo;s site
                </a>
              ) : null}
            </div>

            {figure.caption !== null && figure.alt_text !== null ? (
              <p className="mt-1 font-mono text-[length:var(--text-data)] text-text-faint">
                alt: {figure.alt_text}
              </p>
            ) : null}
          </li>
        ))}
      </ul>

      {!rawAvailable ? (
        <p className="mt-4 font-mono text-[length:var(--text-data)] text-text-faint">
          This deployment does not serve stored files, so figures link only to the publisher.
        </p>
      ) : null}
    </section>
  )
}
