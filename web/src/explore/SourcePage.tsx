import { useEffect, useState } from 'react'

import {
  ApiError,
  getSource,
  getSourceChunks,
  getSourceFigures,
  writeAnnotation,
  type Chunk,
  type NoteDraft,
  type Source,
} from '../lib/api'
import { readable } from '../lib/readable'
import { onInternalClick } from '../lib/route'
import { DataChip, TierChip } from '../ui/Tier'
import { NoteComposer } from './Annotations'
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
 *
 * **Annotation lives here** (`P6-05`, §12.5). This is the screen where reading
 * actually happens, and a note written anywhere else has to remember which
 * passage it came from. Ticking passages is how the citation gets onto the
 * note without the reader copying chunk ids by hand — which is the version of
 * this feature that does not get used.
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

  // Which passages the note will cite. A set rather than a list: ticking the
  // same passage twice is a reader changing their mind, not two citations.
  const [citing, setCiting] = useState<readonly number[]>([])
  const [writing, setWriting] = useState(false)
  const [writeError, setWriteError] = useState<string | null>(null)
  const [kept, setKept] = useState<string | null>(null)

  function onWrite(draft: NoteDraft) {
    setWriting(true)
    setWriteError(null)
    setKept(null)
    writeAnnotation(draft)
      .then((note) => {
        // Said out loud, because a note written here attaches to no node and so
        // appears on no panel this screen shows. Silence after a write is how a
        // reader concludes it did not work and stops writing them.
        setKept(note.title)
        setCiting([])
      })
      .catch((cause: unknown) => {
        setWriteError(cause instanceof ApiError ? cause.message : 'That note was not saved.')
      })
      .finally(() => setWriting(false))
  }

  if (source.status === 'loading') {
    return (
      <div className={PAGE}>
        <p className="font-mono text-[10.5px] text-text-faint">Loading source {sourceId}.</p>
      </div>
    )
  }
  if (source.status === 'error') {
    return (
      <div className={`${PAGE} flex flex-col gap-3`}>
        <p className="text-[14.5px] text-text">{source.message}</p>
        <a
          href="/"
          onClick={onInternalClick('/')}
          className="font-mono text-[10.5px] text-accent-graph hover:underline"
        >
          ← Back to search
        </a>
      </div>
    )
  }

  const it = source.data
  const passageCount = chunks.status === 'ready' ? chunks.data.chunks.length : null

  return (
    <article className={PAGE}>
      {/* The artboard's breadcrumb form: mono, faint, the last step in ink. */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 font-mono text-[11px] text-text-faint">
        <a href="/" onClick={onInternalClick('/')} className="hover:text-accent-graph">
          Explore
        </a>
        <span aria-hidden="true">›</span>
        <span className="text-text">source {it.source_id}</span>
      </nav>

      <header className="mt-5 flex flex-col gap-3 border-b border-line pb-6">
        <h1 className="max-w-[60rem] font-sans text-[length:var(--text-display)] font-semibold leading-[var(--leading-display)] tracking-[var(--tracking-display)] text-text">
          {it.title ?? it.url}
        </h1>

        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <TierChip tier={it.source_tier} />
          {it.publication_date !== null ? <span className={META}>{it.publication_date}</span> : null}
          {it.publisher ? <span className={META}>{it.publisher}</span> : null}
          {it.doi !== null ? <span className={META}>doi {it.doi}</span> : null}
          {/* `P1-44`. A source whose extractor says `pdftotext-failed` and one
              that simply had no text look identical without this. */}
          {it.extractor !== null ? <DataChip>read by {it.extractor}</DataChip> : null}
          {!it.text_available ? <DataChip>no extractable text</DataChip> : null}
          {(it.topic_labels ?? []).map((topic) => (
            <DataChip key={topic}>{topic}</DataChip>
          ))}
        </div>

        <a
          href={it.url}
          rel="noreferrer"
          className="break-all font-mono text-[10.5px] text-accent-graph hover:underline"
        >
          {it.url}
        </a>
      </header>

      <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section aria-labelledby="passages-heading" className="min-w-0">
          <div className="mb-3 flex items-baseline justify-between">
            <h2 id="passages-heading" className={LABEL}>
              Passages
            </h2>
            {passageCount !== null ? (
              <span className="font-mono text-[10px] text-text-faint">
                {passageCount}
                {chunks.status === 'ready' && chunks.data.has_more ? '+' : ''}
              </span>
            ) : null}
          </div>
          {chunks.status === 'loading' ? <p className="font-mono text-[10.5px] text-text-faint">Loading.</p> : null}
          {chunks.status === 'error' ? <p className="text-[13.5px] text-text">{chunks.message}</p> : null}
          {chunks.status === 'ready' ? (
            <Passages
              chunks={chunks.data.chunks}
              citing={citing}
              onCite={(chunkId) =>
                setCiting((current) =>
                  current.includes(chunkId) ? current.filter((id) => id !== chunkId) : [...current, chunkId],
                )
              }
            />
          ) : null}
        </section>

        <aside className="flex flex-col gap-8 lg:sticky lg:top-[78px] lg:self-start">
          <Exports sourceId={sourceId} />

          <section aria-labelledby="note-heading" className="flex flex-col gap-2.5">
            <h2 id="note-heading" className={LABEL}>
              Your note
            </h2>
            <p className="text-[12.5px] leading-[1.55] text-text-muted">
              {/* A note written here usually has no node to attach to, and §12.5
                  wants the habit formed before the graph exists. The passages
                  are the thread back, and they are the part that would be
                  unrecoverable if this screen did not offer it. */}
              Tick the passages it comes from. A note needs no node to attach to — that is the point of having it before
              the graph exists.
            </p>
            <NoteComposer citing={citing} busy={writing} error={writeError} onWrite={onWrite} />
            {kept ? (
              <p className="font-mono text-[10.5px] text-text-muted" role="status">
                Kept “{kept}”. It is in your notes on the Explore landing.
              </p>
            ) : null}
          </section>

          {figures.status === 'ready' ? (
            <section className="flex flex-col gap-2.5">
              <FiguresPanel figures={figures.data.figures} rawAvailable={figures.data.raw_available} />
            </section>
          ) : null}
        </aside>
      </div>
    </article>
  )
}

