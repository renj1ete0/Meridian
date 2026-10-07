import { useEffect, useState } from 'react'

import { addTopic, ApiError, getTopics } from '../../lib/api'
import {
  getAreaSteering,
  restoreNoise,
  steerArea,
  suggestSearch,
  topicNameFrom,
  type Area,
  type AreaSteering,
  type MapSteerResult,
  type SteerAction,
} from '../../lib/areas'
import { MenuItem, type MapActions } from './AreasScreen'

/**
 * Steering from the map (task P6-35): right-click an area for more, less, make a topic or
 * watch; right-click empty canvas to suggest a search. Each goes through existing,
 * logged steering. See docs/features/map.md#steering-from-the-map.
 */
export function mapActions(): MapActions {
  return {
    areaItems: (area, close) => <AreaSteerItems area={area} close={close} />,
    emptySpace: (_at, near, close) => <SuggestBox near={near} close={close} />,
  }
}

function messageOf(cause: unknown, fallback: string): string {
  return cause instanceof ApiError ? cause.message : fallback
}

type Outcome = { kind: 'done'; result: MapSteerResult } | { kind: 'error'; message: string } | null

export function AreaSteerItems({ area, close }: { area: Area; close: () => void }) {
  const [steering, setSteering] = useState<AreaSteering | null>(null)
  const [readError, setReadError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>(null)
  const [naming, setNaming] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    getAreaSteering(area.area_id, { signal: controller.signal })
      .then(setSteering)
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setReadError(messageOf(cause, 'Could not read what this field would steer.'))
      })
    return () => controller.abort()
  }, [area.area_id])

  function run(action: SteerAction) {
    setBusy(true)
    steerArea(area.area_id, action)
      .then((result) => setOutcome({ kind: 'done', result }))
      .catch((cause: unknown) => setOutcome({ kind: 'error', message: messageOf(cause, 'That did not go through.') }))
      .finally(() => setBusy(false))
  }

  if (outcome) return <OutcomeNote outcome={outcome} close={close} />
  if (naming) return <MakeTopic area={area} close={close} onDone={setOutcome} />
  if (readError) return <p className="px-3 py-2 text-[12px] text-text-muted">{readError}</p>
  if (!steering)
    return <p className="px-3 py-2 font-mono text-[11px] text-text-faint">Reading what this would steer.</p>

  const topic = steering.topic
  return (
    <>
      <MenuItem
        disabled={busy}
        onClick={() => run('more')}
        note={
          topic
            ? `boosts “${topic}” ×${steering.more_factor} for ${steering.boost_days} days · searches “${steering.search}”`
            : `no configured topic holds this field · searches “${steering.search}”`
        }
      >
        Crawl more of this
      </MenuItem>
      <MenuItem
        disabled={busy || !topic}
        onClick={() => run('less')}
        note={
          topic
            ? `turns “${topic}” down ×${steering.less_factor} for ${steering.boost_days} days`
            : 'no configured topic holds this field, so no weight draws it on purpose'
        }
      >
        Crawl less of this
      </MenuItem>
      <MenuItem disabled={busy} onClick={() => setNaming(true)} note="a new topic from its terms">
        Make it a topic…
      </MenuItem>
      <MenuItem disabled={busy} onClick={() => run('watch')} note="saves a view of its terms">
        Watch for new sources
      </MenuItem>
      <MenuItem
        disabled={busy || steering.noise_sources === 0}
        onClick={() => run('noise')}
        note={
          steering.noise_sources > 0
            ? `marks ${steering.noise_sources.toLocaleString('en')} source${steering.noise_sources === 1 ? '' : 's'} read and found about none of the topics as junk · undoable`
            : 'no source here was read and found about none of the topics'
        }
      >
        This is noise
      </MenuItem>
      <p className="px-3 pb-1 text-[11px] text-text-faint">Reversible in Admin · written to the steering log</p>
      <div className="my-1.5 border-t border-line" />
    </>
  )
}

function OutcomeNote({ outcome, close }: { outcome: NonNullable<Outcome>; close: () => void }) {
  const [undone, setUndone] = useState<string | null>(null)
  const mark = outcome.kind === 'done' ? outcome.result.noise_mark : null
  return (
    <div role="status" className="flex flex-col gap-1.5 px-3 py-2">
      {outcome.kind === 'done' ? (
        <>
          <p className="text-[12.5px] leading-[1.5] text-text">{outcome.result.message}</p>
          <p className="text-[11.5px] leading-[1.5] text-text-faint">{outcome.result.undo}</p>
          {mark && !undone ? (
            <button
              type="button"
              onClick={() =>
                restoreNoise(mark)
                  .then((r) => setUndone(`${r.restored.toLocaleString('en')} sources put back.`))
                  .catch((cause: unknown) => setUndone(messageOf(cause, 'The undo did not go through.')))
              }
              className="self-start text-[12px] text-accent-attention"
            >
              Undo
            </button>
          ) : null}
          {undone ? <p className="text-[12px] text-text-muted">{undone}</p> : null}
        </>
      ) : (
        <p className="text-[12.5px] leading-[1.5] text-accent-attention">{outcome.message}</p>
      )}
      <button type="button" onClick={close} className="self-start text-[12px] text-accent-graph">
        Close
      </button>
    </div>
  )
}

