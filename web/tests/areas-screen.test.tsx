// @vitest-environment jsdom
/**
 * The Map screen's areas (task P6-34).
 *
 * Tested for what a reader relies on: an empty corpus says what it waits for;
 * a weak or stale area is marked and says why; cited and similar links are
 * drawn differently and never merged in the bridge panel; clicking zooms or
 * opens; right-click offers only what exists, with route disabled and saying
 * why.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { modeFromSearch } from '../src/explore/MapPage'
import { AreasView, areaFromSearch } from '../src/explore/map/AreasScreen'
import { nameWithin } from '../src/lib/areas'
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
    expect(screen.getByText('No fields yet')).toBeTruthy()
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

  it('shades a field by how much of it is on a topic, and says so', () => {
    const shaded = level({
      areas: [
        area({ area_id: 1, name: 'Law', passages: 600, examined: 600, on_topic: 3, topic_mix: { walk: 3 } }),
        area({ area_id: 2, name: 'Transport', passages: 300, examined: 300, on_topic: 210, topic_mix: { walk: 210 } }),
      ],
    })
    const { container } = render(<AreasView level={shaded} onLevel={() => {}} />)
    const law = container.querySelector('[data-area="1"]')!
    const transport = container.querySelector('[data-area="2"]')!
    expect(law.getAttribute('data-share')).toBe('0.005')
    expect(law.getAttribute('aria-label')).toContain('under 1% on a topic')
    expect(transport.getAttribute('aria-label')).toContain('70% on a topic')
    const fill = (g: Element) => Number(g.querySelector('circle')!.getAttribute('fill-opacity'))
    expect(fill(law)).toBeLessThan(fill(transport))
  })

  it('leaves a field the build did not measure at the plain fill', () => {
    const { container } = render(<AreasView level={level()} onLevel={() => {}} />)
    const g = container.querySelector('[data-area="1"]')!
    expect(g.getAttribute('data-share')).toBeNull()
    expect(g.querySelector('circle')!.getAttribute('fill-opacity')).toBe('0.3')
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
        passages: [
          {
            chunk_id: 1,
            source_id: 4,
            title: 'A page',
            url: 'https://x.test',
            source_tier: 'press',
            snippet: 'a passage',
          },
        ],
      }),
    )
    const onLevel = vi.fn()
    const leaf = area({ area_id: 9, level: 3, children: 0, name: 'leaf · terms' })
    const { container } = render(
      <AreasView level={level({ level: 3, parent: area({ level: 2 }), areas: [leaf] })} onLevel={onLevel} />,
    )
    fireEvent.click(container.querySelector('[data-area="9"]')!)
    expect(onLevel).not.toHaveBeenCalled()
    const panel = await screen.findByRole('complementary', { name: 'Field' })
    await waitFor(() => expect(within(panel).getByText(/a passage/)).toBeTruthy())
  })

  it('draws cited links solid and similar-only links dashed', () => {
    const similar: AreaLink = { ...cited, area_a: 1, area_b: 3, cited_claims: 0, cited_sources: 0 }
    const three = level({
      areas: [...level().areas, area({ area_id: 3, passages: 50, x: -0.5 })],
      links: [cited, similar],
    })
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
    expect(within(panel).getByText('Terms both fields share')).toBeTruthy()
    expect(within(panel).getByText('Similar passages across the two')).toBeTruthy()
    expect(within(panel).getByText('cosine 0.81')).toBeTruthy()
  })

  it('draws links under the circles, so no line crosses a name (B-124)', () => {
    const { container } = render(<AreasView level={level({ links: [cited] })} onLevel={() => {}} />)
    const link = container.querySelector('[data-link]')
    const circle = container.querySelector('[data-area]')
    expect(link && circle).toBeTruthy()
    // Earlier in the document is lower in SVG paint order.
    expect(link!.compareDocumentPosition(circle!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
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
  it('offers Find, and no item a reader cannot use', () => {
    const { container } = render(<AreasView level={level()} onLevel={() => {}} />)
    fireEvent.contextMenu(container.querySelector('[data-area="1"]')!)
    const menu = screen.getByRole('menu')
    expect(within(menu).getByRole('menuitem', { name: /Open in Find/ })).toBeTruthy()
    // A disabled "not built yet" entry is a dead end in a reader's menu; route
    // mode returns with P6-39.
    for (const item of within(menu).queryAllByRole('menuitem')) {
      expect((item as HTMLButtonElement).disabled).toBe(false)
      expect(item.textContent).not.toMatch(/not built/i)
    }
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
    expect(screen.getByText(/Fields are clusters of passages/)).toBeTruthy()
    expect(screen.getByText('Subfields in this field')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'All fields' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Fields of the corpus' })).toBeTruthy()
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
    fireEvent.change(screen.getByLabelText('Jump to a field or a term'), { target: { value: 'enzyme' } })
    fireEvent.submit(screen.getByRole('search'))
    const hit = await screen.findByRole('button', { name: /enzyme · protein · folding/ })
    expect(hit.textContent).toContain('in r › a')
    fireEvent.click(hit)
    expect(onLevel).toHaveBeenCalledWith(7)

    // The level arrives: the sub-area opens rather than being closed by it.
    rerender(
      <AreasView
        level={level({ level: 3, parent: area({ area_id: 7, level: 2 }), areas: [leaf] })}
        onLevel={onLevel}
      />,
    )
    expect(await screen.findByRole('complementary', { name: 'Field' })).toBeTruthy()
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

describe('the map as a map (B-93)', () => {
  function withChildren() {
    const root = level()
    const byParent = new Map([
      [
        1,
        level({
          level: 2,
          parent: root.areas[0]!,
          areas: [area({ area_id: 11, passages: 300 }), area({ area_id: 12, passages: 300 })],
        }),
      ],
      [
        2,
        level({
          level: 2,
          parent: root.areas[1]!,
          areas: [area({ area_id: 21, passages: 150 }), area({ area_id: 22, passages: 150 })],
        }),
      ],
    ])
    return { root, byParent }
  }

  function frame(container: HTMLElement): HTMLElement {
    return container.querySelector('[data-theme="dark"]') as HTMLElement
  }

  it('changes the level of detail by zooming, not by a separate gesture', () => {
    const { root, byParent } = withChildren()
    const onDepth = vi.fn()
    const { container } = render(<AreasView level={root} onLevel={vi.fn()} byParent={byParent} onDepth={onDepth} />)

    for (let i = 0; i < 6; i++) fireEvent.wheel(frame(container), { deltaY: -100, clientX: 400, clientY: 300 })

    expect(onDepth).toHaveBeenCalled()
    expect(onDepth.mock.calls.at(-1)![0]).toBeGreaterThan(1)
  })

  it('zooms back out to the root, and never asks for a level that is not there', () => {
    const { root, byParent } = withChildren()
    const onDepth = vi.fn()
    // The level held the way the page holds it, so what is asked for comes back.
    function Page() {
      const [depth, setDepth] = useState(1)
      return (
        <AreasView
          level={root}
          onLevel={vi.fn()}
          byParent={byParent}
          depth={depth}
          onDepth={(d) => {
            onDepth(d)
            setDepth(d)
          }}
        />
      )
    }
    const { container } = render(<Page />)

    for (let i = 0; i < 40; i++) fireEvent.wheel(frame(container), { deltaY: -300, clientX: 400, clientY: 300 })
    for (let i = 0; i < 40; i++) fireEvent.wheel(frame(container), { deltaY: 300, clientX: 400, clientY: 300 })

    const asked = onDepth.mock.calls.map((c) => c[0] as number)
    expect(Math.max(...asked)).toBeLessThanOrEqual(root.levels)
    expect(asked.at(-1)).toBe(1)
  })

  it('does not open a circle a drag started on', () => {
    const { root, byParent } = withChildren()
    const onLevel = vi.fn()
    const { container } = render(<AreasView level={root} onLevel={onLevel} byParent={byParent} onDepth={vi.fn()} />)
    const circle = container.querySelector('[data-area="1"]') as Element

    fireEvent.pointerDown(circle, { pointerId: 1, button: 0, clientX: 100, clientY: 100 })
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 160, clientY: 130 })
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 160, clientY: 130 })
    fireEvent.click(circle)
    expect(onLevel).not.toHaveBeenCalled()

    // The next plain click still opens it: only the drag's own click is eaten.
    fireEvent.click(circle)
    expect(onLevel).toHaveBeenCalledWith(1)
  })
})

describe('the size key on a phone (B-101)', () => {
  it('is one short row below the compact width, and the full key above it', () => {
    const wide = render(<AreasView level={level()} onLevel={vi.fn()} />)
    expect(within(wide.container).getByLabelText('Size key').textContent).toContain('to scale')
    cleanup()

    const box = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 390,
      height: 700,
      top: 0,
      left: 0,
      right: 390,
      bottom: 700,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect)
    const narrow = render(<AreasView level={level()} onLevel={vi.fn()} />)
    const key = within(narrow.container).getByLabelText('Size key')
    expect(key.textContent).not.toContain('to scale')
    expect(key.textContent).toContain('passages')
    box.mockRestore()
  })
})

describe('route mode (P6-39)', () => {
  it('routes from a right-clicked field to the next one clicked, and says when none joins them', () => {
    const joined = level({ links: [cited] })
    const { container, unmount } = render(<AreasView level={joined} onLevel={vi.fn()} />)
    fireEvent.contextMenu(container.querySelector('[data-area="1"]')!)
    fireEvent.click(screen.getByText('Route from here…'))
    expect(screen.getByText(/Route from .*click another/)).toBeTruthy()
    expect(container.querySelector('[data-route-end="1"]')).toBeTruthy()

    fireEvent.click(container.querySelector('[data-area="2"]')!)
    const panel = screen.getByRole('complementary', { name: 'Route' })
    expect(panel.textContent).toContain('every hop cited')
    expect(container.querySelector('[data-route-end="2"]')).toBeTruthy()
    unmount()

    const apart = render(<AreasView level={level({ links: [] })} onLevel={vi.fn()} />)
    fireEvent.contextMenu(apart.container.querySelector('[data-area="1"]')!)
    fireEvent.click(screen.getByText('Route from here…'))
    fireEvent.click(apart.container.querySelector('[data-area="2"]')!)
    expect(screen.getByRole('complementary', { name: 'Route' }).textContent).toContain('No chain of lines')
  })

  it('picking the start does not open it, and Escape cancels', () => {
    const onLevel = vi.fn()
    const { container } = render(<AreasView level={level({ links: [cited] })} onLevel={onLevel} />)
    fireEvent.contextMenu(container.querySelector('[data-area="1"]')!)
    fireEvent.click(screen.getByText('Route from here…'))
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(screen.queryByText(/Route from .*click another/)).toBeNull()
    fireEvent.click(container.querySelector('[data-area="2"]')!)
    expect(onLevel).toHaveBeenCalledWith(2)
  })
})

describe('the fill control (P6-42)', () => {
  it('fills by topic share by default and by research share when asked', () => {
    const lv = level()
    lv.areas[0] = { ...lv.areas[0]!, examined: 10, on_topic: 10, tier_mix: { peer_reviewed: 0, government: 10 } }
    const { container } = render(<AreasView level={lv} onLevel={vi.fn()} />)
    const shaded = () => container.querySelector('[data-area="1"]')!.getAttribute('data-shade')
    expect(shaded()).toBe('1.000')
    fireEvent.click(screen.getByRole('button', { name: 'Research' }))
    expect(shaded()).toBe('0.000')
    expect(container.textContent).toContain('share peer-reviewed')
  })
})

describe('the Map leads somewhere (B-186)', () => {
  it('reads a child by its term inside a parent named alike, and keeps the full name otherwise', () => {
    expect(nameWithin('Transportation (fare)', 'Transportation (time)')).toBe('fare')
    expect(nameWithin('Transportation (fare)', 'Transportation')).toBe('fare')
    expect(nameWithin('Transportation & Mechanics of Materials', 'Transportation (time)')).toBe(
      'Transportation & Mechanics of Materials',
    )
    expect(nameWithin('Transport (fare)', 'Transportation')).toBe('Transport (fare)')
    expect(nameWithin('Ecology', null)).toBe('Ecology')
  })

  it('makes a field’s distinctive terms searches on Find', async () => {
    const leaf = area({ area_id: 9, level: 3, children: 0, name: 'leaf', terms: ['fare', 'ridership'] })
    vi.stubGlobal('fetch', respond({ area: leaf, path: [], passages: [] }))
    const { container } = render(
      <AreasView level={level({ level: 3, parent: area({ level: 2 }), areas: [leaf] })} onLevel={() => {}} />,
    )
    fireEvent.click(container.querySelector('[data-area="9"]')!)
    const panel = await screen.findByRole('complementary', { name: 'Field' })
    const link = await within(panel).findByRole('link', { name: 'fare' })
    expect(link.getAttribute('href')).toBe('/?q=fare')
  })

  it('offers the concept a jump names, beside the fields', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (String(url).includes('/api/explore/graph/search'))
          return new Response(
            JSON.stringify({
              query: 'lidar',
              matches: [
                {
                  entity_id: 254,
                  canonical_name: 'lidar sensors',
                  node_type: 'concept',
                  jurisdiction: null,
                  is_annotation: false,
                  matched_alias: null,
                  degree: 3,
                },
              ],
            }),
          )
        return new Response(JSON.stringify({ query: 'lidar', hits: [] }))
      }),
    )
    render(<AreasView level={level()} onLevel={() => {}} />)
    fireEvent.change(screen.getByLabelText('Jump to a field or a term'), { target: { value: 'lidar' } })
    fireEvent.submit(screen.getByRole('search'))
    const concept = await screen.findByRole('link', { name: /lidar sensors/ })
    expect(concept.getAttribute('href')).toBe('/nodes/254')
  })
})
