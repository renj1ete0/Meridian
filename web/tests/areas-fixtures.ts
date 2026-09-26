import type { Area } from '../src/lib/areas'

/** One area, with whatever a test overrides. */
export function area(over: Partial<Area> = {}): Area {
  return {
    area_id: 1,
    level: 1,
    parent_id: null,
    name: 'alpha · beta · gamma',
    terms: ['alpha', 'beta', 'gamma'],
    passages: 100,
    sources: 10,
    tier_mix: { informal: 100 },
    examined: null,
    on_topic: null,
    topic_mix: {},
    newest_at: '2026-09-01T00:00:00Z',
    x: 0,
    y: 0,
    children: 3,
    weak: false,
    stale: false,
    reasons: [],
    ...over,
  }
}
