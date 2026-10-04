/**
 * The notifications panel (tasks P6-08, P6-27; spec §12.5, §13.3;
 * design-system.md §8, `Notifications.dc.html`).
 *
 * The in-app half of `P5-07`'s digest, reading the rows the alert pass writes
 * before it delivers anything — so a deployment with no bot token still has
 * somewhere to see what would have been sent. §8 fixes its shape: three kinds,
 * grouped by day, filtered by type and never by read state.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { NotificationsPanel } from '../src/explore/NotificationsPanel'
import { KIND_OF_TYPE, countsByKind, dayHeading } from '../src/lib/status'
import { DAGGER } from '../src/ui/Contested'
import type { Notification } from '../src/lib/api'

const REPO = join(fileURLToPath(new URL('..', import.meta.url)), '..')

function item(over: Partial<Notification> = {}): Notification {
  return {
    notification_id: 1,
    notification_type: 'alert',
    title: 'Fetch success 12% over 1h',
    body: '4 of 33 attempts succeeded. Mostly: timeout 20.',
    payload: { condition: 'fetch_success_low' },
    surface: 'admin',
    read_at: null,
    created_at: '2026-09-15T12:40:00Z',
    ...over,
  }
}

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const NOW = new Date('2026-09-15T18:00:00Z')

describe('the kinds match what the database records', () => {
  it('maps every notification type the model declares, and nothing else', () => {
    // A type added to Postgres and not here would fall into no filter — the one
    // way a notification can be recorded, delivered to Telegram, and never
    // shown in the app. Read from the model, not retyped.
    const model = readFileSync(join(REPO, 'packages/meridian_core/meridian_core/models/runs.py'), 'utf8')
    const declaration = /NOTIFICATION_TYPE = constrained\(([\s\S]*?)name="notification_type"/.exec(model)
    expect(declaration, 'NOTIFICATION_TYPE is no longer declared the way this test reads it').toBeTruthy()

    const inDatabase = [...declaration![1]!.matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
    expect(inDatabase.length).toBeGreaterThan(3)
    expect(Object.keys(KIND_OF_TYPE).sort()).toEqual(inDatabase.sort())
  })

  it('folds the per-type counts into §8’s three kinds', () => {
    expect(countsByKind({ alert: 2, seed_proposal: 3, gazetteer_proposal: 1, run_summary: 4 })).toEqual({
      jobs: 4,
      approvals: 4,
      alerts: 2,
    })
  })

  it('counts an unknown type rather than dropping it', () => {
    expect(countsByKind({ something_new: 2 }).jobs).toBe(2)
  })
})

describe('what it shows', () => {
  it('shows the title and the detail beneath it', () => {
    // §12.5's reasoning, carried through: a rate says something is wrong and
    // never what, so the body is the part that decides who gets called.
    const rendered = text(
      renderToStaticMarkup(<NotificationsPanel notifications={[item()]} countsByType={{ alert: 1 }} now={NOW} />),
    )

    expect(rendered).toContain('Fetch success 12% over 1h')
    expect(rendered).toContain('timeout 20')
  })

  it('counts every kind, not only the filtered one', () => {
    // A panel reading "Approvals 0" while three seed proposals wait is the
    // filter hiding the thing the reader came for.
    const rendered = text(
      renderToStaticMarkup(
        <NotificationsPanel
          notifications={[item()]}
          countsByType={{ alert: 1, seed_proposal: 3 }}
          active="alerts"
          now={NOW}
        />,
      ),
    )

    expect(rendered).toContain('Approvals 3')
    expect(rendered).toContain('All 4')
  })

  it('shows only the chosen kind when filtered', () => {
    const rendered = text(
      renderToStaticMarkup(
        <NotificationsPanel
          notifications={[
            item(),
            item({ notification_id: 2, notification_type: 'seed_proposal', title: 'Six seeds proposed' }),
          ]}
          countsByType={{ alert: 1, seed_proposal: 1 }}
          active="approvals"
          now={NOW}
        />,
      ),
    )

    expect(rendered).toContain('Six seeds proposed')
    expect(rendered).not.toContain('Fetch success 12%')
  })

  it('offers no read-state control, because there is no read state to mark', () => {
    // §8: filtered by type, not by read state. The artboard's "Mark all read"
    // would need a write the API does not have, and would teach inbox-clearing.
    const rendered = text(
      renderToStaticMarkup(<NotificationsPanel notifications={[item()]} countsByType={{ alert: 1 }} now={NOW} />),
    )
    expect(rendered.toLowerCase()).not.toContain('mark all read')
    expect(rendered.toLowerCase()).not.toContain('unread')
  })
})

describe('grouped by day', () => {
  it('heads today and yesterday by name, older days by date', () => {
    const now = new Date(2026, 8, 15, 18, 0)
    expect(dayHeading(new Date(2026, 8, 15, 9, 0).toISOString(), now)).toBe('Today')
    expect(dayHeading(new Date(2026, 8, 14, 23, 0).toISOString(), now)).toBe('Yesterday')
    expect(dayHeading(new Date(2026, 8, 10, 9, 0).toISOString(), now)).toBe('2026-09-10')
  })

  it('puts each row under its own day, once', () => {
    const now = new Date(2026, 8, 15, 18, 0)
    const rendered = text(
      renderToStaticMarkup(
        <NotificationsPanel
          notifications={[
            item({ notification_id: 1, title: 'A', created_at: new Date(2026, 8, 15, 9).toISOString() }),
            item({ notification_id: 2, title: 'B', created_at: new Date(2026, 8, 15, 8).toISOString() }),
            item({ notification_id: 3, title: 'C', created_at: new Date(2026, 8, 14, 8).toISOString() }),
          ]}
          countsByType={{ alert: 3 }}
          now={now}
        />,
      ),
    )

    expect(rendered.match(/Today/g)).toHaveLength(1)
    expect(rendered.indexOf('Today')).toBeLessThan(rendered.indexOf(' A '))
    expect(rendered.indexOf('Yesterday')).toBeGreaterThan(rendered.indexOf(' B '))
    expect(rendered.indexOf('Yesterday')).toBeLessThan(rendered.indexOf(' C '))
  })
})

describe('absence', () => {
  it('says nothing is recorded rather than rendering an empty list', () => {
    // And says *why* an empty panel is meaningful: alerts fire on sustained
    // conditions, so silence is a claim rather than an absence of data.
    const rendered = text(renderToStaticMarkup(<NotificationsPanel notifications={[]} countsByType={{}} />))

    expect(rendered).toContain('Nothing recorded')
    expect(rendered).toContain('nothing to report')
  })

  it('offers no filters when there is nothing to filter', () => {
    const markup = renderToStaticMarkup(<NotificationsPanel notifications={[]} countsByType={{}} />)
    expect(markup).not.toContain('aria-pressed')
  })

  it('says a filter matched nothing, rather than showing the empty-system copy', () => {
    const rendered = text(
      renderToStaticMarkup(
        <NotificationsPanel notifications={[item()]} countsByType={{ alert: 1 }} active="jobs" now={NOW} />,
      ),
    )
    expect(rendered).toContain('No jobs among the recent notifications.')
    expect(rendered).not.toContain('Nothing recorded')
  })
})

describe('an alert reads differently from a job', () => {
  it('carries the dagger as text, not only the brass', () => {
    // §8: alerts carry the brass tint *and* the dagger. Strip the colour and
    // the row must still read as an alert — §6's own test.
    const alert = renderToStaticMarkup(
      <NotificationsPanel notifications={[item()]} countsByType={{ alert: 1 }} now={NOW} />,
    )
    const routine = renderToStaticMarkup(
      <NotificationsPanel
        notifications={[item({ notification_type: 'run_summary' })]}
        countsByType={{ run_summary: 1 }}
        now={NOW}
      />,
    )

    expect(alert.replace(/class="[^"]*"/g, '')).toContain(DAGGER)
    expect(alert).toContain('data-kind="alerts"')
    expect(routine.replace(/class="[^"]*"/g, '')).not.toContain(DAGGER)
  })

  it('links a decision to where it is made, and a finished job to nothing', () => {
    const approval = renderToStaticMarkup(
      <NotificationsPanel
        notifications={[item({ notification_type: 'gazetteer_proposal' })]}
        countsByType={{ gazetteer_proposal: 1 }}
        now={NOW}
      />,
    )
    const job = renderToStaticMarkup(
      <NotificationsPanel
        notifications={[item({ notification_type: 'job_complete' })]}
        countsByType={{ job_complete: 1 }}
        now={NOW}
      />,
    )

    expect(approval).toContain('href="/admin"')
    expect(job).not.toContain('href=')
  })
})
