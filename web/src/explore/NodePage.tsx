import { useEffect, useRef, useState } from 'react'

import { NodePanel } from './NodePanel'
import {
  ApiError,
  getNode,
  writeAnnotation,
  type NodeDetail,
  type NoteDraft,
} from '../lib/api'

/**
 * A node at its own URL (task P6-04, spec §12.5).
 *
 * §12.3 puts this panel beside the canvas, and the canvas does not exist yet
 * (`P6-01`). Giving the node a real URL now rather than waiting is the same
 * decision `P6-21` made for sources: a corpus that insists everything be
 * checkable cannot have its own nodes be unaddressable, and a panel that only
 * ever opens from a click is one nobody can cite.
 *
 * It also owns the one write on this screen (`P6-05`). A written note is
 * re-fetched rather than pushed into the panel's state, because the server
 * decides what a note ends up being — the authorship it assigns, the moment it
 * records, the targets it resolved — and a client that renders its own guess
 * shows the reader something the database does not hold.
 */
export function NodePage({ entityId }: { entityId: number }) {
  const [node, setNode] = useState<NodeDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [writing, setWriting] = useState(false)
  const [writeError, setWriteError] = useState<string | null>(null)
  // Bumped after a note lands, to re-run the load below. The alternative —
  // splicing the returned note into `node.annotations` — would have the panel
  // render a list the server never sent.
  const [written, setWritten] = useState(0)
  const shown = useRef<number | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    // Blanked only when the reader moved to a different node. A refresh after
    // writing a note is the same node, and dropping the panel to "Loading."
    // there would flash the whole screen away as the reward for annotating.
    if (shown.current !== entityId) {
      setNode(null)
      setError(null)
      shown.current = entityId
    }
    getNode(entityId, { signal: controller.signal })
      .then(setNode)
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        // The API's own sentence. For a node that does not exist it names the
        // id, which is the only thing a reader arriving from a stale link can
        // act on.
        setError(cause instanceof ApiError ? cause.message : 'That node could not be loaded.')
      })
    return () => controller.abort()
  }, [entityId, written])

  function onWrite(draft: NoteDraft) {
    setWriting(true)
    setWriteError(null)
    writeAnnotation(draft)
      .then(() => setWritten((n) => n + 1))
      .catch((cause: unknown) => {
        // The API's own sentence. A citation that resolves to nothing names the
        // chunk; a write on an instance without Access names the environment
        // variables that would allow it. Both are actionable and both are lost
        // to a generic failure message.
        setWriteError(cause instanceof ApiError ? cause.message : 'That note was not saved.')
      })
      .finally(() => setWriting(false))
  }

  if (error) {
    return (
      <p className="border border-accent-attention bg-surface p-4 text-[length:var(--text-small)] text-accent-attention">
        {error}
      </p>
    )
  }
  if (!node) {
    return <p className="text-[length:var(--text-small)] text-text-muted">Loading.</p>
  }
  return (
    <NodePanel node={node} onWrite={onWrite} writing={writing} writeError={writeError} />
  )
}
