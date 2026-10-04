// @vitest-environment jsdom
/**
 * Steering from the map (task P6-35): the menu says what an action will move
 * before it moves it, refuses "less" of an area no topic holds, and reports
 * the server's own words — result, undo path, or refusal.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AreaSteerItems, SuggestBox } from '../src/explore/map/Steer'
import { topicNameFrom, type AreaSteering, type MapSteerResult } from '../src/lib/areas'
import { area } from './areas-fixtures'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function steering(over: Partial<AreaSteering> = {}): AreaSteering {
  return {
    area_id: 1,
    topic: 'shade',
    dominant_share: 0.5,
    topics: [{ topic: 'shade', passages: 80 }],
    more_factor: 1.5,
    less_factor: 0.5,
    boost_days: 14,
    search: 'canopy shade walkway',
    noise_sources: 0,
    ...over,
  }
}

const done: MapSteerResult = {
  action: 'more',
  area_id: 1,
  topic: 'shade',
  boost_factor: 1.5,
  boost_expires_at: '2026-10-08T00:00:00Z',
  seed_task_ids: [9],
  view_id: null,
  message: '“shade” boosted ×1.5 for 14 days.',
  undo: 'A boost ends early from Admin › Topics.',
  noise_mark: null,
}

/** Answers by path; records every call. */
function server(routes: Record<string, { status?: number; body: unknown }>) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const key = Object.keys(routes).find((path) => url.startsWith(path))
    const route = key ? routes[key]! : { status: 404, body: { detail: 'no route' } }
    void init
    return new Response(JSON.stringify(route.body), { status: route.status ?? 200 })
  })
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('the area menu', () => {
  it('names the topic it would boost and the search it would queue', async () => {
    server({ '/api/explore/areas/1/steering': { body: steering() } })
    render(<AreaSteerItems area={area()} close={() => {}} />)
    const more = await screen.findByRole('menuitem', { name: /Crawl more of this/ })
    expect(more.textContent).toContain('boosts “shade” ×1.5 for 14 days')
    expect(more.textContent).toContain('canopy shade walkway')
  })

  it('refuses less of an area no topic holds, saying why', async () => {
    server({ '/api/explore/areas/1/steering': { body: steering({ topic: null }) } })
    render(<AreaSteerItems area={area()} close={() => {}} />)
    const less = (await screen.findByRole('menuitem', { name: /Crawl less of this/ })) as HTMLButtonElement
    expect(less.disabled).toBe(true)
    expect(less.textContent).toContain('no configured topic holds this field')
    expect((screen.getByRole('menuitem', { name: /Crawl more of this/ }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('posts the action and shows what happened and how to undo it', async () => {
    const fetchMock = server({
      '/api/explore/areas/1/steering': { body: steering() },
      '/api/admin/map/areas/1/steer': { body: done },
    })
    render(<AreaSteerItems area={area()} close={() => {}} />)
    fireEvent.click(await screen.findByRole('menuitem', { name: /Crawl more of this/ }))
    const status = await screen.findByRole('status')
    expect(status.textContent).toContain('boosted ×1.5')
    expect(status.textContent).toContain('Admin › Topics')
    const post = fetchMock.mock.calls.find(([url]) => url.endsWith('/steer'))!
    expect(post[1]!.method).toBe('POST')
    expect(JSON.parse(String(post[1]!.body))).toEqual({ action: 'more' })
  })

  it('shows a refusal in the server’s words', async () => {
    server({
      '/api/explore/areas/1/steering': { body: steering() },
      '/api/admin/map/areas/1/steer': { status: 503, body: { detail: 'Admin is closed on this instance.' } },
    })
    render(<AreaSteerItems area={area()} close={() => {}} />)
    fireEvent.click(await screen.findByRole('menuitem', { name: /Watch for new sources/ }))
    expect((await screen.findByRole('status')).textContent).toContain('Admin is closed on this instance.')
  })

  it('makes a topic through the existing add-topic route, named from the terms', async () => {
    const fetchMock = server({
      '/api/explore/areas/1/steering': { body: steering() },
      '/api/admin/topics': { body: { rows: [], sums_to: 1 } },
    })
    render(<AreaSteerItems area={area({ terms: ['Heat Stress', 'shade', 'x'] })} close={() => {}} />)
    fireEvent.click(await screen.findByRole('menuitem', { name: /Make it a topic/ }))
    expect((screen.getByLabelText('Topic name') as HTMLInputElement).value).toBe('heat-stress-shade')
    fireEvent.click(screen.getByRole('button', { name: 'Add topic' }))
    await screen.findByRole('status')
    const post = fetchMock.mock.calls.find(([url, init]) => url === '/api/admin/topics' && init?.method === 'POST')!
    expect(JSON.parse(String(post[1]!.body)).topic).toBe('heat-stress-shade')
  })
})

describe('suggesting something new', () => {
  it('offers the smallest areas’ terms and queues what is typed, with a topic', async () => {
    const fetchMock = server({
      '/api/admin/topics': {
        body: { rows: [{ topic: { topic: 'shade', status: 'active' } }], sums_to: 1 },
      },
      '/api/admin/map/suggest': { body: { ...done, action: 'suggest', message: '“cool roofs” queued as a search.' } },
    })
    render(
      <SuggestBox
        near={[area({ passages: 900, terms: ['big'] }), area({ area_id: 2, passages: 5, terms: ['tiny'] })]}
        close={() => {}}
      />,
    )
    expect(screen.getByRole('button', { name: 'tiny' })).toBeTruthy()
    const submit = screen.getByRole('button', { name: 'Add as a search seed' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText(/A term or a question/), { target: { value: 'cool roofs' } })
    await waitFor(() => expect(screen.getByLabelText('For topic')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('For topic'), { target: { value: 'shade' } })
    fireEvent.click(submit)
    expect((await screen.findByRole('status')).textContent).toContain('queued as a search')
    const post = fetchMock.mock.calls.find(([url]) => url === '/api/admin/map/suggest')!
    expect(JSON.parse(String(post[1]!.body))).toEqual({ text: 'cool roofs', topic: 'shade' })
  })
})

describe('naming a topic from terms', () => {
  it('lower-cases and hyphenates the first two terms', () => {
    expect(topicNameFrom(['Heat Stress', 'Bus shelter', 'x'])).toBe('heat-stress-bus-shelter')
    expect(topicNameFrom(['  --a  ', 'b'])).toBe('a-b')
    expect(topicNameFrom([])).toBe('')
  })
})

describe('this is noise (P6-42)', () => {
  it('says how many sources it would mark, and is disabled when there are none', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(steering({ noise_sources: 0 })), { status: 200 })),
    )
    render(<AreaSteerItems area={area({ area_id: 1 })} close={vi.fn()} />)
    const none = (await screen.findByText('This is noise')).closest('button')!
    expect(none.hasAttribute('disabled')).toBe(true)
    cleanup()

    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(steering({ noise_sources: 12 })), { status: 200 })),
    )
    render(<AreaSteerItems area={area({ area_id: 1 })} close={vi.fn()} />)
    const some = (await screen.findByText('This is noise')).closest('button')!
    expect(some.hasAttribute('disabled')).toBe(false)
    expect(some.textContent).toContain('12 sources')
  })

  it('offers an undo that restores through the mark, and says how many came back', async () => {
    const calls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        calls.push(String(url))
        if (String(url).includes('/steering'))
          return new Response(JSON.stringify(steering({ noise_sources: 3 })), { status: 200 })
        if (String(url).includes('/restore'))
          return new Response(JSON.stringify({ mark: 'm1', restored: 3 }), { status: 200 })
        return new Response(
          JSON.stringify({ ...done, action: 'noise', noise_mark: 'm1', message: '3 marked', undo: 'Undo restores' }),
          { status: 200 },
        )
      }),
    )
    render(<AreaSteerItems area={area({ area_id: 1 })} close={vi.fn()} />)
    fireEvent.click((await screen.findByText('This is noise')).closest('button')!)
    fireEvent.click(await screen.findByText('Undo'))
    expect(await screen.findByText('3 sources put back.')).toBeTruthy()
    expect(calls.some((u) => u.endsWith('/api/admin/map/noise/m1/restore'))).toBe(true)
  })
})
