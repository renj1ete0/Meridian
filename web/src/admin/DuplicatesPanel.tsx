import { useState } from 'react'

import { ApiError } from '../lib/api'
import type { DuplicateDecided, DuplicatePair, DuplicateSide } from '../lib/duplicates'
import { readable } from '../lib/readable'
import { hrefForNode, hrefForSource, onInternalClick } from '../lib/route'
import { BUTTON_PRIMARY, BUTTON_SECONDARY, Card, LABEL, PageHeader } from './ui'

/**
 * Possible duplicates (`B-202`, spec §5.5): the two nodes side by side, on evidence rather
 * than name, and one decision each. A merge is reversible, so each decision offers undo.
 * See docs/features/knowledge-graph.md#deciding-a-possible-duplicate.
 */

export interface DuplicatesPanelProps {
  pairs: readonly DuplicatePair[]
  total: number
  onDecide: (pair: DuplicatePair, decision: 'merge' | 'keep') => Promise<DuplicateDecided>
  onUndo: (pair: DuplicatePair) => Promise<DuplicateDecided>
}

function Side({ side, role }: { side: DuplicateSide; role: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <span className={LABEL}>{role}</span>
      <a
        href={hrefForNode(side.entity_id)}
        onClick={onInternalClick(hrefForNode(side.entity_id))}
        className="text-[14px] font-semibold leading-snug text-text hover:text-accent-graph"
      >
        {side.canonical_name}
      </a>
      <span className="font-mono text-[10.5px] text-text-faint">
        {side.node_type.replaceAll('_', ' ')}
        {side.jurisdiction ? ` · ${side.jurisdiction}` : ''} · {side.links} {side.links === 1 ? 'link' : 'links'}
      </span>
      {side.aliases.length ? (
        <span className="text-[12px] text-text-muted">also: {side.aliases.slice(0, 6).join(', ')}</span>
      ) : null}
      {side.description ? <p className="text-[12.5px] leading-[1.5] text-text-muted">{side.description}</p> : null}
      {side.passages.length === 0 ? (
        <p className="font-mono text-[10.5px] text-text-faint">No passage behind it yet.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {side.passages.map((p) => (
            <li key={p.chunk_id} className="border-l border-line pl-2.5">
              <a
                href={hrefForSource(p.source_id, p.chunk_id)}
                onClick={onInternalClick(hrefForSource(p.source_id, p.chunk_id))}
                className="text-[12px] leading-[1.5] text-text-muted no-underline hover:text-text"
              >
                “{readable(p.text).trim()}…”
                <span className="block font-mono text-[10px] text-text-faint">
                  {p.title ?? `source ${p.source_id}`}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type State =
  { phase: 'open' } | { phase: 'busy' } | { phase: 'decided'; decision: string } | { phase: 'failed'; message: string }

function Pair({ pair, onDecide, onUndo }: { pair: DuplicatePair } & Pick<DuplicatesPanelProps, 'onDecide' | 'onUndo'>) {
  const [state, setState] = useState<State>({ phase: 'open' })

  async function run(work: () => Promise<DuplicateDecided>) {
    setState({ phase: 'busy' })
    try {
      const done = await work()
      setState(done.decision === 'reopened' ? { phase: 'open' } : { phase: 'decided', decision: done.decision })
    } catch (cause) {
      setState({ phase: 'failed', message: cause instanceof ApiError ? cause.message : 'That did not go through.' })
    }
  }

  return (
    <Card className="flex flex-col gap-4 px-[18px] py-4">
      <p className="text-[13px] text-text">
        A run read “{pair.mention}” and could not tell which it is. It made a separate node rather than risk a bad
        merge.
      </p>
      <div className="grid gap-5 md:grid-cols-2">
        <Side side={pair.created} role="Made by the run" />
        <Side side={pair.candidate} role="The node it might be" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {state.phase === 'decided' ? (
          <>
            <span role="status" className="font-mono text-[11px] text-accent-graph">
              {state.decision === 'merged' ? 'Merged into the existing node.' : 'Kept apart.'}
            </span>
            <button type="button" className={BUTTON_SECONDARY} onClick={() => void run(() => onUndo(pair))}>
              Undo
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className={BUTTON_PRIMARY}
              disabled={state.phase === 'busy'}
              onClick={() => void run(() => onDecide(pair, 'merge'))}
            >
              Same thing: merge
            </button>
            <button
              type="button"
              className={BUTTON_SECONDARY}
              disabled={state.phase === 'busy'}
              onClick={() => void run(() => onDecide(pair, 'keep'))}
            >
              Different: keep apart
            </button>
            <span className="font-mono text-[10.5px] text-text-faint">
              a merge moves its links and passages, keeps the old id as a redirect, and can be undone
            </span>
          </>
        )}
        {state.phase === 'failed' ? (
          <p role="alert" className="w-full font-mono text-[11px] text-accent-attention">
            Refused: {state.message}
          </p>
        ) : null}
      </div>
    </Card>
  )
}

export function DuplicatesPanel({ pairs, total, onDecide, onUndo }: DuplicatesPanelProps) {
  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Possible duplicates">
        Names a run could not place: one node or two? {total.toLocaleString()} waiting
        {pairs.length < total ? `, ${pairs.length} shown` : ''}.
      </PageHeader>
      {pairs.length === 0 ? (
        <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
          Nothing to decide. Resolution sends a pair here only when it cannot tell.
        </p>
      ) : (
        pairs.map((pair) => <Pair key={pair.notification_id} pair={pair} onDecide={onDecide} onUndo={onUndo} />)
      )}
    </section>
  )
}
