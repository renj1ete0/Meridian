/**
 * The application shell (task P6-27) — design-system.md §5's top bar and
 * top-right cluster, and the ⌘K promise printed under the Explore search field.
 *
 * The cluster is three small claims about the system — how deep the queue is,
 * whether the last run failed, whether anything needs the reader — and each has
 * an honest "don't know". Most of this file is about that: a status that could
 * not be read must not render as a status that is fine.
 */
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { App, sectionOf } from '../src/App'
import { focusSearch, isCommandK, SEARCH_INPUT_ID } from '../src/lib/hotkeys'
import { parseRoute } from '../src/lib/route'
import {
  bellState,
  readSeenAt,
  runHealth,
  statusDetail,
  statusLine,
  type RunHistory,
} from '../src/lib/status'
import { NAV, TopBar, type ClusterData } from '../src/ui/TopBar'
import type { CrawlProgress, Notification, RunRow } from '../src/lib/api'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

beforeEach(() => {
  window.localStorage.clear()
})

function run(over: Partial<RunRow> = {}): RunRow {
  return {
    run_id: 1,
    started_at: '2026-09-23T10:00:00Z',
    completed_at: '2026-09-23T10:05:00Z',
    stage: 'done',
    status: 'done',
    agent_id: null,
    tokens_used: 0,
    cost_usd: null,
    edges_added: 0,
    tags_added: 0,
    seeds_emitted: 0,
    last_chunk_id: null,
    heartbeat_at: null,
    error: null,
    ...over,
  }
}

function progress(over: Partial<CrawlProgress> = {}): CrawlProgress {
  return {
    as_of: '2026-09-23T07:14:00Z',
    queue: { pending: 412, fetched: 30 },
    recent_domains: [],
    attempts_last_hour: 50,
    successes_last_hour: 48,
    ...over,
  }
}

function note(over: Partial<Notification> = {}): Notification {
  return {
    notification_id: 1,
    notification_type: 'job_complete',
    title: 'Draft ready',
    body: null,
    payload: null,
    surface: 'admin',
    read_at: null,
    created_at: '2026-09-23T06:48:00Z',
    ...over,
  }
}

function data(over: Partial<ClusterData> = {}): ClusterData {
  return {
    progress: progress(),
    runs: { kind: 'read', rows: [run()] },
    notifications: { notifications: [note()], counts_by_type: { job_complete: 1 }, unread: 1 },
    ...over,
  }
}

function bar(props: Partial<React.ComponentProps<typeof TopBar>> = {}) {
  const onTheme = vi.fn()
  const view = render(
    <TopBar section="explore" translucent theme="system" onTheme={onTheme} data={data()} {...props} />,
  )
  return { ...view, onTheme }
}

// --------------------------------------------------------------------------
// The status pill
// --------------------------------------------------------------------------

describe('run health', () => {
  it('reads the most recent finished run, skipping one still running', () => {
    // A failure followed by a run in progress is still a failure nobody has
    // seen succeed past.
    const history: RunHistory = {
      kind: 'read',
      rows: [run({ run_id: 3, status: 'running' }), run({ run_id: 2, status: 'failed' }), run({ run_id: 1 })],
    }
    expect(runHealth(history)).toBe('failed')
  })

  it('is healthy when the latest finished run did not fail', () => {
    expect(runHealth({ kind: 'read', rows: [run(), run({ status: 'failed' })] })).toBe('ok')
    expect(runHealth({ kind: 'read', rows: [run({ status: 'deferred' })] })).toBe('ok')
  })

  it('is unknown — not healthy — when run history could not be read', () => {
    // On an instance with Admin closed, /api/admin/runs answers 503. A cyan dot
    // there would claim a health check that never happened.
    expect(runHealth({ kind: 'unreadable', reason: 'Admin is closed.' })).toBe('unknown')
    expect(runHealth(null)).toBe('unknown')
  })
})

describe('the status line', () => {
  it('says queue depth, fetch rate and when the counts were taken', () => {
    const line = statusLine(progress())
    expect(line).toMatch(/^queue 412 · fetch 96% · \d\d:\d\d$/)
  })

  it('says idle rather than 0% when nothing was attempted', () => {
    // 0% would read as every fetch failing, which is the opposite fact.
    expect(statusLine(progress({ attempts_last_hour: 0, successes_last_hour: 0 }))).toContain('fetch idle')
  })

  it('treats a queue with no pending key as empty, not unknown', () => {
    expect(statusLine(progress({ queue: { done: 4 } }))).toContain('queue 0')
  })

  it('names why run health is unknown, in the API’s own words', () => {
    const detail = statusDetail(progress(), 'unknown', { kind: 'unreadable', reason: 'Admin is closed.' })
    expect(detail).toContain('Admin is closed.')
  })
})

