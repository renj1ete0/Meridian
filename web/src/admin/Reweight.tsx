import { useEffect, useId, useRef, type ReactNode } from 'react'

import type { TopicRow, Topics } from '../lib/api'
import { BUTTON_SECONDARY, LABEL } from './ui'

/**
 * The re-normalisation, shown before it is committed (design-system §8, spec
 * §10.2; `AdminAddTopic` mock).
 *
 * "The dialog shows the arithmetic before you commit." The arithmetic is the
 * server's: `after` comes from a preview route that runs the real write and
 * rolls it back, so nothing on this table is computed here except the
 * subtraction in the Change column. A client-side copy of clamp-and-redistribute
 * would be right until the day the server's changed, and then it would be a
 * dialog confidently showing numbers the button does not produce.
 */

/** Two decimals, as the mock prints a weight. */
export function weight(value: number): string {
  return value.toFixed(2)
}

/** A signed change, with a real minus sign so the column lines up. */
export function delta(value: number): string {
  const rounded = Math.round(value * 100) / 100
  if (rounded === 0) return '0.00'
  return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded).toFixed(2)}`
}

export interface ReweightLine {
  topic: string
  now: number | null
  after: number | null
  change: string
  /** The row the change is about — the new topic, or the one being archived. */
  focus: boolean
  tone: 'plain' | 'held' | 'new' | 'released'
}

/**
 * One line per topic that draws before or after, in a stable order.
 *
 * Only drawing topics appear. A paused topic is outside the pool on both sides
 * of every change, and listing it at "— → —" would be a row that says nothing.
 * Pinned is reported as what happened to it rather than what the word
 * promises: the server's re-normalisation redistributes over every active
 * topic, pinned ones included, and a Change column reading "pinned, held"
 * beside a number that moved would be the dialog lying on the one row somebody
 * pinned in order to protect.
 */
export function reweightLines(before: readonly TopicRow[], after: Topics, focus: string): ReweightLine[] {
  const drawing = (rows: readonly TopicRow[]) =>
    new Map(rows.filter((r) => r.topic.status === 'active').map((r) => [r.topic.topic, r]))
  const now = drawing(before)
  const next = drawing(after.rows)
  const order = [...now.keys(), ...[...next.keys()].filter((t) => !now.has(t))]

  return order.map((topic) => {
    const was = now.get(topic)
    const will = next.get(topic)
    const nowValue = was ? was.topic.weight : null
    const afterValue = will ? will.topic.weight : null
    const pinned = (will ?? was)?.topic.pinned ?? false

    let change: string
    let tone: ReweightLine['tone'] = 'plain'
    if (nowValue === null) {
      change = 'new'
      tone = 'new'
    } else if (afterValue === null) {
      change = 'released'
      tone = 'released'
    } else if (Math.abs(afterValue - nowValue) < 0.005) {
      change = pinned ? 'pinned, held' : 'held'
      tone = pinned ? 'held' : 'plain'
    } else {
      change = pinned ? `${delta(afterValue - nowValue)} · pinned` : delta(afterValue - nowValue)
    }
    return { topic, now: nowValue, after: afterValue, change, focus: topic === focus, tone }
  })
}

export function ReweightTable({
  before,
  beforeSum,
  after,
  focus,
}: {
  before: readonly TopicRow[]
  beforeSum: number
  after: Topics | null
  focus: string
}) {
  const head = `${LABEL} px-3.5 py-[9px] text-right first:text-left`
  return (
    <div className="flex flex-col gap-[11px]">
      <span className={LABEL}>Re-normalisation preview</span>
      <div className="border border-line">
        <table className="w-full">
          <thead className="bg-surface-raised">
            <tr>
              <th className={`${head} w-full`}>Topic</th>
              <th className={head}>Now</th>
              <th className={head}>After</th>
              <th className={`${head} min-w-[108px]`}>Change</th>
            </tr>
          </thead>
          <tbody>
            {after === null ? (
              <tr className="border-t border-line/60">
                <td colSpan={4} className="px-3.5 py-[9px] text-[12.5px] text-text-muted">
                  Working out the new weights.
                </td>
              </tr>
            ) : (
              reweightLines(before, after, focus).map((line) => (
                <tr
                  key={line.topic}
                  data-topic={line.topic}
                  className={`border-t border-line/60 ${line.focus ? 'bg-accent-graph/5' : ''}`}
                >
                  <td
                    className={`px-3.5 py-[9px] text-[12.5px] ${line.focus ? 'font-semibold text-text' : 'text-text'}`}
                  >
                    {line.topic}
                  </td>
                  <td className="px-3.5 py-[9px] text-right font-mono text-[11.5px] text-text-faint">
                    {line.now === null ? '—' : weight(line.now)}
                  </td>
                  <td
                    className={`px-3.5 py-[9px] text-right font-mono text-[11.5px] ${
                      line.tone === 'new' ? 'text-accent-graph' : 'text-text'
                    }`}
                  >
                    {line.after === null ? '—' : weight(line.after)}
                  </td>
                  <td
                    className={`whitespace-nowrap px-3.5 py-[9px] text-right font-mono text-[11.5px] ${
                      line.tone === 'new'
                        ? 'text-accent-graph'
                        : line.tone === 'held' || line.tone === 'released'
                          ? 'text-accent-attention'
                          : 'text-text-faint'
                    }`}
                  >
                    {line.change}
                  </td>
                </tr>
              ))
            )}
            <tr className="border-t border-line/60">
              <td className={`${LABEL} px-3.5 py-[9px]`}>Normalised</td>
              <td className="px-3.5 py-[9px] text-right font-mono text-[11.5px] text-text-faint">
                {weight(beforeSum)}
              </td>
              <td className="px-3.5 py-[9px] text-right font-mono text-[11.5px] text-text">
                {after ? weight(after.sums_to) : '—'}
              </td>
              <td />
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

/**
 * A plain modal. Square, bordered, opaque (§5: Admin surfaces are opaque), no
 * shadow — depth is the scrim and the edge. Escape and the scrim both cancel,
 * because a dialog that can only be left by its buttons makes Cancel a trap.
 */
export function Dialog({
  title,
  lede,
  children,
  footnote,
  actions,
  onClose,
}: {
  title: string
  lede: ReactNode
  children: ReactNode
  footnote?: ReactNode
  actions: ReactNode
  onClose: () => void
}) {
  const id = useId()
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    box.current?.querySelector<HTMLElement>('input, button')?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-text/25 p-6"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div
        ref={box}
        role="dialog"
        aria-modal="true"
        aria-labelledby={id}
        className="flex max-h-full w-full max-w-[668px] flex-col overflow-y-auto border border-line-strong bg-surface"
      >
        <div className="flex flex-col gap-1.5 border-b border-line px-6 pb-4 pt-5">
          <h2 id={id} className="text-[19px] font-semibold tracking-[var(--tracking-heading)] text-text">
            {title}
          </h2>
          <div className="text-[12.5px] leading-[1.55] text-text-muted">{lede}</div>
        </div>
        <div className="flex flex-col gap-[18px] px-6 py-5">{children}</div>
        <div className="flex flex-wrap items-center gap-2.5 border-t border-line bg-surface-raised px-6 py-3.5">
          {footnote ? <span className="font-mono text-[10.5px] text-text-faint">{footnote}</span> : null}
          <span className="ml-auto flex gap-2">
            <button type="button" className={BUTTON_SECONDARY} onClick={onClose}>
              Cancel
            </button>
            {actions}
          </span>
        </div>
      </div>
    </div>
  )
}
