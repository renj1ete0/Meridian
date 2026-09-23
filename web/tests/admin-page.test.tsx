/**
 * Admin as a whole (tasks P6-13, P6-28; spec §12.6; `AdminLight`).
 *
 * What only the assembled page can get wrong: which section a URL opens, where
 * a fresh install lands, which ground Admin is drawn on, and the order of the
 * writes — a staged weight must be previewed and never written until Apply, and
 * a bulk verdict must be one request followed by a refetch rather than a patch.
 */
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { AdminPage } from '../src/admin/AdminPage'
import {
  DEFAULT_SECTION,
  SECTIONS,
  adminTheme,
  hrefForSection,
  sectionFromPath,
} from '../src/admin/sections'
import { PREVIEW_DEBOUNCE_MS } from '../src/admin/TopicDialogs'
import type { GazetteerRow, TopicRow } from '../src/lib/api'

interface Call {
  method: string
  url: string
  body: unknown
}

function topicRow(topic: string, weight: number): TopicRow {
  return {
    topic: {
      topic,
      weight,
      floor: 0.05,
      ceiling: 0.6,
      boost_factor: null,
      boost_expires_at: null,
      pinned: false,
      status: 'active',
    },
    effective_weight: weight,
    share: weight,
    boost_active: false,
  }
}

function gazRow(id: number): GazetteerRow {
  return {
    term: {
      term_id: id,
      canonical: `Bureau ${id}`,
      aliases: null,
      entity_type: 'concept',
      jurisdiction: null,
      ambiguous: false,
      topic_labels: null,
      source: 'auto_acronym',
      approved: false,
      occurrence_count: 2,
      rejected_at: null,
      created_at: '2026-09-15T00:00:00Z',
    },
    will_load: false,
    withheld_reason: 'unapproved',
    collides_with: [],
  }
}

/** A fake API: answers by path, records every call. */
function stubApi(over: { firstRun?: boolean; closed?: boolean } = {}) {
  const calls: Call[] = []
  const topics = { rows: [topicRow('walkability', 0.6), topicRow('robotics', 0.4)], sums_to: 1 }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null })
      const json = (body: unknown, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { 'content-type': 'application/json' },
        })

      if (over.closed && !url.includes('/first-run')) {
        return json({ detail: 'Admin is closed: set MERIDIAN_ADMIN_ALLOW_ANONYMOUS.' }, 503)
      }
      if (url.includes('/first-run')) {
        return json({
          is_first_run: over.firstRun ?? false,
          sources: 0,
          pending_seeds: [],
          seeds_in_flight: 0,
        })
      }
      if (url.includes('/topics/') && url.endsWith('/preview')) {
        const weight = (calls.at(-1)!.body as { weight: number }).weight
        return json({
          rows: [topicRow('walkability', weight), topicRow('robotics', 1 - weight)],
          sums_to: 1,
        })
      }
      if (url.includes('/topics')) return json(topics)
      if (url.includes('/steering-log')) return json({ entries: [], limit: 60, has_more: false })
      if (url.includes('/runs')) return json({ rows: [], total: 0, active: null })
      if (url.includes('/gazetteer/decide')) {
        const ids = (calls.at(-1)!.body as { term_ids: number[] }).term_ids
        return json({ rows: ids.map(gazRow) })
      }
      if (url.includes('/gazetteer')) {
        return json({
          rows: [gazRow(1), gazRow(2)],
          limit: 100,
          offset: 0,
          has_more: false,
          pending: 2,
          approved: 0,
          rejected: 0,
        })
      }
      return json({})
    }),
  )
  return calls
}

function at(path: string) {
  window.history.replaceState({}, '', path)
}

beforeEach(() => {
  document.documentElement.removeAttribute('data-theme')
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0))
  })
}

// --------------------------------------------------------------------------
// Sections and their URLs
// --------------------------------------------------------------------------

describe('every section is a URL', () => {
  it('round-trips each section through its path', () => {
    for (const def of SECTIONS) {
      expect(sectionFromPath(hrefForSection(def.key)), def.key).toBe(def.key)
      expect(sectionFromPath(`${hrefForSection(def.key)}/`), def.key).toBe(def.key)
    }
  })

  it('gives no two sections the same path', () => {
    const paths = SECTIONS.map((s) => s.path)
    expect(new Set(paths).size).toBe(paths.length)
  })

  it('reads bare Admin and unknown paths as no choice', () => {
    // Null, not the default, so a fresh install can be sent to Seeds without
    // overriding somebody who linked a section on purpose.
    expect(sectionFromPath('/admin')).toBeNull()
    expect(sectionFromPath('/admin/')).toBeNull()
    expect(sectionFromPath('/admin/nope')).toBeNull()
    expect(sectionFromPath('/admin/topics/extra')).toBeNull()
  })

  it('opens the section the address names', async () => {
    stubApi()
    at('/admin/topics')
    render(<AdminPage />)
    await settle()

    expect(screen.getByRole('heading', { name: 'Topics' })).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Topic weights' }).getAttribute('aria-current')).toBe(
      'page',
    )
  })

  it('moves between sections through the address bar', async () => {
    const calls = stubApi()
    at('/admin/topics')
    render(<AdminPage />)
    await settle()

    await act(async () => {
      fireEvent.click(screen.getByRole('link', { name: 'Run history' }))
    })
    await settle()

    expect(window.location.pathname).toBe('/admin/runs')
    expect(screen.getByRole('heading', { name: 'Run history' })).toBeTruthy()
    expect(calls.some((c) => c.url.includes('/api/admin/runs?limit=25'))).toBe(true)
  })

  it('lists a section with no backend, and says so when opened', async () => {
    stubApi()
    at('/admin/enrichment')
    render(<AdminPage />)
    await settle()

    expect(screen.getByRole('heading', { name: 'Enrichment queue' })).toBeTruthy()
    expect(document.body.textContent).toContain('Not built yet')
  })
})