function MakeTopic({ area, close, onDone }: { area: Area; close: () => void; onDone: (outcome: Outcome) => void }) {
  const [name, setName] = useState(topicNameFrom(area.terms))
  const [description, setDescription] = useState(area.terms.slice(0, 8).join(', '))
  const [busy, setBusy] = useState(false)

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    addTopic({
      topic: name.trim(),
      description: description.trim() || null,
      reason: `from the map: field “${area.name}”`,
    })
      .then(() =>
        onDone({
          kind: 'done',
          result: {
            action: 'topic',
            area_id: area.area_id,
            topic: name.trim(),
            boost_factor: null,
            boost_expires_at: null,
            seed_task_ids: [],
            view_id: null,
            noise_mark: null,
            message: `“${name.trim()}” is a topic now, starting at its floor.`,
            undo: 'Archive it from Admin › Topics; nothing is deleted.',
          },
        }),
      )
      .catch((cause: unknown) => onDone({ kind: 'error', message: messageOf(cause, 'The topic was not added.') }))
      .finally(() => setBusy(false))
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-2 px-3 py-2">
      <label className="flex flex-col gap-1 text-[12px] text-text-muted">
        Topic name
        <input
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="h-8 border border-line-strong bg-surface-raised px-2 text-[13px] text-text"
        />
      </label>
      <label className="flex flex-col gap-1 text-[12px] text-text-muted">
        What it is about
        <textarea
          value={description}
          onChange={(event) => setDescription(event.target.value)}
          rows={3}
          className="border border-line-strong bg-surface-raised px-2 py-1 text-[12.5px] text-text"
        />
      </label>
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={busy || !name.trim()}
          className="h-8 flex-1 bg-accent-graph px-3 text-[12.5px] font-semibold text-ground-deep disabled:opacity-50"
        >
          Add topic
        </button>
        <button type="button" onClick={close} className="h-8 border border-line-strong px-3 text-[12.5px] text-text">
          Cancel
        </button>
      </div>
    </form>
  )
}

/**
 * Right-click on empty canvas: something new to search for. The chips are
 * the distinctive terms of the smallest areas on screen — thin here, if the
 * reader wants one of those instead of a term of their own.
 */
export function SuggestBox({ near, close }: { near: readonly Area[]; close: () => void }) {
  const [text, setText] = useState('')
  const [topic, setTopic] = useState<string>('')
  const [topics, setTopics] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<Outcome>(null)

  useEffect(() => {
    const controller = new AbortController()
    getTopics({ signal: controller.signal })
      .then((body) => setTopics(body.rows.filter((r) => r.topic.status === 'active').map((r) => r.topic.topic)))
      .catch(() => setTopics([]))
    return () => controller.abort()
  }, [])

  const thin = [...near]
    .sort((a, b) => a.passages - b.passages)
    .slice(0, 3)
    .map((a) => a.terms[0])
    .filter((t): t is string => Boolean(t))

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    suggestSearch(text, topic || null)
      .then((result) => setOutcome({ kind: 'done', result }))
      .catch((cause: unknown) => setOutcome({ kind: 'error', message: messageOf(cause, 'The search was not queued.') }))
      .finally(() => setBusy(false))
  }

  return (
    <div
      role="dialog"
      aria-label="Research something new"
      className="flex w-[320px] flex-col gap-2.5 border border-line-strong bg-surface p-3.5"
    >
      <span className="font-mono text-[9.5px] uppercase tracking-[var(--tracking-label)] text-text-faint">
        Research something new here
      </span>
      {outcome ? (
        <OutcomeNote outcome={outcome} close={close} />
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-2.5">
          <label className="flex flex-col gap-1.5 text-[12.5px] text-text-muted">
            A term or a question to search for
            <input
              autoFocus
              value={text}
              onChange={(event) => setText(event.target.value)}
              className="h-9 border border-line-strong bg-surface-raised px-2.5 text-[13.5px] text-text"
            />
          </label>
          {thin.length ? (
            <>
              <span className="text-[12px] text-text-faint">Smallest fields here, if you want one of these:</span>
              <div className="flex flex-wrap gap-1.5">
                {thin.map((term) => (
                  <button
                    key={term}
                    type="button"
                    onClick={() => setText(term)}
                    className="border border-line-strong px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.1em] text-text-muted hover:text-text"
                  >
                    {term}
                  </button>
                ))}
              </div>
            </>
          ) : null}
          {topics.length ? (
            <label className="flex flex-col gap-1.5 text-[12.5px] text-text-muted">
              For topic
              <select
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                className="h-8 border border-line-strong bg-surface-raised px-2 text-[13px] text-text"
              >
                <option value="">none — belongs to whatever it finds</option>
                {topics.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <div className="mt-1 flex gap-2">
            <button
              type="submit"
              disabled={busy || text.trim().length < 3}
              className="h-9 flex-1 bg-accent-graph text-[13px] font-semibold text-ground-deep disabled:opacity-50"
            >
              Add as a search seed
            </button>
            <button type="button" onClick={close} className="h-9 border border-line-strong px-3 text-[13px] text-text">
              Cancel
            </button>
          </div>
          <span className="font-mono text-[10.5px] text-text-faint">
            queued as a search · removable in Admin until fetched
          </span>
        </form>
      )}
    </div>
  )
}
