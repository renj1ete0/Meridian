import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'

import { ApiError } from '../lib/api'
import { answerParts, askGraph, getChatThread, getChatThreads, type ChatMessage, type ChatThread } from '../lib/chat'
import { hrefForNode, hrefForSource, onInternalClick } from '../lib/route'
import { clockOf, dayOf, shortDayOf } from '../lib/time'

/**
 * "Ask the graph" (tasks P6-06, P6-07) — `SynthesisPanel.dc.html`, `SynthesisToggle.dc.html`:
 * a toggle and an opaque panel; the page's selection travels as removable context, and
 * answers cite server-checked nodes and passages. See docs/features/web-app.md#the-ask-panel.
 */

// --------------------------------------------------------------------------
// The selection a page binds to the panel

export interface AskSubject {
  entityId: number
  name: string
}

interface AskContextValue {
  subjects: readonly AskSubject[]
  setSubjects: (subjects: readonly AskSubject[]) => void
}

const AskContext = createContext<AskContextValue>({ subjects: [], setSubjects: () => {} })

export function AskContextProvider({ children }: { children: React.ReactNode }) {
  const [subjects, setSubjects] = useState<readonly AskSubject[]>([])
  const value = useMemo(() => ({ subjects, setSubjects }), [subjects])
  return <AskContext.Provider value={value}>{children}</AskContext.Provider>
}

