// @vitest-environment jsdom
/**
 * Find's filter rail (`B-173`, design `Explore`): topic, place, source type and years.
 *
 * Carries the guarantees the topic chips had (`P6-24`, `P2-23`, `B-72`): every configured
 * value is offered, a way back to the whole corpus exists, and the caveat that a filter
 * leaves out unexamined documents is said while narrowing and only then. The years add a
 * caveat of their own: a date filter leaves out every undated document.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { FindRail, FiltersButton, type FindRailProps } from '../src/explore/FindRail'
import { FIND_KINDS, KIND_LABEL, NO_FILTERS, type FindFilters } from '../src/lib/find'
import { DOC_KINDS, type listSites } from '../src/lib/api'
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
      loadSites={async () => ({ sites: [] })}
      {...more}
    />,
  )
  return onChange
}

function box(name: string | RegExp): HTMLInputElement {
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

describe('kind and site (B-254)', () => {
  const SITES = [
    { site: 'dailyledger.example', sources: 1208, latest: null },
    { site: 'metrowire.example', sources: 864, latest: null },
  ]

  function loader() {
    return vi.fn<typeof listSites>(async (params) => ({
      sites: params?.q ? [{ site: 'citypost.example', sources: 5, latest: null }] : SITES,
    }))
  }

  it('offers every kind the database holds but listings, news kinds first', () => {
    expect([...FIND_KINDS].sort()).toEqual(DOC_KINDS.filter((k) => k !== 'listing').sort())
    expect(FIND_KINDS.slice(0, 3)).toEqual(['news', 'press_release', 'opinion'])
  })

  it('offers every searchable kind and never listings', () => {
    rail()
    for (const kind of FIND_KINDS) expect(box(KIND_LABEL[kind]).checked).toBe(false)
    expect(screen.queryByRole('checkbox', { name: 'Listings' })).toBeNull()
  })

  it('ticks a kind into the filters and says unsorted documents are left out', () => {
    const onChange = rail()
    expect(screen.queryByText(/not yet sorted by kind/)).toBeNull()
    fireEvent.click(box('Press releases'))
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, kinds: ['press_release'] })
    cleanup()
    rail({ ...NO_FILTERS, kinds: ['news'] })
    expect(screen.getByText(/not yet sorted by kind/)).toBeTruthy()
  })

  it('lists the most-held sites for the chosen kinds, with counts', async () => {
    const load = loader()
    const onChange = rail({ ...NO_FILTERS, kinds: ['news'] }, { loadSites: load })
    await waitFor(() => expect(box(/^dailyledger\.example/)).toBeTruthy())
    expect(load.mock.calls[0]![0]).toMatchObject({ doc_kind: ['news'] })
    expect(screen.getByText('1208')).toBeTruthy()
    fireEvent.click(box(/^metrowire\.example/))
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, kinds: ['news'], sites: ['metrowire.example'] })
  })

  it('keeps a chosen site listed to untick, even one the list does not hold', async () => {
    const onChange = rail({ ...NO_FILTERS, sites: ['elsewhere.example'] }, { loadSites: loader() })
    await waitFor(() => expect(box(/^dailyledger\.example/)).toBeTruthy())
    expect(box('elsewhere.example').checked).toBe(true)
    fireEvent.click(box('elsewhere.example'))
    expect(onChange).toHaveBeenLastCalledWith(NO_FILTERS)
  })

  it('takes a pasted address as its site, and refuses text with no host in it', () => {
    const onChange = rail()
    const input = screen.getByRole('combobox', { name: 'Find a site' })
    fireEvent.change(input, { target: { value: 'not a host' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('alert').textContent).toMatch(/Not a site: not a host/)

    fireEvent.change(input, { target: { value: 'https://WWW.CityPost.example/news/a' } })
    expect(screen.queryByRole('alert')).toBeNull()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, sites: ['citypost.example'] })
    expect((input as HTMLInputElement).value).toBe('')
  })

  it('suggests sites by part of a name, only once enough is typed', async () => {
    const load = loader()
    rail(NO_FILTERS, { loadSites: load })
    const input = screen.getByRole('combobox', { name: 'Find a site' })
    fireEvent.change(input, { target: { value: 'c' } })
    await new Promise((r) => setTimeout(r, 300))
    expect(load.mock.calls.some(([params]) => params?.q)).toBe(false)
    fireEvent.change(input, { target: { value: 'city' } })
    await waitFor(() => expect(document.querySelector('option[value="citypost.example"]')).toBeTruthy())
    expect(load.mock.calls.at(-1)![0]).toMatchObject({ q: 'city' })
  })

  it('still filters by what is typed when the site list cannot be read', async () => {
    const onChange = rail(NO_FILTERS, { loadSites: async () => Promise.reject(new Error('down')) })
    const input = screen.getByRole('combobox', { name: 'Find a site' })
    fireEvent.change(input, { target: { value: 'example.org' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, sites: ['example.org'] })
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
