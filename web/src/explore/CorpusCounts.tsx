import { DAGGER } from '../ui/Contested'

/**
 * §8's four counts — documents, nodes, edges, contested† — as the landing's stats row. A
 * null renders an em dash per figure, never zeros. See docs/features/web-app.md#corpus-counts.
 */

export interface CorpusFigures {
  documents: number
  nodes: number
  edges: number
  contested: number
}

export interface CorpusCountsProps {
  /** Null while unknown. Renders as em dashes, never as zeros. */
  counts: CorpusFigures | null
}

/** The em dash, for a figure that is not known. */
export const UNKNOWN = '—'

function figure(value: number | undefined): string {
  return value === undefined ? UNKNOWN : value.toLocaleString('en')
}

const CELLS: ReadonlyArray<{ key: keyof CorpusFigures; label: string }> = [
  // Reader words (`P6-44`): nodes and edges stay in Admin.
  { key: 'documents', label: 'Sources' },
  { key: 'nodes', label: 'Concepts' },
  { key: 'edges', label: 'Stated links' },
  { key: 'contested', label: 'Contested' },
]

export function CorpusCounts({ counts }: CorpusCountsProps) {
  return (
    <dl className="grid grid-cols-2 gap-y-5 border-y border-line/70 py-5 sm:flex sm:gap-y-0">
      {CELLS.map(({ key, label }, index) => {
        // Brass and the dagger only when something *is* contested (`B-126`):
        // the tint means "look here", and a zero drawn in it drew the eye to
        // nothing. Unknown and zero are neutral, like the other three.
        const contested = key === 'contested' && (counts?.contested ?? 0) > 0
        return (
          <div
            key={key}
            className={`flex flex-1 basis-0 flex-col-reverse gap-2 ${
              index > 0 ? 'sm:border-l sm:border-line/70 sm:pl-6' : ''
            }`}
          >
            <dt
              className={`font-mono text-[9px] font-medium uppercase leading-none tracking-[var(--tracking-label)] ${
                contested ? 'text-accent-attention' : 'text-text-faint'
              }`}
            >
              {label}
            </dt>
            <dd
              className={`font-mono text-[27px] leading-none tracking-[-0.02em] tabular-nums ${
                contested ? 'text-accent-attention' : 'text-text'
              }`}
            >
              {figure(counts?.[key])}
              {contested ? (
                <sup
                  className="relative -top-[7px] ml-px align-baseline text-[17px] leading-none"
                  aria-label="contested"
                >
                  {DAGGER}
                </sup>
              ) : null}
            </dd>
          </div>
        )
      })}
    </dl>
  )
}
