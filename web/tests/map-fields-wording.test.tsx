// @vitest-environment jsdom
/**
 * The Map's clusters read as fields of work, not as geography: the tab says
 * "Fields", while the URL keeps its older `view=areas` so saved links open.
 */
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { MapPage } from '../src/explore/MapPage'
import type { AreasLevel } from '../src/lib/areas'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.replaceState(null, '', '/')
})

const EMPTY: AreasLevel = {
  build: null,
  level: 1,
  parent: null,
  path: [],
  areas: [],
  links: [],
  levels: 3,
  passages_needed: 20,
  stale_after_days: 180,
  weak_below_sources: 3,
}

function openMap(url: string) {
  window.history.replaceState(null, '', url)
  const fetch = vi.fn(async (_url: string) => new Response(JSON.stringify(EMPTY), { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  render(<MapPage actions={{}} />)
  return fetch
}

describe('the Map names its clusters as fields', () => {
  it('labels the tab Fields, with no tab still saying Areas', () => {
    openMap('/map')
    const views = screen.getByRole('navigation', { name: 'Map views' })
    const tab = within(views).getByRole('link', { name: 'Fields' })
    expect(tab.getAttribute('aria-current')).toBe('page')
    expect(tab.getAttribute('href')).toBe('/map')
    expect(within(views).queryByText(/areas?|regions?/i)).toBeNull()
  })

  it('still opens the fields from an old ?view=areas link, and asks the old API path', async () => {
    const fetch = openMap('/map?view=areas')
    const views = screen.getByRole('navigation', { name: 'Map views' })
    expect(within(views).getByRole('link', { name: 'Fields' }).getAttribute('aria-current')).toBe('page')
    expect(within(views).getByRole('link', { name: 'Topics' }).getAttribute('aria-current')).toBeNull()
    expect(await screen.findByText('No fields yet')).toBeTruthy()
    // The operator command keeps its name; only the sentence around it moved.
    expect(screen.getByText('worker.areas')).toBeTruthy()
    expect(String(fetch.mock.calls[0]![0])).toBe('/api/explore/areas')
  })
})
