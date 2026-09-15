/**
 * Saving the current search as a view (task P6-09, spec §12.5).
 *
 * §12.5 asks for "a filter set plus focus node, named and re-openable", and
 * makes the same point about it that it makes about annotations: build the
 * affordance so it is reached, or it will not be used. The control is therefore
 * offered *while looking at results* — the moment a view is worth saving — and
 * not from a menu.
 *
 * The rest is about the two failure modes of a naming default. A default that is
 * merely the query makes two views of the same words indistinguishable in a
 * list, which is the one thing the name has to prevent; no default at all means
 * an empty box at the moment somebody wanted one click.
 */
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { SaveView, suggestedName } from '../src/explore/SaveView'

afterEach(cleanup)

describe('when the control is offered', () => {
  it('appears once there is a search behind the results', () => {
    render(<SaveView query="walkability" filters={{}} />)

    expect(screen.getByRole('button', { name: /save this view/i })).toBeTruthy()
  })

  it('stays away when there is nothing to save', () => {
    // A disabled control that is always on screen teaches the reader to stop
    // seeing it, and an empty search box has no view behind it.
    const { container } = render(<SaveView query="   " filters={{}} />)

    expect(container.innerHTML).toBe('')
  })
})

describe('the suggested name', () => {
  it('is the query when nothing narrowed it', () => {
    expect(suggestedName('walkability', {})).toBe('walkability')
  })

  it('carries the topics that narrowed it', () => {
    // Two views of the same words are otherwise indistinguishable in the list,
    // which is the one thing a name has to prevent.
    expect(suggestedName('funding', { topic: ['walkability', 'robotics'] })).toBe(
      'funding · walkability, robotics',
    )
  })

  it('ignores a filter shape it does not recognise', () => {
    // Filters are free-form because they mirror `SearchFilters`, which grows.
    // A default name that threw on an unfamiliar key would break saving
    // whenever a filter was added.
    expect(suggestedName('funding', { topic: 'not-an-array' })).toBe('funding')
  })
})

describe('saving', () => {
  it('hands back the name, the query and the filters as they were', () => {
    // Verbatim: a view that reopens with different filters than it was saved
    // with is worse than one that failed to save, because nothing says so.
    const onSave = vi.fn()
    render(<SaveView query="funding" filters={{ topic: ['robotics'] }} onSave={onSave} />)

    fireEvent.click(screen.getByRole('button', { name: /save this view/i }))
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    expect(onSave).toHaveBeenCalledWith('funding · robotics', 'funding', { topic: ['robotics'] })
  })

  it('pre-fills the name so saving is one more click', () => {
    render(<SaveView query="funding" filters={{}} />)

    fireEvent.click(screen.getByRole('button', { name: /save this view/i }))

    expect(screen.getByLabelText(/name for this view/i).getAttribute('value')).toBe('funding')
  })

  it('refuses to save a blank name rather than inventing one', () => {
    const onSave = vi.fn()
    render(<SaveView query="funding" filters={{}} onSave={onSave} />)
    fireEvent.click(screen.getByRole('button', { name: /save this view/i }))
    fireEvent.change(screen.getByLabelText(/name for this view/i), { target: { value: '  ' } })

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    expect(onSave).not.toHaveBeenCalled()
  })

  it('shows the API’s own refusal', () => {
    // On an instance without Cloudflare Access this is a 503 naming the two
    // variables that would allow it (`P6-13`), which is the only actionable
    // thing in the response — replacing it with "could not save" throws it away.
    render(
      <SaveView query="funding" filters={{}} error="Admin is closed: set CF_ACCESS_AUD." />,
    )
    fireEvent.click(screen.getByRole('button', { name: /save this view/i }))

    expect(screen.getByText(/CF_ACCESS_AUD/)).toBeTruthy()
  })

  it('can be backed out of', () => {
    render(<SaveView query="funding" filters={{}} />)
    fireEvent.click(screen.getByRole('button', { name: /save this view/i }))

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    expect(screen.getByRole('button', { name: /save this view/i })).toBeTruthy()
  })
})
