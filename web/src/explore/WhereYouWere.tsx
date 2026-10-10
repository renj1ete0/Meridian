import { ago } from '../lib/status'
import { DAGGER } from '../ui/Contested'
import { Icon } from '../ui/Icon'
import type { NodeGlyphName } from '../ui/icons'

/**
 * §8's "where you were" list — saved views and recent nodes as one short list
 * (`ExploreLanding.dc.html`), with the since-last-visit line at its head and an empty
 * state that says which half is empty. See docs/features/web-app.md#where-you-were.
 */

export interface SavedView {
  id: string
  name: string
  /** When it was last opened, or created. Omitted, no time is shown. */
  at?: string | null
  /** New sources that answer it since then (`P6-43`); null or omitted: not counted. */
  fresh?: number | null
}

/** Past this a watched count reads "200+", as the API caps it (`watch.COUNT_CAP`). */
export const FRESH_CAP = 200

export function freshText(n: number): string {
  return n > FRESH_CAP ? `${FRESH_CAP}+ new` : `${n} new`
}

export interface RecentNode {
  id: string
  name: string
  /** Node type drives the glyph; §5.4's ontology. */
  nodeType: NodeGlyphName
  /** Contested pairs are the valuable ones (§9), so they are marked here too. */
  contested?: boolean
  at?: string | null
}

export interface WhereYouWereProps {
  savedViews: readonly SavedView[]
  recentNodes: readonly RecentNode[]
  onOpenView?: (id: string) => void
  onOpenNode?: (id: string) => void
  /** The since-last-visit line, rendered at the head of the list. */
  delta?: React.ReactNode
  /** At the right of the heading, as the design draws "All saved views →" (`B-180`). */
  action?: React.ReactNode
  now?: Date
}

export const LABEL =
  'font-mono text-[9px] font-medium uppercase leading-none tracking-[var(--tracking-label)] text-text-faint'

const ROW = 'flex w-full items-baseline gap-3.5 py-[9px] text-left'
const KIND = 'font-mono text-[10px] uppercase tracking-[0.1em] text-text-faint'
const WHEN = 'w-[76px] shrink-0 text-right font-mono text-[10.5px] text-text-faint'

export function WhereYouWere({
  savedViews,
  recentNodes,
  onOpenView,
  onOpenNode,
  delta,
  action,
  now,
}: WhereYouWereProps) {
  const empty = savedViews.length === 0 && recentNodes.length === 0

  return (
    <section aria-labelledby="where-you-were" className="flex flex-col gap-3.5">
      <div className="flex items-baseline justify-between gap-4">
        <h2 id="where-you-were" className={LABEL}>
          Where you were
        </h2>
        {action}
      </div>

      {delta}

      {empty ? (
        <p className="text-[13px] text-text-muted">No saved views and no recent nodes.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-line/50">
          {savedViews.map((view) => (
            <li key={`v${view.id}`}>
              <button type="button" onClick={() => onOpenView?.(view.id)} className={`${ROW} group`}>
                <span className="grow text-[13.5px] text-text/85 group-hover:text-accent-graph">{view.name}</span>
                {view.fresh ? (
                  <span
                    className="shrink-0 font-mono text-[10.5px] tabular-nums text-accent-graph"
                    title="Sources that answer this view, new since you last opened it"
                  >
                    {freshText(view.fresh)}
                  </span>
                ) : null}
                <span className={KIND}>{view.fresh === 0 ? 'Nothing new' : 'Saved view'}</span>
                <span className={WHEN}>{view.at ? ago(view.at, now) : ''}</span>
              </button>
            </li>
          ))}
          {recentNodes.map((node) => (
            <li key={`n${node.id}`}>
              <button type="button" onClick={() => onOpenNode?.(node.id)} className={`${ROW} group`}>
                <span
                  className={`flex grow items-center gap-2 text-[13.5px] group-hover:text-accent-graph ${
                    node.contested ? 'text-accent-attention' : 'text-text/85'
                  }`}
                >
                  <span className="self-center text-text-faint">
                    <Icon name={node.nodeType} size={16} />
                  </span>
                  <span>
                    {node.name}
                    {/* The dagger as text, so the mark survives the colour being
                        stripped — §6's stated test, applied to a list item. */}
                    {node.contested ? (
                      <sup className="font-mono text-[0.72em]" aria-label="contested">
                        {DAGGER}
                      </sup>
                    ) : null}
                  </span>
                </span>
                <span className={KIND}>Node</span>
                <span className={WHEN}>{node.at ? ago(node.at, now) : ''}</span>
              </button>
            </li>
          ))}
          {savedViews.length === 0 ? (
            <li className="py-[9px] text-[12.5px] text-text-faint">No saved views yet.</li>
          ) : null}
          {recentNodes.length === 0 ? (
            <li className="py-[9px] text-[12.5px] text-text-faint">No node opened yet.</li>
          ) : null}
        </ul>
      )}
    </section>
  )
}