/** A page's selection, bound for as long as the page shows it. */
export function useAskSubjects(subjects: readonly AskSubject[]): void {
  const { setSubjects } = useContext(AskContext)
  const key = subjects.map((s) => s.entityId).join(',')
  useEffect(() => {
    setSubjects(subjects)
    return () => setSubjects([])
    // The key, not the array: a page re-rendering the same selection must not
    // reset what the reader removed from the context.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, setSubjects])
}

// --------------------------------------------------------------------------

/** The mock's own glyph: a speech box with a tail. Not one of §7's set. */
function AskGlyph({ size = 17, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden className={className}>
      <path
        d="M3.5 5.5 h17 v10.5 h-11 l-4.4 3.6 v-3.6 h-1.6 z"
        stroke="currentColor"
        strokeWidth={1.6}
        strokeLinejoin="round"
      />
      <path d="M7.6 9.4 h8.8 M7.6 12.4 h5.6" stroke="currentColor" strokeWidth={1.6} strokeLinecap="round" />
    </svg>
  )
}

const LABEL = 'font-mono text-[10px] uppercase tracking-[var(--tracking-label)] text-text-faint'

function clock(iso: string): string {
  return clockOf(iso)
}

function day(iso: string, now = new Date()): string {
  return dayOf(iso) === dayOf(now) ? 'Today' : shortDayOf(iso)
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

/** How many earlier questions show before "Show all". */
export const EARLIER_SHOWN = 3

export function AskPanel({ initiallyOpen = false }: { initiallyOpen?: boolean }) {
  const { subjects } = useContext(AskContext)
  const [open, setOpen] = useState(initiallyOpen)
  const [threads, setThreads] = useState<ChatThread[]>([])
  const [total, setTotal] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const [earlierOpen, setEarlierOpen] = useState(true)
  const [threadId, setThreadId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [draft, setDraft] = useState('')
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [removed, setRemoved] = useState<Set<number>>(new Set())
  const thread = useRef<HTMLDivElement>(null)

  const context = subjects.filter((s) => !removed.has(s.entityId))
  useEffect(() => setRemoved(new Set()), [subjects])

  const loadThreads = useCallback(() => {
    getChatThreads(20)
      .then((body) => {
        setThreads(body.threads)
        setTotal(body.total)
      })
      .catch(() => {
        // The history is a convenience beside the question box; a failure to
        // list it must not stop anyone asking.
      })
  }, [])

  useEffect(() => {
    if (open) loadThreads()
  }, [open, loadThreads])

  useEffect(() => {
    thread.current?.scrollTo?.({ top: thread.current.scrollHeight })
  }, [messages, asking])

  function openThread(id: number) {
    setError(null)
    getChatThread(id)
      .then((detail) => {
        setThreadId(detail.thread.thread_id)
        setMessages(detail.messages)
      })
      .catch((cause: unknown) => setError(messageOf(cause, 'That conversation did not load.')))
  }

  function startNew() {
    setThreadId(null)
    setMessages([])
    setError(null)
    setDraft('')
  }

  function submit() {
    const question = draft.trim()
    if (!question || asking) return
    setAsking(true)
    setError(null)
    askGraph(question, { threadId, contextEntityIds: context.map((s) => s.entityId) })
      .then((exchange) => {
        setThreadId(exchange.thread.thread_id)
        setMessages((m) => [...m, exchange.question, exchange.answer])
        setDraft('')
        loadThreads()
      })
      .catch((cause: unknown) => setError(messageOf(cause, 'The question could not be asked.')))
      .finally(() => setAsking(false))
  }

  if (!open) {
    return (
      <button
        type="button"
        aria-label="Ask the graph"
        title="Ask the graph"
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-40 flex h-11 w-11 items-center justify-center rounded-[3px] bg-accent-graph/[0.92] text-ground-deep backdrop-blur-[8px] hover:bg-accent-graph"
      >
        <AskGlyph size={20} />
      </button>
    )
  }

  const earlier = showAll ? threads : threads.slice(0, EARLIER_SHOWN)

  return (
    <aside
      aria-label="Ask the graph"
      className="fixed bottom-0 right-0 top-[54px] z-40 flex w-full max-w-[420px] flex-col border-l border-line-strong bg-surface shadow-[-18px_0_40px_rgba(4,8,18,0.55)]"
    >
      <div className="flex shrink-0 items-center gap-3 border-b border-line px-[18px] py-[15px]">
        <AskGlyph className="shrink-0 text-accent-graph" />
        <h2 className="grow text-[15px] font-semibold text-text">Ask the graph</h2>
        <button type="button" onClick={startNew} className="font-mono text-[11px] text-accent-graph hover:underline">
          New question
        </button>
        <button
          type="button"
          aria-label="Close"
          onClick={() => setOpen(false)}
          className="text-[18px] leading-none text-text-muted hover:text-text"
        >
          ×
        </button>
      </div>

      {threads.length > 0 ? (
        <section className="shrink-0 border-b border-line px-[18px] py-3">
          <button
            type="button"
            aria-expanded={earlierOpen}
            onClick={() => setEarlierOpen((v) => !v)}
            className={`flex w-full items-center gap-2 ${LABEL}`}
          >
            <span aria-hidden>{earlierOpen ? '⌄' : '›'}</span>
            <span className="grow text-left">Earlier questions</span>
            <span className="tabular-nums">{total}</span>
          </button>
          {earlierOpen ? (
            <ul className="mt-2 flex max-h-[180px] flex-col overflow-y-auto">
              {earlier.map((t) => (
                <li key={t.thread_id}>
                  <button
                    type="button"
                    onClick={() => openThread(t.thread_id)}
                    className={`flex w-full items-baseline gap-3 py-1 text-left text-[13px] hover:text-accent-graph ${
                      t.thread_id === threadId ? 'text-accent-graph' : 'text-text'
                    }`}
                  >
                    <span className="grow truncate">{t.title}</span>
                    <span className="shrink-0 font-mono text-[10.5px] text-text-faint">{day(t.updated_at)}</span>
                  </button>
                </li>
              ))}
              {!showAll && total > EARLIER_SHOWN ? (
                <li>
                  <button
                    type="button"
                    onClick={() => setShowAll(true)}
                    className="mt-1 font-mono text-[11px] text-accent-graph hover:underline"
                  >
                    Show all {total} →
                  </button>
                </li>
              ) : null}
            </ul>
          ) : null}
        </section>
      ) : null}

      <div ref={thread} className="flex min-h-0 grow flex-col gap-4 overflow-y-auto px-[18px] py-4">
        {messages.length === 0 && !asking ? (
          <p className="text-[13px] leading-[1.55] text-text-muted">
            Ask about what you are looking at, or anything in the graph. Answers come only from passages the corpus
            holds, and cite them.
          </p>
        ) : null}
        {messages.map((m) =>
          m.role === 'user' ? <Question key={m.message_id} message={m} /> : <Answer key={m.message_id} message={m} />,
        )}
        {asking ? (
          <p role="status" className="font-mono text-[11px] text-text-faint">
            Reading the corpus…
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-[13px] text-accent-attention">
            {error}
          </p>
        ) : null}
      </div>

      <form
        className="shrink-0 border-t border-line px-[18px] py-3"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        {context.length > 0 ? (
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className={LABEL}>Context</span>
            {context.map((s) => (
              <span
                key={s.entityId}
                className="inline-flex items-center gap-1.5 border border-line-strong px-2 py-0.5 font-mono text-[10.5px] text-text-muted"
              >
                {s.name}
                <button
                  type="button"
                  aria-label={`Remove ${s.name} from the context`}
                  onClick={() => setRemoved((r) => new Set(r).add(s.entityId))}
                  className="text-text-faint hover:text-text"
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <div className="border border-line-strong bg-ground px-3 py-2.5">
          <textarea
            aria-label="Your question"
            value={draft}
            rows={2}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                submit()
              }
            }}
            placeholder="Ask about the selection, or anything in the graph"
            className="w-full resize-none bg-transparent text-[13px] text-text placeholder:text-text-faint focus:outline-none"
          />
          <div className="mt-1 flex items-center justify-between gap-3">
            <span className="font-mono text-[10px] text-text-faint">
              answers from retrieved passages · citations checked
            </span>
            <button
              type="submit"
              disabled={asking || draft.trim().length === 0}
              className="bg-accent-graph px-3 py-1 font-mono text-[11px] text-ground-deep disabled:opacity-40"
            >
              Ask
            </button>
          </div>
        </div>
      </form>
    </aside>
  )
}

function Question({ message }: { message: ChatMessage }) {
  return (
    <div className="border border-line bg-surface-raised px-3.5 py-2.5">
      <div className={LABEL}>You · {clock(message.created_at)}</div>
      <p className="mt-1 text-[13.5px] leading-[1.5] text-text">{message.text}</p>
    </div>
  )
}

function Answer({ message }: { message: ChatMessage }) {
  if (message.error) {
    return (
      <div>
        <div className={LABEL}>Meridian</div>
        <p className="mt-1.5 text-[13px] leading-[1.55] text-accent-attention">{message.error}</p>
      </div>
    )
  }
  const nodes = message.nodes ?? []
  const contested = nodes.filter((n) => n.contested).length
  const paragraphs = message.text.split(/\n{2,}/)
  return (
    <div className="flex flex-col gap-3">
      <div className={LABEL}>Meridian</div>
      {paragraphs.map((paragraph, i) => (
        <p key={i} className="text-[14px] leading-[1.6] text-text">
          {answerParts(paragraph, message.citations).map((part, j) =>
            part.kind === 'text' ? (
              <span key={j}>{part.text}</span>
            ) : (
              <a
                key={j}
                href={hrefForSource(part.citation.source_id)}
                onClick={onInternalClick(hrefForSource(part.citation.source_id))}
                title={`${part.citation.title ?? part.citation.url} · ${part.citation.source_tier.replace('_', ' ')}`}
                className="mx-[1px] align-super font-mono text-[10px] text-accent-graph no-underline hover:underline"
              >
                [{part.citation.n}]
              </a>
            ),
          )}
        </p>
      ))}
      {nodes.length > 0 ? (
        <ul className="flex flex-wrap gap-2" aria-label="Nodes cited">
          {nodes.map((n) => (
            <li key={n.entity_id}>
              <a
                href={hrefForNode(n.entity_id)}
                onClick={onInternalClick(hrefForNode(n.entity_id))}
                className={`inline-block border px-2 py-0.5 font-mono text-[10.5px] no-underline ${
                  n.contested
                    ? 'border-accent-attention/60 bg-accent-attention-deep/15 text-accent-attention'
                    : 'border-line-strong text-text-muted hover:text-text'
                }`}
              >
                {n.name}
                {n.contested ? <sup>†</sup> : null}
              </a>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="border-t border-line pt-2 font-mono text-[10.5px] text-text-faint">
        {nodes.length} node{nodes.length === 1 ? '' : 's'} cited
        {contested > 0 ? (
          <>
            {' '}
            · {contested} contested<span className="text-accent-attention">†</span>
          </>
        ) : null}{' '}
        · {(message.citations ?? []).length} passage{(message.citations ?? []).length === 1 ? '' : 's'}
        {message.model ? ` · ${message.model}` : ''}
      </p>
    </div>
  )
}