/** The page's own measure. Wider than a reading column, because the passages
 * and the rail beside them are read together. */
const PAGE = 'mx-auto w-full max-w-[1200px] px-4 pb-24 pt-6 sm:px-6'

const LABEL = 'font-mono text-[9px] font-medium uppercase leading-none tracking-[var(--tracking-label)] text-text-faint'

const META = 'font-mono text-[10.5px] text-text-faint'

function Passages({
  chunks,
  citing,
  onCite,
}: {
  chunks: readonly Chunk[]
  citing: readonly number[]
  onCite: (chunkId: number) => void
}) {
  if (chunks.length === 0) {
    // §6.5 makes metadata-only a valid resting state, so this is a finding
    // rather than an error — a scanned PDF or a paywall, not a broken fetch.
    return (
      <p className="border border-line bg-surface p-5 text-[13.5px] leading-[1.6] text-text-muted">
        No text was extracted from this source. It is still citable, and still counts toward coverage.
      </p>
    )
  }

  return (
    <ol className="divide-y divide-line/60 border border-line bg-surface">
      {chunks.map((chunk) => {
        const on = citing.includes(chunk.chunk_id)
        return (
          <li key={chunk.chunk_id} className={`flex flex-col gap-2.5 px-5 py-4 ${on ? 'bg-accent-graph/5' : ''}`}>
            <p className="whitespace-pre-wrap text-[14.5px] leading-[1.62] text-text/90">{readable(chunk.text)}</p>
            <p className="flex flex-wrap items-center gap-x-3 gap-y-1.5 font-mono text-[10px] text-text-faint">
              <label
                className={`flex cursor-pointer items-center gap-1.5 ${on ? 'text-accent-graph' : 'hover:text-text-muted'}`}
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => onCite(chunk.chunk_id)}
                  className="h-[13px] w-[13px] cursor-pointer accent-[var(--accent-graph)]"
                />
                cite
              </label>
              <span>chunk {chunk.chunk_id}</span>
              {chunk.page_or_offset !== null ? <span>at {chunk.page_or_offset}</span> : null}
              {/* `P2-03`'s verdict. §12.5: a filtered near-duplicate and a
                  never-crawled page are indistinguishable otherwise, and only
                  one is worth investigating. */}
              {chunk.duplicate_of !== null ? <DataChip>duplicate of {chunk.duplicate_of}</DataChip> : null}
            </p>
          </li>
        )
      })}
    </ol>
  )
}

function Exports({ sourceId }: { sourceId: number }) {
  // Plain links, not fetch-and-blob. The endpoints return the file itself
  // (`P6-15`), so the browser's own save is the whole implementation — and a
  // link can be copied, opened in a tab, or piped through curl by someone who
  // would rather not click.
  const base = `/api/explore/export`
  const link =
    'border border-line-strong bg-surface-raised px-3 py-1.5 font-sans text-[12.5px] text-text/85 hover:text-text'
  return (
    <section className="flex flex-col gap-2.5">
      <h2 className={LABEL}>Export</h2>
      <p className="flex flex-wrap items-center gap-2">
        <a href={`${base}/bibtex?source_id=${sourceId}`} download={`meridian-${sourceId}.bib`} className={link}>
          BibTeX
        </a>
        <a href={`${base}/markdown?source_id=${sourceId}`} download={`meridian-${sourceId}.md`} className={link}>
          Markdown
        </a>
      </p>
    </section>
  )
}
