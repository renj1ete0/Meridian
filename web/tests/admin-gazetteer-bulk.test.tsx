/**
 * Working through the gazetteer queue at volume (task P6-28, spec §5.6).
 *
 * The harvest files terms by the thousand, so "a two-minute weekly task" now
 * depends on three things the tests below hold: a page can be selected and
 * decided in one request, the keys decide the row under the cursor and only
 * when nobody is typing, and a selection never carries ids the curator can no
 * longer see.
 */
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { GazetteerQueue, KEYS, PAGE_SIZE, keyDecision } from '../src/admin/GazetteerQueue'
import { GAZETTEER_BULK_MAX, type GazetteerRow, type GazetteerTerm } from '../src/lib/api'

afterEach(cleanup)

function term(id: number, over: Partial<GazetteerTerm> = {}): GazetteerTerm {
  return {
    term_id: id,
    canonical: `Provisional Bureau ${id}`,
    aliases: [`PB${id}`],
    entity_type: 'concept',
    jurisdiction: null,
    ambiguous: false,
    topic_labels: null,
    source: 'auto_acronym',
    approved: false,
    occurrence_count: 3,
    rejected_at: null,
    created_at: '2026-09-15T00:00:00Z',
    ...over,
  }
}

function row(id: number, over: Partial<GazetteerTerm> = {}): GazetteerRow {
  return {
    term: term(id, over),
    will_load: false,
    withheld_reason: 'unapproved',
    collides_with: [],
  }
}

const COUNTS = { pending: 3028, approved: 501, rejected: 4 }

describe('deciding a page at once', () => {
  it('selects the page and sends one decision for it', () => {
    const onBulk = vi.fn()
    render(<GazetteerQueue rows={[row(1), row(2), row(3)]} state="pending" counts={COUNTS} onBulk={onBulk} />)

    fireEvent.click(screen.getByLabelText('Select every term on this page'))
    expect(screen.getByText('3 selected')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }))

    expect(onBulk).toHaveBeenCalledWith([1, 2, 3], 'approve')
  })

  it('gives the two bulk decisions the same treatment', () => {
    // §2: colour carries no verdict, and neither does weight.
    render(<GazetteerQueue rows={[row(1)]} state="pending" counts={COUNTS} />)
    fireEvent.click(screen.getByLabelText('Select Provisional Bureau 1'))

    const approve = screen.getByRole('button', { name: 'Approve selected' })
    const reject = screen.getByRole('button', { name: 'Turn down selected' })
    expect(approve.className).toBe(reject.className)
  })

  it('puts back only the turned-down rows of a mixed selection', () => {
    // In the "all" view a selection can hold both. Restoring a waiting term is
    // not a decision anybody made, so it is not sent.
    const onBulk = vi.fn()
    render(
      <GazetteerQueue
        rows={[row(1), row(2, { rejected_at: '2026-09-15T00:00:00Z' })]}
        state="all"
        counts={COUNTS}
        onBulk={onBulk}
      />,
    )

    fireEvent.click(screen.getByLabelText('Select every term on this page'))
    fireEvent.click(screen.getByRole('button', { name: 'Put back selected' }))
    fireEvent.click(screen.getByRole('button', { name: 'Turn down selected' }))

    expect(onBulk.mock.calls).toEqual([
      [[2], 'restore'],
      [[1], 'reject'],
    ])
  })

  it('drops ids from the selection once they leave the page', () => {
    // A refetch after a decision is a new set of rows. An id still selected
    // but no longer shown would be decided without being seen.
    const onBulk = vi.fn()
    const { rerender } = render(
      <GazetteerQueue rows={[row(1), row(2)]} state="pending" counts={COUNTS} onBulk={onBulk} />,
    )
    fireEvent.click(screen.getByLabelText('Select every term on this page'))

    rerender(<GazetteerQueue rows={[row(2), row(3)]} state="pending" counts={COUNTS} onBulk={onBulk} />)
    fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }))

    expect(onBulk).toHaveBeenCalledWith([2], 'approve')
  })

  it('pages within what one bulk request may carry', () => {
    expect(PAGE_SIZE).toBeLessThanOrEqual(GAZETTEER_BULK_MAX)
  })

  it('says where in the queue the page is, against the unfiltered count', () => {
    const onPage = vi.fn()
    render(
      <GazetteerQueue rows={[row(1), row(2)]} state="pending" counts={COUNTS} offset={100} hasMore onPage={onPage} />,
    )

    expect(screen.getByText('101–102 of 3,028')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(onPage.mock.calls).toEqual([[200], [0]])
  })
})

