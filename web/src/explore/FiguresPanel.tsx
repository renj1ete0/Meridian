import { DataChip } from '../ui/Tier'
import type { FigureRef } from '../lib/api'

/**
 * Figures for one source (task P6-14, spec §6.6, §12.5): captions with links, no
 * thumbnails. See docs/features/web-app.md#figures.
 */

export interface FiguresPanelProps {
  figures: readonly FigureRef[]
  /** Whether this deployment serves raw files at all. */
  rawAvailable: boolean
  /** Logos, icons and controls the API left out (`B-156`). */
  furnitureHidden?: number
}

/** How many of the page's own logos and icons were left out, said in a sentence. */
export function furnitureLine(hidden: number): string | null {
  if (hidden <= 0) return null
  return hidden === 1
    ? 'One logo or icon from the page is not shown.'
    : `${hidden.toLocaleString('en')} logos and icons from the page are not shown.`
}

export function FiguresPanel({ figures, rawAvailable, furnitureHidden = 0 }: FiguresPanelProps) {
  const furniture = furnitureLine(furnitureHidden)
  if (figures.length === 0) {
    // Stated, not blank. §12.5's whole first principle is that absence is a
    // finding — and "this document has no figures" and "figures were never
    // extracted from it" are different facts a reader may need to act on.
    return (
      <p className="text-[12.5px] leading-[1.55] text-text-muted">
        No figures were extracted from this source. Captions are taken at ingestion; a document whose figures carry no
        caption or alt text yields none.
        {furniture ? ` ${furniture}` : ''}
      </p>
    )
  }

  return (
    <section>
      <h3 className="font-mono text-[9px] font-medium uppercase leading-none tracking-[var(--tracking-label)] text-text-faint">
        Figures ({figures.length})
      </h3>

      <ul className="mt-3 space-y-4">
        {figures.map((figure) => (
          <li key={figure.figure_id} className="border-l border-line-strong pl-3">
            {/* A caption that is only the image's file name says nothing (`B-156`). */}
            <p className="text-[13px] leading-[1.55] text-text/90">
              {figure.reader_caption ?? <span className="text-text-faint">Untitled image</span>}
            </p>

            <div className="mt-2 flex flex-wrap items-center gap-2">
              {figure.page !== null ? <DataChip>page {figure.page}</DataChip> : null}

              {/* `image_url` is the publisher's live copy; `raw_url` is this corpus's own,
                  at the caption's page. See docs/features/web-app.md#figures. */}
              {figure.raw_url !== null ? (
                <a href={figure.raw_url} className="font-mono text-[10.5px] text-accent-graph hover:underline">
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

            {figure.reader_caption !== null &&
            figure.reader_caption === figure.caption &&
            figure.alt_text !== null &&
            figure.alt_text !== figure.caption ? (
              <p className="mt-1 font-mono text-[length:var(--text-data)] text-text-faint">alt: {figure.alt_text}</p>
            ) : null}
          </li>
        ))}
      </ul>

      {furniture ? <p className="mt-4 font-mono text-[length:var(--text-data)] text-text-faint">{furniture}</p> : null}

      {!rawAvailable ? (
        <p className="mt-4 font-mono text-[length:var(--text-data)] text-text-faint">
          This deployment does not serve stored files, so figures link only to the publisher.
        </p>
      ) : null}
    </section>
  )
}
