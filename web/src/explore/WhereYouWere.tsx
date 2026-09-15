import { Icon } from '../ui/Icon'
import type { NodeGlyphName } from '../ui/icons'

/**
 * §8's "where you were" list — saved views and recent nodes.
 *
 * Short by design. §12.5 asks the interface to optimise for legibility over
 * volume because the bottleneck is reading time, and a landing screen that
 * listed everything you had ever opened would spend that bottleneck on
 * navigation.
 *
 * **Empty is a first-class state and says so plainly.** A returning reader with
 * nothing here has not used the system yet; a reader who saved three views last
 * week and sees an empty list has a bug. Rendering nothing at all makes those
 * two indistinguishable, so the empty state names the condition instead.
 */

export interface SavedView {
  id: string
  name: string
}

export interface RecentNode {
  id: string
  name: string
  /** Node type drives the glyph; §5.4's ontology. */
  nodeType: NodeGlyphName
  /** Contested pairs are the valuable ones (§9), so they are marked here too. */
  contested?: boolean
}

export interface WhereYouWereProps {
  savedViews: readonly SavedView[]
  recentNodes: readonly RecentNode[]
  onOpenView?: (id: string) => void
  onOpenNode?: (id: string) => void
}

const HEADING =
  'font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted'

export function WhereYouWere({
  savedViews,
  recentNodes,
  onOpenView,
  onOpenNode,
}: WhereYouWereProps) {
  const empty = savedViews.length === 0 && recentNodes.length === 0

  if (empty) {
    return (
      <section>
        <h2 className={HEADING}>Where you were</h2>
        <p className="mt-2 text-[length:var(--text-small)] text-text-muted">
          No saved views and no recent nodes.
        </p>
      </section>
    )
  }

  return (
    <section className="grid gap-6 sm:grid-cols-2">
      <div>
        <h2 className={HEADING}>Saved views</h2>
        {savedViews.length === 0 ? (
          <p className="mt-2 text-[length:var(--text-small)] text-text-muted">None saved.</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {savedViews.map((view) => (
              <li key={view.id}>
                <button
                  type="button"
                  onClick={() => onOpenView?.(view.id)}
                  className="text-left text-[length:var(--text-small)] text-accent-graph"
                >
                  {view.name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h2 className={HEADING}>Recent nodes</h2>
        {recentNodes.length === 0 ? (
          <p className="mt-2 text-[length:var(--text-small)] text-text-muted">None opened yet.</p>
        ) : (
          <ul className="mt-2 space-y-1">
            {recentNodes.map((node) => (
              <li key={node.id}>
                <button
                  type="button"
                  onClick={() => onOpenNode?.(node.id)}
                  className="flex items-center gap-2 text-left text-[length:var(--text-small)] text-accent-graph"
                >
                  <Icon name={node.nodeType} size={16} />
                  <span className={node.contested ? 'text-accent-attention' : undefined}>
                    {node.name}
                    {/* The dagger as text, so the mark survives the colour being
                        stripped — §6's stated test, applied to a list item. */}
                    {node.contested ? (
                      <sup className="font-mono text-[0.72em]" aria-label="contested">
                        †
                      </sup>
                    ) : null}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