describe('the keys', () => {
  it('decide the row under the cursor', () => {
    const onDecide = vi.fn()
    render(<GazetteerQueue rows={[row(1), row(2)]} state="pending" counts={COUNTS} onDecide={onDecide} />)
    const rows = document.querySelectorAll<HTMLTableRowElement>('tr[data-term]')

    rows[0]!.focus()
    fireEvent.keyDown(rows[0]!, { key: 'j' })
    fireEvent.keyDown(document.activeElement!, { key: 'a' })

    expect(onDecide).toHaveBeenCalledWith(2, 'approve')
  })

  it('select with x, and the selection is what a bulk decision sends', () => {
    const onBulk = vi.fn()
    render(<GazetteerQueue rows={[row(1), row(2)]} state="pending" counts={COUNTS} onBulk={onBulk} />)
    const first = document.querySelector<HTMLTableRowElement>('tr[data-term]')!

    first.focus()
    fireEvent.keyDown(first, { key: 'x' })
    fireEvent.click(screen.getByRole('button', { name: 'Turn down selected' }))

    expect(onBulk).toHaveBeenCalledWith([1], 'reject')
  })

  it('belong to nobody who is typing in a control', () => {
    // The type dropdown answers to letters itself; "a" there picks "agency",
    // and must not also approve the row.
    const onDecide = vi.fn()
    render(<GazetteerQueue rows={[row(1)]} state="pending" counts={COUNTS} onDecide={onDecide} />)

    fireEvent.keyDown(screen.getByLabelText('Type for Provisional Bureau 1'), { key: 'a' })

    expect(onDecide).not.toHaveBeenCalled()
  })

  it('ask only for decisions the row can take', () => {
    const waiting = row(1)
    const approved = row(2, { approved: true })
    const rejected = row(3, { rejected_at: '2026-09-15T00:00:00Z' })

    expect(keyDecision('a', waiting)).toBe('approve')
    expect(keyDecision('a', approved)).toBeNull()
    expect(keyDecision('d', rejected)).toBeNull()
    expect(keyDecision('u', rejected)).toBe('restore')
    expect(keyDecision('u', waiting)).toBeNull()
  })

  it('are printed on the screen', () => {
    render(<GazetteerQueue rows={[row(1)]} state="pending" counts={COUNTS} />)

    const hint = KEYS.map(({ key, does }) => `${key} ${does}`).join(' · ')
    expect(screen.getByText(hint)).toBeTruthy()
  })
})

describe('finding a term (B-209)', () => {
  it('passes what is typed to the search, and offers no box without one', () => {
    const onSearch = vi.fn()
    render(<GazetteerQueue rows={[row(1)]} state="pending" counts={COUNTS} search="" onSearch={onSearch} />)

    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a term' }), { target: { value: 'bureau' } })
    expect(onSearch).toHaveBeenCalledWith('bureau')
    cleanup()

    render(<GazetteerQueue rows={[row(1)]} state="pending" counts={COUNTS} />)
    expect(screen.queryByRole('searchbox')).toBeNull()
  })

  it('typing a key letter in the box decides nothing', () => {
    const onDecide = vi.fn()
    render(
      <GazetteerQueue
        rows={[row(1)]}
        state="pending"
        counts={COUNTS}
        onDecide={onDecide}
        search=""
        onSearch={vi.fn()}
      />,
    )

    fireEvent.keyDown(screen.getByRole('searchbox', { name: 'Find a term' }), { key: 'a' })

    expect(onDecide).not.toHaveBeenCalled()
  })
})

describe('the pager over a search (B-209)', () => {
  it('counts what the search matched, not the whole state', () => {
    render(<GazetteerQueue rows={[row(1), row(2)]} state="pending" counts={COUNTS} matched={2} search="bureau" />)
    expect(screen.getByText('1–2 of 2 matching')).toBeTruthy()
    cleanup()

    render(<GazetteerQueue rows={[row(1), row(2)]} state="pending" counts={COUNTS} />)
    expect(screen.getByText(`1–2 of ${COUNTS.pending.toLocaleString()}`)).toBeTruthy()
  })

  it('says a search found nothing in the same terms', () => {
    render(<GazetteerQueue rows={[]} state="pending" counts={COUNTS} matched={0} search="zzz" />)
    expect(screen.getByText('0 of 0 matching')).toBeTruthy()
    // Not the empty-queue line, which would claim every term had been decided.
    expect(screen.getByText(/No term under this tab contains “zzz”/)).toBeTruthy()
    expect(screen.queryByText(/has been decided/)).toBeNull()
  })
})
