/**
 * What the canvas draws, as data (tasks P6-01, P6-03).
 *
 * The API's subgraph plus layout plus the published styling, resolved into one
 * flat list of positioned, styled nodes and edges. The renderer only copies
 * this into Sigma, so everything a reader could get wrong about the picture —
 * which node is brass, which edge is dashed, what is labelled — is decided
 * here, where a test can read it.
 */

import type { GraphEdge, GraphNode, GraphPath, Neighbourhood } from './api'
import { pathLayout, radialLayout } from './layout'
import { collapseEdges, edgeLook, nodeLook, type CanvasPalette, type EdgeLook, type NodeLook } from './style'

export interface SceneNode {
  id: number
  x: number
  y: number
  look: NodeLook
  node: GraphNode
}

export interface SceneEdge {
  id: number
  source: number
  target: number
  look: EdgeLook
  edge: GraphEdge
}

export interface Scene {
  nodes: SceneNode[]
  edges: SceneEdge[]
  focus: number
  /** Draw the meridian circle and ellipse around the focus. */
  meridian: boolean
  /**
   * The extent the camera frames, in layout units. Fixed rather than fitted
   * to the nodes, so two neighbours are not stretched to the corners and a
   * label is never pushed under the chrome at the canvas edges.
   */
  frame: { x: [number, number]; y: [number, number] }
  /** Label the edges with their relation — for a path, where there are few. */
  edgeLabels: boolean
}

export function neighbourhoodScene(hood: Neighbourhood, palette: CanvasPalette): Scene {
  const positions = radialLayout(hood.nodes, hood.edges)
  const byId = new Map(hood.nodes.map((n) => [n.entity_id, n]))
  const maxSupport = Math.max(1, ...hood.nodes.map((n) => n.support))

  const nodes: SceneNode[] = hood.nodes
    .filter((n) => positions.has(n.entity_id))
    .map((node) => ({
      id: node.entity_id,
      ...positions.get(node.entity_id)!,
      look: nodeLook(node, palette, { maxSupport, focusContested: hood.focus_contested }),
      node,
    }))

  const edges: SceneEdge[] = collapseEdges(hood.edges)
    .filter((e) => positions.has(e.from_node) && positions.has(e.to_node))
    .map((edge) => {
      const other = edge.from_node === hood.focus.entity_id ? edge.to_node : edge.from_node
      return {
        id: edge.edge_id,
        source: edge.from_node,
        target: edge.to_node,
        look: edgeLook(edge, palette, {
          crossTopic: edge.kind === 'focus' && Boolean(byId.get(other)?.cross_topic),
        }),
        edge,
      }
    })

  return {
    nodes,
    edges,
    focus: hood.focus.entity_id,
    meridian: true,
    frame: { x: [-1.52, 1.52], y: [-1.52, 1.52] },
    edgeLabels: false,
  }
}

/**
 * A route between two nodes (P6-03). Every stop is labelled and every edge on
 * it is drawn in the accent, because the route is the whole picture.
 */
export function pathScene(path: GraphPath, palette: CanvasPalette): Scene {
  const ids = path.nodes.map((n) => n.entity_id)
  const positions = pathLayout(ids)
  const nodes: SceneNode[] = path.nodes.map((node, index) => {
    const endpoint = index === 0 || index === path.nodes.length - 1
    const role = index === 0 ? 'focus' : 'neighbour'
    const look = nodeLook({ ...node, role }, palette, { maxSupport: 1 })
    return {
      id: node.entity_id,
      ...positions.get(node.entity_id)!,
      look: endpoint && index > 0 ? { ...look, size: 10, color: palette.focus } : look,
      node,
    }
  })
  const edges: SceneEdge[] = path.edges.map((edge) => ({
    id: edge.edge_id,
    source: edge.from_node,
    target: edge.to_node,
    look: edgeLook(edge, palette, { onPath: true }),
    edge,
  }))
  return {
    nodes,
    edges,
    focus: path.source,
    meridian: false,
    frame: { x: [-1.35, 1.35], y: [-0.9, 0.9] },
    edgeLabels: true,
  }
}
