import { useCallback, useEffect, useRef, useState } from 'react'

import {
  ApiError,
  getSource,
  getSourceChunks,
  getSourceFigures,
  writeAnnotation,
  type Chunk,
  type NoteDraft,
  type Source,
  type SourceWithPage,
} from '../lib/api'
import { citablePosition } from '../lib/position'
import { readable } from '../lib/readable'
import { onInternalClick, passageOf } from '../lib/route'
import { DataChip, TierChip } from '../ui/Tier'
import { NoteComposer } from './Annotations'
import { FiguresPanel } from './FiguresPanel'

/**
 * One source, read as a document (tasks P6-14, P6-15): provenance in the header, chunks
 * in order, figures, exports, and the note composer. See docs/features/web-app.md#source-pages.
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

/** Passages read at a time, as the route's default page. */
const WINDOW = 20

type Window =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | {
      status: 'ready'
      chunks: readonly Chunk[]
      /** Where the window starts, in document order. */
      start: number
      more: boolean
      /** A side being read, or the reason it failed. */
      busy: 'earlier' | 'later' | null
      failed: string | null
    }

/**
 * A source's passages as a window that grows both ways (`B-178`): opened on a linked passage
 * in its context, or at the top, with earlier and later passages a click away. Before it the
 * page read the first twenty and stopped, so a hit on page 24 was often not on the page.
 */
export function usePassageWindow(sourceId: number, target: number | null) {
  const [state, setState] = useState<Window>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    setState({ status: 'loading' })
    getSourceChunks(sourceId, target !== null ? { around: target, limit: WINDOW } : { limit: WINDOW }, {
      signal: controller.signal,
    })
      .then((page) =>
        setState({
          status: 'ready',
          chunks: page.chunks,
          start: page.offset,
          more: page.has_more,
          busy: null,
          failed: null,
        }),
      )
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        setState({ status: 'error', message: error instanceof ApiError ? error.message : 'The passages did not load.' })
      })
    return () => controller.abort()
  }, [sourceId, target])

  const extend = useCallback(
    (side: 'earlier' | 'later') => {
      if (state.status !== 'ready' || state.busy) return
      const from = side === 'earlier' ? Math.max(0, state.start - WINDOW) : state.start + state.chunks.length
      const limit = side === 'earlier' ? state.start - from : WINDOW
      if (limit <= 0) return
      setState({ ...state, busy: side, failed: null })
      getSourceChunks(sourceId, { offset: from, limit })
        .then((page) =>
          setState((now) => {
            if (now.status !== 'ready') return now
            const seen = new Set(now.chunks.map((c) => c.chunk_id))
            const fresh = page.chunks.filter((c) => !seen.has(c.chunk_id))
            return side === 'earlier'
              ? { ...now, chunks: [...fresh, ...now.chunks], start: from, busy: null }
              : { ...now, chunks: [...now.chunks, ...fresh], more: page.has_more, busy: null }
          }),
        )
        .catch((error: unknown) =>
          setState((now) =>
            now.status === 'ready'
              ? {
                  ...now,
                  busy: null,
                  failed: error instanceof ApiError ? error.message : 'Those passages did not load.',
                }
              : now,
          ),
        )
    },
    [sourceId, state],
  )

  return { state, extend }
}

