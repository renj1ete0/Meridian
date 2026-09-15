import { useCallback, useEffect, useState } from 'react'

import { GazetteerQueue } from './GazetteerQueue'
import { TopicPanel } from './TopicPanel'
import {
  ApiError,
  decideGazetteerTerm,
  editGazetteerTerm,
  editTopic,
  getGazetteerQueue,
  getSteeringLog,
  getTopics,
  type GazetteerEntityType,
  type GazetteerQueue as Queue,
  type GazetteerState,
  type SteeringEntry,
  type TopicStatus,
  type Topics,
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
 * **Lists are refetched after each change, not patched in place.** Both
 * surfaces here have the same property: one row's state is computed from all
 * the others. A gazetteer verdict depends on every other approved term, and a
 * topic's share depends on every other active topic — so updating only the row
 * that was clicked would leave the screen stating something that stopped being
 * true the moment it was clicked.
 */

type Phase = 'loading' | 'ready' | 'failed'

type Section = 'gazetteer' | 'topics'

const SECTIONS: { key: Section; label: string }[] = [
  { key: 'gazetteer', label: 'Gazetteer' },
  { key: 'topics', label: 'Topics' },
]

export function AdminPage() {
  const [section, setSection] = useState<Section>('gazetteer')
  const [state, setState] = useState<GazetteerState>('pending')
  const [queue, setQueue] = useState<Queue | null>(null)
  const [phase, setPhase] = useState<Phase>('loading')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<number | null>(null)

  const [topics, setTopics] = useState<Topics | null>(null)
  const [entries, setEntries] = useState<readonly SteeringEntry[]>([])
  const [steering, setSteering] = useState<string | null>(null)

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

  const loadTopics = useCallback((signal?: AbortSignal) => {
    return Promise.all([getTopics({ signal }), getSteeringLog({ limit: 20 }, { signal })])
      .then(([vector, log]) => {
        setTopics(vector)
        setEntries(log.entries)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setError(cause instanceof ApiError ? cause.message : 'Steering could not be loaded.')
      })
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    if (section === 'gazetteer') void load(state, controller.signal)
    else void loadTopics(controller.signal)
    return () => controller.abort()
  }, [load, loadTopics, section, state])

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

  async function steer(topic: string, run: () => Promise<Topics>) {
    setSteering(topic)
    try {
      await run()
      await loadTopics()
    } catch (cause: unknown) {
      // A refused steering change names the bound that refused it. Replacing
      // that with a generic line throws away the only thing that says what to
      // change.
      setError(cause instanceof ApiError ? cause.message : 'That change was not saved.')
    } finally {
      setSteering(null)
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <nav className="mb-8 flex items-center gap-4">
        {SECTIONS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => setSection(key)}
            aria-pressed={section === key}
            className={`font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] ${
              section === key ? 'text-text' : 'text-text-muted'
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {error ? (
        // The API's own sentence, shown as written. For a closed Admin it names
        // the variables that open it, and replacing it with a generic line would
        // throw away the only actionable thing in the response.
        <p className="mb-6 border border-accent-attention bg-surface p-4 text-[length:var(--text-small)] text-accent-attention">
          {error}
        </p>
      ) : null}

      {section === 'topics' ? (
        topics ? (
          <TopicPanel
            rows={topics.rows}
            sumsTo={topics.sums_to}
            entries={entries}
            busy={steering}
            onWeight={(topic, weight) => void steer(topic, () => editTopic(topic, { weight }))}
            onStatus={(topic, status: TopicStatus) =>
              void steer(topic, () => editTopic(topic, { status }))
            }
          />
        ) : (
          <p className="text-[length:var(--text-small)] text-text-muted">Loading.</p>
        )
      ) : null}

      {section === 'gazetteer' && phase === 'loading' && !queue ? (
        <p className="text-[length:var(--text-small)] text-text-muted">Loading.</p>
      ) : null}

      {section === 'gazetteer' && queue ? (
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
