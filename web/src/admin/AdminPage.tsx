import { useCallback, useEffect, useState } from 'react'

import { AgentsPanel } from './AgentsPanel'
import type { BoostChange } from './BoostsTable'
import { CrawlHealthPanel } from './CrawlHealthPanel'
import { AssistantAccessPanel } from './AssistantAccessPanel'
import { DisplayPanel } from './DisplayPanel'
import { FetchPolicyPanel } from './FetchPolicyPanel'
import { FirstRunPanel } from './FirstRunPanel'
import { GazetteerQueue, PAGE_SIZE } from './GazetteerQueue'
import { PinsPanel } from './PinsPanel'
import { ProposalsPanel } from './ProposalsPanel'
import { RunsPanel } from './RunsPanel'
import { DEFAULT_SECTION, SECTIONS, hrefForSection, useAdminTheme, usePathSection, type Section } from './sections'
import { SteeringRail } from './SteeringRail'
import { AddTopicDialog, ArchiveDialog, PREVIEW_DEBOUNCE_MS } from './TopicDialogs'
import { TopicPanel, type Draft } from './TopicPanel'
import { LABEL, Loading, PageHeader } from './ui'
import {
  ApiError,
  actOnFetchPolicy,
  addSeed,
  addTopic,
  decideGazetteerTerm,
  decideGazetteerTerms,
  editAgent,
  editGazetteerTerm,
  editTopic,
  getAgents,
  getCrawlHealth,
  getFetchPolicy,
  getFirstRun,
  getGazetteerQueue,
  getRuns,
  getSteeringLog,
  getTopics,
  previewAddTopic,
  previewEditTopic,
  removeSeed,
  type Agents,
  type CrawlHealth,
  type DomainStatus,
  type FetchPolicyPage,
  type FirstRun,
  type GazetteerEntityType,
  type GazetteerQueue as Queue,
  type GazetteerState,
  type RunRow,
  type Runs,
  type SteeringEntry,
  type TopicStatus,
  type Topics,
} from '../lib/api'
import { acceptProposal, getProposals, rejectProposal, type Proposals } from '../lib/proposals'
import { navigate, onInternalClick } from '../lib/route'

/**
 * Admin (tasks P6-13, P6-28; spec §12.6; `AdminLight` mock): one plain, dense language
 * across sections, on paper by default. A closed Admin shows the API's message as written,
 * and lists are refetched after each change. See docs/features/web-app.md#admin.
 */

/** How often the crawl-health panel refetches while it is open (`P6-25`).
 * Its finest grain is a minute of silence, so faster than this redraws the
 * same screen; much slower and a stall that began while somebody watched
 * goes unshown for minutes after it could have been. */
export const HEALTH_REFRESH_MS = 30_000

/** Sections that carry the steering rail. The rail answers "what moved, and
 * what did it steer", which is a question about these pages only; on a table
 * of thousands of terms it would be width taken from the table. */
const WITH_RAIL: readonly Section[] = ['topics', 'boosts', 'proposals']

/** Fetch-policy rows per page — the API's ceiling. */
const DOMAIN_PAGE = 200

function message(cause: unknown, fallback: string): string | null {
  if (cause instanceof DOMException && cause.name === 'AbortError') return null
  return cause instanceof ApiError ? cause.message : fallback
}

