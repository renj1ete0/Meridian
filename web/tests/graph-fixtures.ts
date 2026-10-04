/**
 * Builders for the graph workspace's tests. Shaped exactly as the API returns
 * them — the types come from `src/explore/graph/api.ts`, which the drift test
 * ties to the pydantic classes — so a fixture that drifted from the wire would
 * fail to compile rather than pass a test against a shape nobody sends.
 */
import type {
  ContestedPair,
  Evidence,
  GraphEdge,
  GraphNode,
  GraphNodeDetail,
  GraphPath,
  Neighbourhood,
} from '../src/explore/graph/api'
import type { Entity, NodeAttribute, SearchHit } from '../src/lib/api'

export function gnode(over: Partial<GraphNode> = {}): GraphNode {
  return {
    entity_id: 2,
    canonical_name: 'Neighbour',
    node_type: 'concept',
    jurisdiction: null,
    is_annotation: false,
    role: 'neighbour',
    home_topics: [],
    topics: [],
    contested: false,
    cross_topic: false,
    support: 1,
    degree: 1,
    sources: 1,
    newest: null,
    ...over,
  }
}

export function gedge(over: Partial<GraphEdge> = {}): GraphEdge {
  return {
    edge_id: 100,
    from_node: 1,
    to_node: 2,
    relation_type: 'relates_to',
    kind: 'focus',
    confidence: null,
    stance: null,
    certainty: null,
    support: 1,
    contested: false,
    contested_with: [],
    ...over,
  }
}

/** A focus (id 1) with `n` neighbours (ids 2..n+1), one edge each. */
export function hood(n = 3, over: Partial<Neighbourhood> = {}): Neighbourhood {
  const focus = gnode({ entity_id: 1, canonical_name: 'Focus', role: 'focus', support: 0 })
  const neighbours = Array.from({ length: n }, (_, i) =>
    gnode({ entity_id: i + 2, canonical_name: `N${i + 2}`, support: n - i }),
  )
  return {
    focus,
    focus_contested: false,
    redirects_to: null,
    nodes: [focus, ...neighbours],
    edges: neighbours.map((nb, i) =>
      gedge({ edge_id: 100 + i, from_node: 1, to_node: nb.entity_id, support: nb.support }),
    ),
    total: n,
    shown: n,
    unfiltered: n,
    limit: 30,
    ranked_by: 'support',
    filters: {
      topics: [],
      tiers: [],
      published_from: null,
      published_to: null,
      contested_only: false,
      attribute: null,
    },
    facets: {
      topics: [{ value: 'walkability', count: 2 }],
      tiers: [{ value: 'government', count: 3 }],
      attributes: [],
      contested: 0,
      published_min: '2019-01-01',
      published_max: '2025-06-01',
    },
    ...over,
  }
}

export function entity(over: Partial<Entity> = {}): Entity {
  return {
    entity_id: 1,
    canonical_name: 'Focus',
    node_type: 'intervention',
    jurisdiction: 'SG',
    aliases: null,
    topic_labels: ['walkability'],
    description: null,
    confidence: null,
    merged_from: null,
    redirects_to: null,
    is_annotation: false,
    supporting_chunk_ids: [],
    produced_by: null,
    model: null,
    quality_tier: null,
    produced_at: null,
    schema_version: 1,
    created_at: '2026-09-15T00:00:00Z',
    ...over,
  }
}

export function attribute(over: Partial<NodeAttribute> = {}): NodeAttribute {
  return {
    value_id: 1,
    name: 'density',
    scope: 'global',
    topic: null,
    value: 'high',
    value_numeric: null,
    confidence: 0.81,
    quality_tier: 3,
    supporting_chunk_ids: [12],
    ...over,
  }
}

export function hit(over: Partial<SearchHit> = {}): SearchHit {
  return {
    chunk_id: 12,
    source_id: 3,
    text: 'Casualty rates fell within treated zones.',
    page_or_offset: 2,
    chunk_index: 0,
    url: 'https://www.agency.test/report.pdf',
    title: 'A report',
    source_tier: 'government',
    publication_date: '2023-08-14',
    language: 'en',
    topic_labels: ['walkability'],
    passage_topics: null,
    page_unit: 'page',
    media_type: 'application/pdf',
    duplicate_of: null,
    score: 0,
    lexical_rank: null,
    vector_rank: null,
    age_days: null,
    decay: 1,
    score_before_decay: 0,
    ...over,
  }
}

export function evidence(over: Partial<Evidence> = {}): Evidence {
  return {
    hit: hit(),
    via: 'edge',
    relation_type: 'reduces',
    other_entity_id: 2,
    other_name: 'Neighbour',
    certainty: 'asserted',
    stance: null,
    ...over,
  }
}

export function pair(): ContestedPair {
  return {
    ours: {
      edge_id: 10,
      relation_type: 'increases',
      from_entity_id: 1,
      from_name: 'Focus',
      to_entity_id: 5,
      to_name: 'Claim',
      certainty: 'hedged',
      stance: 'supports',
      evidence: hit({ chunk_id: 20, text: 'Schemes may reduce severity.' }),
    },
    theirs: {
      edge_id: 11,
      relation_type: 'increases',
      from_entity_id: 1,
      from_name: 'Focus',
      to_entity_id: 6,
      to_name: 'Walking trips',
      certainty: 'asserted',
      stance: 'opposes',
      evidence: hit({
        chunk_id: 21,
        text: 'No measurable change in walking trips.',
        url: 'https://press.test/story',
        source_tier: 'press',
        publication_date: '2024-02-09',
      }),
    },
  }
}

export function detail(over: Partial<GraphNodeDetail> = {}): GraphNodeDetail {
  return {
    entity: entity(),
    home_topics: ['walkability'],
    contested: false,
    attributes: [attribute()],
    evidence: [evidence()],
    evidence_total: 1,
    contested_with: [],
    annotations: [],
    ...over,
  }
}

export function path(over: Partial<GraphPath> = {}): GraphPath {
  const nodes = [
    gnode({ entity_id: 1, canonical_name: 'Focus', role: 'focus' }),
    gnode({ entity_id: 7, canonical_name: 'Middle' }),
    gnode({ entity_id: 9, canonical_name: 'Far' }),
  ]
  return {
    source: 1,
    target: 9,
    max_depth: 4,
    found: true,
    hops: 2,
    nodes,
    edges: [gedge({ edge_id: 1, from_node: 1, to_node: 7 }), gedge({ edge_id: 2, from_node: 9, to_node: 7 })],
    ...over,
  }
}
