import { useCallback, useEffect, useState } from 'react'

import { GazetteerQueue } from './GazetteerQueue'
import {
  ApiError,
  decideGazetteerTerm,
  editGazetteerTerm,
  getGazetteerQueue,
  type GazetteerEntityType,
  type GazetteerQueue as Queue,
  type GazetteerState,
} from '../lib/api'

/**
 * Admin (task P6-13, spec §12.6).
 *
 * §12.6 splits the interface in two: Explore is where reading happens and Admin
 * is where configuration changes. Most of Admin is CRUD over tables that already
 * exist, and the spec says as much — "generated forms are fine; effort belongs
 * in Explore". So this screen is deliberately plain.
 *
 * **A closed Admin is explained, not hidden.** These are the only routes that
 * write anything, and the API refuses them outright unless callers are
 * identified or somebody has said this instance is not exposed. That returns
 * 503, and a 503 rendered as "something went wrong" would send whoever deployed
 * it looking for an outage — so the message the API sends is shown as written,
 * because it names the two environment variables that fix it.
 *
 * **The list is refetched after each decision, not patched in place.** A
 * verdict is computed against every other approved row, so approving one term
 * can change what the matcher does with a different one — a collision appears
 * the moment its other half is approved. Updating only the row that was clicked
 * would leave the screen stating something that stopped being true.
 */

type Phase = 'loading' | 'ready' | 'failed'

export function AdminPage() {
  const [state, setState] = useState<GazetteerState>('pending')
  const [queue, setQueue] = useState<Queue | null>(null)
  const [phase, setPhase] = useState<Phase>('loading')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  const load = useCallback(
    (next: GazetteerState, signal?: AbortSignal) => {
      setPhase('loading')
      return getGazetteerQueue({ state: next }, { signal })
        .then((body) => {
          setQueue(body)
          setPhase('ready')
          setError(null)
        })
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === 'AbortError') return
          setError(
            cause instanceof ApiError ? cause.message : 'The gazetteer queue could not be loaded.',
          )
          setPhase('failed')
        })
    },
    [],
  )

  useEffect(() => {
    const controller = new AbortController()
    void load(state, controller.signal)
    return () => controller.abort()
  }, [load, state])

  async function act(termId: number, run: () => Promise<unknown>) {
    setBusy(termId)
    try {
      await run()
      await load(state)
    } catch (cause: unknown) {
      setError(cause instanceof ApiError ? cause.message : 'That change was not saved.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      {error ? (
        // The API's own sentence, shown as written. For a closed Admin it names
        // the variables that open it, and replacing it with a generic line would
        // throw away the only actionable thing in the response.
        <p className="mb-6 border border-accent-attention bg-surface p-4 text-[length:var(--text-small)] text-accent-attention">
          {error}
        </p>
      ) : null}

      {phase === 'loading' && !queue ? (
        <p className="text-[length:var(--text-small)] text-text-muted">Loading.</p>
      ) : null}

      {queue ? (
        <GazetteerQueue
          rows={queue.rows}
          state={state}
          counts={{
            pending: queue.pending,
            approved: queue.approved,
            rejected: queue.rejected,
          }}
          busy={busy}
          onState={setState}
          onDecide={(termId, decision) =>
            void act(termId, () => decideGazetteerTerm(termId, decision))
          }
          onEdit={(termId, entityType: GazetteerEntityType) =>
            void act(termId, () => editGazetteerTerm(termId, { entity_type: entityType }))
          }
        />
      ) : null}
    </div>
  )
}
