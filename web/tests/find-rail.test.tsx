// @vitest-environment jsdom
/**
 * Find's filter rail (`B-173`, design `Explore`): topic, place, source type and years.
 *
 * Carries the guarantees the topic chips had (`P6-24`, `P2-23`, `B-72`): every configured
 * value is offered, a way back to the whole corpus exists, and the caveat that a filter
 * leaves out unexamined documents is said while narrowing and only then. The years add a
 * caveat of their own: a date filter leaves out every undated document.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FindRail, FiltersButton, type FindRailProps } from '../src/explore/FindRail'
import { NO_FILTERS, type FindFilters } from '../src/lib/find'
import { SOURCE_TIERS, TIER_LABEL } from '../src/ui/Tier'

afterEach(cleanup)

const PLACES = 'ABCDEFGHIJ'.split('').map((c, i) => ({ code: `X${c}`, name: `Place ${i + 1}` }))

function rail(filters: FindFilters = NO_FILTERS, more: Partial<FindRailProps> = {}) {
  const onChange = vi.fn<(next: FindFilters) => void>()
  render(
    <FindRail
      topics={['alpha', 'beta-gamma']}
      places={PLACES}
      filters={filters}
      onChange={onChange}
      views={[]}
      onOpenView={() => {}}
      {...more}
    />,
  )
  return onChange
}

function box(name: string): HTMLInputElement {
  return screen.getByRole('checkbox', { name }) as HTMLInputElement
}

describe('what it offers', () => {
  it('offers every topic in words and every source type, unticked', () => {
    rail()
    expect(box('alpha').checked).toBe(false)
    expect(box('beta gamma').checked).toBe(false)
    for (const tier of SOURCE_TIERS) expect(box(TIER_LABEL[tier]).checked).toBe(false)
  })

  it('folds a long place list, but never hides a chosen place', () => {
    rail({ ...NO_FILTERS, places: ['XJ'] })
    expect(screen.queryByRole('checkbox', { name: 'Place 9' })).toBeNull()
    expect(box('Place 10').checked).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '1 more' }))
    expect(screen.getByRole('checkbox', { name: 'Place 9' })).toBeTruthy()
  })

  it('offers all-at-once only when two topics are chosen', () => {
    rail({ ...NO_FILTERS, topics: ['alpha'] })
    expect(screen.queryByRole('checkbox', { name: 'all at once' })).toBeNull()
    cleanup()
    rail({ ...NO_FILTERS, topics: ['alpha', 'beta-gamma'] })
    expect(screen.getByRole('checkbox', { name: 'all at once' })).toBeTruthy()
  })

  it('offers a way back to the whole corpus only while something narrows', () => {
    rail()
    expect(screen.queryByRole('button', { name: /^clear/ })).toBeNull()
    cleanup()
    const onChange = rail({ ...NO_FILTERS, tiers: ['press'], from: 2020 })
    fireEvent.click(screen.getByRole('button', { name: 'clear 2' }))
    expect(onChange).toHaveBeenCalledWith(NO_FILTERS)
  })
})

describe('what a click changes', () => {
  it('ticks and unticks a value without touching the others', () => {
    const start = { ...NO_FILTERS, topics: ['alpha'], places: ['XA'] }
    const onChange = rail(start)
    fireEvent.click(box(TIER_LABEL.government))
    expect(onChange).toHaveBeenLastCalledWith({ ...start, tiers: ['government'] })
    fireEvent.click(box('alpha'))
    expect(onChange).toHaveBeenLastCalledWith({ ...start, topics: [] })
  })

  it('takes a year on Enter or leaving the box, not while it is typed', () => {
    const onChange = rail()
    const from = screen.getByRole('textbox', { name: 'Published from' })
    fireEvent.change(from, { target: { value: '20' } })
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.change(from, { target: { value: '2019' } })
    fireEvent.keyDown(from, { key: 'Enter' })
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, from: 2019 })
  })

  it('refuses a year that is not one, and puts the box back', () => {
    const onChange = rail({ ...NO_FILTERS, to: 2020 })
    const to = screen.getByRole('textbox', { name: 'Published to' }) as HTMLInputElement
    fireEvent.change(to, { target: { value: '99' } })
    fireEvent.blur(to)
    expect(onChange).not.toHaveBeenCalled()
    expect(to.value).toBe('2020')
    // Letters never reach the box at all.
    fireEvent.change(to, { target: { value: '20x1' } })
    expect(to.value).toBe('201')
  })

  it('clears a year when the box is emptied', () => {
    const onChange = rail({ ...NO_FILTERS, from: 2015 })
    const from = screen.getByRole('textbox', { name: 'Published from' })
    fireEvent.change(from, { target: { value: '' } })
    fireEvent.blur(from)
    expect(onChange).toHaveBeenLastCalledWith(NO_FILTERS)
  })
})

describe('the caveats, said while narrowing and only then', () => {
  it('says unexamined documents are left out only when a topic narrows and some exist', () => {
    rail(NO_FILTERS, { unexaminedTopics: true })
    expect(screen.queryByText(/not yet examined for topics/)).toBeNull()
    cleanup()
    rail({ ...NO_FILTERS, topics: ['alpha'] }, { unexaminedTopics: false })
    expect(screen.queryByText(/not yet examined for topics/)).toBeNull()
    cleanup()
    rail({ ...NO_FILTERS, topics: ['alpha'] }, { unexaminedTopics: true })
    expect(screen.getByText(/not yet examined for topics/)).toBeTruthy()
  })

  it('says undated documents are left out whenever a year is set', () => {
    rail({ ...NO_FILTERS, tiers: ['press'] })
    expect(screen.queryByText(/no date are left out/)).toBeNull()
    cleanup()
    rail({ ...NO_FILTERS, to: 2010 })
    expect(screen.getByText(/no date are left out/)).toBeTruthy()
  })
})

describe('the fold below lg', () => {
  it('counts what narrows and says whether it is open', () => {
    render(<FiltersButton open={false} count={3} onClick={() => {}} />)
    const button = screen.getByRole('button', { name: 'Filters · 3' })
    expect(button.getAttribute('aria-expanded')).toBe('false')
    expect(button.getAttribute('aria-controls')).toBe('find-rail')
  })
})
