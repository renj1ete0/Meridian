import { useCallback, useEffect, useState } from 'react'

import { FetchPolicyPanel } from './FetchPolicyPanel'
import { FirstRunPanel } from './FirstRunPanel'
import { GazetteerQueue } from './GazetteerQueue'
import { TopicPanel } from './TopicPanel'
import {
  ApiError,
  actOnFetchPolicy,
  addSeed,
  decideGazetteerTerm,
  editGazetteerTerm,
  editTopic,
  getFetchPolicy,
  getFirstRun,
  getGazetteerQueue,
  getSteeringLog,
  getTopics,
  removeSeed,
  type FirstRun,
  type GazetteerEntityType,
  type GazetteerQueue as Queue,
  type DomainStatus,
  type FetchPolicyPage,
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

type Section = 'gazetteer' | 'topics' | 'domains' | 'seeds'

const SECTIONS: { key: Section; label: string }[] = [
  { key: 'gazetteer', label: 'Gazetteer' },
  { key: 'topics', label: 'Topics' },
  { key: 'domains', label: 'Domains' },
  // Last, because it is the one section that stops mattering. It is also the
  // first thing anybody needs on a fresh install, which is why `AdminPage`
  // opens on it when nothing has been crawled yet rather than leaving somebody
  // to find it.
  { key: 'seeds', label: 'Seeds' },
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

  const [policy, setPolicy] = useState<FetchPolicyPage | null>(null)
  const [domainStatus, setDomainStatus] = useState<DomainStatus | null>(null)

  const [run, setRun] = useState<FirstRun | null>(null)
  const [seedBusy, setSeedBusy] = useState<number | null>(null)
  const [adding, setAdding] = useState(false)

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

  const loadPolicy = useCallback((next: DomainStatus | null, signal?: AbortSignal) => {
    return getFetchPolicy({ status: next ?? undefined, limit: 200 }, { signal })
      .then((page) => {
        setPolicy(page)
        setError(null)
      })
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setError(cause instanceof ApiError ? cause.message : 'Domain policy could not be loaded.')
      })
  }, [])

  const loadRun = useCallback(async (signal?: AbortSignal) => {
    try {
      setRun(await getFirstRun({ signal }))
      setPhase('ready')
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') return
      setError(cause instanceof ApiError ? cause.message : 'Could not read the seed list.')
      setPhase('failed')
    }
  }, [])

  // A fresh install opens on Seeds rather than Gazetteer. The gazetteer queue
  // is empty until something has been crawled, so the default section on a new
  // machine is a screen with nothing on it and no hint that the thing worth
  // doing is elsewhere. Runs once: after that, Admin remembers nothing and the
  // person picks.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const first = await getFirstRun({ signal: controller.signal })
        setRun(first)
        if (first.is_first_run) setSection('seeds')
      } catch {
        // Not worth surfacing. This is a convenience, and a failure here
        // leaves Admin exactly where it would have been anyway.
      }
    })()
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    if (section === 'gazetteer') void load(state, controller.signal)
    else if (section === 'topics') void loadTopics(controller.signal)
    else if (section === 'seeds') void loadRun(controller.signal)
    else void loadPolicy(domainStatus, controller.signal)
    return () => controller.abort()
  }, [domainStatus, load, loadPolicy, loadTopics, section, state])

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

  // Shared by both write surfaces: one in-flight key, one error, and the list
  // refetched by the caller. Both have the same property — a row's state is
  // computed from the others — so patching in place would leave the screen
  // stating something that stopped being true the moment it was clicked.
  async function steer(key: string, run: () => Promise<unknown>) {
    setSteering(key)
    try {
      await run()
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

      {section === 'seeds' ? (
        run ? (
          <FirstRunPanel
            run={run}
            busy={seedBusy}
            adding={adding}
            onAdd={(url, kind, topic) =>
              void (async () => {
                setAdding(true)
                setError(null)
                try {
                  await addSeed({ url_or_query: url, task_type: kind, topic })
                  setRun(await getFirstRun())
                } catch (cause) {
                  setError(cause instanceof ApiError ? cause.message : 'That seed was not added.')
                } finally {
                  setAdding(false)
                }
              })()
            }
            onRemove={(taskId) =>
              void (async () => {
                setSeedBusy(taskId)
                setError(null)
                try {
                  await removeSeed(taskId)
                  setRun(await getFirstRun())
                } catch (cause) {
                  setError(
                    cause instanceof ApiError ? cause.message : 'That seed was not removed.',
                  )
                } finally {
                  setSeedBusy(null)
                }
              })()
            }
          />
        ) : null
      ) : section === 'domains' ? (
        policy ? (
          <FetchPolicyPanel
            rows={policy.rows}
            counts={{ active: policy.active, paused: policy.paused, blocked: policy.blocked }}
            status={domainStatus}
            busy={steering}
            onStatusFilter={setDomainStatus}
            onUnblock={(domain) =>
              void steer(domain, async () => {
                await actOnFetchPolicy(domain, 'unblock')
                await loadPolicy(domainStatus)
              })
            }
            onForgetRender={(domain) =>
              void steer(domain, async () => {
                await actOnFetchPolicy(domain, 'forget-render')
                await loadPolicy(domainStatus)
              })
            }
          />
        ) : (
          <p className="text-[length:var(--text-small)] text-text-muted">Loading.</p>
        )
      ) : null}

      {section === 'topics' ? (
        topics ? (
          <TopicPanel
            rows={topics.rows}
            sumsTo={topics.sums_to}
            entries={entries}
            busy={steering}
            onWeight={(topic, weight) =>
              void steer(topic, async () => {
                await editTopic(topic, { weight })
                await loadTopics()
              })
            }
            onStatus={(topic, status: TopicStatus) =>
              void steer(topic, async () => {
                await editTopic(topic, { status })
                await loadTopics()
              })
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
