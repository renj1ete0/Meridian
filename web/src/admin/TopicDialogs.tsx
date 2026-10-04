import { useEffect, useState } from 'react'

import { ApiError, type TopicAddBody, type TopicRow, type Topics } from '../lib/api'
import { Dialog, ReweightTable } from './Reweight'
import { BUTTON_PRIMARY, FIELD, LABEL } from './ui'

/**
 * Add topic and Archive topic (design-system §8, spec §10.2; `AdminAddTopic`).
 *
 * Both are changes whose cost lands on *other* rows — a new topic takes its
 * share from the rest, and an archived one hands its share back — so both show
 * the re-normalisation before they commit. Pause is not here: it is reversible
 * in one click and the audit rail shows what it moved.
 */

/** How long typing settles before the preview is asked for. */
export const PREVIEW_DEBOUNCE_MS = 250

/** When a change takes effect. The crawl draws a topic per claim (`B-26`), so
 * there is no nightly pass to wait for — which is why this is not the mock's
 * "at 23:00". */
export const TAKES_EFFECT = 'Takes effect at the crawl’s next claim'

type Preview = (signal: AbortSignal) => Promise<Topics>

/** Ask for a preview whenever `key` changes, settled, and report the refusal
 * as the server wrote it — it names the bound that refused. */
function usePreview(key: string | null, preview: Preview) {
  const [after, setAfter] = useState<Topics | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)

  useEffect(() => {
    setAfter(null)
    setRefusal(null)
    if (key === null) return
    const controller = new AbortController()
    const timer = window.setTimeout(() => {
      preview(controller.signal)
        .then(setAfter)
        .catch((cause: unknown) => {
          if (cause instanceof DOMException && cause.name === 'AbortError') return
          setRefusal(cause instanceof ApiError ? cause.message : 'The preview could not be worked out.')
        })
    }, PREVIEW_DEBOUNCE_MS)
    return () => {
      controller.abort()
      window.clearTimeout(timer)
    }
    // `preview` is recreated every render; `key` is what it depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return { after, refusal }
}

/** A bound typed as text, read as a number, or null when it is not one. */
export function bound(text: string): number | null {
  const value = Number(text)
  return text.trim() !== '' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null
}

export function AddTopicDialog({
  rows,
  sumsTo,
  busy = false,
  preview,
  onAdd,
  onClose,
}: {
  rows: readonly TopicRow[]
  sumsTo: number
  busy?: boolean
  preview: (body: TopicAddBody, signal: AbortSignal) => Promise<Topics>
  onAdd: (body: TopicAddBody) => void
  onClose: () => void
}) {
  const [label, setLabel] = useState('')
  // The server's own defaults (`TopicAdd`), so an untouched dialog previews
  // exactly what an API call with no bounds would do.
  const [floor, setFloor] = useState('0.05')
  const [ceiling, setCeiling] = useState('0.60')

  const topic = label.trim()
  const lower = bound(floor)
  const upper = bound(ceiling)
  const body: TopicAddBody | null =
    topic && lower !== null && upper !== null ? { topic, floor: lower, ceiling: upper } : null
  const { after, refusal } = usePreview(body ? JSON.stringify(body) : null, (signal) => preview(body!, signal))
  const starts = after?.rows.find((r) => r.topic.topic === topic)?.topic.weight

  return (
    <Dialog
      title="Add topic"
      lede="A new topic takes its share from the others, starting at its floor. Nothing in the graph changes."
      footnote={TAKES_EFFECT}
      onClose={onClose}
      actions={
        <button
          type="button"
          className={BUTTON_PRIMARY}
          disabled={!body || !after || busy}
          onClick={() => body && onAdd(body)}
        >
          {busy ? 'Adding…' : 'Add topic'}
        </button>
      }
    >
      <div className="flex flex-wrap gap-4">
        <label className="flex min-w-[180px] flex-1 flex-col gap-[7px]">
          <span className={LABEL}>Label</span>
          <input
            className={`${FIELD} font-mono text-[12.5px]`}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
            placeholder="a-new-topic"
            aria-label="Topic label"
          />
        </label>
        <div className="flex w-24 flex-col gap-[7px]">
          <span className={LABEL}>Starts at</span>
          {/* Read, not typed: §10.2 starts a new topic at its floor because it
              has no hand-seeded sources yet, and the server does not accept a
              starting weight. What it will start at is the preview's answer. */}
          <span className={`${FIELD} flex items-center font-mono text-[12.5px] text-text-muted`}>
            {starts === undefined ? '—' : starts.toFixed(2)}
          </span>
        </div>
        <label className="flex w-[78px] flex-col gap-[7px]">
          <span className={LABEL}>Floor</span>
          <input
            className={`${FIELD} font-mono text-[12.5px]`}
            value={floor}
            inputMode="decimal"
            onChange={(event) => setFloor(event.target.value)}
            aria-label="Floor"
          />
        </label>
        <label className="flex w-[78px] flex-col gap-[7px]">
          <span className={LABEL}>Ceiling</span>
          <input
            className={`${FIELD} font-mono text-[12.5px]`}
            value={ceiling}
            inputMode="decimal"
            onChange={(event) => setCeiling(event.target.value)}
            aria-label="Ceiling"
          />
        </label>
      </div>

      {refusal ? (
        <p role="alert" className="text-[12.5px] text-accent-attention">
          {refusal}
        </p>
      ) : body ? (
        <ReweightTable before={rows} beforeSum={sumsTo} after={after} focus={topic} />
      ) : (
        <p className="text-[12.5px] text-text-muted">
          {topic
            ? 'Floor and ceiling are shares between 0 and 1.'
            : 'Name the topic to see what it takes from the others.'}
        </p>
      )}

      <p className="text-[12.5px] leading-[1.6] text-text-muted">
        Every active topic gives up weight in proportion, down to its floor. Existing nodes keep the topic labels they
        already carry — only what gets acquired next changes.
      </p>
    </Dialog>
  )
}

export function ArchiveDialog({
  topic,
  rows,
  sumsTo,
  busy = false,
  preview,
  onArchive,
  onClose,
}: {
  topic: string
  rows: readonly TopicRow[]
  sumsTo: number
  busy?: boolean
  preview: (signal: AbortSignal) => Promise<Topics>
  onArchive: () => void
  onClose: () => void
}) {
  const { after, refusal } = usePreview(topic, preview)
  return (
    <Dialog
      title={`Archive ${topic}`}
      lede="Archiving stops seeding this topic and releases its weight to the others. Its nodes stay in the graph and stay searchable, and Restore is one click."
      footnote={TAKES_EFFECT}
      onClose={onClose}
      actions={
        // §8: archive is styled in the attention accent, never a destructive
        // red. It needs attention; it destroys nothing.
        <button
          type="button"
          className="inline-flex h-[34px] items-center border border-accent-attention bg-accent-attention px-[13px] text-[12.5px] font-medium text-surface disabled:opacity-45"
          disabled={!after || busy}
          onClick={onArchive}
        >
          {busy ? 'Archiving…' : 'Archive topic'}
        </button>
      }
    >
      {refusal ? (
        <p role="alert" className="text-[12.5px] text-accent-attention">
          {refusal}
        </p>
      ) : (
        <ReweightTable before={rows} beforeSum={sumsTo} after={after} focus={topic} />
      )}
    </Dialog>
  )
}