describe('the pill as drawn', () => {
  it('goes brass and says so in words when a run failed', () => {
    // §5: the dot goes brass. State in form as well as colour, so the words go
    // with it.
    bar({ data: data({ runs: { kind: 'read', rows: [run({ status: 'failed' })] } }) })
    const pill = screen.getByTitle(/most recent synthesis run failed/)

    expect(pill.getAttribute('data-health')).toBe('failed')
    expect(pill.textContent).toContain('run failed')
  })

  it('stays cyan and quiet when nothing failed', () => {
    bar()
    const pill = screen.getByTitle(/No failed synthesis run/)
    expect(pill.getAttribute('data-health')).toBe('ok')
    expect(pill.textContent).not.toContain('run failed')
  })

  it('draws a hollow dot, not a cyan one, when runs are unreadable', () => {
    bar({ data: data({ runs: { kind: 'unreadable', reason: 'Admin is closed.' } }) })
    const pill = screen.getByTitle(/Admin is closed/)
    expect(pill.getAttribute('data-health')).toBe('unknown')
    expect(pill.innerHTML).not.toContain('bg-accent-graph')
  })

  it('says the status is unavailable rather than showing zeros', () => {
    bar({ data: data({ progress: null }) })
    expect(screen.getByText('status unavailable')).toBeTruthy()
    expect(screen.queryByText(/queue 0/)).toBeNull()
  })
})

// --------------------------------------------------------------------------
// The bell
// --------------------------------------------------------------------------

