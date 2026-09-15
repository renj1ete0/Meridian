import { Contested } from '../ui/Contested'

/**
 * §8's four counts — documents, nodes, edges, contested†.
 *
 * **The absent value is a designed state, not a loading spinner.** `counts` is
 * nullable and a null renders an em dash per figure, because the alternative —
 * zeros — is a lie that reads as a true statement about an empty corpus, and a
 * reader cannot tell "nothing has been crawled" from "the API did not answer".
 * §4's first voice rule is to state the absence; showing 0 states the opposite.
 *
 * Mono numerals with tabular figures (§3), so the four sit in a row that scans
 * as a column of numbers rather than as four differently-sized words.
 *
 * `contested` carries the dagger through the `Contested` primitive rather than a
 * brass tint, because §6's rule is that the tint never appears without the mark
 * — and a count is exactly where a reader colour-blind to brass would otherwise
 * lose the distinction.
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

export function CorpusCounts({ counts }: CorpusCountsProps) {
  const cells: Array<{ key: keyof CorpusFigures; label: React.ReactNode }> = [
    { key: 'documents', label: 'Documents' },
    { key: 'nodes', label: 'Nodes' },
    { key: 'edges', label: 'Edges' },
    { key: 'contested', label: <Contested form="badge" /> },
  ]

  return (
    <dl className="flex flex-wrap justify-center gap-x-10 gap-y-4">
      {cells.map(({ key, label }) => (
        <div key={key} className="text-center">
          <dd className="font-mono text-[length:var(--text-heading)] text-text">
            {figure(counts?.[key])}
          </dd>
          <dt className="mt-1 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
            {label}
          </dt>
        </div>
      ))}
    </dl>
  )
}