describe('where Admin opens', () => {
  it('opens bare Admin on the default section', async () => {
    stubApi()
    at('/admin')
    render(<AdminPage />)
    await settle()

    const def = SECTIONS.find((s) => s.key === DEFAULT_SECTION)!
    expect(screen.getByRole('link', { name: def.label }).getAttribute('aria-current')).toBe('page')
  })

  it('opens a fresh install on Seeds', async () => {
    stubApi({ firstRun: true })
    at('/admin')
    render(<AdminPage />)
    await settle()

    expect(screen.getByRole('heading', { name: 'Cold-start seeds' })).toBeTruthy()
  })

  it('keeps a linked section even on a fresh install', async () => {
    stubApi({ firstRun: true })
    at('/admin/gazetteer')
    render(<AdminPage />)
    await settle()

    expect(screen.getByRole('heading', { name: 'Gazetteer approvals' })).toBeTruthy()
  })

  it('shows a closed Admin’s own sentence, which names the fix', async () => {
    stubApi({ closed: true })
    at('/admin/gazetteer')
    render(<AdminPage />)
    await settle()

    expect(screen.getByRole('alert').textContent).toContain('MERIDIAN_ADMIN_ALLOW_ANONYMOUS')
  })
})

// --------------------------------------------------------------------------
// Paper
// --------------------------------------------------------------------------

describe('Admin is paper unless somebody chose dark', () => {
  it('maps the three theme states', () => {
    // Design-system §2: light exists for docs, Admin and print. "No choice"
    // means the designed look; an explicit dark choice still wins.
    expect(adminTheme(null)).toBe('light')
    expect(adminTheme('light')).toBe('light')
    expect(adminTheme('dark')).toBe('dark')
  })

  it('stamps its own ground, and follows the toggle while open', async () => {
    stubApi()
    at('/admin/runs')
    render(<AdminPage />)
    await settle()
    const root = document.querySelector('[data-admin-section]')!
    expect(root.getAttribute('data-theme')).toBe('light')

    await act(async () => {
      document.documentElement.setAttribute('data-theme', 'dark')
    })
    await settle()
    expect(root.getAttribute('data-theme')).toBe('dark')
  })
})

// --------------------------------------------------------------------------
// The order of the writes
// --------------------------------------------------------------------------

describe('a staged weight', () => {
  it('is previewed, not written, until Apply — and Apply writes what was previewed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const calls = stubApi()
    at('/admin/topics')
    render(<AdminPage />)
    await settle()

    fireEvent.change(screen.getByLabelText('Weight for walkability'), { target: { value: '30' } })
    await act(() => vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS + 10))
    await settle()

    const previews = calls.filter((c) => c.url.endsWith('/topics/walkability/preview'))
    expect(previews).toHaveLength(1)
    expect(previews[0]!.body).toEqual({ weight: 0.3 })
    expect(calls.some((c) => c.method === 'PATCH')).toBe(false)
    expect(document.querySelector('[data-topic="robotics"]')!.textContent).toContain('0.70')

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
    })
    await settle()

    const writes = calls.filter((c) => c.method === 'PATCH')
    expect(writes).toHaveLength(1)
    expect(writes[0]!.url).toContain('/api/admin/topics/walkability')
    expect(writes[0]!.body).toEqual({ weight: 0.3 })
  })

  it('is dropped by Revert, and nothing is written', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const calls = stubApi()
    at('/admin/topics')
    render(<AdminPage />)
    await settle()

    fireEvent.change(screen.getByLabelText('Weight for walkability'), { target: { value: '30' } })
    await act(() => vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS + 10))
    fireEvent.click(screen.getByRole('button', { name: 'Revert' }))
    await settle()

    expect(calls.some((c) => c.method === 'PATCH')).toBe(false)
    expect((screen.getByLabelText('Weight for walkability') as HTMLInputElement).value).toBe('60')
  })
})

describe('a bulk verdict', () => {
  it('is one request, then a refetch, then a sentence saying what happened', async () => {
    const calls = stubApi()
    at('/admin/gazetteer')
    render(<AdminPage />)
    await settle()
    const before = calls.filter((c) => c.url.includes('/api/admin/gazetteer?')).length

    fireEvent.click(screen.getByLabelText('Select every term on this page'))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Approve selected' }))
    })
    await settle()

    const decided = calls.filter((c) => c.url.endsWith('/api/admin/gazetteer/decide'))
    expect(decided).toHaveLength(1)
    expect(decided[0]!.body).toEqual({ term_ids: [1, 2], decision: 'approve' })
    expect(calls.filter((c) => c.url.includes('/api/admin/gazetteer?')).length).toBe(before + 1)
    expect(screen.getByRole('status').textContent).toContain('Approved 2.')
  })
})
