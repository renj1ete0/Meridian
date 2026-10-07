import { useEffect, useState } from 'react'

import { Loading, PageHeader } from '../admin/ui'
import { ApiError } from '../lib/api'
import { hrefForNode, onInternalClick } from '../lib/route'
import { DAGGER } from '../ui/Contested'
import { getContested, type ContestedList, type ContestedSide } from './graph/api'
import { excerpt, LBL, SourceLine } from './NodePanel'

/**
 * Contested (task P6-10): every disagreement once, newest first, both sides drawn alike;
 * the page resolves nothing. See docs/features/web-app.md#contested.
 */

type Load = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; body: ContestedList }

export function claimOf(side: ContestedSide): string {
  const relation = side.relation_type.replaceAll('_', ' ')
  return `${side.from_name} ${relation} ${side.to_name}`
}

/** What a capped list says about what it left out; nothing when it left nothing. */
export function capLine(body: ContestedList): string | null {
  const left = body.total - body.pairs.length
  return left > 0 ? `${body.pairs.length} of ${body.total} shown; ${left} more not listed.` : null
}

export function ContestedPage() {
  const [load, setLoad] = useState<Load>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    getContested(undefined, { signal: controller.signal })
      .then((body) => setLoad({ status: 'ready', body }))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setLoad({
          status: 'error',
          message: cause instanceof ApiError ? cause.message : 'The contested list could not be read.',
        })
      })
    return () => controller.abort()
  }, [])

  return (
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6 px-4 pb-24 pt-8 sm:px-6">
      <PageHeader title="Contested">
        Claims sources disagree about, newest first. Both sides are kept; neither is resolved.
      </PageHeader>

      {load.status === 'loading' ? <Loading what="the contested claims" /> : null}
      {load.status === 'error' ? (
        <p role="alert" className="text-[13px] text-accent-attention">
          The contested list is unavailable: {load.message}
        </p>
      ) : null}

      {load.status === 'ready' && load.body.pairs.length === 0 ? (
        <p className="text-[13px] text-text-muted">
          No two sources disagree yet. A disagreement appears here once two stated links about the same claim are marked
          as contradicting each other.
        </p>
      ) : null}

      {load.status === 'ready' && load.body.pairs.length > 0 ? (
        <>
          <p className="font-mono text-[11px] text-text-faint">
            {load.body.total.toLocaleString('en')} {load.body.total === 1 ? 'disagreement' : 'disagreements'}
            {capLine(load.body) ? ` · ${capLine(load.body)}` : ''}
          </p>
          <ol className="flex flex-col gap-4">
            {load.body.pairs.map((pair) => (
              <li
                key={`${pair.ours.edge_id}:${pair.theirs.edge_id}`}
                className="border border-accent-attention-deep bg-surface-raised"
              >
                <div className="grid grid-cols-1 gap-0 md:grid-cols-2">
                  {[pair.ours, pair.theirs].map((side, i) => (
                    <Side key={side.edge_id} side={side} divided={i === 1} />
                  ))}
                </div>
              </li>
            ))}
          </ol>
        </>
      ) : null}
    </div>
  )
}

function NodeLink({ id, name }: { id: number; name: string }) {
  return (
    <a
      href={hrefForNode(id)}
      onClick={onInternalClick(hrefForNode(id))}
      className="text-text underline decoration-line-strong underline-offset-2"
    >
      {name}
    </a>
  )
}

function Side({ side, divided }: { side: ContestedSide; divided: boolean }) {
  return (
    <section
      aria-label={claimOf(side)}
      className={`flex min-w-0 flex-col gap-2 px-5 py-4 ${divided ? 'border-t border-accent-attention-deep md:border-l md:border-t-0' : ''}`}
    >
      <span className={`${LBL} !text-accent-attention`}>
        {DAGGER} {side.stance ?? 'no stance recorded'}
        {side.certainty ? ` · ${side.certainty}` : ''}
      </span>
      <p className="text-[14px] leading-[1.5] text-text">
        <NodeLink id={side.from_entity_id} name={side.from_name} />{' '}
        <span className="text-text-muted">{side.relation_type.replaceAll('_', ' ')}</span>{' '}
        <NodeLink id={side.to_entity_id} name={side.to_name} />
      </p>
      {side.evidence ? (
        <>
          <blockquote className="font-mono text-[11.5px] italic leading-[1.6] text-text-muted">
            “{excerpt(side.evidence.text, 280)}”
          </blockquote>
          <SourceLine hit={side.evidence} />
        </>
      ) : (
        <span className="text-[12px] text-text-faint">No passage backs this link.</span>
      )}
    </section>
  )
}
