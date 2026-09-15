import { useEffect, useState } from 'react'

import { NodePanel } from './NodePanel'
import { ApiError, getNode, type NodeDetail } from '../lib/api'

/**
 * A node at its own URL (task P6-04, spec §12.5).
 *
 * §12.3 puts this panel beside the canvas, and the canvas does not exist yet
 * (`P6-01`). Giving the node a real URL now rather than waiting is the same
 * decision `P6-21` made for sources: a corpus that insists everything be
 * checkable cannot have its own nodes be unaddressable, and a panel that only
 * ever opens from a click is one nobody can cite.
 */
export function NodePage({ entityId }: { entityId: number }) {
  const [node, setNode] = useState<NodeDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setNode(null)
    setError(null)
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
  }, [entityId])

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
  return <NodePanel node={node} />
}
