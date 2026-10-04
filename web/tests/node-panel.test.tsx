// @vitest-environment jsdom
/**
 * The node panel beside the canvas (tasks P6-04, P6-01; spec §12.5, §7, §9;
 * design `Explore`).
 *
 * The redesign moved the blocks; these tests hold the rules that are about
 * honesty rather than layout, which survive any redesign:
 *
 * - confidence is on the chip, as a number;
 * - a tag with no evidence says so;
 * - nothing is invented to fill a block — no description, no block;
 * - §6: the contested badge carries the dagger, and only appears when an edge
 *   is actually contested;
 * - §9: a contested pair shows *both* sides.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  EVIDENCE_VISIBLE,
  NodePanel,
  citationFor,
  domainOf,
  excerpt,
  formatConfidence,
  metaLine,
} from '../src/explore/NodePanel'
import { attribute, detail, entity, evidence, hit, pair } from './graph-fixtures'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('helpers', () => {
  it('confidence is two decimals, and absent is a dash, not zero', () => {
    expect(formatConfidence(0.806)).toBe('0.81')
    expect(formatConfidence(0)).toBe('0.00')
    expect(formatConfidence(null)).toBe('—')
  })

  it('a domain drops www and survives a bad URL', () => {
    expect(domainOf('https://www.agency.test/x')).toBe('agency.test')
    expect(domainOf('not a url')).toBe('not a url')
  })

  it('an excerpt cuts at a word and marks the cut', () => {
    const long = 'word '.repeat(100)
    const cut = excerpt(long, 50)
    expect(cut.endsWith('…')).toBe(true)
    expect(cut.length).toBeLessThanOrEqual(51)
    expect(excerpt('short text', 50)).toBe('short text')
  })

  it('the meta line is type, topics, jurisdiction — only what is known', () => {
    expect(metaLine(detail())).toBe('intervention · walkability · SG')
    expect(metaLine(detail({ home_topics: [], entity: entity({ jurisdiction: null, node_type: 'use_case' }) }))).toBe(
      'use case',
    )
  })

  it('a citation names the node and its address', () => {
    expect(citationFor(detail(), 'https://m.test')).toBe(
      'Focus (intervention, SG). Meridian node #1. https://m.test/nodes/1',
    )
  })
})

describe('the panel', () => {
  it('shows the description when there is one, and nothing when there is not', () => {
    render(<NodePanel detail={detail({ entity: entity({ description: 'A scheme.' }) })} />)
    expect(screen.getByText('A scheme.')).toBeTruthy()
    cleanup()
    const { container } = render(<NodePanel detail={detail()} />)
    // The header holds the title and meta only: no invented prose.
    expect(container.querySelector('header')!.querySelectorAll('p')).toHaveLength(1)
  })

  it('§6: the badge appears only when contested, with the dagger', () => {
    render(<NodePanel detail={detail()} />)
    expect(screen.queryByText(/Contested$/)).toBeNull()
    cleanup()
    render(<NodePanel detail={detail({ contested: true })} />)
    const badge = screen.getByText(/Contested$/)
    expect(badge.textContent!.startsWith('†')).toBe(true)
  })

  it('puts confidence on each chip, in the accent only when confident', () => {
    render(
      <NodePanel
        detail={detail({
          attributes: [
            attribute({ value_id: 1, confidence: 0.81 }),
            attribute({ value_id: 2, name: 'shade', confidence: 0.29 }),
          ],
        })}
      />,
    )
    expect(screen.getByText('0.81').className).toContain('text-accent-graph')
    expect(screen.getByText('0.29').className).toContain('text-text-faint')
  })

  it('says when a tag has no evidence', () => {
    render(<NodePanel detail={detail({ attributes: [attribute({ supporting_chunk_ids: [] })] })} />)
    expect(screen.getByText(/no evidence/)).toBeTruthy()
  })

  it('states an empty attribute list and an empty evidence list', () => {
    render(<NodePanel detail={detail({ attributes: [], evidence: [], evidence_total: 0 })} />)
    expect(screen.getByText(/None recorded yet/)).toBeTruthy()
    expect(screen.getByText(/No passage cites this concept yet/)).toBeTruthy()
  })

  it('opens with a few passages and reaches the rest', () => {
    const many = Array.from({ length: EVIDENCE_VISIBLE + 3 }, (_, i) =>
      evidence({ hit: hit({ chunk_id: 100 + i, text: `passage ${i}` }) }),
    )
    render(<NodePanel detail={detail({ evidence: many, evidence_total: many.length })} />)
    expect(screen.queryByText(/passage 6/)).toBeNull()
    fireEvent.click(screen.getByText(`Show all ${many.length}`))
    expect(screen.getByText(/passage 6/)).toBeTruthy()
  })

  it('says how many passages the panel did not carry', () => {
    render(<NodePanel detail={detail({ evidence_total: 55 })} />)
    expect(screen.getByText('54 more not carried by this panel.')).toBeTruthy()
  })

  it('shows hedged certainty in brass, and none for an attribute citation', () => {
    render(
      <NodePanel
        detail={detail({
          evidence: [
            evidence({ certainty: 'hedged' }),
            evidence({ hit: hit({ chunk_id: 13 }), via: 'attribute', certainty: null }),
          ],
          evidence_total: 2,
        })}
      />,
    )
    expect(screen.getByText('hedged').className).toContain('text-accent-attention')
    expect(screen.getAllByText(/hedged|asserted|qualified|measured/)).toHaveLength(1)
  })

  it('§9: a contested pair names the other side and compares both', () => {
    render(<NodePanel detail={detail({ contested: true, contested_with: [pair()] })} />)
    expect(screen.getByText('Walking trips')).toBeTruthy()
    fireEvent.click(screen.getByText('Compare →'))
    // Both passages, this node's first.
    const quotes = screen.getAllByText(/Schemes may reduce severity|No measurable change in walking trips/)
    expect(quotes.map((q) => q.textContent!.includes('Schemes'))).toContain(true)
    expect(screen.getByText('This concept')).toBeTruthy()
  })

  it('shows the latest note, and states the absence of one', () => {
    render(<NodePanel detail={detail()} />)
    expect(screen.getByText(/No note on this concept/)).toBeTruthy()
    cleanup()
    const note = (title: string, at: string) => ({
      entity_id: 90,
      title,
      body: `${title} body`,
      about: [],
      supporting_chunk_ids: [],
      topic_labels: null,
      produced_by: 'human',
      produced_at: at,
      created_at: at,
    })
    render(
      <NodePanel
        detail={detail({
          annotations: [note('Latest', '2026-08-31T10:00:00Z'), note('Older', '2026-08-01T10:00:00Z')],
        })}
      />,
    )
    expect(screen.getByText('Latest')).toBeTruthy()
    expect(screen.getByText('2026-08-31')).toBeTruthy()
    expect(screen.getByText('1 earlier note on this concept.')).toBeTruthy()
    expect(screen.queryByText('Older')).toBeNull()
  })

  it('Annotate opens a composer that writes a note about this node', () => {
    const onWrite = vi.fn()
    render(<NodePanel detail={detail()} onWrite={onWrite} />)
    fireEvent.click(screen.getByText('Annotate'))
    fireEvent.change(screen.getByLabelText('Title for this note'), { target: { value: '  Check  ' } })
    fireEvent.change(screen.getByLabelText('The note itself'), { target: { value: '' } })
    fireEvent.click(screen.getByText('Keep it'))
    expect(onWrite).toHaveBeenCalledWith({ title: 'Check', body: null, about: [1], supporting_chunk_ids: [] })
  })

  it('refuses a note with no title', () => {
    const onWrite = vi.fn()
    render(<NodePanel detail={detail()} onWrite={onWrite} />)
    fireEvent.click(screen.getByText('Annotate'))
    expect((screen.getByText('Keep it') as HTMLButtonElement).disabled).toBe(true)
  })

  it('Expand is disabled with the reason when nothing more can be shown', () => {
    const onExpand = vi.fn()
    render(<NodePanel detail={detail()} onExpand={onExpand} expandBlocked="All 4 neighbours are shown." />)
    const button = screen.getByText('Expand neighbours') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.title).toBe('All 4 neighbours are shown.')
  })

  it('Cite copies the citation', async () => {
    const writeText = vi.fn(async () => {})
    vi.stubGlobal('navigator', { clipboard: { writeText } })
    render(<NodePanel detail={detail()} origin="https://m.test" />)
    fireEvent.click(screen.getByText('Cite'))
    expect(writeText).toHaveBeenCalledWith(citationFor(detail(), 'https://m.test'))
    expect(await screen.findByText('Copied')).toBeTruthy()
  })

  it('names the node id in the action bar', () => {
    render(<NodePanel detail={detail()} />)
    expect(screen.getByText('node #1')).toBeTruthy()
  })
})
