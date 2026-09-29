/**
 * Contested (task P6-10): the third entry point. Both sides drawn alike, the
 * honest empty and failure states, a capped list that says it is capped, and
 * the landing card that opens it.
 */
// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { act, cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { sectionOf } from '../src/App'
import { capLine, claimOf, ContestedPage } from '../src/explore/ContestedPage'
import { CONTESTED_LIST_FIELDS, type ContestedList } from '../src/explore/graph/api'
import { parseRoute } from '../src/lib/route'
import { DAGGER } from '../src/ui/Contested'
import { pair } from './graph-fixtures'

const REPO = join(process.cwd(), '..')

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function respond(value: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(value), { status, headers: { 'content-type': 'application/json' } }),
  )
}

async function renderWith(fetchImpl: (url: string) => Promise<Response>) {
  const fetchMock = vi.fn(fetchImpl)
  vi.stubGlobal('fetch', fetchMock)
  await act(async () => {
    render(<ContestedPage />)
  })
  return fetchMock
}

describe('the client mirrors the DTO', () => {
  it('ContestedListRead', () => {
    const source = readFileSync(
      join(REPO, 'packages/meridian_core/meridian_core/schemas/graphview.py'),
      'utf8',
    )
    const start = source.indexOf('class ContestedListRead(')
    expect(start).toBeGreaterThan(-1)
    const rest = source.slice(start)
    const end = rest.slice(1).search(/^class /m)
    const body = rest.slice(0, end + 1).replace(/"""[\s\S]*?"""/g, '')
    const fields = [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map((m) => m[1]!)
    expect([...CONTESTED_LIST_FIELDS].sort()).toEqual(fields.sort())
  })
})

describe('the route', () => {
  it('has its own path, inside Explore', () => {
    expect(parseRoute('/contested')).toEqual({ name: 'contested' })
    expect(parseRoute('/contested/')).toEqual({ name: 'contested' })
    expect(sectionOf(parseRoute('/contested'))).toBe('explore')
  })
})

describe('the list', () => {
  it('asks the graph route and draws both sides, each with its claim and passage', async () => {
    const body: ContestedList = { pairs: [pair()], total: 1 }
    const fetchMock = await renderWith(() => respond(body))

    expect(String(fetchMock.mock.calls[0]![0])).toBe('/api/explore/graph/contested')
    const [ours, theirs] = [body.pairs[0]!.ours, body.pairs[0]!.theirs]
    for (const side of [ours, theirs]) {
      const region = screen.getByRole('region', { name: claimOf(side) })
      expect(within(region).getByText(side.to_name).getAttribute('href')).toBe(`/nodes/${side.to_entity_id}`)
      expect(region.textContent).toContain(side.evidence!.text)
      expect(region.textContent).toContain(DAGGER)
    }
    expect(screen.getByText(/1 disagreement$/)).toBeTruthy()
  })

  it('says what a capped list left out, and says nothing when it left nothing', () => {
    expect(capLine({ pairs: [pair()], total: 3 })).toBe('1 of 3 shown; 2 more not listed.')
    expect(capLine({ pairs: [pair()], total: 1 })).toBeNull()
  })

  it('states a missing passage rather than drawing an empty quote', async () => {
    const p = pair()
    p.theirs.evidence = null
    await renderWith(() => respond({ pairs: [p], total: 1 }))

    const region = screen.getByRole('region', { name: claimOf(p.theirs) })
    expect(region.textContent).toContain('No passage backs this link.')
    expect(region.querySelector('blockquote')).toBeNull()
  })

  it('an empty graph states the absence and invents no pair', async () => {
    await renderWith(() => respond({ pairs: [], total: 0 }))

    expect(screen.getByText(/No two sources disagree yet/)).toBeTruthy()
    expect(screen.queryAllByRole('region')).toEqual([])
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('a failed read is an alert, not an empty list', async () => {
    await renderWith(() => respond({ detail: 'down' }, 503))

    expect(screen.getByRole('alert').textContent).toMatch(/unavailable/)
    expect(screen.queryByText(/No two sources disagree yet/)).toBeNull()
  })
})
