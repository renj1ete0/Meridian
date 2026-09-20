import { NoteComposer, NoteList } from './Annotations'
import { ResultList } from './ResultList'
import type { NodeAttribute, NodeDetail, NoteDraft } from '../lib/api'

/**
 * The node detail panel (task P6-04, spec §12.5, §7).
 *
 * §12.5 asks for "description, attribute tags with confidence, supporting
 * chunks with source and tier, contested edges". Three decisions carry it.
 *
 * **Tags are grouped by scope.** §7.1 splits attributes into ones that apply
 * across the corpus and ones that only mean something inside a topic, and a
 * flat row of tags says they are the same kind of claim. They are not: a
 * topic-local dimension compared across topics is a comparison nobody made.
 *
 * **Confidence is on the tag, not behind it.** §7 makes it first-class. A tag
 * whose confidence a reader has to hover for is a claim rendered as a fact, and
 * this is a corpus whose whole argument is that claims should be checkable.
 *
 * **Overflow expands rather than truncating.** A dozen attributes is the cap
 * (§7.3), so the list is short by design — but an entity near the cap in both
 * scopes is still more than a panel should open with. `<details>` because it
 * needs no state and works with the keyboard, and because a "+7 more" that is
 * not reachable is the same as not having the tags.
 *
 * §12.5 ends the panel with "own annotations", and `P6-05` put them there: the
 * reader's own thinking about this node, and the affordance to add to it,
 * last — after the evidence, because a note written before reading the passages
 * is a note about the title.
 */

export interface NodePanelProps {
  node: NodeDetail
  /** How many tags each group shows before folding the rest away. */
  visible?: number
  /**
   * Writing a note about this node. Optional, and the panel renders without it
   * — `P6-04` built this screen to be readable before anything could write to
   * it, and a panel that needed a write handler to render would have made the
   * read path depend on the control surface being reachable.
   */
  onWrite?: (draft: NoteDraft) => void
  writing?: boolean
  writeError?: string | null
}

export const DEFAULT_VISIBLE = 6

/** §7.1's two scopes, in the order they should be read. */
const GROUPS: { scope: string; label: string; hint: string }[] = [
  { scope: 'global', label: 'across the corpus', hint: 'Compared against every entity.' },
  { scope: 'topic_local', label: 'within a topic', hint: 'Only meaningful inside its topic.' },
]

export function describeConfidence(value: number | null): string {
  // A number, not a word. §7's confidence is a probability and rounding it to
  // "high" throws away the difference between 0.61 and 0.94 — which is most of
  // what a reader weighing two contradictory tags has to go on.
  if (value === null) return 'no confidence recorded'
  return `${Math.round(value * 100)}%`
}

export function groupByScope(
  attributes: readonly NodeAttribute[],
): { scope: string; label: string; hint: string; items: NodeAttribute[] }[] {
  return GROUPS.map((group) => ({
    ...group,
    items: attributes.filter((a) => a.scope === group.scope),
  })).filter((group) => group.items.length > 0)
}

function Tag({ attribute }: { attribute: NodeAttribute }) {
  return (
    <li className="flex items-baseline gap-2 border border-line-strong px-2 py-1">
      <span className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
        {attribute.name}
      </span>
      <span className="font-sans text-[length:var(--text-small)]">
        {attribute.value ?? attribute.value_numeric ?? '—'}
      </span>
      <span className="font-mono text-[length:var(--text-label)] text-text-muted">
        {describeConfidence(attribute.confidence)}
      </span>
      {attribute.supporting_chunk_ids.length === 0 ? (
        // §2 principle 3: nothing is assertable without a citation. A tag with
        // no supporting chunk should not exist — the schema requires the array
        // — so an empty one is a data problem, and saying so beats hiding it.
        <span className="font-mono text-[length:var(--text-label)] text-accent-attention">
          no evidence
        </span>
      ) : null}
    </li>
  )
}

