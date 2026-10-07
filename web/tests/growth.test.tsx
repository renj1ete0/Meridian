/**
 * Corpus growth (task B-140, ADRs 0005, 0009 and 0010): the URL holds the view, colours follow
 * the topic, uncrawled days are gaps, and dates are never shifted.
 */
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { sectionOf } from '../src/App'
import { DailyChart, GrowthBody } from '../src/explore/GrowthPage'
import { parseRoute } from '../src/lib/route'
import {
  growthQuery,
  readGrowthQuery,
  seriesColour,
  type Growth,
  type GrowthDay,
  type TopicGrowth,
} from '../src/lib/growth'
import { shortDateOf } from '../src/lib/time'

afterEach(cleanup)

const topic = (name: string, series: number, total = 10): TopicGrowth => ({
  topic: name,
  series,
  passages: { total, in_window: 3 },
  daily: [1, 0, 2],
})

const day = (date: string, crawled: boolean, a = 0, multi = 0): GrowthDay => ({
  day: date,
  crawled,
  by_topic: a ? { 'Topic A': a } : {},
  multi,
  new_sites: 1,
})

function growth(over: Partial<Growth> = {}): Growth {
  const count = { total: 100, in_window: 10 }
  return {
    as_of: '2026-10-05T00:12:00Z',
    zone: 'Asia/Singapore',
    days: 3,
    first_day: '2026-10-03',
    topics: ['Topic A'],
    all_topics: [topic('Topic A', 0), topic('Topic B', 1, 0)],
    passages: count,
    sources: count,
    sites: count,
    concepts: count,
    links: count,
    daily: [day('2026-10-03', true, 4, 1), day('2026-10-04', false), day('2026-10-05', true, 2)],
    map_now: { computed_at: '2026-10-05T00:00:00Z', regions: 12, areas: 86, sub_areas: 388, weak_areas: 9 },
    map_history: [],
    map_history_from: '2026-10-05T00:00:00Z',
    ...over,
  }
}

describe('the URL holds the view (ADR 0010)', () => {
  it('opens on 30 days and leaves the default out of the URL', () => {
    expect(readGrowthQuery('')).toEqual({ range: '30d', topics: [] })
    expect(growthQuery('30d', [])).toBe('')
  })

  it('round-trips a window and topics', () => {
    const query = growthQuery('7d', ['Topic A', 'Topic B'])
    expect(readGrowthQuery(query)).toEqual({ range: '7d', topics: ['Topic A', 'Topic B'] })
  })

  it('refuses a window it does not offer', () => {
    expect(readGrowthQuery('?range=90d').range).toBe('30d')
  })
})

describe('colour follows the topic, never its rank', () => {
  it('uses the topic slot and folds past the eighth', () => {
    expect(seriesColour(0)).toBe('var(--series-1)')
    expect(seriesColour(7)).toBe('var(--series-8)')
    expect(seriesColour(8)).toBe('var(--text-faint)')
  })
})

describe('the daily chart', () => {
  it('draws a day the crawl did not run as a dashed gap, with no bar', () => {
    const { container } = render(<DailyChart days={growth().daily} topics={[topic('Topic A', 0)]} />)
    expect(container.querySelectorAll('line[stroke-dasharray]')).toHaveLength(1)
    // 3 coloured segments across the two crawled days, plus one hover target each.
    const filled = [...container.querySelectorAll('rect')].filter((r) => r.getAttribute('fill') !== 'transparent')
    expect(filled).toHaveLength(3)
  })

  it('has a table view that says which days were not crawled', () => {
    render(<DailyChart days={growth().daily} topics={[topic('Topic A', 0)]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Table view' }))
    expect(screen.getByText(/2026-10-04 · not crawled/)).toBeTruthy()
  })
})

describe('the page body', () => {
  it('shows the headline counts and the map', () => {
    render(<GrowthBody growth={growth()} chosen={[]} />)
    expect(screen.getByText('Passages on a topic')).toBeTruthy()
    expect(screen.getByText(/9 weak/)).toBeTruthy()
  })

  it('offers only topics with passages, and toggles them', () => {
    const onToggle = vi.fn()
    render(<GrowthBody growth={growth()} chosen={[]} onToggle={onToggle} />)
    const group = screen.getByRole('group', { name: 'Topics' })
    expect(group.textContent).toContain('Topic A')
    expect(group.textContent).not.toContain('Topic B')
    fireEvent.click(screen.getByRole('button', { name: /Topic A/ }))
    expect(onToggle).toHaveBeenCalledWith('Topic A')
  })

  it('names topics in words, as Find does, and still filters by the stored name', () => {
    // Found on a live corpus: Growth read "on-demand-bus" where every other page reads words.
    const onToggle = vi.fn()
    const named = growth({ topics: ['on-demand-bus'], all_topics: [topic('on-demand-bus', 0)] })
    const { container } = render(<GrowthBody growth={named} chosen={[]} onToggle={onToggle} />)
    expect(container.textContent).toContain('on demand bus')
    expect(container.textContent).not.toContain('on-demand-bus')
    fireEvent.click(screen.getByRole('button', { name: /on demand bus/ }))
    expect(onToggle).toHaveBeenCalledWith('on-demand-bus')
  })
})

describe('calendar dates are not converted (ADR 0009)', () => {
  it('formats the date as written', () => {
    expect(shortDateOf('2026-10-05')).toBe('05 Oct')
    expect(shortDateOf('2026-01-01')).toBe('01 Jan')
  })
})

describe('routing', () => {
  it('has its own section (ADR 0010)', () => {
    expect(parseRoute('/growth').name).toBe('growth')
    expect(parseRoute('/growth/').name).toBe('growth')
    expect(parseRoute('/growthx').name).toBe('explore')
    expect(sectionOf(parseRoute('/growth'))).toBe('growth')
  })
})
