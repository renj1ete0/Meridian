/**
 * The Map screen's areas (task P6-34).
 *
 * Tested for what a reader relies on: an empty corpus says what it waits for;
 * a weak or stale area is marked and says why; cited and similar links are
 * drawn differently and never merged in the bridge panel; clicking zooms or
 * opens; right-click offers only what exists, with route disabled and saying
 * why.
 *
 * @vitest-environment jsdom
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { modeFromSearch } from '../src/explore/MapPage'
import { AreasView, areaFromSearch } from '../src/explore/map/AreasScreen'
import type { AreaLink, AreasLevel, Bridge } from '../src/lib/areas'
import { area } from './areas-fixtures'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function level(over: Partial<AreasLevel> = {}): AreasLevel {
  return {
    build: { build_id: 7, computed_at: '2026-09-24T00:00:00Z', passages: 900, regions: 2, areas: 5, leaves: 20 },
    level: 1,
    parent: null,
    path: [],
    areas: [
      area({ area_id: 1, name: 'rule · court · party', passages: 600, children: 3 }),
      area({
        area_id: 2,
        name: 'shade · canopy · heat',
        passages: 300,
        sources: 2,
        children: 2,
        weak: true,
        reasons: ['2 sources; fewer than 3 independent sources'],
        x: 0.5,
      }),
    ],
    links: [],
    levels: 3,
    passages_needed: 20,
    stale_after_days: 180,
    weak_below_sources: 3,
    ...over,
  }
}

const cited: AreaLink = {
  area_a: 1,
  area_b: 2,
  cited_claims: 2,
  cited_sources: 3,
  similar_pairs: 3,
  similarity: 0.7,
  shared_terms: ['heat'],
}

function respond(body: unknown) {
  return vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }))
}

describe('an empty map', () => {
  it('says what it is waiting for rather than drawing nothing', () => {
    render(<AreasView level={level({ build: null, areas: [] })} onLevel={() => {}} />)
    expect(screen.getByText('No areas yet')).toBeTruthy()
    expect(screen.getByText(/at least 20 of them/)).toBeTruthy()
  })
})

describe('areas on the canvas', () => {
  it('draws one circle per area, flags the weak one and says why', () => {
    const { container } = render(<AreasView level={level()} onLevel={() => {}} />)
    const circles = container.querySelectorAll('[data-area]')
    expect(circles.length).toBe(2)
    const weak = container.querySelector('[data-area="2"]')!
    expect(weak.getAttribute('data-flagged')).toBe('true')
    expect(weak.getAttribute('aria-label')).toContain('fewer than 3 independent sources')
    expect(container.querySelector('[data-area="1"]')!.getAttribute('data-flagged')).toBeNull()
  })

  it('zooms into an area that has areas inside', () => {
    const onLevel = vi.fn()
    const { container } = render(<AreasView level={level()} onLevel={onLevel} />)
    fireEvent.click(container.querySelector('[data-area="1"]')!)
    expect(onLevel).toHaveBeenCalledWith(1)
  })

  it('opens a sub-area in the panel instead of zooming', async () => {
    vi.stubGlobal(
      'fetch',
      respond({
        area: area({ area_id: 9, level: 3, children: 0, name: 'leaf · terms' }),
        path: [],
        passages: [{ chunk_id: 1, source_id: 4, title: 'A page', url: 'https://x.test', source_tier: 'press', snippet: 'a passage' }],
      }),
    )
    const onLevel = vi.fn()
    const leaf = area({ area_id: 9, level: 3, children: 0, name: 'leaf · terms' })
    const { container } = render(
      <AreasView level={level({ level: 3, parent: area({ level: 2 }), areas: [leaf] })} onLevel={onLevel} />,
    )
    fireEvent.click(container.querySelector('[data-area="9"]')!)
    expect(onLevel).not.toHaveBeenCalled()
    const panel = await screen.findByRole('complementary', { name: 'Area' })
    await waitFor(() => expect(within(panel).getByText(/a passage/)).toBeTruthy())
  })

  it('draws cited links solid and similar-only links dashed', () => {
    const similar: AreaLink = { ...cited, area_a: 1, area_b: 3, cited_claims: 0, cited_sources: 0 }
    const three = level({ areas: [...level().areas, area({ area_id: 3, passages: 50, x: -0.5 })], links: [cited, similar] })
    const { container } = render(<AreasView level={three} onLevel={() => {}} />)
    const solid = container.querySelector('[aria-label*="2 cited claims"]')!
    const dashed = container.querySelector('[aria-label*="similar passages, no cited claim"]')!
    expect(solid.previousElementSibling!.getAttribute('stroke-dasharray')).toBeNull()
    expect(dashed.previousElementSibling!.getAttribute('stroke-dasharray')).toBe('4 5')
  })

  it('keys circle size as passages collected', () => {
    render(<AreasView level={level()} onLevel={() => {}} />)
    const key = screen.getByLabelText('Size key')
    expect(within(key).getByText('Passages collected')).toBeTruthy()
    expect(within(key).getByText(/to scale · area, not importance/)).toBeTruthy()
  })
})

describe('the bridge panel', () => {
  it('keeps cited claims, shared terms and similar passages apart', async () => {
    const bridge: Bridge = {
      a: { area_id: 1, level: 1, name: 'rule · court · party' },
      b: { area_id: 2, level: 1, name: 'shade · canopy · heat' },
      claims: [
        {
          edge_id: 5,
          from_node: 1,
          from_name: 'shelters',
          relation_type: 'reduces',
          to_node: 2,
          to_name: 'heat stress',
          sources: 2,
          citations: 2,
          tiers: ['government'],
        },
      ],
      cited_sources: 2,
      similar: [
        {
          score: 0.81,
          a: { chunk_id: 1, source_id: 1, title: 'One', source_tier: 'press', snippet: 'first passage' },
          b: { chunk_id: 2, source_id: 2, title: 'Two', source_tier: 'press', snippet: 'second passage' },
        },
      ],
      shared_terms: ['heat'],
      similarity: 0.7,
    }
    vi.stubGlobal('fetch', respond(bridge))
    render(<AreasView level={level({ links: [cited] })} onLevel={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /2 cited claims/ }))
    const panel = await screen.findByRole('complementary', { name: 'Bridge' })
    await waitFor(() => expect(within(panel).getByText('shelters')).toBeTruthy())
    expect(within(panel).getByText('claim · cited')).toBeTruthy()
    expect(within(panel).getByText('Terms both areas share')).toBeTruthy()
    expect(within(panel).getByText('Similar passages across the two')).toBeTruthy()
    expect(within(panel).getByText('cosine 0.81')).toBeTruthy()
  })

  it('says outright when two areas share no cited claim', async () => {
    vi.stubGlobal(
      'fetch',
      respond({
        a: { area_id: 1, level: 1, name: 'x' },
        b: { area_id: 2, level: 1, name: 'y' },
        claims: [],
        cited_sources: 0,
        similar: [],
        shared_terms: [],
        similarity: 0.4,
      }),
    )
    render(<AreasView level={level({ links: [{ ...cited, cited_claims: 0, cited_sources: 0 }] })} onLevel={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: /no cited claim/ }))
    const panel = await screen.findByRole('complementary', { name: 'Bridge' })
    await waitFor(() => expect(within(panel).getByText(/No claim in the graph has evidence in both/)).toBeTruthy())
    expect(within(panel).getByText('no cited claim')).toBeTruthy()
  })
})

describe('the context menu', () => {
  it('offers Find and a disabled route that says why', () => {
    const { container } = render(<AreasView level={level()} onLevel={() => {}} />)
    fireEvent.contextMenu(container.querySelector('[data-area="1"]')!)
    const menu = screen.getByRole('menu')
    expect(within(menu).getByRole('menuitem', { name: /Open in Find/ })).toBeTruthy()
    const route = within(menu).getByRole('menuitem', { name: /Route from here/ }) as HTMLButtonElement
    expect(route.disabled).toBe(true)
    expect(route.textContent).toContain('not built yet')
  })

  it('renders steering items when given them', () => {
    const { container } = render(
      <AreasView
        level={level()}
        onLevel={() => {}}
        actions={{ areaItems: (a) => <button role="menuitem">Crawl more of {a.name}</button> }}
      />,
    )
    fireEvent.contextMenu(container.querySelector('[data-area="2"]')!)
    expect(screen.getByRole('menuitem', { name: /Crawl more of shade/ })).toBeTruthy()
  })

  it('closes on Escape', () => {
    const { container } = render(<AreasView level={level()} onLevel={() => {}} />)
    fireEvent.contextMenu(container.querySelector('[data-area="1"]')!)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
  })
})

describe('inside a region', () => {
  it('shows where you are, the areas as a list, and what an area is', () => {
    const parent = area({ area_id: 1, name: 'rule · court · party', children: 2 })
    render(
      <AreasView
        level={level({ level: 2, parent, path: [{ area_id: 1, level: 1, name: 'rule · court · party' }] })}
        onLevel={() => {}}
      />,
    )
    expect(screen.getByRole('navigation', { name: 'Where you are' }).textContent).toContain('rule · court · party')
    expect(screen.getByText(/Areas are clusters of passages/)).toBeTruthy()
    expect(screen.getByText('Areas in this region')).toBeTruthy()
  })
})

describe('jumping to an area or a term', () => {
  it('goes to a sub-area’s level and opens it once that level arrives', async () => {
    const leaf = area({ area_id: 42, level: 3, parent_id: 7, children: 0, name: 'enzyme · protein · folding' })
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes('/areas/jump')) {
        return new Response(
          JSON.stringify({
            query: 'enzyme',
            hits: [
              {
                area: leaf,
                path: [
                  { area_id: 1, level: 1, name: 'r' },
                  { area_id: 7, level: 2, name: 'a' },
                  { area_id: 42, level: 3, name: leaf.name },
                ],
                match: 'name',
                hits: 0,
              },
            ],
          }),
        )
      }
      return new Response(JSON.stringify({ area: leaf, path: [], passages: [] }))
    })
    vi.stubGlobal('fetch', fetchMock)
    const onLevel = vi.fn()
    const { rerender } = render(<AreasView level={level()} onLevel={onLevel} />)
    fireEvent.change(screen.getByLabelText('Jump to an area or a term'), { target: { value: 'enzyme' } })
    fireEvent.submit(screen.getByRole('search'))
    const hit = await screen.findByRole('button', { name: /enzyme · protein · folding/ })
    expect(hit.textContent).toContain('in r › a')
    fireEvent.click(hit)
    expect(onLevel).toHaveBeenCalledWith(7)

    // The level arrives: the sub-area opens rather than being closed by it.
    rerender(
      <AreasView level={level({ level: 3, parent: area({ area_id: 7, level: 2 }), areas: [leaf] })} onLevel={onLevel} />,
    )
    expect(await screen.findByRole('complementary', { name: 'Area' })).toBeTruthy()
  })
})

describe('the URL', () => {
  it('reads the area and the view from the query', () => {
    expect(areaFromSearch('?area=12')).toBe(12)
    expect(areaFromSearch('?area=x')).toBeNull()
    expect(areaFromSearch('?area=-3')).toBeNull()
    expect(modeFromSearch('?view=points')).toBe('points')
    expect(modeFromSearch('')).toBe('areas')
  })
})