describe('the bell’s count', () => {
  it('counts what arrived since the panel was last opened', () => {
    const items = [
      note({ notification_id: 2, created_at: '2026-09-23T08:00:00Z' }),
      note({ notification_id: 1, created_at: '2026-09-22T08:00:00Z' }),
    ]
    expect(bellState(items, null).count).toBe(2)
    expect(bellState(items, '2026-09-23T00:00:00Z').count).toBe(1)
    expect(bellState(items, '2026-09-24T00:00:00Z').count).toBe(0)
  })

  it('is cyan for completions and brass when the new ones hold an alert', () => {
    const job = note()
    const alert = note({ notification_id: 2, notification_type: 'alert' })
    expect(bellState([job], null).tone).toBe('graph')
    expect(bellState([job, alert], null).tone).toBe('attention')
  })

  it('does not stay brass for an alert the reader has already seen', () => {
    const old = note({ notification_type: 'alert', created_at: '2026-09-20T00:00:00Z' })
    const fresh = note({ notification_id: 2, created_at: '2026-09-23T08:00:00Z' })
    expect(bellState([fresh, old], '2026-09-21T00:00:00Z')).toEqual({ count: 1, tone: 'graph' })
  })

  it('shows the count on the bell, in the tone it earned', () => {
    bar({
      data: data({
        notifications: {
          notifications: [note(), note({ notification_id: 2, notification_type: 'alert' })],
          counts_by_type: { job_complete: 1, alert: 1 },
          unread: 2,
        },
      }),
    })
    const bell = screen.getByRole('button', { name: /Notifications, 2 new, including an alert/ })
    expect(bell.querySelector('[data-tone="attention"]')?.textContent).toBe('2')
  })

  it('opens the panel and clears the badge — the panel itself stays filtered by type only', () => {
    bar()
    fireEvent.click(screen.getByRole('button', { name: /Notifications, 1 new/ }))

    const panel = screen.getByRole('dialog', { name: 'Notifications' })
    expect(within(panel).getByText('Draft ready')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Notifications, nothing new' })).toBeTruthy()
    expect(readSeenAt()).not.toBeNull()
  })

  it('says the notifications could not be read rather than showing an empty panel', () => {
    bar({ data: data({ notifications: null }) })
    fireEvent.click(screen.getByRole('button', { name: /Notifications/ }))
    expect(screen.getByText(/could not be read/)).toBeTruthy()
    expect(screen.queryByText(/Nothing recorded/)).toBeNull()
  })

  it('closes on Escape', () => {
    bar()
    fireEvent.click(screen.getByRole('button', { name: /Notifications/ }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// --------------------------------------------------------------------------
// Nav, surface, settings
// --------------------------------------------------------------------------

describe('the bar', () => {
  it('offers every section and marks the current one', () => {
    bar({ section: 'map' })
    const nav = screen.getByRole('navigation', { name: 'Sections' })
    const links = within(nav).getAllByRole('link')

    expect(links.map((link) => link.textContent)).toEqual(NAV.map((item) => item.label))
    expect(within(nav).getByRole('link', { name: 'Map' }).getAttribute('aria-current')).toBe('page')
    expect(within(nav).getByRole('link', { name: 'Explore' }).getAttribute('aria-current')).toBeNull()
  })

  it('is translucent over Explore and opaque over Admin', () => {
    // §5: translucency means floating above your work; opacity means this is
    // the thing you are reading.
    const { container, unmount } = bar({ translucent: true })
    expect(container.querySelector('header')?.getAttribute('data-surface')).toBe('translucent')
    unmount()

    const opaque = bar({ section: 'admin', translucent: false })
    expect(opaque.container.querySelector('header')?.getAttribute('data-surface')).toBe('opaque')
  })

  it('carries no greeting', () => {
    // §5: one person owns this system; a welcome line addresses nobody.
    const { container } = bar()
    expect(container.textContent?.toLowerCase()).not.toMatch(/welcome|hello|good (morning|evening)/)
  })

  it('holds the theme choice in settings, as three states', () => {
    const { onTheme } = bar({ theme: 'dark' })
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))

    const group = screen.getByRole('radiogroup', { name: 'Theme' })
    const options = within(group).getAllByRole('radio')
    expect(options.map((o) => o.textContent)).toEqual(['System', 'Light', 'Dark'])
    expect(within(group).getByRole('radio', { name: 'Dark' }).getAttribute('aria-checked')).toBe('true')

    fireEvent.click(within(group).getByRole('radio', { name: 'Light' }))
    expect(onTheme).toHaveBeenCalledWith('light')
  })
})

describe('which section a route belongs to', () => {
  it('files source and node pages under Explore', () => {
    // Marking no destination while a reader is two clicks into the corpus
    // would say the bar does not know where they are.
    expect(sectionOf(parseRoute('/sources/7'))).toBe('explore')
    expect(sectionOf(parseRoute('/nodes/3'))).toBe('explore')
    expect(sectionOf(parseRoute('/map'))).toBe('map')
    expect(sectionOf(parseRoute('/admin/topics'))).toBe('admin')
  })
})

// --------------------------------------------------------------------------
// ⌘K
// --------------------------------------------------------------------------

describe('⌘K', () => {
  it('accepts ⌘K and Ctrl+K and nothing near them', () => {
    const key = { key: 'k', metaKey: false, ctrlKey: false, altKey: false, shiftKey: false }
    expect(isCommandK({ ...key, metaKey: true })).toBe(true)
    expect(isCommandK({ ...key, ctrlKey: true })).toBe(true)
    expect(isCommandK({ ...key, key: 'K', metaKey: true })).toBe(true)
    expect(isCommandK(key)).toBe(false)
    expect(isCommandK({ ...key, metaKey: true, shiftKey: true })).toBe(false)
    expect(isCommandK({ ...key, ctrlKey: true, altKey: true })).toBe(false)
    expect(isCommandK({ ...key, key: 'j', metaKey: true })).toBe(false)
  })

  it('focuses the field where it is on screen, without navigating', () => {
    render(<input id={SEARCH_INPUT_ID} />)
    window.history.pushState({}, '', '/?q=kept')
    focusSearch()
    expect(document.activeElement?.id).toBe(SEARCH_INPUT_ID)
    expect(window.location.search).toBe('?q=kept')
  })

  it('goes to Explore first when the field is not on screen', () => {
    window.history.pushState({}, '', '/admin')
    const queued: Array<() => void> = []
    focusSearch(document, (fn) => queued.push(fn))
    expect(window.location.pathname).toBe('/')

    // The field arrives a render later; the waiting attempt finds it.
    render(<input id={SEARCH_INPUT_ID} />)
    queued.shift()!()
    expect(document.activeElement?.id).toBe(SEARCH_INPUT_ID)
  })
})

describe('the shell, assembled', () => {
  beforeEach(() => {
    // Every read stays pending: these tests are about the shell, and a page
    // that never finishes loading is the most neutral thing to sit inside it.
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
  })

  it('works from any page: ⌘K on Admin lands in the Explore search field', async () => {
    window.history.pushState({}, '', '/admin')
    render(<App />)
    expect(document.querySelector('header')?.getAttribute('data-surface')).toBe('opaque')

    await act(async () => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
      await new Promise((resolve) => setTimeout(resolve, 50))
    })

    expect(window.location.pathname).toBe('/')
    expect(document.activeElement?.id).toBe(SEARCH_INPUT_ID)
    expect(document.querySelector('header')?.getAttribute('data-surface')).toBe('translucent')
  })

  it('no longer forces pages into a reading column', () => {
    // Pages own their width now; a max-width on `main` drew the map and the
    // graph at thumbnail size.
    window.history.pushState({}, '', '/map')
    render(<App />)
    expect(document.querySelector('main')?.className).not.toMatch(/max-w-/)
  })
})
