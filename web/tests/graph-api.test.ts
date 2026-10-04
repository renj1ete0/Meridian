/**
 * The graph workspace's client against its DTOs (tasks P6-01–P6-03).
 *
 * The second link of the chain `src/explore/graph/api.ts` documents: `tsc`
 * ties each interface to its `*_FIELDS` list, and this ties each list to the
 * pydantic class it mirrors, parsed out of `meridian_core/schemas/`. A field
 * added in Python and not here fails; so does one removed.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  CONTESTED_LIST_FIELDS,
  CONTESTED_PAIR_FIELDS,
  CONTESTED_SIDE_FIELDS,
  EVIDENCE_FIELDS,
  FACET_COUNT_FIELDS,
  GRAPH_EDGE_FIELDS,
  GRAPH_FACETS_FIELDS,
  GRAPH_FILTERS_FIELDS,
  GRAPH_NODE_DETAIL_FIELDS,
  GRAPH_NODE_FIELDS,
  GRAPH_PATH_FIELDS,
  NEIGHBOURHOOD_FIELDS,
  NODE_MATCH_FIELDS,
  NODE_SEARCH_FIELDS,
  NO_FILTERS,
  filtersToRecord,
  getNeighbourhood,
  getPath,
  neighbourhoodQuery,
  saveGraphView,
  searchNodes,
} from '../src/explore/graph/api'
import { ApiError } from '../src/lib/api'

const REPO = join(fileURLToPath(new URL('..', import.meta.url)), '..')
const SCHEMAS = join(REPO, 'packages/meridian_core/meridian_core/schemas')
const ROUTES = join(REPO, 'services/api/api/routes/graph.py')

/** Field names declared on one pydantic class, docstrings stripped first. */
function pydanticFields(file: string, className: string): string[] {
  const source = readFileSync(join(SCHEMAS, file), 'utf8')
  const start = source.indexOf(`class ${className}(`)
  if (start === -1) throw new Error(`${file} has no class ${className}`)
  const rest = source.slice(start)
  const end = rest.slice(1).search(/^class /m)
  const body = (end === -1 ? rest : rest.slice(0, end + 1)).replace(/"""[\s\S]*?"""/g, '')
  return [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map((m) => m[1]!)
}

describe('the graph client types match the DTOs', () => {
  const pairs = [
    ['GraphNodeRead', GRAPH_NODE_FIELDS],
    ['GraphEdgeRead', GRAPH_EDGE_FIELDS],
    ['GraphFilters', GRAPH_FILTERS_FIELDS],
    ['FacetCount', FACET_COUNT_FIELDS],
    ['GraphFacetsRead', GRAPH_FACETS_FIELDS],
    ['NeighbourhoodRead', NEIGHBOURHOOD_FIELDS],
    ['EvidenceRead', EVIDENCE_FIELDS],
    ['ContestedSideRead', CONTESTED_SIDE_FIELDS],
    ['ContestedPairRead', CONTESTED_PAIR_FIELDS],
    ['ContestedListRead', CONTESTED_LIST_FIELDS],
    ['GraphNodeDetailRead', GRAPH_NODE_DETAIL_FIELDS],
    ['NodeMatchRead', NODE_MATCH_FIELDS],
    ['NodeSearchRead', NODE_SEARCH_FIELDS],
    ['PathRead', GRAPH_PATH_FIELDS],
  ] as const

  it('parses real fields, so the comparison cannot pass vacuously', () => {
    expect(pydanticFields('graphview.py', 'GraphNodeRead')).toContain('cross_topic')
  })

  it.each(pairs)('%s', (className, fields) => {
    expect([...fields].sort()).toEqual(pydanticFields('graphview.py', className).sort())
  })

  it('every graphview class has a client mirror', () => {
    // Completeness: a DTO added to the module and not here is a response
    // field the client silently ignores.
    const source = readFileSync(join(SCHEMAS, 'graphview.py'), 'utf8')
    const classes = [...source.matchAll(/^class (\w+)\(BaseModel\)/gm)].map((m) => m[1]!)
    expect(classes.sort()).toEqual(pairs.map(([name]) => name).sort())
  })
})

describe('the neighbourhood query', () => {
  it('omits everything when nothing is filtered', () => {
    expect(neighbourhoodQuery(NO_FILTERS)).toBe('')
  })

  it('repeats list keys rather than joining them', () => {
    const query = new URLSearchParams(
      neighbourhoodQuery({ ...NO_FILTERS, tiers: ['press', 'government'], topics: ['a', 'b'] }),
    )
    expect(query.getAll('tier')).toEqual(['press', 'government'])
    expect(query.getAll('topic')).toEqual(['a', 'b'])
  })

  it('sends only the filters that are on', () => {
    const query = new URLSearchParams(
      neighbourhoodQuery({ ...NO_FILTERS, publishedFrom: '2020-01-01', contestedOnly: true, attribute: 'density' }, 60),
    )
    expect(Object.fromEntries(query)).toEqual({
      published_from: '2020-01-01',
      contested_only: 'true',
      attribute: 'density',
      limit: '60',
    })
  })

  it('uses the parameter names the route declares', () => {
    // Drift across the language boundary: a key the route does not declare is
    // silently ignored by FastAPI, which makes a filter that does nothing.
    const route = readFileSync(ROUTES, 'utf8')
    const sent = new URLSearchParams(
      neighbourhoodQuery(
        {
          topics: ['t'],
          tiers: ['press'],
          publishedFrom: '2020-01-01',
          publishedTo: '2021-01-01',
          contestedOnly: true,
          attribute: 'a',
        },
        10,
      ),
    )
    for (const key of new Set(sent.keys())) {
      expect(route, key).toMatch(new RegExp(`^\\s+${key}:`, 'm'))
    }
  })

  it('stores a saved view with the same keys it sends', () => {
    const filters = { ...NO_FILTERS, tiers: ['press' as const], contestedOnly: true }
    const sent = new Set(new URLSearchParams(neighbourhoodQuery(filters)).keys())
    expect(new Set(Object.keys(filtersToRecord(filters)))).toEqual(sent)
  })
})

describe('requests', () => {
  afterEach(() => vi.unstubAllGlobals())

  function stub(status: number, body: unknown) {
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(body), { status }))
    vi.stubGlobal('fetch', fetch)
    return fetch
  }

  it('reads the neighbourhood from the graph prefix, under explore', async () => {
    const fetch = stub(200, {})
    await getNeighbourhood(7, { ...NO_FILTERS, tiers: ['press'] }, 30)
    expect(fetch.mock.calls[0]![0]).toBe('/api/explore/graph/nodes/7/neighbourhood?tier=press&limit=30')
  })

  it('turns a 404 into the API sentence', async () => {
    stub(404, { detail: 'No entity 7.' })
    await expect(getNeighbourhood(7, NO_FILTERS)).rejects.toEqual(new ApiError(404, 'No entity 7.'))
  })

  it('searches and paths with encoded parameters', async () => {
    const fetch = stub(200, { query: 'a&b', matches: [] })
    await searchNodes('a&b')
    await getPath(1, 2, 3)
    expect(fetch.mock.calls[0]![0]).toBe('/api/explore/graph/search?q=a%26b&limit=8')
    expect(fetch.mock.calls[1]![0]).toBe('/api/explore/graph/path?source=1&target=2&max_depth=3')
  })

  it('saves a view through admin, with its focus', async () => {
    const fetch = stub(200, { view_id: 1 })
    await saveGraphView('mine', 9, { ...NO_FILTERS, topics: ['t'] })
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/admin/views')
    expect(JSON.parse(init.body as string)).toEqual({
      name: 'mine',
      focus_entity_id: 9,
      filters: { topic: ['t'] },
    })
  })
})
