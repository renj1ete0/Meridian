/**
 * The notifications panel (task P6-08, spec §12.5, §13.3).
 *
 * The in-app half of `P5-07`'s digest, reading the rows the alert pass writes
 * before it delivers anything — so a deployment with no bot token still has
 * somewhere to see what would have been sent.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { NotificationsPanel } from '../src/explore/NotificationsPanel'
import type { Notification } from '../src/lib/api'

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

describe('what it shows', () => {
  it('shows the title and the detail beneath it', () => {
    // §12.5's reasoning, carried through: a rate says something is wrong and
    // never what, so the body is the part that decides who gets called.
    const rendered = text(
      renderToStaticMarkup(
        <NotificationsPanel notifications={[item()]} countsByType={{ alert: 1 }} />,
      ),
    )

    expect(rendered).toContain('Fetch success 12% over 1h')
    expect(rendered).toContain('timeout 20')
  })

  it('counts every type, not only the filtered ones', () => {
    // A panel reading "alerts (0)" while three seed proposals wait is the
    // filter hiding the thing the reader came for.
    const rendered = text(
      renderToStaticMarkup(
        <NotificationsPanel
          notifications={[item()]}
          countsByType={{ alert: 1, seed_proposal: 3 }}
          active={['alert']}
        />,
      ),
    )

    expect(rendered).toContain('seeds (3)')
  })

  it('does not parse the timestamp into a Date', () => {
    // The same trap the API client documents: `new Date('2026-09-15')` is UTC
    // midnight, which renders as the 14th in any negative offset.
    const markup = renderToStaticMarkup(
      <NotificationsPanel notifications={[item()]} countsByType={{ alert: 1 }} />,
    )

    expect(text(markup)).toContain('2026-09-15 12:40')
  })
})

describe('absence', () => {
  it('says nothing is recorded rather than rendering an empty list', () => {
    // And says *why* an empty panel is meaningful: alerts fire on sustained
    // conditions, so silence is a claim rather than an absence of data.
    const rendered = text(
      renderToStaticMarkup(<NotificationsPanel notifications={[]} countsByType={{}} />),
    )

    expect(rendered).toContain('Nothing recorded')
    expect(rendered).toContain('nothing to report')
  })

  it('offers no filters when there is nothing to filter', () => {
    const rendered = text(
      renderToStaticMarkup(<NotificationsPanel notifications={[]} countsByType={{}} />),
    )

    expect(rendered).not.toContain('all')
  })
})

describe('an alert reads differently from a run summary', () => {
  it('marks alerts at attention weight', () => {
    // §2: "state in form, not only colour". The border weight differs as well
    // as the hue, so the distinction survives a colour-blind reader and print.
    const alert = renderToStaticMarkup(
      <NotificationsPanel notifications={[item()]} countsByType={{ alert: 1 }} />,
    )
    const routine = renderToStaticMarkup(
      <NotificationsPanel
        notifications={[item({ notification_type: 'run_summary' })]}
        countsByType={{ run_summary: 1 }}
      />,
    )

    expect(alert).toContain('border-l-2')
    expect(routine).not.toContain('border-l-2')
  })
})
