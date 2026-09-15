import { useEffect, useState } from 'react'

import {
  ApiError,
  getSource,
  getSourceChunks,
  getSourceFigures,
  type Chunk,
  type Source,
} from '../lib/api'
import { onInternalClick } from '../lib/route'
import { DataChip, TierChip } from '../ui/Tier'
import { FiguresPanel } from './FiguresPanel'

/**
 * One source, read as a document (tasks P6-14, P6-15).
 *
 * The first screen where the corpus reads like documents rather than results.
 * Everything here already existed as an endpoint and had nowhere to be shown:
 * the chunks in document order, the figures with their captions, and the
 * exports §12.5 asks for.
 *
 * **Provenance is the page, not a footnote on it.** Tier, date, DOI and how the
 * text was extracted are in the header, because "what is this and how do I know"
 * is the question a reader arrives with — and because `extractor` (`P1-44`) is
 * the difference between a document that had no text and one whose extractor
 * fell over.
 */

type Load<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T }

function useResource<T>(load: (signal: AbortSignal) => Promise<T>, key: unknown): Load<T> {
  const [state, setState] = useState<Load<T>>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    setState({ status: 'loading' })
    load(controller.signal)
      .then((data) => setState({ status: 'ready', data }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setState({
          status: 'error',
          // §4: an error names the cause. "Something went wrong" tells a reader
          // nothing they can act on, and this one usually means the id is stale.
          message: error instanceof ApiError ? error.message : 'The request did not complete.',
        })
      })
    return () => controller.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return state
}

export function SourcePage({ sourceId }: { sourceId: number }) {
  const source = useResource<Source>((signal) => getSource(sourceId, { signal }), sourceId)
  const chunks = useResource((signal) => getSourceChunks(sourceId, {}, { signal }), sourceId)
  const figures = useResource((signal) => getSourceFigures(sourceId, { signal }), sourceId)

  if (source.status === 'loading') {
    return <p className="text-text-muted">Loading source {sourceId}.</p>
  }
  if (source.status === 'error') {
    return (
      <div>
        <p className="text-text">{source.message}</p>
        <a href="/" onClick={onInternalClick('/')} className="text-accent-graph underline">
          Back to search
        </a>
      </div>
    )
  }

  const it = source.data
  return (
    <article>
      <a href="/" onClick={onInternalClick('/')} className="text-accent-graph underline">
        ← search
      </a>

      <h1 className="mt-4 font-sans text-[length:var(--text-display)] font-semibold leading-[var(--leading-display)] tracking-[var(--tracking-display)]">
        {it.title ?? it.url}
      </h1>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <TierChip tier={it.source_tier} />
        {it.publication_date !== null ? <DataChip>{it.publication_date}</DataChip> : null}
        {it.doi !== null ? <DataChip>doi {it.doi}</DataChip> : null}
        {/* `P1-44`. A source whose extractor says `pdftotext-failed` and one
            that simply had no text look identical without this. */}
        {it.extractor !== null ? <DataChip>read by {it.extractor}</DataChip> : null}
        {!it.text_available ? <DataChip>no extractable text</DataChip> : null}
      </div>

      <p className="mt-3">
        <a href={it.url} rel="noreferrer" className="break-all text-accent-graph underline">
          {it.url}
        </a>
      </p>

      <Exports sourceId={sourceId} />

      <section className="mt-10">
        <h2 className="font-sans text-[length:var(--text-heading)] font-semibold">Passages</h2>
        {chunks.status === 'loading' ? <p className="mt-2 text-text-muted">Loading.</p> : null}
        {chunks.status === 'error' ? <p className="mt-2 text-text">{chunks.message}</p> : null}
        {chunks.status === 'ready' ? <Passages chunks={chunks.data.chunks} /> : null}
      </section>

      <section className="mt-10">
        {figures.status === 'ready' ? (
          <FiguresPanel
            figures={figures.data.figures}
            rawAvailable={figures.data.raw_available}
          />
        ) : null}
      </section>
    </article>
  )
}

function Passages({ chunks }: { chunks: readonly Chunk[] }) {
  if (chunks.length === 0) {
    // §6.5 makes metadata-only a valid resting state, so this is a finding
    // rather than an error — a scanned PDF or a paywall, not a broken fetch.
    return (
      <p className="mt-2 text-text-muted">
        No text was extracted from this source. It is still citable, and still counts toward
        coverage.
      </p>
    )
  }

  return (
    <ol className="mt-4 space-y-6">
      {chunks.map((chunk) => (
        <li key={chunk.chunk_id}>
          <p className="whitespace-pre-wrap text-text">{chunk.text}</p>
          <p className="mt-2 flex flex-wrap items-center gap-2">
            <DataChip>chunk {chunk.chunk_id}</DataChip>
            {chunk.page_or_offset !== null ? <DataChip>at {chunk.page_or_offset}</DataChip> : null}
            {/* `P2-03`'s verdict. §12.5: a filtered near-duplicate and a
                never-crawled page are indistinguishable otherwise, and only one
                is worth investigating. */}
            {chunk.duplicate_of !== null ? (
              <DataChip>duplicate of {chunk.duplicate_of}</DataChip>
            ) : null}
          </p>
        </li>
      ))}
    </ol>
  )
}

function Exports({ sourceId }: { sourceId: number }) {
  // Plain links, not fetch-and-blob. The endpoints return the file itself
  // (`P6-15`), so the browser's own save is the whole implementation — and a
  // link can be copied, opened in a tab, or piped through curl by someone who
  // would rather not click.
  const base = `/api/explore/export`
  return (
    <p className="mt-6 flex flex-wrap items-center gap-3 font-mono text-[length:var(--text-data)]">
      <span className="text-text-faint">export</span>
      <a
        href={`${base}/bibtex?source_id=${sourceId}`}
        download={`meridian-${sourceId}.bib`}
        className="text-accent-graph underline"
      >
        BibTeX
      </a>
      <a
        href={`${base}/markdown?source_id=${sourceId}`}
        download={`meridian-${sourceId}.md`}
        className="text-accent-graph underline"
      >
        Markdown
      </a>
    </p>
  )
}

