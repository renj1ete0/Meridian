import type { ReactNode } from 'react'
import { stampOf } from '../lib/time'

/**
 * Admin's visual vocabulary (task P6-28, design-system §5, `AdminLight` mock).
 *
 * §5: "Admin is generated CRUD and should stay plain." Plain is not the same as
 * unstyled, and the failure the operator named was every section inventing its
 * own card. So there is one of each thing here — a page header, a card, a
 * table, two buttons, a badge — and every section is built out of them. A
 * section that needs something these do not offer is a question for the
 * design, not a new class string.
 *
 * Every colour is a role from tokens.css, so the same markup reads on paper
 * (Admin's designed ground) and on the dark palette when somebody has asked
 * for it explicitly.
 */

/** Mono label: 9–10px, 0.15em, uppercase — §3's only use of caps. */
export const LABEL =
  'font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint'

/** Table head cell. A step smaller than a label, as the mock sets it. */
export const TH =
  'px-3 py-2.5 text-left align-bottom font-mono text-[8.5px] font-medium uppercase ' +
  'tracking-[var(--tracking-label)] text-text-faint first:pl-[18px] last:pr-[18px]'

/** Table body cell, reader's text. */
export const TD =
  'px-3 py-2.5 align-baseline text-[12.5px] text-text first:pl-[18px] last:pr-[18px]'

/** Table body cell, measured data. */
export const TDM =
  'px-3 py-2.5 align-baseline font-mono text-[11.5px] tabular-nums text-text ' +
  'first:pl-[18px] last:pr-[18px]'

/** A row rule inside a card: lighter than the card's own edge, as in the mock. */
export const ROW = 'border-t border-line/60'

/** §5 controls: primary is the graph accent filled; secondary is bordered. */
const BUTTON =
  'inline-flex h-[34px] items-center justify-center whitespace-nowrap px-[13px] text-[12.5px] ' +
  'disabled:cursor-not-allowed disabled:opacity-45'

export const BUTTON_PRIMARY = `${BUTTON} border border-accent-graph bg-accent-graph font-medium text-surface`

export const BUTTON_SECONDARY = `${BUTTON} border border-line-strong bg-surface text-text`

/** A compact secondary button for use inside a table row. */
export const BUTTON_ROW =
  'inline-flex h-[26px] items-center whitespace-nowrap border border-line-strong bg-surface px-2.5 ' +
  'text-[12px] text-text disabled:cursor-not-allowed disabled:opacity-45'

/** Previous / Next, and the other toolbar buttons beside a filter strip. */
export const PAGER =
  'inline-flex h-[30px] items-center whitespace-nowrap border border-line-strong bg-surface px-3 ' +
  'text-[12.5px] text-text disabled:cursor-not-allowed disabled:opacity-45'

/** A text action in the mono face — the mock's "+ Add topic", "Restore". */
export const LINK_ACTION =
  'font-mono text-[11px] text-accent-graph hover:underline disabled:cursor-not-allowed ' +
  'disabled:text-text-faint disabled:no-underline'

/** A form field: square, bordered, on the surface. */
export const FIELD =
  'h-[34px] border border-line-strong bg-surface px-[11px] text-[13px] text-text ' +
  'placeholder:text-text-faint disabled:opacity-60'

export function PageHeader({
  title,
  children,
  actions,
}: {
  title: string
  children?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-6">
      <div className="flex min-w-0 flex-col gap-1.5">
        <h1 className="text-[25px] font-semibold leading-[1.15] tracking-[var(--tracking-display)] text-text">
          {title}
        </h1>
        {children ? (
          <div className="max-w-[74ch] text-[13px] leading-[1.55] text-text-muted">{children}</div>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 gap-2">{actions}</div> : null}
    </header>
  )
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`border border-line bg-surface ${className}`}>{children}</div>
}

/** A card holding one table. The table scrolls sideways inside the card rather
 * than pushing the page wider than the window. */
export function TableCard({ children }: { children: ReactNode }) {
  return (
    <Card className="overflow-x-auto">
      <table className="w-full">{children}</table>
    </Card>
  )
}

/** A heading inside a page, above a card — the mock's "Active boosts". */
export function SubHeading({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <h2 className="text-[15px] font-semibold text-text">{children}</h2>
      {action}
    </div>
  )
}

/**
 * A state badge. Bordered mono caps, because §5 puts state in form and not only
 * in colour — a Paused row must read as paused with the colour stripped.
 */
export function Badge({
  children,
  tone = 'plain',
}: {
  children: ReactNode
  tone?: 'plain' | 'attention'
}) {
  const colour =
    tone === 'attention'
      ? 'border-accent-attention/50 bg-accent-attention/10 text-accent-attention'
      : 'border-line-strong bg-surface-raised text-text-muted'
  return (
    <span
      className={`inline-block border px-1.5 py-[2px] font-mono text-[9px] uppercase leading-none tracking-[0.12em] ${colour}`}
    >
      {children}
    </span>
  )
}

/**
 * The filter strip every list section uses: one control per state, each with
 * its count. Counts are the server's unfiltered ones, so a tab can never read
 * zero while the rows it would hold sit under another.
 */
export function Filters<K extends string | null>({
  options,
  value,
  onChange,
}: {
  options: readonly { key: K; label: string; count?: number }[]
  value: K
  onChange?: (key: K) => void
}) {
  return (
    <div className="flex flex-wrap border border-line bg-surface" role="group">
      {options.map(({ key, label, count }) => (
        <button
          key={label}
          type="button"
          onClick={() => onChange?.(key)}
          aria-pressed={value === key}
          className={`h-[30px] border-r border-line px-3 font-mono text-[10.5px] last:border-r-0 ${
            value === key ? 'bg-surface-raised text-text' : 'text-text-muted hover:text-text'
          }`}
        >
          {label}
          {count === undefined ? '' : ` (${count.toLocaleString()})`}
        </button>
      ))}
    </div>
  )
}

/** Loading, stated. Never a spinner: a sentence says which thing is loading. */
export function Loading({ what }: { what: string }) {
  return <p className="text-[12.5px] text-text-muted">Loading {what}.</p>
}

/** A timestamp as the mock prints it, `2026-09-04 21:40`, in the display zone (ADR 0009). */
export function stamp(iso: string | null): string {
  return iso === null ? '—' : stampOf(iso)
}
