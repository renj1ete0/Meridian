/**
 * The node detail panel (task P6-04, spec §12.5, §7, §9).
 *
 * Nothing renders this yet — there are no entities, because `P4-01` has not run
 * and nothing has ever written an edge. That is deliberate rather than
 * premature: the panel's hard parts are about *how a claim is presented*, and
 * those do not get easier by waiting for data.
 *
 * Three of them.
 *
 * **Scope is not decoration.** §7.1 splits attributes into ones that apply
 * across the corpus and ones that only mean something inside a topic. A flat row
 * of tags asserts they are the same kind of claim, and comparing a topic-local
 * dimension across topics is a comparison nobody made.
 *
 * **Confidence is on the tag.** §7 makes it first-class, and a tag whose
 * confidence a reader must hover for is a claim rendered as a fact.
 *
 * **Overflow expands.** A "+7 more" that cannot be reached is the same as not
 * having the tags at all.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { DEFAULT_VISIBLE, NodePanel, describeConfidence, groupByScope } from '../src/explore/NodePanel'
import type { Entity, NodeAttribute, NodeDetail, SearchHit } from '../src/lib/api'

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function entity(over: Partial<Entity> = {}): Entity {
  return {
    entity_id: 1,
    canonical_name: 'Silver Zone',
    node_type: 'scheme',
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

function attribute(over: Partial<NodeAttribute> = {}): NodeAttribute {
  return {
    value_id: Math.floor(Math.random() * 1e9),
    name: 'maturity',
    scope: 'global',
    topic: null,
    value: 'piloted',
    value_numeric: null,
    confidence: 0.8,
    quality_tier: 3,
    supporting_chunk_ids: [12],
    ...over,
  }
}

function hit(): SearchHit {
  return {
    chunk_id: 12,
    source_id: 3,
    text: 'The scheme was piloted in four precincts.',
    page_or_offset: 2,
    chunk_index: 0,
    url: 'https://example.test/a.pdf',
    title: 'A report',
    source_tier: 'government',
    publication_date: '2025-01-01',
    language: 'en',
    topic_labels: ['walkability'],
    page_unit: 'page',
    media_type: 'application/pdf',
    duplicate_of: null,
    score: 0,
    lexical_rank: null,
    vector_rank: null,
  }
}

function node(over: Partial<NodeDetail> = {}): NodeDetail {
  return {
    entity: entity(over.entity),
    attributes: [attribute()],
    supporting: [hit()],
    contested_edges: 0,
    annotations: [],
    ...over,
  }
}

// --------------------------------------------------------------------------
// Scope (§7.1)
// --------------------------------------------------------------------------

describe('tags are grouped by what they can be compared against', () => {
  it('separates corpus-wide dimensions from topic-local ones', () => {
    const groups = groupByScope([
      attribute({ scope: 'global', name: 'maturity' }),
      attribute({ scope: 'topic_local', name: 'footfall', topic: 'walkability' }),
    ])

    expect(groups.map((g) => g.scope)).toEqual(['global', 'topic_local'])
    expect(groups[0]!.items).toHaveLength(1)
  })

  it('omits a group with nothing in it', () => {
    // An empty heading reads as a group that failed to load.
    const groups = groupByScope([attribute({ scope: 'global' })])

    expect(groups).toHaveLength(1)
  })

  it('says what each scope means, rather than naming the enum', () => {
    // `topic_local` is a column value. A reader needs to know that comparing it
    // across topics is a comparison nobody made.
    const rendered = text(
      renderToStaticMarkup(
        <NodePanel node={node({ attributes: [attribute({ scope: 'topic_local' })] })} />,
      ),
    )

    expect(rendered).toContain('within a topic')
    expect(rendered).toContain('Only meaningful inside its topic')
    expect(rendered).not.toContain('topic_local')
  })
})

// --------------------------------------------------------------------------
// Confidence (§7)
// --------------------------------------------------------------------------

describe('a tag carries its confidence', () => {
  it('shows it as a number', () => {
    // Rounding to "high" throws away the difference between 0.61 and 0.94,
    // which is most of what a reader weighing two contradictory tags has.
    expect(describeConfidence(0.61)).toBe('61%')
    expect(describeConfidence(0.94)).toBe('94%')
  })

  it('says so when there is none rather than showing nothing', () => {
    expect(describeConfidence(null)).toContain('no confidence')
  })

  it('renders it beside the value', () => {
    const rendered = text(renderToStaticMarkup(<NodePanel node={node()} />))

    expect(rendered).toContain('piloted')
    expect(rendered).toContain('80%')
  })

  it('marks a tag with no evidence behind it', () => {
    // §2 principle 3. The schema requires the array, so an empty one is a data
    // problem — and saying so beats rendering an unsupported claim silently.
    const rendered = text(
      renderToStaticMarkup(
        <NodePanel node={node({ attributes: [attribute({ supporting_chunk_ids: [] })] })} />,
      ),
    )

    expect(rendered).toContain('no evidence')
  })
})

// --------------------------------------------------------------------------
// Overflow (§7.3's cap is a dozen, and a dozen is still a lot in a panel)
// --------------------------------------------------------------------------

describe('overflow folds away rather than truncating', () => {
  const many = Array.from({ length: DEFAULT_VISIBLE + 3 }, (_, i) =>
    attribute({ name: `attr-${i}`, value_id: i }),
  )

  it('shows the count of what is folded', () => {
    const rendered = text(renderToStaticMarkup(<NodePanel node={node({ attributes: many })} />))

    expect(rendered).toContain('3 more')
  })

  it('keeps the folded tags in the document', () => {
    // A "+3 more" that drops the tags is truncation wearing a disclosure's
    // clothes: the reader is told there is more and cannot reach it.
    const markup = renderToStaticMarkup(<NodePanel node={node({ attributes: many })} />)

    expect(markup).toContain('<details')
    for (const a of many) expect(text(markup)).toContain(a.name)
  })

  it('folds nothing when everything fits', () => {
    const markup = renderToStaticMarkup(<NodePanel node={node()} />)

    expect(markup).not.toContain('<details')
  })
})

// --------------------------------------------------------------------------
// Contested edges (§9) and the empty states
// --------------------------------------------------------------------------

describe('what the panel says when something is wrong or missing', () => {
  it('warns that sources disagree, in the singular', () => {
    const rendered = text(renderToStaticMarkup(<NodePanel node={node({ contested_edges: 1 })} />))

    expect(rendered).toContain('One connection here is contested')
  })

  it('and in the plural', () => {
    const rendered = text(renderToStaticMarkup(<NodePanel node={node({ contested_edges: 4 })} />))

    expect(rendered).toContain('4 connections here are contested')
  })

  it('says nothing about contest when there is none', () => {
    expect(text(renderToStaticMarkup(<NodePanel node={node()} />))).not.toContain('contested')
  })

  it('explains an untagged node rather than showing a blank', () => {
    const rendered = text(
      renderToStaticMarkup(<NodePanel node={node({ attributes: [], supporting: [] })} />),
    )

    expect(rendered).toContain('Nothing has been tagged')
  })

  it('calls missing evidence a gap', () => {
    // Not "no results". Every tag is supposed to name the chunk it came from,
    // so an empty evidence section is a defect and reads as one.
    const rendered = text(renderToStaticMarkup(<NodePanel node={node({ supporting: [] })} />))

    expect(rendered).toContain('a gap')
  })

  it('warns that the evidence may predate the current page', () => {
    // `P1-32`: this is the one place superseded chunks are shown, because the
    // tag was derived from this text and the page may have changed since.
    const rendered = text(renderToStaticMarkup(<NodePanel node={node()} />))

    expect(rendered).toContain('as they read when they were read')
  })
})