export function AdminPage() {
  const chosen = usePathSection()
  const theme = useAdminTheme()
  const [firstRun, setFirstRun] = useState(false)
  const section: Section = chosen ?? (firstRun ? 'seeds' : DEFAULT_SECTION)

  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [state, setState] = useState<GazetteerState>('pending')
  const [offset, setOffset] = useState(0)
  const [queue, setQueue] = useState<Queue | null>(null)
  const [busy, setBusy] = useState<number | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)

  const [topics, setTopics] = useState<Topics | null>(null)
  const [entries, setEntries] = useState<readonly SteeringEntry[]>([])
  const [recent, setRecent] = useState<readonly RunRow[] | null>(null)
  const [steering, setSteering] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [preview, setPreview] = useState<{ key: string; topics: Topics } | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [archiving, setArchiving] = useState<string | null>(null)

  const [policy, setPolicy] = useState<FetchPolicyPage | null>(null)
  const [domainStatus, setDomainStatus] = useState<DomainStatus | null>(null)
  // `?q=` opens Fetch policy on one domain, as Crawl health links to it (`B-197`).
  const [domainQuery, setDomainQuery] = useState(() => new URLSearchParams(window.location.search).get('q') ?? '')
  const [domainSearch, setDomainSearch] = useState(() => new URLSearchParams(window.location.search).get('q') ?? '')
  const [domainOffset, setDomainOffset] = useState(0)

  const [agents, setAgents] = useState<Agents | null>(null)
  const [runs, setRuns] = useState<Runs | null>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [health, setHealth] = useState<CrawlHealth | null>(null)
  const [proposals, setProposals] = useState<Proposals | null>(null)
  const [proposalBusy, setProposalBusy] = useState<number | null>(null)

  const [run, setRun] = useState<FirstRun | null>(null)
  const [seedBusy, setSeedBusy] = useState<number | null>(null)
  const [seedAdding, setSeedAdding] = useState(false)

  const load = useCallback((next: GazetteerState, from: number, signal?: AbortSignal) => {
    return getGazetteerQueue({ state: next, limit: PAGE_SIZE, offset: from }, { signal })
      .then((body) => {
        setQueue(body)
        setError(null)
      })
      .catch((cause: unknown) => {
        const text = message(cause, 'The gazetteer queue could not be loaded.')
        if (text) setError(text)
      })
  }, [])

  const loadTopics = useCallback((signal?: AbortSignal) => {
    return Promise.all([getTopics({ signal }), getSteeringLog({ limit: 60 }, { signal })])
      .then(([vector, log]) => {
        setTopics(vector)
        setEntries(log.entries)
        setError(null)
      })
      .catch((cause: unknown) => {
        const text = message(cause, 'Steering could not be loaded.')
        if (text) setError(text)
      })
  }, [])

  const loadRecent = useCallback((signal?: AbortSignal) => {
    // The rail's runs. A failure leaves the rail saying it is loading rather
    // than replacing the steering page's own error with one about a sidebar.
    return getRuns({ limit: 4 }, { signal })
      .then((body) => setRecent(body.rows))
      .catch(() => undefined)
  }, [])

  const loadPolicy = useCallback((next: DomainStatus | null, q: string, from: number, signal?: AbortSignal) => {
    return getFetchPolicy(
      { status: next ?? undefined, q: q || undefined, limit: DOMAIN_PAGE, offset: from },
      { signal },
    )
      .then((page) => {
        setPolicy(page)
        setError(null)
      })
      .catch((cause: unknown) => {
        const text = message(cause, 'Domain policy could not be loaded.')
        if (text) setError(text)
      })
  }, [])

  const loadAgents = useCallback((signal?: AbortSignal) => {
    return getAgents({ signal })
      .then((body) => {
        setAgents(body)
        setError(null)
      })
      .catch((cause: unknown) => {
        const text = message(cause, 'The registry could not be loaded.')
        if (text) setError(text)
      })
  }, [])

  const loadRuns = useCallback((signal?: AbortSignal) => {
    return getRuns({ limit: 25 }, { signal })
      .then((body) => {
        setRuns(body)
        setError(null)
      })
      .catch((cause: unknown) => {
        const text = message(cause, 'Run history could not be loaded.')
        if (text) setError(text)
      })
  }, [])

  const loadHealth = useCallback((signal?: AbortSignal) => {
    return getCrawlHealth({ signal })
      .then((body) => {
        setHealth(body)
        setError(null)
      })
      .catch((cause: unknown) => {
        // The last good reading stays on screen under the error. A refresh
        // that failed says nothing about the crawl, and blanking the panel
        // would make an unreachable API look like a crawl with nothing to show.
        const text = message(cause, 'Crawl health could not be loaded.')
        if (text) setError(text)
      })
  }, [])

  const loadProposals = useCallback((signal?: AbortSignal) => {
    // With the steering log, because the rail beside the proposals is where a
    // decision made here shows up.
    return Promise.all([getProposals({ signal }), getSteeringLog({ limit: 60 }, { signal })])
      .then(([body, log]) => {
        setProposals(body)
        setEntries(log.entries)
        setError(null)
      })
      .catch((cause: unknown) => {
        const text = message(cause, 'Proposals could not be loaded.')
        if (text) setError(text)
      })
  }, [])

  const loadRun = useCallback(async (signal?: AbortSignal) => {
    try {
      setRun(await getFirstRun({ signal }))
    } catch (cause) {
      const text = message(cause, 'Could not read the seed list.')
      if (text) setError(text)
    }
  }, [])

  // A fresh install opens on Seeds rather than the gazetteer. The queue is
  // empty until something has been crawled, so the default section on a new
  // machine is a screen with nothing on it and no hint that the thing worth
  // doing is elsewhere. Only for bare `/admin`: a link to a section is a
  // choice, and is kept.
  useEffect(() => {
    const controller = new AbortController()
    void (async () => {
      try {
        const first = await getFirstRun({ signal: controller.signal })
        setRun(first)
        setFirstRun(first.is_first_run)
      } catch {
        // Not worth surfacing. This is a convenience, and a failure here
        // leaves Admin exactly where it would have been anyway.
      }
    })()
    return () => controller.abort()
  }, [])

  // A domain search is sent once typing settles, and starts from the first page.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDomainSearch(domainQuery.trim())
      setDomainOffset(0)
    }, PREVIEW_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [domainQuery])

  // Leaving a section drops any sentence about it. A staged weight change is kept: it was
  // dropped silently, and nothing about another section moves the base it was previewed
  // against (`B-199`); the page asks before it is closed with one staged.
  useEffect(() => {
    setError(null)
    setNotice(null)
  }, [section])

  useEffect(() => {
    if (!draft) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [draft])

  useEffect(() => {
    const controller = new AbortController()
    const signal = controller.signal
    if (section === 'gazetteer') void load(state, offset, signal)
    else if (section === 'topics' || section === 'boosts') {
      void loadTopics(signal)
      void loadRecent(signal)
    } else if (section === 'proposals') {
      void loadProposals(signal)
      void loadRecent(signal)
    } else if (section === 'seeds') void loadRun(signal)
    else if (section === 'agents') void loadAgents(signal)
    else if (section === 'runs') void loadRuns(signal)
    else if (section === 'health') void loadHealth(signal)
    else if (section === 'domains') void loadPolicy(domainStatus, domainSearch, domainOffset, signal)
    return () => controller.abort()
  }, [
    domainOffset,
    domainSearch,
    domainStatus,
    load,
    loadAgents,
    loadHealth,
    loadPolicy,
    loadProposals,
    loadRecent,
    loadRun,
    loadRuns,
    loadTopics,
    offset,
    section,
    state,
  ])

  // Crawl health refreshes itself, and only while it can be seen: the panel is
  // for watching a long run, and a background tab polling all night is load
  // with nobody reading it. Returning to the tab refreshes at once rather than
  // showing a reading up to half a minute stale.
  useEffect(() => {
    if (section !== 'health') return
    const controller = new AbortController()
    const tick = () => {
      if (document.visibilityState === 'visible') void loadHealth(controller.signal)
    }
    const timer = window.setInterval(tick, HEALTH_REFRESH_MS)
    document.addEventListener('visibilitychange', tick)
    return () => {
      controller.abort()
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', tick)
    }
  }, [loadHealth, section])

  // A staged weight is previewed by the server once the slider settles. The
  // preview is keyed by the draft it answers, so a slow answer to an earlier
  // position can never be applied as if it were the current one.
  const draftKey = draft ? `${draft.topic}=${draft.weight}` : null
  useEffect(() => {
    setRefusal(null)
    if (!draft) {
      setPreview(null)
      return
    }
    const controller = new AbortController()
    const key = `${draft.topic}=${draft.weight}`
    const timer = window.setTimeout(() => {
      previewEditTopic(draft.topic, { weight: draft.weight }, { signal: controller.signal })
        .then((body) => setPreview({ key, topics: body }))
        .catch((cause: unknown) => {
          const text = message(cause, 'That weight could not be previewed.')
          if (text) setRefusal(text)
        })
    }, PREVIEW_DEBOUNCE_MS)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
  }, [draft])
  const current = preview && preview.key === draftKey ? preview.topics : null

  async function act(termId: number, work: () => Promise<unknown>) {
    setBusy(termId)
    setNotice(null)
    try {
      await work()
      await load(state, offset)
    } catch (cause: unknown) {
      setError(cause instanceof ApiError ? cause.message : 'That change was not saved.')
    } finally {
      setBusy(null)
    }
  }

  async function bulk(termIds: number[], decision: 'approve' | 'reject' | 'restore') {
    setBulkBusy(true)
    setNotice(null)
    try {
      const { rows } = await decideGazetteerTerms(termIds, decision)
      const verb = decision === 'approve' ? 'Approved' : decision === 'reject' ? 'Turned down' : 'Put back'
      // Collisions are reported here as well as on the rows, because in the
      // waiting view the approved rows leave the page — and the collision
      // with them.
      const collided = rows.filter((row) => row.withheld_reason === 'collision').length
      setNotice(
        `${verb} ${rows.length}.` +
          (collided
            ? ` ${collided} claim wording another term already claims, so they match nothing yet; they are under Approved.`
            : ''),
      )
      await load(state, offset)
    } catch (cause: unknown) {
      setError(cause instanceof ApiError ? cause.message : 'Those decisions were not saved.')
    } finally {
      setBulkBusy(false)
    }
  }

  // Shared by every steering write: one in-flight key, one error, and the list
  // refetched after.
  async function steer(key: string, work: () => Promise<unknown>) {
    setSteering(key)
    try {
      await work()
      return true
    } catch (cause: unknown) {
      // A refused steering change names the bound that refused it. Replacing
      // that with a generic line throws away the only thing that says what to
      // change.
      setError(cause instanceof ApiError ? cause.message : 'That change was not saved.')
      return false
    } finally {
      setSteering(null)
    }
  }

  // A decision on a proposal. The list is refetched after, not patched: a
  // proposal that was accepted can supersede nothing else, but a refusal from
  // the server (someone else decided it first) must show the list as it is.
  async function decide(proposalId: number, done: string, work: () => Promise<unknown>) {
    setProposalBusy(proposalId)
    setNotice(null)
    setError(null)
    let refusal: string | null = null
    try {
      await work()
    } catch (cause: unknown) {
      refusal = cause instanceof ApiError ? cause.message : 'That decision was not saved.'
    }
    // Refetched first, then the sentence: a successful reload clears the
    // error line, and the refusal is the one thing that must survive it.
    await loadProposals()
    setProposalBusy(null)
    if (refusal) setError(refusal)
    else setNotice(done)
  }

  const steerTopic = (topic: string, work: () => Promise<unknown>) => {
    // Any other change moves the base a staged weight was previewed against.
    setDraft(null)
    void steer(topic, async () => {
      await work()
      await loadTopics()
    })
  }

  const onStatus = (topic: string, status: TopicStatus) => steerTopic(topic, () => editTopic(topic, { status }))
  const onPinned = (topic: string, pinned: boolean) => steerTopic(topic, () => editTopic(topic, { pinned }))
  const onDescribe = (topic: string, description: string | null) =>
    steerTopic(topic, () => editTopic(topic, { description }))
  const onBoost = (topic: string, change: BoostChange) => steerTopic(topic, () => editTopic(topic, change))

  const steeringPage = WITH_RAIL.includes(section)

  return (
    <div
      data-theme={theme}
      data-admin-section={section}
      className="flex min-h-[calc(100vh-54px)] flex-col bg-ground text-text lg:flex-row"
    >
      {/* Below lg, one picker: a strip that scrolled sideways hid eight of twelve sections
          with no sign they were there (`B-199`). */}
      <label className="flex items-center gap-2 border-b border-line px-4 py-2.5 lg:hidden">
        <span className={LABEL}>Section</span>
        <select
          aria-label="Admin section"
          value={section}
          onChange={(e) => navigate(hrefForSection(e.target.value as Section))}
          className="h-8 min-w-0 flex-1 border border-line-strong bg-surface-raised px-2 text-[13px] text-text"
        >
          {(['steering', 'system'] as const).map((group) => (
            <optgroup key={group} label={group}>
              {SECTIONS.filter((s) => s.group === group).map((def) => (
                <option key={def.key} value={def.key}>
                  {def.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>

      <nav
        aria-label="Admin sections"
        className="hidden shrink-0 lg:flex lg:w-[196px] lg:flex-col lg:gap-[3px] lg:border-r lg:border-line lg:py-[18px]"
      >
        {(['steering', 'system'] as const).map((group) => (
          <div key={group} className="flex shrink-0 items-center gap-1 lg:flex-col lg:items-stretch lg:gap-[3px]">
            <div className={`${LABEL} hidden px-3.5 pb-2.5 lg:block ${group === 'system' ? 'lg:pt-5' : ''}`}>
              {group}
            </div>
            {SECTIONS.filter((s) => s.group === group).map((def) => {
              const href = hrefForSection(def.key)
              const on = section === def.key
              return (
                <a
                  key={def.key}
                  href={href}
                  onClick={onInternalClick(href)}
                  aria-current={on ? 'page' : undefined}
                  title={def.unbuilt ? `Not built yet (${def.unbuilt})` : undefined}
                  className={`whitespace-nowrap px-3 py-2 text-[13px] ${
                    on
                      ? 'border-l-2 border-accent-graph bg-surface pl-2.5 font-medium text-text'
                      : def.unbuilt
                        ? 'text-text-faint hover:text-text-muted'
                        : 'text-text-muted hover:text-text'
                  }`}
                >
                  {def.label}
                  {def.key === 'topics' && draft && !on ? (
                    // Kept while elsewhere, so it says it is there.
                    <span className="ml-1.5 font-mono text-[10px] text-accent-attention">· staged</span>
                  ) : null}
                </a>
              )
            })}
          </div>
        ))}
      </nav>

      {/* Not a second <main>: the app's shell already is one (`B-199`). */}
      <div className="flex min-w-0 flex-1 flex-col gap-5 px-4 py-6 sm:px-8 sm:py-7">
        {error ? (
          // The API's own sentence, shown as written. For a closed Admin it
          // names the variables that open it, and replacing it with a generic
          // line would throw away the only actionable thing in the response.
          <p
            role="alert"
            className="border border-accent-attention bg-surface px-[18px] py-3 text-[12.5px] text-accent-attention"
          >
            {error}
          </p>
        ) : null}
        {notice ? (
          <p role="status" className="border border-line bg-surface px-[18px] py-3 text-[12.5px] text-text">
            {notice}
          </p>
        ) : null}

        {section === 'topics' ? (
          topics ? (
            <TopicPanel
              rows={topics.rows}
              sumsTo={topics.sums_to}
              entries={entries}
              busy={steering}
              draft={draft}
              preview={current}
              refusal={refusal}
              onDraft={(topic, weight) => setDraft({ topic, weight })}
              onRevert={() => setDraft(null)}
              onApply={() => {
                if (!draft) return
                const { topic, weight } = draft
                void steer(topic, async () => {
                  await editTopic(topic, { weight })
                  setDraft(null)
                  await loadTopics()
                })
              }}
              onStatus={onStatus}
              onPinned={onPinned}
              onDescribe={onDescribe}
              onArchive={(topic) => setArchiving(topic)}
              onAddTopic={() => setAdding(true)}
              onBoost={onBoost}
            />
          ) : (
            <Loading what="the topics" />
          )
        ) : null}

        {section === 'boosts' ? (
          topics ? (
            <PinsPanel rows={topics.rows} busy={steering} onPinned={onPinned} onBoost={onBoost} />
          ) : (
            <Loading what="the topics" />
          )
        ) : null}

        {section === 'proposals' ? (
          proposals ? (
            <ProposalsPanel
              proposals={proposals}
              busy={proposalBusy}
              onAccept={(proposalId) => void decide(proposalId, 'Applied now.', () => acceptProposal(proposalId))}
              onReject={(proposalId, reason) =>
                void decide(proposalId, 'Rejected; it will not apply.', () => rejectProposal(proposalId, reason))
              }
            />
          ) : error ? null : (
            <Loading what="the proposals" />
          )
        ) : null}

        {section === 'seeds' ? (
          run ? (
            <FirstRunPanel
              run={run}
              busy={seedBusy}
              adding={seedAdding}
              onAdd={(url, kind, topic) =>
                void (async () => {
                  setSeedAdding(true)
                  setError(null)
                  try {
                    await addSeed({ url_or_query: url, task_type: kind, topic })
                    setRun(await getFirstRun())
                  } catch (cause) {
                    setError(cause instanceof ApiError ? cause.message : 'That seed was not added.')
                  } finally {
                    setSeedAdding(false)
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
                    setError(cause instanceof ApiError ? cause.message : 'That seed was not removed.')
                  } finally {
                    setSeedBusy(null)
                  }
                })()
              }
            />
          ) : (
            <Loading what="the seed list" />
          )
        ) : null}

        {section === 'domains' ? (
          policy ? (
            <FetchPolicyPanel
              rows={policy.rows}
              counts={{ active: policy.active, paused: policy.paused, blocked: policy.blocked }}
              status={domainStatus}
              busy={steering}
              query={domainQuery}
              offset={policy.offset}
              hasMore={policy.has_more}
              pageSize={DOMAIN_PAGE}
              onQuery={setDomainQuery}
              onPage={setDomainOffset}
              onStatusFilter={(next) => {
                setDomainStatus(next)
                setDomainOffset(0)
              }}
              onUnblock={(domain) =>
                void steer(domain, async () => {
                  await actOnFetchPolicy(domain, 'unblock')
                  await loadPolicy(domainStatus, domainSearch, domainOffset)
                })
              }
              onForgetRender={(domain) =>
                void steer(domain, async () => {
                  await actOnFetchPolicy(domain, 'forget-render')
                  await loadPolicy(domainStatus, domainSearch, domainOffset)
                })
              }
            />
          ) : (
            <Loading what="fetch policy" />
          )
        ) : null}

        {section === 'agents' ? (
          agents ? (
            <AgentsPanel
              rows={agents.rows}
              unserved={agents.unserved_tasks}
              busy={steering}
              onToggle={(agentId, enabled) =>
                void steer(agentId, async () => {
                  // The whole registry comes back, because `unserved_tasks` is
                  // a fact across rows: disabling the only agent that declares
                  // a task type changes what every other row's screen says.
                  setAgents(await editAgent(agentId, { enabled }))
                })
              }
              onModel={(agentId, model) =>
                steer(agentId, async () => {
                  setAgents(await editAgent(agentId, { model }))
                })
              }
            />
          ) : (
            <Loading what="the registry" />
          )
        ) : null}

        {section === 'access' ? <AssistantAccessPanel /> : null}
        {section === 'display' ? <DisplayPanel /> : null}

        {section === 'health' ? health ? <CrawlHealthPanel health={health} /> : <Loading what="crawl health" /> : null}

        {section === 'runs' ? (
          runs ? (
            <RunsPanel
              rows={runs.rows}
              total={runs.total}
              active={runs.active}
              loadingOlder={loadingOlder}
              onOlder={() => {
                setLoadingOlder(true)
                getRuns({ limit: 25, offset: runs.rows.length })
                  .then((older) => {
                    const seen = new Set(runs.rows.map((r) => r.run_id))
                    setRuns({ ...runs, rows: [...runs.rows, ...older.rows.filter((r) => !seen.has(r.run_id))] })
                  })
                  .catch(() => {})
                  .finally(() => setLoadingOlder(false))
              }}
            />
          ) : (
            <Loading what="run history" />
          )
        ) : null}

        {section === 'enrichment' ? (
          <PageHeader title="Enrichment queue">
            Not built yet. The table this screen will read exists, but nothing fills it until{' '}
            <span className="font-mono">P7-07</span>: figure descriptions, OCR at a higher quality tier and chart
            reading, each started by a person rather than on a schedule. There is nothing queued to show.
          </PageHeader>
        ) : null}

        {section === 'gazetteer' ? (
          queue ? (
            <GazetteerQueue
              rows={queue.rows}
              state={state}
              counts={{
                pending: queue.pending,
                approved: queue.approved,
                rejected: queue.rejected,
              }}
              busy={busy}
              bulkBusy={bulkBusy}
              offset={queue.offset}
              hasMore={queue.has_more}
              onState={(next) => {
                setState(next)
                setOffset(0)
              }}
              onPage={setOffset}
              onDecide={(termId, decision) => void act(termId, () => decideGazetteerTerm(termId, decision))}
              onEdit={(termId, entityType: GazetteerEntityType) =>
                void act(termId, () => editGazetteerTerm(termId, { entity_type: entityType }))
              }
              onBulk={(ids, decision) => void bulk(ids, decision)}
            />
          ) : error ? null : (
            <Loading what="the gazetteer queue" />
          )
        ) : null}
      </div>

      {steeringPage ? (
        <div className="shrink-0 border-t border-line bg-surface px-[22px] py-7 lg:w-[300px] lg:border-l lg:border-t-0">
          <SteeringRail entries={entries} runs={recent} />
        </div>
      ) : null}

      {adding && topics ? (
        <AddTopicDialog
          rows={topics.rows}
          sumsTo={topics.sums_to}
          busy={steering !== null}
          preview={(body, signal) => previewAddTopic(body, { signal })}
          onClose={() => setAdding(false)}
          onAdd={(body) =>
            void (async () => {
              setDraft(null)
              const ok = await steer(body.topic, async () => {
                await addTopic(body)
                await loadTopics()
              })
              if (ok) setAdding(false)
            })()
          }
        />
      ) : null}

      {archiving && topics ? (
        <ArchiveDialog
          topic={archiving}
          rows={topics.rows}
          sumsTo={topics.sums_to}
          busy={steering !== null}
          preview={(signal) => previewEditTopic(archiving, { status: 'archived' }, { signal })}
          onClose={() => setArchiving(null)}
          onArchive={() =>
            void (async () => {
              setDraft(null)
              const ok = await steer(archiving, async () => {
                await editTopic(archiving, { status: 'archived' })
                await loadTopics()
              })
              if (ok) setArchiving(null)
            })()
          }
        />
      ) : null}
    </div>
  )
}