export function NodePanel({
  node,
  visible = DEFAULT_VISIBLE,
  onWrite,
  writing = false,
  writeError = null,
}: NodePanelProps) {
  const groups = groupByScope(node.attributes)
  const here = {
    entity_id: node.entity.entity_id,
    canonical_name: node.entity.canonical_name,
    node_type: node.entity.node_type,
  }

  return (
    <article>
      <header>
        <h1 className="font-sans text-[length:var(--text-heading)] font-semibold">
          {node.entity.canonical_name}
        </h1>
        <p className="mt-1 font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
          {node.entity.node_type}
          {node.entity.jurisdiction ? ` · ${node.entity.jurisdiction}` : ''}
          {node.entity.is_annotation ? ' · yours' : ''}
        </p>
        {node.entity.aliases && node.entity.aliases.length > 0 ? (
          <p className="mt-1 font-mono text-[length:var(--text-small)] text-text-muted">
            also: {node.entity.aliases.join(' · ')}
          </p>
        ) : null}
      </header>

      {node.entity.description ? (
        <p className="mt-4 max-w-prose">{node.entity.description}</p>
      ) : null}

      {node.contested_edges > 0 ? (
        <p className="mt-4 border border-accent-attention bg-surface p-3 text-[length:var(--text-small)] text-accent-attention">
          {/* §9: a contested pair is two sources disagreeing, which is a finding
              rather than an error — but it is the thing a reader most needs to
              know before quoting this node. */}
          {node.contested_edges === 1
            ? 'One connection here is contested — sources disagree.'
            : `${node.contested_edges} connections here are contested — sources disagree.`}
        </p>
      ) : null}

      {groups.length === 0 ? (
        <p className="mt-6 text-text-muted">
          Nothing has been tagged on this node yet. Attributes are assigned by the slow loop, so a
          node can exist with none.
        </p>
      ) : (
        groups.map((group) => (
          <section key={group.scope} className="mt-6">
            <h2 className="font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
              {group.label}
            </h2>
            <p className="mt-1 text-[length:var(--text-small)] text-text-muted">{group.hint}</p>

            <ul className="mt-2 flex flex-wrap gap-2">
              {group.items.slice(0, visible).map((attribute) => (
                <Tag key={attribute.value_id} attribute={attribute} />
              ))}
            </ul>

            {group.items.length > visible ? (
              <details className="mt-2">
                <summary className="cursor-pointer font-mono text-[length:var(--text-label)] uppercase tracking-[var(--tracking-label)] text-text-muted">
                  {group.items.length - visible} more
                </summary>
                <ul className="mt-2 flex flex-wrap gap-2">
                  {group.items.slice(visible).map((attribute) => (
                    <Tag key={attribute.value_id} attribute={attribute} />
                  ))}
                </ul>
              </details>
            ) : null}
          </section>
        ))
      )}

      <section className="mt-8">
        <h2 className="font-sans text-[length:var(--text-body)] font-semibold">Evidence</h2>
        {node.supporting.length === 0 ? (
          <p className="mt-2 text-text-muted">
            No passages behind these tags. That is a gap rather than an absence of material — every
            tag is supposed to name the chunk it came from.
          </p>
        ) : (
          <>
            <p className="mt-1 max-w-prose text-[length:var(--text-small)] text-text-muted">
              {/* `P1-32`: this is the one place superseded chunks are shown. The
                  tag was derived from this text, and the page may since have
                  changed — which is exactly why the row is kept. */}
              The passages these tags were drawn from, as they read when they were read. A page may
              have changed since.
            </p>
            <div className="mt-3">
              <ResultList hits={node.supporting} />
            </div>
          </>
        )}
      </section>

      <section className="mt-8">
        <h2 className="font-sans text-[length:var(--text-body)] font-semibold">Your notes</h2>
        <p className="mt-1 max-w-prose text-[length:var(--text-small)] text-text-muted">
          {/* §12.5 expects this to become the highest-quality layer in the
              system over months. Saying whose it is matters: everything else on
              this panel was derived by something, and this was not. */}
          Yours, not the corpus's. Everything else here was derived from a source; this was not.
        </p>
        <NoteList notes={node.annotations} inContextOf={node.entity.entity_id} />
        <div className="mt-3">
          <NoteComposer
            about={[here]}
            busy={writing}
            error={writeError}
            onWrite={onWrite}
          />
        </div>
      </section>
    </article>
  )
}
