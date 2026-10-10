// @vitest-environment jsdom
/**
 * The Display section's time-zone choice (`B-145`, `B-210`).
 *
 * Four hundred zones in one select are unusable by type-ahead, which matches only from the
 * start of `Area/City`. These tests hold that a city, a spaced name or an offset reaches its
 * zone, and that narrowing the list never changes what is chosen behind the curator's back.
 */
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DisplayPanel, zonesMatching } from '../src/admin/DisplayPanel'

afterEach(cleanup)

const ZONES = ['America/New_York', 'Asia/Kuala_Lumpur', 'Asia/Singapore', 'Europe/London', 'UTC']
const JAN = new Date('2026-01-15T12:00:00Z')

describe('narrowing the zones', () => {
  it('matches any part of the name, ignoring case', () => {
    expect(zonesMatching(ZONES, 'singapore', '', JAN)).toEqual(['Asia/Singapore'])
    expect(zonesMatching(ZONES, 'ASIA', '', JAN)).toEqual(['Asia/Kuala_Lumpur', 'Asia/Singapore'])
  })

  it('reads a space as the underscore zone names use', () => {
    expect(zonesMatching(ZONES, '  new york ', '', JAN)).toEqual(['America/New_York'])
  })

  it('matches an offset exactly, not as a prefix', () => {
    expect(zonesMatching(ZONES, 'gmt+8', '', JAN)).toEqual(['Asia/Kuala_Lumpur', 'Asia/Singapore'])
    // GMT+8 must not also catch a zone at GMT, nor GMT-5 one at GMT-5:30.
    expect(zonesMatching(ZONES, 'GMT', '', JAN)).toEqual(['Europe/London', 'UTC'])
    expect(zonesMatching(ZONES, 'GMT-5', '', JAN)).toEqual(['America/New_York'])
  })

  it('keeps the chosen zone first when the text would hide it', () => {
    expect(zonesMatching(ZONES, 'london', 'Asia/Singapore', JAN)).toEqual(['Asia/Singapore', 'Europe/London'])
    // Including one the browser's list lacks: `supportedValuesOf` omits UTC.
    expect(zonesMatching(['Europe/London'], '', 'UTC', JAN)).toEqual(['UTC', 'Europe/London'])
    expect(zonesMatching(['Europe/London'], 'london', 'UTC', JAN)).toEqual(['UTC', 'Europe/London'])
  })

  it('an empty text leaves the whole list', () => {
    expect(zonesMatching(ZONES, '   ', 'UTC', JAN)).toEqual(ZONES)
  })
})

describe('the panel', () => {
  const initial = { display_timezone: 'UTC', label: 'GMT' }

  it('shows the stored zone even when the browser does not list it', () => {
    render(<DisplayPanel initial={initial} save={vi.fn()} />)
    expect((screen.getByLabelText('Time zone') as HTMLSelectElement).value).toBe('UTC')
  })

  it('picks the one zone a text leaves, and saves it', async () => {
    const save = vi.fn(async (zone: string) => ({ display_timezone: zone, label: 'GMT+8' }))
    render(<DisplayPanel initial={initial} save={save} />)

    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a zone' }), { target: { value: 'singapore' } })
    expect((screen.getByLabelText('Time zone') as HTMLSelectElement).value).toBe('Asia/Singapore')

    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(save).toHaveBeenCalledWith('Asia/Singapore')
    expect(await screen.findByText(/Saved\. Times now read in Asia\/Singapore/)).toBeTruthy()
  })

  it('leaves the choice alone while several zones match', () => {
    render(<DisplayPanel initial={initial} save={vi.fn()} />)

    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a zone' }), { target: { value: 'asia' } })

    expect((screen.getByLabelText('Time zone') as HTMLSelectElement).value).toBe('UTC')
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('says why a save failed', async () => {
    const { ApiError } = await import('../src/lib/api')
    const save = vi.fn(async () => {
      throw new ApiError(422, 'Unknown time zone.')
    })
    render(<DisplayPanel initial={initial} save={save} />)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Find a zone' }), { target: { value: 'london' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Unknown time zone.')).toBeTruthy()
  })
})