export function SourcePage({ sourceId }: { sourceId: number }) {
  const source = useResource<SourceWithPage>((signal) => getSource(sourceId, { signal }), sourceId)
  const target = passageOf(window.location.search)
  const { state: chunks, extend } = usePassageWindow(sourceId, target)
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
  const span =
    chunks.status === 'ready' && chunks.chunks.length > 0
      ? `${chunks.start + 1}–${chunks.start + chunks.chunks.length}${chunks.more ? ', more after' : ''}`
      : null

  return (
    <article className={PAGE}>
      {/* The artboard's breadcrumb form: mono, faint, the last step in ink. */}
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 font-mono text-[11px] text-text-faint">
        <a href="/" onClick={onInternalClick('/')} className="hover:text-accent-graph">
          Explore
        </a>
        <span aria-hidden="true">›</span>
        <span className="text-text">{hostOf(it.url)}</span>
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
        <RecordDetails source={it} />
      </header>

      <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_320px]">
        <section aria-labelledby="passages-heading" className="min-w-0">
          <div className="mb-3 flex items-baseline justify-between">
            <h2 id="passages-heading" className={LABEL}>
              Passages
            </h2>
            {span !== null ? <span className="font-mono text-[10px] text-text-faint">{span}</span> : null}
          </div>
          {chunks.status === 'loading' ? <p className="font-mono text-[10.5px] text-text-faint">Loading.</p> : null}
          {chunks.status === 'error' ? <p className="text-[13.5px] text-text">{chunks.message}</p> : null}
          {chunks.status === 'ready' && chunks.start > 0 ? (
            <MoreButton side="earlier" busy={chunks.busy === 'earlier'} onClick={() => extend('earlier')} />
          ) : null}
          {chunks.status === 'ready' && target !== null && !chunks.chunks.some((c) => c.chunk_id === target) ? (
            // The link named a passage this source no longer holds as live text (`P1-32`).
            <p className="mb-3 font-mono text-[10.5px] leading-[1.5] text-text-muted">
              The passage this link points to is no longer in the source's current text; it opens at the start.
            </p>
          ) : null}
          {chunks.status === 'ready' ? (
            <Passages
              chunks={chunks.chunks}
              target={target}
              pageUnit={source.status === 'ready' ? source.data.page_unit : null}
              citing={citing}
              onCite={(chunkId) =>
                setCiting((current) =>
                  current.includes(chunkId) ? current.filter((id) => id !== chunkId) : [...current, chunkId],
                )
              }
            />
          ) : null}
          {chunks.status === 'ready' && chunks.more ? (
            <MoreButton side="later" busy={chunks.busy === 'later'} onClick={() => extend('later')} />
          ) : null}
          {chunks.status === 'ready' && chunks.failed ? (
            <p className="mt-2 font-mono text-[10.5px] text-text-muted">{chunks.failed}</p>
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
              Tick the passages it comes from. A note needs no concept to attach to — that is the point of having it
              before the graph exists.
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
              <FiguresPanel
                figures={figures.data.figures}
                rawAvailable={figures.data.raw_available}
                furnitureHidden={figures.data.furniture_hidden}
              />
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

/** The host, for the breadcrumb: a reader knows a site, not a row number. */
function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

/**
 * The corpus's own bookkeeping about a source, folded away from a reader (`B-156`): its id,
 * which tool read it (`P1-44`: a failed extractor and a document with no text look alike
 * without it), and its language and how that was found.
 */
export function RecordDetails({ source }: { source: Source }) {
  const languageFrom = (source.extra as { language_from?: string } | null)?.language_from
  return (
    <details className="group font-mono text-[10.5px] text-text-faint">
      <summary className="w-fit cursor-pointer list-none hover:text-text-muted">
        record details <span className="inline-block transition-transform group-open:rotate-90">›</span>
      </summary>
      <dl className="mt-2 grid w-fit grid-cols-[auto_auto] gap-x-4 gap-y-1">
        <dt>source</dt>
        <dd className="tabular-nums text-text-muted">{source.source_id}</dd>
        {source.extractor !== null ? (
          <>
            <dt>read by</dt>
            <dd className="text-text-muted">{source.extractor}</dd>
          </>
        ) : null}
        {source.language !== null ? (
          <>
            <dt>language</dt>
            <dd className="text-text-muted">
              {source.language}
              {languageFrom === 'text' ? ' (read from the text)' : ''}
            </dd>
          </>
        ) : null}
      </dl>
    </details>
  )
}

function MoreButton({ side, busy, onClick }: { side: 'earlier' | 'later'; busy: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className={`${side === 'earlier' ? 'mb-3' : 'mt-3'} h-8 border border-line-strong bg-surface-raised px-3 font-sans text-[12.5px] text-text/85 hover:text-text disabled:opacity-50`}
    >
      {busy ? 'Reading…' : side === 'earlier' ? '↑ Earlier passages' : 'Later passages ↓'}
    </button>
  )
}

function Passages({
  chunks,
  target,
  pageUnit,
  citing,
  onCite,
}: {
  chunks: readonly Chunk[]
  /** The passage the link opened on, marked and scrolled to once. */
  target: number | null
  pageUnit: SourceWithPage['page_unit']
  citing: readonly number[]
  onCite: (chunkId: number) => void
}) {
  const marked = useRef<HTMLLIElement | null>(null)
  const scrolled = useRef(false)
  useEffect(() => {
    scrolled.current = false
  }, [target])
  useEffect(() => {
    if (scrolled.current || !marked.current) return
    scrolled.current = true
    marked.current.scrollIntoView?.({ block: 'center' })
  }, [chunks])

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
        const linked = chunk.chunk_id === target
        return (
          <li
            key={chunk.chunk_id}
            id={`p-${chunk.chunk_id}`}
            ref={linked ? marked : undefined}
            aria-current={linked ? 'location' : undefined}
            className={`flex scroll-mt-24 flex-col gap-2.5 px-5 py-4 ${
              linked ? 'border-l-2 border-accent-graph bg-accent-graph/[0.07]' : on ? 'bg-accent-graph/5' : ''
            }`}
          >
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
              {/* A page is citable; the chunk id and a character offset are bookkeeping,
                  kept within reach of an operator's hover (`B-156`). */}
              <span
                title={`chunk ${chunk.chunk_id}${chunk.page_or_offset !== null ? ` · at ${chunk.page_or_offset}` : ''}`}
              >
                {citablePosition(pageUnit, chunk.page_or_offset) ?? ''}
              </span>
              {/* `P2-03`'s verdict. §12.5: a filtered near-duplicate and a
                  never-crawled page are indistinguishable otherwise, and only
                  one is worth investigating. */}
              {chunk.duplicate_of !== null ? (
                <span title={`duplicate of chunk ${chunk.duplicate_of}`}>
                  <DataChip>a copy of an earlier passage</DataChip>
                </span>
              ) : null}
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
